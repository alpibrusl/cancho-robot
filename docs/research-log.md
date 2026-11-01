# Research log

One entry per experiment: what was asked, what was run, what came out, and what it changes.
Negative results stay. A claim found false later is corrected in place, with the date.

## 2026-10-08 · #3 · Opening the servo bus at 1 Mbaud from cancho

**Asked.** Can a cancho program open the left arm's bus (`/dev/cu.usbmodem5B610332201`), put it in
raw mode at 1,000,000 baud and get servo 1 to answer a PING, read-only?

**Setup.** cancho `7bf3628` (main), LLVM backend, darwin-aarch64 (Mac Studio). `spikes/ping/ping.cho`;
its packet builder can only make PING and READ (a WRITE request answers -1, printed by the run).
The `struct termios` layout was measured with a C program, `spikes/ping/abi.c`, as an independent
reference: 72 bytes, flags at 0/8/16/24 (8 bytes each), `c_cc` at 32, `c_ispeed` at 56, `c_ospeed`
at 64, `VMIN` 16, `VTIME` 17.

**What came out, in order.**

1. **A `&[byte]` crosses to C as two arguments, pointer and length.** So `open(path, flags)` and
   `cfsetspeed(t, speed)` cannot be declared directly: the length would land where the flags or the
   speed belong. Worked around: the path goes through `strdup` (which takes the slice and answers a
   `c_ptr`), and the speed fields are written into the termios bytes by hand.
2. **`extern fn free` cannot be declared.** The LLVM backend refuses its own output: `invalid
   redefinition of function 'free'` (the runtime declares `free` with a different signature). The
   compiler reports it as its own bug, correctly. To file upstream.
3. **macOS refuses 1,000,000 in `tcsetattr`**: `EINVAL`, and nothing is applied, not even raw mode
   (read back: still 9600). Raw mode has to be set at a standard rate first (115200 was accepted).
4. **The speed then needs `ioctl(fd, IOSSIOSPEED, &speed)`, and `ioctl` is variadic.** Declared as
   an ordinary function it returned **0** (success) and set the port to garbage: the speed read
   back as `0xfffffffffffffff0`. On darwin-aarch64 named arguments travel in x0-x7 and a variadic
   one on the stack, so `ioctl` read its pointer from whatever was on the stack. A silent wrong
   configuration, not an error. Confirmed by padding the declaration with six unused integers so the
   pointer lands in the first stack slot: the speed then read back as `40 42 0f 00...` = 1,000,000.
   That is an ABI hack that proves the diagnosis; it is not a way to write a driver.
5. **The header's `IOSSIOSPEED` is `0x80085402` here** (`_IOW('T', 2, speed_t)` with an 8-byte
   `speed_t`); pyserial 3.5 hard-codes `0x80045402` (a 4-byte size). Both are accepted by this
   driver as far as observed; noted because the number in the epic and in cancho#387 came from
   pyserial.
6. **The authority report does not see the device.** `cancho authority` lists eleven `libc`
   symbols (`open`, `tcsetattr`, `read`, `write`, ...) and says the program **"never touches the
   filesystem"**, while it opens `/dev/cu.usbmodem5B610332201`. Reaching a device through `libc:open`
   is invisible as a device. This is the headline question, now measured: today the report cannot
   name the bus.
7. **No servo answered at first**: PING and READ sent, 0 bytes back in 50 ms; the reference stack
   (scservo_sdk on pyserial, same port, same packets) got nothing either, on both buses. The servo
   supply was off. **Corrected the same day, with the supply on:**
8. **Servo 1 answered cancho.** PING `ff ff 01 02 01 fb` -> `ff ff 01 02 00 fc` (id 1, no error,
   checksum right) in about 2 ms; READ of `Present_Position` (56, 2 bytes) -> `ff ff 01 04 00 5a 07 99`,
   **1882 ticks, the same value the reference read from the same servo** straight after. 3 runs of 3.
9. **Control, servos powered, 3 runs each**: with `ioctl` declared plainly the call answers 0, the
   speed reads back as garbage and **no servo answers**; with the padded declaration the speed is
   1,000,000 and every PING and READ is answered. So finding 4 is the whole difference between a
   working and a silently dead bus.
10. The right arm's bus (`/dev/cu.usbmodem5B3D0437151`) stayed silent to the reference too (ids 1
    to 10) while the left one answered ids 1 to 8: a supply question on that side, not looked into.

**What it changes.** **Viable, with caveats that are the research.** A servo answers a cancho program
at 1 Mbaud, with the same reading as the reference. But today that takes foreign calls, two
workarounds (`strdup` for a C string, termios written by hand) and one ABI hack (a variadic `ioctl`
that otherwise fails *silently*), and the report cannot say which device it reaches. Findings 1, 2,
4, 5 and 6 go to cancho#387; finding 2 is filed as a compiler bug.

