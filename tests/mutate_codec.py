#!/usr/bin/env python3
"""The codec's gates can fail: each deliberately wrong codec below must be caught by the cancho tests or by the
differential test against scservo_sdk. A mutant nobody catches means a gate that cannot see that defect.

Usage:  python3 tests/mutate_codec.py path/to/cancho python3
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (file, original text, mutated text, what it breaks)
MUTANTS = [
    ("src/feetech.cho", "return 255 - sum % 256;", "return 254 - sum % 256;", "checksum off by one"),
    ("src/feetech.cho", "out[3] = byte_of(len(params) + 2);", "out[3] = byte_of(len(params) + 3);", "length field"),
    ("src/feetech.cho", "if id > max_id() || length > max_packet() || error > 127 {",
     "if id > max_id() || length > max_packet() || error > 254 {", "error byte over 127 taken as a header"),
    ("src/feetech.cho", "if id > max_id() || length > max_packet() || error > 127 {",
     "if length > max_packet() || error > 127 {", "impossible id taken as a header"),
    ("src/feetech.cho", "if length < 2 {", "if length < 1 {", "length 1 accepted"),
    ("src/feetech.cho", "let end = start + length + 4;", "let end = start + length + 3;", "packet end"),
    ("src/feetech.cho", "if id != expected_id {", "if id == 999 {", "another servo's reply accepted"),
    ("src/feetech.cho", "table[2] = start + 5;", "table[2] = start + 4;", "parameter offset"),
    ("src/feetech.cho", "table[3] = length - 2;", "table[3] = length - 1;", "parameter count"),
    ("src/feetech.cho", "if int_of(buf[end - 1]) != checksum(buf, start + 2, end - 1) {",
     "if int_of(buf[end - 1]) != checksum(buf, start + 3, end - 1) {", "checksum range"),
    ("src/feetech.cho", "        if int_of(ids[k]) > max_id() {", "        if int_of(ids[k]) > 255 {", "sync read ids unchecked"),
    ("src/feetech.cho", "if !is_byte(address) || !is_byte(count) || count == 0 {\n        return error_bad_argument();\n    }\n    region a {\n        let p = alloc_slice[a](2, byte_of(0));\n        p[0] = byte_of(address);",
     "if !is_byte(address) || !is_byte(count) {\n        return error_bad_argument();\n    }\n    region a {\n        let p = alloc_slice[a](2, byte_of(0));\n        p[0] = byte_of(address);", "read of zero bytes allowed"),
    ("src/feetech.cho", "    if id < 0 || id > max_id() {\n        return error_bad_id();\n    }\n    region a {\n        let none",
     "    if id < 0 || id > broadcast_id() {\n        return error_bad_id();\n    }\n    region a {\n        let none", "broadcast ping allowed"),
    ("src/feetech_write.cho", "if len(blocks) % (count + 1) != 0 {", "if len(blocks) % count != 0 {", "sync write block size"),
    ("src/feetech_write.cho", "        k = k + count + 1;", "        k = k + count;", "sync write id walk"),
    ("src/feetech_write.cho", "    out[3] = byte_of(params + 2);", "    out[3] = byte_of(params + 1);", "write length field"),
    ("src/feetech_write.cho", "    return 131;", "    return 130;", "sync write instruction byte"),
]


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def caught(cancho, python, tree):
    build = run([cancho, "build", "--std", "tests/driver.cho", "src/feetech.cho", "src/feetech_write.cho",
                 "-o", "build/driver"], tree)
    if build.returncode != 0:
        return "does not build"
    for test in (["tests/feetech_test.cho", "src/feetech.cho"],
                 ["tests/feetech_write_test.cho", "src/feetech.cho", "src/feetech_write.cho"]):
        if run([cancho, "test", *test, "--std"], tree).returncode != 0:
            return "cancho tests"
    if run([python, "tests/differential_codec.py", "build/driver", "--cases", "3000"], tree).returncode != 0:
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
