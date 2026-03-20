"""Low-level GANI track keyframe import utilities.

This module contains the core per-track and per-segment keyframe import
functions used by both the main animation importer and the motion-points
importer.  Keeping them in a dedicated module avoids circular dependencies
between ``tools_mtar_importer`` and ``tools_motion_points_importer``.
"""

from typing import Optional, List, Dict, Union

import bpy
from mathutils import Quaternion, Vector

from ..py_core.core_logging import Debug

from ..py_utilities.utilities_transforms import (
    calculate_directional_location,
    prepare_rotation_offset_quats,
    apply_rotation_transforms,
    fox_to_blender_vector,
    apply_rest_pose_correction_local,
    make_blender_quaternion_compatible,
)
from ..py_utilities.utilities_blender_animation import (
    BLENDER_OBJECT_TRANSFORMS_GROUP_NAME,
    MTAR_ARMATURE_SLOT_NAME,
    ensure_action_fcurve,
    build_data_path_for_bone,
)

from ..py_fox.fox_gani_types import SegmentType

from ..py_foxwrap.foxwrap_misc import TrackUnitWrapper, TrackDataBlobWrapper
from ..py_foxwrap.foxwrap_mapping import ARMATURE_TARGET_NAME


def import_keyframes_track(
    context: bpy.types.Context,
    action: bpy.types.Action,
    keyframes_track: TrackDataBlobWrapper,
    slot_name: Optional[str] = MTAR_ARMATURE_SLOT_NAME,
    apply_transforms: bool = True,
) -> int:
    """Import a single track data blob into a Blender action.

    Args:
        context:          Blender context (used to access import settings such as
                          ``ik_up_distance``).
        action:           Blender action to add keyframes to.
        keyframes_track:  :class:`TrackDataBlobWrapper` containing animation data.
        slot_name:        Action slot name to use when creating FCurves.  Defaults
                          to :data:`MTAR_ARMATURE_SLOT_NAME`; pass
                          :data:`MTAR_SHADER_SLOT_NAME` for shader tracks.
        apply_transforms: When ``True`` (default), axis swaps, rotation offsets,
                          rest-pose corrections and IK conversions are applied.
                          Pass ``False`` for raw tracks such as shader nodes.

    Returns:
        Maximum frame number encountered in this track.
    """
    max_frame: int = 0

    Debug.log(
        f"  - Import Track '{keyframes_track.name}' "
        f"({keyframes_track.data_blob.type.name}): "
        f"{len(keyframes_track.data_blob.keyframes)} keyframe(s)"
    )

    # Always use LINEAR interpolation — decimation will create bezier curves later if enabled.

    # Detect armature-object target: uses direct property paths, no bone group.
    is_armature_target: bool = (keyframes_track.name == ARMATURE_TARGET_NAME)
    # Pre-compute data paths: direct property names for armature target, bone paths otherwise.
    data_path_rotation: str = (
        'rotation_quaternion' if is_armature_target
        else build_data_path_for_bone(keyframes_track.name, 'rotation_quaternion')
    )
    data_path_location: str = (
        'location' if is_armature_target
        else build_data_path_for_bone(keyframes_track.name, 'location')
    )
    # Ensure group_name is a string (name can be an integer hash).
    # For armature targets, put object transform fcurves in Blender's default group.
    group_name: Optional[str] = (
        BLENDER_OBJECT_TRANSFORMS_GROUP_NAME
        if is_armature_target
        else str(keyframes_track.name)
    )

    # Prepare rotation transformations (only applies to rotation tracks)
    rotation_offset_quats: List[Quaternion] = []
    rotation_axis_map: Optional[List[Dict[str, Union[str, bool]]]] = None

    if keyframes_track.data_blob.type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
        if apply_transforms:
            if keyframes_track.rotation_offset:
                rotation_offset_quats = prepare_rotation_offset_quats(keyframes_track.rotation_offset)
            if keyframes_track.rotation_axis_map:
                rotation_axis_map = keyframes_track.rotation_axis_map
                axis_str = ','.join(
                    [('-' if m['negate'] else '') + m['axis'] for m in rotation_axis_map]
                )
                Debug.log(f"    Applying rotation axis mapping transformation: {axis_str}")

            # IK special case: quaternion rotation converted to directional location
            if keyframes_track.as_ik_up:
                ik_data = keyframes_track.as_ik_up
                axis = ik_data.axis

                distance: float = 1.0
                if hasattr(context.scene, 'mtar_properties'):
                    distance = context.scene.mtar_properties.import_props.ik_up_distance

                Debug.log(
                    f"    Converting rotation to directional location "
                    f"(axis={axis}, distance={distance})"
                )

                converted_locations = []
                absolute_frame = 0
                prev_quat: Optional[Quaternion] = None
                for keyframe in keyframes_track.data_blob.keyframes:
                    absolute_frame += keyframe.frame_count
                    quat = apply_rotation_transforms(
                        keyframe.data.value,
                        rotation_axis_map,
                        rotation_offset_quats,
                        offset_first=True,
                    )

                    if keyframes_track.space_r:
                        pass
                    elif keyframes_track.map_r_rest_pose:
                        quat = apply_rest_pose_correction_local(quat, keyframes_track.map_r_rest_pose)

                    # Ensure quaternion stays in same hemisphere as previous keyframe
                    quat = make_blender_quaternion_compatible(quat, prev_quat)
                    prev_quat = quat

                    bone_base_location = Vector((0.0, 0.0, 0.0))
                    target_location = calculate_directional_location(
                        bone_location=bone_base_location,
                        bone_rotation_quat=quat,
                        axis=axis,
                        distance=distance,
                    )
                    converted_locations.append((absolute_frame, target_location))
                    max_frame = max(max_frame, absolute_frame)

                for i in range(3):
                    try:
                        data_path_str = data_path_location
                        fcurve: bpy.types.FCurve = ensure_action_fcurve(
                            action,
                            data_path=data_path_str,
                            index=i,
                            action_group_name=group_name,
                            slot_name=slot_name,
                        )
                    except Exception as e:
                        data_path_str = data_path_location
                        Debug.log_warning(
                            f"Could not create fcurve '{data_path_str}[{i}]' "
                            f"on action '{getattr(action, 'name', '<unknown>')}': {e}"
                        )
                        continue
                    for frame_count, target_location in converted_locations:
                        kf_point: bpy.types.Keyframe = fcurve.keyframe_points.insert(
                            frame_count, target_location[i]
                        )
                        kf_point.interpolation = 'LINEAR'

                Debug.log(f"    Added directional location keyframes (frames 0-{max_frame})")
                return max_frame  # IK path terminates here

            # Normal rotation with transforms
            converted_quaternions = []
            absolute_frame = 0
            prev_quat: Optional[Quaternion] = None
            for keyframe in keyframes_track.data_blob.keyframes:
                absolute_frame += keyframe.frame_count
                quat = apply_rotation_transforms(
                    keyframe.data.value,
                    rotation_axis_map,
                    rotation_offset_quats,
                    offset_first=False,
                )

                if keyframes_track.space_r:
                    if keyframes_track.rotation_offset:
                        pass
                    Debug.log("    Applied world space transformation (space_r)")
                elif keyframes_track.map_r_rest_pose:
                    quat = apply_rest_pose_correction_local(quat, keyframes_track.map_r_rest_pose)
                    euler = keyframes_track.map_r_rest_pose['euler']
                    Debug.log(
                        f"    Applied local space rest pose correction: "
                        f"({euler[0]}, {euler[1]}, {euler[2]})"
                    )

                # Ensure quaternion stays in same hemisphere as previous keyframe
                quat = make_blender_quaternion_compatible(quat, prev_quat)
                prev_quat = quat

                converted_quaternions.append((absolute_frame, quat))
                max_frame = max(max_frame, absolute_frame)

        else:
            # Raw quaternion values — no transforms (shader tracks)
            converted_quaternions = []
            absolute_frame = 0
            for keyframe in keyframes_track.data_blob.keyframes:
                absolute_frame += keyframe.frame_count
                converted_quaternions.append((absolute_frame, keyframe.data.value))
                max_frame = max(max_frame, absolute_frame)

        for i in range(4):
            try:
                data_path_str = data_path_rotation
                fcurve: bpy.types.FCurve = ensure_action_fcurve(
                    action,
                    data_path=data_path_str,
                    index=i,
                    action_group_name=group_name,
                    slot_name=slot_name,
                )
            except Exception as e:
                data_path_str = data_path_rotation
                Debug.log_warning(
                    f"Could not create fcurve '{data_path_str}[{i}]' "
                    f"on action '{getattr(action, 'name', '<unknown>')}': {e}"
                )
                continue
            for frame_count, quat in converted_quaternions:
                quat_component: float = quat[i]
                kf_point: bpy.types.Keyframe = fcurve.keyframe_points.insert(
                    frame_count, quat_component
                )
                kf_point.interpolation = 'LINEAR'

        Debug.log(f"    Added quaternion rotation keyframes (frames 0-{max_frame})")

    elif keyframes_track.data_blob.type in [SegmentType.VECTOR3, SegmentType.VECTOR_DIFF]:
        converted_vectors = []
        absolute_frame = 0
        for keyframe in keyframes_track.data_blob.keyframes:
            absolute_frame += keyframe.frame_count
            if apply_transforms:
                blender_vec: List[float] = fox_to_blender_vector(keyframe.data.value)
            else:
                blender_vec = list(keyframe.data.value)
            converted_vectors.append((absolute_frame, blender_vec))
            max_frame = max(max_frame, absolute_frame)

        for i in range(3):
            try:
                data_path_str = data_path_location
                fcurve: bpy.types.FCurve = ensure_action_fcurve(
                    action,
                    data_path=data_path_str,
                    index=i,
                    action_group_name=group_name,
                    slot_name=slot_name,
                )
            except Exception as e:
                data_path_str = data_path_location
                Debug.log_warning(
                    f"Could not create fcurve '{data_path_str}[{i}]' "
                    f"on action '{getattr(action, 'name', '<unknown>')}': {e}"
                )
                continue
            for abs_frame, blender_vec in converted_vectors:
                kf_point: bpy.types.Keyframe = fcurve.keyframe_points.insert(
                    abs_frame, blender_vec[i]
                )
                kf_point.interpolation = 'LINEAR'

        Debug.log(f"    Added location keyframes (frames 0-{max_frame})")

    elif keyframes_track.data_blob.type == SegmentType.FLOAT:
        # FLOAT segment: raw scalar stored as location[0].
        float_values = []
        absolute_frame = 0
        for keyframe in keyframes_track.data_blob.keyframes:
            absolute_frame += keyframe.frame_count
            float_val = (
                keyframe.data.value[0]
                if isinstance(keyframe.data.value, list)
                else keyframe.data.value
            )
            float_values.append((absolute_frame, float_val))
            max_frame = max(max_frame, absolute_frame)

        try:
            data_path_str = data_path_location
            fcurve: bpy.types.FCurve = ensure_action_fcurve(
                action,
                data_path=data_path_str,
                index=0,
                action_group_name=group_name,
                slot_name=slot_name,
            )
        except Exception as e:
            data_path_str = data_path_location
            Debug.log_warning(
                f"Could not create fcurve '{data_path_str}[0]' "
                f"on action '{getattr(action, 'name', '<unknown>')}': {e}"
            )
            return max_frame

        for abs_frame, float_val in float_values:
            kf_point: bpy.types.Keyframe = fcurve.keyframe_points.insert(abs_frame, float_val)
            kf_point.interpolation = 'LINEAR'

        Debug.log(f"    Added FLOAT keyframes as location[0] (frames 0-{max_frame})")

    elif keyframes_track.data_blob.type == SegmentType.VECTOR2:
        # VECTOR2: raw [x, y] stored as location[0] and location[1].
        vec2_values = []
        absolute_frame = 0
        for keyframe in keyframes_track.data_blob.keyframes:
            absolute_frame += keyframe.frame_count
            vec2_values.append((absolute_frame, keyframe.data.value))
            max_frame = max(max_frame, absolute_frame)

        for i in range(2):
            try:
                data_path_str = data_path_location
                fcurve: bpy.types.FCurve = ensure_action_fcurve(
                    action,
                    data_path=data_path_str,
                    index=i,
                    action_group_name=group_name,
                    slot_name=slot_name,
                )
            except Exception as e:
                data_path_str = data_path_location
                Debug.log_warning(
                    f"Could not create fcurve '{data_path_str}[{i}]' "
                    f"on action '{getattr(action, 'name', '<unknown>')}': {e}"
                )
                continue
            for abs_frame, vec2 in vec2_values:
                kf_point: bpy.types.Keyframe = fcurve.keyframe_points.insert(abs_frame, vec2[i])
                kf_point.interpolation = 'LINEAR'

        Debug.log(f"    Added VECTOR2 keyframes as location[0,1] (frames 0-{max_frame})")

    elif keyframes_track.data_blob.type == SegmentType.VECTOR4:
        Debug.log_warning(
            f"  Segment type VECTOR4 on track '{keyframes_track.name}' is not supported "
            f"as Blender FCurves and will be lost. Round-trip fidelity requires the "
            f"layout action to contain this track's segment types."
        )

    return max_frame


