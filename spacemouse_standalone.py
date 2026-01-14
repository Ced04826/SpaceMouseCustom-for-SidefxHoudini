"""
Space Mouse Standalone Reader for Houdini Network Editor

This script reads Space Mouse input via raw HID and sends commands to Houdini
via UDP socket. Run this OUTSIDE of Houdini.

REQUIREMENTS:
    1. STOP the 3Dconnexion driver first:
       - Open Task Manager
       - End these processes: 3DxService.exe, 3dxpiemenus.exe, 3DxSmartUi.exe, etc.
       - Or run as admin: taskkill /F /IM 3DxService.exe

    2. Install hidapi: pip install hidapi

    3. Start the receiver in Houdini first (see below)

USAGE:
    1. In Houdini Python Shell:
       >>> import sys
       >>> sys.path.append(r"E:\\AI\\Houdini_MCP")
       >>> from spacemouse_network_pan.spacemouse_standalone import start_receiver
       >>> start_receiver()

    2. In a separate terminal (NOT Houdini):
       > python spacemouse_standalone.py

    3. Move the Space Mouse - Network Editor should pan!

    4. To stop:
       - Press Ctrl+C in the terminal
       - In Houdini: stop_receiver()

CONFIGURATION:
    Edit config.json to customize speed, axis mapping, and deadzone.
"""

import socket
import json
import struct
import time
import sys
import os
import atexit
from collections import deque

# Get config file path (same directory as this script)
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def load_config():
    """Load configuration from config.json."""
    default_config = {
        "mode": "network",  # 'network', 'viewport', or 'viewport_fps'
        "active_preset": "translate",
        "presets": {
            "translate": {
                "axis_mapping": {
                    "pan_horizontal": "x",
                    "pan_vertical": "-y",
                    "zoom": "z",
                }
            },
            "rotate": {
                "axis_mapping": {
                    "pan_horizontal": "-ry",
                    "pan_vertical": "-rx",
                    "zoom": "z",
                }
            },
        },
        "network_speed": {"pan": 0.03, "zoom": 0.07},
        "network_axis_multiplier": {
            "pan_horizontal": 1.0,
            "pan_vertical": 1.0,
            "zoom": 1.0,
        },
        "viewport_axis_mapping": {
            "translate_x": "x",
            "translate_y": "y",
            "translate_z": "z",
            "rotate_x": "rx",
            "rotate_y": "ry",
            "rotate_z": "rz",
        },
        "viewport_speed": {"translate": 0.5, "rotate": 0.3},
        "viewport_axis_multiplier": {
            "translate_x": 1.0,
            "translate_y": 1.0,
            "translate_z": 1.0,
            "rotate_x": 1.0,
            "rotate_y": 1.0,
            "rotate_z": 1.0,
        },
        "fps_axis_mapping": {
            "translate_x": "x",
            "translate_y": "y",
            "translate_z": "z",
            "rotate_x": "rx",
            "rotate_y": "ry",
            "rotate_z": "rz",
        },
        "fps_speed": {"translate": 0.5, "rotate": 0.3},
        "fps_axis_multiplier": {
            "translate_x": 1.0,
            "translate_y": 1.0,
            "translate_z": 1.0,
            "rotate_x": 1.0,
            "rotate_y": 1.0,
            "rotate_z": 1.0,
        },
        "button_hotkeys": {
            "network": {"button_1": "none", "button_2": "none"},
            "viewport": {"button_1": "none", "button_2": "none"},
            "viewport_fps": {"button_1": "none", "button_2": "none"},
        },
        # Optional: If enabled, automatically route SpaceMouse input to the
        # Network Editor when the mouse cursor is over a Network Editor pane.
        # This makes mode switching seamless: viewport navigation when over the
        # Scene Viewer, network panning when over the Network Editor.
        "auto_mode_switch": {"enabled": False, "network_under_cursor": True},
        # Optional workaround: when navigating the viewport via script, Houdini
        # may not recompute hover-preselect until the mouse moves. When enabled,
        # we inject a lightweight MouseMove to refresh it.
        "hover_refresh": {
            "enabled": False,
            "hz": 30,
            "method": "win32",  # win32 (WM_MOUSEMOVE), qt (synthetic), cursor (jitter cursor)
            "jitter_px": 1,
        },
        "deadzone": {"value": 15},
        "network": {"port": 19879},
    }

    loaded = {}
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                loaded = json.load(f)
                # Merge with defaults
                for key in default_config:
                    if key in loaded:
                        if isinstance(default_config[key], dict) and key != "presets":
                            default_config[key].update(
                                {
                                    k: v
                                    for k, v in loaded[key].items()
                                    if not k.startswith("#")
                                }
                            )
                        else:
                            default_config[key] = loaded[key]

                # Handle legacy 'speed' key (migrate to network_speed)
                if "speed" in loaded and "network_speed" not in loaded:
                    default_config["network_speed"] = loaded["speed"]

            print(f"Config loaded from {CONFIG_PATH}")
    except Exception as e:
        print(f"Warning: Could not load config ({e}), using defaults")

    # Backward compatibility: if the config file doesn't define FPS settings, mirror the viewport.
    if not isinstance(loaded, dict):
        loaded = {}
    if "fps_axis_mapping" not in loaded:
        default_config["fps_axis_mapping"] = dict(
            default_config.get("viewport_axis_mapping", {})
        )
    if "fps_speed" not in loaded:
        default_config["fps_speed"] = dict(default_config.get("viewport_speed", {}))
    if "fps_axis_multiplier" not in loaded:
        default_config["fps_axis_multiplier"] = dict(
            default_config.get("viewport_axis_multiplier", {})
        )

    # Resolve active preset to axis_mapping (for network mode)
    active = default_config.get("active_preset", "translate")
    if active in default_config.get("presets", {}):
        preset = default_config["presets"][active]
        default_config["axis_mapping"] = preset.get("axis_mapping", {})
    else:
        # Fallback to translate preset
        default_config["axis_mapping"] = default_config["presets"]["translate"][
            "axis_mapping"
        ]

    # Alias for backward compatibility
    default_config["speed"] = default_config["network_speed"]

    return default_config


def save_config(config):
    """Save configuration to config.json."""
    # Build clean config for saving (preserve structure)
    save_data = {
        "# Space Mouse Configuration": "",
        "mode": config.get("mode", "network"),
        "# mode options: network (Network Editor), viewport (3D Viewport tumble), viewport_fps (3D Viewport first-person)": "",
        "active_preset": config.get("active_preset", "translate"),
        "presets": {},
        "network_speed": {
            "pan": config.get("network_speed", {}).get("pan", 0.03),
            "zoom": config.get("network_speed", {}).get("zoom", 0.07),
        },
        "network_axis_multiplier": config.get(
            "network_axis_multiplier",
            {"pan_horizontal": 1.0, "pan_vertical": 1.0, "zoom": 1.0},
        ),
        "viewport_axis_mapping": config.get(
            "viewport_axis_mapping",
            {
                "translate_x": "x",
                "translate_y": "y",
                "translate_z": "z",
                "rotate_x": "rx",
                "rotate_y": "ry",
                "rotate_z": "rz",
            },
        ),
        "viewport_speed": {
            "translate": config.get("viewport_speed", {}).get("translate", 0.5),
            "rotate": config.get("viewport_speed", {}).get("rotate", 0.3),
        },
        "viewport_axis_multiplier": config.get(
            "viewport_axis_multiplier",
            {
                "translate_x": 1.0,
                "translate_y": 1.0,
                "translate_z": 1.0,
                "rotate_x": 1.0,
                "rotate_y": 1.0,
                "rotate_z": 1.0,
            },
        ),
        "fps_axis_mapping": config.get(
            "fps_axis_mapping",
            {
                "translate_x": "x",
                "translate_y": "y",
                "translate_z": "z",
                "rotate_x": "rx",
                "rotate_y": "ry",
                "rotate_z": "rz",
            },
        ),
        "fps_speed": {
            "translate": config.get("fps_speed", {}).get("translate", 0.5),
            "rotate": config.get("fps_speed", {}).get("rotate", 0.3),
        },
        "fps_axis_multiplier": config.get(
            "fps_axis_multiplier",
            {
                "translate_x": 1.0,
                "translate_y": 1.0,
                "translate_z": 1.0,
                "rotate_x": 1.0,
                "rotate_y": 1.0,
                "rotate_z": 1.0,
            },
        ),
        "button_hotkeys": config.get(
            "button_hotkeys",
            {
                "network": {"button_1": "none", "button_2": "none"},
                "viewport": {"button_1": "none", "button_2": "none"},
                "viewport_fps": {"button_1": "none", "button_2": "none"},
            },
        ),
        "auto_mode_switch": config.get(
            "auto_mode_switch", {"enabled": False, "network_under_cursor": True}
        ),
        "hover_refresh": config.get(
            "hover_refresh",
            {
                "enabled": False,
                "hz": 30,
                "method": "win32",
                "jitter_px": 1,
            },
        ),
        "deadzone": {"value": config.get("deadzone", {}).get("value", 15)},
        "network": {"port": config.get("network", {}).get("port", 19879)},
    }

    # Copy presets
    for name, preset in config.get("presets", {}).items():
        if not name.startswith("#"):
            save_data["presets"][name] = preset

    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(save_data, f, indent=4)
        print(f"Config saved to {CONFIG_PATH}")
        return True
    except Exception as e:
        print(f"Error saving config: {e}")
        return False


def switch_preset(preset_name):
    """Switch to a different preset and reload."""
    global CONFIG
    CONFIG = load_config()

    if preset_name not in CONFIG.get("presets", {}):
        print(
            f"Preset '{preset_name}' not found. Available: {list(CONFIG['presets'].keys())}"
        )
        return False

    CONFIG["active_preset"] = preset_name
    CONFIG["axis_mapping"] = CONFIG["presets"][preset_name]["axis_mapping"]
    save_config(CONFIG)

    # Apply to receiver
    reload_config()
    print(f"Switched to preset: {preset_name}")
    return True


def get_presets():
    """Get list of available presets."""
    config = load_config()
    return list(config.get("presets", {}).keys())


def get_active_preset():
    """Get the currently active preset name."""
    config = load_config()
    return config.get("active_preset", "translate")


# Load config
CONFIG = load_config()

# Configuration from config file
UDP_HOST = "localhost"
UDP_PORT = CONFIG["network"]["port"]
POLL_RATE = 60  # Hz
READER_STATE_DIR = os.path.join(os.path.expanduser("~"), ".spacemouse_network_pan")
READER_PIDFILE = os.path.join(READER_STATE_DIR, f"reader_{UDP_PORT}.json")


def _pid_exists(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
            if handle:
                try:
                    exit_code = wintypes.DWORD()
                    if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                        return int(exit_code.value) == STILL_ACTIVE
                    # If we can't query exit status, assume it's running.
                    return True
                finally:
                    kernel32.CloseHandle(handle)

            # If we can't query due to privileges, assume it's running so we
            # don't launch duplicates from non-elevated Houdini sessions.
            err = kernel32.GetLastError()
            return err == 5  # ERROR_ACCESS_DENIED
        except Exception:
            return False

    try:
        os.kill(pid, 0)
    except Exception:
        return False
    return True


def _pid_create_time_nt(pid):
    """Best-effort Windows process creation time (FILETIME int) or None."""
    if os.name != "nt":
        return None
    if not isinstance(pid, int) or pid <= 0:
        return None

    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

        class FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD),
            ]

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if not handle:
            return None

        try:
            creation = FILETIME()
            exit_time = FILETIME()
            kernel_time = FILETIME()
            user_time = FILETIME()
            ok = kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            )
            if not ok:
                return None
            return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def _pid_matches(pid, expected_create_time=None):
    """Check whether pid is running and (optionally) matches expected create time."""
    if not _pid_exists(pid):
        return False
    if expected_create_time is None:
        return True

    current = _pid_create_time_nt(pid)
    if current is None:
        # If we expected a specific process instance but can no longer query its
        # creation time (e.g., PID reused by an elevated/system process that
        # denies access), treat it as a mismatch so the reader can auto-exit.
        return False
    return current == expected_create_time


def is_reader_running():
    """Best-effort check whether the external reader is already running."""
    if not os.path.exists(READER_PIDFILE):
        return False

    pid = None
    try:
        with open(READER_PIDFILE, "r") as f:
            data = json.load(f)
            pid = data.get("pid")
    except Exception:
        try:
            with open(READER_PIDFILE, "r") as f:
                pid = int(f.read().strip())
        except Exception:
            pid = None

    if _pid_exists(pid):
        return True

    # Stale pidfile
    try:
        os.remove(READER_PIDFILE)
    except Exception:
        pass
    return False


def _register_reader_pidfile():
    try:
        os.makedirs(READER_STATE_DIR, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "t_start_ns": time.time_ns(),
            "udp_port": UDP_PORT,
        }
        with open(READER_PIDFILE, "w") as f:
            json.dump(payload, f)
    except Exception:
        return

    def _cleanup():
        try:
            with open(READER_PIDFILE, "r") as f:
                data = json.load(f)
            if data.get("pid") != os.getpid():
                return
        except Exception:
            pass
        try:
            os.remove(READER_PIDFILE)
        except Exception:
            pass

    atexit.register(_cleanup)


# 3Dconnexion device IDs (0x256F is 3Dconnexion, 0x046D is Logitech legacy)
VENDOR_3DCONNEXION = 0x256F
VENDOR_LOGITECH = 0x046D

# Known 3Dconnexion product IDs
SPACEMOUSE_PRODUCTS = {
    0xC62E: "SpaceMouse Wireless",
    0xC652: "3Dconnexion Universal Receiver",
    0xC626: "SpaceNavigator",
    0xC627: "SpaceExplorer",
    0xC628: "SpacePilot",
    0xC629: "SpacePilot Pro",
    0xC62B: "SpaceMouse Pro",
    0xC631: "SpaceMouse Pro Wireless",
    0xC632: "SpaceMouse Pro Wireless Receiver",
    0xC633: "SpaceMouse Enterprise",
    0xC635: "SpaceMouse Compact",
}


def find_spacemouse():
    """Find all Space Mouse HID interfaces."""
    import hid

    axes = []
    others = []

    # Find all 3Dconnexion HID interfaces.
    # Prefer the standard Multi-axis Controller interface (usage_page=1, usage=8).
    # Opening extra joystick/gamepad-style collections can produce spurious
    # "button" reports on some devices.
    for d in hid.enumerate():
        if d.get("vendor_id") != VENDOR_3DCONNEXION:
            continue
        # Multi-axis Controller interface (usage_page=1, usage=8)
        if d.get("usage_page") == 1 and d.get("usage") == 8:
            axes.append(d)
        # Fallback: other generic desktop controls
        elif d.get("usage_page") == 1 and d.get("usage") in (
            4,
            5,
            6,
        ):  # Joystick, Game Pad, Multi-axis
            others.append(d)

    interfaces = axes if axes else others

    # If no standard interfaces found, try vendor-specific
    if not interfaces:
        for d in hid.enumerate():
            if d["vendor_id"] == VENDOR_3DCONNEXION:
                if b"Col01" in d["path"] or b"Col02" in d["path"]:
                    interfaces.append(d)

    return interfaces


