"""Root motion utilities for MTAR import.

Post-bake step: move root-motion bone FCurves (location + rotation) to the
armature-object level so that pose libraries work at any point in an animation
without requiring manual repositioning of hands and feet.

Entry point
-----------
``apply_root_motion_to_object_framebyframe(custom_rig, baked_actions, layout_action, track_mapping)``

This must be called **after** ``bake_constraints_and_decimate_fcurves()`` has
completed and returned its ``actions_created`` list.

Coordinate-space handling
--------------------------
The baked ``pose.bones[X].location`` / ``rotation_quaternion`` FCurves are in
**bone-local pose space** (relative to parent and rest pose).

The correct object transform at each frame is::

    M_obj = arm_world_orig @ bone_armspace_matrix

where ``arm_world_orig`` is the armature's world transform **before** any root
motion FCurves are written, and ``bone_armspace_matrix`` is the bone's evaluated
matrix in armature space (``pose_bone.matrix`` after depsgraph evaluation).  This
formula ensures that when the root bone FCurves are deleted and it returns to rest
pose, its world position equals its original animated world position::

    root_bone.world = M_obj @ rest = (arm_world_orig @ bone_armspace) @ rest
                    = arm_world_orig @ (bone_armspace @ rest) / rest
                    = arm_world_orig @ (rest @ basis) / rest  [depends on basis only, rest cancels]

For world-space IK target bones (parentless, identity rest), compensation is::

    new_pose_value = M_obj⁻¹ @ old_pose_value

Blender version compatibility
------------------------------
FCurve creation/deletion uses the existing utilities
``ensure_action_fcurve`` / ``find_action_fcurve`` / ``remove_action_fcurve``
from ``py_utilities.utilities_blender_animation``.  These handle both the
pre-4.4 ``action.fcurves`` API and the 4.4+ slot/channelbag API transparently.

Object-level FCurves are always written to the ``MTAR_ARMATURE_SLOT_NAME``
slot (the same slot that holds the baked bone FCurves) so that Blender
evaluates them together when the action is active on the custom rig.
"""

from typing import Dict, List, Optional, Set, Tuple

import bpy
from bpy.types import Context
from mathutils import Matrix, Quaternion, Vector

from ..py_fox.fox_gani_enums import SegmentType
from ..py_foxwrap.foxwrap_metadata import iter_track_properties, parse_track_metadata_generic
from ..py_foxwrap.foxwrap_mapping import BoneParameters
from ..py_utilities.utilities_logging import Debug
from ..py_utilities.utilities_blender_animation import (
    assign_action_to_datablock,
    remove_action_from_datablock,
    ensure_action_fcurve,
    find_action_fcurve,
    remove_action_fcurve,
    MTAR_ARMATURE_SLOT_NAME,
)

# ---------------------------------------------------------------------------
# Root-motion detection helpers
# ---------------------------------------------------------------------------

# DIFF segment types that characterise a root-motion track.
_DIFF_SEGMENT_TYPES: frozenset = frozenset((SegmentType.QUAT_DIFF, SegmentType.VECTOR_DIFF))

# Custom property key stored on the armature object.  Encodes the 4×4
# matrix_world the armature had BEFORE root motion FCurves were written.
# Used by the exporter to cancel the arm_world_orig factor from M_obj.
MTAR_ROOT_MOTION_ARM_WORLD_PROP: str = "mtar_root_motion_arm_world"


def find_root_motion_track_info(layout_action: bpy.types.Action) -> Optional[Tuple[int, str]]:
    """Return ``(track_idx, fox_track_name)`` for the root-motion track, or ``None``.

    A root-motion track is one whose *all* segment types are DIFF variants
    (``QUAT_DIFF`` or ``VECTOR_DIFF``).  This mirrors the ``is_root_motion_track()``
    check from ``foxwrap_misc`` but operates on the layout-action metadata
    (available at post-bake time when ``TrackUnitWrapper`` objects are gone).

    Logs a warning if multiple candidates are found; in that case the first is
    returned (same heuristic as the GANI writer).
    """
    candidates: List[Tuple[int, str]] = []

    for track_idx, fox_name, metadata_str in iter_track_properties(layout_action):
        parsed = parse_track_metadata_generic(metadata_str)
        if not parsed:
            continue
        seg_types: List[SegmentType] = parsed.get("segment_types") or []
        # Must have at least one segment and ALL must be DIFF types
        if seg_types and all(st in _DIFF_SEGMENT_TYPES for st in seg_types):
            candidates.append((track_idx, fox_name))

    if not candidates:
        return None

    if len(candidates) > 1:
        names = [name for _, name in candidates]
        Debug.log_warning(
            f"find_root_motion_track_info: Multiple root-motion candidates: "
            f"{names}.  Using first: '{candidates[0][1]}'"
        )

    return candidates[0]


