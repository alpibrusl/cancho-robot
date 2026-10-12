#!/usr/bin/env python3
"""Gate C1 (docs/design.md section 8): the codec against scservo_sdk, the reference LeRobot drives these servos with.

Encoding: thousands of random instructions, inside the domain both sides accept, built by `tests/driver.cho` and by
scservo_sdk's PacketHandler writing into a fake port; the bytes must be identical.

Decoding: thousands of byte streams (valid replies, noise before them, corrupted checksums, truncations, another
servo's id, impossible headers, random bytes) fed to the driver and to scservo_sdk's `rxPacket` through the same
fake port; each answer must map to the reference's, except for two deliberate divergences, each asserted so the
list cannot go stale:

  D1  a status packet whose length field is under 2 is refused here (`feetech.bad-length`); the reference reads it
      as a short packet.
  D2  a valid packet from another id is refused here (`feetech.wrong-id`); the reference, in txRxPacket, waits past
      it for the id it asked.

Usage:  python3 tests/differential_codec.py build/driver [--cases N] [--seed S]
"""
import argparse
import random
import subprocess
import sys

import scservo_sdk as scs
from scservo_sdk.scservo_def import (COMM_RX_CORRUPT, COMM_RX_TIMEOUT, COMM_SUCCESS)


class FakePort:
    """Captures what the reference writes; serves it bytes to read, then times out."""

    def __init__(self, incoming=b""):
        self.is_using = False
        self.written = b""
        self.incoming = list(incoming)

    def clearPort(self):
        pass

    def writePort(self, packet):
        self.written += bytes(packet)
        return len(packet)

    def readPort(self, n):
        out, self.incoming = self.incoming[:n], self.incoming[n:]
        return out

    def setPacketTimeout(self, n):
        pass

    def isPacketTimeout(self):
        return not self.incoming

    def getBaudRate(self):
        return 1_000_000


PH = scs.PacketHandler(0)


def ref_encode(cmd, args):
    p = FakePort()
    if cmd == "ping":
        PH.ping(p, args[0])
    elif cmd == "read":
        PH.readTx(p, *args)
    elif cmd == "sync_read":
        addr, count, ids = args
        PH.syncReadTx(p, addr, count, list(ids), len(ids))
    elif cmd == "write":
        sid, addr, data = args
        PH.writeTxOnly(p, sid, addr, len(data), list(data))
    elif cmd == "reg_write":
        sid, addr, data = args
        PH.regWriteTxOnly(p, sid, addr, len(data), list(data))
    elif cmd == "action":
        PH.action(p, args[0])
    elif cmd == "sync_write":
        addr, count, blocks = args
        PH.syncWriteTxOnly(p, addr, count, list(blocks), len(blocks))
    return p.written


def line_of(cmd, args):
    parts = [cmd]
    for a in args:
        parts.append(a.hex() if isinstance(a, (bytes, bytearray)) else str(a))
    return " ".join(parts)


def gen_encode(rng):
    """A random instruction inside the domain both sides accept (ids 0..253, broadcast where it means something,
    addresses and counts 0..255, counts above 0, and packets that fit in 250 bytes)."""
    kind = rng.choice(["ping", "read", "sync_read", "write", "reg_write", "action", "sync_write"])
    if kind == "ping":
        return kind, [rng.randint(0, 253)]
    if kind == "read":
        return kind, [rng.randint(0, 253), rng.randint(0, 255), rng.randint(1, 255)]
    if kind == "sync_read":
        ids = bytes(rng.randint(0, 253) for _ in range(rng.randint(1, 30)))
        return kind, [rng.randint(0, 255), rng.randint(1, 255), ids]
    if kind in ("write", "reg_write"):
        data = bytes(rng.randint(0, 255) for _ in range(rng.randint(1, 40)))
        return kind, [rng.randint(0, 254), rng.randint(0, 255), data]
    if kind == "action":
        return kind, [rng.randint(0, 254)]
    count = rng.randint(1, 8)
    servos = rng.randint(1, 20)
    blocks = b"".join(bytes([rng.randint(0, 253)]) + bytes(rng.randint(0, 255) for _ in range(count))
                      for _ in range(servos))
    return kind, [rng.randint(0, 255), count, blocks]


def status(sid, err, params):
    body = bytes([sid, len(params) + 2, err]) + bytes(params)
    return b"\xff\xff" + body + bytes([255 - sum(body) % 256])


