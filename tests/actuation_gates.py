#!/usr/bin/env python3
"""Task #9's gates without hardware: `play` against the simulated bus, judged from the bytes on the bus.

The simulated bus (sim/fake_bus.py) logs every packet. The controller cannot tell it from the robot, and the trace
is the bus's, not the controller's, so these are claims about what reached a servo, not about what the program says:

  G1  a refused request sends nothing: a session whose only motion is refused writes exactly what `torque on` writes;
  G2  `torque on` sets every goal to the present position, tick for tick, BEFORE enabling torque (no jump);
  G3  every goal written leads the position last read from that servo by at most 8 degrees (the gripper by 10% of
      its range), plus the distance the servo can move between that read and the write;
  G4  torque is never disabled except by `torque <side> off`;
  G5  a stall (an obstacle on a joint) is detected, and the arm's last goal is where it stopped, not where it was going;
  G6  arms are recognised by their servos: with the arm chains on the opposite buses, each is still driven as itself.

Usage:  python3 tests/actuation_gates.py build/play [--keep DIR]
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim" / "fake_bus.py"
CAL = {s: json.loads((ROOT / "tests" / "fixtures" / "calibration" / f).read_text())
       for s, f in (("left", "xle_right.json"), ("right", "xle_left.json"))}
GOAL, TORQUE, PRESENT = 42, 40, 56
DEG = 4096 / 360.0
LEAD_TICKS = 8.0 * DEG
SPEED_TICKS_PER_S = 3000.0


def run_session(play, commands, sim_args, workdir):
    trace = Path(workdir) / f"trace-{time.monotonic_ns()}.jsonl"
    sim = subprocess.Popen([sys.executable, str(SIM), "--dir", "/tmp/cancho-robot-sim", "--trace", str(trace), *sim_args],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(50):
            if Path("/tmp/cancho-robot-sim/left").exists() and Path("/tmp/cancho-robot-sim/right").exists():
                break
            time.sleep(0.05)
        out = subprocess.run([play, "--sim"], input="\n".join(commands) + "\nquit\n", capture_output=True, text=True,
                             timeout=180)
    finally:
        sim.terminate()
        sim.wait(timeout=10)
    rows = [json.loads(l) for l in trace.read_text().splitlines()] if trace.exists() else []
    return out.stdout + out.stderr, rows


def packets_in(rows):
    """The controller's packets, decoded: (row, instr, params)."""
    for r in rows:
        if r["dir"] == "in" and r["instr"]:
            b = bytes.fromhex(r["bytes"])
            yield r, r["instr"], b[5:-1]


def sync_writes(rows, address):
    for r, instr, p in packets_in(rows):
        if instr == "sync_write" and p[0] == address:
            count = p[1]
            blocks = p[2:]
            yield r, {blocks[k]: int.from_bytes(blocks[k + 1:k + 1 + count], "little")
                      for k in range(0, len(blocks), count + 1)}


def magnitude(raw):
    return -(raw & 0x7FFF) if raw & 0x8000 else raw


def last_reads(rows):
    """Per bus and id, the positions read, in order: (time, ticks)."""
    out = {}
    for r in rows:
        if r["dir"] == "out" and r["instr"] == "read":
            b = bytes.fromhex(r["bytes"])
            if b[3] == 4:  # a two-byte answer
                out.setdefault((r["bus"], b[2]), []).append((r["t"], magnitude(b[5] | b[6] << 8), r))
    return out


