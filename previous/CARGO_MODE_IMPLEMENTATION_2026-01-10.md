# SpaceMouse Cargo Mode - Camera-Relative Rotation Implementation

## Project Overview

**Date**: 2026-01-10
**Status**: Completed and Tested in Houdini
**Next Step**: Port algorithm to Unreal Blueprint

---

## Problem Statement

When rotating an object using its **local axes**, user intuition breaks after rotation:

- Initial state: Object faces camera, pitch/yaw/roll work as expected
- After 90° yaw rotation: The object's local axes have rotated
- **Problem**: Roll from camera's view now maps to pitch in object space

**User expectation**: Pitch/Yaw/Roll should ALWAYS be relative to the camera's view direction, regardless of the object's current orientation.

---

## Solution: Quaternion-Based Camera-Relative Rotation

### Core Concept

Instead of rotating around the object's local axes, we rotate around the **camera's world-space axes**:

1. Get camera orientation as quaternion
2. Extract camera's world-space axes (right, up, forward)
3. Build rotation quaternions around these camera axes
4. Apply to object orientation (pre-multiply for world-space effect)

### Why Quaternions?

| Advantage | Description |
|-----------|-------------|
| **No Gimbal Lock** | Quaternions avoid Euler singularities |
| **Portable** | Maps directly to Unreal `FQuat`, Unity `Quaternion` |
| **Clean Composition** | Multiply and normalize - no matrix decomposition |
| **Numeric Stability** | Easy normalization prevents drift |

---

## Universal Algorithm (Engine-Agnostic)

### Pseudocode

```pseudo
function CameraRelativeRotate(q_obj, q_cam, pitch, yaw, roll):
    # Normalize inputs
    q_obj = Normalize(q_obj)
    q_cam = Normalize(q_cam)

    # Extract camera world axes by rotating basis vectors
    camRight   = RotateVector(q_cam, Vec3(1, 0, 0))
    camUp      = RotateVector(q_cam, Vec3(0, 1, 0))
    camForward = RotateVector(q_cam, Vec3(0, 0, -1))  # -Z for Houdini/Unreal

    # Build axis-angle quaternions about camera axes
    qPitch = QuatFromAxisAngle(camRight,   pitch)
    qYaw   = QuatFromAxisAngle(camUp,      yaw)
    qRoll  = QuatFromAxisAngle(camForward, roll)

    # Compose: Roll * Pitch * Yaw (order affects feel)
    q_delta_world = Normalize(qRoll * qPitch * qYaw)

    # Apply to object (pre-multiply = world-space rotation)
    q_obj_new = Normalize(q_delta_world * q_obj)

    return q_obj_new
```

### Input/Output Specification

| Parameter | Type | Description |
|-----------|------|-------------|
| `q_obj` | Quaternion | Object orientation (Object→World) |
| `q_cam` | Quaternion | Camera orientation (Camera→World) |
| `pitch` | float | Rotation angle about camera right axis (degrees) |
| `yaw` | float | Rotation angle about camera up axis (degrees) |
| `roll` | float | Rotation angle about camera forward axis (degrees) |
| **Return** | Quaternion | New object orientation |

### Required Math Operations

Any engine must provide:

1. `QuatFromAxisAngle(axis, angle)` → Quaternion
2. `Mul(qA, qB)` → Quaternion multiplication
3. `Normalize(q)` → Unit quaternion
4. `RotateVector(q, v)` → Rotate vector by quaternion

---

## Houdini Implementation

### File Location

`E:\AI\Houdini_MCP\spacemouse_network_pan\spacemouse_standalone.py`

### Key Function: `_apply_cargo_rotation()` (Lines 2377-2514)

