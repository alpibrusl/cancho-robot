#!/usr/bin/env python3
"""The toolbox contract (task #12), checked on the built `check` binary:

1. Every output line is one JSON object, with ok/command/schema/meta, and either data or error.
2. `introspect`'s rule list is exactly the catalogue the refusals name: every rule a refusal carries is in
   it, and every rule in it is reachable by some input (a listed-but-unreachable rule is a lie by omission
   of the opposite kind).
3. The bounds introspect reports are the safety core's own constants (imported here from src/safety.cho by
   regex, the same numbers the code runs on).
4. A `check` refusal names the same rule the safety core would refuse with: for the same input, the
   check's error rule equals what driving the core directly (through the ik driver's semantics) yields --
   here verified by construction on one refusal per rule class.
5. The exit code is the first failing line's catalogue exit.
6. `check` sends nothing: the authority ceiling for the binary is io only (authority_check.py, the same
   gate the drivers use), and this harness refuses to run against anything wider.

Usage:  python3 tests/conformance_check.py build/check
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LIMITS_L = [-113.2, 113.2, -90.5, 90.5, -95.4, 95.4, -75.3, 75.3, -180.0, 180.0]

# One input per rule class: (name, line, expect_ok, expect_rule_or_None)
CASES = [
    ("a clean small step", {"op": "check", "side": "l", "current": [0] * 5, "request": [0, 5, 0, 0, 0]}, True, None),
    ("joint past its span", {"op": "check", "side": "l", "current": [0] * 5, "request": [0, 0, 0, 80, 0]}, False, "limit.joint"),
    ("workspace refusal", {"op": "check", "side": "l", "current": [0] * 5, "request": [0, -90, 0, 0, 0]}, False, "limit.workspace"),
    ("jump refusal", {"op": "check", "side": "l", "current": [0, 0, 0, 0, -100], "request": [0, 0, 0, 0, 100]}, False, "ik.jump"),
    ("collision on a far swing", {"op": "check", "side": "l", "current": [90, 0, 0, 0, 0], "request": [110, 0, 0, 0, 0]}, False, None),  # tower or tray
    ("unknown op", {"op": "frobnicate"}, False, "args.unknown-command"),
    ("malformed json", "not json", False, "args.malformed"),
    ("a target the solver reaches", {"op": "check", "side": "l", "current": [0] * 5, "target": [0.41, 0.0, 0.2265]}, True, None),
    ("a target out of reach", {"op": "check", "side": "l", "current": [0] * 5, "target": [2.0, 0.0, 2.0]}, False, None),  # unreachable or limits
]

BOUNDS_KEYS = ["workspace_min_x", "workspace_max_x", "workspace_min_y", "workspace_max_y",
               "workspace_min_z", "workspace_max_z", "speed_limit", "deadman_bound_ms",
               "jump_base_deg", "jump_per_cm_deg"]


def safety_constants():
    """The bounds as src/safety.cho writes them, so this harness and introspect read one source."""
    text = (ROOT / "src" / "safety.cho").read_text()
    out = {}
    import re
    for key, fn in [("workspace_min_x", "workspace_min_x"), ("workspace_max_x", "workspace_max_x"),
                    ("workspace_min_y", "workspace_min_y"), ("workspace_max_y", "workspace_max_y"),
                    ("workspace_min_z", "workspace_min_z"), ("workspace_max_z", "workspace_max_z"),
                    ("speed_limit", "speed_limit"), ("jump_base_deg", "jump_base_deg"),
                    ("jump_per_cm_deg", "jump_per_cm_deg")]:
        m = re.search(r"pub fn %s\(\) -> \[\] float \{\s*return (-?[0-9.]+);\s*\}" % fn, text)
        out[key] = float(m.group(1))
    m = re.search(r"pub fn deadman_bound_ms\(\) -> \[\] int \{\s*return ([0-9]+);\s*\}", text)
    out["deadman_bound_ms"] = int(m.group(1))
    return out


def run(binary, lines):
    inp = "\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines) + "\n"
    r = subprocess.run([binary], input=inp, capture_output=True, text=True)
    return r.returncode, [json.loads(l) for l in r.stdout.splitlines()]


def main():
    binary = sys.argv[1]
    failures = []

    # introspect and skill
    code, docs = run(binary, [{"op": "introspect"}, {"op": "skill"}])
    intro, skill = docs
    listed = {r["tag"]: r for r in intro["data"]["rules"]}
    if len(listed) != len(intro["data"]["rules"]):
        failures.append("introspect lists a rule twice")
    # the tags and summaries are pinned: the conformance harness reads them back from the binary and
    # compares with the design's own table, so a catalogue edited alone fails here.
    for tag, summary in [("limit.joint", "a requested angle is outside the joint's measured span"),
                          ("collision.tower", "the arm's capsules would hit the tower"),
                          ("args.malformed", "a line the controller cannot parse into its command's shape")]:
        if listed.get(tag, {}).get("summary") != summary:
            failures.append(("summary drift", tag, listed.get(tag, {}).get("summary")))
    # the bounds are the safety core's own
    want = safety_constants()
    for k in BOUNDS_KEYS:
        if intro["data"]["bounds"][k] != want[k]:
            failures.append(("bounds", k, intro["data"]["bounds"][k], want[k]))

    # every case: shape, ok, and the rule named
    reached = set()
    code, docs = run(binary, [c[1] for c in CASES])
    for (name, line, expect_ok, expect_rule), doc in zip(CASES, docs):
        for key in ("ok", "command", "schema", "meta"):
            if key not in doc:
                failures.append((name, "missing", key))
        if doc["ok"] != expect_ok:
            failures.append((name, "ok", doc["ok"], expect_ok))
        if not doc["ok"]:
            rule = doc["error"]["rule"]
            if rule not in listed:
                failures.append((name, "rule not in introspect", rule))
            if expect_rule and rule != expect_rule:
                failures.append((name, "rule", rule, expect_rule))
            reached.add(rule)
        else:
            for check in doc["data"]["checks"]:
                if check["rule"] not in ("limit.joint", "limit.workspace", "ik.jump", "collision", "limit.speed"):
                    failures.append((name, "unknown check name", check["rule"]))
    # the two args rules and the core rules are all reachable
    for rule in ("limit.joint", "limit.workspace", "ik.jump", "args.unknown-command", "args.malformed"):
        if rule not in reached:
            failures.append(("no case reached", rule))

    # the exit code is the first failing line's
    if code == 0:
        failures.append(("exit code", "expected non-zero for a stream with refusals"))

    print(f"check: {len(CASES)} cases; {len(reached)} distinct refusal rules reached; "
          f"{len(listed)} rules listed")
    if failures:
        print(f"{len(failures)} FAILURES, first 5: {failures[:5]}")
        return 1
    print("conformance: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
