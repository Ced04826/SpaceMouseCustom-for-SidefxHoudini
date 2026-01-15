# SpaceMouse Houdini 集成 - Cargo Attached Mode (Spring Arm Effect) 实现文档

## 项目概述

本项目为 Houdini 实现 SpaceMouse 控制的 "cargo_attached" 模式，类似游戏引擎中的 **Spring Arm Effect**（弹簧臂效果）。

### 核心目标
- Box (`/obj/cargo`) 跟随 Camera 保持固定距离
- Box 只使用 Y rotation 面向 Camera（潮汐锁定效果）
- 用户用键鼠控制 Camera，SpaceMouse 将来用于控制 Box 的 rotation

---

## 核心文件

| 文件 | 说明 |
|------|------|
| `D:\2026\Q1\Houdini\Plugins\spacemouse_network_pan\spacemouse_standalone.py` | 主代码文件 |
| `C:\Users\sherr\Documents\houdini21.0\toolbar\spacemouse.shelf` | Houdini Shelf 工具 |

### 关键函数

| 函数 | 行号 | 说明 |
|------|------|------|
| `_apply_cargo_attached` | ~2594 | Spring Arm 核心逻辑 |
| `_grab_cargo_attached` | ~2674 | 初始化 cargo 位置 |
| `reset_cargo()` | ~3389 | 重置 cargo 到相机前方 |
| `start_cargo_attached_mode()` | ~3381 | 启动 cargo_attached 模式 |
| `eventFilter()` | ~989 | Qt 事件过滤器，拦截 H/F 键 |

---

## 已实现功能

### 1. Spring Arm Effect ✅

**实现原理**：
```python
# 优先使用 /obj/sm_camera 节点
cam = hou.node('/obj/sm_camera')
if cam:
    cam_xform = cam.worldTransform()
    cam_pos = hou.Vector3(0, 0, 0) * cam_xform
    # Camera -Z 轴是 forward 方向
    p_fwd = hou.Vector3(0, 0, -1) * cam_xform
    cam_forward = (p_fwd - cam_pos).normalized()
else:
    # Fallback: 使用 viewport
    view_c2w = viewport.viewTransform().inverted()
    cam_pos = hou.Vector3(0, 0, 0) * view_c2w
    p_fwd = hou.Vector3(0, 0, 1) * view_c2w  # 注意：viewport 用 +Z
    cam_forward = (p_fwd - cam_pos).normalized()

# 水平方向（忽略 pitch）
cam_forward_flat = hou.Vector3(cam_forward[0], 0.0, cam_forward[2]).normalized()

# Box 位置 = Camera 位置 + forward * distance
distance = 10.0
box_pos = cam_pos + cam_forward_flat * distance
box_pos = hou.Vector3(box_pos[0], cam_pos[1], box_pos[2])  # 保持相机高度

# Yaw rotation 面向相机
yaw_deg = math.degrees(math.atan2(-cam_forward_flat[0], -cam_forward_flat[2]))
```

### 2. Home/Frame 按键处理 ✅

使用 Qt Event Filter 拦截 H/F 键，临时隐藏 cargo：
```python
def eventFilter(self, watched, event):
    if event.type() == QtCore.QEvent.KeyPress:
        key = event.key()
        if key == QtCore.Qt.Key_H or key == QtCore.Qt.Key_F:
            self._hide_cargo_for_home()
    return False

def _hide_cargo_for_home(self):
    cargo = hou.node('/obj/cargo')
    if cargo and cargo.isDisplayFlagSet():
        cargo.setDisplayFlag(False)
        QtCore.QTimer.singleShot(10, lambda: self._restore_cargo_visibility(cargo))
```

### 3. SpaceMouse Box Rotation (Additive) ✅

**实现日期**: 2026-01-12

**设计决策**: Additive Rotation
- SpaceMouse 旋转叠加在 yaw-lock 之上
- 最终旋转 = q_yaw_basis * q_user_local
- 当相机移动时，Box 保持面向相机，同时保留用户的相对旋转偏移

**核心代码位置**:
| 函数 | 行号 | 说明 |
|------|------|------|
| _compute_camera_relative_delta_quat | 2725-2820 | 计算相机相对 delta quaternion |
| _apply_cargo_attached | 3058-3137 | quaternion 旋转累积逻辑 |
| _apply_button_hotkeys | 1874-1879 | reset_rotation 特殊动作 |

**配置项** (config.json):
| 配置 | 默认值 | 说明 |
|------|--------|------|
| cargo_attached_rotate_speed | 5.0 | 旋转速度 |
| cargo_attached_axis_mapping | {pitch:rx, yaw:-rz, roll:-ry} | 轴映射 |
| cargo_attached_axis_multiplier | {pitch:1.0, yaw:1.0, roll:1.0} | 轴乘数 |
| button_hotkeys.cargo_attached.button_1 | reset_rotation | 重置旋转 |

