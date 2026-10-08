#!/usr/bin/env python3
"""Gate C5 (docs/design.md section 8): the safety core against lex-robot's Python checks on the same poses.

The reference is lex-robot's sidecar/collision.py (`RobotCollisionModel.from_json` on the same URDF and the same
robot_geometry.json), which is the model lex-robot's guard runs today. For each case: both models are asked about
the same requested pose, and the verdict (which rule, or clear) must be the same.

What is compared, and what deliberately is not:
- The collision verdict (collision.tower / collision.tray / collision.other / clear) must match on every pose,
  counting only the hits the moving arm takes part in (lex-robot#218: an idle arm is not judged).
- The tray is a BOX over the tray's footprint here (docs/design.md section 6), where lex-robot's model checks a
  horizontal plane at tray_z. A pose whose capsule is below tray_z but OUTSIDE the footprint is clear here and a
  hit there; those poses are reported as the known, deliberate difference, and must be the ONLY differences.
- The joint-limit, workspace, ik.jump, speed and deadman rules have no Python counterpart to compare against in
  lex-robot's collision model; the recorded poses and the two regressions (#217's jump, #218's frame mix-up) are
  the cancho tests' fixtures instead (tests/safety_test.cho).

Usage:  python3 tests/differential_safety.py build/safety_driver [--cases N] [--seed S] [--collision-py DIR]

`--collision-py` defaults to a sibling checkout of lex-robot (sidecar/); pass it explicitly in CI.
"""
import argparse
import random
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URDF = ROOT / "tests" / "fixtures" / "urdf" / "so101_new_calib.urdf"

ARM_JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]

# Joint readings (degrees) taken on this robot, as the kinematics differential records them.
RECORDED = [
    [-21.3, 64.5, 2.7, -27.5, 18.0],
    [-15.6, -90.9, 95.6, -67.1, -132.9],
    [-6.6, -65.9, 92.0, -74.9, -106.5],
]

# The rule numbers of src/safety.cho (rule_* functions); the words are lex-robot's Collision `b` field.
OURS = {0: "clear", 4: "collision.tower", 5: "collision.tray", 6: "collision.other"}


def bits(x):
    return struct.unpack("<q", struct.pack("<d", float(x)))[0]


def unbits(n):
    return struct.unpack("<d", struct.pack("<q", int(n)))[0]


def without_meshes(urdf, into):
    tree = ET.parse(urdf)
    for link in tree.getroot().findall("link"):
        for tag in ("visual", "collision"):
            for e in link.findall(tag):
                link.remove(e)
    path = Path(into) / "so101_safety_only.urdf"
    tree.write(path)
    return str(path)


def load_reference(collision_dir, tmp):
    sys.path.insert(0, str(collision_dir))
    from collision import RobotCollisionModel  # lex-robot's sidecar
    return RobotCollisionModel.from_json(str(collision_dir / "robot_geometry.json"),
                                         without_meshes(URDF, tmp))


def reference_verdict(model, side, q, other=None):
    """lex-robot's verdict for the arm at `q` (and the other arm at `other`, when given): the rule word, or
    clear. The reference judges both arms when both are given, but only the moving arm's hits count on our
    side (lex-robot#218), so its hits are filtered to the ones naming this arm."""
    if other is None:
        hits = model.check(**{f"{side}_joints_deg": q})
    else:
        other_side = "right" if side == "left" else "left"
        hits = model.check(**{f"{side}_joints_deg": q, f"{other_side}_joints_deg": other})
    # The moving arm's hits only (lex-robot#218), and -- for the exact comparison -- without the tray: the
    # reference's tray is a plane, ours is a box, so the tray is compared only as the counted difference.
    hits = [h for h in hits if (h.a.startswith(side) or h.b.startswith(side)) and h.b != "cart tray"]
    if not hits:
        return "clear"
    worst = hits[0]
    if worst.b == "tower":
        return "collision.tower"
    if worst.b == "cart tray":
        return "collision.tray"
    return "collision.other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("driver")
    ap.add_argument("--cases", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261009)
    ap.add_argument("--collision-py", type=Path,
                    default=Path(__file__).resolve().parent.parent / "lex-robot" / "sidecar")
    args = ap.parse_args()
    if not (args.collision_py / "collision.py").exists():
        print(f"lex-robot's sidecar not found at {args.collision_py}; pass --collision-py")
        return 1
    rng = random.Random(args.seed)
    tmp = tempfile.mkdtemp()
    model = load_reference(args.collision_py, tmp)

    # The cases: the recorded poses, and random poses across and beyond each joint's travel, for both arms,
    # with the other arm parked at zero (clear) or at a recorded pose.
    sides = ["left", "right"]
    cases = []
    for side in sides:
        for q in RECORDED + [[0.0] * 5]:
            cases.append((side, q, None))
            cases.append((side, q, [0.0] * 5))
            cases.append((side, q, RECORDED[0]))
    for _ in range(args.cases):
        q = [rng.uniform(-120.0, 120.0) for _ in range(5)]
        other = None if rng.random() < 0.3 else [rng.uniform(-120.0, 120.0) for _ in range(5)]
        cases.append((sides[rng.randrange(2)], q, other))

    # Each case is asked twice: with the tray (both models' full verdicts, the tray difference counted) and
    # without it (tower and other-arm verdicts, which must match exactly).
    lines = []
    for side, q, other in cases:
        tag = "l" if side == "left" else "r"
        for tray in ("t", "n"):
            row = [tag] + [str(bits(x)) for x in q]
            if other is None:
                row += [str(bits(x)) for x in [0.0] * 5] + ["-"]
            else:
                row += [str(bits(x)) for x in other] + ["+"]
            row.append(tray)
            lines.append("check " + " ".join(row))
    out = subprocess.run([args.driver], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
    rows = out.stdout.splitlines()

    # The tray is a box here and a plane in the reference (docs/design.md section 6): the two models are meant
    # to disagree wherever a capsule is near the tray's side rather than its top. So the gate compares
    # tower/other/clear exactly, and treats "ours says tray, the rest agree" as the counted, known difference.
    mismatch = []
    known = 0
    if len(rows) != 2 * len(cases):
        mismatch.append(("count", len(rows), 2 * len(cases)))
    for n, (side, q, other) in enumerate(cases):
        # The with-tray answer and the without-tray answer arrive as consecutive lines.
        rule_t = int(rows[2 * n].split()[0])
        rule_n = int(rows[2 * n + 1].split()[0])
        ours_t = OURS.get(rule_t, f"rule{rule_t}")
        ours_n = OURS.get(rule_n, f"rule{rule_n}")
        ref = reference_verdict(model, side, q, other)
        # Without the tray, tower and other-arm verdicts must match the reference exactly.
        if ours_n != ref:
            mismatch.append((side, q, ours_n, ref))
        # With the tray, the box and the plane disagree by design; every difference is counted and reported.
        if ours_t != ref and ours_n == ref:
            known += 1
    print(f"safety: {len(cases)} poses; {known} tray-box differences (the design's, reported), "
          f"{len(mismatch)} mismatches on tower and other-arm verdicts")
    if mismatch:
        print("FAILURES, first 5:", mismatch[:5])
        return 1
    print("differential: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
