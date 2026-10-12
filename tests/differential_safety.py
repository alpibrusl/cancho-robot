#!/usr/bin/env python3
"""Gate C5 (docs/design.md section 8): the safety core against lex-robot's Python checks on the same poses.

The reference is lex-robot's sidecar/collision.py (`RobotCollisionModel.from_json` on the same URDF and the same
robot_geometry.json), at lex-robot#218 (branch fix/workspace-frames, pinned by commit in CI): there the cart is a
box from the floor to tray_z over the tray's footprint, as it is here (docs/design.md section 6). lex-robot's main
still checks an infinite plane at tray_z -- the defect #218 fixes -- so main is not the oracle.

What is compared, and what deliberately is not:
- Each capsule's cart clearance must lie inside the reference's own bound. lex-robot samples the segment
  (`capsule_box_clearance`, BOX_SAMPLES points) and subtracts half a spacing, so its answer d_ref satisfies
  d_ref <= exact <= d_ref + length / (2 * BOX_SAMPLES). Ours is exact, so it must land in that interval. The
  reference runs at BOX_SAMPLES = 256 here, which narrows the interval to about a quarter of a millimetre.
- The collision verdict (collision.tower / collision.tray / collision.other / clear) must match on every pose,
  with and without the cart, counting only the hits the moving arm takes part in (lex-robot#218: an idle arm is
  not judged). The only verdicts allowed to differ are the ones the reference's bound leaves open: a cart
  clearance within that interval of the margin, or a cart hit within it of another hit (the worst-first order
  can flip). Those are counted and reported.
- The joint-limit, workspace, ik.jump, speed and deadman rules have no Python counterpart to compare against in
  lex-robot's collision model; the recorded poses and the two regressions (#217's jump, #218's frame mix-up) are
  the cancho tests' fixtures instead (tests/safety_test.cho).

Usage:  python3 tests/differential_safety.py build/safety_driver [--cases N] [--seed S] [--collision-py DIR]

`--collision-py` defaults to a sibling checkout of lex-robot (sidecar/); pass it explicitly in CI.
"""
import argparse
import math
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

# The reference's sampling of a capsule against the cart's box (collision.BOX_SAMPLES; 16 in lex-robot itself).
REFERENCE_SAMPLES = 256

# Float noise allowed on top of the reference's bound: the two sides compute the same geometry in a different
# order of operations.
EPS = 1e-9


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
    import collision  # lex-robot's sidecar
    if not hasattr(collision, "capsule_box_clearance"):
        raise SystemExit("this lex-robot has no cart box (capsule_box_clearance): the oracle is lex-robot#218, "
                         "branch fix/workspace-frames, not main")
    collision.BOX_SAMPLES = REFERENCE_SAMPLES
    model = collision.RobotCollisionModel.from_json(str(collision_dir / "robot_geometry.json"),
                                                    without_meshes(URDF, tmp))
    if model.tray_size is None:
        raise SystemExit("robot_geometry.json has no tray_size_m: the reference would check a plane")
    return collision, model


def slack(c):
    """The width of the reference's bound for capsule `c`: half a sample spacing."""
    return math.dist(c.a, c.b) / (2 * REFERENCE_SAMPLES)


