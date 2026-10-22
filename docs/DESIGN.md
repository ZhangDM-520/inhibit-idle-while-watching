# DESIGN — inhibit-idle-while-watching

## The contract

Idle is about to act → capture ONE frame → a face is present → hold the idle
inhibitors and re-check on an interval; anything else → release and let the
configured dim/screen-off/lock chain proceed silently. Fail-open everywhere: a
broken camera must never pin the session awake, and nothing may capture while
the session is locked.

## Why the trigger is a handshake (not a hook)

The plugin API has no idle hook (`noctalia.d.luau`: `onIpc`, `onOpen`,
`onConfigChanged`, `onExit`, … — nothing idle-related). Noctalia's idle
behaviours can run `command` actions, so the trigger is a user-configured idle
behaviour that messages the plugin (`… all idle-begin`). This is the same
prod-verified pattern als-brightness uses for its idle handshake.

## Why inhibitor-hold instead of resetting a timer

Alternatives considered:

* **Synthetic input (ydotool/wtype):** reads as real activity and restarts the
  countdown, but injects events into the user's input stream (focus, app
  reactions) and needs a daemon on this host. Rejected.
* **Caffeine toggle:** an inhibitor, not a reset; toggling it does not
  re-arm the idle chain predictably. Rejected.
* **Inhibitor hold while a face is present:** logind `Inhibit("idle")` (Noctalia
  consults `BlockInhibited`) + niri `ScreenSaver.Inhibit`. Measured on this
  host (niri 26.04 + Noctalia 5.2.1, MEMORY §11): releasing an inhibitor
  restarts the idle countdown from zero (`reinsert_timer`), so the chain is
  cleanly re-armed the moment the viewer leaves. No synthetic input, no polling
  loop. **Chosen.**

The niri cookie must be acquired and released on ONE persistent session-bus
connection — fresh-connection-per-call yields invalid cookies on `UnInhibit`
(measured). If niri's ScreenSaver name is absent, logind alone still suppresses
Noctalia's behaviours; the helper logs and continues.

## Crash-safety

The logind lock is a `systemd-inhibit … sleep <remaining>` child: it dies with
the holder (or self-expires at max_hold). The niri cookie dies with the bus
connection (niri drops vanished clients). A SIGKILL of the whole plugin can
therefore never leave the session pinned awake.

## Power posture

* One short capture+detect per check: idle triggers plus `recheck_interval_s`
  while holding. No continuous stream, no background poll outside the cadence.
* Detection runs on a 320 px-wide grayscale downscale regardless of
  `capture_width`; Haar default (tens of ms class), DNN opt-in and stricter.
* `cooldown_s` caps episode starts (camera wake-ups), `max_hold_s` caps a hold.
* The holder is a sleeping process plus a D-Bus lock.

Spike status (2026-10-20): niri `ScreenSaver.Inhibit` verified live (cookie
returned; takes `ss`), `v4l2-ctl --list-devices` autodetect verified on the
ASUS FHD webcam (`/dev/video0` capture, `video2/3` metadata). Capture wall-time
and cv2/Haar timing measurements are pending user approval (harness gates
camera reads and package installs); they fold into live validation and tune
the defaults if they disagree.

## Privacy posture

Frames are decoded in RAM and dropped (ffmpeg mjpeg → stdout pipe). Nothing is
written unless `debug_log` (verdicts and camera details, never images). The
helper refuses to touch the camera while locked and releases on lock. The
camera activity LED is the visible signal and cannot be suppressed.

## Module split

* `policy.luau` — pure spawn policy (enabled/locked/running/cooldown), tested
  under bare luau.
* `service.luau` — thin glue: settings, status-file presence, spawn argv,
  teardown stop-file. Spawns detached (an episode outlives runAsync's 60s
  timeout clamp).
* `face-watch` — python helper: camera autodetect, capture, detectors,
  inhibitor hold/release, episode loop, status file. Pure functions at the top
  (device parsing, sensitivity mapping, hold/release decision) are tested
  without cv2/dbus/camera via lazy imports.

## Detector data paths (measured 2026-10-22)

Haar cascade resolution must not assume `cv2.data.haarcascades`: that attribute
exists only in the pip wheels. Arch `python-opencv` (5.0.0) has no `cv2.data` at
all — its XMLs live in `/usr/share/opencv5/haarcascades/`. `face-watch` therefore
resolves the cascade over a candidate list (wheel data dir first, then
`/usr/share/opencv{5,4,}/haarcascades`) via the pure `resolve_haar_cascade()`,
covered by `tests/test_face_watch.py::HaarCascadePath`. A first live run failed
with `module 'cv2' has no attribute 'data'` before this fix.

## Rejected shapes

* Continuous presence polling (camera always on) — power and privacy cost.
* Doing capture/detect inside Luau — no subprocess-free camera or cv2 access.
* Killing the helper for teardown — a SIGKILL racing acquire could leak an
  inhibitor; the stop file (polled every second) is the polite path.
