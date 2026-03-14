"""Root motion utilities for MTAR import.

Post-bake step: move root-motion bone FCurves (location + rotation) to the
armature-object level so that pose libraries work at any point in an animation
without requiring manual repositioning of hands and feet.

Entry point
-----------
``apply_root_motion_to_object(custom_rig, baked_actions, layout_action, track_mapping)``

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
# Core: evaluate bone world transforms via depsgraph
# ---------------------------------------------------------------------------

def _evaluate_root_bone_transforms(
    custom_rig: bpy.types.Object,
    action: bpy.types.Action,
    bone_name: str,
    frame_times: List[float],
    arm_world_orig: Optional[Matrix] = None,
) -> Dict[float, Tuple[Vector, Quaternion]]:
    """Evaluate the root bone's *object-level* transform at each frame time.

    Temporarily assigns *action* to the rig, sets each frame, reads the bone's
    evaluated armature-space matrix (``pose_bone.matrix``), and computes::

        M_obj = arm_world_orig @ bone_armspace

    where ``arm_world_orig`` is the armature's world transform **before** any
    root motion FCurves are written, and ``bone_armspace`` is the root bone's
    evaluated matrix in armature space.

    This ensures that when the root bone FCurves are deleted and it returns to
    rest pose, the armature's position preserves the bone's original world
    location while the bone itself rests at the armature origin (no offset).

    Returns a dict mapping frame → (location, quaternion).
    """
    scene = bpy.context.scene
    original_frame = scene.frame_current

    if arm_world_orig is None:
        arm_world_orig = custom_rig.matrix_world.copy()

    pose_bone = custom_rig.pose.bones.get(bone_name)
    if pose_bone is None:
        Debug.log_warning(
            f"  _evaluate_root_bone_transforms: Bone '{bone_name}' not found "
            f"in rig '{custom_rig.name}'"
        )
        return {}

    # No need to compute rest matrix - not used in new formula

    result: Dict[float, Tuple[Vector, Quaternion]] = {}
    prev_rot: Optional[Quaternion] = None

    # Temporarily assign the action so depsgraph evaluates the bone FCurves
    assign_action_to_datablock(custom_rig, action, slot_name=MTAR_ARMATURE_SLOT_NAME)
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for t in frame_times:
            scene.frame_set(int(round(t)), subframe=t - int(round(t)))
            depsgraph.update()

            # Read the bone's evaluated matrix in armature space
            eval_rig = custom_rig.evaluated_get(depsgraph)
            eval_bone = eval_rig.pose.bones.get(bone_name)
            if eval_bone is None:
                continue

            bone_armspace = eval_bone.matrix.copy()

            # M_obj = arm_world_orig @ bone_armspace
            # Places the armature so the root bone (at rest) ends up where it
            # was originally, without any rest-pose offset.
            m_obj = arm_world_orig @ bone_armspace
            obj_loc = m_obj.to_translation()
            obj_rot = m_obj.to_quaternion()

            # Ensure quaternion sign consistency across frames
            if prev_rot is not None:
                obj_rot.make_compatible(prev_rot)
            prev_rot = obj_rot.copy()

            result[t] = (obj_loc, obj_rot)
    finally:
        remove_action_from_datablock(custom_rig)
        scene.frame_set(original_frame)

    return result


def _evaluate_ik_bone_world_transforms(
    custom_rig: bpy.types.Object,
    action: bpy.types.Action,
    ik_bone_names: List[str],
    frame_times_per_bone: Dict[str, List[float]],
) -> Dict[str, Dict[float, Tuple[Vector, Quaternion]]]:
    """Evaluate IK bones' world-space transforms BEFORE root motion is moved.

    Must be called while the root bone FCurves still exist (armature at origin,
    bone animation drives the root bone).

    Returns bone_name → { frame → (world_loc, world_rot) }.
    """
    scene = bpy.context.scene
    original_frame = scene.frame_current

    result: Dict[str, Dict[float, Tuple[Vector, Quaternion]]] = {
        name: {} for name in ik_bone_names
    }

    # Collect ALL unique frame times to minimize frame_set calls
    all_times: Set[float] = set()
    for times in frame_times_per_bone.values():
        all_times.update(times)
    sorted_times = sorted(all_times)

    if not sorted_times:
        return result

    assign_action_to_datablock(custom_rig, action, slot_name=MTAR_ARMATURE_SLOT_NAME)
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for t in sorted_times:
            scene.frame_set(int(round(t)), subframe=t - int(round(t)))
            depsgraph.update()

            eval_rig = custom_rig.evaluated_get(depsgraph)
            arm_world = eval_rig.matrix_world

            for bone_name in ik_bone_names:
                if t not in frame_times_per_bone.get(bone_name, []):
                    continue
                eval_bone = eval_rig.pose.bones.get(bone_name)
                if eval_bone is None:
                    continue
                world_mat = arm_world @ eval_bone.matrix
                world_loc = world_mat.to_translation()
                world_rot = world_mat.to_quaternion()
                result[bone_name][t] = (world_loc, world_rot)
    finally:
        remove_action_from_datablock(custom_rig)
        scene.frame_set(original_frame)

    return result


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
    ik_bone_names: Optional[List[str]] = None,
) -> None:
    """Keep the root bone's keyframe timing while cancelling its rest pose.

    This preserves the set of frames that contain keyframes (which is used
    by the exporter as a reference) while making the root bone sit at the
    armature origin (rest pose is effectively undone).
    """
    pose_bone = custom_rig.pose.bones.get(bone_name)
    if pose_bone is None:
        Debug.log_warning(
            f"  _key_bone_to_arm_origin: Bone '{bone_name}' not found in rig '{custom_rig.name}'"
        )
        return

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

    # Determine the world-space delta caused by applying the rest-inverse correction.
    # This delta can be applied to IK bones so they remain in the same world position.
    before_world = custom_rig.matrix_world @ pose_bone.matrix
    pose_bone.matrix = Matrix.Identity(4)
    after_world = custom_rig.matrix_world @ pose_bone.matrix
    delta_world = after_world @ before_world.inverted()

    # Apply the same delta to IK bones if requested
    if ik_bone_names:
        _move_ik_bones_by_delta(custom_rig, ik_bone_names, delta_world)

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


def _compensate_one_bone(
    custom_rig: bpy.types.Object,
    action: bpy.types.Action,
    bone_name: str,
    obj_transforms: Dict[float, Tuple[Vector, Quaternion]],
    pre_move_world: Dict[float, Tuple[Vector, Quaternion]],
) -> None:
    """Apply world-space compensation to a single IK bone in *action*.

    Uses the pre-computed world-space transforms from before root motion was moved
    (``pre_move_world``) and the object-level transforms (``obj_transforms``) to
    compute the correct new pose-basis values.

    The correct formula for a parentless bone with rest matrix ``R``::

        old_armspace = R @ old_basis   (captured as pre_move_world when arm was at identity)
        We need: M_obj @ R @ new_basis = R @ old_basis  (preserve world position)
        Therefore: new_basis = R⁻¹ @ M_obj⁻¹ @ R @ old_basis

    Which simplifies to ``new_basis = rest⁻¹ @ M_obj⁻¹ @ old_armspace``.

    Only **existing** keyframe points are modified — no new keyframes are inserted.
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
    rest_matrix = pose_bone.bone.matrix_local.copy()
    rest_matrix_inv = rest_matrix.inverted()

    frame_times = sorted(pre_move_world.keys())
    Debug.log(f"    Compensating IK bone '{bone_name}' over {len(frame_times)} frame times")

    prev_rot: Optional[Quaternion] = None
    for t in frame_times:
        # Get the object transform at this frame
        obj_data = obj_transforms.get(t)
        if obj_data is None:
            # Should not normally happen (root has keyframes at all bone times)
            continue
        obj_loc, obj_rot = obj_data
        m_obj = Matrix.LocRotScale(obj_loc, obj_rot, Vector((1.0, 1.0, 1.0)))
        m_obj_inv = m_obj.inverted()

        # Get the bone's pre-move armature-space transform
        # (arm_world was identity at evaluation time, so "world" = armspace)
        world_loc, world_rot = pre_move_world[t]
        old_armspace = Matrix.LocRotScale(world_loc, world_rot, Vector((1.0, 1.0, 1.0)))

        # new_basis = rest⁻¹ @ M_obj⁻¹ @ old_armspace
        new_basis = rest_matrix_inv @ m_obj_inv @ old_armspace

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

