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
7. **No servo answered**: PING and READ sent, 0 bytes back in 50 ms. The reference stack (scservo_sdk
   on pyserial, same port, same packets) got no status packet either, on both buses, for ids 1, 2
   and 6. So the bus side was silent to everyone: most likely the servo supply was off. **Not yet a
   result about cancho.** To repeat with the servos powered.

**What it changes.** Opening and configuring a serial port from cancho works today only through
foreign calls, with two workarounds and one ABI hack, and the report cannot say which device. That
is what cancho#387 asks about; findings 1, 2, 4 and 6 go there. Whether a servo answers is still open.
