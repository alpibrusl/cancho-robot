# The controller on an operating system that is itself cancho

> **Status: a side track, started 2026-11-24. One result, in an emulator, read-only** (research log, same date).
> v1's controller is still a cancho program on Debian, on the robot's Raspberry Pi 5 (`decisions.md`). Nothing here
> has run on a real board or a real servo, and nothing here moves one.

## 1. What the headline claim leaves out on Linux

The question of this project is whether the compiler can prove that only the safety core can move the arms. On the
Pi under Debian the proof has two edges it does not reach, both already written down here:

1. **It is about one program.** `cancho authority` says what the controller can touch. It says nothing about the
   other programs of the machine, and any of them can open `/dev/ttyACM0`. "One program per bus" is, on the Pi, a
   property of what is installed there (`decisions.md`, "The controller is the Raspberry Pi 5"), kept by
   discipline, not by a check.
2. **The bus is reached through foreign calls.** `src/serial.cho` calls `open`, `tcsetattr`, `read`, `write`,
   `clock_gettime` and five more. A foreign call is reported symbol by symbol, not device by device, so the report
   lists libc functions where the README wants it to name a bus (the README's "question that decides the
   headline", cancho#387).

Issue #31 asks whether hardware can close the first edge from outside, with an FPGA between the controller and the
bus. This track asks whether the operating system can close both from underneath.

## 2. The machine

[cancho-os](https://github.com/alpibrusl/cancho-os) is a kernel for the Raspberry Pi written in cancho, with no
Linux under it and no C in it. What it changes for the two edges:

1. **Only checked cancho runs there.** There is no second program that could open the bus, because there is no way
   to start one that the compiler has not seen. The kernel holds the bus and hands it to one holder: the linear
   capability that makes "one owner of a bus" true inside a program becomes true for the machine.
2. **The serial port is the kernel's own driver.** No `tcsetattr`, no ioctl: a UART is a handful of registers.
   What the report can then name is the device. (Today it names eleven hardware primitives of one or two
   instructions, which is still symbol by symbol; a capability over an address range is an open issue of the
   compiler, drafted in cancho-os `docs/issues/02`.)

What it does not change: Python cannot run there. Cameras, recording, training and policies stay on another
machine, which is what v1's boundary to Python (#11) already says, with a wire where v1 has a process boundary.

## 3. What is built

On cancho-os, branch `robot`, at `dc9e141`:

- `src/feetech.cho` of this repository, copied unchanged from `6192833` (the file is the same on `main` today):
  PING, READ, SYNC READ and the decoder. `feetech_write.cho` is not copied, so the kernel holds no packet that
  moves a servo.
- A driver for a second UART and a `transact` with the shape of `serial.transact`: drop what is waiting, send,
  collect until `feetech.decode` answers or a timeout on the ARM counter passes.
- Two shell commands, `servo scan` (PING ids 1 to 8) and `servo pos` (READ register 56 of ids 1 to 6).
- A simulated bus, `sim/servos.cho`: six servos, a cancho program for the host, connected to the emulated board's
  second serial port.

## 4. What it does not show

- **No hardware.** The run is in `qemu-system-aarch64`, on its Raspberry Pi 3 and Pi 4 models. QEMU has no Pi 5
  model, and the robot's board is a Pi 5.
- **The simulator is a model, not a servo.** It was written from the protocol by the same hand as the driver and
  can share a misreading with it. What ties it to reality is one step removed: its answers decode with
  `feetech.decode`, which this repository holds to scservo_sdk and has run against the real bus (#5).
- **No timing.** A baud rate means nothing in the emulator. 1 Mbaud, the 0.27 ms read of #5 and a control cycle
  are unmeasured there.
- **Not the bus's electrical shape.** The servo line is half duplex; the emulated port is two pipes.
- **Not the Pi 5's serial ports.** On a Pi 5 both the USB ports (where the adapter boards are today) and the
  header UARTs sit behind the RP1 chip on PCIe. The emulated port is the BCM283x mini UART, a different device:
  its driver is the part that is thrown away.
- **No second simulator has been compared with it.** PR #27 has its own (`sim/fake_bus.py`); the two were written
  separately and have not been run against each other.

## 5. Steps, each with what would count

1. *(done, in the emulator)* Read-only bus: six simulated servos found, two absent ids silent, positions equal to
   what the simulator holds.
2. Calibration and kinematics on the bare target. They import `std.math`; what that needs with no libc under it
   is unknown until tried.
3. The safety core as the only holder of the bus, on the kernel: a motion request goes through `approve` or is
   not sent. Against the simulator first, as #9 does.
4. The Pi 5: a console on the debug UART, the addresses read from the board's own device tree, then a header
   UART behind RP1. This is the first step that needs the board, a debug probe and a second SD card (the robot's
   own card is not touched).
5. The real arm, read-only first, with the gate of #5: the same ticks as scservo_sdk on the same bus. Read before
   write, and an operator at the e-stop for anything after that, as everywhere in this project.

## 6. What would end the track

- The header UART cannot be reached without bringing up PCIe from scratch, and that costs more than the claim is
  worth. The fallback is the adapter boards on USB, which needs a USB host stack and is worse.
- The control cycle cannot be kept by a kernel that polls with interrupts masked (its present design). Then it
  needs interrupt entry into cancho, which the compiler does not have.
- The FPGA gate of #31 gives the same guarantee for less: then the operating system is not where to spend it.