def read_spacemouse_loop(houdini_pid=None):
    """Main loop to read Space Mouse and send to Houdini.

    Args:
        houdini_pid: Optional Houdini PID to monitor. If provided, the reader
            will exit automatically once the Houdini process is no longer
            running (useful when the reader was launched from a shelf tool).
    """
    import hid

    print("=" * 50)
    print("Space Mouse -> Houdini Network Editor")
    print("=" * 50)

    if is_reader_running():
        print(f"\nAnother Space Mouse reader appears to already be running.")
        print(f"PID file: {READER_PIDFILE}")
        print("If it's stale, delete the pid file and try again.")
        return False
    _register_reader_pidfile()

    # Find all device interfaces
    print("\nLooking for Space Mouse...")
    interfaces = find_spacemouse()

    if not interfaces:
        print("ERROR: No Space Mouse found!")
        print("Make sure the device is connected.")
        return False

    print(f"Found {len(interfaces)} interface(s):")
    for i, d in enumerate(interfaces):
        print(
            f"  [{i}] {d['product_string']} - usage_page={d['usage_page']}, usage={d['usage']}"
        )

    # Open all interfaces
    devices = []
    print("\nOpening devices...")
    for d in interfaces:
        try:
            device = hid.device()
            device.open_path(d["path"])
            device.set_nonblocking(True)
            devices.append(device)
            print(f"  Opened: usage_page={d['usage_page']}, usage={d['usage']}")
        except Exception as e:
            print(f"  Failed to open {d['path']}: {e}")

    if not devices:
        print("ERROR: Could not open any device!")
        print("\nMake sure the 3Dconnexion driver is STOPPED!")
        return False

    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    print(f"\nSending to Houdini at {UDP_HOST}:{UDP_PORT}")
    print("Make sure start_receiver() is running in Houdini!")
    print("\nPress Ctrl+C to stop\n")

    parent_pid = (
        houdini_pid if isinstance(houdini_pid, int) and houdini_pid > 0 else None
    )
    parent_create_time = _pid_create_time_nt(parent_pid) if parent_pid else None
    if parent_pid:
        print(f"Monitoring Houdini PID {parent_pid} (auto-exit on close)\n")

    # State
    x, y, z = 0, 0, 0
    rx, ry, rz = 0, 0, 0
    buttons_mask = 0
    last_sent_buttons_mask = 0
    scale = 350.0
    deadzone = CONFIG["deadzone"]["value"]  # From config

    # Printing every packet can add noticeable overhead/jitter on Windows
    # terminals. Throttle debug output to keep input processing smooth.
    debug_print_hz = 20
    debug_print_interval = 1.0 / debug_print_hz
    last_debug_print = 0.0

    # Include lightweight timing/sequence metadata so the Houdini receiver can
    # compute end-to-end latency and packet backlog without relying on "feel".
    seq = 0
    last_perf = {}
    shutdown_requested = False
    config_mtime = None
    hotkey_mode = None
    hotkey_map = {}
    last_status_len = 0

    def refresh_hotkeys():
        nonlocal config_mtime, hotkey_mode, hotkey_map
        try:
            mtime = os.path.getmtime(CONFIG_PATH)
        except Exception:
            return
        if config_mtime is not None and mtime == config_mtime:
            return
        config_mtime = mtime
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        hotkey_mode = data.get("mode", "network")
        hotkey_map = data.get("button_hotkeys", {}) or {}

    def poll_messages():
        nonlocal last_perf, shutdown_requested, parent_pid, parent_create_time
        while True:
            try:
                payload, _addr = sock.recvfrom(4096)
            except BlockingIOError:
                break
            except Exception:
                break

            try:
                msg = json.loads(payload.decode())
            except Exception:
                continue

            if not isinstance(msg, dict):
                continue

            msg_type = msg.get("type")
            if msg_type == "perf":
                last_perf = msg
            elif msg_type == "hello_reply":
                hp = msg.get("houdini_pid")
                if parent_pid is None and isinstance(hp, int) and hp > 0:
                    parent_pid = hp
                    parent_create_time = _pid_create_time_nt(parent_pid)
                    print(
                        f"\nMonitoring Houdini PID {parent_pid} (auto-exit on close)\n"
                    )
            elif msg_type == "shutdown":
                shutdown_requested = True
                break

    # Register with the Houdini receiver so it can send perf/shutdown replies even
    # before any motion occurs.
    last_hello_send = 0.0
    try:
        hello = json.dumps(
            {"type": "hello", "seq": 0, "t_send_ns": time.time_ns()}
        ).encode()
        sock.sendto(hello, (UDP_HOST, UDP_PORT))
        last_hello_send = time.time()
    except Exception:
        pass

    try:
        while True:
            if parent_pid and not _pid_matches(parent_pid, parent_create_time):
                print("\n\nHoudini has exited; stopping reader.")
                break

            # If the receiver started after the reader (or the initial UDP packet
            # was dropped), keep sending a lightweight hello until we learn the
            # Houdini PID via hello_reply.
            if parent_pid is None:
                now_s = time.time()
                if (now_s - last_hello_send) >= 1.0:
                    try:
                        hello = json.dumps(
                            {"type": "hello", "seq": 0, "t_send_ns": time.time_ns()}
                        ).encode()
                        sock.sendto(hello, (UDP_HOST, UDP_PORT))
                        last_hello_send = now_s
                    except Exception:
                        pass

            poll_messages()
            if shutdown_requested:
                print("\n\nShutdown requested by Houdini; exiting.")
                break

            # Read from ALL devices
            for device in devices:
                while True:
                    data = device.read(64)
                    if not data:
                        break

                    report_id = data[0]

                    if report_id == 1 and len(data) >= 7:
                        # Translation
                        x = struct.unpack("<h", bytes(data[1:3]))[0]
                        y = struct.unpack("<h", bytes(data[3:5]))[0]
                        z = struct.unpack("<h", bytes(data[5:7]))[0]

                        # Some devices send rotation in the same report (bytes 7-12)
                        if len(data) >= 13:
                            rx = struct.unpack("<h", bytes(data[7:9]))[0]
                            ry = struct.unpack("<h", bytes(data[9:11]))[0]
                            rz = struct.unpack("<h", bytes(data[11:13]))[0]

                    elif report_id == 2 and len(data) >= 7:
                        # Rotation (separate report format)
                        rx = struct.unpack("<h", bytes(data[1:3]))[0]
                        ry = struct.unpack("<h", bytes(data[3:5]))[0]
                        rz = struct.unpack("<h", bytes(data[5:7]))[0]
                    elif report_id == 3 and len(data) >= 2:
                        # Buttons (bitmask). Most 3Dconnexion devices report a small
                        # mask (often 8–16 bits). Parsing extra bytes can create
                        # spurious high-button bits on some devices.
                        mask = 0
                        for i, b in enumerate(data[1:3]):  # 16 buttons max
                            mask |= (int(b) & 0xFF) << (8 * i)
                        buttons_mask = mask

            # Apply deadzone and normalize
            def apply_deadzone(v):
                return 0 if abs(v) < deadzone else v / scale

            nx = apply_deadzone(x)
            ny = apply_deadzone(y)
            nz = apply_deadzone(z)
            nrx = apply_deadzone(rx)
            nry = apply_deadzone(ry)
            nrz = apply_deadzone(rz)

            buttons_changed = buttons_mask != last_sent_buttons_mask

            # Send if there's input (motion) or button state changes.
            if (
                nx != 0 or ny != 0 or nz != 0 or nrx != 0 or nry != 0 or nrz != 0
            ) or buttons_changed:
                seq += 1
                msg = json.dumps(
                    {
                        "x": nx,
                        "y": ny,
                        "z": nz,
                        "rx": nrx,
                        "ry": nry,
                        "rz": nrz,
                        "buttons": int(buttons_mask),
                        "seq": seq,
                        "t_send_ns": time.time_ns(),
                    }
                ).encode()
                sock.sendto(msg, (UDP_HOST, UDP_PORT))
                last_sent_buttons_mask = buttons_mask

                # Non-blocking: read any perf/shutdown replies from Houdini.
                poll_messages()
                if shutdown_requested:
                    print("\n\nShutdown requested by Houdini; exiting.")
                    break

                # Debug output
                now = time.time()
                if buttons_changed or (now - last_debug_print) >= debug_print_interval:
                    last_debug_print = now
                    perf_str = ""
                    if last_perf:

                        def fmt(v):
                            return "N/A" if v is None else f"{v:.1f}"

                        perf_str = (
                            f" | LAT:{fmt(last_perf.get('latency_last_ms'))}ms"
                            f" P90:{fmt(last_perf.get('latency_p90_ms'))}ms"
                            f" B:{last_perf.get('backlog_steps_last', 'N/A')}"
                            f" Hz:{fmt(last_perf.get('apply_hz'))}"
                        )

                    btn_str = ""
                    if buttons_mask:
                        refresh_hotkeys()
                        mode = hotkey_mode or "network"
                        mode_cfg = {}
                        if isinstance(hotkey_map, dict):
                            mode_cfg = hotkey_map.get(mode, {}) or {}

                        parts = []
                        pressed_buttons = [
                            bit + 1
                            for bit in range(0, 16)
                            if (buttons_mask & (1 << bit))
                        ]
                        for button_number in pressed_buttons[:6]:
                            combo = None
                            if isinstance(mode_cfg, dict):
                                combo = mode_cfg.get(f"button_{button_number}")
                                if combo is None:
                                    combo = mode_cfg.get(str(button_number))
                                if combo is None:
                                    combo = mode_cfg.get(button_number)

                            combo_str = "" if combo is None else str(combo).strip()
                            if combo_str and combo_str.lower() not in (
                                "none",
                                "off",
                                "disabled",
                                "disable",
                                "null",
                            ):
                                parts.append(f"B{button_number}:{combo_str}")
                            else:
                                parts.append(f"B{button_number}")

                        extra = len(pressed_buttons) - min(len(pressed_buttons), 6)
                        extra_str = f" +{extra}" if extra > 0 else ""
                        btn_str = (
                            f" BTN:{int(buttons_mask):04X}{extra_str} "
                            + " ".join(parts)
                        )

                    line = (
                        f"X:{nx:+.2f} Y:{ny:+.2f} Z:{nz:+.2f} | "
                        f"RX:{nrx:+.2f} RY:{nry:+.2f} RZ:{nrz:+.2f}"
                        f"{btn_str}{perf_str}"
                    )
                    # Render a single live-updating status line without wrapping.
                    # If a line wraps in Windows terminals, the cursor moves to
                    # the next row and subsequent '\r' updates start producing
                    # "spam" lines.
                    width = None
                    try:
                        import shutil

                        width = int(shutil.get_terminal_size(fallback=(80, 20)).columns)
                    except Exception:
                        width = None

                    if not width or width < 20:
                        width = 80

                    max_len = max(1, width - 1)
                    if len(line) > max_len:
                        line = line[:max_len]
                    line = line.ljust(max_len)
                    print("\r" + line, end="", flush=True)

            time.sleep(1.0 / POLL_RATE)

    except KeyboardInterrupt:
        print("\n\nStopping...")
    finally:
        for device in devices:
            device.close()
        sock.close()
        print("Done!")

    return True


# ============================================================================
# HOUDINI RECEIVER (run this inside Houdini)
# ============================================================================

_receiver = None


