#!/usr/bin/env python3
"""A simulated XLeRobot servo bus: the playground's robot, and the trace the actuation gates read.

One pseudo-terminal per bus. Each answers the Feetech STS protocol (PING, READ, WRITE, REG WRITE, ACTION, SYNC
READ, SYNC WRITE) for the ids the real robot has (left 1-8, right 1-6, 9, 10), from a register file per servo.
The controller opens the pty's path the way it opens /dev/ttyACM0, and cannot tell the two apart -- which is
the point: the same binary runs against this and against the robot.

What it models, and what it does not:
- the registers the controller uses: Torque_Enable (40), Goal_Position (42, sign-magnitude at bit 15),
  Present_Position (56), Present_Load (60), Present_Voltage (62), Present_Temperature (63), Moving (66);
- a servo with torque on moves its position toward its goal at the STS3215's no-load speed (about 3,000 ticks/s);
  with torque off it stays where it is (no gravity: an arm that sags on the robot will not sag here);
- an obstacle (`--obstacle left:2:2300`): that joint cannot pass that tick; pushing against it shows as load,
  which is how the stall gate is exercised without hurting a servo;
- each arm servo stores its calibration (Homing_Offset 31, Min/Max_Position_Limit 9 and 11) from LeRobot's files,
  as `lerobot-calibrate` leaves them, so the controller recognises an arm by its servos; `--crossed` puts each arm
  on the other bus (the robot on 2026-10-12: the arm chains were on the opposite adapters from lex-robot's record);
- the starting positions are the robot's own, as `tools/scan` read them on 2026-10-12.

Every packet in and out is appended to the trace (`--trace FILE`, JSON lines): the time, the bus, the direction,
the bytes and the instruction. A write instruction in the trace is a byte that reached a servo, so "a refusal
sends nothing" is checked by reading the trace, not by trusting the controller.

Usage:  python3 sim/fake_bus.py [--dir /tmp/cancho-robot-sim] [--trace FILE] [--obstacle BUS:ID:TICK]... [--crossed]
        prints the two device paths (DIR/left, DIR/right), then serves until interrupted.
"""
import argparse
import json
import os
import select
import signal
import sys
import termios
import threading
import time
import tty
from pathlib import Path

PING, READ, WRITE, REG_WRITE, ACTION, SYNC_READ, SYNC_WRITE = 1, 2, 3, 4, 5, 0x82, 0x83
WRITES = {WRITE, REG_WRITE, ACTION, SYNC_WRITE}
NAMES = {PING: "ping", READ: "read", WRITE: "write", REG_WRITE: "reg_write", ACTION: "action",
         SYNC_READ: "sync_read", SYNC_WRITE: "sync_write"}
TORQUE_ENABLE, GOAL_POSITION, PRESENT_POSITION, PRESENT_LOAD = 40, 42, 56, 60
PRESENT_VOLTAGE, PRESENT_TEMPERATURE, MOVING = 62, 63, 66
TICKS_PER_S = 3000.0  # STS3215 no-load: about 0.22 s per 60 degrees

# Present_Position as tools/scan read the robot on 2026-10-12 (docs/research-log.md), per PHYSICAL arm, and the
# tower (on the left arm's bus) and wheel (on the right's) servos. The left arm's servos carry xle_right.json's
# calibration and the right's xle_left.json's: LeRobot's names are the other way round (measured by hand).
ARMS = {
    "left": {1: 1885, 2: 425, 3: 3041, 4: 901, 5: 2085, 6: 2056},
    "right": {1: 2056, 2: 1049, 3: 3061, 4: 2049, 5: 716, 6: 2042},
}
FILE_OF = {"left": "xle_right.json", "right": "xle_left.json"}
EXTRAS = {"left": {7: 1508, 8: 3387}, "right": {9: 1302, 10: 2668}}
CALIBRATION = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "calibration"
HOMING_OFFSET, MIN_LIMIT, MAX_LIMIT = 31, 9, 11


def checksum(body):
    return (~sum(body)) & 0xFF


def status(ident, error=0, params=b""):
    body = bytes([ident, len(params) + 2, error]) + params
    return b"\xff\xff" + body + bytes([checksum(body)])


def sign_magnitude(raw, bit=15):
    return -(raw & ((1 << bit) - 1)) if raw & (1 << bit) else raw


class Servo:
    def __init__(self, ident, position):
        self.mem = bytearray(256)
        self.mem[5] = ident
        self.position = float(position)
        self.pending = None  # a REG WRITE waiting for ACTION
        self.obstacle = None
        self.mem[PRESENT_VOLTAGE] = 120  # 12.0 V
        self.mem[PRESENT_TEMPERATURE] = 30
        self._store(GOAL_POSITION, int(position))
        self._store(PRESENT_POSITION, int(position))

    def _store(self, address, value):
        self.mem[address] = value & 0xFF
        self.mem[address + 1] = (value >> 8) & 0xFF

    def goal(self):
        return sign_magnitude(self.mem[GOAL_POSITION] | self.mem[GOAL_POSITION + 1] << 8)

    def step(self, dt):
        load = 0
        moving = 0
        if self.mem[TORQUE_ENABLE]:
            target = max(0, min(4095, self.goal()))
            room = TICKS_PER_S * dt
            delta = max(-room, min(room, target - self.position))
            nxt = self.position + delta
            if self.obstacle is not None:
                if self.position <= self.obstacle < nxt or nxt < self.obstacle <= self.position:
                    nxt = self.obstacle
                    load = 900  # pushing against it: most of the servo's torque
            if abs(nxt - self.position) > 1e-9:
                moving = 1
            if self.obstacle is not None and nxt == self.obstacle and target != self.obstacle:
                load = 900
                moving = 0
            self.position = nxt
        self._store(PRESENT_POSITION, int(round(self.position)))
        self._store(PRESENT_LOAD, load)
        self.mem[MOVING] = moving