```python
def _apply_cargo_rotation(self, msg, steps=1):
    """Rotate cargo box relative to camera view using quaternions."""
    cargo = hou.node('/obj/cargo')
    if cargo is None:
        return

    # 1. Get camera orientation as quaternion
    viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
    viewport = viewer.curViewport()
    cam = viewport.defaultCamera()

    # Detect C2W vs W2C convention (important for correct axis extraction)
    current_rot = cam.rotation()
    if not hasattr(self, "_cargo_cam_rot_is_c2w"):
        view_rot_c2w = hou.Matrix3(viewport.viewTransform())
        # Compare to determine convention
        diff_c2w = max_abs_diff(current_rot, view_rot_c2w)
        diff_w2c = max_abs_diff(current_rot.transposed(), view_rot_c2w)
        self._cargo_cam_rot_is_c2w = diff_c2w <= diff_w2c

    cam_rot_c2w = current_rot if self._cargo_cam_rot_is_c2w else current_rot.transposed()
    q_cam = hou.Quaternion(cam_rot_c2w)

    # 2. Get SpaceMouse input and scale to angles
    pitch_deg = get_cargo_axis('pitch') * self.cargo_rotate_speed * steps
    yaw_deg = get_cargo_axis('yaw') * self.cargo_rotate_speed * steps
    roll_deg = get_cargo_axis('roll') * self.cargo_rotate_speed * steps

    # 3. Extract camera world axes
    cam_right = q_cam.rotate(hou.Vector3(1, 0, 0))
    cam_up = q_cam.rotate(hou.Vector3(0, 1, 0))
    cam_forward = q_cam.rotate(hou.Vector3(0, 0, -1))  # -Z is forward

    # 4. Build axis-angle quaternions
    def quat_from_axis_angle(axis, angle_deg):
        q = hou.Quaternion()
        q.setToAngleAxis(angle_deg, axis.normalized())  # Houdini API
        return q

    q_pitch = quat_from_axis_angle(cam_right, pitch_deg)
    q_yaw = quat_from_axis_angle(cam_up, yaw_deg)
    q_roll = quat_from_axis_angle(cam_forward, roll_deg)

    # 5. Compose: Roll * Pitch * Yaw
    q_delta_world = q_roll * q_pitch * q_yaw
    q_delta_world = q_delta_world.normalized()

    # 6. Get current cargo orientation
    cargo_xform = cargo.worldTransform()
    q_obj = hou.Quaternion(hou.Matrix3(cargo_xform)).normalized()

    # 7. Apply: q_obj_new = q_delta_world * q_obj
    q_obj_new = (q_delta_world * q_obj).normalized()

    # 8. Convert back and update (preserving scale)
    new_rot_mat = q_obj_new.extractRotationMatrix3()
    scale = extract_scale(cargo_xform)  # Preserve original scale

    new_xform = hou.Matrix4(new_rot_mat)
    # Apply scale to rotation matrix
    for r in range(3):
        for c in range(3):
            new_xform.setAt(r, c, new_xform.at(r, c) * scale[r])

    # Preserve translation
    new_xform.setAt(3, 0, cargo_xform.at(3, 0))
    new_xform.setAt(3, 1, cargo_xform.at(3, 1))
    new_xform.setAt(3, 2, cargo_xform.at(3, 2))

    cargo.setWorldTransform(new_xform)
```

### Houdini API Reference

| Operation | Houdini API |
|-----------|-------------|
| Create quaternion from matrix | `hou.Quaternion(matrix3)` |
| Axis-angle to quaternion | `q.setToAngleAxis(angle_deg, axis_vector)` |
| Rotate vector by quaternion | `q.rotate(vector3)` |
| Quaternion multiply | `q1 * q2` |
| Normalize quaternion | `q.normalized()` |
| Quaternion to rotation matrix | `q.extractRotationMatrix3()` |

### Important Notes

1. **Houdini uses -Z as camera forward** (same as Unreal)
2. **`setToAngleAxis` takes angle first, then axis** (verified via testing)
3. **Scale must be preserved** when updating world transform
4. **C2W/W2C detection** is needed because `cam.rotation()` convention varies

