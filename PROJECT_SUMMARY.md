# SpaceMouse for Houdini - Project Status & Handoff

This document is for agents picking up development of `spacemouse_network_pan`.
The previous "lessons learned" write-up is archived at `previous/PROJECT_SUMMARY_2026-01-04.md`.

## TL;DR
- Working: 3 modes (`network`, `viewport`, `viewport_fps`), camera-local navigation, low-latency receiver loop, per-axis multipliers, hover-preselect refresh mitigation, auto mode switching (network under cursor), perf HUD in reader terminal.
- Not working (major): SpaceMouse **button hotkeys do not trigger anything in Houdini**, and "hold" behavior is unreliable (often interrupted by cursor/mouse movement).

## What This Project Does
Control Houdini's:
- **Network Editor** pan/zoom (analog, SpaceMouse axes).
- **3D Viewport** navigation in two styles:
  - `viewport`: tumble/orbit-style camera movement (camera-local axes).
  - `viewport_fps`: first-person/drone-style movement (camera center, no orbit pivot).

## Features Implemented (Current State)

### Modes
- **`network`**: pans/zooms the Network Editor (uses `hou.NetworkEditor.setVisibleBounds()`).
- **`viewport`**: camera-local translate + rotate (tumble-style behavior).
- **`viewport_fps`**: camera-local translate + rotate around camera center (drone/first-person).
- **`cargo_attached`**: Spring Arm Effect - Box follows Camera at fixed distance (see `CARGO_ATTACHED_IMPLEMENTATION.md`).

### Cargo Attached Mode (Spring Arm) - NEW 2026-01-11
- Box (`/obj/cargo`) follows Camera (`/obj/sm_camera`) at fixed distance
- Yaw-only rotation facing camera (tidal locking effect)
- Uses real Camera node for accurate transforms
- User controls Camera with mouse, SpaceMouse will control Box rotation (future)

### Configurable Axis Mapping + Speed
From `config.json`:
- Axis mapping per mode: `axis_mapping` (network), `viewport_axis_mapping`, `fps_axis_mapping`.
- Base speeds per mode:
  - `network_speed.pan`, `network_speed.zoom`
  - `viewport_speed.translate`, `viewport_speed.rotate`
  - `fps_speed.translate`, `fps_speed.rotate`
- Per-axis multipliers:
  - `network_axis_multiplier`
  - `viewport_axis_multiplier`
  - `fps_axis_multiplier`

Final per-axis speed is `base_speed * axis_multiplier`.

### Performance / Latency Telemetry
The reader prints a live status line with:
- `LAT`: last end-to-end latency (ms)
- `P90`: 90th percentile latency over a sliding window (ms)
- `B`: backlog steps (how many UDP packets were drained in the last Houdini tick)
- `Hz`: effective apply rate inside Houdini

### Hover Preselect "Sticking" Mitigation
Optional `hover_refresh`:
- Injects lightweight mouse-move updates while navigating to reduce Houdini's cyan hover highlight "dragging" artifact.
- Methods: `win32`, `qt`, or `cursor` (see `config.json`).

### Auto Mode Switch (Quality-of-Life)
Optional `auto_mode_switch`:
- Routes SpaceMouse input to Network Editor automatically when the cursor is over a Network Editor pane (even if the current mode is viewport).

### Houdini Shelf Tools
Shelf file is stored in Houdini user prefs:
- `C:/Users/<you>/Documents/houdini21.0/toolbar/spacemouse.shelf`

Tools:
- `SM Network`, `SM Viewport`, `SM FPS`: set mode + start receiver + launch reader.
- `Space Mouse`: control/debug tool (reload config, debug info, etc.).
- `SM Config`: edits `config.json` (global options + per-mode settings).

Important: Houdini caches shelves; changes may require "Reload All Shelves" or restarting Houdini.
Note: the intended `SM Config` UX is:
- **Global Options**: `auto_mode_switch` + `hover_refresh`
- **Edit Network Config**: includes "Switch Preset" (network mode only)

## Architecture (Why It's Built This Way)

### High-Level Flow
1. **External reader process** reads SpaceMouse raw HID via `hidapi`.
2. Reader sends a compact **UDP** packet to Houdini at `localhost:<port>`.
3. **Houdini receiver** runs in-process (`hou.session._spacemouse_receiver`) and applies the latest state each UI tick.

```
[SpaceMouse HID] -> [external reader: spacemouse_standalone.py] -> UDP -> [Houdini receiver: start_receiver()]
```

