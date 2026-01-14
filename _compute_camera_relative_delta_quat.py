
def _compute_camera_relative_delta_quat(self, msg, steps, rotate_speed, axis_mapping, axis_multiplier):
    import hou

    axis_mapping = axis_mapping or {}
    axis_multiplier = axis_multiplier or {}

    # Get Scene Viewer and Camera
    viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
    if not viewer:
        return hou.Quaternion()
    viewport = viewer.curViewport()
    if not viewport:
        return hou.Quaternion()
    cam = viewport.defaultCamera()

    # Get Camera Rotation (C2W)
    current_rot = cam.rotation()

    # Check orientation convention (C2W vs W2C)
    if not hasattr(self, "_cargo_cam_rot_is_c2w"):
        try:
            view_rot_c2w = hou.Matrix3(viewport.viewTransform())
            def max_abs_diff(a, b):
                m = 0.0
                for r in range(3):
                    for c in range(3):
                        d = abs(a.at(r, c) - b.at(r, c))
                        if d > m: m = d
                return m
            diff_c2w = max_abs_diff(current_rot, view_rot_c2w)
            diff_w2c = max_abs_diff(current_rot.transposed(), view_rot_c2w)
            self._cargo_cam_rot_is_c2w = diff_c2w <= diff_w2c
        except:
            self._cargo_cam_rot_is_c2w = True

    cam_rot_c2w = current_rot if self._cargo_cam_rot_is_c2w else current_rot.transposed()
    q_cam = hou.Quaternion(cam_rot_c2w)

    # Helper to get axis value
    def get_axis(axis_name):
        axis = axis_mapping.get(axis_name, "none")
        if axis == "none":
            return 0.0
        invert = axis.startswith("-")
        if invert:
            axis = axis[1:]
        value = msg.get(axis, 0.0)
        return -value if invert else value

    # Calculate angles
    pitch_deg = get_axis("pitch") * rotate_speed * axis_multiplier.get("pitch", 1.0) * steps
    yaw_deg = get_axis("yaw") * rotate_speed * axis_multiplier.get("yaw", 1.0) * steps
    roll_deg = get_axis("roll") * rotate_speed * axis_multiplier.get("roll", 1.0) * steps

    if abs(pitch_deg) < 0.001 and abs(yaw_deg) < 0.001 and abs(roll_deg) < 0.001:
        return hou.Quaternion()

    # Camera axes (Houdini camera looks down -Z)
    cam_right = q_cam.rotate(hou.Vector3(1, 0, 0))
    cam_up = q_cam.rotate(hou.Vector3(0, 1, 0))
    cam_forward = q_cam.rotate(hou.Vector3(0, 0, -1))

    # Build Rotations
    def quat_angle_axis(angle, axis):
        q = hou.Quaternion()
        q.setToAngleAxis(angle, axis.normalized())
        return q

    q_pitch = quat_angle_axis(pitch_deg, cam_right)
    q_yaw = quat_angle_axis(yaw_deg, cam_up)
    q_roll = quat_angle_axis(roll_deg, cam_forward)

    # Combine: Roll * Pitch * Yaw
    q_delta = q_roll * q_pitch * q_yaw
    return q_delta.normalized()
