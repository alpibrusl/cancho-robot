#!/usr/bin/env python3
"""Gate C4 can fail: each deliberately wrong forward kinematics below must be caught by the cancho tests, by the
check that src/so101.cho is what the URDF generates, or by the differential against placo.

Usage:  python3 tests/mutate_kinematics.py path/to/cancho python3
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
K, S = "src/kinematics.cho", "src/so101.cho"

MUTANTS = [
    (K, "    let r01 = cy * sp * sr - sy * cr;", "    let r01 = cy * sp * sr + sy * cr;", "a sign in the rpy rotation"),
    (K, "    let r20 = -sp;", "    let r20 = sp;", "rpy composed in another order"),
    (K, "    t[1] = r01 * cq - r00 * sq;", "    t[1] = r01 * cq + r00 * sq;", "the joint turns the wrong way"),
    (K, "    return degrees * (pi() / 180.0);", "    return degrees * (pi() / 360.0);", "half the angle"),
    (K, "            sum = sum + m[r * 4 + k] * t[k * 4 + c];", "            sum = sum + m[r * 4 + k] * t[c * 4 + k];",
     "multiplies by the transpose"),
    (K, "        var k = 0;\n        while k < so101.transforms() {\n            var q = 0.0;\n            if k < so101.moving() {\n                q = radians(degrees[k]);\n            }\n            element(k, q, t);\n            times(pose, t, scratch);",
       "        var k = 0;\n        while k < so101.moving() {\n            var q = 0.0;\n            if k < so101.moving() {\n                q = radians(degrees[k]);\n            }\n            element(k, q, t);\n            times(pose, t, scratch);",
       "the fixed gripper frame left out"),
    (K, "    t[3] = so101.xyz(k, 0);", "    t[3] = so101.xyz(k, 1);", "a translation component swapped"),
    (K, "                q = radians(degrees[k]);\n            }\n            element(k, q, t);\n            times(pose, t, scratch);",
       "                q = radians(degrees[4 - k]);\n            }\n            element(k, q, t);\n            times(pose, t, scratch);",
       "angles applied to the wrong joints"),
    (K, "    m[10] = 1.0;", "    m[10] = 0.0;", "a wrong identity"),
    (S, "        return 0.0624;", "        return 0.0625;", "a hand-edited chain constant"),
]


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def caught(cancho, python, tree):
    if run([cancho, "build", "--std", "tests/kinematics_driver.cho", K, S, "-o", "build/kinematics_driver"], tree).returncode:
        return "does not build"
    if run([cancho, "test", "tests/kinematics_test.cho", K, S, "--std"], tree).returncode:
        return "cancho tests"
    if run([python, "scripts/gen_chain.py", "tests/fixtures/urdf/so101_new_calib.urdf", "--check", S], tree).returncode:
        return "the generated chain check"
    if run([python, "tests/differential_kinematics.py", "build/kinematics_driver", "--cases", "300"], tree).returncode:
        return "differential"
    return None


def main():
    # Absolute: each mutant is built in a temporary copy, where a path relative to the checkout means nothing.
    cancho, python = str(Path(sys.argv[1]).resolve()), sys.argv[2]
    missed = []
    for path, before, after, what in MUTANTS:
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
    print(f"{len(MUTANTS) - len(missed)} of {len(MUTANTS)} mutants caught")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
