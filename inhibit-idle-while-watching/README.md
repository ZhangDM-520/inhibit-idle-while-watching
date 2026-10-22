# Inhibit Idle While Watching

Keeps your session awake while you are sitting in front of the camera. When
Noctalia's idle chain is about to act (dim, screen-off, lock), this plugin
captures one webcam frame, looks for a face, and only while a face keeps
appearing does it hold the idle inhibitors — so watching a video or reading
never locks the screen, and walking away lets the normal idle chain proceed,
silently and exactly as configured.

## Plugin

| Field | Value |
| --- | --- |
| ID | `zhangdm/inhibit-idle-while-watching` |
| Entries | Service: `face-watch` |

## Requirements

- `noctalia`, `python3`, `ffmpeg`, `v4l2-ctl` on `PATH` (all in `dependencies`).
- Python modules for the helper: `cv2` (OpenCV, e.g. `python-opencv`) and `dbus`
  (e.g. `python-dbus`). The `dnn` detector additionally needs
  `assets/face-dnn.onnx` (not bundled yet — use `haar`, the default).
- A V4L2 webcam. The idle handshake below is compositor-agnostic (Noctalia owns
  idle), but the inhibitor paths are verified on Niri.

## Usage

The plugin cannot observe idle by itself (the plugin API has no idle hook), so
it is triggered by a Noctalia idle behaviour that messages it. Add a **command**
idle behaviour that fires a few seconds *before* the first action you want
postponed — example for postponing `dim` at 50 s:

```toml
# ~/.local/state/noctalia/settings.toml (or ~/.config/noctalia/config.toml)
[idle.behavior.presence-check]
action = "command"
command = "noctalia msg plugin zhangdm/inhibit-idle-while-watching:face-watch all idle-begin"
timeout = 45.0
```

The lead matters: the inhibitor must already be held when the next behaviour's
timer expires, and a check costs about a second end-to-end (measured ~0.3 s
capture + ~15 ms Haar on a 640px frame). A `timeout` equal to the behaviour you
are postponing loses that race every time — that action has already fired by the
time the face is detected.

Then `noctalia msg config-reload`. On each trigger the plugin captures one
frame; with a face present it holds logind's `idle` inhibitor and niri's
`ScreenSaver.Inhibit` and re-checks every *Re-check interval*; without a face
it releases immediately and your dim/screen-off/lock chain runs untouched.

Manual control:

```sh
noctalia msg plugin zhangdm/inhibit-idle-while-watching:face-watch all stop    # release now
noctalia msg plugin zhangdm/inhibit-idle-while-watching:face-watch all status  # log current state
```

## Settings

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `enabled` | `bool` | `true` | Master switch; off means the camera is never touched. |
| `camera_path` | `string` | `""` | Video device such as `/dev/video0`; empty auto-detects. |
| `detector` | `string` | `"haar"` | `haar` (cheap) or `dnn` (accurate, needs the ONNX model). |
| `sensitivity` | `int` | `5` | 1 lenient … 10 strict. |
| `capture_width` | `int` | `640` | Frame width per check (height 4:3); detection downscales to 320 px. |
| `recheck_interval_s` | `int` | `60` | Seconds between checks while holding. |
| `cooldown_s` | `int` | `10` | Minimum seconds between episodes (camera wake-up cap). |
| `max_hold_s` | `int` | `7200` | Hard cap on one continuous hold. |
| `debug_log` | `bool` | `false` | Helper activity log in the plugin data dir (verdicts, never images). |

## IPC

```sh
noctalia msg plugin zhangdm/inhibit-idle-while-watching:face-watch all <event> [payload]
```

| Event | Effect |
| --- | --- |
| `idle-begin` | Run a presence check (this is what the idle behaviour above sends). |
| `stop` | Release the inhibitors and end the current episode. |
| `status` | Append the helper's status file to the log for debugging. |

## Security

This plugin sees a webcam image — the most sensitive data on the desk. Threat
model and guarantees:

- **Image lifetime:** one frame per check, captured by `ffmpeg` straight into a
  stdout pipe, decoded in RAM (`cv2.imdecode`), and dropped when the check
  ends. There is **no code path that writes a frame to disk** (checked
  including debug and error paths). The camera activity LED lights during every
  capture — that is hardware, and it is your visible signal.