## 2026-10-08 · #3 · The same spike on Linux, on the robot's Raspberry Pi 5

**Asked.** Does the bus open as easily from cancho on Linux, and does cancho run at all on linux-aarch64 (a
target its own CI does not exercise)?

**Setup.** Raspberry Pi 5 (8 GB, NVMe), Debian 13, kernel 6.18, glibc 2.41, aarch64; the robot's two USB-serial
adapters plugged in (`/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033220-if00` is the left arm, as on the Mac).
cancho `7bf3628` built on the Pi with Rust 1.98.1 (rustup); first on the **Cranelift backend**, because the LLVM
backend needs `clang`, which was not installed yet (it was later the same day: finding 7). `spikes/ping/ping_linux.cho`; the layout from `spikes/ping/abi_linux.c` on
the Pi.

**What came out.**

1. **Linux needs no `ioctl`.** 1,000,000 baud is a standard rate there (`B1000000`, 010010 octal, in the `CBAUD`
   bits of `c_cflag`), so `tcsetattr` alone sets it; no two-step, no variadic call. Read back: the speed bits are
   4104, as written.
2. **The ABI differs from macOS in almost every number**: `struct termios` is 60 bytes with 4-byte fields (72 and 8
   on macOS), `c_cflag` at 8, `c_cc` at 17, `VMIN` 6 and `VTIME` 5, `O_NONBLOCK` 2048, `O_NOCTTY` 256,
   `TCIFLUSH` 0 (1 on macOS), `CLOCAL` 0x800, `CREAD` 0x80. glibc 2.41 also stores the B-constant in
   `c_ispeed`/`c_ospeed` (measured: 4104 after `cfsetspeed(B1000000)`). Every one of these is a constant a
   program writing `extern fn` has to get right per platform, and gets no help from the compiler for.
3. **Servo 1 answered, 3 runs of 3**: PING `ff ff 01 02 00 fc`, READ `ff ff 01 04 00 5a 07 99` = **1882 ticks, the
   value scservo_sdk read on the Pi straight after** (and the same as on the Mac: the arm had not moved).
4. **cancho works on linux-aarch64 with Cranelift**: the codec, calibration and kinematics test files (34 tests)
   pass there, and the codec's differential against scservo_sdk passes on the Pi (20,000 instructions, 20,000
   byte streams). The LLVM backend on this target is still untried (waiting for `clang`).
5. The authority report is the same as on macOS: foreign symbols, and "never touches the filesystem" while it
   opens a device. **Linux makes configuring the port simpler; it does not change what the report can say.**
6. The right arm's bus was silent on the Pi too (the left answered ids 1 to 8). **Corrected the same day:** it was
   the cables; reseated, the right bus answers ids 1 to 6 and the wheels 9 and 10.
7. **With `clang` installed, the LLVM backend works on linux-aarch64 too**: the spike on **both** buses, 3 runs of 3
   each, left servo 1 = 1882 ticks and right servo 1 = 1898 ticks, each equal to what scservo_sdk read straight
   after; the 34 tests and the codec differential (40,000 cases) pass on LLVM as on Cranelift. So the backend
   question in finding 4 is answered: both work on the Pi.

## 2026-10-12 · #8 · The safety core on data, held to lex-robot#218's model

**Asked.** Does `src/safety.cho` decide every motion the way lex-robot's guard does -- one `approve`, joint limits
from the calibration spans, the workspace bound in the arm's own frame (`ArmPoint` and `RobotPoint` are distinct
types, so lex-robot#218's frame mix-up does not type-check), the ik.jump bound, capsule collisions (tower, cart,
other arm, counting only the hits the moving arm takes part in), a speed clamp that reports itself, a deadman that
holds the pose?

**Setup.** cancho `7bf3628`. The oracle is lex-robot's `sidecar/collision.py` at lex-robot#218
(`fix/workspace-frames`, commit `7e5436b`), which models the cart as a box from the floor to the tray over the
tray's footprint. `tests/differential_safety.py`, 1524 poses (recorded and random, both arms, with and without the
other arm).

**What came out.**

1. **The first version of the gate compared against lex-robot's main, which checks an infinite plane.** Every
   tray verdict was then a "known difference" and none was checked: 739 of 1524 decisions were unverified.
2. **With #218 as the oracle the gate found a real defect:** the footprint's axes were swapped (0.45 m along x;
   lex-robot reads `tray_size_m` as [x, y] = [0.35, 0.45], the arms side by side along the long side). With the
   swap, 7 of 324 poses were *clear* here and a *tray hit* in the reference: the unsafe direction. Fixed. The
   order is still lex-robot's assumption, not a measurement (design section 12).