def start_receiver():
    """Start the UDP receiver in Houdini to receive Space Mouse commands."""
    global _receiver

    import hou
    from PySide6 import QtCore, QtNetwork, QtGui, QtWidgets

    # Avoid accidental multiple receivers (can happen after module reloads if the
    # module-level `_receiver` is reset but the old instance is still stored in
    # `hou.session`). Multiple active callbacks can make mode switching feel
    # broken because more than one receiver is applying input.
    existing = getattr(hou.session, "_spacemouse_receiver", None)
    if existing is not None and getattr(existing, "_callback_registered", False):
        _receiver = existing
        try:
            reload_config()
        except Exception:
            pass
        return existing

    class SpaceMouseReceiver(QtCore.QObject):
        """Receives Space Mouse data via UDP and controls Network Editor or 3D Viewport."""

        def __init__(self):
            super().__init__()
            self.socket = QtNetwork.QUdpSocket(self)
            # Bind with ReuseAddressHint to allow rebinding
            result = self.socket.bind(
                QtNetwork.QHostAddress.LocalHost,
                UDP_PORT,
                QtNetwork.QAbstractSocket.BindFlag.ReuseAddressHint,
            )

            # Load settings from config
            self.mode = CONFIG.get(
                "mode", "network"
            )  # 'network', 'viewport', or 'viewport_fps'
            self.pan_speed = CONFIG["network_speed"]["pan"]
            self.zoom_speed = CONFIG["network_speed"]["zoom"]
            self.network_axis_multiplier = dict(
                CONFIG.get(
                    "network_axis_multiplier",
                    {
                        "pan_horizontal": 1.0,
                        "pan_vertical": 1.0,
                        "zoom": 1.0,
                    },
                )
            )
            self.viewport_translate_speed = CONFIG["viewport_speed"]["translate"]
            self.viewport_rotate_speed = CONFIG["viewport_speed"]["rotate"]
            self.axis_mapping = dict(
                CONFIG["axis_mapping"]
            )  # Network mode axis mapping
            self.viewport_axis_mapping = dict(
                CONFIG.get(
                    "viewport_axis_mapping",
                    {
                        "translate_x": "x",
                        "translate_y": "y",
                        "translate_z": "z",
                        "rotate_x": "rx",
                        "rotate_y": "ry",
                        "rotate_z": "rz",
                    },
                )
            )
            self.viewport_axis_multiplier = dict(
                CONFIG.get(
                    "viewport_axis_multiplier",
                    {
                        "translate_x": 1.0,
                        "translate_y": 1.0,
                        "translate_z": 1.0,
                        "rotate_x": 1.0,
                        "rotate_y": 1.0,
                        "rotate_z": 1.0,
                    },
                )
            )
            fps_speed = CONFIG.get("fps_speed", {})
            self.fps_translate_speed = fps_speed.get(
                "translate", self.viewport_translate_speed
            )
            self.fps_rotate_speed = fps_speed.get("rotate", self.viewport_rotate_speed)
            self.fps_axis_mapping = dict(
                CONFIG.get("fps_axis_mapping", self.viewport_axis_mapping)
            )
            self.fps_axis_multiplier = dict(
                CONFIG.get("fps_axis_multiplier", self.viewport_axis_multiplier)
            )
            # Cargo mode settings
            cargo_speed = CONFIG.get("cargo_speed", {})
            self.cargo_rotate_speed = cargo_speed.get("rotate", 5.0)
            self.cargo_axis_mapping = dict(
                CONFIG.get(
                    "cargo_axis_mapping", {"pitch": "rx", "yaw": "-rz", "roll": "-ry"}
                )
            )
            self.cargo_axis_multiplier = dict(
                CONFIG.get(
                    "cargo_axis_multiplier", {"pitch": 1.0, "yaw": 1.0, "roll": 1.0}
                )
            )
            # Cargo attached mode rotation tuning
            self.cargo_attached_rotate_speed = float(
                CONFIG.get("cargo_attached_rotate_speed", self.cargo_rotate_speed)
            )
            self.cargo_attached_axis_mapping = dict(
                CONFIG.get("cargo_attached_axis_mapping", self.cargo_axis_mapping)
            )
            self.cargo_attached_axis_multiplier = dict(
                CONFIG.get("cargo_attached_axis_multiplier", self.cargo_axis_multiplier)
            )
            self._cargo_initial_transform = None  # Stores initial transform for reset
            # Cargo attached mode (camera follow)
            self._cargo_local_rotation = (
                None  # hou.Quaternion, pitch/roll offset (yaw derived from camera)
            )
            self._cargo_held_rotation = None  # legacy alias for attached mode
            self._cargo_held_scale = None  # (sx, sy, sz) captured on grab
            self._cargo_attached_distance = 10.0  # Distance from camera
            self.button_hotkeys = dict(CONFIG.get("button_hotkeys", {}))
            self.auto_mode_switch = dict(
                CONFIG.get(
                    "auto_mode_switch", {"enabled": False, "network_under_cursor": True}
                )
            )
            self.hover_refresh = dict(
                CONFIG.get("hover_refresh", {"enabled": False, "hz": 30})
            )
            self._hover_refresh_last_ns = 0
            self._hover_refresh_enabled = False
            self._hover_refresh_method = "win32"
            self._hover_refresh_jitter_px = 1
            self._hover_refresh_jitter_sign = 1
            self._hover_refresh_interval_ns = int(1_000_000_000 / 30)
            self._sync_hover_refresh_settings()
            self._buttons_prev_mask = 0
            self._button_hold_bindings = {}
            self._key_hold_counts = {}
            self._vk_hold_counts = {}
            self.message_count = 0
            self._last_sender_host = None
            self._last_sender_port = None
            self._shutdown_sent = False

            # Install event filter to catch Home (H) and Frame (F) keys
            try:
                app = QtWidgets.QApplication.instance()
                if app:
                    app.installEventFilter(self)
            except Exception:
                pass

            # Ensure the external reader exits when Houdini closes.
            try:
                app = QtCore.QCoreApplication.instance()
                if app is not None:
                    app.aboutToQuit.connect(self._on_about_to_quit)
            except Exception:
                pass

            # Lightweight performance metrics (printed via debug_receiver()).
            self._perf_latency_count = 0
            self._perf_latency_last_ms = None
            self._perf_latency_min_ms = None
            self._perf_latency_max_ms = None
            self._perf_latency_mean_ms = 0.0
            self._perf_latency_m2 = 0.0
            self._perf_latency_window_ms = deque(maxlen=300)

            self._perf_seq_last = None
            self._perf_seq_skipped = 0

            self._perf_steps_last = 1
            self._perf_steps_sum = 0
            self._perf_steps_count = 0
            self._perf_steps_max = 1

            self._perf_apply_last_ms = None
            self._perf_apply_window_ms = deque(maxlen=300)
            self._perf_apply_interval_last_ms = None
            self._perf_apply_interval_window_ms = deque(maxlen=300)
            self._perf_last_apply_end_ns = None

            # Sender-facing perf replies (for real-time display in the reader
            # terminal). Keep this lightweight and throttled.
            self._perf_reply_last_send_ns = 0
            self._perf_reply_interval_ns = int(1_000_000_000 / 10)  # 10 Hz

            # Higher-frequency polling for viewport mode. Houdini's
            # hou.ui.addEventLoopCallback() tends to tick ~50ms when idle,
            # which can feel sluggish for camera navigation.
            self._qt_timer = QtCore.QTimer(self)
            self._qt_timer.setInterval(16)  # ~60 Hz
            try:
                self._qt_timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
            except Exception:
                pass
            self._qt_timer.timeout.connect(self._poll_data)

            # Use Houdini's event loop callback for reliable polling
            self._callback_registered = False
            if result:
                hou.ui.addEventLoopCallback(self._poll_data)
                self._callback_registered = True
                self._sync_polling()
                print(
                    f"SpaceMouseReceiver: Listening on port {UDP_PORT} (mode: {self.mode})"
                )
            else:
                print(f"SpaceMouseReceiver: Failed to bind to port {UDP_PORT}")

        def eventFilter(self, watched, event):
            """Intercept keys to handle special cases like excluding cargo from Home."""
            if event.type() == QtCore.QEvent.KeyPress:
                key = event.key()
                # Intercept H (Home) and F (Frame) to hide cargo during calculation
                if key == QtCore.Qt.Key_H or key == QtCore.Qt.Key_F:
                    try:
                        # Only affect Scene Viewer
                        pane = hou.ui.paneTabUnderCursor()
                        if pane and pane.type() == hou.paneTabType.SceneViewer:
                            self._hide_cargo_for_home()
                    except Exception:
                        pass
            return False  # Propagate event

        def _hide_cargo_for_home(self):
            """Temporarily hide cargo object so it is ignored by 'Home All'."""
            if self.mode not in ("cargo", "cargo_attached"):
                return

            try:
                cargo = hou.node("/obj/cargo")
                if cargo and cargo.isDisplayFlagSet():
                    cargo.setDisplayFlag(False)
                    # Restore shortly after (enough for Home calculation to complete)
                    # 10ms is usually sufficient as Home runs in the current event loop
                    QtCore.QTimer.singleShot(
                        10, lambda: self._restore_cargo_visibility(cargo)
                    )
            except Exception:
                pass

        def _restore_cargo_visibility(self, cargo):
            try:
                if cargo:
                    cargo.setDisplayFlag(True)
            except Exception:
                pass

        def _sync_polling(self):
            """Start/stop the Qt timer based on current mode."""
            try:
                timer = getattr(self, "_qt_timer", None)
                if timer is None:
                    return

                if self.mode in (
                    "network",
                    "viewport",
                    "viewport_fps",
                    "cargo",
                    "cargo_attached",
                ):
                    if not timer.isActive():
                        timer.start()
                else:
                    if timer.isActive():
                        timer.stop()
            except Exception:
                return

        def _poll_data(self):
            """Poll for UDP data using Houdini's event loop."""
            # Viewport navigation is latency-sensitive. If Houdini's UI event
            # loop runs slower than the sender's poll rate, pending datagrams
            # can build up and make motion feel laggy. We drain the socket and
            # apply only the latest state once, scaling by the number of packets
            # drained to preserve overall speed.
            last_payload = None
            last_recv_ns = None
            last_sender_host = None
            last_sender_port = None
            steps = 0

            while self.socket.hasPendingDatagrams():
                size = self.socket.pendingDatagramSize()
                data, host, port = self.socket.readDatagram(size)
                self.message_count += 1
                last_payload = data.data()
                last_recv_ns = time.time_ns()
                last_sender_host = host
                last_sender_port = port
                steps += 1

            if not last_payload:
                # Cargo attached mode needs to update even when no SpaceMouse packets
                # arrive, so the box keeps following the camera (and yaw-locking if enabled).
                if getattr(self, "mode", None) == "cargo_attached":
                    try:
                        self._apply_cargo_attached({}, steps=1)
                    except Exception:
                        pass
                return

            if last_sender_host is not None and last_sender_port is not None:
                self._last_sender_host = last_sender_host
                try:
                    self._last_sender_port = int(last_sender_port)
                except Exception:
                    self._last_sender_port = last_sender_port

            try:
                msg = json.loads(last_payload.decode())
                if isinstance(msg, dict) and msg.get("type") == "hello":
                    # Handshake packet from the reader so we learn its host/port even
                    # before any motion happens. Don't apply to the viewport.
                    try:
                        payload = json.dumps(
                            {
                                "type": "hello_reply",
                                "houdini_pid": os.getpid(),
                                "t_recv_ns": last_recv_ns,
                            },
                            separators=(",", ":"),
                        ).encode("utf-8")
                        self.socket.writeDatagram(
                            payload, last_sender_host, int(last_sender_port)
                        )
                    except Exception:
                        pass
                    self._maybe_send_perf_reply(last_sender_host, last_sender_port)
                    return
                self._apply_input(msg, steps=steps, recv_ns=last_recv_ns)
                self._maybe_send_perf_reply(last_sender_host, last_sender_port)
            except Exception as e:
                # Store last error for debugging (shown in Debug Info shelf tool).
                self._last_error = f"{type(e).__name__}: {e}"

        def _get_axis_value(self, msg, axis_name):
            """Get value from message based on axis mapping (supports inversion with -)."""
            axis = self.axis_mapping.get(axis_name, "none")
            if axis == "none":
                return 0

            invert = axis.startswith("-")
            if invert:
                axis = axis[1:]

            value = msg.get(axis, 0)
            return -value if invert else value

        def _qt_key_from_token(self, token):
            if not token:
                return None
            name = str(token).strip()
            if not name:
                return None

            upper = name.upper().replace(" ", "")
            if name in ("`", "~") or upper in (
                "GRAVE",
                "BACKTICK",
                "QUOTELEFT",
                "TILDE",
            ):
                return getattr(QtCore.Qt, "Key_QuoteLeft", None)
            if len(upper) == 1:
                ch = upper
                if "A" <= ch <= "Z" or "0" <= ch <= "9":
                    return getattr(QtCore.Qt, f"Key_{ch}", None)

            if upper.startswith("F") and upper[1:].isdigit():
                n = int(upper[1:])
                return getattr(QtCore.Qt, f"Key_F{n}", None)

            named = {
                "SPACE": QtCore.Qt.Key_Space,
                "TAB": QtCore.Qt.Key_Tab,
                "ENTER": QtCore.Qt.Key_Return,
                "RETURN": QtCore.Qt.Key_Return,
                "ESC": QtCore.Qt.Key_Escape,
                "ESCAPE": QtCore.Qt.Key_Escape,
                "BACKSPACE": QtCore.Qt.Key_Backspace,
                "DEL": QtCore.Qt.Key_Delete,
                "DELETE": QtCore.Qt.Key_Delete,
                "INSERT": QtCore.Qt.Key_Insert,
                "HOME": QtCore.Qt.Key_Home,
                "END": QtCore.Qt.Key_End,
                "PAGEUP": QtCore.Qt.Key_PageUp,
                "PAGEDOWN": QtCore.Qt.Key_PageDown,
                "UP": QtCore.Qt.Key_Up,
                "DOWN": QtCore.Qt.Key_Down,
                "LEFT": QtCore.Qt.Key_Left,
                "RIGHT": QtCore.Qt.Key_Right,
                "PLUS": QtCore.Qt.Key_Plus,
                "MINUS": QtCore.Qt.Key_Minus,
                "EQUAL": QtCore.Qt.Key_Equal,
            }
            return named.get(upper)

        def _vk_from_token(self, token):
            if token is None:
                return None
            name = str(token).strip()
            if not name:
                return None

            upper = name.upper().replace(" ", "")

            # Single-char alpha/num
            if len(upper) == 1:
                ch = upper
                if "A" <= ch <= "Z":
                    return ord(ch)
                if "0" <= ch <= "9":
                    return ord(ch)

            # Function keys
            if upper.startswith("F") and upper[1:].isdigit():
                n = int(upper[1:])
                if 1 <= n <= 24:
                    return 0x70 + (n - 1)  # VK_F1...

            # Common named keys
            named = {
                "SPACE": 0x20,  # VK_SPACE
                "TAB": 0x09,  # VK_TAB
                "ENTER": 0x0D,  # VK_RETURN
                "RETURN": 0x0D,
                "ESC": 0x1B,  # VK_ESCAPE
                "ESCAPE": 0x1B,
                "BACKSPACE": 0x08,  # VK_BACK
                "DEL": 0x2E,  # VK_DELETE
                "DELETE": 0x2E,
                "INSERT": 0x2D,  # VK_INSERT
                "HOME": 0x24,  # VK_HOME
                "END": 0x23,  # VK_END
                "PAGEUP": 0x21,  # VK_PRIOR
                "PAGEDOWN": 0x22,  # VK_NEXT
                "UP": 0x26,  # VK_UP
                "DOWN": 0x28,  # VK_DOWN
                "LEFT": 0x25,  # VK_LEFT
                "RIGHT": 0x27,  # VK_RIGHT
            }
            if upper in named:
                return named[upper]

            # Punctuation (US keyboard OEM virtual keys)
            if name in ("`", "~") or upper in (
                "GRAVE",
                "BACKTICK",
                "QUOTELEFT",
                "TILDE",
            ):
                return 0xC0  # VK_OEM_3
            if name in ("-", "_") or upper in ("MINUS", "DASH", "HYPHEN"):
                return 0xBD  # VK_OEM_MINUS
            if name in ("=", "+") or upper in ("EQUAL", "EQUALS", "PLUS"):
                return 0xBB  # VK_OEM_PLUS
            if name in ("[", "{"):
                return 0xDB  # VK_OEM_4
            if name in ("]", "}"):
                return 0xDD  # VK_OEM_6
            if name in ("\\", "|"):
                return 0xDC  # VK_OEM_5
            if name in (";", ":"):
                return 0xBA  # VK_OEM_1
            if name in ("'", '"'):
                return 0xDE  # VK_OEM_7
            if name in (",", "<"):
                return 0xBC  # VK_OEM_COMMA
            if name in (".", ">"):
                return 0xBE  # VK_OEM_PERIOD
            if name in ("/", "?"):
                return 0xBF  # VK_OEM_2

            return None

        def _parse_hotkey_combo(self, combo):
            if combo is None:
                return None, None
            if not isinstance(combo, str):
                return None, None
            combo = combo.strip()
            if not combo:
                return None, None

            lowered = combo.lower()
            if lowered in ("none", "off", "disabled", "disable", "null"):
                return None, None

            modifiers = QtCore.Qt.NoModifier
            key = None
            last_modifier_key = None

            parts = [p.strip() for p in combo.split("+") if p.strip()]
            for part in parts:
                pl = part.strip().lower()
                if pl in ("ctrl", "control"):
                    modifiers |= QtCore.Qt.ControlModifier
                    last_modifier_key = QtCore.Qt.Key_Control
                    continue
                if pl == "shift":
                    modifiers |= QtCore.Qt.ShiftModifier
                    last_modifier_key = QtCore.Qt.Key_Shift
                    continue
                if pl in ("alt", "option"):
                    modifiers |= QtCore.Qt.AltModifier
                    last_modifier_key = QtCore.Qt.Key_Alt
                    continue
                if pl in ("meta", "cmd", "command", "win", "windows", "super"):
                    modifiers |= QtCore.Qt.MetaModifier
                    last_modifier_key = QtCore.Qt.Key_Meta
                    continue

                key = self._qt_key_from_token(part)

            if key is None:
                key = last_modifier_key

            return key, modifiers

        def _trigger_hotkey(self, combo):
            key, modifiers = self._parse_hotkey_combo(combo)
            if key is None:
                return

            try:
                app = QtWidgets.QApplication.instance()
                if app is None:
                    return
                target = app.focusWidget() or app.activeWindow()
                if target is None:
                    try:
                        target = hou.ui.mainQtWindow()
                    except Exception:
                        target = None
                if target is None:
                    return

                press_event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, modifiers)
                release_event = QtGui.QKeyEvent(
                    QtCore.QEvent.KeyRelease, key, modifiers
                )
                QtWidgets.QApplication.postEvent(target, press_event)
                QtWidgets.QApplication.postEvent(target, release_event)
            except Exception as e:
                self._last_error = f"HotkeyError: {type(e).__name__}: {e}"

        def _modifier_flag_for_key(self, key):
            if key == QtCore.Qt.Key_Control:
                return QtCore.Qt.ControlModifier
            if key == QtCore.Qt.Key_Shift:
                return QtCore.Qt.ShiftModifier
            if key == QtCore.Qt.Key_Alt:
                return QtCore.Qt.AltModifier
            if key == QtCore.Qt.Key_Meta:
                return QtCore.Qt.MetaModifier
            return QtCore.Qt.NoModifier

        def _hotkey_target(self):
            app = QtWidgets.QApplication.instance()
            if app is None:
                return None
            # Prefer the currently focused widget so Houdini's shortcut system
            # sees the key events even when the external reader console is the
            # foreground OS window.
            target = app.focusWidget() or app.activeWindow()
            if target is not None:
                return target
            try:
                target = hou.ui.mainQtWindow()
                if target is not None:
                    return target
            except Exception:
                pass
            return None

        def _key_text_for_event(self, key, modifiers):
            try:
                if key == QtCore.Qt.Key_Space:
                    return " "
                if key == getattr(QtCore.Qt, "Key_QuoteLeft", None):
                    return "~" if (modifiers & QtCore.Qt.ShiftModifier) else "`"
                if QtCore.Qt.Key_A <= key <= QtCore.Qt.Key_Z:
                    ch = chr(ord("A") + (int(key) - int(QtCore.Qt.Key_A)))
                    if modifiers & QtCore.Qt.ShiftModifier:
                        return ch
                    return ch.lower()
                if QtCore.Qt.Key_0 <= key <= QtCore.Qt.Key_9:
                    return chr(ord("0") + (int(key) - int(QtCore.Qt.Key_0)))
            except Exception:
                return ""
            return ""

        def _post_key_event(self, target, event_type, key, modifiers):
            if target is None or key is None:
                return
            try:
                text = self._key_text_for_event(key, modifiers)
                event = QtGui.QKeyEvent(event_type, int(key), modifiers, text)
                try:
                    QtWidgets.QApplication.sendEvent(target, event)
                except Exception:
                    QtWidgets.QApplication.postEvent(target, event)
            except Exception as e:
                self._last_error = f"HotkeyError: {type(e).__name__}: {e}"

        def _current_injected_modifiers(self):
            mods = QtCore.Qt.NoModifier
            for mod_key in (
                QtCore.Qt.Key_Control,
                QtCore.Qt.Key_Shift,
                QtCore.Qt.Key_Alt,
                QtCore.Qt.Key_Meta,
            ):
                if getattr(self, "_key_hold_counts", {}).get(mod_key, 0) > 0:
                    mods |= self._modifier_flag_for_key(mod_key)
            return mods

        def _ensure_houdini_foreground(self):
            """Best-effort bring Houdini to the foreground (Windows only).

            This is used for OS-level SendInput hotkey injection so key events
            don't accidentally go to the reader console when it's focused.
            """
            if os.name != "nt":
                return False
            try:
                import ctypes
                from ctypes import wintypes
            except Exception:
                return False

            try:
                hwnd = None
                try:
                    wnd = hou.ui.mainQtWindow()
                    if wnd is not None:
                        hwnd = int(wnd.winId())
                except Exception:
                    hwnd = None

                if not hwnd:
                    return False

                user32 = ctypes.windll.user32
                try:
                    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
                    user32.SetForegroundWindow.restype = wintypes.BOOL
                except Exception:
                    pass
                try:
                    user32.GetForegroundWindow.restype = wintypes.HWND
                except Exception:
                    pass

                try:
                    fg = user32.GetForegroundWindow()
                    if fg and int(fg) == int(hwnd):
                        return True
                except Exception:
                    pass

                user32.SetForegroundWindow(hwnd)
                return True
            except Exception:
                return False

        def _hold_key_down(self, key, modifiers):
            if key is None:
                return
            if not hasattr(self, "_key_hold_counts"):
                self._key_hold_counts = {}

            count = self._key_hold_counts.get(key, 0)
            self._key_hold_counts[key] = count + 1
            if count > 0:
                return

            self._post_key_event(
                self._hotkey_target(), QtCore.QEvent.KeyPress, key, modifiers
            )

        def _hold_key_up(self, key):
            if key is None or not hasattr(self, "_key_hold_counts"):
                return

            count = self._key_hold_counts.get(key, 0)
            if count <= 0:
                return

            if count == 1:
                # Use current modifier state (before release).
                mods = self._current_injected_modifiers()
                self._post_key_event(
                    self._hotkey_target(), QtCore.QEvent.KeyRelease, key, mods
                )
                self._key_hold_counts.pop(key, None)
            else:
                self._key_hold_counts[key] = count - 1

        def _win32_send_key(self, vk, is_down):
            if vk is None or os.name != "nt":
                return False
            try:
                import ctypes
                from ctypes import wintypes
            except Exception:
                return False

            try:
                if not hasattr(self, "_win32_sendinput_init"):
                    self._win32_sendinput_init = True

                    INPUT_KEYBOARD = 1
                    KEYEVENTF_EXTENDEDKEY = 0x0001
                    KEYEVENTF_KEYUP = 0x0002
                    KEYEVENTF_SCANCODE = 0x0008
                    ULONG_PTR = getattr(wintypes, "ULONG_PTR", ctypes.c_size_t)

                    class KEYBDINPUT(ctypes.Structure):
                        _fields_ = [
                            ("wVk", wintypes.WORD),
                            ("wScan", wintypes.WORD),
                            ("dwFlags", wintypes.DWORD),
                            ("time", wintypes.DWORD),
                            ("dwExtraInfo", ULONG_PTR),
                        ]

                    class _INPUT_UNION(ctypes.Union):
                        _fields_ = [("ki", KEYBDINPUT)]

                    class INPUT(ctypes.Structure):
                        _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]

                    self._win32_INPUT_KEYBOARD = INPUT_KEYBOARD
                    self._win32_KEYEVENTF_EXTENDEDKEY = KEYEVENTF_EXTENDEDKEY
                    self._win32_KEYEVENTF_KEYUP = KEYEVENTF_KEYUP
                    self._win32_KEYEVENTF_SCANCODE = KEYEVENTF_SCANCODE
                    self._win32_INPUT = INPUT
                    self._win32_KEYBDINPUT = KEYBDINPUT
                    self._win32_INPUT_UNION = _INPUT_UNION

                    user32 = ctypes.windll.user32
                    self._win32_SendInput = user32.SendInput
                    self._win32_MapVirtualKeyW = user32.MapVirtualKeyW
                    try:
                        self._win32_SendInput.argtypes = (
                            wintypes.UINT,
                            ctypes.POINTER(INPUT),
                            ctypes.c_int,
                        )
                        self._win32_SendInput.restype = wintypes.UINT
                    except Exception:
                        pass
                    try:
                        self._win32_MapVirtualKeyW.argtypes = (
                            wintypes.UINT,
                            wintypes.UINT,
                        )
                        self._win32_MapVirtualKeyW.restype = wintypes.UINT
                    except Exception:
                        pass
            except Exception as e:
                self._last_error = f"HotkeyError(win32-init): {type(e).__name__}: {e}"
                return False

            try:
                scan = int(self._win32_MapVirtualKeyW(int(vk), 0)) & 0xFFFF
                flags = int(self._win32_KEYEVENTF_SCANCODE)
                if not is_down:
                    flags |= int(self._win32_KEYEVENTF_KEYUP)

                # Extended keys (arrows, nav cluster)
                if int(vk) in (
                    0x21,
                    0x22,
                    0x23,
                    0x24,
                    0x25,
                    0x26,
                    0x27,
                    0x28,
                    0x2D,
                    0x2E,
                ):
                    flags |= int(self._win32_KEYEVENTF_EXTENDEDKEY)

                ki = self._win32_KEYBDINPUT(0, scan, flags, 0, 0)
                inp = self._win32_INPUT(
                    self._win32_INPUT_KEYBOARD, self._win32_INPUT_UNION(ki=ki)
                )
                sent = int(
                    self._win32_SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
                )
                return sent == 1
            except Exception as e:
                self._last_error = f"HotkeyError(win32): {type(e).__name__}: {e}"
                return False

        def _hold_vk_down(self, vk):
            if vk is None:
                return
            if not hasattr(self, "_vk_hold_counts"):
                self._vk_hold_counts = {}

            count = self._vk_hold_counts.get(vk, 0)
            self._vk_hold_counts[vk] = count + 1
            if count > 0:
                return
            self._win32_send_key(vk, True)

        def _hold_vk_up(self, vk):
            if vk is None or not hasattr(self, "_vk_hold_counts"):
                return

            count = self._vk_hold_counts.get(vk, 0)
            if count <= 0:
                return

            if count == 1:
                self._win32_send_key(vk, False)
                self._vk_hold_counts.pop(vk, None)
            else:
                self._vk_hold_counts[vk] = count - 1

        def _parse_hotkey_combo_hold(self, combo):
            if combo is None:
                return None
            if not isinstance(combo, str):
                return None
            combo = combo.strip()
            if not combo:
                return None

            lowered = combo.lower()
            if lowered in ("none", "off", "disabled", "disable", "null"):
                return None

            mods = []
            main_key = None
            main_vk = None
            parts = [p.strip() for p in combo.split("+") if p.strip()]
            for part in parts:
                pl = part.strip().lower()
                if pl in ("ctrl", "control"):
                    mods.append((QtCore.Qt.Key_Control, 0x11))  # VK_CONTROL
                    continue
                if pl == "shift":
                    mods.append((QtCore.Qt.Key_Shift, 0x10))  # VK_SHIFT
                    continue
                if pl in ("alt", "option"):
                    mods.append((QtCore.Qt.Key_Alt, 0x12))  # VK_MENU
                    continue
                if pl in ("meta", "cmd", "command", "win", "windows", "super"):
                    mods.append((QtCore.Qt.Key_Meta, 0x5B))  # VK_LWIN
                    continue

                main_key = self._qt_key_from_token(part)
                main_vk = self._vk_from_token(part)

            # De-dupe modifiers while preserving stable order.
            seen = set()
            mods = [m for m in mods if not (m[0] in seen or seen.add(m[0]))]

            if main_key is None and main_vk is None and not mods:
                return None

            # Canonical modifier order for key events.
            order = {
                QtCore.Qt.Key_Control: 0,
                QtCore.Qt.Key_Shift: 1,
                QtCore.Qt.Key_Alt: 2,
                QtCore.Qt.Key_Meta: 3,
            }
            mods = sorted(mods, key=lambda m: order.get(m[0], 99))
            mod_keys = [m[0] for m in mods]
            mod_vks = [m[1] for m in mods]

            return {
                "combo": combo,
                "mod_keys": mod_keys,
                "main_key": main_key,
                "mod_vks": mod_vks,
                "main_vk": main_vk,
            }

        def _combo_for_button(self, mode_map, button_number):
            if not isinstance(mode_map, dict):
                return None
            for key_name in (
                f"button_{button_number}",
                str(button_number),
                button_number,
            ):
                if key_name in mode_map:
                    return mode_map.get(key_name)
            return None

        def _apply_button_hotkeys(self, msg, mode_override=None):
            mask = msg.get("buttons")
            if not isinstance(mask, int):
                return

            prev = getattr(self, "_buttons_prev_mask", 0)
            pressed = mask & (~prev)
            released = prev & (~mask)
            self._buttons_prev_mask = mask

            if not pressed and not released:
                return

            cfg = getattr(self, "button_hotkeys", {}) or {}
            mode = (
                mode_override
                if mode_override is not None
                else getattr(self, "mode", "network")
            )
            mode_map = cfg.get(mode, {}) or {}
            if not isinstance(mode_map, dict):
                mode_map = {}

            # Button down: press and HOLD mapped keys until release.
            for bit in range(0, 32):
                if not (pressed & (1 << bit)):
                    continue
                button_number = bit + 1

                # If the button was already tracked (stale state), release first.
                existing = getattr(self, "_button_hold_bindings", {}).pop(
                    button_number, None
                )
                if existing:
                    try:
                        inject = existing.get("_inject", "qt")
                        if inject == "win32":
                            if existing.get("main_vk") is not None:
                                self._hold_vk_up(existing.get("main_vk"))
                            for vk in reversed(existing.get("mod_vks", [])):
                                self._hold_vk_up(vk)
                        else:
                            if existing.get("main_key") is not None:
                                self._hold_key_up(existing.get("main_key"))
                            for mod_key in reversed(existing.get("mod_keys", [])):
                                self._hold_key_up(mod_key)
                    except Exception:
                        pass

                combo = self._combo_for_button(mode_map, button_number)

                # Special action: reset_rotation
                if combo == "reset_rotation":
                    if mode == "cargo_attached":
                        self._cargo_held_rotation = None
                        self._cargo_local_rotation = None
                    continue

                binding = self._parse_hotkey_combo_hold(combo)
                if not binding:
                    continue

                main_key = binding.get("main_key")
                main_vk = binding.get("main_vk")
                mod_vks = binding.get("mod_vks", [])

                # For true "hold" behavior (especially modifiers that must stay
                # active while the cursor/mouse moves), prefer OS-level key state
                # via SendInput when available.
                can_win32 = (
                    os.name == "nt"
                    and (bool(mod_vks) or main_vk is not None)
                    and (main_key is None or main_vk is not None)
                )
                if can_win32:
                    binding["_inject"] = "win32"
                    # Try to ensure Houdini is the foreground window so the key
                    # goes to Houdini (not the reader console).
                    self._ensure_houdini_foreground()
                    for vk in mod_vks:
                        self._hold_vk_down(vk)
                    if main_vk is not None:
                        self._hold_vk_down(main_vk)
                else:
                    # Fallback: inject Qt key events (works for many shortcuts,
                    # but may not behave like a real "hold" for modifiers).
                    binding["_inject"] = "qt"
                    mods_now = self._current_injected_modifiers()
                    for mod_key in binding["mod_keys"]:
                        mods_now |= self._modifier_flag_for_key(mod_key)
                        self._hold_key_down(mod_key, mods_now)

                    if main_key is not None:
                        self._hold_key_down(main_key, mods_now)

                getattr(self, "_button_hold_bindings", {})[button_number] = binding

            # Button up: release held keys.
            for bit in range(0, 32):
                if not (released & (1 << bit)):
                    continue
                button_number = bit + 1
                binding = getattr(self, "_button_hold_bindings", {}).pop(
                    button_number, None
                )
                if not binding:
                    continue
                try:
                    inject = binding.get("_inject", "qt")
                    if inject == "win32":
                        if binding.get("main_vk") is not None:
                            self._hold_vk_up(binding.get("main_vk"))
                        for vk in reversed(binding.get("mod_vks", [])):
                            self._hold_vk_up(vk)
                    else:
                        if binding.get("main_key") is not None:
                            self._hold_key_up(binding.get("main_key"))
                        for mod_key in reversed(binding.get("mod_keys", [])):
                            self._hold_key_up(mod_key)
                except Exception:
                    pass

        def _apply_input(self, msg, steps=1, recv_ns=None):
            """Apply Space Mouse input based on current mode."""
            if recv_ns is not None:
                self._update_perf_metrics(msg, recv_ns=recv_ns, steps=steps)

            effective_mode = self.mode
            network_editor = None
            try:
                cfg = getattr(self, "auto_mode_switch", {}) or {}
                if cfg.get("enabled", False) and cfg.get("network_under_cursor", True):
                    pane = hou.ui.paneTabUnderCursor()
                    if (
                        pane is not None
                        and pane.type() == hou.paneTabType.NetworkEditor
                    ):
                        effective_mode = "network"
                        network_editor = pane
            except Exception:
                pass

            self._apply_button_hotkeys(msg, mode_override=effective_mode)

            apply_start_ns = time.time_ns()
            if effective_mode == "viewport_fps":
                self._apply_viewport_fps_input(msg, steps=steps)
            elif effective_mode == "viewport":
                self._apply_viewport_input(msg, steps=steps)
            elif effective_mode == "cargo":
                self._apply_cargo_rotation(msg, steps=steps)
            elif effective_mode == "cargo_attached":
                self._apply_cargo_attached(msg, steps=steps)
            else:
                self._apply_network_input(msg, steps=steps, editor=network_editor)
            apply_end_ns = time.time_ns()
            self._update_perf_apply(apply_start_ns, apply_end_ns)

        def _sync_hover_refresh_settings(self):
            cfg = getattr(self, "hover_refresh", None)
            if not isinstance(cfg, dict):
                cfg = {}

            self._hover_refresh_enabled = bool(cfg.get("enabled", False))

            hz = cfg.get("hz", 30)
            try:
                hz = float(hz)
            except Exception:
                hz = 30.0
            if hz <= 0:
                hz = 30.0
            self._hover_refresh_interval_ns = int(1_000_000_000 / hz)

            method = cfg.get("method", "win32")
            if not isinstance(method, str):
                method = "win32"
            method = method.strip().lower()
            if method not in ("win32", "qt", "cursor"):
                method = "win32"
            self._hover_refresh_method = method

            jitter = cfg.get("jitter_px", 1)
            try:
                jitter = int(jitter)
            except Exception:
                jitter = 1
            if jitter < 0:
                jitter = -jitter
            if jitter > 4:
                jitter = 4
            self._hover_refresh_jitter_px = jitter

        def _maybe_refresh_hover_preselect(self):
            """Best-effort refresh for hover preselect highlight in Scene Viewer."""
            if not getattr(self, "_hover_refresh_enabled", False):
                return

            try:
                now_ns = time.time_ns()
                last_ns = getattr(self, "_hover_refresh_last_ns", 0) or 0
                if (now_ns - last_ns) < getattr(self, "_hover_refresh_interval_ns", 0):
                    return
                self._hover_refresh_last_ns = now_ns
            except Exception:
                return

            try:
                under_cursor = hou.ui.paneTabUnderCursor()
                if (
                    under_cursor is None
                    or under_cursor.type() != hou.paneTabType.SceneViewer
                ):
                    return
            except Exception:
                # If we can't query it, still try the Qt injection.
                pass

            app = QtWidgets.QApplication.instance()
            if app is None:
                return

            try:
                button_mask = QtWidgets.QApplication.mouseButtons()
                no_button = getattr(QtCore.Qt, "NoButton", None)
                if no_button is None:
                    no_button = QtCore.Qt.MouseButton.NoButton
                try:
                    if int(button_mask) != 0:
                        return
                except Exception:
                    if button_mask and button_mask != no_button:
                        return

                global_pos = QtGui.QCursor.pos()
                root = None
                try:
                    pane = hou.ui.paneTabUnderCursor()
                    if pane is not None:
                        root = pane.qtWindow()
                except Exception:
                    root = None

                if root is not None:
                    root_pos = root.mapFromGlobal(global_pos)
                    target = root.childAt(root_pos) or root
                else:
                    target = app.widgetAt(global_pos)
                    if target is None:
                        return

                local_pos = target.mapFromGlobal(global_pos)

                jitter_px = int(getattr(self, "_hover_refresh_jitter_px", 1) or 1)
                jitter_sign = int(getattr(self, "_hover_refresh_jitter_sign", 1) or 1)
                self._hover_refresh_jitter_sign = -jitter_sign
                jitter = jitter_sign * max(1, jitter_px)

                method = getattr(self, "_hover_refresh_method", "qt")
                mods = QtWidgets.QApplication.keyboardModifiers()

                if method == "cursor":
                    try:
                        QtGui.QCursor.setPos(global_pos + QtCore.QPoint(jitter, 0))
                        QtGui.QCursor.setPos(global_pos)
                    except Exception as e:
                        self._last_error = (
                            f"HoverRefreshError(cursor): {type(e).__name__}: {e}"
                        )
                    return

                if method == "win32":
                    if os.name != "nt":
                        return
                    try:
                        import ctypes
                        from ctypes import wintypes

                        hwnd_widget = (
                            root
                            if root is not None
                            else (target.window() if target is not None else target)
                        )
                        if hwnd_widget is None:
                            return
                        hwnd = int(hwnd_widget.winId())

                        hwnd_pos = hwnd_widget.mapFromGlobal(global_pos)
                        hwnd_pos2 = hwnd_pos + QtCore.QPoint(jitter, 0)

                        def lparam_from_point(pt):
                            x = int(pt.x()) & 0xFFFF
                            y = int(pt.y()) & 0xFFFF
                            return (y << 16) | x

                        WM_MOUSEMOVE = 0x0200
                        MK_SHIFT = 0x0004
                        MK_CONTROL = 0x0008

                        user32 = ctypes.windll.user32

                        wparam = 0
                        # Prefer actual OS key state so hover-refresh mouse move
                        # messages remain consistent with SendInput-injected
                        # button hotkeys (modifiers in particular).
                        try:
                            VK_SHIFT = 0x10
                            VK_CONTROL = 0x11
                            if int(user32.GetAsyncKeyState(VK_SHIFT)) & 0x8000:
                                wparam |= MK_SHIFT
                            if int(user32.GetAsyncKeyState(VK_CONTROL)) & 0x8000:
                                wparam |= MK_CONTROL
                        except Exception:
                            if mods & QtCore.Qt.ShiftModifier:
                                wparam |= MK_SHIFT
                            if mods & QtCore.Qt.ControlModifier:
                                wparam |= MK_CONTROL

                        user32.PostMessageW.argtypes = [
                            wintypes.HWND,
                            wintypes.UINT,
                            wintypes.WPARAM,
                            wintypes.LPARAM,
                        ]
                        user32.PostMessageW.restype = wintypes.BOOL

                        user32.PostMessageW(
                            hwnd, WM_MOUSEMOVE, wparam, lparam_from_point(hwnd_pos2)
                        )
                        user32.PostMessageW(
                            hwnd, WM_MOUSEMOVE, wparam, lparam_from_point(hwnd_pos)
                        )
                    except Exception as e:
                        self._last_error = (
                            f"HoverRefreshError(win32): {type(e).__name__}: {e}"
                        )
                    return

                local_pos2 = local_pos + QtCore.QPoint(jitter, 0)
                global_pos2 = global_pos + QtCore.QPoint(jitter, 0)

                try:
                    event_a = QtGui.QMouseEvent(
                        QtCore.QEvent.Type.MouseMove,
                        QtCore.QPointF(local_pos2),
                        QtCore.QPointF(local_pos2),
                        QtCore.QPointF(global_pos2),
                        no_button,
                        button_mask,
                        mods,
                    )
                    event_b = QtGui.QMouseEvent(
                        QtCore.QEvent.Type.MouseMove,
                        QtCore.QPointF(local_pos),
                        QtCore.QPointF(local_pos),
                        QtCore.QPointF(global_pos),
                        no_button,
                        button_mask,
                        mods,
                    )
                except Exception:
                    # Qt5-style signature fallback.
                    event_a = QtGui.QMouseEvent(
                        QtCore.QEvent.MouseMove,
                        local_pos2,
                        global_pos2,
                        no_button,
                        button_mask,
                        mods,
                    )
                    event_b = QtGui.QMouseEvent(
                        QtCore.QEvent.MouseMove,
                        local_pos,
                        global_pos,
                        no_button,
                        button_mask,
                        mods,
                    )

                try:
                    QtWidgets.QApplication.sendEvent(target, event_a)
                    QtWidgets.QApplication.sendEvent(target, event_b)
                except Exception:
                    QtWidgets.QApplication.postEvent(target, event_a)
                    QtWidgets.QApplication.postEvent(target, event_b)

            except Exception as e:
                self._last_error = f"HoverRefreshError: {type(e).__name__}: {e}"
                return

        def _update_perf_metrics(self, msg, recv_ns=None, steps=1):
            # Packet backlog (how many datagrams were drained for this UI tick)
            self._perf_steps_last = steps
            self._perf_steps_sum += steps
            self._perf_steps_count += 1
            if steps > self._perf_steps_max:
                self._perf_steps_max = steps

            # Sender sequence numbers (helps distinguish real packet loss from
            # deliberate skipping when we drain/backlog).
            seq = msg.get("seq")
            if isinstance(seq, int):
                if self._perf_seq_last is not None:
                    gap = seq - self._perf_seq_last
                    if gap > 1:
                        self._perf_seq_skipped += gap - 1
                self._perf_seq_last = seq

            # One-way latency (sender wall-clock -> receiver wall-clock)
            t_send_ns = msg.get("t_send_ns")
            if recv_ns is None or not isinstance(t_send_ns, int):
                return

            latency_ms = (recv_ns - t_send_ns) / 1_000_000.0
            # Guard against clock adjustments
            if latency_ms < -100.0 or latency_ms > 10_000.0:
                return

            self._perf_latency_last_ms = latency_ms
            self._perf_latency_window_ms.append(latency_ms)
            self._perf_latency_count += 1

            if (
                self._perf_latency_min_ms is None
                or latency_ms < self._perf_latency_min_ms
            ):
                self._perf_latency_min_ms = latency_ms
            if (
                self._perf_latency_max_ms is None
                or latency_ms > self._perf_latency_max_ms
            ):
                self._perf_latency_max_ms = latency_ms

            # Welford online mean/variance
            delta = latency_ms - self._perf_latency_mean_ms
            self._perf_latency_mean_ms += delta / self._perf_latency_count
            delta2 = latency_ms - self._perf_latency_mean_ms
            self._perf_latency_m2 += delta * delta2

        def _update_perf_apply(self, apply_start_ns, apply_end_ns):
            apply_ms = (apply_end_ns - apply_start_ns) / 1_000_000.0
            self._perf_apply_last_ms = apply_ms
            self._perf_apply_window_ms.append(apply_ms)

            if self._perf_last_apply_end_ns is not None:
                interval_ms = (
                    apply_end_ns - self._perf_last_apply_end_ns
                ) / 1_000_000.0
                self._perf_apply_interval_last_ms = interval_ms
                self._perf_apply_interval_window_ms.append(interval_ms)
            self._perf_last_apply_end_ns = apply_end_ns

        def _maybe_send_perf_reply(self, host, port):
            """Send throttled perf stats back to the sender (reader)."""
            if host is None or port is None:
                return

            now_ns = time.time_ns()
            if (now_ns - self._perf_reply_last_send_ns) < self._perf_reply_interval_ns:
                return
            self._perf_reply_last_send_ns = now_ns

            try:
                window = list(self._perf_latency_window_ms)
                window.sort()

                def pct(p):
                    if not window:
                        return None
                    return window[int(p * (len(window) - 1))]

                p50 = pct(0.50)
                p90 = pct(0.90)
                p99 = pct(0.99)

                interval_window = list(self._perf_apply_interval_window_ms)
                interval_avg = (
                    (sum(interval_window) / len(interval_window))
                    if interval_window
                    else None
                )
                apply_hz = (
                    (1000.0 / interval_avg)
                    if interval_avg and interval_avg > 0
                    else None
                )

                perf = {
                    "type": "perf",
                    "t_recv_ns": now_ns,
                    "latency_last_ms": self._perf_latency_last_ms,
                    "latency_p50_ms": p50,
                    "latency_p90_ms": p90,
                    "latency_p99_ms": p99,
                    "backlog_steps_last": self._perf_steps_last,
                    "backlog_steps_max": self._perf_steps_max,
                    "skipped_seq": self._perf_seq_skipped,
                    "apply_last_ms": self._perf_apply_last_ms,
                    "apply_interval_last_ms": self._perf_apply_interval_last_ms,
                    "apply_hz": apply_hz,
                }
                payload = json.dumps(perf, separators=(",", ":")).encode("utf-8")
                self.socket.writeDatagram(payload, host, int(port))
            except Exception:
                return

        def _apply_viewport_input(self, msg, steps=1):
            """Apply Space Mouse input to 3D Viewport camera in camera-local space."""
            # Find a Scene Viewer pane
            viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
            if not viewer:
                return

            # Get the current viewport
            viewport = viewer.curViewport()
            if not viewport:
                return

            # Helper to get axis value with inversion support
            def get_viewport_axis(axis_name):
                axis = self.viewport_axis_mapping.get(axis_name, "none")
                if axis == "none":
                    return 0
                invert = axis.startswith("-")
                if invert:
                    axis = axis[1:]
                value = msg.get(axis, 0)
                return -value if invert else value

            # Get axis values based on viewport axis mapping
            tx = get_viewport_axis("translate_x")
            ty = get_viewport_axis("translate_y")
            tz = get_viewport_axis("translate_z")
            rx = get_viewport_axis("rotate_x")
            ry = get_viewport_axis("rotate_y")
            rz = get_viewport_axis("rotate_z")

            moving = any(abs(v) > 1e-6 for v in (tx, ty, tz, rx, ry, rz))

            # Get current camera state
            cam = viewport.defaultCamera()
            current_trans = cam.translation()
            current_rot = cam.rotation()  # This is a Matrix3

            # Calculate speed factors
            translate_speed = self.viewport_translate_speed * 0.1
            rotate_speed = self.viewport_rotate_speed * 0.5

            # If we drained multiple packets this UI tick, scale deltas so the
            # motion doesn't slow down relative to the sender's poll rate.
            if steps > 1:
                translate_speed *= steps
                rotate_speed *= steps

            axis_mul = getattr(self, "viewport_axis_multiplier", {})
            tx_mul = axis_mul.get("translate_x", 1.0)
            ty_mul = axis_mul.get("translate_y", 1.0)
            tz_mul = axis_mul.get("translate_z", 1.0)
            rx_mul = axis_mul.get("rotate_x", 1.0)
            ry_mul = axis_mul.get("rotate_y", 1.0)
            rz_mul = axis_mul.get("rotate_z", 1.0)

            # === TRANSLATION IN CAMERA-LOCAL SPACE ===
            # Houdini's viewport camera translation() is ALREADY in camera-local space:
            # X = right/left (truck), Y = up/down (pedestal), Z = back/forward (dolly)
            # Positive Z moves camera AWAY from pivot (zoom out)
            # Positive tz input = push forward = should zoom IN = decrease Z
            new_trans = hou.Vector3(
                current_trans[0] + tx * translate_speed * tx_mul,  # Right/Left
                current_trans[1] + ty * translate_speed * ty_mul,  # Up/Down
                current_trans[2]
                + tz * translate_speed * tz_mul,  # Forward/Back (tz positive = zoom in)
            )

            # === ROTATION IN CAMERA-LOCAL SPACE ===
            # Build rotation deltas (in degrees)
            pitch_angle = rx * rotate_speed * rx_mul  # Tilt up/down
            yaw_angle = ry * rotate_speed * ry_mul  # Turn left/right
            roll_angle = rz * rotate_speed * rz_mul  # Roll/bank

            # Apply rotation in true camera-local space (so roll always follows
            # the camera's current view axis, regardless of world alignment).
            #
            # Houdini uses row-vector math. Treat the camera's local axes as:
            # - +X: right
            # - +Y: up
            # - -Z: view direction (camera looks down -Z)
            #
            # We apply rotation deltas in CAMERA space, then pre-multiply the
            # camera->world rotation.

            if not hasattr(self, "_viewport_cam_rot_is_c2w"):
                try:
                    view_rot_c2w = hou.Matrix3(viewport.viewTransform())

                    def max_abs_diff(a, b):
                        m = 0.0
                        for r in range(3):
                            for c in range(3):
                                d = abs(a.at(r, c) - b.at(r, c))
                                if d > m:
                                    m = d
                        return m

                    diff_c2w = max_abs_diff(current_rot, view_rot_c2w)
                    diff_w2c = max_abs_diff(current_rot.transposed(), view_rot_c2w)
                    self._viewport_cam_rot_is_c2w = diff_c2w <= diff_w2c
                except Exception:
                    self._viewport_cam_rot_is_c2w = True

            cam_rot_c2w = (
                current_rot
                if self._viewport_cam_rot_is_c2w
                else current_rot.transposed()
            )

            rot_pitch = hou.hmath.buildRotateAboutAxis(
                hou.Vector3(1, 0, 0), pitch_angle
            )
            rot_yaw = hou.hmath.buildRotateAboutAxis(hou.Vector3(0, 1, 0), yaw_angle)
            rot_roll = hou.hmath.buildRotateAboutAxis(hou.Vector3(0, 0, -1), roll_angle)
            rot_delta = rot_roll * rot_pitch * rot_yaw

            new_rot4_c2w = rot_delta * hou.Matrix4(cam_rot_c2w)
            new_rot_c2w = hou.Matrix3(new_rot4_c2w)
            new_rot = (
                new_rot_c2w
                if self._viewport_cam_rot_is_c2w
                else new_rot_c2w.transposed()
            )

            # Apply changes
            cam.setTranslation(new_trans)
            cam.setRotation(new_rot)
            viewport.setDefaultCamera(cam)
            if moving:
                self._maybe_refresh_hover_preselect()

        def _apply_viewport_fps_input(self, msg, steps=1):
            """Apply Space Mouse input to 3D Viewport in a first-person/drone style.

            Compared to regular viewport tumble/orbit navigation, this keeps the
            camera position fixed under rotation (rotation happens about the
            camera center, not the viewport pivot) and translates in camera-local
            axes.
            """
            viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
            if not viewer:
                return

            viewport = viewer.curViewport()
            if not viewport:
                return

            axis_mapping = getattr(self, "fps_axis_mapping", self.viewport_axis_mapping)

            def get_viewport_axis(axis_name):
                axis = axis_mapping.get(axis_name, "none")
                if axis == "none":
                    return 0
                invert = axis.startswith("-")
                if invert:
                    axis = axis[1:]
                value = msg.get(axis, 0)
                return -value if invert else value

            tx = get_viewport_axis("translate_x")
            ty = get_viewport_axis("translate_y")
            tz = get_viewport_axis("translate_z")
            rx = get_viewport_axis("rotate_x")
            ry = get_viewport_axis("rotate_y")
            rz = get_viewport_axis("rotate_z")

            # Respect all-`none` mappings: if nothing is mapped, do nothing.
            if not any((tx, ty, tz, rx, ry, rz)):
                return

            cam = viewport.defaultCamera()
            current_trans = hou.Vector3(cam.translation())
            current_rot = cam.rotation()

            translate_speed = (
                getattr(self, "fps_translate_speed", self.viewport_translate_speed)
                * 0.1
            )
            rotate_speed = (
                getattr(self, "fps_rotate_speed", self.viewport_rotate_speed) * 0.5
            )
            if steps > 1:
                translate_speed *= steps
                rotate_speed *= steps

            axis_mul = getattr(
                self,
                "fps_axis_multiplier",
                getattr(self, "viewport_axis_multiplier", {}),
            )
            tx_mul = axis_mul.get("translate_x", 1.0)
            ty_mul = axis_mul.get("translate_y", 1.0)
            tz_mul = axis_mul.get("translate_z", 1.0)
            rx_mul = axis_mul.get("rotate_x", 1.0)
            ry_mul = axis_mul.get("rotate_y", 1.0)
            rz_mul = axis_mul.get("rotate_z", 1.0)

            pitch_angle = rx * rotate_speed * rx_mul
            yaw_angle = ry * rotate_speed * ry_mul
            roll_angle = rz * rotate_speed * rz_mul

            def max_abs_diff(a, b):
                m = 0.0
                for r in range(3):
                    for c in range(3):
                        d = abs(a.at(r, c) - b.at(r, c))
                        if d > m:
                            m = d
                return m

            def mul_vec3_mat3_row(v, m):
                return hou.Vector3(
                    v[0] * m.at(0, 0) + v[1] * m.at(1, 0) + v[2] * m.at(2, 0),
                    v[0] * m.at(0, 1) + v[1] * m.at(1, 1) + v[2] * m.at(2, 1),
                    v[0] * m.at(0, 2) + v[1] * m.at(1, 2) + v[2] * m.at(2, 2),
                )

            # Match the rotation convention Houdini uses for the viewport camera.
            if not hasattr(self, "_viewport_cam_rot_is_c2w"):
                try:
                    view_rot_c2w = hou.Matrix3(viewport.viewTransform())
                    diff_c2w = max_abs_diff(current_rot, view_rot_c2w)
                    diff_w2c = max_abs_diff(current_rot.transposed(), view_rot_c2w)
                    self._viewport_cam_rot_is_c2w = diff_c2w <= diff_w2c
                except Exception:
                    self._viewport_cam_rot_is_c2w = True

            cam_rot_c2w = (
                current_rot
                if self._viewport_cam_rot_is_c2w
                else current_rot.transposed()
            )

            try:
                pivot_world = hou.Vector3(cam.pivot())
            except Exception:
                pivot_world = hou.Vector3(0, 0, 0)

            cam_pos_world = pivot_world + mul_vec3_mat3_row(current_trans, cam_rot_c2w)

            # Rotate in camera-local space
            rot_pitch = hou.hmath.buildRotateAboutAxis(
                hou.Vector3(1, 0, 0), pitch_angle
            )
            rot_yaw = hou.hmath.buildRotateAboutAxis(hou.Vector3(0, 1, 0), yaw_angle)
            rot_roll = hou.hmath.buildRotateAboutAxis(hou.Vector3(0, 0, -1), roll_angle)
            rot_delta = rot_roll * rot_pitch * rot_yaw

            new_rot4_c2w = rot_delta * hou.Matrix4(cam_rot_c2w)
            new_rot_c2w = hou.Matrix3(new_rot4_c2w)
            new_rot = (
                new_rot_c2w
                if self._viewport_cam_rot_is_c2w
                else new_rot_c2w.transposed()
            )

            # Drone-style translation in camera-local axes (using the updated rotation).
            delta_local = hou.Vector3(
                tx * translate_speed * tx_mul,
                ty * translate_speed * ty_mul,
                tz * translate_speed * tz_mul,
            )
            new_cam_pos_world = cam_pos_world + mul_vec3_mat3_row(
                delta_local, new_rot_c2w
            )

            # Solve translation that yields new camera position under the new rotation.
            new_trans = mul_vec3_mat3_row(
                new_cam_pos_world - pivot_world, new_rot_c2w.transposed()
            )
            cam.setTranslation(new_trans)
            cam.setRotation(new_rot)
            viewport.setDefaultCamera(cam)
            self._maybe_refresh_hover_preselect()

        def _apply_network_input(self, msg, steps=1, editor=None):
            """Apply Space Mouse input to Network Editor."""
            if editor is None:
                editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            if not editor:
                return

            # Get axis values based on config mapping
            pan_h = self._get_axis_value(msg, "pan_horizontal")
            pan_v = self._get_axis_value(msg, "pan_vertical")
            zoom_val = self._get_axis_value(msg, "zoom")

            bounds = editor.visibleBounds()
            center = bounds.center()
            size = bounds.size()
            width, height = size[0], size[1]

            # Pan
            axis_mul = getattr(self, "network_axis_multiplier", {})
            pan_h_mul = axis_mul.get("pan_horizontal", 1.0)
            pan_v_mul = axis_mul.get("pan_vertical", 1.0)
            zoom_mul = axis_mul.get("zoom", 1.0)

            dx = pan_h * width * self.pan_speed * pan_h_mul * steps
            dy = pan_v * width * self.pan_speed * pan_v_mul * steps

            # Zoom
            zoom_factor = 1.0
            if abs(zoom_val) > 0.01:
                per_step = 1.0 - (zoom_val * self.zoom_speed * zoom_mul)
                per_step = max(0.95, min(1.05, per_step))
                zoom_factor = per_step**steps
                width *= zoom_factor
                height *= zoom_factor

            # Apply
            new_cx = center[0] + dx
            new_cy = center[1] + dy

            new_bounds = hou.BoundingRect(
                new_cx - width / 2,
                new_cy - height / 2,
                new_cx + width / 2,
                new_cy + height / 2,
            )
            editor.setVisibleBounds(new_bounds)

        def _setup_cargo_scene(self):
            """Create cargo box node and position it in front of camera."""
            # Find or create cargo geo node
            cargo = hou.node("/obj/cargo")
            if cargo is None:
                obj = hou.node("/obj")
                cargo = obj.createNode("geo", "cargo")
                # Create box inside
                box = cargo.createNode("box", "cargo_box")
                box.parm("sizex").set(2)
                box.parm("sizey").set(1)
                box.parm("sizez").set(3)
                box.setDisplayFlag(True)
                box.setRenderFlag(True)
                cargo.layoutChildren()

            # Get camera position and orientation using viewTransform (correct method)
            viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
            if viewer:
                viewport = viewer.curViewport()
                if viewport:
                    # Use viewTransform().inverted() for correct C2W matrix
                    view_c2w = viewport.viewTransform().inverted()
                    cam_pos = hou.Vector3(
                        view_c2w.at(3, 0), view_c2w.at(3, 1), view_c2w.at(3, 2)
                    )
                    # Forward is +column2 (not negated)
                    cam_forward = hou.Vector3(
                        view_c2w.at(0, 2), view_c2w.at(1, 2), view_c2w.at(2, 2)
                    ).normalized()

                    # Place box 10 units in front of camera
                    box_pos = cam_pos + cam_forward * 10

                    cargo.parmTuple("t").set(box_pos)
                    cargo.parmTuple("r").set((0, 0, 0))

            # Store initial transform for reset
            self._cargo_initial_transform = cargo.worldTransform()
            print(f"Cargo scene setup complete. Node: /obj/cargo")
            return cargo

        def _reset_cargo(self):
            """Reset cargo to be in front of the current camera position."""
            cargo = hou.node("/obj/cargo")
            if cargo is None:
                print("No cargo node found. Run setup_cargo_scene() first.")
                return False

            # Get current camera position and forward direction
            viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
            if not viewer:
                print("No scene viewer found.")
                return False
            viewport = viewer.curViewport()
            if not viewport:
                print("No viewport found.")
                return False

            try:
                # Get Camera-to-World matrix
                view_c2w = viewport.viewTransform().inverted()

                # Camera position and forward using point transformation
                o = hou.Vector3(0, 0, 0) * view_c2w
                pz = hou.Vector3(0, 0, 1) * view_c2w
                cam_forward = (pz - o).normalized()

                # Place cargo at distance units in front of camera
                distance = getattr(self, "_cargo_attached_distance", 10.0)
                new_pos = o + cam_forward * distance

                cargo.parmTuple("t").set(new_pos)
                cargo.parmTuple("r").set((0, 0, 0))

                print(f"Cargo reset to camera front: {new_pos}")
                return True
            except Exception as e:
                print(f"Error resetting cargo: {e}")
                return False

        def _compute_camera_relative_delta_quat(
            self,
            msg,
            steps=1,
            *,
            rotate_speed=None,
            axis_mapping=None,
            axis_multiplier=None,
        ):
            """Compute a world-space delta quaternion from SpaceMouse rotation, camera-relative.

            Returns hou.Quaternion or None if no meaningful rotation input.
            """
            viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
            if not viewer:
                return None
            viewport = viewer.curViewport()
            if not viewport:
                return None
            cam = viewport.defaultCamera()

            current_rot = cam.rotation()
            if not hasattr(self, "_cargo_cam_rot_is_c2w"):
                try:
                    view_rot_c2w = hou.Matrix3(viewport.viewTransform())

                    def max_abs_diff(a, b):
                        m = 0.0
                        for r in range(3):
                            for c in range(3):
                                d = abs(a.at(r, c) - b.at(r, c))
                                if d > m:
                                    m = d
                        return m

                    diff_c2w = max_abs_diff(current_rot, view_rot_c2w)
                    diff_w2c = max_abs_diff(current_rot.transposed(), view_rot_c2w)
                    self._cargo_cam_rot_is_c2w = diff_c2w <= diff_w2c
                except Exception:
                    self._cargo_cam_rot_is_c2w = True

            cam_rot_c2w = (
                current_rot if self._cargo_cam_rot_is_c2w else current_rot.transposed()
            )
            q_cam = hou.Quaternion(cam_rot_c2w).normalized()

            axis_mapping = axis_mapping or self.cargo_axis_mapping
            axis_multiplier = axis_multiplier or self.cargo_axis_multiplier
            rotate_speed = float(
                self.cargo_rotate_speed if rotate_speed is None else rotate_speed
            )

            def get_axis(axis_name):
                axis = axis_mapping.get(axis_name, "none")
                if axis == "none":
                    return 0
                invert = axis.startswith("-")
                if invert:
                    axis = axis[1:]
                value = msg.get(axis, 0)
                return -value if invert else value

            pitch_raw = get_axis("pitch")
            yaw_raw = get_axis("yaw")
            roll_raw = get_axis("roll")

            pitch_deg = (
                pitch_raw * rotate_speed * axis_multiplier.get("pitch", 1.0) * steps
            )
            yaw_deg = yaw_raw * rotate_speed * axis_multiplier.get("yaw", 1.0) * steps
            roll_deg = (
                roll_raw * rotate_speed * axis_multiplier.get("roll", 1.0) * steps
            )

            if (
                abs(pitch_deg) < 0.001
                and abs(yaw_deg) < 0.001
                and abs(roll_deg) < 0.001
            ):
                return None

            cam_right = q_cam.rotate(hou.Vector3(1, 0, 0))
            cam_up = q_cam.rotate(hou.Vector3(0, 1, 0))
            cam_forward = q_cam.rotate(hou.Vector3(0, 0, -1))

            def quat_from_axis_angle(axis, angle_deg):
                q = hou.Quaternion()
                q.setToAngleAxis(angle_deg, axis.normalized())
                return q

            q_pitch = quat_from_axis_angle(cam_right, pitch_deg)
            q_yaw = quat_from_axis_angle(cam_up, yaw_deg)
            q_roll = quat_from_axis_angle(cam_forward, roll_deg)

            q_delta_world = (q_roll * q_pitch * q_yaw).normalized()
            return q_delta_world

        def _apply_cargo_rotation(self, msg, steps=1):
            """Rotate cargo box relative to camera view using quaternions.

            This implements camera-relative rotation where pitch/yaw/roll
            are always relative to the camera's view direction, regardless
            of the cargo's current orientation. The algorithm is portable
            to other engines (e.g., Unreal Blueprint).
            """
            cargo = hou.node("/obj/cargo")
            if cargo is None:
                return

            # Get camera orientation as quaternion
            viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
            if not viewer:
                return
            viewport = viewer.curViewport()
            if not viewport:
                return
            cam = viewport.defaultCamera()

            # IMPORTANT: Detect C2W vs W2C convention (same logic as _apply_viewport_input)
            # Use viewTransform() as the authoritative C2W source
            current_rot = cam.rotation()

            if not hasattr(self, "_cargo_cam_rot_is_c2w"):
                try:
                    view_rot_c2w = hou.Matrix3(viewport.viewTransform())

                    def max_abs_diff(a, b):
                        m = 0.0
                        for r in range(3):
                            for c in range(3):
                                d = abs(a.at(r, c) - b.at(r, c))
                                if d > m:
                                    m = d
                        return m

                    diff_c2w = max_abs_diff(current_rot, view_rot_c2w)
                    diff_w2c = max_abs_diff(current_rot.transposed(), view_rot_c2w)
                    self._cargo_cam_rot_is_c2w = diff_c2w <= diff_w2c
                except Exception:
                    self._cargo_cam_rot_is_c2w = True

            cam_rot_c2w = (
                current_rot if self._cargo_cam_rot_is_c2w else current_rot.transposed()
            )
            q_cam = hou.Quaternion(cam_rot_c2w)

            # Helper to get axis value with inversion support
            def get_cargo_axis(axis_name):
                axis = self.cargo_axis_mapping.get(axis_name, "none")
                if axis == "none":
                    return 0
                invert = axis.startswith("-")
                if invert:
                    axis = axis[1:]
                value = msg.get(axis, 0)
                return -value if invert else value

            # Get SpaceMouse input and scale to angles (degrees)
            pitch_raw = get_cargo_axis("pitch")
            yaw_raw = get_cargo_axis("yaw")
            roll_raw = get_cargo_axis("roll")

            axis_mul = self.cargo_axis_multiplier
            pitch_deg = (
                pitch_raw * self.cargo_rotate_speed * axis_mul.get("pitch", 1.0) * steps
            )
            yaw_deg = (
                yaw_raw * self.cargo_rotate_speed * axis_mul.get("yaw", 1.0) * steps
            )
            roll_deg = (
                roll_raw * self.cargo_rotate_speed * axis_mul.get("roll", 1.0) * steps
            )

            # Skip if no rotation input
            if (
                abs(pitch_deg) < 0.001
                and abs(yaw_deg) < 0.001
                and abs(roll_deg) < 0.001
            ):
                return

            # Extract camera world axes by rotating basis vectors
            # Houdini: camera looks down -Z
            cam_right = q_cam.rotate(hou.Vector3(1, 0, 0))
            cam_up = q_cam.rotate(hou.Vector3(0, 1, 0))
            cam_forward = q_cam.rotate(hou.Vector3(0, 0, -1))  # -Z is forward

            # Build axis-angle quaternions about camera axes
            def quat_from_axis_angle(axis, angle_deg):
                """Create quaternion from axis and angle in degrees."""
                q = hou.Quaternion()
                q.setToAngleAxis(
                    angle_deg, axis.normalized()
                )  # Houdini API: angle first, then axis
                return q

            q_pitch = quat_from_axis_angle(cam_right, pitch_deg)
            q_yaw = quat_from_axis_angle(cam_up, yaw_deg)
            q_roll = quat_from_axis_angle(cam_forward, roll_deg)

            # Compose: Roll * Pitch * Yaw (order matters for feel)
            q_delta_world = q_roll * q_pitch * q_yaw
            q_delta_world = q_delta_world.normalized()

            # Get current cargo orientation - extract pure rotation via polar decomposition
            cargo_xform = cargo.worldTransform()

            # Extract rotation-only by orthonormalizing the 3x3 portion
            # This avoids scale/shear contamination
            cargo_rot_mat = hou.Matrix3(cargo_xform)
            # Orthonormalize using Gram-Schmidt (approximate polar decomposition)
            try:
                # Use quaternion round-trip to get pure rotation
                q_obj = hou.Quaternion(cargo_rot_mat)
                q_obj = q_obj.normalized()
            except Exception:
                q_obj = hou.Quaternion()  # Identity if extraction fails

            # Apply: q_obj_new = q_delta_world * q_obj (pre-multiply for world-space)
            q_obj_new = q_delta_world * q_obj
            q_obj_new = q_obj_new.normalized()

            # Convert back to matrix and update cargo
            new_rot_mat = q_obj_new.extractRotationMatrix3()

            # Build new world transform preserving translation AND scale
            # Extract original scale from the 3x3 portion
            def extract_scale(m4):
                """Extract scale factors from Matrix4."""
                sx = hou.Vector3(m4.at(0, 0), m4.at(0, 1), m4.at(0, 2)).length()
                sy = hou.Vector3(m4.at(1, 0), m4.at(1, 1), m4.at(1, 2)).length()
                sz = hou.Vector3(m4.at(2, 0), m4.at(2, 1), m4.at(2, 2)).length()
                return (sx, sy, sz)

            scale = extract_scale(cargo_xform)

            # Apply scale to the new rotation matrix
            new_xform = hou.Matrix4(new_rot_mat)
            # Scale each row of the rotation
            for r in range(3):
                for c in range(3):
                    new_xform.setAt(r, c, new_xform.at(r, c) * scale[r])

            # Preserve translation
            new_xform.setAt(3, 0, cargo_xform.at(3, 0))  # tx
            new_xform.setAt(3, 1, cargo_xform.at(3, 1))  # ty
            new_xform.setAt(3, 2, cargo_xform.at(3, 2))  # tz

            cargo.setWorldTransform(new_xform)

        def _apply_cargo_attached(self, msg, steps=1):
            """Spring Arm Effect: cargo follows camera at fixed distance."""
            import math

            cargo = hou.node("/obj/cargo")
            if cargo is None:
                return

            # Prefer real Camera node for accurate transform
            cam = hou.node("/obj/sm_camera")
            if cam:
                try:
                    cam_xform = cam.worldTransform()
                    cam_pos = hou.Vector3(0, 0, 0) * cam_xform
                    # Camera -Z axis is forward direction
                    p_fwd = hou.Vector3(0, 0, -1) * cam_xform
                    cam_forward = p_fwd - cam_pos
                except Exception:
                    cam = None

            if not cam:
                # Fallback to viewport (keep existing viewport code)
                viewer = None
                try:
                    under_cursor = hou.ui.paneTabUnderCursor()
                    if (
                        under_cursor is not None
                        and under_cursor.type() == hou.paneTabType.SceneViewer
                    ):
                        viewer = under_cursor
                except Exception:
                    viewer = None

                if viewer is None:
                    viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
                if not viewer:
                    return

                viewport = viewer.curViewport()
                if not viewport:
                    return

                # Camera position and forward direction (world).
                try:
                    view_c2w = viewport.viewTransform().inverted()
                except Exception:
                    return

                cam_pos = hou.Vector3(0, 0, 0) * view_c2w
                # Note: +Z for viewport
                p_fwd = hou.Vector3(0, 0, 1) * view_c2w
                cam_forward = p_fwd - cam_pos
            if cam_forward.length() < 1e-8:
                return
            cam_forward = cam_forward.normalized()

            # Use horizontal-only forward (ignore pitch) for position
            # This keeps cargo at same height as camera, not following camera's look angle
            cam_forward_flat = hou.Vector3(cam_forward[0], 0.0, cam_forward[2])
            if cam_forward_flat.length() < 1e-6:
                # Camera looking straight up/down, use last known direction or default
                cam_forward_flat = hou.Vector3(0, 0, -1)
            cam_forward_flat = cam_forward_flat.normalized()

            distance = float(getattr(self, "_cargo_attached_distance", 10.0) or 10.0)
            # Position cargo horizontally in front of camera, at camera height
            box_pos = hou.Vector3(
                cam_pos[0] + cam_forward_flat[0] * distance,
                cam_pos[1],  # Same height as camera
                cam_pos[2] + cam_forward_flat[2] * distance,
            )

            # Yaw-only spring arm: rotate so the box's +Z faces the camera.
            cam_forward_flat = hou.Vector3(cam_forward[0], 0.0, cam_forward[2])
            if cam_forward_flat.length() < 1e-6:
                yaw_deg = float(getattr(self, "_cargo_attached_yaw_deg", 0.0) or 0.0)
            else:
                cam_forward_flat = cam_forward_flat.normalized()
                yaw_deg = math.degrees(
                    math.atan2(-cam_forward_flat[0], -cam_forward_flat[2])
                )
                self._cargo_attached_yaw_deg = yaw_deg

            # Build yaw basis and apply additive SpaceMouse rotation (camera-relative).
            world_up = hou.Vector3(0, 1, 0)

            def quat_from_axis_angle(axis, angle_deg):
                q = hou.Quaternion()
                q.setToAngleAxis(angle_deg, axis.normalized())
                return q

            q_yaw_basis = quat_from_axis_angle(world_up, yaw_deg).normalized()
            q_yaw_basis_inv = quat_from_axis_angle(world_up, -yaw_deg).normalized()

            rotate_speed = float(
                CONFIG.get("cargo_attached_rotate_speed", self.cargo_rotate_speed)
            )
            axis_mapping = dict(
                CONFIG.get("cargo_attached_axis_mapping", self.cargo_axis_mapping)
            )
            axis_multiplier = dict(
                CONFIG.get("cargo_attached_axis_multiplier", self.cargo_axis_multiplier)
            )

            q_delta_world = self._compute_camera_relative_delta_quat(
                msg,
                steps=steps,
                rotate_speed=rotate_speed,
                axis_mapping=axis_mapping,
                axis_multiplier=axis_multiplier,
            )

            if q_delta_world is not None:
                q_delta_local = (
                    q_yaw_basis_inv * q_delta_world * q_yaw_basis
                ).normalized()
                if self._cargo_held_rotation is None:
                    self._cargo_held_rotation = hou.Quaternion()
                self._cargo_held_rotation = (
                    q_delta_local * self._cargo_held_rotation
                ).normalized()

            q_user_local = (
                self._cargo_held_rotation
                if self._cargo_held_rotation is not None
                else hou.Quaternion()
            )
            q_world = (q_yaw_basis * q_user_local).normalized()
            # Prefer setting world transform to avoid pivot/parent/pre-transform surprises.
            scale = None
            try:
                cargo_xform = cargo.worldTransform()
                sx = hou.Vector3(
                    cargo_xform.at(0, 0), cargo_xform.at(0, 1), cargo_xform.at(0, 2)
                ).length()
                sy = hou.Vector3(
                    cargo_xform.at(1, 0), cargo_xform.at(1, 1), cargo_xform.at(1, 2)
                ).length()
                sz = hou.Vector3(
                    cargo_xform.at(2, 0), cargo_xform.at(2, 1), cargo_xform.at(2, 2)
                ).length()
                scale = (sx, sy, sz)
            except Exception:
                scale = None

            try:
                rot_m3 = q_world.extractRotationMatrix3()
                new_xform = hou.Matrix4(rot_m3)
                if scale is not None:
                    for r in range(3):
                        s = scale[r] if abs(scale[r]) > 1e-8 else 1.0
                        for c in range(3):
                            new_xform.setAt(r, c, new_xform.at(r, c) * s)
                new_xform.setAt(3, 0, box_pos[0])
                new_xform.setAt(3, 1, box_pos[1])
                new_xform.setAt(3, 2, box_pos[2])
                cargo.setWorldTransform(new_xform)
            except Exception:
                try:
                    cargo.parmTuple("t").set(box_pos)
                    cargo.parmTuple("r").set((0, yaw_deg, 0))
                except Exception:
                    pass

        def _grab_cargo_attached(self):
            """Initialize cargo attached mode - save current rotation as held rotation."""
            cargo = hou.node("/obj/cargo")
            if cargo is None:
                print("No cargo node found. Run setup_cargo_scene() first.")
                return False

            viewer = None
            try:
                under_cursor = hou.ui.paneTabUnderCursor()
                if (
                    under_cursor is not None
                    and under_cursor.type() == hou.paneTabType.SceneViewer
                ):
                    viewer = under_cursor
            except Exception:
                viewer = None

            if viewer is None:
                viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
            if not viewer:
                return False
            viewport = viewer.curViewport()
            if not viewport:
                return False
            cam = viewport.defaultCamera()

            view_rot_c2w = None
            # Get Camera-to-World matrix directly.
            # Houdini's viewTransform() always returns World-to-Camera, so invert it.
            view_xform_c2w = None
            cam_pos = None
            try:
                view_xform_c2w = viewport.viewTransform().inverted()
                view_rot_c2w = hou.Matrix3(view_xform_c2w)
                cam_pos = hou.Vector3(0, 0, 0) * view_xform_c2w
            except Exception:
                view_rot_c2w = None
                view_xform_c2w = None
                cam_pos = None

            current_rot = cam.rotation()
            if not hasattr(self, "_cargo_attached_cam_rot_is_c2w"):
                try:
                    if view_rot_c2w is None:
                        raise RuntimeError("view_rot_c2w unavailable")

                    def max_abs_diff(a, b):
                        m = 0.0
                        for r in range(3):
                            for c in range(3):
                                d = abs(a.at(r, c) - b.at(r, c))
                                if d > m:
                                    m = d
                        return m

                    diff_c2w = max_abs_diff(current_rot, view_rot_c2w)
                    diff_w2c = max_abs_diff(current_rot.transposed(), view_rot_c2w)
                    self._cargo_attached_cam_rot_is_c2w = diff_c2w <= diff_w2c
                except Exception:
                    self._cargo_attached_cam_rot_is_c2w = True

            cam_rot_c2w = (
                current_rot
                if self._cargo_attached_cam_rot_is_c2w
                else current_rot.transposed()
            )

            # Use the same camera axis extraction as _apply_cargo_attached.
            if view_xform_c2w is not None:
                o = hou.Vector3(0, 0, 0) * view_xform_c2w
                px = hou.Vector3(1, 0, 0) * view_xform_c2w
                py = hou.Vector3(0, 1, 0) * view_xform_c2w
                pz = hou.Vector3(0, 0, -1) * view_xform_c2w

                cam_pos = o
                cam_right = (px - o).normalized()
                cam_up = (py - o).normalized()
                cam_forward = (pz - o).normalized()
            else:
                q_cam = hou.Quaternion(cam_rot_c2w).normalized()
                cam_pos = None
                cam_right = q_cam.rotate(hou.Vector3(1, 0, 0))
                cam_up = q_cam.rotate(hou.Vector3(0, 1, 0))
                cam_forward = q_cam.rotate(hou.Vector3(0, 0, -1))

            # On grab, immediately place cargo in front of the camera.
            if cam_pos is not None:
                new_pos = (
                    cam_pos + cam_forward.normalized() * self._cargo_attached_distance
                )
                try:
                    cargo.parmTuple("t").set(new_pos)
                except Exception:
                    pass

            # Tidal locking: compute yaw-only basis quaternion from camera forward (ignore pitch).
            import math

            world_up = hou.Vector3(0, 1, 0)
            cam_forward_flat = hou.Vector3(cam_forward[0], 0.0, cam_forward[2])
            if cam_forward_flat.length() < 1e-6:
                yaw_basis_deg = float(
                    getattr(self, "_cargo_attached_yaw_basis_deg", 0.0) or 0.0
                )
            else:
                cam_forward_flat = cam_forward_flat.normalized()
                yaw_basis_deg = math.degrees(
                    math.atan2(-cam_forward_flat[0], -cam_forward_flat[2])
                )
                self._cargo_attached_yaw_basis_deg = yaw_basis_deg

            def quat_from_axis_angle(axis, angle_deg):
                q = hou.Quaternion()
                q.setToAngleAxis(angle_deg, axis.normalized())
                return q

            q_yaw_basis = quat_from_axis_angle(world_up, yaw_basis_deg).normalized()
            q_yaw_basis_inv = quat_from_axis_angle(
                world_up, -yaw_basis_deg
            ).normalized()

            cargo_xform = cargo.worldTransform()

            sx = hou.Vector3(
                cargo_xform.at(0, 0), cargo_xform.at(0, 1), cargo_xform.at(0, 2)
            ).length()
            sy = hou.Vector3(
                cargo_xform.at(1, 0), cargo_xform.at(1, 1), cargo_xform.at(1, 2)
            ).length()
            sz = hou.Vector3(
                cargo_xform.at(2, 0), cargo_xform.at(2, 1), cargo_xform.at(2, 2)
            ).length()
            self._cargo_held_scale = (sx, sy, sz)

            rot_m4 = hou.Matrix4(cargo_xform.asTuple())
            for r, s in enumerate(self._cargo_held_scale):
                denom = s if abs(s) > 1e-8 else 1.0
                for c in range(3):
                    rot_m4.setAt(r, c, rot_m4.at(r, c) / denom)

            q_cargo_world = hou.Quaternion(hou.Matrix3(rot_m4)).normalized()

            q_rel = (q_yaw_basis_inv * q_cargo_world).normalized()
            rel_fwd = q_rel.rotate(hou.Vector3(0, 0, -1))
            rel_fwd_h = hou.Vector3(rel_fwd[0], 0.0, rel_fwd[2])
            if rel_fwd_h.length() < 1e-6:
                rel_yaw_deg = 0.0
            else:
                rel_fwd_h = rel_fwd_h.normalized()
                rel_yaw_deg = math.degrees(math.atan2(-rel_fwd_h[0], -rel_fwd_h[2]))

            q_rel_yaw_inv = quat_from_axis_angle(world_up, -rel_yaw_deg).normalized()
            self._cargo_local_rotation = (q_rel_yaw_inv * q_rel).normalized()
            self._cargo_held_rotation = self._cargo_local_rotation
            print(f"Cargo grabbed. Local rotation: {self._cargo_local_rotation}")
            return True

        def _release_cargo_attached(self):
            """Release cargo - clear held rotation."""
            self._cargo_held_rotation = None
            self._cargo_local_rotation = None
            self._cargo_held_scale = None
            print("Cargo released.")

        def _send_shutdown(self):
            """Best-effort signal to the external reader to exit."""
            if getattr(self, "_shutdown_sent", False):
                return
            host = getattr(self, "_last_sender_host", None)
            port = getattr(self, "_last_sender_port", None)
            if host is None or port is None:
                return

            try:
                payload = json.dumps(
                    {"type": "shutdown", "t_send_ns": time.time_ns()},
                    separators=(",", ":"),
                ).encode("utf-8")
                self.socket.writeDatagram(payload, host, int(port))
                self._shutdown_sent = True
            except Exception:
                return

        def _on_about_to_quit(self):
            try:
                self._send_shutdown()
            except Exception:
                pass

        def stop(self):
            # Avoid leaving Houdini with "stuck" injected keys if the receiver is
            # stopped while SpaceMouse buttons are held.
            try:
                for _btn, binding in list(
                    getattr(self, "_button_hold_bindings", {}).items()
                ):
                    inject = binding.get("_inject", "qt")
                    if inject == "win32":
                        if binding.get("main_vk") is not None:
                            self._hold_vk_up(binding.get("main_vk"))
                        for vk in reversed(binding.get("mod_vks", [])):
                            self._hold_vk_up(vk)
                    else:
                        if binding.get("main_key") is not None:
                            self._hold_key_up(binding.get("main_key"))
                        for mod_key in reversed(binding.get("mod_keys", [])):
                            self._hold_key_up(mod_key)
                getattr(self, "_button_hold_bindings", {}).clear()
                self._buttons_prev_mask = 0
            except Exception:
                pass

            # Force-release any remaining held keys (best-effort cleanup).
            try:
                for vk in list(getattr(self, "_vk_hold_counts", {}).keys()):
                    try:
                        self._win32_send_key(vk, False)
                    except Exception:
                        pass
                getattr(self, "_vk_hold_counts", {}).clear()
            except Exception:
                pass

            try:
                for key in list(getattr(self, "_key_hold_counts", {}).keys()):
                    try:
                        self._post_key_event(
                            self._hotkey_target(),
                            QtCore.QEvent.KeyRelease,
                            key,
                            self._current_injected_modifiers(),
                        )
                    except Exception:
                        pass
                getattr(self, "_key_hold_counts", {}).clear()
            except Exception:
                pass

            try:
                timer = getattr(self, "_qt_timer", None)
                if timer is not None and timer.isActive():
                    timer.stop()
            except Exception:
                pass
            try:
                if self._callback_registered:
                    hou.ui.removeEventLoopCallback(self._poll_data)
                    self._callback_registered = False

                # Remove event filter
                app = QtWidgets.QApplication.instance()
                if app:
                    app.removeEventFilter(self)
            except Exception:
                pass
            self.socket.close()
            print("SpaceMouseReceiver: Stopped")

    # Create and store receiver
    _receiver = SpaceMouseReceiver()
    hou.session._spacemouse_receiver = _receiver

    print("\nSpace Mouse Receiver started!")
    print("Now run spacemouse_standalone.py in a separate terminal.")
    return _receiver