def main():
    play = str(Path(sys.argv[1]).resolve())
    workdir = tempfile.mkdtemp()
    failures = []

    def gate(name, ok, detail=""):
        print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    # G1, G2, G4: a move with torque off (refused: nothing holds a goal), torque on, then requests the safety core
    # refuses on their FIRST step: each arm's shoulder lowered further past its span from rest (limit.joint; both
    # rest a degree or so below the floor calibration swept).
    out, rows = run_session(play, ["move l pan 10", "torque l on", "move l lift -20", "torque r on", "move r lift -20"],
                            [], workdir)
    gate("G6/plain: both arms recognised", "the left arm" in out and "the right arm" in out, out.splitlines()[0:2])
    refusals = out.count("refused:")
    goals = list(sync_writes(rows, GOAL))
    torques = list(sync_writes(rows, TORQUE))
    gate("G1 a refusal sends nothing", refusals == 3 and len(goals) == 2 and len(torques) == 2,
         f"{refusals} refusals, {len(goals)} goal writes, {len(torques)} torque writes")
    if goals and len(goals) == len(torques):
        reads = last_reads(rows)
        ok = True
        for (g_row, g), (t_row, _) in zip(goals, torques):
            ok = ok and g_row["bus"] == t_row["bus"] and g_row["t"] < t_row["t"]
            for ident, tick in g.items():
                before = [x for x in reads.get((g_row["bus"], ident), []) if x[0] <= g_row["t"]]
                ok = ok and bool(before) and magnitude(tick) == before[-1][1]
        gate("G2 torque on holds the present position, goal before torque", ok, f"{len(goals)} arms")
    gate("G4 torque never dropped implicitly", all(v == 1 for _, t in torques for v in t.values()))

    # G3, G4, G6: a crossed robot, both arms moved through several joints.
    out, rows = run_session(play, ["torque l on", "move l lift 30", "move l pan 10", "move l elbow -20", "move l grip 40",
                                   "torque r on", "move r wrist 10", "move r lift 30", "move r pan 15", "read"],
                            ["--crossed"], workdir)
    gate("G6/crossed: each arm recognised by its servos",
         "/left: the right arm" in out and "/right: the left arm" in out, [l for l in out.splitlines() if l.startswith("bus")])
    done = out.count("  done,")
    gate("motions complete on the simulated bus", done >= 6, f"{done} of 7 moves done")
    reads = last_reads(rows)
    worst = 0.0
    bad = []
    for g_row, g in sync_writes(rows, GOAL):
        for ident, raw in g.items():
            before = [x for x in reads.get((g_row["bus"], ident), []) if x[0] <= g_row["t"]]
            if not before:
                bad.append(("no read before", g_row["bus"], ident))
                continue
            t_read, present, _ = before[-1]
            allowance = LEAD_TICKS
            if ident == 6:  # the gripper: 10% of its range (the wider of the two arms')
                allowance = 0.10 * max(CAL[s]["gripper"]["range_max"] - CAL[s]["gripper"]["range_min"] for s in CAL)
            slack = SPEED_TICKS_PER_S * max(0.0, g_row["t"] - t_read) + 2
            lead = abs(magnitude(raw) - present)
            worst = max(worst, lead / DEG)
            if lead > allowance + slack:
                bad.append((g_row["bus"], ident, magnitude(raw), present))
    gate("G3 every goal leads the last reading by at most the lead bound", not bad, f"worst {worst:.1f} deg; {bad[:3]}")
    gate("G4 torque never dropped implicitly", all(v == 1 for _, t in sync_writes(rows, TORQUE) for v in t.values()))

    # G5: an obstacle on the right arm's elbow (its chain is on the bus named 'right' when not crossed), at about
    # 84 degrees: the shoulder is raised first (out of the workspace corner the rest pose sits in), then the elbow is
    # asked to 76 degrees and meets the obstacle on the way.
    out, rows = run_session(play, ["torque r on", "move r lift 30", "move r elbow -20"], ["--obstacle", "right:3:2900"],
                            workdir)
    stalled = "stalled" in out
    goals = list(sync_writes(rows, GOAL))
    reads = last_reads(rows)
    ok = stalled and bool(goals)
    if goals:
        g_row, g = goals[-1]
        present = [x for x in reads.get((g_row["bus"], 3), []) if x[0] <= g_row["t"]]
        ok = ok and bool(present) and abs(magnitude(g[3]) - present[-1][1]) <= 3
    gate("G5 a stall is detected and the goal comes back to where the joint stopped", ok,
         [l for l in out.splitlines() if "stalled" in l or "done" in l or "refused" in l])
    if not ok:
        print("  the session:\n    " + "\n    ".join(out.splitlines()))

    # G7: with static friction (the robot's servos settle about 10 ticks short), a joint under the speed limit still
    # gets there: the goal advances from the last goal, not from the present position (2026-10-12, the right elbow
    # stalled on the robot when the goal was kept a degree ahead of a still servo).
    out, rows = run_session(play, ["torque r on", "move r elbow -10", "move r elbow 8"], ["--breakaway", "12"], workdir)
    arrived = out.count("  done,")
    gate("G7 a move under the speed limit overcomes static friction", arrived == 2 and "stalled" not in out,
         [l for l in out.splitlines() if "done" in l or "stalled" in l or "refused" in l])

    if failures:
        print(f"actuation: {len(failures)} gate(s) failed: {failures}")
        return 1
    print("actuation: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