---

## Unreal Blueprint Migration Guide

### Equivalent Nodes

| Houdini | Unreal Blueprint |
|---------|------------------|
| `hou.Quaternion()` | `Make Quat` / `FQuat` |
| `hou.Vector3()` | `Make Vector` / `FVector` |
| `q.rotate(v)` | `Rotate Vector` node |
| `q.setToAngleAxis(angle, axis)` | `Quat from Axis and Angle` |
| `q1 * q2` | `Combine Rotations` / `Multiply (Quaternion)` |
| `q.normalized()` | `Normalize` node |
| `cam.rotation()` | `Get Camera Rotation` → `To Quat (Rotator)` |
| `cargo.worldTransform()` | `Get Actor Transform` → `Get Rotation` → `To Quat` |

### Unreal Blueprint Pseudocode

```
Event Tick:
    // Get camera quaternion
    CamRotator = GetPlayerCameraManager().GetCameraRotation()
    Q_Cam = ToQuat(CamRotator)

    // Get input (from SpaceMouse or gamepad)
    Pitch = InputAxis("Pitch") * RotateSpeed * DeltaTime
    Yaw = InputAxis("Yaw") * RotateSpeed * DeltaTime
    Roll = InputAxis("Roll") * RotateSpeed * DeltaTime

    // Extract camera axes
    CamRight = RotateVector(Q_Cam, (1, 0, 0))
    CamUp = RotateVector(Q_Cam, (0, 0, 1))      // Unreal: Z-up
    CamForward = RotateVector(Q_Cam, (1, 0, 0)) // Unreal: X-forward

    // Build rotation quaternions
    Q_Pitch = QuatFromAxisAngle(CamRight, Pitch)
    Q_Yaw = QuatFromAxisAngle(CamUp, Yaw)
    Q_Roll = QuatFromAxisAngle(CamForward, Roll)

    // Compose
    Q_Delta = Normalize(Q_Roll * Q_Pitch * Q_Yaw)

    // Get current object rotation
    Q_Obj = ToQuat(CargoActor.GetActorRotation())

    // Apply
    Q_New = Normalize(Q_Delta * Q_Obj)

    // Set new rotation
    CargoActor.SetActorRotation(ToRotator(Q_New))
```

### Unreal Coordinate System Differences

| Aspect | Houdini | Unreal |
|--------|---------|--------|
| Up axis | Y | Z |
| Forward axis | -Z | X |
| Right axis | X | Y |
| Rotation order | Varies | Pitch(Y), Yaw(Z), Roll(X) |

**Adjustment needed**: When extracting camera axes in Unreal:
- `CamRight` = Rotate (0, 1, 0) by camera quat
- `CamUp` = Rotate (0, 0, 1) by camera quat
- `CamForward` = Rotate (1, 0, 0) by camera quat

---

## Configuration

### config.json Cargo Settings

```json
{
  "mode": "cargo",
  "cargo_axis_mapping": {
    "pitch": "rx",
    "yaw": "-rz",
    "roll": "-ry"
  },
  "cargo_speed": {
    "rotate": 5.0
  },
  "cargo_axis_multiplier": {
    "pitch": 1.0,
    "yaw": 1.0,
    "roll": 1.0
  }
}
```

---

## Shelf Tools

Location: `C:/Users/sherr/Documents/houdini21.0/toolbar/spacemouse.shelf`

### SM Cargo

Starts cargo mode and creates the cargo scene:

```python
import spacemouse_network_pan.spacemouse_standalone as sm
sm.setup_cargo_scene()  # Creates /obj/cargo with box
sm.start_receiver()
sm.set_mode("cargo")
sm.launch_reader(elevated=True, no_wait=True)
```

### SM Cargo Reset

Resets cargo to initial position/rotation:

```python
sm.reset_cargo()
```

---

## Testing Results