def reference_verdict(collision, model, side, q, other, with_tray):
    """lex-robot's verdict for the arm at `q` (and the other arm at `other`, when given), and whether the
    reference's sampling bound leaves it open. The reference judges both arms when both are given, but only the
    moving arm's hits count on our side (lex-robot#218), so its hits are filtered to the ones naming this arm."""
    if other is None:
        hits = model.check(**{f"{side}_joints_deg": q})
    else:
        other_side = "right" if side == "left" else "left"
        hits = model.check(**{f"{side}_joints_deg": q, f"{other_side}_joints_deg": other})
    hits = [h for h in hits if (h.a.startswith(side) or h.b.startswith(side))]
    if not with_tray:
        hits = [h for h in hits if h.b != "cart tray"]
    # Open: a cart clearance the bound cannot place on either side of the margin, or a cart hit the bound cannot
    # order against the worst other hit.
    open_ = False
    if with_tray:
        caps = {c.name: c for c in model.arm_capsules(side, q)}
        for i, c in enumerate(model.arm_capsules(side, q)):
            if i < collision.MOUNTED_LINKS:
                continue
            d = model._cart_clearance(c)
            if model.margin - slack(c) - EPS <= d < model.margin + EPS:
                open_ = True
        if len(hits) >= 2:
            tray = [h for h in hits if h.b == "cart tray"]
            rest = [h for h in hits if h.b != "cart tray"]
            if tray and rest and abs(tray[0].clearance - rest[0].clearance) <= slack(caps[tray[0].a]) + EPS:
                open_ = True
    if not hits:
        return "clear", open_
    worst = hits[0]
    if worst.b == "tower":
        return "collision.tower", open_
    if worst.b == "cart tray":
        return "collision.tray", open_
    return "collision.other", open_


def mount_row(q, other):
    row = [str(bits(x)) for x in q]
    if other is None:
        row += [str(bits(x)) for x in [0.0] * 5] + ["-"]
    else:
        row += [str(bits(x)) for x in other] + ["+"]
    return row


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
    collision, model = load_reference(args.collision_py, tmp)

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

    # Each case is asked three times: the verdict with the cart, the verdict without it, and each capsule's
    # cart clearance.
    lines = []
    for side, q, other in cases:
        tag = "l" if side == "left" else "r"
        for tray in ("t", "n"):
            lines.append("check " + " ".join([tag] + mount_row(q, other) + [tray]))
        lines.append("tray " + " ".join([tag] + [str(bits(x)) for x in q]))
    out = subprocess.run([args.driver], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
    rows = out.stdout.splitlines()

    mismatch = []
    clearance = []
    open_cases = 0
    tray_hits = 0
    worst_gap = 0.0
    if len(rows) != 3 * len(cases):
        mismatch.append(("count", len(rows), 3 * len(cases)))
        rows += ["-1"] * (3 * len(cases) - len(rows))
    for n, (side, q, other) in enumerate(cases):
        rule_t = int(rows[3 * n].split()[0])
        rule_n = int(rows[3 * n + 1].split()[0])
        ours_t = OURS.get(rule_t, f"rule{rule_t}")
        ours_n = OURS.get(rule_n, f"rule{rule_n}")
        ref_n, _ = reference_verdict(collision, model, side, q, other, False)
        ref_t, open_t = reference_verdict(collision, model, side, q, other, True)
        # Without the cart, tower and other-arm verdicts must match exactly.
        if ours_n != ref_n:
            mismatch.append(("no cart", side, q, other, ours_n, ref_n))
        # With the cart, exactly too, unless the reference's own bound leaves the verdict open.
        if ref_t == "collision.tray":
            tray_hits += 1
        if ours_t != ref_t:
            if open_t:
                open_cases += 1
            else:
                mismatch.append(("cart", side, q, other, ours_t, ref_t))
        # Each capsule's cart clearance, inside the reference's bound.
        ours = [unbits(x) for x in rows[3 * n + 2].split()]
        caps = model.arm_capsules(side, q)
        if len(ours) != len(caps):
            clearance.append(("capsules", side, q, len(ours), len(caps)))
            continue
        for i, c in enumerate(caps):
            d_ref = model._cart_clearance(c)
            hi = d_ref + slack(c)
            if not (d_ref - EPS <= ours[i] <= hi + EPS):
                clearance.append((side, q, i, ours[i], d_ref, hi))
            worst_gap = max(worst_gap, ours[i] - d_ref)

    print(f"safety: {len(cases)} poses ({tray_hits} refused for the cart by the reference); "
          f"{len(mismatch)} verdict mismatches, {open_cases} verdicts the reference's bound leaves open; "
          f"{len(clearance)} capsule clearances outside the reference's bound "
          f"(ours sits at most {worst_gap * 1000:.4f} mm above its lower end)")
    if mismatch or clearance:
        print("FAILURES, first 5:", (mismatch + clearance)[:5])
        return 1
    print("differential: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