def _blender_bone_name_for_fox(fox_name: str, track_mapping: Optional[Dict[str, BoneParameters]]) -> str:
    """Return the Blender bone name mapped to *fox_name*, or *fox_name* as fallback."""
    if track_mapping:
        bp = track_mapping.get(fox_name)
        if bp and bp.track_name:
            return bp.track_name
    return fox_name


# ---------------------------------------------------------------------------
# FCurve path helpers
# ---------------------------------------------------------------------------

def _bone_loc_path(bone_name: str) -> str:
    return f'pose.bones["{bone_name}"].location'


def _bone_rot_path(bone_name: str) -> str:
    return f'pose.bones["{bone_name}"].rotation_quaternion'


def _collect_keyframe_times(fcurves: List[Optional["bpy.types.FCurve"]]) -> Set[float]:
    """Return the union of all keyframe ``co[0]`` times across *fcurves*."""
    times: Set[float] = set()
    for fc in fcurves:
        if fc is None:
            continue
        for kp in fc.keyframe_points:
            times.add(kp.co[0])
    return times


# ---------------------------------------------------------------------------
# FCurve write / delete helpers
# ---------------------------------------------------------------------------

def _write_object_fcurves(
    custom_rig: bpy.types.Object,
    action: bpy.types.Action,
    frame_transforms: Dict[float, Tuple[Vector, Quaternion]],
    interpolation: str = 'LINEAR',
) -> bool:
    """Write object-level ``location`` and ``rotation_quaternion`` FCurves.

    *frame_transforms* maps frame time → (location Vector, rotation Quaternion).
    Keyframes are inserted in frame order.  Returns True if any FCurves were written.
    """
    if not frame_transforms:
        return False

    # Ensure all 7 FCurves exist (loc x3 + rot x4)
    loc_fcs = []
    for i in range(3):
        fc = ensure_action_fcurve(
            action, "location", i,
            datablock=custom_rig,
            slot_name=MTAR_ARMATURE_SLOT_NAME,
        )
        if fc is None:
            Debug.log_warning(f"  _write_object_fcurves: Could not ensure location[{i}]")
            return False
        fc.keyframe_points.clear()
        loc_fcs.append(fc)

    rot_fcs = []
    for i in range(4):
        fc = ensure_action_fcurve(
            action, "rotation_quaternion", i,
            datablock=custom_rig,
            slot_name=MTAR_ARMATURE_SLOT_NAME,
        )
        if fc is None:
            Debug.log_warning(f"  _write_object_fcurves: Could not ensure rotation_quaternion[{i}]")
            return False
        fc.keyframe_points.clear()
        rot_fcs.append(fc)

    # Insert keyframes in sorted frame order
    for t in sorted(frame_transforms.keys()):
        loc, rot = frame_transforms[t]
        for i in range(3):
            kp = loc_fcs[i].keyframe_points.insert(t, loc[i], options={"FAST"})
            kp.interpolation = interpolation
        for i in range(4):
            kp = rot_fcs[i].keyframe_points.insert(t, rot[i], options={"FAST"})
            kp.interpolation = interpolation

    # Finalize
    for fc in loc_fcs + rot_fcs:
        fc.update()

    return True


def _delete_bone_fcurves(action: bpy.types.Action, bone_name: str) -> int:
    """Delete all location and rotation_quaternion FCurves for *bone_name*.

    Returns the number of FCurves removed.
    """
    loc_path = _bone_loc_path(bone_name)
    rot_path = _bone_rot_path(bone_name)
    removed = 0

    for path, count in ((loc_path, 3), (rot_path, 4)):
        for i in range(count):
            fc = find_action_fcurve(action, path, i)
            if fc is not None:
                remove_action_fcurve(action, fc)
                removed += 1

    return removed


