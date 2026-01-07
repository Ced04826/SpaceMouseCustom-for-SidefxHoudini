"""
Space Mouse Network Editor Pan for Houdini

Control Houdini's Network Editor panning using a 3Dconnexion Space Mouse.

RECOMMENDED USAGE (Standalone Reader):
    
    1. Stop 3Dconnexion driver processes (3DxService.exe, etc.)
    
    2. In Houdini Python Shell:
       >>> from spacemouse_network_pan.spacemouse_standalone import start_receiver
       >>> start_receiver()
       
    3. In a separate terminal:
       > python spacemouse_network_pan/spacemouse_standalone.py
       
    4. Move the Space Mouse - Network Editor pans!

ALTERNATIVE (Keyboard Shortcuts):
    
    If you want to keep the 3Dconnexion driver running:
    
    >>> from spacemouse_network_pan import keyboard_pan
    >>> keyboard_pan.setup()
    >>> hou.session.network_pan.pan_left()  # etc.
    
    Then map Space Mouse buttons to keyboard shortcuts in 3Dconnexion software.
"""

__version__ = "2.0.0"
__author__ = "Houdini MCP Project"

# Only import keyboard_pan when running inside Houdini
# (it requires the hou module)
try:
    import hou
    from . import keyboard_pan
    __all__ = ['keyboard_pan']
except ImportError:
    # Running outside Houdini (e.g., standalone script)
    __all__ = []

# For the standalone approach, users import directly:
# from spacemouse_network_pan.spacemouse_standalone import start_receiver, stop_receiver
