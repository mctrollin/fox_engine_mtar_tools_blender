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
                    = arm_world_orig @ (bone_armspace @ rest)
                      (since bone_armspace = rest @ basis, the rest contributions cancel,
                       leaving the result depend only on basis)

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
from mathutils import Matrix, Quaternion, Vector

from ..py_fox.fox_gani_enums import SegmentType
from ..py_foxwrap.foxwrap_metadata import iter_track_properties, parse_track_metadata_generic
from ..py_foxwrap.foxwrap_mapping import BoneParameters
from ..py_utilities.utilities_logging import Debug
from ..py_utilities.utilities_blender_animation import (
    assign_action_to_datablock,
    build_data_path_for_bone,
    collect_keyframe_times,
    densify_bone_fcurves,
    ensure_action_fcurve,
    find_action_fcurve,
    prune_action_fcurves_to_frames,
    remove_action_from_datablock,
    set_keypoint_value,
    MTAR_ARMATURE_SLOT_NAME,
)
from ..py_utilities.utilities_blender_armature import clear_rest_pose_from_bone

# ---------------------------------------------------------------------------
# Root-motion detection helpers
# ---------------------------------------------------------------------------

# DIFF segment types that characterise a root-motion track.
_DIFF_SEGMENT_TYPES: frozenset = frozenset((SegmentType.QUAT_DIFF, SegmentType.VECTOR_DIFF))

# Custom property key stored on the armature object.  Encodes the 4×4
# matrix_world the armature had BEFORE root motion FCurves were written.
# NOTE: not currently consumed by the exporter; reserved for future use.
MTAR_ROOT_MOTION_ARM_WORLD_PROP: str = "mtar_root_motion_arm_world"


def _find_root_motion_track_info(layout_action: bpy.types.Action) -> Optional[Tuple[int, str]]:
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
            f"_find_root_motion_track_info: Multiple root-motion candidates: "
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


def _write_ik_bone_fcurves_fbf(
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
    loc_fcs = [find_action_fcurve(action, build_data_path_for_bone(bone_name, 'location'), i) for i in range(3)]
    rot_fcs = [find_action_fcurve(action, build_data_path_for_bone(bone_name, 'rotation_quaternion'), i) for i in range(4)]
    has_loc = any(fc is not None for fc in loc_fcs)
    has_rot = any(fc is not None for fc in rot_fcs)

    for t in sorted(frame_data.keys()):
        loc, rot = frame_data[t]
        if has_loc:
            for i, fc in enumerate(loc_fcs):
                if fc is not None:
                    set_keypoint_value(fc, t, loc[i])
        if has_rot:
            for i, fc in enumerate(rot_fcs):
                if fc is not None:
                    set_keypoint_value(fc, t, rot[i])

    for fc in loc_fcs + rot_fcs:
        if fc is not None:
            fc.update()


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


def _get_interpolation_at_frame(fc: Optional["bpy.types.FCurve"], frame: float) -> Optional[str]:
    """Return the interpolation mode of the keyframe point closest to *frame*.

    Returns ``None`` if *fc* is ``None`` or no keyframe is found within ±0.001 frames.
    """
    if fc is None:
        return None
    for kp in fc.keyframe_points:
        if abs(kp.co[0] - frame) < 0.001:
            return kp.interpolation
    return None


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
        find_action_fcurve(action, build_data_path_for_bone(bone_name, 'location'), i) for i in range(3)
    ]
    rot_fcs = [
        find_action_fcurve(action, build_data_path_for_bone(bone_name, 'rotation_quaternion'), i) for i in range(4)
    ]

    frame_times = sorted(collect_keyframe_times(loc_fcs + rot_fcs))
    if not frame_times:
        return

    first_frame = frame_times[0]
    loc_interps = [_get_interpolation_at_frame(fc, first_frame) for fc in loc_fcs]
    rot_interps = [_get_interpolation_at_frame(fc, first_frame) for fc in rot_fcs]

    # Write the corrected pose values into existing keyframes (preserves keyframe timing)
    prev_rot: Optional[Quaternion] = None
    for t in frame_times:
        for i, fc in enumerate(loc_fcs):
            if fc is None:
                continue
            set_keypoint_value(fc, t, corrected_loc[i], interpolation=loc_interps[i])

        if any(fc is not None for fc in rot_fcs):
            new_rot = corrected_rot.copy()
            if prev_rot is not None:
                new_rot.make_compatible(prev_rot)
            prev_rot = new_rot.copy()
            for i, fc in enumerate(rot_fcs):
                if fc is None:
                    continue
                set_keypoint_value(fc, t, new_rot[i], interpolation=rot_interps[i])

    for fc in loc_fcs + rot_fcs:
        if fc is not None:
            fc.update()