- **What is persisted:** `face-watch-status.json` (state, a verdict string such
  as `face (1)`, check count, timestamps) and — only with `debug_log = true` —
  `face-watch.log` (camera path and one-line verdicts). Neither contains image
  data. Both live in the plugin data directory, are written with mode `0600`
  (`O_NOFOLLOW`), and tighten any pre-existing looser file.
- **No capture while locked — fail closed:** the privacy gate captures only on
  an *explicit* "unlocked" from `noctalia msg status`. A lock screen, a missing
  or malformed status answer, or a schema drift all refuse capture ("skipped").
  The gate is checked at spawn (service side, also fail-closed), at check
  start, and again immediately before the frame is taken; a lock that appears
  while inhibitors are held releases them within 5 s.
- **No shell, fixed binaries:** `ffmpeg`, `v4l2-ctl`, `systemd-inhibit` and
  `noctalia` are invoked with argument lists (never through a shell). Settings
  such as `camera_path` are passed as discrete arguments; there is no string
  interpolation into a command line. Defaults come from `v4l2-ctl`
  autodetection (`/dev/videoN` only).
- **Two failure postures, deliberately different:** the *privacy gate* fails
  closed (above); the *inhibitor policy* fails open — any check error, a
  missing detector asset, a lock, or the stop file immediately releases the
  idle inhibitors, so a broken camera can never pin the session awake.
- **Inhibitor scope:** the logind `idle` blocker and niri's
  `ScreenSaver.Inhibit` cookie are held only while faces keep appearing. The
  logind blocker is a `systemd-inhibit` child carrying `PR_SET_PDEATHSIG`, so
  it dies with its holder even on `kill -9` (verified 2026-10-22); the niri
  cookie is released on the same bus path it was acquired on, and a connection
  death frees it regardless.
- **Detector weights:** `assets/face-dnn.onnx` is fetched, not bundled, and is
  **pinned by sha256** — only the exact reviewed `version-RFB-320.onnx`
  (upstream MIT repository in *Notes*) loads; anything else is refused before
  reaching OpenCV's ONNX parser.

Known limits: a local attacker with your user's privileges can read the same
camera; the plugin cannot defend against that.

## Notes

- **What "presence" means:** a face *toward the camera*. Measured 2026-10-22 at
  reclined desk distance: facing the screen is detected 12/12 (Haar, YuNet and
  the DNN model agree); looking down at a phone is missed by all three (the
  camera sees mostly the top of the head). By design a look-away releases the
  inhibitors — this plugin inhibits idle while you are *watching*, not merely
  occupying the chair.
- **Timing:** the check fires at `timeout` **plus Noctalia's global
  `pre_action_fade_seconds`**, and any input during that fade cancels the whole
  idle chain. With the default 30 s fade the camera only wakes at 75 s of
  perfect stillness; set `pre_action_fade_seconds = 0` for a check at exactly
  `timeout`.
- **Detectors:** `haar` (default, no assets needed) or `dnn`
  (`assets/face-dnn.onnx`, Ultra-Light-Fast-Generic-Face-Detector-1MB
  `version-RFB-320.onnx`, MIT — drop the file in to enable; detections are
  counted after non-maximum suppression).
- **Privacy:** see *Security* above (image lifetime, persistence, lock guard).
- **Power:** one short capture+detect per check (~1 s of camera), never a
  continuous stream. The holder is a sleeping process plus a D-Bus lock, not a
  polling loop. Tune `recheck_interval_s` and `capture_width` to taste.
- **What is spawned and written:** the service spawns `python3 face-watch
  episode …` (which runs `ffmpeg`, `v4l2-ctl`, `systemd-inhibit`, `noctalia msg
  status`); the helper writes `face-watch-status.json` and, with `debug_log`,
  `face-watch.log` in the plugin data directory. No network access.
- **Failure mode is fail-open:** any capture/detect error releases the
  inhibitors and lets the idle chain proceed — a broken camera never pins the
  session awake.
