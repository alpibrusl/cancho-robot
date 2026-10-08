#!/usr/bin/env python3
"""Gates A1 and A4 of docs/design.md section 8: a program's authority, diffed against a ceiling file.

    python3 tests/authority_check.py <cancho> <ceiling file> <source files...> [--expect-failure]

Runs `cancho authority --std --output json` over the sources and compares with the ceiling. The ceiling has one
label per line (`io_write`, or `net_in("8920")` for a label with an argument) and, for a program allowed to call C,
one `foreign <symbol>` line per symbol. It fails if the program performs a label or reaches a symbol the ceiling
does not list (authority grew: the ceiling is edited in the same change, in review), if the ceiling lists one the
program no longer uses (a ceiling only comes down, so a stale one is a failure too), if a program with no `foreign`
lines is not bounded, or if the report could not be produced. `--expect-failure` inverts the exit status, which is
how CI shows the gate can fail. Modelled on cancho-dns's gate 6.
"""

import json
import subprocess
import sys


def labels(report):
    out = set()
    for label in report["labels"]:
        out.add(label["name"] if label["argument"] is None else '%s("%s")' % (label["name"], label["argument"]))
    return out


def main():
    args = sys.argv[1:]
    expect_failure = "--expect-failure" in args
    args = [a for a in args if a != "--expect-failure"]
    cancho, ceiling_file, sources = args[0], args[1], args[2:]
    done = subprocess.run([cancho, "authority", "--std", "--output", "json", *sources], capture_output=True, text=True)
    problems = []
    performed = set()
    if done.returncode != 0:
        problems.append("`cancho authority` failed: " + done.stderr.strip()[-300:])
    else:
        report = json.loads(done.stdout)
        lines = [l.strip() for l in open(ceiling_file) if l.strip() and not l.lstrip().startswith("#")]
        allowed_symbols = {l.split()[1] for l in lines if l.startswith("foreign ")}
        ceiling = {l for l in lines if not l.startswith("foreign ")}
        performed = labels(report)
        for label in sorted(performed - ceiling):
            problems.append("performs %s, which the ceiling does not allow" % label)
        for label in sorted(ceiling - performed):
            problems.append("the ceiling allows %s, which nothing performs (a ceiling only comes down)" % label)
        reached = set(report["foreign_symbols"])
        for sym in sorted(reached - allowed_symbols):
            problems.append("reaches the foreign symbol %s, which the ceiling does not allow" % sym)
        for sym in sorted(allowed_symbols - reached):
            problems.append("the ceiling allows the foreign symbol %s, which nothing reaches" % sym)
        if not allowed_symbols and not report["bounded"]:
            problems.append("the report is not bounded: " + ", ".join(report["unbounded_by"]))
    for p in problems:
        print("authority: " + p)
    failed = bool(problems)
    if expect_failure:
        print("authority: the gate refused, as it was meant to" if failed
              else "authority: the gate did NOT refuse a ceiling it should have")
        sys.exit(0 if failed else 1)
    if not failed:
        print("authority: ok (%s)" % ", ".join(sorted(performed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
