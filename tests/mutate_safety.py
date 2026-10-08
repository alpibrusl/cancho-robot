#!/usr/bin/env python3
"""Gate C5 can fail: each deliberately wrong safety check below must be caught by the cancho tests
(tests/safety_test.cho) or by the differential against lex-robot's model (tests/differential_safety.py).

One mutant per check (docs/design.md section 6): the joint limits widened, the workspace bound loosened, the
jump bound relaxed, a collision distance sign, the tray's box, the margin, the speed limit, the deadman bound.

Usage:  python3 tests/mutate_safety.py path/to/cancho python3 --collision-py DIR
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
K = "src/safety.cho"

def build_mutants():
    text = (ROOT / K).read_text()

    def one(before, after, what):
        assert text.count(before) == 1, (what, text.count(before))
        return (K, before, after, what)

    return [
        one("pub fn workspace_min_x() -> [] float {\n    return 0.05;\n}",
            "pub fn workspace_min_x() -> [] float {\n    return -1.0;\n}",
            "the workspace bound's x floor removed"),
        one("pub fn workspace_max_z() -> [] float {\n    return 0.50;\n}",
            "pub fn workspace_max_z() -> [] float {\n    return 5.0;\n}",
            "the workspace bound's z ceiling removed"),
        one("pub fn jump_base_deg() -> [] float {\n    return 15.0;\n}",
            "pub fn jump_base_deg() -> [] float {\n    return 400.0;\n}",
            "the ik.jump allowance made unrefusable"),
        one("pub fn deadman_bound_ms() -> [] int {\n    return 300;\n}",
            "pub fn deadman_bound_ms() -> [] int {\n    return 100000;\n}",
            "the deadman bound made unrefusable"),
        one("pub fn speed_limit() -> [] float {\n    return 0.25;\n}",
            "pub fn speed_limit() -> [] float {\n    return 1000.0;\n}",
            "the speed limit removed"),
        one("    return d - c1.radius - c2.radius;",
            "    return d + c1.radius + c2.radius;",
            "the capsule clearance never negative"),
        one("pub fn margin() -> [] float {\n    return 0.01;\n}",
            "pub fn margin() -> [] float {\n    return -10.0;\n}",
            "the collision margin refuses nothing"),
        one("pub fn mounted_links() -> [] int {\n    return 2;\n}",
            "pub fn mounted_links() -> [] int {\n    return 99;\n}",
            "the tray never checked (all links 'mounted')"),
        one("    // limit.joint: the request's angles are inside the measured spans.\n    var i = 0;\n    while i < so101.moving() {",
            "    // limit.joint: the request's angles are inside the measured spans.\n    var i = 0;\n    while i < 0 {",
            "the joint limits never checked"),
        one("pub fn tray_half_x() -> [] float {\n    return 0.225;\n}",
            "pub fn tray_half_x() -> [] float {\n    return 100.0;\n}",
            "the tray's box footprint made everything a tray hit"),
        one("pub fn tray_half_y() -> [] float {\n    return 0.175;\n}",
            "pub fn tray_half_y() -> [] float {\n    return 100.0;\n}",
            "the tray's box footprint widened over the whole floor"),
        one("    var j = 1;\n    while j < len(caps) {",
            "    var j = 1;\n    while j < 0 {",
            "the other arm never checked"),
    ]


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)

def caught(cancho, python, tree, collision_py):
    if run([cancho, "build", "--std", "tests/safety_driver.cho", K, "src/kinematics.cho", "src/so101.cho",
            "-o", "build/safety_driver", "--backend", "cranelift"], tree).returncode:
        return "does not build"
    if run([cancho, "test", "tests/safety_test.cho", K, "src/kinematics.cho", "src/so101.cho",
            "--std", "--backend", "cranelift"], tree).returncode:
        return "cancho tests"
    r = run([python, "tests/differential_safety.py", "build/safety_driver", "--cases", "80",
             "--collision-py", str(Path(collision_py).resolve())], tree)
    if r.returncode:
        return "differential"
    return None

def main():
    cancho, python = str(Path(sys.argv[1]).resolve()), sys.argv[2]
    collision_py = sys.argv[3] if len(sys.argv) > 3 else str(ROOT / "lex-robot" / "sidecar")
    if not (Path(collision_py) / "collision.py").exists():
        print(f"lex-robot's sidecar not found at {collision_py}")
        return 1
    missed = []
    for path, before, after, what in build_mutants():
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "t"
            shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(".git", "build"))
            (tree / "build").mkdir()
            f = tree / path
            text = f.read_text()
            if text.count(before) != 1:
                print(f"STALE  {what}: the text to mutate is not in {path} exactly once")
                missed.append(what)
                continue
            f.write_text(text.replace(before, after))
            by = caught(cancho, python, tree, collision_py)
            print(f"{'caught' if by else 'MISSED'}  {what}" + (f"  (by {by})" if by else ""))
            if not by:
                missed.append(what)
    print(f"{len(build_mutants()) - len(missed)} of {len(build_mutants())} mutants caught")
    return 1 if missed else 0

if __name__ == "__main__":
    sys.exit(main())
