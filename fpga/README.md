# fpga: cancho tools for an FPGA bus recorder

Not part of the controller and not built or formatted by CI: these programs use the `Tty` capability, which needs **edition 8**, and
this repository pins the compiler `7bf3628` (editions up to 7). Build them with a newer compiler (`471b8e93` or later; the one built by
cancho works):

```sh
cancho build fpga/labio.cho fpga/busrec.cho --std -o busrec
```

`labio.cho` is a copy of cancho-hdl's `boards/tang-nano-20k/host/labio.cho`. See `docs/hardware-gate.md` for what they do and did not show.
