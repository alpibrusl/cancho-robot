#!/usr/bin/env python3
"""Gate C3 can fail: each deliberately wrong `calibration` below must be caught by the cancho tests or by the
bit-for-bit differential against LeRobot.

Usage:  python3 tests/mutate_calibration.py path/to/cancho python3
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
F = "src/calibration.cho"

MUTANTS = [
    ("    return 4095.0;", "    return 4096.0;", "divides by the resolution, not the largest tick"),
    ("    return float_of(min + max) / 2.0;", "    return float_of((min + max) / 2);", "integer midpoint"),
    ("    return (float_of(ticks) - mid(min, max)) * 360.0 / max_tick();",
     "    return (float_of(ticks) - mid(min, max)) * (360.0 / max_tick());", "a different rounding order"),
    ("    return truncate(value * max_tick() / 360.0 + mid(min, max));",
     "    return truncate(value * max_tick() / 360.0 + mid(min, max) + 0.5);", "rounds instead of truncating"),
    ("    if high < at_least {\n        return high;\n    }\n    return at_least;\n}\n\nfn bound_float",
     "    if high < v {\n        return high;\n    }\n    return at_least;\n}\n\nfn bound_float", "clamp order on an inverted range"),
    ("    let norm = float_of(bounded - min) / float_of(max - min) * 100.0;\n    if drive_mode {\n        return 100.0 - norm;",
     "    let norm = float_of(bounded - min) / float_of(max - min) * 100.0;\n    if !drive_mode {\n        return 100.0 - norm;", "drive mode inverted"),
    ("    let bounded = bound_float(v, 0.0, 100.0);", "    let bounded = v;", "percent not clamped going back"),
    ("        return -norm;", "        return norm;", "signed percent ignores drive mode"),
    ('    let magnitude = raw & (1 << bit) - 1;', '    let magnitude = raw & (1 << (bit + 1)) - 1;', "sign bit kept in the magnitude"),
    ("    if magnitude > (1 << bit) - 1 {", "    if magnitude > (1 << bit) {", "encode accepts one too many"),
    ("    return min != max;", "    return min < max;", "usable refuses inverted ranges LeRobot accepts"),
]


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def caught(cancho, python, tree):
    if run([cancho, "build", "--std", "tests/calibration_driver.cho", F, "-o", "build/calibration_driver"], tree).returncode:
        return "does not build"
    if run([cancho, "test", "tests/calibration_test.cho", F, "--std"], tree).returncode:
        return "cancho tests"
    if run([python, "tests/differential_calibration.py", "build/calibration_driver", "--cases", "30"], tree).returncode:
        return "differential"
    return None


def main():
    # Absolute: each mutant is built in a temporary copy, where a path relative to the checkout means nothing.
    cancho, python = str(Path(sys.argv[1]).resolve()), sys.argv[2]
    missed = []
    for before, after, what in MUTANTS:
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "t"
            shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(".git", "build"))
            (tree / "build").mkdir()
            f = tree / F
            text = f.read_text()
            if before == after or text.count(before) != 1:
                print(f"STALE  {what}: the text to mutate is not in {F} exactly once")
                missed.append(what)
                continue
            f.write_text(text.replace(before, after))
            by = caught(cancho, python, tree)
            print(f"{'caught' if by else 'MISSED'}  {what}" + (f"  (by {by})" if by else ""))
            if not by:
                missed.append(what)
    print(f"{len(MUTANTS) - len(missed)} of {len(MUTANTS)} mutants caught")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