3. **The cart distance was a bound from the segment's bounding box**, which refuses a link passing outside the
   tray's corner (a 0.031 m clearance read as 0.06 m under the surface). Replaced by the exact distance: the box's
   signed distance is convex, so a ternary search along the segment finds its minimum. lex-robot samples the
   segment and subtracts half a spacing, so the gate holds each capsule's clearance inside that interval (run at
   256 samples: ours sits at most 0.26 mm above the reference's lower end, as it must).
4. **Result:** 0 verdict mismatches with the cart and without it, 461 cart refusals agreed, 2 verdicts inside the
   reference's own sampling band (counted, reported), 0 clearances outside the bound. `tests/mutate_safety.py`:
   16 of 16 mutants caught, including the swapped axes, the search removed, the depth inside the cart and the
   link's radius.

**Changes.** Nothing in the design: section 6 already said box, not plane. The geometry is still estimates until
measured on this robot (section 12); the left arm's rest pose put a link 22 mm into the tray on 2026-10-02, so a
mount or tray number is off by centimetres.

## 2026-10-12 · #7 · Inverse kinematics: damped least squares from the live state (position only)

**Asked.** Can the IK step lex-robot#217 got wrong -- linearised around a stale pose, so a 1 cm step asked for
51 degrees -- be written so the stale state cannot happen, and held to placo?

**Setup.** cancho `7bf3628`. `src/ik.cho`, `tests/differential_ik.py` (placo's forward kinematics as the chain),
`tests/mutate_ik.py`.

**What came out.**

1. **Linearised at the live state every iteration**, damped least squares with an adaptive damping (a floor of
   0.001, lambda at 5% of the current error: a fixed lambda measured settling 0.2 mm short near a joint limit).
   An answer that leaves the measured spans or jumps further than the distance justifies is refused, not
   returned, inside the solve.
2. **Gate C4, IK half:** the single descent step matches the Jacobian's own DLS prediction to 3.3e-10 rad over
   800 sampled live states (234 refused, honestly); converged solves put the gripper within 0.1 mm of the target.
   placo's own `solve()` is not the oracle of the step policy (near a singularity its soft-task weighting takes
   barely-descent steps); its kinematics are.
3. **The mutation gate first claimed 6 of 6 and ran none:** two patterns matched the one-line form `cancho fmt`
   rewrites, so the harness crashed on its own assert and CI was red. The patterns now match the formatted text
   and an identity mutant is refused. 6 of 6 caught.

**Not done.** The solve is **position only**. Issue #7 asks to hold the gripper's current orientation, which
lex-robot learned on the real arm (position-only drifted the wrist 13-30 degrees per 10 cm). The SO-101 has five
joints, so a full 6-D pose is not generally reachable; holding the orientation as a weighted secondary task is
the open choice (design section 13). #7 stays open for it.

## 2026-10-12 · #12, #15 · The toolbox contract on the controller; decision records

**Asked.** Can an agent ask the controller what a motion would do, and be sure the asking moves nothing?

