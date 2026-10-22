# DESIGN — inhibit-idle-while-watching

## The contract

Idle is about to act → capture ONE frame → a face is present → restart the
idle countdown with one synthetic input tap and hand the chain back to the
shell (a gate, not a daemon); anything else → exit and let the configured
dim/screen-off/lock chain proceed silently. Two failure postures: the chain
fails OPEN (a broken camera must never keep the session awake), the privacy
gate fails CLOSED (nothing may capture while the session is locked).

## Why the trigger is a handshake (not a hook)

The plugin API has no idle hook (`noctalia.d.luau`: `onIpc`, `onOpen`,
`onConfigChanged`, `onExit`, … — nothing idle-related). Noctalia's idle
behaviours can run `command` actions, so the trigger is a user-configured idle
behaviour that messages the plugin (`… all idle-begin`). This is the same
prod-verified pattern als-brightness uses for its idle handshake.

## Why a synthetic-input reset (not inhibitors, not caffeine)

The gate needs the idle chain to *count again AND re-arm a behaviour that
already fired*. Measured 2026-10-22 on umbriel (hands-off windows, journal
timelines):

* **Caffeine toggle pair (`noctalia msg caffeine-toggle` x2):** restarts the
  timers (dim fired at poke-release + timeout + ~1 s debounce) but takes no
  `resumed` path — the already-fired `presence-check` stayed spent and never
  re-fired (90 s observed, both 0 s and 3 s gaps). Insufficient.
* **Inhibitor hold (logind `Inhibit` + compositor `ScreenSaver.Inhibit`):**
  works (suppresses behaviours; release restarts the countdown — original
  niri-era measurements, re-verified on umbriel) but requires a holder
  process, its own re-check timer, a stop protocol and crash-safety
  machinery. That is a daemon for what is logically one reset. Retired.
* **Synthetic input (`wtype -M shift -m shift`):** reads as real user
  activity — the shell emits `presence-check resumed` and the behaviour
  re-fires at tap + timeout (22:10:55 tap → 22:11:45 trigger, 50 s timeout).
  **Chosen.** Cost: real input can nudge drag states/game keybinds; a bare
  modifier tap types nothing into applications.

A fired behaviour only re-arms on the `resumed` path, and only real input
produces it. The plugin still owns nothing after the tap: the shell recounts.

## Crash-safety

There is nothing to crash: the gate is one process per check that exits after
the tap (or exits silently on any non-face outcome). No inhibitor is held, no
timer is kept, no state outlives the check — a SIGKILL mid-check cannot leave
the session awake or asleep in a wrong state.

## Power posture

* One short capture+detect per check (~0.5 s measured: capture ~0.3 s + Haar
  ~15 ms on a 640px frame) plus one `wtype` call per confirmed face. No
  continuous stream, no background poll, no holder process.
* Detection runs on a 320 px-wide grayscale downscale regardless of
  `capture_width`; Haar default (tens of ms class), DNN opt-in and stricter.
* `cooldown_s` caps gate starts (camera wake-ups) if idle triggers stack.

## Privacy posture

Frames are decoded in RAM and dropped (ffmpeg mjpeg → stdout pipe). Nothing is
written unless `debug_log` (verdicts and camera details, never images). The
gate refuses to touch the camera while the session is locked — checked at
spawn, at check start, and again immediately before capture (so capture is
never *started* locked/unknown; an in-flight frame cannot be cancelled and
dies with the check). The lock is re-checked one final time immediately
before the wtype tap: capture+detect can span up to ~15 s, and synthetic
input must never land on a lockscreen (locked/unknown refuses the tap). The
camera activity LED is the visible signal and cannot be suppressed.

## Module split

* `policy.luau` — pure spawn policy (enabled/locked/running/cooldown), tested
  under bare luau.
* `service.luau` — thin glue: settings, status-file overlap check, spawn argv.
  Spawns detached; nothing to tear down in `onExit`.
* `face-watch` — python helper: camera autodetect, capture, detectors, the
  lock gate, the wtype tap, status file. Pure functions at the top (device
  parsing, sensitivity mapping, lock parsing, the poke predicate) are tested
  without cv2/camera via lazy imports.

## Detector data paths (measured 2026-10-22)

Haar cascade resolution must not assume `cv2.data.haarcascades`: that attribute
exists only in the pip wheels. Arch `python-opencv` (5.0.0) has no `cv2.data` at
all — its XMLs live in `/usr/share/opencv5/haarcascades/`. `face-watch` therefore
resolves the cascade over a candidate list (wheel data dir first, then
`/usr/share/opencv{5,4,}/haarcascades`) via the pure `resolve_haar_cascade()`,
covered by `tests/test_face_watch.py::HaarCascadePath`. A first live run failed
with `module 'cv2' has no attribute 'data'` before this fix.

## Detection reality at reclined desk distance (measured 2026-10-22)

| posture | Haar@640 | RFB-320 DNN | YuNet |
| --- | --- | --- | --- |
| reclined, face toward the screen | 12/12 | 12/12 (score 1.0) | 4/4 |
| reclined, looking down at a phone | 0/12 | 0/12 (score ~0.1) | 0/12 |

The camera sits above the screen, so the phone posture shows mostly the crown
of the head — no model fixes that from this angle. Decision (user, 2026-10-22):
**watching-only semantics** — a look-away releases the inhibitors, person-level
presence is out of scope. The DNN decode counts faces after greedy NMS
(`decode_dnn_faces`, pure + tested): the raw net fires ~10 anchor boxes per real
face, and its score rows are `[background, face]` pairs — indexing them as
scalars raised `TypeError` and one `float()` away from every check erroring.

## Trigger latency: timeout + global fade (measured 2026-10-22)

Noctalia's `pre_action_fade_seconds` is a **global** fade in front of every
behaviour: joined behaviours all fire when the one fade completes, and any input
during the fade cancels the whole chain ("pre-action fade cancelled"). With
fade 30 s the presence check ran at 75 s of perfect stillness and six
consecutive attempts were cancelled by input in the 9–16 s window; with fade 0
(measured after the change) `presence-check triggered` lands exactly at its
timeout. Live value lives in `~/.local/state/noctalia/settings.toml`, which
overrides the `nri-idle` fragment.

## Rejected shapes

* Continuous presence polling (camera always on) — power and privacy cost.
* Doing capture/detect inside Luau — no subprocess-free camera or cv2 access.
* Inhibitor hold (the v0 design) — needs a holder process, its own re-check
  timer, a stop protocol and crash-safety machinery; retired for the gate.
* Caffeine pair as the poke — restarts timers but never re-arms a fired
  behaviour (no `resumed` verb; measured 2026-10-22).