def stop_receiver():
    """Stop the UDP receiver."""
    global _receiver
    import hou

    session_receiver = getattr(hou.session, "_spacemouse_receiver", None)
    if session_receiver and session_receiver is not _receiver:
        try:
            session_receiver.stop()
        except Exception:
            pass

    if _receiver:
        try:
            _receiver.stop()
        except Exception:
            pass
        _receiver = None

    if hasattr(hou.session, "_spacemouse_receiver"):
        del hou.session._spacemouse_receiver

    print("Receiver stopped")


def set_pan_speed(speed):
    """Set pan speed (default 0.002)."""
    global _receiver
    if _receiver:
        _receiver.pan_speed = speed
        print(f"Pan speed set to {speed}")


def set_zoom_speed(speed):
    """Set zoom speed (default 0.001)."""
    global _receiver
    if _receiver:
        _receiver.zoom_speed = speed
        print(f"Zoom speed set to {speed}")


def reload_config():
    """Reload config from config.json and apply to running receiver.

    Note: This directly sets attributes on the receiver instance to avoid
    issues with class definitions changing after module reload.
    """
    global _receiver, CONFIG

    CONFIG = load_config()

    # Check both module-level and hou.session for receiver
    receiver = _receiver
    try:
        import hou

        if hasattr(hou.session, "_spacemouse_receiver"):
            receiver = hou.session._spacemouse_receiver
    except:
        pass

    if receiver:
        # Directly set attributes (works even after module reload)
        receiver.mode = CONFIG.get("mode", "network")
        try:
            if hasattr(receiver, "_sync_polling"):
                receiver._sync_polling()
        except Exception:
            pass
        receiver.pan_speed = CONFIG["network_speed"]["pan"]
        receiver.zoom_speed = CONFIG["network_speed"]["zoom"]
        receiver.network_axis_multiplier = dict(
            CONFIG.get("network_axis_multiplier", {})
        )
        receiver.viewport_translate_speed = CONFIG["viewport_speed"]["translate"]
        receiver.viewport_rotate_speed = CONFIG["viewport_speed"]["rotate"]
        receiver.axis_mapping = dict(CONFIG["axis_mapping"])  # Network mode
        receiver.viewport_axis_mapping = dict(
            CONFIG.get("viewport_axis_mapping", {})
        )  # Viewport mode
        receiver.viewport_axis_multiplier = dict(
            CONFIG.get("viewport_axis_multiplier", {})
        )
        fps_speed = CONFIG.get("fps_speed", {})
        receiver.fps_translate_speed = fps_speed.get(
            "translate", receiver.viewport_translate_speed
        )
        receiver.fps_rotate_speed = fps_speed.get(
            "rotate", receiver.viewport_rotate_speed
        )
        receiver.fps_axis_mapping = dict(
            CONFIG.get("fps_axis_mapping", receiver.viewport_axis_mapping)
        )
        receiver.fps_axis_multiplier = dict(
            CONFIG.get("fps_axis_multiplier", receiver.viewport_axis_multiplier)
        )
        receiver.button_hotkeys = dict(CONFIG.get("button_hotkeys", {}))
        receiver.auto_mode_switch = dict(CONFIG.get("auto_mode_switch", {}))
        receiver.hover_refresh = dict(CONFIG.get("hover_refresh", {}))
        try:
            if hasattr(receiver, "_sync_hover_refresh_settings"):
                receiver._sync_hover_refresh_settings()
        except Exception:
            pass
        print(f"Config applied to receiver:")
        print(f"  mode: {receiver.mode}")
        print(f"  network_speed: pan={receiver.pan_speed}, zoom={receiver.zoom_speed}")
        print(
            f"  viewport_speed: translate={receiver.viewport_translate_speed}, rotate={receiver.viewport_rotate_speed}"
        )
        print(
            f"  fps_speed: translate={receiver.fps_translate_speed}, rotate={receiver.fps_rotate_speed}"
        )
        print(f"  axis_mapping (network): {receiver.axis_mapping}")
        print(f"  viewport_axis_mapping: {receiver.viewport_axis_mapping}")
        print(f"  fps_axis_mapping: {receiver.fps_axis_mapping}")
    else:
        print("Config reloaded (no active receiver)")

    return CONFIG