def apply_root_motion_to_object(
    custom_rig: bpy.types.Object,
    baked_actions: List[bpy.types.Action],
    layout_action: bpy.types.Action,
    track_mapping: Optional[Dict[str, BoneParameters]] = None,
) -> bool:
    """Move root-motion bone FCurves to the armature-object level.

    This always also moves the armature so the root bone's world transform matches
    its original world transform (as if the root bone were still driving the rig).

    This is the main entry point.  Call it from the import operator
    **after** ``bake_constraints_and_decimate_fcurves()`` returns.

    Steps performed per baked action:

    1. Detect the root-motion fox track name via DIFF-only segment check on
       the layout action metadata.
    2. Resolve the Blender bone name via *track_mapping* (fallback: fox name).
    3. Temporarily assign the action and evaluate the root bone's armature-space
       matrix at each keyframe via depsgraph.  Compute the correct object
       transform as ``arm_world_orig @ bone_armspace``.
    4. If *track_mapping* has ``space_l=world`` bones, also evaluate their
       world transforms **before** the root bone FCurves are removed (so the
       armature is still at the origin and the bone chain is intact).
    5. Write object-level ``location`` + ``rotation_quaternion`` FCurves with
       the computed transforms.
    6. Delete the root bone's FCurves.
    7. Compensate IK bones: for each IK bone at each keyframe, set its local
       value to ``M_obj⁻¹ @ pre_move_world_pos``.

    Args:
        custom_rig: The Rigify (or custom) armature object to apply root motion to.
        baked_actions: Baked action objects (``bake_result["actions_created"]``).
        layout_action: Layout track action with track structure metadata.
        track_mapping: Optional fox_name → BoneParameters mapping (from
            ``foxwrap_mapping.parse_track_mapping_file()``).

    Returns:
        ``True`` if at least one FCurve was successfully moved, ``False`` otherwise.
    """
    if not custom_rig or not layout_action or not baked_actions:
        Debug.log("apply_root_motion_to_object: Missing required args — skipping")
        return False

    # Step 1 – detect root-motion track
    root_info = find_root_motion_track_info(layout_action)
    if root_info is None:
        Debug.log(
            "apply_root_motion_to_object: No root-motion track detected in "
            "layout metadata — skipping"
        )
        return False

    _, fox_track_name = root_info
    root_bone_name = _blender_bone_name_for_fox(fox_track_name, track_mapping)

    Debug.log(
        f"apply_root_motion_to_object: root-motion bone = '{root_bone_name}' "
        f"(fox track: '{fox_track_name}')"
    )

    # Validate that the bone exists on the rig
    if root_bone_name not in custom_rig.pose.bones:
        Debug.log_warning(
            f"apply_root_motion_to_object: Bone '{root_bone_name}' not found "
            f"in rig '{custom_rig.name}' — skipping"
        )
        return False

    # Ensure QUATERNION rotation mode so the object-level rotation keys are valid
    custom_rig.rotation_mode = "QUATERNION"

    # Capture the armature's world transform BEFORE any root motion FCurves exist.
    # This accounts for the rig not being at the world origin (e.g. a Rigify rig
    # placed at z=−1.075 so its root bone rests at z=0) and ensures that when we
    # write M_obj = arm_world_orig @ bone_armspace @ rest⁻¹ to the object FCurves,
    # the bone's world position is preserved after its FCurves are deleted.
    # Stored as a custom property so the exporter can cancel the factor.
    arm_world_orig: Matrix = custom_rig.matrix_world.copy()
    custom_rig[MTAR_ROOT_MOTION_ARM_WORLD_PROP] = [v for row in arm_world_orig for v in row]
    Debug.log(
        f"  arm_world_orig = loc={arm_world_orig.to_translation()}, "
        f"rot={arm_world_orig.to_quaternion()}"
    )

    # Identify IK bones that will need compensation
    ik_bone_names: List[str] = []
    if track_mapping:
        ik_bone_names = _get_ik_bone_names(track_mapping, root_bone_name)
        if ik_bone_names:
            Debug.log(
                f"  IK bones to compensate ({len(ik_bone_names)}): {ik_bone_names}"
            )

    any_moved = False

    for action in baked_actions:
        Debug.log(f"  Processing baked action '{action.name}' ...")

        # Collect root bone keyframe times from its FCurves
        loc_path = _bone_loc_path(root_bone_name)
        rot_path = _bone_rot_path(root_bone_name)
        root_bone_fcs = (
            [find_action_fcurve(action, loc_path, i) for i in range(3)]
            + [find_action_fcurve(action, rot_path, i) for i in range(4)]
        )
        root_frame_times = sorted(_collect_keyframe_times(root_bone_fcs))

        if not root_frame_times:
            Debug.log(
                f"    No keyframes found for bone '{root_bone_name}' in "
                f"action '{action.name}' — skipping"
            )
            continue

        Debug.log(f"    Root bone has {len(root_frame_times)} keyframe times")

        # --- Phase 1: Evaluate transforms BEFORE modifying anything ---
        # (action still has the root bone FCurves, armature at origin)

        # 1a. Evaluate root bone → object transforms
        obj_transforms = _evaluate_root_bone_transforms(
            custom_rig, action, root_bone_name, root_frame_times, arm_world_orig
        )
        if not obj_transforms:
            Debug.log_warning(
                "    Failed to evaluate root bone transforms — skipping action"
            )
            continue

        # 1b. Evaluate IK bone world transforms (before root motion is removed)
        ik_world_transforms: Dict[str, Dict[float, Tuple[Vector, Quaternion]]] = {}
        if ik_bone_names:
            # Collect per-bone keyframe times
            ik_frame_times: Dict[str, List[float]] = {}
            for ik_name in ik_bone_names:
                ik_fcs = (
                    [find_action_fcurve(action, _bone_loc_path(ik_name), i) for i in range(3)]
                    + [find_action_fcurve(action, _bone_rot_path(ik_name), i) for i in range(4)]
                )
                times = sorted(_collect_keyframe_times(ik_fcs))
                if times:
                    ik_frame_times[ik_name] = times

            if ik_frame_times:
                ik_world_transforms = _evaluate_ik_bone_world_transforms(
                    custom_rig, action, list(ik_frame_times.keys()), ik_frame_times
                )

        # --- Phase 2: Write object FCurves and delete bone FCurves ---

        wrote = _write_object_fcurves(custom_rig, action, obj_transforms)
        if not wrote:
            Debug.log_warning("    Failed to write object FCurves — skipping action")
            continue

        _key_bone_to_arm_origin(custom_rig, action, root_bone_name)
        Debug.log(
            f"    Wrote {len(obj_transforms)} object keyframes, preserved root bone keyframe times"
        )

        if obj_transforms:
            first_frame = min(obj_transforms.keys())
            loc, rot = obj_transforms[first_frame]
            target_mat = Matrix.LocRotScale(loc, rot, Vector((1.0, 1.0, 1.0)))
            custom_rig.matrix_world = target_mat
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
            Debug.log(
                f"    Moved armature to root world at frame {first_frame}"
            )

        any_moved = True

        # --- Phase 3: Compensate IK bones ---
        if ik_world_transforms:
            for ik_name, world_data in ik_world_transforms.items():
                if world_data:
                    _compensate_one_bone(custom_rig, action, ik_name, obj_transforms, world_data)

    if not any_moved:
        Debug.log_warning(
            f"apply_root_motion_to_object: No FCurves were found for bone "
            f"'{root_bone_name}' in any of the {len(baked_actions)} baked action(s)"
        )
        return False

    Debug.log("apply_root_motion_to_object: Complete")
    return True