### Why External Reader (Non-Negotiable)
The native 3Dconnexion driver conflicts with raw HID access and/or Houdini's native support. The working pattern is to:
- Stop 3Dconnexion background processes (admin).
- Read raw HID outside Houdini.
- Keep Houdini purely as a receiver/apply layer.

## How To Run (Current Workflow)

### Preferred: Shelf Tools (1-click)
Use `SM Viewport` / `SM Network` / `SM FPS` in Houdini.

These call:
- `start_receiver()` (inside Houdini)
- `set_mode(...)`
- `launch_reader(...)` (starts `start_spacemouse_pan.bat` in a new console, elevated)

### Manual
In Houdini Python shell:
```python
import sys
sys.path.insert(0, r"E:\AI\Houdini_MCP")
from spacemouse_network_pan.spacemouse_standalone import start_receiver
start_receiver()
```

In an elevated terminal:
```bat
start_spacemouse_pan.bat
```

### Dev Workflow (Restart vs Reload)
- `reload_config()` updates runtime settings on the existing receiver instance, but **does not update code** for the receiver class (it's defined inside `start_receiver()`).
- If you change `spacemouse_standalone.py` receiver logic, you must `stop_receiver()` + `start_receiver()` (or restart Houdini).
- Shelf tool scripts often call `importlib.reload(sm)`; this updates module globals but **won't replace** an already-instantiated receiver class.

## Current Blocking Issue: Buttons / Hotkeys (BROKEN)

### Symptom
- Reader terminal shows SpaceMouse buttons (and shows mapped strings like `BTN1:Shift+A`), but **Houdini does not react**.
- "Hold" behavior is unreliable; it frequently breaks as soon as the cursor/mouse moves.
- Modifier-only mappings (e.g. `Shift` alone) are especially problematic.
- Some keys like `` ` `` have been reported as not working in certain iterations.

### Example Reader Output (Problem Cases)
- High button indices (seen in earlier iterations): the reader printed many button slots (e.g. BTN17/BTN19/BTN32) even on devices with only a few physical buttons. This strongly suggests either a stale build parsing too many bytes from the HID button report, or a noisy/incorrect button report source.
- Log "spam" (seen earlier): when the live status line wrapped, each update appeared as a new line rather than overwriting the previous line. This was a terminal formatting issue, not a receiver loop issue.

### Caution
This area has been changed multiple times during debugging and may be in a partially-regressed state. Treat any "seems fixed" reports as unreliable until verified with instrumentation (event filters, raw HID logs, and a controlled focus test).

### What We Know / Why This Is Hard
Button support has two separate problems:

1) **Button state noise / flicker**
- When axes move + buttons are pressed, some devices/collections can emit extra "button" reports or flickering masks.
- A flickering mask looks like press/release sequences, which will cancel "hold" even if injection is correct.

2) **Injection into Houdini**
Two approaches were tried at different points:
- **Qt synthetic key events** (`QKeyEvent` posted/sent to widgets):
  - Pros: doesn't care which OS window is foreground.
  - Cons: does **not** change OS modifier state; mouse movement generates events without those modifiers, so "hold" can appear to drop. Some Houdini shortcuts may also ignore non-spontaneous Qt events.
- **Win32 SendInput** (OS-level key state):
  - Pros: true key-down/key-up semantics, modifiers affect mouse messages.
  - Cons: keys are delivered to the **foreground OS window**. The reader console is often foreground, and Windows focus rules can prevent reliably forcing Houdini to the foreground. Result: "no reaction in Houdini".

This is why a change can "fix" one aspect (e.g., hold semantics) while making it appear broken overall (events go to the wrong window).

### Why It Can Seem Intermittent
- If OS-level injection is used, keys will only go to Houdini when Houdini is the **active foreground window**; if the reader console has focus, keys can "disappear" (or land in the console).
- If Qt-level injection is used, one-shot shortcuts may appear to work, but modifier "holds" won't affect real mouse movement because OS modifier state never changed.

### Debug Timeline (Recent)
- Added `button_hotkeys` per mode in `config.json`; reader prints mappings next to pressed buttons (e.g. `B1:Shift+A`) to verify config parsing (works).
- Early behavior: some one-shot keys could trigger, but modifier-only bindings (Shift/Ctrl/Alt) did not behave like a hold.
- Implemented "hold" semantics by tracking the `buttons` bitmask and sending key-down on press + key-up on release; observed "hold interrupted by cursor move".
- Tried Win32 `SendInput` keydown/keyup to get real OS-level holds; often regressed to "no reaction" because the reader console is the foreground window and receives the injected keys.
- Tried bringing Houdini to the foreground before `SendInput`; Windows focus rules make this unreliable in practice (still inconsistent).
- Observed noisy/spurious button masks in some iterations (e.g. high button numbers like BTN17+ in logs); mitigated by limiting parsing of report ID 3 to the first 2 bytes (16 bits). If your device has >16 buttons, you may need to parse more bytes carefully and validate stability.
- Backtick (`` ` ``) and modifier-only bindings have repeatedly shown regressions; treat as a separate validation target.
- Reader "log spam" was caused by the live status line wrapping; truncating/padding to terminal width avoids newlines during `\r` updates.
- Current state: reader sees button presses and shows mapped combos, but Houdini still does not receive them, and "hold" breaks easily.