def import_gani_track(
    context: bpy.types.Context,
    action: bpy.types.Action,
    gani_track: TrackUnitWrapper,
    slot_name: Optional[str] = MTAR_ARMATURE_SLOT_NAME,
    apply_transforms: bool = True,
) -> int:
    """Import a :class:`TrackUnitWrapper` (all its segments) into a Blender action.

    Args:
        context:          Blender context.
        action:           Blender action to add keyframes to.
        gani_track:       :class:`TrackUnitWrapper` containing per-segment keyframe data.
        slot_name:        Action slot name forwarded to :func:`import_keyframes_track`.
        apply_transforms: When ``False``, coordinate transforms are skipped (shader tracks).

    Returns:
        Maximum frame number encountered across all segments.
    """
    max_frame: int = 0

    Debug.log(
        f"  - Import GaniTrack '{gani_track.name}' "
        f"(RigUnitType: {gani_track.rig_unit_type.name if gani_track.rig_unit_type else 'None'}) "
        f"Segments: {len(gani_track.segments_track_data)}"
    )

    for keyframes_track in gani_track.segments_track_data:
        track_max_frame: int = import_keyframes_track(
            context, action, keyframes_track,
            slot_name=slot_name,
            apply_transforms=apply_transforms,
        )
        max_frame = max(max_frame, track_max_frame)

    return max_frame
