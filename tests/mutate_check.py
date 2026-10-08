#!/usr/bin/env python3
"""The toolbox contract's gates can fail: each deliberately wrong table or mismatch below must be caught
by the cancho tests (tests/check_test.cho) or the conformance harness (tests/conformance_check.py).

The contract's whole claim is "everything derived from the same tables the code runs on", so the mutants
break exactly that: a rule the code emits but the catalogue does not name, a bound introspect reports that
the core does not run, a tag renamed in one place but not the other, a rule the catalogue lists that no
input reaches.

Usage:  python3 tests/mutate_check.py path/to/cancho python3
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
R = "src/rules.cho"
C = "src/check.cho"
S = "src/safety.cho"

MUTANTS = [
    (R, "limit.joint|8|never|a requested angle is outside the joint's measured span;",
     "limit.joint|8|never|a requested angle is outside the joint's measured range;",
     "a rule's summary changed in the catalogue but not in the code that names it"),
    (R, "collision.tower|8|never|the arm's capsules would hit the tower;",
     "collision.tower|8|never|the arm's capsules would hit the mast;",
     "a rule renamed in the catalogue only (introspect and refusals must agree)"),
    (R, "args.malformed|2|never|a line the controller cannot parse into its command's shape",
     "args.malformed|2|never|a line the controller cannot read",
     "the malformed rule's summary drifted"),
    (C, "pub fn schema() -> [] &static [byte] {\n    return \"check.v1\";\n}",
     "pub fn schema() -> [] &static [byte] {\n    return \"check.v2\";\n}",
     "the schema version bumped alone"),
    (C, "    w = json.put_key(heap, w, \"speed_limit\");\n    w = json.put_float(heap, w, safety.speed_limit());",
     "    w = json.put_key(heap, w, \"speed_limit\");\n    w = json.put_float(heap, w, 2.5);",
     "introspect reports a speed limit the core does not run"),
    (C, "    w = json.put_key(heap, w, \"deadman_bound_ms\");\n    w = json.put_int(heap, w, safety.deadman_bound_ms());",
     "    w = json.put_key(heap, w, \"deadman_bound_ms\");\n    w = json.put_int(heap, w, 5000);",
     "introspect reports a deadman bound the core does not run"),
]


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def caught(cancho, python, tree):
    if run([cancho, "build", "--std", "tests/check_driver.cho", "src/check.cho", "src/rules.cho",
            "src/safety.cho", "src/ik.cho", "src/kinematics.cho", "src/so101.cho",
            "-o", "build/check", "--backend", "cranelift"], tree).returncode:
        return "does not build"
    if run([cancho, "test", "tests/check_test.cho", "src/check.cho", "src/rules.cho", "src/safety.cho",
            "src/ik.cho", "src/kinematics.cho", "src/so101.cho", "--std", "--backend", "cranelift"],
           tree).returncode:
        return "cancho tests"
    r = run([python, "tests/conformance_check.py", "build/check"], tree)
    if r.returncode:
        return "conformance"
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