def get_config():
    """Get current config."""
    return CONFIG


def debug_receiver():
    """Print current receiver state for debugging."""
    receiver = None
    try:
        import hou

        if hasattr(hou.session, "_spacemouse_receiver"):
            receiver = hou.session._spacemouse_receiver
    except:
        pass

    if not receiver:
        global _receiver
        receiver = _receiver

    if receiver:
        print("=== Receiver State ===")
        print(f"  mode: {receiver.mode}")
        print(f"  pan_speed: {receiver.pan_speed}")
        print(f"  zoom_speed: {receiver.zoom_speed}")
        print(f"  viewport_translate_speed: {receiver.viewport_translate_speed}")
        print(f"  viewport_rotate_speed: {receiver.viewport_rotate_speed}")
        if hasattr(receiver, "fps_translate_speed"):
            print(f"  fps_translate_speed: {receiver.fps_translate_speed}")
        if hasattr(receiver, "fps_rotate_speed"):
            print(f"  fps_rotate_speed: {receiver.fps_rotate_speed}")
        print(f"  axis_mapping (network): {receiver.axis_mapping}")
        print(
            f"  network_axis_multiplier: {getattr(receiver, 'network_axis_multiplier', 'N/A')}"
        )
        print(
            f"  viewport_axis_mapping: {getattr(receiver, 'viewport_axis_mapping', 'N/A')}"
        )
        print(
            f"  viewport_axis_multiplier: {getattr(receiver, 'viewport_axis_multiplier', 'N/A')}"
        )
        print(f"  fps_axis_mapping: {getattr(receiver, 'fps_axis_mapping', 'N/A')}")
        print(
            f"  fps_axis_multiplier: {getattr(receiver, 'fps_axis_multiplier', 'N/A')}"
        )
        print(f"  button_hotkeys: {getattr(receiver, 'button_hotkeys', 'N/A')}")
        print(f"  auto_mode_switch: {getattr(receiver, 'auto_mode_switch', 'N/A')}")
        print(f"  hover_refresh: {getattr(receiver, 'hover_refresh', 'N/A')}")
        if hasattr(receiver, "_last_error"):
            print(f"  last_error: {getattr(receiver, '_last_error', 'N/A')}")
        print(f"  message_count: {receiver.message_count}")
        print(f"  callback_registered: {receiver._callback_registered}")

        if hasattr(receiver, "_perf_latency_count"):
            try:
                import math

                def _fmt_ms(v):
                    return "N/A" if v is None else f"{v:.2f}"

                lat_count = receiver._perf_latency_count
                lat_mean = receiver._perf_latency_mean_ms if lat_count else None
                lat_std = None
                if lat_count and lat_count > 1:
                    lat_std = math.sqrt(receiver._perf_latency_m2 / (lat_count - 1))

                window = list(getattr(receiver, "_perf_latency_window_ms", []))
                p50 = p90 = p99 = None
                if window:
                    window.sort()
                    n = len(window)

                    def _pct(p):
                        return window[int(p * (n - 1))]

                    p50 = _pct(0.50)
                    p90 = _pct(0.90)
                    p99 = _pct(0.99)

                steps_avg = None
                if receiver._perf_steps_count:
                    steps_avg = receiver._perf_steps_sum / receiver._perf_steps_count

                apply_avg = None
                apply_window = list(getattr(receiver, "_perf_apply_window_ms", []))
                if apply_window:
                    apply_avg = sum(apply_window) / len(apply_window)

                interval_avg = None
                interval_window = list(
                    getattr(receiver, "_perf_apply_interval_window_ms", [])
                )
                if interval_window:
                    interval_avg = sum(interval_window) / len(interval_window)

                apply_hz = None
                if interval_avg and interval_avg > 0:
                    apply_hz = 1000.0 / interval_avg

                print("=== Performance ===")
                print(
                    f"  latency_ms: last={_fmt_ms(receiver._perf_latency_last_ms)} mean={_fmt_ms(lat_mean)} std={_fmt_ms(lat_std)} min={_fmt_ms(receiver._perf_latency_min_ms)} max={_fmt_ms(receiver._perf_latency_max_ms)}"
                )
                print(
                    f"  latency_ms_window: p50={_fmt_ms(p50)} p90={_fmt_ms(p90)} p99={_fmt_ms(p99)} (n={len(window)})"
                )
                print(
                    f"  backlog_steps: last={receiver._perf_steps_last} avg={_fmt_ms(steps_avg)} max={receiver._perf_steps_max} skipped_seq={receiver._perf_seq_skipped}"
                )
                print(
                    f"  apply_ms: last={_fmt_ms(receiver._perf_apply_last_ms)} avg={_fmt_ms(apply_avg)}"
                )
                print(
                    f"  apply_interval_ms: last={_fmt_ms(receiver._perf_apply_interval_last_ms)} avg={_fmt_ms(interval_avg)} hz={_fmt_ms(apply_hz)}"
                )
            except Exception as e:
                print(f"Performance metrics unavailable: {e}")
        return receiver
    else:
        print("No active receiver found")
        return None


