# SpaceMouse Control for Houdini (Network + Viewport)

Control Houdini's Network Editor and 3D viewport using a 3Dconnexion SpaceMouse.

**Status**
- Navigation works (Network Editor pan/zoom, viewport tumble, viewport FPS/drone).
- SpaceMouse **button hotkeys are currently unreliable/broken** (see `PROJECT_SUMMARY.md`).

## Architecture

Why: Houdini + the 3Dconnexion driver make raw HID access unreliable inside Houdini. The stable pattern is "external reader + IPC".

```
[SpaceMouse] --(hidapi raw HID)--> [external reader process]
                                  |
                                  +-- UDP localhost:<port> --> [Houdini receiver (hou.session._spacemouse_receiver)]
                                                              |
                                                              +-- Network Editor / Scene Viewer navigation
```

## Requirements

- Windows
- Houdini 21+ (PySide6)
- Python venv at `E:\AI\Houdini_MCP\.venv` (used by `start_spacemouse_pan.bat`)
- `hidapi` installed in that venv (`pip install hidapi`)
- `uv` on PATH (optional): `start_spacemouse_pan.bat` uses `uv pip install hidapi` if `hidapi` is missing
- Admin rights (to stop 3Dconnexion processes for raw HID access)

## Quick Start

### 1) Start via Shelf Tools (recommended)

Shelf tools live at:
- `C:/Users/<you>/Documents/houdini21.0/toolbar/spacemouse.shelf`

Use:
- `SM Network` (Network Editor)
- `SM Viewport` (3D viewport tumble/orbit)
- `SM FPS` (3D viewport first-person/drone)

Each mode tool starts the Houdini receiver, sets the mode, and launches the external reader console.

### 2) Manual start

In Houdini Python Shell:
```python
import sys
sys.path.insert(0, r"E:\AI\Houdini_MCP")
from spacemouse_network_pan.spacemouse_standalone import start_receiver
start_receiver()
```

Then run (as Administrator):
```bat
start_spacemouse_pan.bat
```

To restore the 3Dconnexion driver afterwards:
```bat
restore_3dconnexion.bat
```

## Modes

| Mode | What it controls | Shelf tool |
|------|-------------------|-----------|
| `network` | Network Editor pan/zoom | `SM Network` |
| `viewport` | Scene Viewer tumble-style navigation | `SM Viewport` |
| `viewport_fps` | Scene Viewer first-person/drone navigation | `SM FPS` |

## Configuration (`config.json`)

Edit `config.json` directly or use `SM Config` in Houdini.

Key concepts:
- Axis tokens: `x`, `y`, `z`, `rx`, `ry`, `rz` (prefix with `-` to invert).
- Per-axis speed: `final = base_speed * axis_multiplier`.

Important settings:
- Network editor:
  - `axis_mapping`, `network_speed`, `network_axis_multiplier`
  - `presets` + `active_preset` (network mappings can be driven by presets)
- Viewport:
  - `viewport_axis_mapping`, `viewport_speed`, `viewport_axis_multiplier`
- FPS:
  - `fps_axis_mapping`, `fps_speed`, `fps_axis_multiplier`
- QoL:
  - `auto_mode_switch` (route input to Network Editor when cursor is over a Network Editor pane)
  - `hover_refresh` (reduces Scene Viewer hover-preselect "sticking" while navigating)
- Buttons:
  - `button_hotkeys` (per mode), currently unreliable/broken; see `PROJECT_SUMMARY.md`.

## Performance / Telemetry

The reader console prints a live status line including:
- `LAT` (last end-to-end latency, ms)
- `P90` (p90 latency over a window, ms)
- `B` (backlog steps: packets drained per Houdini tick)
- `Hz` (receiver apply rate)

## Dev Notes

- `reload_config()` updates settings on the running receiver instance.
- Code changes to receiver logic require `stop_receiver()` + `start_receiver()` (receiver class is defined inside `start_receiver()`).
- Houdini caches shelf tools; if `SM Config` changes don't show up, use Shelves -> "Reload All Shelves" or restart Houdini.

## Files

| Path | Purpose |
|------|---------|
| `spacemouse_standalone.py` | External reader + Houdini receiver implementation |
| `config.json` | User configuration |
| `start_spacemouse_pan.bat` | Launcher (kills driver processes, starts reader) |
| `restore_3dconnexion.bat` | Restores 3Dconnexion driver |
| `PROJECT_SUMMARY.md` | Current project status + debugging handoff |
| `dev/diagnose_spacemouse.py` | HID diagnostics |
| `dev/keyboard_pan.py` | Legacy discrete shortcut approach |
