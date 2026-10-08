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
cancho `7bf3628` built on the Pi with Rust 1.98.1 (rustup); **Cranelift backend**, because the LLVM backend needs
`clang`, which is not installed yet. `spikes/ping/ping_linux.cho`; the layout from `spikes/ping/abi_linux.c` on
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
6. The right arm's bus is silent on the Pi too (the left answers ids 1 to 8): a supply question on that side,
   not the computer.