def launch_reader(elevated=True, no_wait=True, houdini_pid=None):
    """Launch the external Space Mouse reader batch file in a new console.

    Useful for Houdini shelf tools so you don't have to start the receiver and
    the reader as two separate manual steps.

    Args:
        elevated: If True on Windows, request UAC elevation (needed to kill
            3Dconnexion processes in the default batch script).
        no_wait: If True, pass --no-wait to the batch script to skip the
            "Press any key when receiver is ready" prompt.
        houdini_pid: Optional Houdini PID to pass through to the reader so it
            can auto-exit when Houdini closes.
    """
    bat_path = os.path.join(os.path.dirname(__file__), "start_spacemouse_pan.bat")
    if not os.path.exists(bat_path):
        print(f"start_spacemouse_pan.bat not found: {bat_path}")
        return False

    if is_reader_running():
        print("Space Mouse reader already running; skipping launch.")
        return True

    pid_arg = None
    if isinstance(houdini_pid, int) and houdini_pid > 0:
        pid_arg = int(houdini_pid)
    elif os.name == "nt":
        # If we're running inside Houdini, pass its PID so the reader can exit
        # automatically when Houdini closes (even if shutdown UDP messages are missed).
        try:
            import hou  # noqa: F401

            pid_arg = os.getpid()
        except Exception:
            pid_arg = None

    # cmd.exe quoting is finicky when passing a quoted script path + args.
    # The empty quoted string pattern ensures args are preserved even if the
    # batch path ever contains spaces:
    #   cmd.exe /c ""C:\path with spaces\script.bat" --no-wait"
    bat_args = f'/c ""{bat_path}"'
    if no_wait:
        bat_args += " --no-wait"
    if pid_arg is not None:
        bat_args += f" --houdini-pid {pid_arg}"
    bat_args += '"'

    if os.name == "nt" and elevated:
        try:
            import ctypes

            rc = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                "cmd.exe",
                bat_args,
                os.path.dirname(bat_path),
                1,
            )
            return rc > 32
        except Exception as e:
            print(f"Failed to launch reader (elevated): {e}")
            return False

    try:
        import subprocess

        CREATE_NEW_CONSOLE = 0x00000010
        cmdline = f'""{bat_path}"'
        if no_wait:
            cmdline += " --no-wait"
        if pid_arg is not None:
            cmdline += f" --houdini-pid {pid_arg}"
        cmdline += '"'

        subprocess.Popen(
            ["cmd.exe", "/c", cmdline],
            cwd=os.path.dirname(bat_path),
            creationflags=CREATE_NEW_CONSOLE,
        )
        return True
    except Exception as e:
        print(f"Failed to launch reader: {e}")
        return False