def gen_stream(rng):
    """A byte stream a servo bus might deliver, and the id the caller asked."""
    sid = rng.randint(0, 253)
    err = rng.randint(0, 127)
    params = bytes(rng.randint(0, 255) for _ in range(rng.randint(0, 10)))
    good = status(sid, err, params)
    kind = rng.choice(["valid", "noise", "checksum", "truncated", "other_id", "bad_header", "short_len", "random"])
    if kind == "valid":
        return kind, good, sid
    if kind == "noise":
        noise = bytes(rng.choice([0, 1, 2, 0x55, 0xFE]) for _ in range(rng.randint(1, 6)))
        return kind, noise + good, sid
    if kind == "checksum":
        return kind, good[:-1] + bytes([(good[-1] + rng.randint(1, 255)) % 256]), sid
    if kind == "truncated":
        return kind, good[:rng.randint(0, len(good) - 1)], sid
    if kind == "other_id":
        return kind, good, (sid + rng.randint(1, 253)) % 254
    if kind == "bad_header":
        bad = b"\xff\xff" + bytes([rng.choice([254, 255]), rng.randint(0, 255), rng.randint(0, 255)])
        return kind, bad + good, sid
    if kind == "short_len":
        ln = rng.randint(0, 1)
        body = bytes([sid, ln, err])
        return kind, b"\xff\xff" + body + bytes([255 - sum(body) % 256]) + bytes(rng.randint(0, 255) for _ in range(4)), sid
    return kind, bytes(rng.choice([0, 1, 2, 4, 0xFE, 0xFF, rng.randint(0, 255)]) for _ in range(rng.randint(0, 16))), sid


def ref_decode(stream):
    p = FakePort(stream)
    rx, result = PH.rxPacket(p)
    return rx, result


def run_driver(driver, lines):
    out = subprocess.run([driver], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("driver")
    ap.add_argument("--cases", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=20261008)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    failures = []

    # -- encoding --
    cases = [gen_encode(rng) for _ in range(a.cases)]
    answers = run_driver(a.driver, [line_of(c, args) for c, args in cases])
    encoded = 0
    for (cmd, args), got in zip(cases, answers):
        want = ref_encode(cmd, args)
        if got != "PKT " + want.hex():
            failures.append(f"encode {line_of(cmd, args)}: ours {got!r}, reference PKT {want.hex()}")
        encoded += 1

    # Broadcast PING: both sides refuse it (the reference writes nothing and answers COMM_NOT_AVAILABLE).
    got = run_driver(a.driver, ["ping 254"])[0]
    if not got.startswith("ERR") or ref_encode("ping", [254]) != b"":
        failures.append(f"broadcast ping: ours {got!r}, reference wrote {ref_encode('ping', [254]).hex()!r}")

    # -- decoding --
    streams = [gen_stream(rng) for _ in range(a.cases)]
    answers = run_driver(a.driver, [f"decode {sid} {s.hex()}" for _, s, sid in streams])
    tally = {}
    d1 = d2 = 0
    for (kind, stream, sid), got in zip(streams, answers):
        rx, result = ref_decode(stream)
        tally[kind] = tally.get(kind, 0) + 1
        words = got.split()
        ref_ok = result == COMM_SUCCESS and len(rx) >= 6 and rx[3] >= 2
        if words[0] == "OK":
            consumed, rid, err, pstart, pcount = map(int, words[1:6])
            ok = (ref_ok and rx[2] == sid and rid == sid and err == rx[4]
                  and stream[pstart:pstart + pcount] == bytes(rx[5:5 + pcount]) and pcount == rx[3] - 2)
            if not ok:
                failures.append(f"decode {kind} {stream.hex()} id {sid}: ours {got}, reference {result} {bytes(rx).hex()}")
        elif words[0] == "NEED":
            # The stream ran out before a whole packet: the reference reports a timeout (nothing read) or CORRUPT
            # (something read), never a complete packet whose checksum failed.
            full_bad = (result == COMM_RX_CORRUPT and len(rx) >= 4 and rx[0] == 255 and rx[1] == 255
                        and rx[3] >= 2 and len(rx) >= rx[3] + 4)
            if result == COMM_SUCCESS or full_bad:
                failures.append(f"decode {kind} {stream.hex()}: ours NEED, reference {result} {bytes(rx).hex()}")
        elif words[2] == "feetech.bad-checksum":
            if not (result == COMM_RX_CORRUPT and len(rx) >= 4 and len(rx) >= rx[3] + 4 and rx[3] >= 2):
                failures.append(f"decode {kind} {stream.hex()}: ours bad-checksum, reference {result} {bytes(rx).hex()}")
        elif words[2] == "feetech.wrong-id":
            d2 += 1
            if not (ref_ok and rx[2] != sid):
                failures.append(f"decode {kind} {stream.hex()}: ours wrong-id, reference {result} {bytes(rx).hex()}")
        elif words[2] == "feetech.bad-length":
            d1 += 1
            # D1: the reference found a header whose length field is under 2
            if not (len(rx) >= 4 and rx[3] < 2):
                failures.append(f"decode {kind} {stream.hex()}: ours bad-length, reference {result} {bytes(rx).hex()}")
        else:
            failures.append(f"decode {kind} {stream.hex()}: unexpected answer {got}")

    # The divergences must still occur, or this list has gone stale.
    if d1 == 0:
        failures.append("D1 never exercised: no short-length packet was generated or refused")
    if d2 == 0:
        failures.append("D2 never exercised: no other-id packet was refused")

    print(f"encode: {encoded} instructions compared byte for byte")
    print(f"decode: {len(streams)} streams {dict(sorted(tally.items()))}; divergences D1 {d1}, D2 {d2}")
    if failures:
        print(f"{len(failures)} FAILURES, first 10:")
        for f in failures[:10]:
            print("  " + f)
        return 1
    print("differential: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