def _write_ik_bone_fcurves_from_basis(
    action: bpy.types.Action,
    bone_name: str,
    frame_data: Dict[float, Tuple[Vector, Quaternion]],
) -> None:
    """Overwrite IK bone FCurve keypoints with pre-recorded matrix_basis values.

    *frame_data* maps frame time → (location, quaternion) obtained by reading
    ``pose_bone.matrix_basis`` after applying the world-space delta to the live pose.
    Call ``_densify_bone_fcurves`` first so keypoints exist at every target time;
    this function only calls ``_set_keypoint_value`` and does not insert new points.
    """
    loc_fcs = [find_action_fcurve(action, _bone_loc_path(bone_name), i) for i in range(3)]
    rot_fcs = [find_action_fcurve(action, _bone_rot_path(bone_name), i) for i in range(4)]
    has_loc = any(fc is not None for fc in loc_fcs)
    has_rot = any(fc is not None for fc in rot_fcs)

    for t in sorted(frame_data.keys()):
        loc, rot = frame_data[t]
        if has_loc:
            for i, fc in enumerate(loc_fcs):
                if fc is not None:
                    _set_keypoint_value(fc, t, loc[i])
        if has_rot:
            for i, fc in enumerate(rot_fcs):
                if fc is not None:
                    _set_keypoint_value(fc, t, rot[i])

    for fc in loc_fcs + rot_fcs:
        if fc is not None:
            fc.update()


def clear_rest_pose_from_bone(pose_bone: bpy.types.PoseBone) -> None:
    """Place the given pose bone at the armature origin (in world space).

    This sets the bone's armature-space matrix to identity, which makes the
    bone's world transform match its parent armature's world transform.

    This is intended for debugging root motion and does NOT keyframe any values.
    """
    pose_bone.matrix = Matrix.Identity(4)


def compute_rest_inverse_delta(
    context: Context,
    arm: bpy.types.Object,
    pose_bone: bpy.types.PoseBone,
) -> tuple[Matrix, Matrix]:
    """Apply rest pose inversion and return the resulting world delta.

    Returns:
        (before_world, delta_world)

    The returned delta maps the bone's world transform before the inversion to
    its world transform after the inversion.
    """
    before_world = arm.matrix_world @ pose_bone.matrix
    clear_rest_pose_from_bone(pose_bone)
    context.view_layer.update()
    after_world = arm.matrix_world @ pose_bone.matrix
    delta_world = after_world @ before_world.inverted()
    return before_world, delta_world


def _move_ik_bones_by_delta(custom_rig: bpy.types.Object, ik_bone_names: List[str], delta_world: Matrix) -> None:
    """Apply a world-space delta transform to a set of IK bones.

    *delta_world* is a 4x4 matrix that maps the pre-move world transform to the
    desired post-move world transform.  We convert it back into the bone's
    armature-space pose matrix and apply it directly.
    """
    arm_world = custom_rig.matrix_world
    arm_world_inv = arm_world.inverted()
    for ik_name in ik_bone_names:
        ik_bone = custom_rig.pose.bones.get(ik_name)
        if ik_bone is None:
            continue
        pre_world = arm_world @ ik_bone.matrix
        post_world = delta_world @ pre_world
        ik_bone.matrix = arm_world_inv @ post_world
        Debug.log(f"  _move_ik_bones_by_delta: '{ik_name}' > \n{delta_world}\n"
                  f"arm_world:\n({arm_world}),\n"
                  f"arm_world_inv:\n({arm_world_inv}),\n"
                  f"pre_world:\n({pre_world}),\n"
                  f"post_world:\n({post_world})\n"
                  f"final bone matrix:\n({ik_bone.matrix})")


