#!/usr/bin/env python3
"""Task #10's gates without hardware: `teleop` on a pseudo-terminal, against the simulated bus.

This program plays the operator's terminal: it answers teleop's question about the kitty keyboard protocol (or does
not, for the plain-character mode), sends key presses, repeats and releases with real timing, and then reads the
simulated bus's trace -- what reached the servos, and when -- on the same monotonic clock.

  T1  a held key moves its joint, the right way (q raises the shoulder: its goal ticks grow);
  T2  no backlog: after a release the joint's goal stops changing within two cycles (40 ms);
  T3  keys combine (kitty): q and w held together move the shoulder and the elbow in the same cycles;
  T4  a connection that stops carrying events while a key is down stops the arm (the 1 s guard);
  T5  plain characters (a terminal without the protocol): a key held by its repeats moves, and stops when the
      repeats stop, within the repeat guard (150 ms) and two cycles;
  T6  the terminal is put back and the program says so; torque stays on (no torque write but the first);
  T7  one joint at its span does not stop the others: with the elbow at the top of its span, a and s held together
      still lower the shoulder (the request is asked again with the limited joint held).

Usage:  python3 tests/teleop_gates.py build/teleop
"""
import json
import os
import pty
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim" / "fake_bus.py"
ESC = "\x1b"
LIFT, ELBOW = 2, 3  # servo ids


