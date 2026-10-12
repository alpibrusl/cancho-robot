#!/usr/bin/env python3
"""Task #5's gate, on the robot (local only, never CI): cancho's `scan` against scservo_sdk on the same servos.

READ-ONLY. Both sides only PING and READ; nothing here can move a servo. One program per bus: stop lex-robot's
sidecar, LeLab and practice.sh first (each round opens and closes the port, one side at a time).

For each bus and id, `--rounds` times, alternately:
  1. `build/scan <device> <reads> <id>`: cancho reads Present_Position `reads` times, timed, and reports the
     first and last raw tick, the failures and the latency percentiles;
  2. scservo_sdk (the library LeRobot's FeetechMotorsBus drives) reads the same register `reads` times, timed.

Gates, per id (the arm held still, torque off):
  - the raw ticks agree: cancho's last reading and the reference's first, taken within a second of each other,
    differ by at most `--tolerance` ticks (a still servo's encoder can flicker by one);
  - the failure rate of each side is reported per id, never hidden (a bus that drops replies also drops
    commands);
Pre-registered measurement, reported whichever way it falls: reads per second and the latency distribution of
each side on the same machine.

Usage:  python3 tests/hw/scan_vs_reference.py build/scan --bus left=/dev/serial/by-id/... 1 2 3 4 5 6 7 8 \
                                                        --bus right=/dev/serial/by-id/... 1 2 3 4 5 6 9 10
"""
import argparse
import statistics
import subprocess
import sys
import time

PRESENT_POSITION = 56


def percentile(xs, pct):
    if not xs:
        return -1
    xs = sorted(xs)
    return xs[min(len(xs) - 1, len(xs) * pct // 100)]


def cancho_side(scan, device, ident, reads):
    out = subprocess.run([scan, device, str(reads), str(ident)], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise SystemExit(f"scan failed on {device}: {out.stdout}{out.stderr}")
    f = out.stdout.split()
    row = {f[i]: f[i + 1] for i in range(0, 12, 2)}
    return {
        "ok": int(row["ok"]), "fail": int(row["fail"]), "flagged": int(f[11]), "last_error": f[13],
        "us": [int(x) for x in f[15:20]], "first": int(f[21]), "last": int(f[22]),
    }


def reference_side(sdk, device, ident, reads):
    port = sdk.PortHandler(device)
    if not port.openPort() or not port.setBaudRate(1_000_000):
        raise SystemExit(f"scservo_sdk could not open {device}")
    packets = sdk.PacketHandler(0)
    lat, ok, fail, first, last = [], 0, 0, -1, -1
    try:
        for _ in range(reads):
            t0 = time.perf_counter_ns()
            value, result, _error = packets.read2ByteTxRx(port, ident, PRESENT_POSITION)
            t1 = time.perf_counter_ns()
            if result == sdk.COMM_SUCCESS:
                ok += 1
                lat.append((t1 - t0) // 1000)
                last = value
                if first < 0:
                    first = value
            else:
                fail += 1
    finally:
        port.closePort()
    return {"ok": ok, "fail": fail, "lat": lat, "first": first, "last": last}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scan")
    ap.add_argument("--bus", nargs="+", action="append", required=True, metavar="NAME=DEVICE ID")
    ap.add_argument("--reads", type=int, default=1000)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--tolerance", type=int, default=2)
    args = ap.parse_args()
    import scservo_sdk as sdk

    problems = []
    print(f"{'bus':6}{'id':>3}  {'side':7}{'ok':>7}{'fail':>6}  {'p50':>5}{'p90':>5}{'p99':>6}{'max':>7} us"
          f"  {'reads/s':>8}  ticks")
    for spec in args.bus:
        name, device = spec[0].split("=", 1)
        for ident in [int(x) for x in spec[1:]]:
            c_ok = c_fail = r_ok = r_fail = 0
            c_p = {50: [], 90: [], 99: [], 100: []}
            r_lat = []
            c_time = r_time = 0.0
            ticks = []
            for _ in range(args.rounds):
                t0 = time.perf_counter()
                c = cancho_side(args.scan, device, ident, args.reads)
                t1 = time.perf_counter()
                r = reference_side(sdk, device, ident, args.reads)
                t2 = time.perf_counter()
                c_time += t1 - t0
                r_time += t2 - t1
                c_ok += c["ok"]
                c_fail += c["fail"]
                r_ok += r["ok"]
                r_fail += r["fail"]
                for pct, v in zip((50, 90, 99, 100), c["us"][1:]):
                    c_p[pct].append(v)
                r_lat += r["lat"]
                if c["last"] >= 0 and r["first"] >= 0:
                    ticks.append((c["last"], r["first"]))
                    if abs(c["last"] - r["first"]) > args.tolerance:
                        problems.append(f"{name} id {ident}: cancho read {c['last']}, the reference {r['first']}")
            if c_ok and not r_ok:
                problems.append(f"{name} id {ident}: cancho reads it, the reference never does")
            if r_ok and not c_ok:
                problems.append(f"{name} id {ident}: the reference reads it, cancho never does")
            n = args.rounds * args.reads
            cp = [max(c_p[p]) if c_p[p] else -1 for p in (50, 90, 99, 100)]
            print(f"{name:6}{ident:>3}  {'cancho':7}{c_ok:>7}{c_fail:>6}  {cp[0]:>5}{cp[1]:>5}{cp[2]:>6}{cp[3]:>7}   "
                  f"{n / c_time:>8.0f}  {ticks[-1] if ticks else '-'}")
            print(f"{'':6}{'':>3}  {'sdk':7}{r_ok:>7}{r_fail:>6}  {percentile(r_lat, 50):>5}{percentile(r_lat, 90):>5}"
                  f"{percentile(r_lat, 99):>6}{percentile(r_lat, 100):>7}   {n / r_time:>8.0f}")
    print("(cancho's percentiles are the worst of its rounds; reads/s includes starting the process)")
    if problems:
        print("FAILURES:")
        for p in problems:
            print("  " + p)
        return 1
    print("scan: ticks agree on every id both sides read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