def _key_bone_to_arm_origin(
    custom_rig: bpy.types.Object,
    action: bpy.types.Action,
    bone_name: str,
) -> None:
    """Keep the root bone's keyframe timing while cancelling its rest pose.

    This preserves the set of frames that contain keyframes (which is used
    by the exporter as a reference) while making the root bone sit at the
    armature origin (rest pose is effectively undone).
    """
    pose_bone = custom_rig.pose.bones.get(bone_name)
    if pose_bone is None:
        Debug.log_warning(f"  _key_bone_to_arm_origin: Bone '{bone_name}' not found in rig '{custom_rig.name}'")
        return

    if pose_bone.parent:
        Debug.log(f"  _key_bone_to_arm_origin: Bone '{bone_name}' has parent '{pose_bone.parent.name}'; results may be affected by parent animation.")

    # Determine the correction that cancels the rest pose, which places the bone
    # exactly at the armature origin in armature space.
    rest_inv = pose_bone.bone.matrix_local.inverted()
    corrected_loc, corrected_rot, _ = rest_inv.decompose()

    loc_fcs = [
        find_action_fcurve(action, _bone_loc_path(bone_name), i) for i in range(3)
    ]
    rot_fcs = [
        find_action_fcurve(action, _bone_rot_path(bone_name), i) for i in range(4)
    ]

    frame_times = sorted(_collect_keyframe_times(loc_fcs + rot_fcs))
    if not frame_times:
        return

    # Preserve existing interpolation modes (from the first keyframe if present)
    def _get_interpolation_at_frame(fc: Optional["bpy.types.FCurve"], frame: float) -> Optional[str]:
        if fc is None:
            return None
        for kp in fc.keyframe_points:
            if abs(kp.co[0] - frame) < 0.001:
                return kp.interpolation
        return None

    first_frame = frame_times[0]
    loc_interps = [_get_interpolation_at_frame(fc, first_frame) for fc in loc_fcs]
    rot_interps = [_get_interpolation_at_frame(fc, first_frame) for fc in rot_fcs]

    # Now write the corrected pose values into existing keyframes (keeps keyframe timing)
    prev_rot: Optional[Quaternion] = None
    for t in frame_times:
        for i, fc in enumerate(loc_fcs):
            if fc is None:
                continue
            _set_keypoint_value(fc, t, corrected_loc[i], interpolation=loc_interps[i])

        if any(fc is not None for fc in rot_fcs):
            new_rot = corrected_rot.copy()
            if prev_rot is not None:
                new_rot.make_compatible(prev_rot)
            prev_rot = new_rot.copy()
            for i, fc in enumerate(rot_fcs):
                if fc is None:
                    continue
                _set_keypoint_value(fc, t, new_rot[i], interpolation=rot_interps[i])

    for fc in loc_fcs + rot_fcs:
        if fc is not None:
            fc.update()


# ---------------------------------------------------------------------------
# IK compensation helpers
# ---------------------------------------------------------------------------

def _set_keypoint_value(
    fc: "bpy.types.FCurve",
    frame: float,
    value: float,
    interpolation: Optional[str] = None,
) -> None:
    """Overwrite the value of the existing keyframe point nearest to *frame*.

    Shifts Bezier handles by the same delta so the curve shape is preserved.
    This is a no-op if no keyframe point is found within ±0.001 frames.

    If *interpolation* is provided, the keyframe point's interpolation is set.
    """
    for kp in fc.keyframe_points:
        if abs(kp.co[0] - frame) < 0.001:
            delta = value - kp.co[1]
            kp.co[1] = value
            # Shift handles (stored as absolute y positions) by the same delta
            kp.handle_left = (kp.handle_left[0], kp.handle_left[1] + delta)
            kp.handle_right = (kp.handle_right[0], kp.handle_right[1] + delta)
            if interpolation is not None:
                kp.interpolation = interpolation
            break


def _densify_bone_fcurves(
    action: bpy.types.Action,
    bone_name: str,
    target_frame_times: List[float],
) -> int:
    """Insert keyframe points in IK bone FCurves at *target_frame_times* where missing.

    Evaluates each FCurve at the target time using ``fc.evaluate(t)`` (which reads
    the existing curve value without disturbing the shape) and inserts a new
    keypoint at that time.  This ensures ``_compensate_one_bone`` can overwrite a
    correct value at every target frame rather than silently skipping frames.

    Returns the total number of keyframe points inserted across all channels.
    """
    loc_path = _bone_loc_path(bone_name)
    rot_path = _bone_rot_path(bone_name)
    fcs = (
        [find_action_fcurve(action, loc_path, i) for i in range(3)]
        + [find_action_fcurve(action, rot_path, i) for i in range(4)]
    )

    inserted = 0
    for fc in fcs:
        if fc is None:
            continue
        for t in target_frame_times:
            already_keyed = any(abs(kp.co[0] - t) < 0.001 for kp in fc.keyframe_points)
            if already_keyed:
                continue
            value = fc.evaluate(t)
            kp = fc.keyframe_points.insert(t, value, options={"FAST"})
            kp.interpolation = 'LINEAR'
            inserted += 1

    for fc in fcs:
        if fc is not None:
            fc.update()

    return inserted


