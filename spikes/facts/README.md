Three facts `docs/design.md` rests on, each a program to run with `cancho check` / `run` / `authority`:

* `a_ok.cho` is accepted: a foreign call two functions deep, every row saying `ffi("libc")`.
* `b_outer_hides.cho` is refused with `effect-not-declared`: the outer function's row `[]` hides the foreign call.
* `d_listen.cho` checks and runs; its report is `net_in("8920")` and nothing else on the network.