| Test | Result |
|------|--------|
| Initial rotation (pitch/yaw/roll) | Pass |
| 90° yaw then pitch | Pass - No gimbal lock |
| 180° rotation | Pass |
| Combined rotations | Pass |
| Scale preservation | Pass |
| Reset function | Pass |

---

## Mathematical Foundation

### Camera-Relative Rotation Formula

The key insight is that we want to rotate around camera axes in world space:

```
R_obj' = Delta_world * R_obj
```

Where `Delta_world` is built from camera axes:

```
Delta_world = R(camForward, roll) * R(camRight, pitch) * R(camUp, yaw)
```

This is equivalent to the conjugation formula:
```
R_obj' = R_obj * (R_cam^T * Delta_cam * R_cam)
```

But building Delta directly in world space is simpler and more numerically stable.

---

## Files Modified

| File | Changes |
|------|---------|
| `spacemouse_standalone.py` | Added `_apply_cargo_rotation()`, `_setup_cargo_scene()`, `_reset_cargo()`, cargo mode routing |
| `config.json` | Added cargo mode configuration |
| `spacemouse.shelf` | Added SM Cargo and SM Cargo Reset tools |

---

## Credits

- **Algorithm Design**: Codex (Quaternion math, camera-relative rotation)
- **Houdini Implementation**: Claude (API integration, testing)
- **Review**: Gemini (UX considerations)

---

## Next Steps for Unreal Migration

1. Create Blueprint Actor for "Cargo" object
2. Implement SpaceMouse input reading (HID or plugin)
3. Port the quaternion algorithm using Blueprint nodes
4. Test with Unreal camera system
5. Adjust for Unreal coordinate system (Z-up, X-forward)

**Key difference**: Unreal uses X-forward, Z-up. Adjust basis vectors accordingly when extracting camera axes.

---

## Peer Review Results (2026-01-10)

### Gemini Review - Documentation Completeness

| Item | Status | Notes |
|------|--------|-------|
| Math correctness | ✅ | Quaternion composition formula correct |
| Axis extraction | ✅ | Using `q.rotate(basis)` is robust |
| Scale preservation | ✅ | Correctly handles quaternion normalization losing scale |
| **Pivot Point** | ⚠️ | Document that object pivot should be centered, otherwise it will "swing" |
| **Deadzone** | ⚠️ | Input should be deadzone-filtered before rotation |
| Unreal X-Forward | ⚠️ | Explicitly note why `(1,0,0)` is Forward in Unreal |

### Codex Review - Algorithm Details

| Item | Status | Notes |
|------|--------|-------|
| Math concept | ✅ | World-space delta rotation with camera basis vectors |
| **C2W Prerequisite** | ⚠️ | `q_cam` MUST be Camera→World, or axes will be inverted |
| Composition order | ✅ | Any fixed order works; small deltas minimize difference |
| **Normalization** | ⚠️ | Normalize periodically to prevent drift |
| **Non-uniform Scale** | ⚠️ | Orthonormalize rotation basis first if scale present |
| **Avoid Euler storage** | ⚠️ | Store as quaternion; Euler reintroduces gimbal lock |

### Unreal Axis Adjustment (Codex Confirmed)

```cpp
// Unreal: Z-up, X-forward (Left-handed)
cam_forward = RotateVector(q_cam, FVector(1, 0, 0));  // +X
cam_right   = RotateVector(q_cam, FVector(0, 1, 0));  // +Y
cam_up      = RotateVector(q_cam, FVector(0, 0, 1));  // +Z
```

### Additional Recommendations

1. **Angular Velocity Approach**: For smoother feel, consider combining pitch/yaw/roll into a single angular velocity vector in camera space, then transform to world
2. **AddActorWorldRotation**: Unreal's built-in node is BP-idiomatic but explicit quaternion math guarantees no gimbal lock
3. **Sign Conventions**: Verify what "positive yaw/pitch/roll" means in target engine (handedness differs)