def _compensate_one_bone(
    custom_rig: bpy.types.Object,
    action: bpy.types.Action,
    bone_name: str,
    obj_transforms: Dict[float, Tuple[Vector, Quaternion]],
    pre_move_world: Dict[float, Tuple[Vector, Quaternion]],
    effective_rest: Matrix,
) -> None:
    """Apply world-space compensation to a single IK bone in *action*.

    Uses the pre-computed world-space transforms from before root motion was moved
    (``pre_move_world``) and the object-level transforms (``obj_transforms``) to
    compute the correct new pose-basis values.

    The correct formula for a parentless bone with rest matrix ``R``::

        old_world = arm_world_orig @ R @ old_basis   (captured in pre_move_world)
        We need: M_obj @ R @ new_basis = old_world  (preserve world position)
        Therefore: new_basis = R⁻¹ @ M_obj⁻¹ @ old_world

    Which simplifies to ``new_basis = rest⁻¹ @ M_obj⁻¹ @ old_world``.

    Keyframe points at *all* times in ``pre_move_world`` are overwritten.  Call
    ``_densify_bone_fcurves`` first to ensure FCurve keypoints exist at every
    target frame time before this function runs.
    """
    loc_path = _bone_loc_path(bone_name)
    rot_path = _bone_rot_path(bone_name)

    bone_loc_fcs = [find_action_fcurve(action, loc_path, i) for i in range(3)]
    bone_rot_fcs = [find_action_fcurve(action, rot_path, i) for i in range(4)]

    has_loc = any(fc is not None for fc in bone_loc_fcs)
    has_rot = any(fc is not None for fc in bone_rot_fcs)

    if not has_loc and not has_rot:
        Debug.log(f"    _compensate_one_bone: No FCurves for '{bone_name}' — skipping")
        return

    # Use pre-computed world transforms for this bone
    if not pre_move_world:
        Debug.log(f"    _compensate_one_bone: No pre-move world transforms for '{bone_name}' — skipping")
        return

    # Get the bone's rest matrix (constant, needed for non-identity rest)
    pose_bone = custom_rig.pose.bones.get(bone_name)
    if pose_bone is None:
        Debug.log_warning(f"    _compensate_one_bone: Bone '{bone_name}' not found — skipping")
        return

    # The rest matrix is a constant property of the armature skeleton; compute
    # its inverse once outside the loop for efficiency.
    rest_matrix_inv = effective_rest.inverted()

    frame_times = sorted(pre_move_world.keys())
    Debug.log(f"    Compensating IK bone '{bone_name}' over {len(frame_times)} frame times")

    prev_rot: Optional[Quaternion] = None
    for t in frame_times:

        # Get the object transform at this frame
        obj_data = obj_transforms.get(t)
        if obj_data is None:
            # Should not happen after dense evaluation, but guard defensively.
            Debug.log_warning(
                f"    _compensate_one_bone: No obj_transform at t={t} for '{bone_name}' — skipping frame"
            )
            continue
        obj_loc, obj_rot = obj_data
        m_obj = Matrix.LocRotScale(obj_loc, obj_rot, Vector((1.0, 1.0, 1.0)))
        m_obj_inv = m_obj.inverted()

        # Get the bone's pre-move world-space transform (arm_world_orig @ bone_armspace).
        world_loc, world_rot = pre_move_world[t]
        old_world = Matrix.LocRotScale(world_loc, world_rot, Vector((1.0, 1.0, 1.0)))

        # new_basis = rest⁻¹ @ M_obj⁻¹ @ old_world
        new_basis = rest_matrix_inv @ m_obj_inv @ old_world

        if has_loc:
            new_loc = new_basis.to_translation()
            for i, fc in enumerate(bone_loc_fcs):
                if fc is None:
                    continue
                _set_keypoint_value(fc, t, new_loc[i])

        if has_rot:
            new_rot = new_basis.to_quaternion()
            # Ensure quaternion sign consistency across frames
            if prev_rot is not None:
                new_rot.make_compatible(prev_rot)
            prev_rot = new_rot.copy()
            for i, fc in enumerate(bone_rot_fcs):
                if fc is None:
                    continue
                _set_keypoint_value(fc, t, new_rot[i])

    # Notify Blender that the FCurves have been modified
    all_fcs = bone_loc_fcs + bone_rot_fcs
    for fc in all_fcs:
        if fc is not None:
            fc.update()