def set_mode(mode):
    """Set the control mode ('network', 'viewport', 'viewport_fps', 'cargo', or 'cargo_attached')."""
    global _receiver, CONFIG

    valid_modes = ("network", "viewport", "viewport_fps", "cargo", "cargo_attached")
    if mode not in valid_modes:
        print(f"Invalid mode '{mode}'. Use one of: {', '.join(valid_modes)}.")
        return False

    CONFIG = load_config()
    CONFIG["mode"] = mode
    save_config(CONFIG)

    # Apply full config to the running receiver (including FPS mappings/speeds).
    # This also updates the mode on the receiver instance.
    try:
        reload_config()
    except Exception:
        pass

    # Apply to receiver
    receiver = _receiver
    try:
        import hou

        if hasattr(hou.session, "_spacemouse_receiver"):
            receiver = hou.session._spacemouse_receiver
    except:
        pass

    if receiver:
        receiver.mode = mode
        try:
            if hasattr(receiver, "_sync_polling"):
                receiver._sync_polling()
        except Exception:
            pass

        # Auto-grab when entering cargo_attached mode
        if mode == "cargo_attached" and hasattr(receiver, "_grab_cargo_attached"):
            try:
                receiver._grab_cargo_attached()
                receiver._apply_cargo_attached({}, steps=1)
            except Exception:
                pass

        print(f"Mode set to: {mode}")
    else:
        print(f"Mode set to: {mode} (no active receiver)")

    return True


