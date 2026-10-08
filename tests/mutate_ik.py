#!/usr/bin/env python3
"""Gate C4 can fail: each deliberately wrong inverse kinematics below must be caught by the cancho tests
(tests/ik_test.cho) or by the differential against the Jacobian's own prediction (tests/differential_ik.py).

One mutant per lesson the issue names: the jump guard (lex-robot#217's 51 degrees for a 1 cm step), the live
state as the seed (never a cached pose), the damping, the Jacobian's sign, the convergence tolerance, and
the measured-spans check.

Usage:  python3 tests/mutate_ik.py path/to/cancho python3
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
K = "src/ik.cho"


def build_mutants():
    text = (ROOT / K).read_text()

    def one(before, after, what):
        assert text.count(before) == 1, (what, text.count(before))
        return (K, before, after, what)

    return [
        one("    let travel_cm = 100.0 * distance_m(from_degrees, to_degrees);",
            "    let travel_cm = 10000.0 * distance_m(from_degrees, to_degrees);",
            "the jump guard's bound made unrefusable"),
        one("    var i = 0;\n    while i < so101.moving() {\n        solution[i] = current[i];\n        i = i + 1;\n    }",
            "    var i = 0;\n    while i < so101.moving() {\n        solution[i] = 0.0;\n        i = i + 1;\n    }",
            "the solve seeded from zero, not the live state (the stale-state defect)"),
        one("pub fn damping_min() -> [] float { return 0.001; }",
            "pub fn damping_min() -> [] float { return 10.0; }",
            "the damping floor so high the step never moves"),
        one("        j[c] = (px - pose[3]) / d;",
            "        j[c] = (pose[3] - px) / d;",
            "the Jacobian's sign flipped"),
        one("pub fn tolerance_m() -> [] float { return 0.000001; }",
            "pub fn tolerance_m() -> [] float { return 0.05; }",
            "the convergence tolerance loosened to 5 cm"),
        one("            if solution[j1] < limits[j1 * 2] || solution[j1] > limits[j1 * 2 + 1] {",
            "            if solution[j1] < limits[j1 * 2] - 1000.0 || solution[j1] > limits[j1 * 2 + 1] + 1000.0 {",
            "the measured-spans check removed"),
    ]


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def caught(cancho, python, tree):
    if run([cancho, "build", "--std", "tests/ik_driver.cho", K, "src/kinematics.cho", "src/so101.cho",
            "-o", "build/ik_driver", "--backend", "cranelift"], tree).returncode:
        return "does not build"
    if run([cancho, "test", "tests/ik_test.cho", K, "src/kinematics.cho", "src/so101.cho",
            "--std", "--backend", "cranelift"], tree).returncode:
        return "cancho tests"
    r = run([python, "tests/differential_ik.py", "build/ik_driver", "--cases", "40"], tree)
    if r.returncode:
        return "differential"
    return None


def main():
    # Absolute: each mutant is built in a temporary copy, where a path relative to the checkout means nothing.
    cancho, python = str(Path(sys.argv[1]).resolve()), sys.argv[2]
    missed = []
    mutants = build_mutants()
    for path, before, after, what in mutants:
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "t"
            shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(".git", "build"))
            (tree / "build").mkdir()
            f = tree / path
            text = f.read_text()
            if before == after or text.count(before) != 1:
                print(f"STALE  {what}: the text to mutate is not in {path} exactly once")
                missed.append(what)
                continue
            f.write_text(text.replace(before, after))
            by = caught(cancho, python, tree)
            print(f"{'caught' if by else 'MISSED'}  {what}" + (f"  (by {by})" if by else ""))
            if not by:
                missed.append(what)
    print(f"{len(mutants) - len(missed)} of {len(mutants)} mutants caught")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