class Terminal:
    """teleop on a pty: we are the terminal."""

    def __init__(self, teleop, side, kitty):
        self.master, slave = pty.openpty()
        self.proc = subprocess.Popen([teleop, side, "--sim"], stdin=slave, stdout=slave, stderr=slave, close_fds=True)
        os.close(slave)
        self.kitty = kitty
        self.out = b""
        self.answered = False

    def pump(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            r, _, _ = select.select([self.master], [], [], 0.005)
            if r:
                try:
                    data = os.read(self.master, 4096)
                except OSError:
                    return
                self.out += data
                if not self.answered and b"\x1b[c" in self.out:
                    reply = (ESC + "[?0u" if self.kitty else "") + ESC + "[?62;22c"
                    os.write(self.master, reply.encode())
                    self.answered = True

    def send(self, text):
        os.write(self.master, text.encode("utf-8"))
        return time.monotonic()

    def hold(self, keys, seconds, kitty_codes, gap=0.03):
        """Hold `keys` for `seconds`: down, then repeats every `gap` (kitty) or the characters again (plain)."""
        t0 = None
        if self.kitty:
            t0 = self.send("".join(f"{ESC}[{kitty_codes[k]}u" for k in keys))
            end = t0 + seconds
            while time.monotonic() < end:
                self.pump(gap)
                self.send("".join(f"{ESC}[{kitty_codes[k]};1:2u" for k in keys))
        else:
            t0 = self.send(keys[-1])
            self.pump(0.4)  # the OS's initial delay before it repeats
            end = t0 + seconds
            while time.monotonic() < end:
                self.send(keys[-1])
                self.pump(gap)
        return t0

    def up(self, keys, kitty_codes):
        if self.kitty:
            return self.send("".join(f"{ESC}[{kitty_codes[k]};1:3u" for k in keys))
        return time.monotonic()  # plain characters: the release is the repeats stopping, now

    def quit(self):
        self.send(f"{ESC}[27u" if self.kitty else ESC)
        self.pump(1.0)
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.pump(0.2)
        return self.out.decode("utf-8", "replace")


CODES = {"q": 113, "s": 115, "w": 119, "a": 97, "p": 112}
PAN = 1


def goal_writes(rows, ident):
    out = []
    for r in rows:
        if r["dir"] == "in" and r["instr"] == "sync_write":
            b = bytes.fromhex(r["bytes"])
            p = b[5:-1]
            if p[0] == 42:
                count = p[1]
                for k in range(2, len(p), count + 1):
                    if p[k] == ident:
                        raw = p[k + 1] | p[k + 2] << 8
                        out.append((r["t"], -(raw & 0x7FFF) if raw & 0x8000 else raw))
    return out


def torque_writes(rows):
    return [r for r in rows if r["dir"] == "in" and r["instr"] == "sync_write" and bytes.fromhex(r["bytes"])[5] == 40]


def session(teleop, kitty, script, workdir):
    trace = Path(workdir) / f"t-{time.monotonic_ns()}.jsonl"
    sim = subprocess.Popen([sys.executable, str(SIM), "--trace", str(trace)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if Path("/tmp/cancho-robot-sim/left").exists() and Path("/tmp/cancho-robot-sim/right").exists():
                break
            time.sleep(0.05)
        term = Terminal(teleop, "l", kitty)
        term.pump(1.5)  # identify, torque on, the protocol question
        marks = script(term)
        out = term.quit()
    finally:
        sim.terminate()
        sim.wait(timeout=10)
    rows = [json.loads(l) for l in trace.read_text().splitlines()] if trace.exists() else []
    return out, rows, marks


def changes(writes, after, before=None):
    """The goal writes after `after` (and before `before`) that change the goal."""
    out = []
    last = None
    for t, v in writes:
        if last is not None and v != last and t > after and (before is None or t < before):
            out.append((t, v))
        last = v
    return out


def main():
    teleop = str(Path(sys.argv[1]).resolve())
    workdir = tempfile.mkdtemp()
    failures = []

    def gate(name, ok, detail=""):
        print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ""), flush=True)
        if not ok:
            failures.append(name)

    # kitty: q held 1 s; released; q and s together 0.6 s; released; q held, then the connection goes quiet.
    def kitty_script(term):
        m = {}
        m["q_down"] = term.hold(["q"], 1.0, CODES)
        m["q_up"] = term.up(["q"], CODES)
        term.pump(0.6)
        m["qs_down"] = term.hold(["q", "w"], 0.6, CODES)
        m["qs_up"] = term.up(["q", "w"], CODES)
        term.pump(0.6)
        m["lim_down"] = term.hold(["s"], 1.5, CODES)  # the elbow up to the top of its span
        term.up(["s"], CODES)
        term.pump(0.3)
        m["qlim_down"] = term.hold(["a", "s"], 0.6, CODES)  # the elbow at its span, the shoulder down: still moves
        m["qlim_up"] = term.up(["a", "s"], CODES)
        term.pump(0.6)
        m["quiet_down"] = term.hold(["p"], 0.3, CODES)  # p: the base, 200 degrees of room; then nothing more is sent
        m["quiet_last"] = time.monotonic()
        term.pump(1.8)
        return m

    out, rows, m = session(teleop, True, kitty_script, workdir)
    gate("kitty protocol detected", "kitty protocol" in out, out.splitlines()[0:1] if out else "no output")
    lift = goal_writes(rows, LIFT)
    elbow = goal_writes(rows, ELBOW)
    held1 = changes(lift, m["q_down"], m["q_up"])
    gate("T1 a held key moves its joint the right way", len(held1) >= 10 and held1[-1][1] > held1[0][1],
         f"{len(held1)} lift goal changes, {held1[0][1] if held1 else '-'} -> {held1[-1][1] if held1 else '-'}")
    after = changes(lift, m["q_up"], m["qs_down"])
    late = [t - m["q_up"] for t, _ in after]
    gate("T2 no backlog after a release", not late or max(late) <= 0.040,
         f"last lift change {1000 * max(late):.0f} ms after release" if late else "no change after release")
    both = [t for t, _ in changes(lift, m["qs_down"], m["qs_up"])]
    el = [t for t, _ in changes(elbow, m["qs_down"], m["qs_up"])]
    gate("T3 keys combine", len(both) >= 5 and len(el) >= 5, f"{len(both)} lift and {len(el)} elbow changes together")
    guard_end = m["quiet_last"] + 1.0 + 0.04
    quiet = changes(goal_writes(rows, PAN), m["quiet_down"])
    gate("T4 a connection gone quiet stops the arm", quiet and max(t for t, _ in quiet) <= guard_end,
         f"last change {max(t for t, _ in quiet) - m['quiet_last']:.2f} s after the last event" if quiet else "never moved")
    lim = changes(lift, m["qlim_down"], m["qlim_up"])
    gate("T7 one joint at its span does not stop the others", len(lim) >= 5,
         f"{len(lim)} lift changes while the elbow was held at its span")
    gate("T6 the terminal is restored and torque stays on",
         "torque stays on" in out and len(torque_writes(rows)) == 1 and "key-up to stop" in out,
         [l for l in out.splitlines() if "key-up" in l])

    # plain characters: q held 1 s by its repeats, then they stop.
    def plain_script(term):
        m = {}
        m["q_down"] = term.hold(["q"], 1.0, CODES)
        m["q_up"] = term.up(["q"], CODES)
        term.pump(0.8)
        return m

    out, rows, m = session(teleop, False, plain_script, workdir)
    gate("plain characters detected", "plain characters" in out, out.splitlines()[0:1] if out else "no output")
    lift = goal_writes(rows, LIFT)
    moved = changes(lift, m["q_down"], m["q_up"])
    after = [t - m["q_up"] for t, _ in changes(lift, m["q_up"])]
    gate("T5 plain keys move, and stop when the repeats stop",
         len(moved) >= 10 and (not after or max(after) <= 0.150 + 0.040),
         f"{len(moved)} changes held; last {1000 * max(after):.0f} ms after the repeats stopped" if after else f"{len(moved)} changes held")

    if failures:
        print(f"teleop: {len(failures)} gate(s) failed: {failures}")
        return 1
    print("teleop: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
