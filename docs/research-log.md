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
