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
idle behaviour that fires at (or just before) the first action you want
postponed — example for postponing `dim` at 50 s:

```toml
# ~/.local/state/noctalia/settings.toml (or ~/.config/noctalia/config.toml)
[idle.behavior.presence-check]
action = "command"
command = "noctalia msg plugin zhangdm/inhibit-idle-while-watching:face-watch all idle-begin"
timeout = 50.0
```

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

## Notes

- **Privacy:** frames are decoded in RAM and dropped; nothing is written to
  disk (debug mode logs verdicts only). The helper refuses to capture while
  the session is locked. The camera's activity LED lights during each check —
  that is the user-visible signal and cannot be suppressed.
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
