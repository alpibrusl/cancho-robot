#!/usr/bin/env python3
"""Task #10's gates can fail: each deliberately wrong teleoperation below must be caught by tests/keys_test.cho, by
tests/teleop_gates.py (a pseudo-terminal and the simulated bus's trace), or refused by the compiler.

Usage:  python3 tests/mutate_teleop.py path/to/cancho
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ["tools/teleop.cho", "src/libc.cho", "src/keys.cho", "src/term.cho", "src/core.cho", "src/serial.cho",
           "src/serial_write.cho", "src/feetech.cho", "src/feetech_write.cho", "src/calibration.cho", "src/robot.cho",
           "src/safety.cho", "src/kinematics.cho", "src/so101.cho"]


def build_mutants():
    def one(path, before, after, what):
        text = (ROOT / path).read_text()
        assert text.count(before) == 1, (what, text.count(before))
        assert before != after, ("identity mutant", what)
        return (path, before, after, what)

    return [
        one("src/keys.cho", "        if ev[2] == key_up() {\n            until[b] = -1;",
            "        if false {\n            until[b] = -1;", "a key release ignored (the arm runs on after it)"),
        one("src/keys.cho", "pub fn kitty_hold_ms() -> [] int {\n    return 1000;\n}",
            "pub fn kitty_hold_ms() -> [] int {\n    return 100000;\n}", "no guard for a connection gone quiet"),
        one("src/keys.cho", "    if code == 'q' {\n        return 2;\n    }\n    if code == 'a' {\n        return 3;\n    }",
            "    if code == 'q' {\n        return 3;\n    }\n    if code == 'a' {\n        return 2;\n    }",
            "q and a swapped (the shoulder the wrong way)"),
        one("tools/teleop.cho", "                    if rule == safety.rule_joint() && joint[0] >= 0 {",
            "                    if false && joint[0] >= 0 {", "one joint at its span stops them all"),
        one("src/keys.cho", "pub fn repeat_hold_ms() -> [] int {\n    return 150;\n}",
            "pub fn repeat_hold_ms() -> [] int {\n    return 2000;\n}", "plain keys held two seconds after the repeats stop"),
        one("tools/teleop.cho", "                        kitty = true;", "                        kitty = false;",
            "the kitty protocol never used (keys cannot combine)"),
        one("src/keys.cho", "        if held(until, j * 2 + 1, now) {\n            v = v - speed(j);",
            "        if held(until, j * 2 + 1, now) {\n            v = v + speed(j);", "the minus key of a pair moves the plus way"),
    ]


def caught(cancho, tree):
    b = subprocess.run([cancho, "build", "--std", *SOURCES, "-o", "build/teleop"], cwd=tree, capture_output=True, text=True)
    if b.returncode:
        return "does not build"
    t = subprocess.run([cancho, "test", "tests/keys_test.cho", "src/keys.cho", "--std"], cwd=tree, capture_output=True,
                       text=True)
    if t.returncode:
        return "keys tests"
    r = subprocess.run([sys.executable, "tests/teleop_gates.py", "build/teleop"], cwd=tree, capture_output=True,
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