**Quaternion 算法**:
1. 构建 yaw basis: q_yaw_basis = quat_from_axis_angle(world_up, yaw_deg)
2. 获取 SpaceMouse delta: q_delta_world = _compute_camera_relative_delta_quat()
3. 转换到 local: q_delta_local = q_yaw_basis_inv * q_delta_world * q_yaw_basis
4. 累积: _cargo_held_rotation = q_delta_local * _cargo_held_rotation
5. 最终旋转: q_world = q_yaw_basis * q_user_local

**Reset 功能**:
- SpaceMouse Button 1 按下时重置旋转
- 将 _cargo_held_rotation 和 _cargo_local_rotation 设为 None

---

## 踩过的坑（重要！）

### 坑 1：viewport.viewTransform() 不可靠

**问题**：3D Viewport Camera 不是 Houdini 中真实存在的 object，`viewTransform()` 返回的数据无法准确反映用户操作。

**症状**：
- 在 Front Orthographic 视图下，Camera 向上移动时，Box 向下移动（方向相反）
- 在 Perspective 视图下，Box 位置偏移

**解决方案**：创建真实的 Camera 节点 `/obj/sm_camera`，从节点直接读取 `worldTransform()`。

### 坑 2：forward 方向差异

**问题**：Camera 节点和 Viewport 使用不同的 forward 方向约定。

| 来源 | forward 方向 |
|------|-------------|
| Camera 节点 (`/obj/sm_camera`) | `-Z` |
| Viewport (`viewTransform().inverted()`) | `+Z` |

**解决方案**：
```python
if cam:  # Camera 节点
    p_fwd = hou.Vector3(0, 0, -1) * cam_xform
else:    # Viewport fallback
    p_fwd = hou.Vector3(0, 0, 1) * view_c2w
```

### 坑 3：Home 按键导致 cargo 位置错误

**问题**：按 H (Home) 时，Houdini 会考虑场景中所有可见物体（包括 cargo）来计算相机位置。这导致：
1. 先更新 cargo 位置到相机前方
2. Houdini 根据新的 cargo 位置重新计算相机位置
3. 相机移动后，cargo 位置变得不正确

**症状**：按 H 后，cargo 短暂出现在正确位置，随后跳到错误位置。

**解决方案**：
1. 使用 Qt Event Filter 拦截 H/F 按键
2. 临时关闭 cargo 的 Display Flag
3. 10ms 后恢复 Display Flag

### 坑 4：Skip-frames 逻辑导致 cargo 停止更新

**问题**：原代码有检测相机移动距离的逻辑：
```python
# 错误的逻辑 - 已删除
if cam_delta > 5.0:
    self._cargo_skip_frames = 3
    return  # 跳过更新
```

当相机快速移动时，这个逻辑会导致 cargo 停止跟随。

**解决方案**：完全删除 skip-frames 逻辑。

### 坑 5：importlib.reload 不更新实例方法

**问题**：使用 `importlib.reload(spacemouse_standalone)` 后，`hou.session._spacemouse_receiver` 实例仍然使用旧代码。

**解决方案**：在 shelf 工具中重新创建 receiver 实例，或者在模块级函数中直接实现逻辑（不依赖实例方法）。

---

## 用户使用流程

1. **创建 Camera 节点**：
   - `/obj` → Tab → Camera
   - 命名为 `sm_camera`

2. **设置 Viewport**：
   - Viewport 右键 → Look Through → `sm_camera`

3. **启动 cargo_attached 模式**：
   - 点击 Shelf → **Reload SM Cargo**
   - 或点击 **SM Cargo Attached**

4. **操作**：
   - 用键鼠控制 Camera（在 Viewport 中 tumble/pan/dolly）
   - Box 自动跟随 Camera 保持固定距离

---

## 后续要做的事情

### 1. SpaceMouse 控制 Box Rotation ✅

**目标**：让 SpaceMouse 控制 Box 的 rotation，而不是控制相机。

**状态**: Completed (2026-01-12) - Additive Mode 实现。

### 2. 可调参数 ✅

- `distance`：Box 与 Camera 的距离
- `height_offset`：Box 相对 Camera 的高度偏移
- `rotation_speed`：SpaceMouse rotation 灵敏度 (Implemented as cargo_attached_rotate_speed)
- `axis_mapping/multiplier`: Implemented

### 3. 模式切换

- 快捷键切换 cargo_attached 模式开关
- 状态指示（当前模式显示）

### 4. 用户体验优化

- **Lock to View 按钮**：自动将 Viewport 链接到 `sm_camera`
- **自动创建 Camera**：如果 `/obj/sm_camera` 不存在，自动创建

### 5. Unreal Engine 移植

算法（四元数相对旋转）是引擎无关的，可以直接移植到 Blueprints。

#### 坐标系差异

| 引擎 | Forward | Up | Right | 旋转顺序 |
|------|---------|----|----|----------|
| Houdini | -Z | +Y | +X | Y-up, right-handed |
| Unreal | +X | +Z | +Y | Z-up, left-handed |

#### 核心算法移植指南

**Step 1: 获取相机朝向 (Camera Orientation)**
```cpp
// Houdini: q_cam = hou.Quaternion(cam_rot_c2w)
// Unreal Blueprint:
FRotator CameraRotation = PlayerCameraManager->GetCameraRotation();
FQuat CameraQuat = CameraRotation.Quaternion();
```

