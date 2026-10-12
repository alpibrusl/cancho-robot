#!/usr/bin/env python3
"""Gate C3 (docs/design.md section 8): `calibration` against LeRobot's own arithmetic, bit for bit.

The reference is LeRobot's FeetechMotorsBus `_normalize` and `_unnormalize` (lerobot 0.6.1, `motors_bus.py`) and its
`encode_sign_magnitude` / `decode_sign_magnitude` (`encoding_utils.py`), called directly: no port is opened. Each
case goes to `tests/calibration_driver.cho` and to the reference; floats are compared as their 64 bits, not within
a tolerance, because the point is that the controller computes exactly what the Python stack computed when the
demonstrations were recorded.

Ranges: the two calibration files LeRobot keeps for this robot (when present: `--calibration DIR`), a committed
copy of their ranges (so CI has them without the robot), and random ranges. Ticks: across and beyond each range,
negative ones included (a sign-decoded Present_Position can be negative).

Usage:  python3 tests/differential_calibration.py build/calibration_driver [--cases N] [--seed S]
"""
import argparse
import json
import random
import struct
import subprocess
import sys
from pathlib import Path

from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.encoding_utils import decode_sign_magnitude, encode_sign_magnitude
from lerobot.motors.feetech import FeetechMotorsBus

HERE = Path(__file__).resolve().parent


def bits(x):
    return struct.unpack("<q", struct.pack("<d", x))[0]


def float_of_bits(n):
    return struct.unpack("<d", struct.pack("<q", n))[0]


def bus(mode, lo, hi, drive):
    return FeetechMotorsBus(
        port="/dev/null",
        motors={"m": Motor(1, "sts3215", mode)},
        calibration={"m": MotorCalibration(id=1, drive_mode=drive, homing_offset=0, range_min=lo, range_max=hi)},
    )


def ranges(rng, calibration_dir, n_random):
    """(min, max, drive) triples: the recorded ones first, then random ones."""
    out = []
    files = sorted(HERE.glob("fixtures/calibration/*.json"))
    if calibration_dir:
        files += sorted(Path(calibration_dir).glob("*.json"))
    for f in files:
        for motor in json.loads(f.read_text()).values():
            out.append((motor["range_min"], motor["range_max"], motor["drive_mode"]))
    for _ in range(n_random):
        lo = rng.randint(0, 4095)
        hi = rng.randint(0, 4095)
        if lo == hi:
            hi = (hi + 1) % 4096
        out.append((lo, hi, rng.randint(0, 1)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("driver")
    ap.add_argument("--cases", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261008)
    ap.add_argument("--calibration", default=None, help="LeRobot's calibration directory, if it is on this machine")
    a = ap.parse_args()
    rng = random.Random(a.seed)

    lines, expect = [], []
    rs = ranges(rng, a.calibration, a.cases)
    for lo, hi, drive in rs:
        deg = bus(MotorNormMode.DEGREES, lo, hi, drive)
        pct = bus(MotorNormMode.RANGE_0_100, lo, hi, drive)
        spct = bus(MotorNormMode.RANGE_M100_100, lo, hi, drive)
        ticks = [lo, hi, (lo + hi) // 2, lo - 1, hi + 1, 0, 4095, -1, -4095]
        ticks += [rng.randint(-600, 4700) for _ in range(40)]
        for t in ticks:
            lines.append(f"deg {t} {lo} {hi}")
            expect.append(bits(deg._normalize({1: t})[1]))
            lines.append(f"pct {t} {lo} {hi} {drive}")
            expect.append(bits(pct._normalize({1: t})[1]))
            lines.append(f"spct {t} {lo} {hi} {drive}")
            expect.append(bits(spct._normalize({1: t})[1]))
        values = [0.0, 100.0, -100.0, 50.0, 99.9999, 0.0001, -0.0, 1e-9, 180.0, -180.0, 359.9]
        values += [rng.uniform(-200.0, 200.0) for _ in range(40)]
        for v in values:
            lines.append(f"tdeg {bits(v)} {lo} {hi}")
            expect.append(deg._unnormalize({1: v})[1])
            lines.append(f"tpct {bits(v)} {lo} {hi} {drive}")
            expect.append(pct._unnormalize({1: v})[1])
            lines.append(f"tspct {bits(v)} {lo} {hi} {drive}")
            expect.append(spct._unnormalize({1: v})[1])

    for bit in (10, 11, 15):
        for raw in [0, 1, (1 << bit) - 1, 1 << bit, (1 << bit) + 1, (1 << (bit + 1)) - 1, 65535] + \
                   [rng.randint(0, 65535) for _ in range(300)]:
            lines.append(f"dsign {raw} {bit}")
            expect.append(decode_sign_magnitude(raw, bit))
        for value in [0, 1, -1, (1 << bit) - 1, -((1 << bit) - 1), 1 << bit, -(1 << bit)] + \
                     [rng.randint(-(1 << bit) - 50, (1 << bit) + 50) for _ in range(300)]:
            lines.append(f"esign {value} {bit}")
            try:
                expect.append(encode_sign_magnitude(value, bit))
            except ValueError:
                expect.append(-1)

    out = subprocess.run([a.driver], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
    got = [int(x) for x in out.stdout.split()]
    failures = [(l, g, e) for l, g, e in zip(lines, got, expect) if g != e]
    if len(got) != len(expect):
        failures.append(("(count)", len(got), len(expect)))
    print(f"calibration: {len(expect)} cases over {len(rs)} ranges compared bit for bit")
    if failures:
        print(f"{len(failures)} FAILURES, first 10:")
        for l, g, e in failures[:10]:
            if l.split()[0] in ("deg", "pct", "spct"):
                print(f"  {l}: ours {float_of_bits(g)!r}, LeRobot {float_of_bits(e)!r}")
            else:
                print(f"  {l}: ours {g}, LeRobot {e}")
        return 1
    print("differential: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
