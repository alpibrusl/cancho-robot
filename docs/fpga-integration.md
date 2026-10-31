# FPGA integration: where the gate sits on this robot, and what the controller does for it

> **Status: design, nothing built.** Written 2026-10-31 to prepare steps 2 and 3 of the hardware gate (`docs/hardware-gate.md`, issue #31).
> What was measured is marked **[measured]** with where; what was told about the robot is marked **[stated]** (the operator's description,
> not yet checked on the machine); everything else is a **[choice]** or an open question. Nothing here has touched a servo.

## 1. What is fixed

* **[stated]** The robot's servos are grouped on **two buses, each reached through its own USB serial adapter** from the Pi. It has **three USB
  webcams**: one on the tower and one on each arm.
* **[observed, not measured]** The Tang Nano 20K has **one** USB-C port (the BL616: programming and a UART): the operator's photo of the
  product listing shows one USB-C connector and one HDMI connector, and the "S1" and "S2" markings are push buttons. The schematic was not read. The webcam therefore cannot plug into the board: a
  USB device needs a USB host controller and a PHY the FPGA does not have.
* **[measured, cancho-hdl#24]** The board's UART runs at 115200 by default and was run at 1,000,000 baud (`CLKS=27`) with block commands that
  move a frame in one round trip. At 3,000,000 baud long frames were lost (cause unknown). The link tops out near 100 KB/s at 1 Mbaud.
* **[measured, this repository]** The servo bus is 1 Mbaud, half-duplex, on one wire; a servo answers a read in a median 0.33 ms
  (`docs/design.md` §1, `docs/research-log.md`).
* **[measured, cancho-hdl]** The FPGA's design lives in SRAM only: it is gone at power-off and must be reloaded.

## 2. Cameras stay out of the FPGA

Three webcams cannot go through the board: at 1 Mbaud the whole link carries about 100,000 bytes a second, and one 320x240 grey frame is 76,800.
The cameras belong to the Pi, read through cancho's `Camera` capability (edition 12, alpibrusl/cancho#664). **That capability opens nothing on
aarch64 yet**: its V4L2 numbers were measured on x86_64 only, so on the Pi `camera_open` answers `Failed(ENODEV)` until the numbers are measured
there (`docs/camera.md` §2 of cancho). USB bandwidth for three cameras on the Pi's ports is **not measured**.

## 3. Where the gate sits **[choice, to be decided by the first hardware step]**

Three places, in the order the work goes:

| | Placement | What the FPGA can do | Risk |
|---|---|---|---|
| A | **Tap**: one FPGA input on each bus's data line, the adapter still wired to the servos | record every byte with a 27 MHz timestamp (step 1, done in loopback: `fpga/busrec.cho`) | none for the arm: the FPGA cannot write |
| B | **Inline**, one per arm: adapter -> FPGA -> servos | forward frames; refuse an out-of-limit write; drop torque when the controller stops kicking (step 3) | a bug in the glue can pass or block a write; the bus direction control is unmeasured |
| C | **Replace the adapter**: the FPGA is the bus master and the Pi talks to it over the one UART | everything in B, plus framing for both buses on one 115200 or 1 Mbaud line | the most to build; the Pi loses the adapter's tested timing |

Decision for the next step: **A on one arm**, to measure the real bus (its idle level, its voltage, the turn-around between a command and a
reply) before any wire is cut. B is only designed after A has numbers; C is not pursued.

Not known, and needed before B: the **voltage level** of these adapters and servos against the FPGA's 3.3 V pins; **which header pins are free**
(the constraints file `tang_nano_20k.cst` in cancho-hdl names the ones the lab design uses); the **direction control** of a half-duplex line at
1 Mbaud (27 clocks a bit); and **what the bus does when the FPGA is unprogrammed** (an unprogrammed part's pins float; inline, that cuts the
arm off rather than passing commands, which is the safe way to fail, but it has to be seen on the bench, not assumed).

## 4. What the controller does for the gate **[choice]**

The safety core stays the only thing that can commit a motion. The gate is a second enforcement point, not a replacement, and not a proof
(`docs/hardware-gate.md`: the glue is hand-written; its guarantee is its tests).

1. **Limits in, read back.** The host takes each joint's limits from the calibration and safety data and writes them to the gate's registers
   (the contract is cancho-hdl's `chw` file; a limit register block is not written yet: today only the deadman's `CTRL.ENABLE`, `TIMEOUT_MS` and
   `KICK.SEQ` exist). It reads them back and refuses to go on unless they match what it wrote.
2. **Kick at the control rate.** The loop changes `KICK.SEQ` every cycle (50 to 100 Hz). A timeout shorter than the cycle would trip it, so the
   timeout is declared next to the cycle time and checked against it.
3. **Fail closed.** Torque is not enabled until the gate reports armed with the limits just read back. If the gate trips or stops answering,
   the controller stops commanding and reports it; it does not retry the write.
4. **One owner per device.** Each gate is opened through a `Tty` narrowed to its device, like each bus; the report names the devices
   (`tty_open("/dev/...")`) and nothing else. Same ceiling discipline as the other tools (`authority/*.ceiling`).

## 5. How it is tested without hardware

* **Recorder loopback** (done, step 1): the host's own traffic read back and decoded (`fpga/busrec.cho`).
* **Simulation** of the gate's glue in cancho-hdl's testbench, with 1 Mbaud frames and the direction signal, before any pin is wired.
* **A dry run on the host**: the module of §4 against a simulated register file, printing what it would write (the `check` tool's shape:
  what a motion would do, without sending anything), with a differential test of the register values against the safety core's own limits.
* **Hardware** only with an operator at the e-stop, one arm, A before B.

## 6. Slices

| Slice | Needs | Done when |
|---|---|---|
| S1 this document | nothing | merged |
| S2 tap on one arm (A) | an adapter, a level shifter, an operator | idle level, voltage and a ping/read round trip recorded and compared with scservo_sdk on the same bus |
| S3 host module with a dry run | cancho-hdl's limit contract | the differential test of §5 passes; ceiling names the device only |
| S4 inline gate on one arm (B) | S2, S3, direction control measured | an out-of-limit write is refused on the bus (seen in the recorder) and the deadman drops torque within the declared time |
| S5 cameras on the Pi | `Camera` on aarch64 | numbers measured on the Pi and written into cancho's `docs/camera.md` |

## 7. Not claimed

* That the FPGA can be put inline without changing the bus's timing: unmeasured.
* That the Tang Nano 20K has the pins, the levels or the speed for both buses: unmeasured.
* Anything about the webcams on the robot: the `Camera` capability was run against one laptop webcam on x86_64 only, and read frames by byte
  count, not by looking at them.
* That `fpga/*.cho` build with the compiler this repository pins (`cancho.toml`: editions up to 7): they need edition 8 and are built with a
  newer compiler by hand, as `fpga/README.md` says. They build with the current compiler (checked 2026-10-31: `fpga/busrec.cho` with the
  refreshed `fpga/labio.cho`, report `tty_open("/dev")`, no foreign code).
