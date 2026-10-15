#!/usr/bin/env python3
"""Task #9's gates can fail: each deliberately wrong actuation below must be caught by tests/core_test.cho, by
tests/actuation_gates.py (the simulated bus's trace), or refused by the compiler.

Usage:  python3 tests/mutate_actuation.py path/to/cancho
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ["tools/play.cho", "src/core.cho", "src/libc.cho", "src/serial.cho", "src/serial_write.cho", "src/feetech.cho",
           "src/feetech_write.cho", "src/calibration.cho", "src/robot.cho", "src/safety.cho", "src/kinematics.cho",
           "src/so101.cho"]


def build_mutants():
    def one(path, before, after, what):
        text = (ROOT / path).read_text()
        assert text.count(before) == 1, (what, text.count(before))
        assert before != after, ("identity mutant", what)
        return (path, before, after, what)

    return [
        one("src/core.cho", "        if rule == safety.rule_ok() {\n            let ticks = alloc_slice[a](robot.joints(), 0);",
            "        if true {\n            let ticks = alloc_slice[a](robot.joints(), 0);",
            "a refused request written anyway"),
        one("src/core.cho", "            if serial_write.commit_goals(ffi, fd, ids, ticks, robot.joints()) < 0 {\n                status = rule_bus();\n            } else if serial_write.set_torque(ffi, fd, ids, robot.joints(), true) < 0 {",
            "            if serial_write.set_torque(ffi, fd, ids, robot.joints(), true) < 0 {\n                status = rule_bus();\n            } else if serial_write.commit_goals(ffi, fd, ids, ticks, robot.joints()) < 0 {",
            "torque enabled before the goal is set to the present position"),
        one("src/core.cho", "pub fn lead_deg() -> [] float {\n    return 8.0;\n}",
            "pub fn lead_deg() -> [] float {\n    return 80.0;\n}", "the lead bound relaxed to 80 degrees"),
        one("tools/play.cho", "                } else if since_better >= 50 {", "                } else if since_better >= 100000 {",
            "the stall never detected"),
        one("src/core.cho", "    if matches(ffi, fd, robot.left()) {\n        return robot.left();\n    }",
            "    if true {\n        return robot.left();\n    }", "every bus taken for the left arm"),
        one("src/core.cho", "            } else if serial_write.set_torque(ffi, fd, ids, robot.joints(), true) < 0 {",
            "            } else if serial_write.set_torque(ffi, fd, ids, robot.joints(), false) < 0 {",
            "torque disabled where it should be enabled"),
        one("src/serial_write.cho", "            let raw = calibration.encode_sign(ticks[k], calibration.sign_bit_position());",
            "            let raw = ticks[k] + 40;", "goals written 40 ticks off what was approved"),
        one("src/core.cho", "                goals[k] = lead(previous[k] + (request[k] - previous[k]) * step, current[k], lead_deg());",
            "                goals[k] = lead(arm[k], current[k], lead_deg());",
            "the speed limit interpolated from the present position (the elbow stall)"),
    ]


def caught(cancho, tree):
    b = subprocess.run([cancho, "build", "--std", *SOURCES, "-o", "build/play"], cwd=tree, capture_output=True, text=True)
    if b.returncode:
        return "does not build"
    t = subprocess.run([cancho, "test", "tests/core_test.cho", *SOURCES[1:], "--std"], cwd=tree, capture_output=True,
                       text=True)
    if t.returncode:
        return "core tests"
    r = subprocess.run([sys.executable, "tests/actuation_gates.py", "build/play"], cwd=tree, capture_output=True,
                       text=True, timeout=900)
    if r.returncode:
        failed = [l.split("  ", 1)[1].split(":")[0] for l in r.stdout.splitlines() if l.startswith("FAIL")]
        return "gates: " + ", ".join(failed)
    return None


def main():
    cancho = str(Path(sys.argv[1]).resolve())
    missed = []
    mutants = build_mutants()
    for path, before, after, what in mutants:
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "t"
            shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(".git", "build"))
            (tree / "build").mkdir()
            f = tree / path
            f.write_text(f.read_text().replace(before, after))
            by = caught(cancho, tree)
            print(f"{'caught' if by else 'MISSED'}  {what}" + (f"  ({by})" if by else ""), flush=True)
            if not by:
                missed.append(what)
    print(f"{len(mutants) - len(missed)} of {len(mutants)} mutants caught")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
