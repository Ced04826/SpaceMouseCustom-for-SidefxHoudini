"""
Keyboard-triggered Network Editor Panning for Houdini

Alternative approach: Map Space Mouse buttons to keyboard shortcuts via 
3Dconnexion software, then trigger these functions.

This is NOT the primary solution - see spacemouse_standalone.py for the 
main implementation that provides smooth analog panning.

Usage:
    from spacemouse_network_pan.dev import keyboard_pan
    keyboard_pan.setup()
    
    # Then use:
    hou.session.network_pan.pan_left()
    hou.session.network_pan.pan_right()
    hou.session.network_pan.pan_up()
    hou.session.network_pan.pan_down()
    hou.session.network_pan.zoom_in()
    hou.session.network_pan.zoom_out()
"""

import hou


class NetworkEditorPan:
    def __init__(self):
        self.pan_speed = 0.15
        self.zoom_speed = 0.1
        
    def _get_editor(self):
        return hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        
    def pan_left(self):
        self._pan(-self.pan_speed, 0)
        
    def pan_right(self):
        self._pan(self.pan_speed, 0)
        
    def pan_up(self):
        self._pan(0, self.pan_speed)
        
    def pan_down(self):
        self._pan(0, -self.pan_speed)
        
    def zoom_in(self):
        self._zoom(1.0 - self.zoom_speed)
        
    def zoom_out(self):
        self._zoom(1.0 + self.zoom_speed)
        
    def _pan(self, dx_factor, dy_factor):
        editor = self._get_editor()
        if not editor:
            return
        bounds = editor.visibleBounds()
        size = bounds.size()
        dx = dx_factor * size[0]
        dy = dy_factor * size[1]
        new_bounds = hou.BoundingRect(
            bounds.min()[0] + dx, bounds.min()[1] + dy,
            bounds.max()[0] + dx, bounds.max()[1] + dy
        )
        editor.setVisibleBounds(new_bounds)
        
    def _zoom(self, factor):
        editor = self._get_editor()
        if not editor:
            return
        bounds = editor.visibleBounds()
        center = bounds.center()
        size = bounds.size()
        new_width = size[0] * factor
        new_height = size[1] * factor
        new_bounds = hou.BoundingRect(
            center[0] - new_width / 2, center[1] - new_height / 2,
            center[0] + new_width / 2, center[1] + new_height / 2
        )
        editor.setVisibleBounds(new_bounds)


_panner = None

def setup():
    global _panner
    _panner = NetworkEditorPan()
    hou.session.network_pan = _panner
    print("Network Editor Panner initialized!")
    return _panner

def get_panner():
    global _panner
    if _panner is None:
        setup()
    return _panner

def pan_left(): get_panner().pan_left()
def pan_right(): get_panner().pan_right()
def pan_up(): get_panner().pan_up()
def pan_down(): get_panner().pan_down()
def zoom_in(): get_panner().zoom_in()
def zoom_out(): get_panner().zoom_out()
