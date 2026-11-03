# fpga: cancho tools for an FPGA bus recorder

Not part of the controller and not built or formatted by CI: these programs use the `Tty` capability, which needs **edition 8**, and
this repository's pinned compiler (`cancho.toml`) is a release built by cancho, which has editions well past 8, so the programs here build with it:

```sh
cancho build fpga/labio.cho fpga/busrec.cho --std -o busrec
```

`labio.cho` is a copy of cancho-hdl's `boards/tang-nano-20k/host/labio.cho`. See `docs/hardware-gate.md` for what they do and did not show.

`camdisplay.cho` (edition 12, needs the `Camera` capability on aarch64: cancho `camera-aarch64`, or a release that has it) reads webcam frames
through `Camera` and sends them to cancho-hdl's HDMI design (`DESIGN=hdmi`, `boards/tang-nano-20k/rtl/top_hdmi.v`) through `Tty`:
`printf '/dev/video0\n20\n' | camdisplay`. One 320x240 grey frame is 76,804 bytes at 1 Mbaud: 0.77 s, about 1.2 frames a second, at the line rate
(measured 2026-11-01 on the robot's Pi 5: 60 of 60 frames of random bytes arrived unpaced). The board drops a frame if it waits more than about
78 ms for a byte, so the frame must not be interrupted. The operator saw the picture on a 7-inch 1024x600 panel, with some speckle.