**Step 2: 提取相机轴向量**
```cpp
// Houdini: cam_right = q_cam.rotate((1,0,0))
//          cam_up = q_cam.rotate((0,1,0))
//          cam_forward = q_cam.rotate((0,0,-1))
// Unreal:
FVector CamForward = CameraQuat.GetForwardVector();  // +X in Unreal
FVector CamRight = CameraQuat.GetRightVector();      // +Y in Unreal
FVector CamUp = CameraQuat.GetUpVector();            // +Z in Unreal
```

**Step 3: 构建 Yaw Basis (面向相机)**
```cpp
// Houdini: yaw_deg = atan2(-cam_forward_flat[0], -cam_forward_flat[2])
// Unreal:
FVector FlatForward = FVector(CamForward.X, CamForward.Y, 0).GetSafeNormal();
float YawRad = FMath::Atan2(FlatForward.Y, FlatForward.X);
FQuat YawBasis = FQuat(FVector::UpVector, YawRad);
FQuat YawBasisInv = YawBasis.Inverse();
```

**Step 4: SpaceMouse 输入转换为 Delta Quaternion**
```cpp
// 从 SpaceMouse rx, ry, rz 构建 axis-angle quaternions
FQuat QPitch = FQuat(CamRight, FMath::DegreesToRadians(PitchDeg));
FQuat QYaw = FQuat(CamUp, FMath::DegreesToRadians(YawDeg));
FQuat QRoll = FQuat(CamForward, FMath::DegreesToRadians(RollDeg));

// 组合顺序 (与 Houdini 一致)
FQuat DeltaWorld = QRoll * QPitch * QYaw;
DeltaWorld.Normalize();
```

**Step 5: World-to-Local 转换**
```cpp
// Houdini: q_delta_local = q_yaw_basis_inv * q_delta_world * q_yaw_basis
FQuat DeltaLocal = YawBasisInv * DeltaWorld * YawBasis;
DeltaLocal.Normalize();
```

**Step 6: 累积旋转**
```cpp
// Houdini: self._cargo_held_rotation = q_delta_local * self._cargo_held_rotation
HeldRotation = DeltaLocal * HeldRotation;
HeldRotation.Normalize();
```

**Step 7: 最终世界旋转**
```cpp
// Houdini: q_world = q_yaw_basis * q_user_local
FQuat WorldRot = YawBasis * HeldRotation;
WorldRot.Normalize();
CargoActor->SetActorRotation(WorldRot);
```
#### Unreal 注意事项

1. **Quaternion 乘法顺序**: Unreal 和 Houdini 的 quaternion 乘法顺序相同 (Q1 * Q2 表示先 Q2 后 Q1)
2. **Axis-Angle 构造**: Unreal 的 FQuat(Axis, AngleRad) 与 Houdini 的 setToAngleAxis(AngleDeg, Axis) 参数顺序不同
3. **角度单位**: Unreal 默认使用弧度，需要用 FMath::DegreesToRadians() 转换
4. **Normalize**: 每次 quaternion 运算后都应该 normalize 防止数值漂移

#### Blueprint 实现建议

1. 创建 ActorComponent 存储 HeldRotation 状态
2. 在 Tick 中读取 SpaceMouse 输入并更新旋转
3. 使用 FQuat 而非 FRotator 进行所有旋转计算
4. Spring Arm Component 可以直接用于位置跟随

---

## 调试技巧

### 检查运行中的代码版本
```python
import hou.session, inspect
r = getattr(hou.session, '_spacemouse_receiver', None)
if r:
    src = inspect.getsource(r._apply_cargo_attached)
    print("Has sm_camera check:", "/obj/sm_camera" in src)
```

### 检查 Camera 和 Cargo 位置
```python
import hou
cam = hou.node('/obj/sm_camera')
cargo = hou.node('/obj/cargo')
if cam and cargo:
    cam_pos = hou.Vector3(0,0,0) * cam.worldTransform()
    cargo_pos = cargo.parmTuple('t').eval()
    print(f"Camera: {cam_pos}")
    print(f"Cargo: {cargo_pos}")
    print(f"Distance: {(hou.Vector3(cargo_pos) - cam_pos).length()}")
```

### MCP 调试
```python
# 通过 Houdini MCP 执行 Python 代码
mcp__houdini__execute_houdini_code(code="...")

# 捕获 Viewport 截图
mcp__houdini__capture_pane(pane_type="viewport")
```

---

## 版本历史

| 日期 | 修改内容 |
|------|----------|
| 2026-01-11 | 实现 Spring Arm Effect，使用 /obj/sm_camera 节点 |
| 2026-01-11 | 修复 Home/Frame 按键问题，使用 Qt Event Filter |
| 2026-01-11 | 修复 forward 方向问题（Camera -Z, Viewport +Z） |
| 2026-01-11 | 删除 skip-frames 逻辑 |
| 2026-01-12 | 实现 SpaceMouse Box Rotation (Additive mode)，Codex code review LGTM |
| 2026-01-13 | 添加详细的 Unreal Engine 移植指南（7步算法、C++代码示例） |