def _get_ik_bone_names(track_mapping: Dict[str, BoneParameters], root_bone_name: str) -> List[str]:
    """Return Blender bone names for IK targets (``space_l=world``) excluding root."""
    ik_names: List[str] = []
    for fox_name, bone_params in track_mapping.items():
        blender_name = _blender_bone_name_for_fox(fox_name, track_mapping)
        if blender_name == root_bone_name:
            continue
        space_l = bone_params.space_l
        if space_l and isinstance(space_l, dict) and space_l.get("space") == "WORLD":
            if blender_name not in ik_names:
                ik_names.append(blender_name)
    return ik_names


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_root_motion_to_object_framebyframe(
    custom_rig: bpy.types.Object,
    baked_actions: List[bpy.types.Action],
    layout_action: bpy.types.Action,
    track_mapping: Optional[Dict[str, BoneParameters]] = None,
) -> bool:
    """Apply root motion using frame-by-frame live pose manipulation.

    Mirrors the single-frame debug operator
    ``MTAR_OT_DebugRootMotionRestInverseWithIKAndArmature`` but runs across
    every keyframe time and writes the recorded results as FCurves.

    Uses a two-phase approach to prevent NLA contamination between actions:

    **Phase 1 — Record all actions (NLA isolated)**

    All NLA tracks are muted before the recording loop so that ``frame_set``
    evaluates *only* the directly assigned action.  For each action and frame T:

    1. Reset ``arm.matrix_world`` to ``arm_world_orig`` and call ``frame_set(T)``
       so all pose bone FCurves are freshly evaluated.
    2. Capture ``before_world = arm.matrix_world @ root_bone.matrix`` — the root
       bone's world-space transform, which becomes the new armature object transform.
    3. Zero the root bone to rest pose (``clear_rest_pose_from_bone``).
    4. Compute ``delta_world = after_world @ before_world⁻¹``.
    5. Apply ``delta_world`` to all IK bones via ``_move_ik_bones_by_delta`` to
       preserve their world-space positions.
    6. Record ``obj_transforms[T] = decompose(before_world)`` and
       ``ik_recorded[bone][T] = decompose(ik_bone.matrix_basis)`` for each IK bone.

    After all actions are recorded, NLA track mute states are restored and the
    armature is reset to its original world transform.

    **Phase 2 — Apply all recorded data (pure FCurve writes)**

    No ``frame_set`` calls here — only FCurve manipulation:

    7. Write ``obj_transforms`` as object-level FCurves.
    8. Zero root bone FCurves (``_key_bone_to_arm_origin``).
    9. Densify IK bone FCurves to the dense frame set, then overwrite them with
       the recorded ``matrix_basis`` values.

    After all actions are applied, the armature is moved to the first recorded
    position of the first action (viewport convenience).
    """
    if not custom_rig or not layout_action or not baked_actions:
        Debug.log("apply_root_motion_to_object_framebyframe: Missing required args — skipping")
        return False

    root_info = find_root_motion_track_info(layout_action)
    if root_info is None:
        Debug.log(
            "apply_root_motion_to_object_framebyframe: No root-motion track detected — skipping"
        )
        return False

    _, fox_track_name = root_info
    root_bone_name = _blender_bone_name_for_fox(fox_track_name, track_mapping)

    Debug.log(
        f"apply_root_motion_to_object_framebyframe: root-motion bone = '{root_bone_name}' "
        f"(fox: '{fox_track_name}')"
    )

    if root_bone_name not in custom_rig.pose.bones:
        Debug.log_warning(
            f"apply_root_motion_to_object_framebyframe: Bone '{root_bone_name}' not found "
            f"in rig '{custom_rig.name}' — skipping"
        )
        return False

    ik_bone_names: List[str] = []
    if track_mapping:
        ik_bone_names = _get_ik_bone_names(track_mapping, root_bone_name)
        if ik_bone_names:
            Debug.log(f"  IK bones to compensate ({len(ik_bone_names)}): {ik_bone_names}")

    custom_rig.rotation_mode = 'QUATERNION'
    arm_world_orig: Matrix = custom_rig.matrix_world.copy()
    custom_rig[MTAR_ROOT_MOTION_ARM_WORLD_PROP] = [v for row in arm_world_orig for v in row]
    Debug.log(
        f"  arm_world_orig = loc={arm_world_orig.to_translation()}, "
        f"rot={arm_world_orig.to_quaternion()}"
    )

    scene = bpy.context.scene
    original_frame = scene.frame_current

    # -----------------------------------------------------------------------
    # Phase 1 — Record all actions with NLA isolated
    # Mute every NLA track so frame_set() evaluates only the directly assigned
    # action and cannot be contaminated by strips from other (already-modified)
    # actions.
    # -----------------------------------------------------------------------
    nla_mute_states: Dict[str, bool] = {}
    if custom_rig.animation_data and custom_rig.animation_data.nla_tracks:
        for nla_track in custom_rig.animation_data.nla_tracks:
            nla_mute_states[nla_track.name] = nla_track.mute
            nla_track.mute = True
    if nla_mute_states:
        Debug.log(f"  [fbf] Muted {len(nla_mute_states)} NLA track(s) for isolated recording")

    # recorded_data: action → (obj_transforms, ik_recorded, dense_times)
    recorded_data: Dict[
        "bpy.types.Action",
        Tuple[
            Dict[float, Tuple[Vector, Quaternion]],
            Dict[str, Dict[float, Tuple[Vector, Quaternion]]],
            List[float],
        ],
    ] = {}

    try:
        for action in baked_actions:
            Debug.log(f"  [fbf] Recording action '{action.name}' ...")

            # Collect root bone keyframe times
            root_fcs = (
                [find_action_fcurve(action, _bone_loc_path(root_bone_name), i) for i in range(3)]
                + [find_action_fcurve(action, _bone_rot_path(root_bone_name), i) for i in range(4)]
            )
            root_times = sorted(_collect_keyframe_times(root_fcs))
            if not root_times:
                Debug.log(f"    No root bone keyframes in '{action.name}' — skipping")
                continue

            # Build dense frame set: union of root times and IK bone own keyframe times
            ik_own_times: Set[float] = set()
            for ik_name in ik_bone_names:
                ik_fcs = (
                    [find_action_fcurve(action, _bone_loc_path(ik_name), i) for i in range(3)]
                    + [find_action_fcurve(action, _bone_rot_path(ik_name), i) for i in range(4)]
                )
                ik_own_times.update(_collect_keyframe_times(ik_fcs))

            dense_times: List[float] = sorted(set(root_times) | ik_own_times)
            Debug.log(
                f"    Root: {len(root_times)}, IK-extra: {len(ik_own_times - set(root_times))}, "
                f"dense total: {len(dense_times)}"
            )

            # Per-action recording buffers
            obj_transforms: Dict[float, Tuple[Vector, Quaternion]] = {}
            ik_recorded: Dict[str, Dict[float, Tuple[Vector, Quaternion]]] = {
                n: {} for n in ik_bone_names
            }
            prev_arm_rot: Optional[Quaternion] = None
            prev_ik_rot: Dict[str, Optional[Quaternion]] = {n: None for n in ik_bone_names}

            assign_action_to_datablock(custom_rig, action, slot_name=MTAR_ARMATURE_SLOT_NAME)
            try:
                for t in dense_times:
                    # Reset armature to original world so FCurve-driven pose is evaluated
                    # correctly.  NLA tracks are muted so only this action contributes.
                    custom_rig.matrix_world = arm_world_orig.copy()
                    scene.frame_set(int(round(t)), subframe=t - int(round(t)))
                    bpy.context.view_layer.update()

                    pose_bone = custom_rig.pose.bones.get(root_bone_name)
                    if pose_bone is None:
                        continue

                    # Step 2: capture root bone's current world transform
                    before_world: Matrix = custom_rig.matrix_world @ pose_bone.matrix

                    # Step 3: zero root bone to rest pose
                    clear_rest_pose_from_bone(pose_bone)
                    bpy.context.view_layer.update()

                    # Step 4: compute world-space delta (how far the root bone moved)
                    after_world: Matrix = custom_rig.matrix_world @ pose_bone.matrix
                    delta_world: Matrix = after_world @ before_world.inverted()

                    # Step 5: shift IK bones by delta and record their new matrix_basis
                    if ik_bone_names:
                        _move_ik_bones_by_delta(custom_rig, ik_bone_names, delta_world)
                        bpy.context.view_layer.update()

                        for ik_name in ik_bone_names:
                            ik_bone = custom_rig.pose.bones.get(ik_name)
                            if ik_bone is None:
                                continue
                            mb = ik_bone.matrix_basis.copy()
                            ik_loc = mb.to_translation()
                            ik_rot = mb.to_quaternion()
                            prev = prev_ik_rot.get(ik_name)
                            if prev is not None:
                                ik_rot.make_compatible(prev)
                            prev_ik_rot[ik_name] = ik_rot.copy()
                            ik_recorded[ik_name][t] = (ik_loc, ik_rot)

                    # Step 6: record armature object transform
                    obj_loc = before_world.to_translation()
                    obj_rot = before_world.to_quaternion()
                    if prev_arm_rot is not None:
                        obj_rot.make_compatible(prev_arm_rot)
                    prev_arm_rot = obj_rot.copy()
                    obj_transforms[t] = (obj_loc, obj_rot)

            finally:
                remove_action_from_datablock(custom_rig)

            if obj_transforms:
                recorded_data[action] = (obj_transforms, ik_recorded, dense_times)
            else:
                Debug.log_warning(f"    No transforms recorded for '{action.name}' — skipping")

    finally:
        # Restore NLA mute states regardless of any errors during recording
        if custom_rig.animation_data and custom_rig.animation_data.nla_tracks:
            for nla_track in custom_rig.animation_data.nla_tracks:
                if nla_track.name in nla_mute_states:
                    nla_track.mute = nla_mute_states[nla_track.name]
        if nla_mute_states:
            Debug.log(f"  [fbf] Restored {len(nla_mute_states)} NLA track mute state(s)")
        # Clean final reset after all recording — the armature and timeline are
        # left pristine before the apply pass begins.
        custom_rig.matrix_world = arm_world_orig.copy()
        scene.frame_set(original_frame)

    # -----------------------------------------------------------------------
    # Phase 2 — Apply all recorded data (pure FCurve writes, no frame_set)
    # -----------------------------------------------------------------------
    any_moved = False
    first_action_transform: Optional[Tuple[Vector, Quaternion]] = None

    for action in baked_actions:
        if action not in recorded_data:
            continue

        obj_transforms, ik_recorded, dense_times = recorded_data[action]
        Debug.log(f"  [fbf] Applying action '{action.name}' ...")

        # Step 7: write object-level location + rotation FCurves
        wrote = _write_object_fcurves(custom_rig, action, obj_transforms)
        if not wrote:
            Debug.log_warning(f"    Failed to write object FCurves for '{action.name}' — skipping")
            continue

        # Step 8: zero root bone FCurves (bone stays at rest; armature carries motion)
        _key_bone_to_arm_origin(custom_rig, action, root_bone_name)

        # Step 9: densify IK bone FCurves and overwrite with recorded matrix_basis values
        for ik_name in ik_bone_names:
            recorded = ik_recorded.get(ik_name, {})
            if not recorded:
                continue
            densified = _densify_bone_fcurves(action, ik_name, dense_times)
            if densified:
                Debug.log(f"    [fbf] Densified '{ik_name}': +{densified} keypoints")
            _write_ik_bone_fcurves_from_basis(action, ik_name, recorded)

        if first_action_transform is None:
            first_t = min(obj_transforms.keys())
            first_action_transform = obj_transforms[first_t]

        Debug.log(
            f"    [fbf] Wrote {len(obj_transforms)} armature keyframes, "
            f"compensated {len(ik_bone_names)} IK bone(s)"
        )
        any_moved = True

    # Move armature to first frame of first action (viewport convenience)
    if first_action_transform is not None:
        loc0, rot0 = first_action_transform
        custom_rig.matrix_world = Matrix.LocRotScale(loc0, rot0, Vector((1.0, 1.0, 1.0)))
        try:
            bpy.context.view_layer.update()
        except Exception:  # noqa: BLE001
            pass
        Debug.log("  [fbf] Moved armature to first recorded position")

    if not any_moved:
        Debug.log_warning(
            f"apply_root_motion_to_object_framebyframe: No actions processed for "
            f"bone '{root_bone_name}'"
        )
        return False

    Debug.log("apply_root_motion_to_object_framebyframe: Complete")
    return True
