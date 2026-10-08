# cancho-robot: a robot arm controller in cancho

Status: **design (task #1 of the epic, [#16](https://github.com/alpibrusl/cancho-robot/issues/16)); nothing in it is built except the spike of
section 2.** Every value in brackets is *proposed*: it waits for the maintainer, and once confirmed it changes only in place, with the reason.
A gate is fixed before the code it judges, so none of them moves to suit a result. Measurements were taken on cancho
`alpibrusl/cancho@7bf3628`, LLVM backend, darwin-aarch64 (the Mac Studio the robot is wired to), on 2026-10-08.

## 1. What this is for, and the claim it must survive

A controller for the SO-101 arms of an XLeRobot: the Feetech servo bus, calibration, kinematics and a safety core that every motion goes
through. The research question is the README's: **can the compiler prove that the only thing able to move the arms is the safety core**, and
that nothing else (a keyboard, a learned policy, a recorder, a second program) can reach the servo bus?

The motivation is measured, not argued. In two weeks of driving these arms from Python (lex-robot on LeRobot), the defects that mattered were
about authority: two programs on one bus; a recording path that skipped the safety checks; a workspace bound written in the robot's frame and
checked in each arm's own (lex-robot#218); a bus lock held for a whole move and a re-entrant lock that let the same thread through
(lex-robot#219); an IK solver linearising around a stale pose (lex-robot#217). Each was found by a person, on the robot, after the fact.

The performance claim is modest and stated before measuring: the bus is the bottleneck (a servo answers a read in a median 0.33 ms), so the aim
is **parity on the bus** with the Python stack, and steadier timing as a hope, not a claim.

## 2. What the compiler can and cannot prove about the bus (measured, spike #3)

The spike (`spikes/ping/`, PR #17, `docs/research-log.md`) opened the left arm's bus from cancho and got servo 1 to answer a PING and a READ:
1882 ticks, the value the reference stack (scservo_sdk on pyserial) read from the same servo. What it took is what this section is about.

1. **Configuring a serial port is foreign code today.** cancho opens files under a path-narrowed `Fs`, but only to read (`open_read`) or to
   write whole files, and no builtin sets raw mode or a speed. Every step went through `extern fn`: `open`, `tcgetattr`, `cfmakeraw`,
   `tcsetattr`, `ioctl`, `tcflush`, `read`, `write`, `close`.
2. **The report cannot see the device.** `cancho authority` listed those symbols and said the program **"never touches the filesystem"** while
   it opened `/dev/cu.usbmodem5B610332201`. A device reached through `libc:open` is invisible as a device. So, today, **no report can name the
   bus**, and none can tell reading the bus from writing it: both are `ffi("libc")`.
3. **`ffi` is the one label the compiler tracks through every caller.** A function that calls a foreign function must declare `ffi(...)` in its
   row, and so must every function that calls it, transitively; a row that omits it is refused. So the set of functions that can reach the
   bus **is** the set of functions whose row contains `ffi`, and that set is checked by the compiler, not by review. **Measured:** a wrapper
   that calls an `ffi` function with the row `[]` is refused with `effect-not-declared`; and a function that does not hold the `Ffi`
   capability cannot call a foreign function at all (the capability is its first argument; without it the call is `arity-mismatch`). This is
   the strongest statement available today, and section 3 builds on it.
4. **A module is not a trust boundary** (`cancho/docs/modules.md` §6): `pub` hides names, not effects. "Only the safety core may write" cannot
   rest on a private type; it has to rest on who is lent the capability (fact 3).
5. **Three traps, each a silent or confusing failure:** a `&[byte]` crosses to C as pointer and length, so `open(path, flags)` and
   `cfsetspeed(t, speed)` cannot be declared directly; macOS refuses 1,000,000 in `tcsetattr` and applies *nothing*, raw mode included; and
   `ioctl` is variadic, so declared plainly it **returns success and sets a garbage speed** on darwin-aarch64, and no servo answers (the trap
   cancho documents for `open` in `docs/filesystem.md` §2.2; controlled 3 runs against 3).
6. **The poller cannot wait on the port.** `Poller` takes listeners, connections, pipes, children and signals (`docs/native-sockets.md`,
   `docs/processes.md`), not a descriptor obtained through `extern fn`. A serial read has to be driven by the clock, not by readiness.

Filed upstream: the question of a native, path-narrowed serial device as [alpibrusl/cancho#387](https://github.com/alpibrusl/cancho/issues/387)
(with the measurements above), and a compiler bug found on the way, [alpibrusl/cancho#388](https://github.com/alpibrusl/cancho/issues/388).

### 2.1 Can the controller avoid calling C?

**In this repository's code, yes, if cancho grows a serial builtin; underneath, no, and that is fine.** On macOS the only supported way to
talk to the kernel is `libSystem`: Apple does not keep system-call numbers stable, so cancho's own runtime already reaches files, sockets,
the poller and the clock through libc. What matters is *where* the call is written. When the compiler's runtime makes it, the program's
report carries a precise label (`fs_read("/etc/…")`, `net_in("8920")`, `clock`) and stays bounded. When the program declares `extern fn`, the
report says `ffi("libc")`, lists symbols, and is unbounded. So the goal is **no `extern fn` in this repository**, which needs cancho#387.

Ways around it that were considered and are not taken:

* **Lower the servos to a standard speed** (LeRobot's STS table lists 115,200 as `Baud_Rate` value 4), so `ioctl` is not needed. Raw mode still needs
  `tcsetattr`, so C remains; the bus gets about 8.7 times slower; and it rewrites the EEPROM of 16 servos that LeRobot expects at 1 Mbaud.
* **Configure the port with another program** (`stty`) and then open it as a plain file. `stty` has the same 1 Mbaud limit on macOS; whether a
  setting survives the last close of a `cu` device is not something to build on; and running a program needs `Exec`, a wider authority than
  the one avoided.
* **Talk to the USB adapter directly** (IOKit or libusb): more foreign code, not less.

## 3. Decision (proposed): what v1 claims, and how the bus is held

**v1 confines every foreign call to one module, `serial`, and lends `Ffi` to nothing else but the safety core's commit step.** The capability
flows from `main` into exactly two places: the `serial` module (open, configure, read, write, close) and the function that commits an approved
motion. Everything else (teleoperation, the Python boundary, kinematics, calibration, the checks) receives no `Ffi` and therefore, by fact 3,
**cannot reach a bus, and the compiler proves it**.

What the report and the gates then **do** prove, and the only thing the README may say:

* the program's foreign symbols are exactly the ones in `serial` (a pinned list in `ceilings/`, diffed in CI: [open, tcgetattr, cfmakeraw,
  tcsetattr, ioctl, tcflush, read, write, close, strdup]);
* **the functions able to reach a bus are exactly those whose row contains `ffi`**, and a CI gate lists them from the compiler-checked
  signatures and compares with a committed list (gate A2, section 8). Adding a path to the bus anywhere fails CI, not review;
* the Python side, connected over a local socket, cannot reach a bus at all: it is another process, and this one's report has no outbound
  network, no `exec`, and an inbound port only (section 7);
* **which device** the program opens is enforced in code, from a compiled-in table of the two bus paths, and the report does not say it. That
  is a target, not a claim, until cancho#387.

**Reading versus writing.** Both are `ffi("libc")` today, so the compiler cannot tell a function that reads positions from one that writes
goals. v1 separates them by construction and by gate: `serial` exposes `read_positions` and `commit_goals`, and only the safety core's `commit`
calls the second (gate A3, a call-site check over the source: weaker than fact 3, and called weaker). An option that would let the compiler
draw that line is in section 13.

**One owner per bus.** A bus is a linear value (`res`) holding its descriptor; it is consumed exactly once, by `close`. v1 is
**single-threaded** (section 5), so no second writer can exist at run time either. Threads would need a design of their own:
`cancho/docs/atomics.md` §2 measured that the checker does not stop two threads writing one value today.

## 4. Scope

**v1:** one SO-101 arm, then both. The serial bus at 1 Mbaud; the Feetech STS protocol (PING, READ, WRITE, SYNC READ, SYNC WRITE); calibration
from LeRobot's files; forward and inverse kinematics; the safety core (section 6); terminal teleoperation by keyboard; the boundary to Python
(section 7); `introspect`, `skill`, `rules` and `check`, errors as data with rule tags.

**Not in v1** (task #15 records each): cameras, dataset writing, training and policies (they stay in Python behind section 7), the wheeled base
and the tower (task #14), a real-time guarantee, Linux (the code is written for both platforms where it costs nothing; only darwin-aarch64 is
run against hardware in v1).

## 5. Design

**One process, one thread, a fixed cycle.** The loop runs at [100 Hz] from `clock_ms`:

1. read every arm joint of both buses (one SYNC READ per bus: `Present_Position`, and every [10th] cycle `Present_Load` and temperature);
2. take the pending requests (keyboard, Python) and pass each through the safety core (section 6), which answers an approved goal or a refusal
   with its rule tag;
3. commit approved goals (one SYNC WRITE of `Goal_Position` per bus);
4. publish the state to clients, then wait on the `Poller` for the listener and client connections with a timeout of whatever is left of the
   cycle.

**Serial I/O is clock-driven** (fact 6): the descriptor is non-blocking; a transaction writes its packet and reads until the expected length
arrives or a deadline [3 ms per servo answered] passes, sleeping in `poller_wait` (with nothing registered for the port) rather than spinning.
A late cycle is counted, not caught up: the next cycle starts from the clock, and no queued motion is replayed.

**Budget, to be measured in #5:** a SYNC READ of six servos is one 14-byte packet and six 8-byte replies, about 0.6 ms on the wire at 1 Mbaud
plus six reply latencies (0.33 ms median each, measured): about 3 ms per bus, about 6 ms for both arms read sequentially, about 7 ms with the
writes. That fits [100 Hz] with little room; if #5 measures otherwise, the rate moves in place, here.

**Codec: sans-io**, as in cancho-dns: pure functions from bytes to a decoded status packet or a tagged refusal; encoding writes into a caller's
buffer and refuses rather than overrun. The write instructions are built only inside `serial.commit_goals`.

**Held targets** (the lesson of lex-robot PR #215): a goal is held, not recomputed from the present position, so gravity cannot walk a joint
down; and the goal may lead the measured position by at most [8 degrees] per joint (LeRobot's `max_relative_target`, as lex-robot runs it), so
pressing against an obstacle is a soft push, not a growing error.

**Torque is never dropped implicitly.** Opening a bus does not touch torque (LeRobot's `connect` disables it while it configures, and an arm
under load sags); closing does not either. Freeing an arm is an explicit, logged request.

## 6. The safety core

One function, `approve`, takes a request and the latest state and answers `Approved(goals)` or `Refused(rule, detail)`. The checks, in order,
each with a rule tag and a fixture:

| check | rule tag | behaviour | origin |
|---|---|---|---|
| request well formed, joint known, delta finite | `request.*` | refuse | lex-robot `jog_joint` |
| joint limits from calibration | `limit.joint` | refuse | calibration files |
| workspace bound per arm, **in that arm's own frame** [x 0.05..0.45, y -0.35..0.35, z -0.30..0.50 m] | `limit.workspace` | refuse the whole path | lex-robot#218 |
| IK step that moves a joint further than the distance justifies [15 + 6 per cm, degrees] | `ik.jump` | refuse | lex-robot#217 |
| capsule collisions: tower, cart (a box over the tray footprint), the other arm, counting only hits the moving arm takes part in | `collision.*` | refuse | lex-robot `collision.py`, #218 |
| speed [0.25 m/s at the gripper] | `limit.speed` | clamp, reported as a clamp | lex-robot grant |
| deadman: no fresh request for [300 ms] from a client that is driving | `deadman` | hold position | lex-robot #195 |

**Frames are types.** A point in an arm's frame and a point in the robot's frame are different structs, and the only conversion takes the arm's
mount; a bound is declared on one of them. The collision geometry carries its known uncertainty: lex-robot measured the model putting the left
gripper 22 mm inside the tray while it physically rested beside it, so the geometry is measured before task #8 trusts it (section 12).

**What learns cannot change this.** The bounds, the checks and the e-stop are constants of this program; a client can request motion and read
refusals, and nothing it sends changes a bound (the rule of the self-improving-controller note that motivated the epic).

## 7. The boundary to Python

Python keeps cameras, LeRobot recording, training and policies. It talks to the controller over **TCP on 127.0.0.1**, the controller only
listening: `narrow(net, "[8920]")`. **Measured:** a program holding `Net("8920")` that calls `tcp_listen(net, 8920, …)` checks, runs, and its
report reads `net_in("8920")` and nothing else on the network (cancho-dns measured the same for UDP: a narrowed `Net` works in one direction). Newline-delimited JSON both ways: the state stream (joints per arm, the cycle's refusals and clamps, a sequence number) and
requests (`jog_joint`, `move_arm`, `grasp`, `release`, `hold`).

Proposed: speak **the subset of lex-robot's sidecar skill API** that these map to (same names, same argument shapes), so lex-robot's tools and
`/control` page can point at the controller with a URL change. Decided in task #11; the state stream is what LeRobot recording needs, and a
client that disconnects trips the deadman.

## 8. Gates, fixed before the code

**Authority (these come first).**

* **A1. Foreign symbols.** `cancho authority --output json`'s `foreign_symbols` equals the pinned list in `ceilings/controller.json`; CI fails on
  any difference.
* **A2. Who can reach a bus.** A script lists every function whose declared row contains `ffi` (rows are compiler-checked, fact 3) and compares
  with a committed list: [`serial.*`, `core.commit`, `main`]. It must be able to fail: a mutant that calls `serial.read_positions` from the
  teleop module is refused by the compiler unless its row says `ffi`, and with the row widened, it fails A2.
* **A3. Who writes.** `serial.commit_goals` has exactly one call site, in `core.commit`; checked over the source. Weak, and called weak.
* **A4. No outbound network, no exec, no fs_write.** The report's labels are within `ceilings/controller.json`: `args`, `clock`, `heap`,
  `io_read`, `io_write`, `err_write`, `poll`, `net_in("8920")`, `conn_accept`, `conn_read`, `conn_write`, `fs_read` of the two calibration files,
  and `ffi("libc")`.

**Correctness, each against an independent reference.**

* **C1. Packets** byte-identical to scservo_sdk's for every instruction and address used; replies captured from the real bus decode the same;
  split at any byte, the same; a wrong checksum, id, length or status each refused with its own rule.
* **C2. Reads**: on the real bus, ticks equal to scservo_sdk's for every servo, arm held still, read back to back.
* **C3. Calibration**: normalised values equal to LeRobot's normalisation over recorded poses and a synthetic sweep of every joint's range,
  both directions and across the encoder wrap; a cache that disagrees with the EEPROM is refused.
* **C4. Kinematics**: forward within [0.1 mm] of placo (LeRobot's `RobotKinematics`) over [10,000] sampled poses; IK steps against placo from
  the same live state; lex-robot's stale-state and orientation cases as regression tests.
* **C5. Safety core**: the same verdict and rule as lex-robot's Python checks on its recorded trajectories and on generated poses, both lex-robot
  regressions (#218) as fixtures, and a mutant of every check killed.
* **C6. Never write by accident**: tasks #3 and #5 build no write packet (the builder refuses), proven by a bus trace in the hardware test.

**Hardware (task #9 onward, operator at the e-stop).** A scripted sequence of small moves: tracking error per joint against LeRobot driving
the same sequence; every refusal of C5 reproduced on the real arm with no byte on the bus (trace); an obstruction detected and stopped.

## 9. Measurements, pre-registered (task #13)

Same machine, same arm, five interleaved rounds against the Python stack (LeRobot 0.6.1 through scservo_sdk), medians and spread: reads per
second per bus; cycle period and its p50, p99 and worst jitter over [10 minutes]; key-down and key-up to motion and to stop; CPU and resident
memory. **Expectation, written before measuring:** parity on reads per second (the bus bounds both), smaller p99 jitter for the compiled loop,
and lower memory. **Not the claim:** speed. A loss is written up here, in place, with by how much.

## 10. Plan, each step with its own gate

| step | tasks | what | gate |
|---|---|---|---|
| R0 | #1, #2 | this document; scaffold, pinned compiler, CI without hardware | A1, A4 on the spike |
| R1 | #4, #5 | `serial` module from the spike; codec; read both buses | C1, C2, C6, A1-A3 |
| R2 | #6, #7 | calibration; kinematics | C3, C4 |
| R3 | #8 | the safety core, on data only | C5 |

R2's kinematics half is done (branches `kin/forward` and `ik/dls`): forward kinematics from the generated
chain, held to placo to [1e-15] over sampled poses; inverse kinematics as damped least squares -- the
method this design left open, now chosen: position-only (a 5-DOF arm cannot generally reach an arbitrary
orientation), linearised at the LIVE state every iteration (the stale-state defect, lex-robot#217), the
damping adaptive (a floor of [0.001] and lambda at [5%] of the current error, so the tail converges -- a
fixed lambda measured settling 0.2 mm short near a joint limit), and the answer refused, not returned, when
it leaves the measured spans or jumps further than the gripper's travel justifies (the safety core's own
bounds, checked inside the solve so a caller cannot take a refusing answer to the bus). Gate C4's IK half
holds the single descent step to the Jacobian's own DLS prediction to [1e-9 rad] over sampled live states
(placo's FK is the reference chain; placo's own single solve() near a singularity takes barely-descent
steps of its soft-task weighting, so its step policy is not the oracle -- its kinematics are), and
converged solves put the gripper at the target to [0.1 mm] or refuse honestly.

R3 is done (branch `safety/core`): `src/safety.cho` checks every motion through one `approve` -- joint limits
from the calibration spans, the workspace bound in the arm's own frame with `ArmPoint`/`RobotPoint` as distinct
types, the ik.jump bound, the capsule model (tower, tray as a box over the tray's footprint -- not the plane
lex-robot checks, the difference counted in the differential -- and the other arm, only the hits the moving arm
takes part in), a speed clamp that reports itself, and a deadman that holds the pose. Gate C5 holds it to
lex-robot's `collision.py` on recorded and random poses (tower and other-arm verdicts match exactly; the tray
differences are the design's own box-vs-plane choice, counted and reported), and `tests/mutate_safety.py` kills
twelve mutants of the checks.
| R4 | #9 | actuation through the core, **first motion** | hardware gates |
| R5 | #10, #11, #12 | teleoperation; the Python boundary; the agent contract | key-to-stop latency; LeRobot records through the boundary |
| R6 | #13 | measurements | section 9 |

## 11. Hardware rules

* Read before write: R1 sends no write instruction, and the builder refuses one.
* An operator at the e-stop for anything that moves; nothing that moves runs in CI or unattended.
* One program per bus: lex-robot's sidecar, `practice.sh`, LeRobot and LeLab must not hold a port while this runs; the controller refuses to
  start if it cannot open a port exclusively (`O_EXLOCK` or an equivalent, decided in R1 and measured).
* No camera image is committed; only derived data.

## 12. What would make this project not worth continuing

* **A compiled-loop cycle that cannot hold [100 Hz] with both arms**, where the Python stack can: the bus would not be the bottleneck after all,
  and the loop has a cost to find before anything is built on it.
* **The authority story not surviving contact**: if gate A2 cannot be made to fail (the rows do not, in practice, carry `ffi` through every
  caller), fact 3 is wrong and the headline goes with it.
* **The collision geometry not being measurable** to a few millimetres: the safety core's main check would then be a guess, and the page has to
  say so.
* cancho never gaining a device-level serial capability: the project stays a controller whose bus access is proven confined to two modules,
  but "names the buses" would mean a table in code, and the page would say that.

Any of these is written up here, in place, as the result.

## 13. Open questions for the maintainer

1. Confirm section 3: one `serial` module with every foreign call, `Ffi` lent only to it and to `core.commit`, the device set in code until
   cancho#387.
2. Confirm the proposed values: [100 Hz], [3 ms] per servo, [8 degrees] lead, [300 ms] deadman, the workspace and speed bounds, port [8920].
3. **Separating read from write in the type.** The label inside `Ffi("…")` is nominal today (`cancho/docs/foreign-authority.md`: a symbol
   declared under one label links from libc anyway). Declaring the write path under its own label (`Ffi("bus-write")`) would let the compiler,
   not a source check, prove which functions can write. That uses a property cancho documents as a gap, so it would be built on sand: ask
   upstream first, or wait for cancho#387?
4. The Python boundary: the subset of lex-robot's skill API (section 7), or a narrower protocol of its own?
5. Single-threaded v1 (section 5): is a threaded bus reader worth a design before #13 measures the cycle?

## Reproduce (section 2)

```sh
git clone https://github.com/alpibrusl/cancho && (cd cancho && git checkout 7bf3628 && cargo build --release -p cancho)
cancho/target/release/cancho authority spikes/ping/ping.cho --std     # "never touches the filesystem"; eleven libc symbols
cancho/target/release/cancho run spikes/ping/ping.cho --std           # needs the left bus powered and free; read-only
cc spikes/ping/abi.c -o abi && ./abi                                  # the termios layout and IOSSIOSPEED on this machine
```
