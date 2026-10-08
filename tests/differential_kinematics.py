#!/usr/bin/env python3
"""Gate C4 (docs/design.md section 8): forward kinematics against placo, through LeRobot's RobotKinematics.

The reference is what lex-robot's sidecar runs today: `lerobot.model.kinematics.RobotKinematics` on the same URDF
(tests/fixtures/urdf/so101_new_calib.urdf), target frame `gripper_frame_link`. Random joint angles across and beyond
each joint's travel, plus the zero pose and the poses recorded on this robot, go to `tests/kinematics_driver.cho` and
to the reference. Reported: the largest position error (metres) and the largest difference in any rotation entry.
Gate: position within 1e-9 m (a nanometre; docs/design.md proposed 0.1 mm, and the measurement says how much room
there is) and rotation entries within 1e-9.

Usage:  python3 tests/differential_kinematics.py build/kinematics_driver [--cases N] [--seed S]
"""
import argparse
import random
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from lerobot.model.kinematics import RobotKinematics

URDF = str(Path(__file__).resolve().parent / "fixtures" / "urdf" / "so101_new_calib.urdf")
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
POS_TOL = 1e-9
ROT_TOL = 1e-9

# Joint readings (degrees) taken on this robot: the left arm resting on the desk with the ink bottle, and the right
# arm by the tower (lex-robot, 2026-10-02 and 2026-10-06).
RECORDED = [
    [-21.3, 64.5, 2.7, -27.5, 18.0],
    [-15.6, -90.9, 95.6, -67.1, -132.9],
    [-6.6, -65.9, 92.0, -74.9, -106.5],
]


def without_meshes(urdf, into):
    """The same URDF with every <visual> and <collision> removed. placo loads the meshes those name, which this
    repository does not carry; forward kinematics depends only on the joints and their origins, which stay as they
    are (and scripts/gen_chain.py reads the unstripped file)."""
    tree = ET.parse(urdf)
    for link in tree.getroot().findall("link"):
        for tag in ("visual", "collision"):
            for e in link.findall(tag):
                link.remove(e)
    path = Path(into) / "so101_kinematics_only.urdf"
    tree.write(path)
    return str(path)


def bits(x):
    return struct.unpack("<q", struct.pack("<d", x))[0]


def unbits(n):
    return struct.unpack("<d", struct.pack("<q", n))[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("driver")
    ap.add_argument("--cases", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=20261008)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    tmp = tempfile.mkdtemp()
    kin = RobotKinematics(urdf_path=without_meshes(URDF, tmp), target_frame_name="gripper_frame_link",
                          joint_names=JOINTS)

    poses = [[0.0] * 5] + RECORDED
    poses += [[rng.uniform(-200.0, 200.0) for _ in range(5)] for _ in range(a.cases)]
    lines = ["fk " + " ".join(str(bits(x)) for x in q) for q in poses]
    out = subprocess.run([a.driver], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
    rows = out.stdout.splitlines()
    worst_pos = worst_rot = 0.0
    failures = []
    for q, row in zip(poses, rows):
        ours = np.array([unbits(int(x)) for x in row.split()]).reshape(4, 4)
        ref = kin.forward_kinematics(np.array(q + [0.0]))
        dp = float(np.max(np.abs(ours[:3, 3] - ref[:3, 3])))
        dr = float(np.max(np.abs(ours[:3, :3] - ref[:3, :3])))
        worst_pos, worst_rot = max(worst_pos, dp), max(worst_rot, dr)
        if dp > POS_TOL or dr > ROT_TOL or not np.allclose(ours[3], [0, 0, 0, 1]):
            failures.append((q, dp, dr))
    if len(rows) != len(poses):
        failures.append(("count", len(rows), len(poses)))
    print(f"kinematics: {len(poses)} poses; worst position error {worst_pos:.3e} m, worst rotation entry {worst_rot:.3e}")
    if failures:
        print(f"{len(failures)} FAILURES, first 5: {failures[:5]}")
        return 1
    print("differential: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