class Bus:
    def __init__(self, name, path, arm, trace):
        self.name = name
        self.servos = {i: Servo(i, p) for i, p in {**ARMS[arm], **EXTRAS[name]}.items()}
        cal = json.loads((CALIBRATION / FILE_OF[arm]).read_text())
        for joint in cal.values():
            servo = self.servos[joint["id"]]
            offset = joint["homing_offset"]
            servo._store(HOMING_OFFSET, (1 << 11 | -offset) if offset < 0 else offset)
            servo._store(MIN_LIMIT, joint["range_min"])
            servo._store(MAX_LIMIT, joint["range_max"])
        self.trace = trace
        self.master, slave = os.openpty()
        tty.setraw(self.master)
        self.slave_name = os.ttyname(slave)
        # Keep the slave open so the pty survives the controller closing it between runs.
        self._slave = slave
        if path.is_symlink() or path.exists():
            path.unlink()
        path.symlink_to(self.slave_name)
        self.path = path
        self.buffer = b""
        self.lock = threading.Lock()

    def log(self, direction, data, instr=None, ident=None):
        if self.trace:
            self.trace.write(json.dumps({"t": round(time.monotonic(), 6), "bus": self.name, "dir": direction,
                                         "bytes": data.hex(), "instr": NAMES.get(instr), "id": ident}) + "\n")
            self.trace.flush()

    def send(self, data, instr, ident):
        self.log("out", data, instr, ident)
        os.write(self.master, data)

    def feed(self, data):
        self.buffer += data
        while True:
            start = self.buffer.find(b"\xff\xff")
            if start < 0:
                self.buffer = self.buffer[-1:] if self.buffer.endswith(b"\xff") else b""
                return
            self.buffer = self.buffer[start:]
            if len(self.buffer) < 4:
                return
            ident, length = self.buffer[2], self.buffer[3]
            if length < 2:
                self.buffer = self.buffer[2:]
                continue
            end = 4 + length
            if len(self.buffer) < end:
                return
            packet, self.buffer = self.buffer[:end], self.buffer[end:]
            if packet[-1] != checksum(packet[2:-1]):
                self.log("in", packet, None, ident)
                continue
            instr, params = packet[4], packet[5:-1]
            self.log("in", packet, instr, ident)
            with self.lock:
                self.handle(ident, instr, params)

    def handle(self, ident, instr, params):
        broadcast = ident == 0xFE
        servo = self.servos.get(ident)
        if instr == PING:
            targets = sorted(self.servos) if broadcast else ([ident] if servo else [])
            for i in targets:
                self.send(status(i), instr, i)
        elif instr == READ and servo and len(params) == 2:
            address, count = params
            self.send(status(ident, 0, bytes(servo.mem[address:address + count])), instr, ident)
        elif instr in (WRITE, REG_WRITE) and len(params) >= 2:
            targets = list(self.servos.values()) if broadcast else ([servo] if servo else [])
            for s in targets:
                if instr == WRITE:
                    s.mem[params[0]:params[0] + len(params) - 1] = params[1:]
                else:
                    s.pending = (params[0], bytes(params[1:]))
            if servo and not broadcast:
                self.send(status(ident), instr, ident)
        elif instr == ACTION:
            for s in (self.servos.values() if broadcast else ([servo] if servo else [])):
                if s.pending:
                    a, d = s.pending
                    s.mem[a:a + len(d)] = d
                    s.pending = None
        elif instr == SYNC_READ and len(params) >= 2:
            address, count = params[0], params[1]
            for i in params[2:]:
                s = self.servos.get(i)
                if s:
                    self.send(status(i, 0, bytes(s.mem[address:address + count])), instr, i)
        elif instr == SYNC_WRITE and len(params) >= 2:
            address, count = params[0], params[1]
            blocks = params[2:]
            for k in range(0, len(blocks) - count, count + 1):
                s = self.servos.get(blocks[k])
                if s:
                    s.mem[address:address + count] = blocks[k + 1:k + 1 + count]

    def step(self, dt):
        with self.lock:
            for s in self.servos.values():
                s.step(dt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=Path("/tmp/cancho-robot-sim"))
    ap.add_argument("--trace", type=Path)
    ap.add_argument("--obstacle", action="append", default=[], metavar="BUS:ID:TICK")
    ap.add_argument("--crossed", action="store_true", help="each arm on the other bus")
    args = ap.parse_args()
    args.dir.mkdir(parents=True, exist_ok=True)
    trace = open(args.trace, "a") if args.trace else None
    arms = {"left": "right", "right": "left"} if args.crossed else {"left": "left", "right": "right"}
    buses = [Bus(n, args.dir / n, arms[n], trace) for n in ("left", "right")]
    for spec in args.obstacle:
        name, ident, tick = spec.split(":")
        next(b for b in buses if b.name == name).servos[int(ident)].obstacle = int(tick)
    for b in buses:
        print(f"{b.name} {b.path} -> {b.slave_name} (the {arms[b.name]} arm's servos)", flush=True)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    def physics():
        last = time.monotonic()
        while not stop.is_set():
            time.sleep(0.002)
            now = time.monotonic()
            for b in buses:
                b.step(now - last)
            last = now

    threading.Thread(target=physics, daemon=True).start()
    fds = {b.master: b for b in buses}
    try:
        while not stop.is_set():
            ready, _, _ = select.select(list(fds), [], [], 0.1)
            for fd in ready:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    continue
                if data:
                    fds[fd].feed(data)
    except KeyboardInterrupt:
        pass
    for b in buses:
        b.path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
