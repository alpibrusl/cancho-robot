#!/usr/bin/env python3
"""Gate A3 (docs/design.md sections 3 and 8): the write path is the one the design draws, checked over the source.

Reading and writing are both `ffi("libc")`, so the compiler cannot tell a function that reads positions from one
that writes goals. The write path is kept narrow by construction, and this gate checks the construction:

  - `feetech_write` (the only builder of WRITE / SYNC WRITE packets) is imported only by `serial_write`;
  - `serial_write` is imported, and its functions called, only by `core`;
  - `serial.send` (bytes out with no reply expected) is called only by `serial_write`;
  - `tools/scan.cho` imports neither `core` nor `serial_write`.

Test drivers and tests (tests/) are exempt: they are not part of a program that runs against the robot. This is a
call-site check, weaker than the compiler's proof (design section 3 says so); `--self-test` shows it can fail by
adding a forbidden call to a copy of the source.

Usage:  python3 tests/write_path_check.py [--self-test]
"""
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RULES = [
    (re.compile(r"^\s*import\s+feetech_write\s*;", re.M), {"src/serial_write.cho"}, "imports feetech_write"),
    (re.compile(r"^\s*import\s+serial_write\s*;", re.M), {"src/core.cho"}, "imports serial_write"),
    (re.compile(r"\bserial_write\.\w+\s*\("), {"src/core.cho"}, "calls serial_write"),
    (re.compile(r"\bserial\.send\s*\("), {"src/serial_write.cho"}, "calls serial.send"),
]


def check(root):
    problems = []
    for path in sorted(list((root / "src").glob("*.cho")) + list((root / "tools").glob("*.cho"))):
        rel = path.relative_to(root).as_posix()
        text = path.read_text()
        for pattern, allowed, what in RULES:
            if rel not in allowed and pattern.search(text):
                problems.append(f"{rel} {what}")
    scan = root / "tools" / "scan.cho"
    if scan.exists() and re.search(r"^\s*import\s+(core|serial_write)\s*;", scan.read_text(), re.M):
        problems.append("tools/scan.cho imports a write path")
    return problems


def main():
    problems = check(ROOT)
    if "--self-test" in sys.argv:
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "t"
            shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(".git", "build"))
            play = tree / "tools" / "play.cho"
            play.write_text(play.read_text().replace("import core;", "import core;\nimport serial_write;", 1))
            injected = check(tree)
            if not any("tools/play.cho imports serial_write" in p for p in injected):
                print("write path: the self-test's forbidden import was NOT caught")
                return 1
            print("write path: self-test caught", injected)
    if problems:
        print("write path: FAILURES")
        for p in problems:
            print("  " + p)
        return 1
    print("write path: OK (feetech_write <- serial_write <- core; serial.send <- serial_write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