def get_mode():
    """Get the current control mode."""
    receiver = None
    try:
        import hou

        if hasattr(hou.session, "_spacemouse_receiver"):
            receiver = hou.session._spacemouse_receiver
    except:
        pass

    if not receiver:
        global _receiver
        receiver = _receiver

    if receiver:
        return receiver.mode
    else:
        config = load_config()
        return config.get("mode", "network")


def toggle_mode():
    """Toggle between 'network' and 'viewport' modes."""
    current = get_mode()
    new_mode = "viewport" if current == "network" else "network"
    set_mode(new_mode)
    return new_mode


def setup_cargo_scene():
    """Create cargo box and position it in front of camera.

    Use this to initialize the cargo scene for SpaceMouse control.
    The cargo will be placed 10 units in front of the current camera.
    """
    receiver = None
    try:
        import hou

        if hasattr(hou.session, "_spacemouse_receiver"):
            receiver = hou.session._spacemouse_receiver
    except:
        pass

    if not receiver:
        global _receiver
        receiver = _receiver

    if receiver:
        return receiver._setup_cargo_scene()
    else:
        print("No active receiver. Start receiver first with start_receiver().")
        return None


def reset_cargo():
    """Reset cargo box to be in front of the current camera.

    This function directly sets the cargo position without relying on
    the receiver instance's method, ensuring the latest code is always used.
    """
    import hou

    cargo = hou.node("/obj/cargo")
    if cargo is None:
        print("No cargo node found. Run setup_cargo_scene() first.")
        return False

    # Get current camera position and forward direction
    viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
    if not viewer:
        print("No scene viewer found.")
        return False
    viewport = viewer.curViewport()
    if not viewport:
        print("No viewport found.")
        return False

    try:
        # Get Camera-to-World matrix
        view_c2w = viewport.viewTransform().inverted()

        # Camera position and forward using point transformation
        o = hou.Vector3(0, 0, 0) * view_c2w
        pz = hou.Vector3(0, 0, 1) * view_c2w
        cam_forward = (pz - o).normalized()

        # Get distance from receiver if available, otherwise use default
        distance = 10.0
        receiver = getattr(hou.session, "_spacemouse_receiver", None)
        if receiver:
            distance = getattr(receiver, "_cargo_attached_distance", 10.0)

        new_pos = o + cam_forward * distance

        cargo.parmTuple("t").set(new_pos)
        cargo.parmTuple("r").set((0, 0, 0))

        print(f"Cargo reset to camera front: {new_pos}")
        return True
    except Exception as e:
        print(f"Error resetting cargo: {e}")
        return False


def start_cargo_mode():
    """Convenience function to set up cargo mode.

    This will:
    1. Start the receiver if not already running
    2. Set mode to 'cargo'
    3. Create the cargo scene
    """
    start_receiver()
    set_mode("cargo")
    setup_cargo_scene()
    print("Cargo mode started. Use SpaceMouse to rotate the cargo box.")
    print("Call reset_cargo() to reset to initial state.")


def start_cargo_attached_mode():
    """Convenience function to set up cargo attached mode.

    This mode is a "spring arm" camera attachment:
    - Cargo stays at a fixed distance in front of the camera
    - Cargo yaw rotates to face the camera (pitch/roll ignored)
    - SpaceMouse input is currently ignored (camera drives the attachment)

    This will:
    1. Start the receiver if not already running
    2. Set mode to 'cargo_attached'
    3. Create the cargo scene
    4. Initialize held rotation
    """
    start_receiver()
    set_mode("cargo_attached")
    setup_cargo_scene()
    global _receiver
    if _receiver is not None:
        _receiver._grab_cargo_attached()
    print("Cargo attached mode started.")
    print("  - Cargo stays in front of camera (fixed distance)")
    print("  - Cargo faces the camera (yaw only)")


def grab_cargo():
    """Grab cargo - save current rotation as held rotation."""
    global _receiver
    if _receiver is not None:
        return _receiver._grab_cargo_attached()
    return False


def release_cargo():
    """Release cargo - clear held rotation."""
    global _receiver
    if _receiver is not None:
        _receiver._release_cargo_attached()
        return True
    return False


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    # Running standalone - read Space Mouse and send to Houdini
    try:
        import hid
    except ImportError:
        print("ERROR: hidapi not installed")
        print("Install with: pip install hidapi")
        sys.exit(1)

    import argparse

    parser = argparse.ArgumentParser(add_help=True)
    # Kept for backwards compatibility with older launchers/scripts.
    parser.add_argument("--no-wait", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--houdini-pid",
        type=int,
        default=None,
        help="Exit automatically when this Houdini PID is no longer running.",
    )
    args = parser.parse_args()

    success = read_spacemouse_loop(houdini_pid=args.houdini_pid)
    sys.exit(0 if success else 1)
