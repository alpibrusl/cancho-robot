# Decision records: what is NOT in v1, and why

Each entry is a scope decision, made once, with its reason. A decision that changes is rewritten here, not
silently abandoned; the date is the date the reason was written down. These record *exclusions* -- what the
controller deliberately does not do. The epic's tasks are the plan; this file is the boundary.

## Cameras are not in v1

**Decision:** the controller reads no camera and links no camera library.

**Reason:** macOS's camera stack is AVFoundation, an Objective-C API; reaching it over FFI means
Objective-C's runtime and message dispatch inside the controller's foreign calls, and the authority report
would widen from a list of libc symbols to a list of framework entry points -- the report that is this
project's headline would say less, not more. libuvc over FFI is possible (user-space USB, no framework),
but it would still widen the report and add a second device class to prove. Cameras stay in Python behind
the boundary (section 7), where lex-robot already runs them; the controller's report stays about buses.

**Revisit when:** cancho grows a device-level capability whose report is as narrow as the serial one
(cancho#387 is the open question), or v1's measurement says the boundary's latency costs a control loop.

## Dataset writing is not in v1

**Decision:** the controller writes no datasets -- no parquet, no video, no files at all.

**Reason:** recording is LeRobot's job and it is a big one: parquet schemas, video encoding, dataset
card metadata. Porting it means a file-writing capability and a media codec inside the controller, and the
authority report would carry `fs_write` of dataset paths forever after -- for a task Python already does
well, behind a boundary the state stream was designed to feed. The controller's `fs_read` authority is the
two calibration files, read once at startup; that narrowness is worth more than a second recorder.

**Revisit when:** a measurement shows the state stream drops frames LeRobot's recorder needs, and the
cause is the boundary rather than Python's own scheduling.

## Training and policy inference are not in v1

**Decision:** nothing that learns runs in the controller, and no policy checkpoint is loaded by it.

**Reason:** training is torch on GPUs, a Python ecosystem with no cancho equivalent, and inference has the
same shape. More importantly, the design's rule is stronger than a scope line: *what learns cannot change
the bounds* (section 6) -- a policy is a client that requests motion and reads refusals, and nothing it
sends changes a bound, a verifier or the e-stop. Keeping policies behind the boundary is not a limitation
of v1; it is the property the whole design argues for, and a policy inside the controller would weaken the
very claim the report makes.

**Revisit when:** never for the bounds; for inference, only if cancho gains a proven numeric runtime and
the measurement shows the boundary costs a control loop latency that matters.

## Linux and the Raspberry Pi target are not in v1

**Decision:** the code is written for both platforms where it costs nothing (the spike ran both), but only
darwin-aarch64 runs against hardware in v1.

**Reason:** the robot's controller in production is a Mac Studio; the Pi 5 is the training rig. The
differences that matter are narrow (the spike measured them: darwin needs the `IOSSIOSPEED` ioctl at
1 Mbaud, Linux takes `tcsetattr` alone) and both are already handled in the spike's code. What is not
done is a Linux CI target, Linux hardware gates, and the Pi's own timing budget -- each a real cost for a
machine v1 does not drive. The design doc's section 5 numbers are the Mac's.

**Revisit when:** the controller moves to the Pi in a later phase, with its own hardware gates.

## A real-time guarantee is not in v1

**Decision:** the controller is soft real time (a fixed 100 Hz cycle, a counted late cycle), not hard.

**Reason:** cancho has no real-time runtime: no priority inheritance, no CPU pinning, no allocator-free
steady state, and the operating systems v1 runs on do not promise a deadline to a user-space process
anyway. What v1 promises instead is measured: the cycle rate and its jitter, reported honestly (task #13),
with a late cycle counted and not caught up (section 5). A hard guarantee would be a claim the platform
cannot back, and this project's discipline is to claim only what a gate checks.

**Revisit when:** a measurement shows jitter that matters, and then the first move is the design's
section 13 item 5 (a threaded bus reader), not a real-time claim.

---

*History: written with task #15; the reasons summarise the design doc's own sections 2, 5, 6 and 7, they do
not add new ones. A decision that changes is a new commit on this file, with its date and its reason.*
