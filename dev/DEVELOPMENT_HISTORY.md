# Space Mouse Network Pan - Development History

This document archives the development process and technical discoveries made while building this tool.

## Problem Statement

The 3Dconnexion Space Mouse works natively with Houdini's 3D viewport, but NOT with the Network Editor. The goal was to enable Space Mouse control for Network Editor panning.

## Technical Challenges Solved

### 1. 3Dconnexion Driver Exclusive Access

**Problem:** When Houdini is running, the 3Dconnexion driver has exclusive HID access:
- COM interface (`TDxInput.Device`) returns zeros
- Raw HID is blocked
- Windows Raw Input messages are intercepted

**Solution:** Stop the driver processes, read HID directly, send via UDP to Houdini.

**Processes that must be killed (requires admin):**
- `3DxService.exe`
- `3dxpiemenus.exe`
- `3DxSmartUi.exe`
- `3DxVirtualLCD.exe`
- `3DxProfileServer.exe`
- `Mgl3DCtlrRPCService.exe` (critical - requires admin rights)

### 2. HID Interface Selection

**Problem:** 3Dconnexion devices expose multiple HID interfaces. Not all contain motion data.

**Solution:** Look for `usage_page=1, usage=8` (Multi-axis Controller) interface.

**Discovery:** The 3Dconnexion Universal Receiver sends all 6 axes in a SINGLE HID report:
- Bytes 1-6: Translation (x, y, z) as signed 16-bit integers
- Bytes 7-12: Rotation (rx, ry, rz) as signed 16-bit integers

This differs from some older devices that send separate reports for translation (ID=1) and rotation (ID=2).

### 3. Qt Signal Reliability in Houdini

**Problem:** Qt signals (`readyRead`) don't fire reliably in Houdini's event loop.

**Solution:** Use `hou.ui.addEventLoopCallback()` to poll the UDP socket instead of relying on Qt signals.

### 4. PySide Version

**Discovery:** Houdini 21+ uses PySide6, not PySide2. This affects imports and some API calls.

### 5. Config Reload After Module Reimport

**Problem:** When using `importlib.reload()`, the receiver instance was created by the OLD module, so updating config didn't work.

**Solution:** Store receiver in `hou.session._spacemouse_receiver` and access it directly when reloading config, rather than relying on module-level variables.

## Architecture

```
[Space Mouse] 
    ↓ (HID)
[spacemouse_standalone.py - external process]
    ↓ (UDP localhost:19879)
[SpaceMouseReceiver in Houdini]
    ↓ (hou.ui.addEventLoopCallback)
[Network Editor setVisibleBounds()]
```

## Alternative Approaches Considered

### Keyboard Shortcuts (keyboard_pan.py)
Map Space Mouse buttons to keyboard shortcuts via 3Dconnexion software. Works but provides discrete steps, not smooth analog control.

### COM Interface
Tried `TDxInput.Device` COM interface - returns zeros when Houdini has native support active.

### Windows Raw Input
Tried registering for WM_INPUT messages - driver intercepts them.

## Files

| File | Purpose |
|------|---------|
| `spacemouse_standalone.py` | Main implementation (HID reader + Houdini receiver) |
| `config.json` | User configuration with presets |
| `start_spacemouse_pan.bat` | One-click launcher (kills driver, starts reader) |
| `restore_3dconnexion.bat` | Restore driver for normal 3D viewport use |
| `dev/diagnose_spacemouse.py` | HID diagnostic tool |
| `dev/keyboard_pan.py` | Alternative discrete-step approach |

## Shelf Tools Created

Two shelf tools on the "Custom" shelf:
1. **Space Mouse** - Start/stop receiver, reload config, debug info
2. **SM Config** - Switch presets, edit all config values

## Requirements

- Windows (tested on Windows 11)
- Python 3.x with `hidapi` package
- Houdini 21+ (uses PySide6)
- 3Dconnexion Space Mouse device