# ---------------------------------------------------------------------------
# Analytical IK compensation (matrix-math approach — alternative to fbf)
#
# This is an ALTERNATIVE to the frame-by-frame IK compensation approach.
# Instead of evaluating a live Blender pose via frame_set() it computes the
# new IK bone pose entirely from pre-recorded world-space transforms using
# matrix math — making it faster but currently less accurate.
#
# TODO: This approach does NOT yet produce correct results for hand IK bones.
#       The frame-by-frame approach (_write_ik_bone_fcurves_fbf via
#       _move_ik_bones_by_delta) should be used instead until this is fixed.
#       Kept here for future reference and potential optimisation.
# ---------------------------------------------------------------------------

def _compensate_ik_bone_analytical(
    custom_rig: bpy.types.Object,
    action: bpy.types.Action,
    bone_name: str,
    obj_transforms: Dict[float, Tuple[Vector, Quaternion]],
    pre_move_world: Dict[float, Tuple[Vector, Quaternion]],
    effective_rest: Matrix,
) -> None:
    """Apply world-space compensation to a single IK bone in *action* (analytical approach).

    Uses pre-recorded world-space transforms (``pre_move_world``) and object-level
    transforms (``obj_transforms``) to compute new pose-basis values via matrix
    math, without any live ``frame_set`` calls.

    The formula for a parentless bone with rest matrix ``R``::

        old_world = arm_world_orig @ R @ old_basis   (captured in pre_move_world)
        We need: M_obj @ R @ new_basis = old_world  (preserve world position)
        Therefore: new_basis = R⁻¹ @ M_obj⁻¹ @ old_world

    Call ``densify_bone_fcurves`` first to ensure FCurve keypoints exist at every
    target frame time before this function runs.

    .. note::
        This approach does **not** yet produce correct results for hand IK bones.
        Use ``_write_ik_bone_fcurves_fbf`` (frame-by-frame approach) instead.
    """
    loc_path = build_data_path_for_bone(bone_name, 'location')
    rot_path = build_data_path_for_bone(bone_name, 'rotation_quaternion')

    bone_loc_fcs = [find_action_fcurve(action, loc_path, i) for i in range(3)]
    bone_rot_fcs = [find_action_fcurve(action, rot_path, i) for i in range(4)]

    has_loc = any(fc is not None for fc in bone_loc_fcs)
    has_rot = any(fc is not None for fc in bone_rot_fcs)

    if not has_loc and not has_rot:
        Debug.log(f"    _compensate_ik_bone_analytical: No FCurves for '{bone_name}' — skipping")
        return

    if not pre_move_world:
        Debug.log(f"    _compensate_ik_bone_analytical: No pre-move world transforms for '{bone_name}' — skipping")
        return

    pose_bone = custom_rig.pose.bones.get(bone_name)
    if pose_bone is None:
        Debug.log_warning(f"    _compensate_ik_bone_analytical: Bone '{bone_name}' not found — skipping")
        return

    # Compute rest inverse once outside the frame loop for efficiency.
    rest_matrix_inv = effective_rest.inverted()

    frame_times = sorted(pre_move_world.keys())
    Debug.log(f"    Compensating IK bone '{bone_name}' over {len(frame_times)} frame times (analytical)")

    prev_rot: Optional[Quaternion] = None
    for t in frame_times:
        obj_data = obj_transforms.get(t)
        if obj_data is None:
            # Should not happen after dense evaluation, but guard defensively.
            Debug.log_warning(
                f"    _compensate_ik_bone_analytical: No obj_transform at t={t} for '{bone_name}' — skipping frame"
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
                set_keypoint_value(fc, t, new_loc[i])

        if has_rot:
            new_rot = new_basis.to_quaternion()
            # Ensure quaternion sign consistency across frames
            if prev_rot is not None:
                new_rot.make_compatible(prev_rot)
            prev_rot = new_rot.copy()
            for i, fc in enumerate(bone_rot_fcs):
                if fc is None:
                    continue
                set_keypoint_value(fc, t, new_rot[i])

    for fc in bone_loc_fcs + bone_rot_fcs:
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

    root_info = _find_root_motion_track_info(layout_action)
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

    # Precompute root-motion+IK keyframe times for progress reporting.
    # Build a *minimal* set of frames where any of the affected bones already has a keyframe.
    action_record_times: Dict[bpy.types.Action, List[float]] = {}
    action_ik_bones: Dict[bpy.types.Action, List[str]] = {}
    total_frames = 0
    root_times_per_action: Dict[bpy.types.Action, List[float]] = {}
    for action in baked_actions:
        root_fcs = (
            [find_action_fcurve(action, build_data_path_for_bone(root_bone_name, 'location'), i) for i in range(3)]
            + [find_action_fcurve(action, build_data_path_for_bone(root_bone_name, 'rotation_quaternion'), i) for i in range(4)]
        )
        root_times = sorted(collect_keyframe_times(root_fcs))
        if not root_times:
            continue

        # Collect IK bone keyframes if present (unioned into the record set)
        ik_bones_with_keys: List[str] = []
        ik_times: Set[float] = set()
        for ik_name in ik_bone_names:
            ik_fcs = (
                [find_action_fcurve(action, build_data_path_for_bone(ik_name, 'location'), i) for i in range(3)]
                + [find_action_fcurve(action, build_data_path_for_bone(ik_name, 'rotation_quaternion'), i) for i in range(4)]
            )
            this_ik_times = collect_keyframe_times(ik_fcs)
            if this_ik_times:
                ik_bones_with_keys.append(ik_name)
                ik_times.update(this_ik_times)

        record_times: List[float] = sorted(set(root_times) | ik_times)
        action_record_times[action] = record_times
        if ik_bones_with_keys:
            action_ik_bones[action] = ik_bones_with_keys
        root_times_per_action[action] = root_times
        total_frames += len(record_times)

    if total_frames == 0:
        Debug.log_warning(
            "apply_root_motion_to_object_framebyframe: No keyframes found in any baked action — skipping"
        )
        return False

    progress_step = max(1, total_frames // 100)
    processed_frames = 0

    Debug.update_progress(75, "Applying root motion (recording)...")

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

    # recorded_data: action → (obj_transforms, ik_recorded, record_times, root_times)
    recorded_data: Dict[
        "bpy.types.Action",
        Tuple[
            Dict[float, Tuple[Vector, Quaternion]],
            Dict[str, Dict[float, Tuple[Vector, Quaternion]]],
            List[float],
            List[float],
        ],
    ] = {}

    try:
        for action_idx, action in enumerate(baked_actions, 1):
            record_times = action_record_times.get(action)
            if not record_times:
                Debug.log(f"  [fbf] Skipping action '{action.name}' (no keyframes)")
                continue

            Debug.log(f"  [fbf] Recording action {action_idx}/{len(baked_actions)}: '{action.name}' ...")
            Debug.log(f"    Record frame count: {len(record_times)}")

            # Per-action recording buffers
            obj_transforms: Dict[float, Tuple[Vector, Quaternion]] = {}
            ik_bones_for_action: List[str] = action_ik_bones.get(action, [])
            ik_recorded: Dict[str, Dict[float, Tuple[Vector, Quaternion]]] = {
                n: {} for n in ik_bones_for_action
            }
            prev_arm_rot: Optional[Quaternion] = None
            prev_ik_rot: Dict[str, Optional[Quaternion]] = {n: None for n in ik_bones_for_action}

            assign_action_to_datablock(custom_rig, action, slot_name=MTAR_ARMATURE_SLOT_NAME)
            try:
                for t in record_times:
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

                    # Step 5: shift IK bones by delta and record their new matrix_basis (only for bones with keyframes)
                    if ik_bones_for_action:
                        _move_ik_bones_by_delta(custom_rig, ik_bones_for_action, delta_world)
                        bpy.context.view_layer.update()

                        for ik_name in ik_bones_for_action:
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

                    # Progress update
                    processed_frames += 1
                    if processed_frames % progress_step == 0 or processed_frames == total_frames:
                        pct = 75 + (processed_frames / total_frames) * 20
                        Debug.update_progress(
                            pct,
                            f"Applying root motion (recording) {action_idx}/{len(baked_actions)}: {action.name}"
                        )

            finally:
                remove_action_from_datablock(custom_rig)

            if obj_transforms:
                recorded_data[action] = (obj_transforms, ik_recorded, record_times, root_times_per_action[action])
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

    apply_actions = [a for a in baked_actions if a in recorded_data]
    apply_total = len(apply_actions)

    for idx, action in enumerate(apply_actions):
        pct = 95 + (idx / max(1, apply_total)) * 4
        Debug.update_progress(pct, f"Applying root motion (writing FCurves) {idx+1}/{apply_total}: {action.name}")

        obj_transforms, ik_recorded, record_times, root_times = recorded_data[action]
        Debug.log(f"  [fbf] Applying action '{action.name}' ...")

        # Step 7: write object-level location + rotation FCurves
        wrote = _write_object_fcurves(custom_rig, action, obj_transforms)
        if not wrote:
            Debug.log_warning(f"    Failed to write object FCurves for '{action.name}' — skipping")
            continue

        # Clean up object FCurves so they contain only the root keyframes
        keep_frames = {int(round(t)) for t in root_times}
        prune_action_fcurves_to_frames(
            action,
            "location",
            list(range(3)),
            keep_frames,
            slot_name=MTAR_ARMATURE_SLOT_NAME,
        )
        prune_action_fcurves_to_frames(
            action,
            "rotation_quaternion",
            list(range(4)),
            keep_frames,
            slot_name=MTAR_ARMATURE_SLOT_NAME,
        )

        # Step 8: zero root bone FCurves (bone stays at rest; armature carries motion)
        _key_bone_to_arm_origin(custom_rig, action, root_bone_name)

        # Step 9: densify IK bone FCurves and overwrite with recorded matrix_basis values
        for ik_name, recorded in ik_recorded.items():
            if not recorded:
                continue
            densified = densify_bone_fcurves(action, ik_name, record_times)
            if densified:
                Debug.log(f"    [fbf] Densified '{ik_name}': +{densified} keypoints")
            _write_ik_bone_fcurves_fbf(action, ik_name, recorded)

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
        except RuntimeError:
            pass
        Debug.log("  [fbf] Moved armature to first recorded position")

    if not any_moved:
        Debug.log_warning(
            f"apply_root_motion_to_object_framebyframe: No actions processed for "
            f"bone '{root_bone_name}'"
        )
        return False

    Debug.update_progress(99, "Root motion applied")
    Debug.log("apply_root_motion_to_object_framebyframe: Complete")
    return True
