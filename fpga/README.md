# fpga: cancho tools for an FPGA bus recorder

Not part of the controller and not built or formatted by CI: these programs use the `Tty` capability, which needs **edition 8**, and
this repository pins the compiler `7bf3628` (editions up to 7). Build them with a newer compiler (`471b8e93` or later; the one built by
cancho works):

```sh
cancho build fpga/labio.cho fpga/busrec.cho --std -o busrec
```

`labio.cho` is a copy of cancho-hdl's `boards/tang-nano-20k/host/labio.cho`. See `docs/hardware-gate.md` for what they do and did not show.

`camdisplay.cho` (edition 12, needs the `Camera` capability on aarch64: cancho `camera-aarch64`, or a release that has it) reads webcam frames
through `Camera` and sends them to cancho-hdl's HDMI design (`DESIGN=hdmi`, `boards/tang-nano-20k/rtl/top_hdmi.v`) through `Tty`:
`printf '/dev/video0\n20\n' | camdisplay`. About one 320x240 grey frame a second; the frame leaves in 128-byte chunks a millisecond apart
because the board's USB serial bridge drops bytes written faster than the line. Measured on the robot's Pi 5 on 2026-11-01 (the operator
saw the picture on a 7-inch 1024x600 panel, with some speckle); the paced sending was 15 frames of 15 stored.