**What came out.** `check` answers every check the core applies, named by rule tag, the speed clamp reported as a
clamp, and the goals it would commit, without sending anything. Its authority ceiling is `heap, io_read,
io_write` and nothing else: `cancho authority` proves it sends nothing, rather than the code promising it.
`introspect` lists the rules, bounds and authority from the same tables the code runs on (`src/rules.cho`,
`src/safety.cho`'s own functions), `skill` says how to ask, errors are JSON with stable rule tags.
`tests/conformance_check.py` holds the three in agreement (every rule a refusal names is listed, every listed rule
is reachable, the bounds introspect reports are the ones the core runs); `tests/mutate_check.py`: 6 of 6 table
drifts caught. What v1 leaves out, and why, is in `docs/decisions.md`.

## 2026-10-12 · #5 · Reading the whole robot from cancho, on the Pi (read-only)

**Asked.** Can the controller, on the Raspberry Pi 5, read every servo of both buses with the ticks LeRobot
reads, how often does a read fail, and how fast is it next to scservo_sdk on the same machine?

**Setup.** cancho `7bf3628`, Cranelift, on the Pi (Debian 13, aarch64). `src/serial.cho` (every foreign call
of the controller: `open`, `tcsetattr`, `read`, `write`, `clock_gettime` and five more, pinned by
`authority/scan.ceiling`), `tools/scan.cho` (PING, then timed READs of Present_Position; it imports no write
builder). `tests/hw/scan_vs_reference.py` alternates it with scservo_sdk 1.0.0 (what LeRobot's FeetechMotorsBus
drives), 3 rounds of 1000 reads per id, arms still, torque off.

**What came out.**

1. **First, nobody answered**, not even scservo_sdk, at any baud rate, and not one byte came back to a raw
   broadcast PING, with the servo LEDs on. The cause was **the adapter boards' jumper on the ESP32 side**: in
   that position USB writes into nothing. Moved to USB, a broadcast PING found exactly the robot: left 1-8 (arm,
   tower pan and tilt), right 1-6, 9, 10 (arm, wheels). Check the jumper first next time; the LEDs prove power,
   not the data path.
2. **All 16 servos, 48,000 reads each side: 0 failures, cancho and scservo_sdk alike**, and the raw ticks equal
   on every id (tolerance 2 ticks; the difference was 0 everywhere).
3. **Latency** (per read, microseconds, worst round): cancho p50 267-270, p99 329-335, max 335-346; scservo_sdk
   p50 278-279, p99 310-338, max 339-463. About 10 us faster at the median; the rest is the wire and the servo
   (a READ is 8 bytes out and 8 back at 1 Mbaud, 160 us, plus the servo's return delay). Throughput 3,610-3,670
   reads/s against 3,444-3,467, each including opening the port. **Not a speed claim**: the bus sets the pace,
   and both are within 4% of each other.

**Changes.** Task #5's gate holds on this robot. The scan is read-only by construction (no `feetech_write`
import), and its authority is pinned in CI; the hardware comparison is local-only, as section 11 says.

## 2026-10-12 · #9 · Actuation through the core, on a simulated bus first

**Asked.** Can every goal reach the servos only through `safety.approve`, with a refusal sending no byte, torque
never dropped implicitly, the goal held and never more than 8 degrees ahead, and a stall stopped -- shown from the
bus's side, before the first motion on the robot?

**Setup.** cancho `7bf3628`, on the Pi. `src/core.cho` (the one path: `commit` = approve, then one SYNC WRITE),
`src/serial_write.cho` (goal and torque packets; no foreign call), `src/robot.cho` (generated from LeRobot's
calibration files), `tools/play.cho` (the console). `sim/fake_bus.py`: one pty per bus answering the Feetech
protocol from register files, with the robot's calibration in its servos and its rest positions, logging every
packet. The controller opens it as it opens `/dev/ttyACM0`. `tests/actuation_gates.py` judges six claims from that
trace; `tests/mutate_actuation.py` breaks each.

**What came out.**

1. **The calibration files and the buses did not match.** Read-only, the servos' own registers (homing offset 31,
   limits 9 and 11) on the adapter `5B61033220` are exactly `xle_right.json`'s, and on `5B3D043715` `xle_left.json`'s,
   the opposite of lex-robot's `bus_preflight.py`; the tower (7, 8) and wheel (9, 10) servos moved too. Something
   was re-cabled since lex-robot wrote that. So the controller recognises an arm by its servos (`core.identify`),
   and a bus whose servos match neither calibration is not driven. lex-robot opens arms by adapter serial; worth
   checking there.
2. **At rest, every arm is past a bound.** Joints folded past the swept spans (left lift -91.2 vs -90.5, elbow,
   wrist), the right gripper 2.4 mm outside the workspace, and the left arm inside the tower's margin by the
   estimated geometry until its shoulder rises to -46 degrees. Judging only the request refused every motion
   from rest. The rules now judge from where the arm is: stay or move back, never further, and per collision pair,
   so leaving the tower into the cart is still refused (design section 6). 24/24 safety mutants.
3. **The console first held the speed-clamped goal**, which drifted the joints that were not moving toward their
   present positions (the gravity walk of lex-robot#215, reintroduced). It now holds what was requested, and a
   stall is a second without progress rather than "the goal reached but not the target", which never fired.
4. **Gates, on the simulated bus:** G1 a refusal sends nothing (3 refusals, 0 extra writes); G2 torque on writes
   goal = present tick for tick before enabling; G3 every goal within the lead bound of the last reading; G4 no
   implicit torque off; G5 a stall is detected and the goal comes back to where the joint stopped; G6 arms
   recognised with the chains crossed. 7 of 7 actuation mutants caught (one only by a unit test: the console's
   2-degree steps never exercise the core's 8-degree lead bound, a careless client would). Gate A3, the write path
   by call site, with its self-test.

**Not yet.** Nothing has moved on the robot. The hardware gate of #9 (one joint first, small steps, the operator
at the e-stop, tracking against LeRobot driving the same sequence) is next, and the geometry of design section 12
is still estimates: the tower contact at rest says so.
