# A hardware gate between the controller and the bus: step 1, a recorder

Written 2026-10-29. Answers the first step of issue #31. **Nothing here has touched a servo or the servo bus**, and it is not claimed
to. What ran is a bus recorder and a deadman in an FPGA, driven from cancho. The designs, the measurements and the tests are in
[cancho-hdl](https://github.com/alpibrusl/cancho-hdl) (`boards/tang-nano-20k/`, `docs/fpga-experiments.md`); this repository holds the cancho
tool that reads the recorder (`fpga/busrec.cho`, with a copy of `labio.cho`).

## What was run

A Sipeed Tang Nano 20K (Gowin GW2AR-18) on this machine's USB, driven only through cancho's `Tty` capability (`cancho authority` of the tool:
`tty_open("/dev")`, `tty_read`, `tty_write`, `poll`, `io_write`, `heap`; no foreign call), with the compiler at `471b8e93` built by cancho.

* **Recorder** (`rtl/lab.v`, the analyzer): timestamps every edge of a probe pin on the 27 MHz clock (37 ns) into a 512-entry FIFO; its
  registers are a checked contract (`lab/lab.chw`, `chw check` clean) and the generated block reads the instrument through an input
  (cancho-hdl#21).
* **`fpga/busrec.cho`**: arms the recorder on the board's serial RX pin, sends `H`, `i` and `0x55` 30 ms apart, stops, drains the
  edges and decodes 8N1 frames at 234.375 ticks a bit, sampling mid-bit. **Measured:** 51 edges, 12 frames: the three bytes sent decode
  as 72, 105 and 85; the nine bytes of the register write that stopped the capture follow (`W`, the address 0x40000000 and the data);
  consecutive bytes of one write are 2343 ticks apart (10 bit times); `H` to `i` is 816,589 ticks, 30.2 ms by the FPGA's clock.
* **Bit time of the host's adapter**, seen by the same recorder: 234.30 ticks, about 0.03 % faster than 115200 baud.
* **Deadman** (`deadman.chw`, `rtl/deadman.v`), tested from cancho: 24 kicks 50 ms apart kept it alive on every poll; with a 200 ms timeout
  the trip is first seen 197 to 210 ms after the last kick (8 runs; 204 ms on the three runs after the trip flag moved into a contract
  register); a kick after the trip changes nothing; `ENABLE` 0 then 1 re-arms; `TIMEOUT_MS=0` never trips. The output is an LED.

## What this does not show

* The servo bus is half-duplex at 1 Mbaud on a TTL pin. The board's USB serial runs at 115200 and is not that bus; the recorder here
  watched the host's own traffic as a stand-in. A 1 Mbaud recorder is a parameter of the design (27 clocks a bit, simulated, not built
  or run); the pin, the level shifting and the direction control are unmeasured.
* No claim about the controller's authority report changes: this is a second enforcement point, not a proof about the controller.
* The glue (`rtl/*.v`) is hand-written; its guarantee is its tests. Only the register block is generated from a contract.

## Next, in the issue's order

2. Read-only on a real servo through the FPGA as a passthrough, compared with the Python reference on the same bus (needs the adapter,
   a level-shifted pin pair and an operator). 3. The limit gate and the deadman on goal-position writes, with an operator at the e-stop.