### Button Test Checklist (Suggested)
1. Disable `hover_refresh` and `auto_mode_switch` while testing buttons (reduce confounds).
2. Ensure Houdini is the foreground window (click into a pane); keep the reader console in the background.
3. Start with a non-modifier single key mapping (`Space`, `A`, `F1`) and confirm Houdini receives `KeyPress/KeyRelease` using an in-Houdini event filter.
4. Test modifier-only bindings (`Shift`, `Ctrl`, `Alt`) and confirm whether modifier state is visible to mouse-driven tools while moving the cursor.
5. Test combos (`Shift+A`, `Alt+F`, `Ctrl+Shift+S`).
6. Test punctuation (especially backtick: `` ` ``), which has historically regressed.
7. While holding a SpaceMouse button, move the SpaceMouse axes and also move the mouse cursor; verify the `buttons` mask stays stable (no rapid press/release flicker).
8. If high button numbers appear (BTN17+), log raw HID report bytes for `report_id == 3` and reconsider how many bytes should be parsed.

### Guidance for the Next Agent (Debug Path, Not Implementation)
Start by isolating which layer is failing.

1) **Verify the raw button mask is stable**
- Log/report the raw HID bytes for button reports and confirm it is not toggling rapidly while held.
- Ensure only the intended HID collection/interface is opened for button parsing.

2) **Prove whether Houdini is receiving any key events**
- Install a Qt event filter (or temporary widget) inside Houdini that logs `KeyPress/KeyRelease`.
- Press SpaceMouse buttons and confirm whether any key events are observed and which widget receives them.

3) **If OS-level injection is used, confirm where keys go**
- Test with a simple foreground app (e.g., Notepad) to see if the key is being generated at all.
- If it appears in the reader console instead of Houdini, the problem is focus/foreground ownership, not key generation.

4) **Consider bypassing keyboard simulation entirely**
- A robust path is to map buttons to **Houdini actions** directly (menu commands / hscript / tool toggles) rather than trying to synthesize keystrokes. This avoids "spontaneous event" and foreground-window problems.

### Where to Look in Code
- Reader (external process): `spacemouse_standalone.py` -> `read_spacemouse_loop()` parses HID reports and sends UDP (look for `report_id == 3` button parsing).
- Receiver (Houdini): `start_receiver()` -> `SpaceMouseReceiver._apply_button_hotkeys()` decides press/release/hold from the `buttons` bitmask.
- Hotkey parsing: `SpaceMouseReceiver._parse_hotkey_combo_hold()` and the token maps for keys/VKs.
- Hover mitigation: `SpaceMouseReceiver._maybe_refresh_hover_preselect()` (can interact with modifier semantics if using OS-level injection).

## Notes on Shelf/UI Changes Not Showing
If `SM Config` seems unchanged:
- Houdini caches shelf definitions.
- You may need Shelves -> "Reload All Shelves", or restart Houdini.
- The source file is `C:/Users/<you>/Documents/houdini21.0/toolbar/spacemouse.shelf` (not in this repo).

## File Map
- `spacemouse_standalone.py`: both external reader (when run as script) and Houdini receiver (`start_receiver()`).
- `config.json`: all user-facing configuration.
- `start_spacemouse_pan.bat`: kills 3Dconnexion processes (admin) and starts the reader using the repo's venv.
- `restore_3dconnexion.bat`: restores the 3Dconnexion driver service.
- `dev/diagnose_spacemouse.py`: HID diagnostic helper.
- `dev/keyboard_pan.py`: legacy/alternative approach (discrete keyboard shortcuts).
- `dev/DEVELOPMENT_HISTORY.md`: early development notes.

## Cleanup Notes
- Searched for zero-length files under `spacemouse_network_pan/`: none found as of 2026-01-07.
