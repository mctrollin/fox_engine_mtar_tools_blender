"""Motion-point-specific Blender export utilities.

This module contains the three functions that read motion point data back from
a Blender armature for export:

* :func:`build_motion_points_list_from_armature` — bone hierarchy →
  :class:`~py_foxwrap.foxwrap_motionpoint.MotionPointWrapper`
* :func:`build_motion_point_metadata_dict` — per-bone segment/bit-size metadata
* :func:`collect_motion_point_actions` — gather NLA / active actions

These functions previously lived in ``tools_mtar_exporter`` but have been
extracted here to reduce that module's size and to centralise all
motion-point logic.
"""

from typing import List, Dict

import bpy

from ..py_utilities.utilities_logging import Debug
from ..py_utilities.utilities_blender_animation import (
    action_has_fcurves,
    iter_action_fcurves,
    is_relevant_strip,
    build_data_path_for_bone,
    extract_bone_name_from_data_path,
)

from ..py_foxwrap.foxwrap_metadata import (
    TrackMetaData,
    iter_track_properties,
    parse_action_track_metadata,
)
from ..py_foxwrap.foxwrap_misc_export import ExportActionData
from ..py_foxwrap.foxwrap_motionpoint import MotionPointWrapper, MotionPointEntryWrapper

from ..py_fox.fox_gani_types import SegmentType, TrackUnitFlags
from ..py_fox.fox_misc_types import StrCode32


def build_motion_points_list_from_armature(
    motion_points_armature: bpy.types.Object,
) -> MotionPointWrapper:
    """Build a :class:`MotionPointWrapper` from a motion-points armature.

    Extracts bone names and parent relationships from the armature to produce
    the motion-point definition list that is ultimately written to the MTAR
    CommonInfo section.

    Only bones that have animation data are exported as motion points.
    Parent-only bones (bones whose sole purpose is to anchor hierarchy) are
    excluded from the exported list but their hashes are still recorded as
    ``parent_hash`` values in child entries.

    .. note::
        Bone names in the armature are expected to be decimal-hash strings
        (e.g. ``"4036034414"``) because the importer stores them that way
        when the hash could not be resolved.  FC1 (human-readable bone names)
        is a deferred improvement; this function preserves the existing
        convention for now.

    Args:
        motion_points_armature: Armature object containing motion-point bones.

    Returns:
        :class:`MotionPointWrapper` (may be empty if the armature is invalid).
    """
    if not motion_points_armature or motion_points_armature.type != 'ARMATURE':
        return MotionPointWrapper()

    Debug.log(
        f"\nBuilding MotionPointWrapper from armature "
        f"'{motion_points_armature.name}'..."
    )

    # Identify which bones have animation data across all relevant NLA actions
    bones_with_animation: set = set()

    if motion_points_armature.animation_data:
        for nla_track in motion_points_armature.animation_data.nla_tracks:
            for strip in nla_track.strips:
                if strip.action and is_relevant_strip(strip):
                    for fcurve in iter_action_fcurves(strip.action):
                        bone_name = extract_bone_name_from_data_path(fcurve.data_path)
                        if bone_name:
                            bones_with_animation.add(bone_name)
                else:
                    if strip.action:
                        Debug.log(
                            f"  Skipping motion point strip "
                            f"'{getattr(strip, 'name', '<unknown>')}' "
                            f"(not a GANI strip)"
                        )

    if not bones_with_animation:
        Debug.log_warning(
            "  Warning: No bones with animation data found in motion points "
            "armature.  All bones in the armature will be exported."
        )
        bones_with_animation = {
            bone.name for bone in motion_points_armature.data.bones
        }

    entries: List[MotionPointEntryWrapper] = []
    parent_only_bones: List[str] = []

    for bone in motion_points_armature.data.bones:
        if bone.name not in bones_with_animation:
            parent_only_bones.append(bone.name)
            continue

        # Bone names are decimal hash strings (set by importer for GANI2 consistency)
        hash_value = int(bone.name)
        parent_hash = int(bone.parent.name) if bone.parent else 0
        parent_name = bone.parent.name if bone.parent else None

        if not bone.parent:
            Debug.log_warning(
                f"  Warning: Motion point bone '{bone.name}' has no parent; "
                f"writing empty parent hash"
            )

        entries.append(MotionPointEntryWrapper(
            hash_value=hash_value,
            name=bone.name,
            parent_hash=parent_hash,
            parent_name=parent_name,
        ))

        parent_str = f"→ {bone.parent.name}" if bone.parent else "(no parent)"
        Debug.log(
            f"  {bone.name} {parent_str} "
            f"(hash: {hash_value}, parent_hash: {parent_hash})"
        )

    if parent_only_bones:
        Debug.log(
            f"  Skipped {len(parent_only_bones)} parent-only bone(s): "
            + ", ".join(parent_only_bones)
        )

    wrapper = MotionPointWrapper(entries=entries)
    Debug.log(f"MotionPointWrapper built: {wrapper.count} point(s)")
    return wrapper


def build_motion_point_metadata_dict(
    motion_points_armature: bpy.types.Object,
    action: bpy.types.Action,
) -> Dict[str, TrackMetaData]:
    """Build a per-bone metadata dictionary for motion point tracks.

    Motion points have no layout-track action, so segment types and bit sizes
    are derived by inspecting FCurves in *action* and reading stored metadata
    properties.

    Args:
        motion_points_armature: Motion points armature object.
        action:                 The Blender action to inspect (required).

    Returns:
        ``{bone_name: TrackMetaData}`` for every bone present in *action*.
    """
    metadata_dict: Dict[str, TrackMetaData] = {}

    if not motion_points_armature or motion_points_armature.type != 'ARMATURE':
        return metadata_dict

    if not action:
        Debug.log_warning(
            f"  Warning: No action provided to build_motion_point_metadata_dict() "
            f"for armature '{motion_points_armature.name}', returning empty dict"
        )
        return metadata_dict

    bones = motion_points_armature.data.bones
    missing_metadata_bones: List[str] = []

    for bone in bones:
        bone_name = bone.name

        has_rotation = False
        has_location = False

        if action_has_fcurves(action):
            rotation_quat_path = build_data_path_for_bone(bone_name, 'rotation_quaternion')
            rotation_euler_path = build_data_path_for_bone(bone_name, 'rotation_euler')
            location_path = build_data_path_for_bone(bone_name, 'location')
            for fc in iter_action_fcurves(action):
                if fc.data_path in (rotation_quat_path, rotation_euler_path):
                    has_rotation = True
                elif fc.data_path == location_path:
                    has_location = True

        segment_types: List[SegmentType] = []
        if has_rotation:
            segment_types.append(SegmentType.QUAT)
        if has_location:
            segment_types.append(SegmentType.VECTOR3)

        component_bit_sizes = None
        unit_flags = 0
        found_metadata_in_action = False

        for _, track_name, metadata_str in iter_track_properties(action):
            if track_name == bone_name:
                found_metadata_in_action = True
                if isinstance(metadata_str, str):
                    parsed = parse_action_track_metadata(metadata_str)
                    if parsed:
                        if parsed.get('component_bit_sizes'):
                            component_bit_sizes = parsed['component_bit_sizes']
                        if parsed.get('flags'):
                            flag_enums = [
                                TrackUnitFlags[name]
                                for name in parsed['flags']
                                if name in TrackUnitFlags.__members__
                            ]
                            if flag_enums:
                                unit_flags = TrackUnitFlags.track_unit_flags_to_int(flag_enums)
                break

        bone_present_in_action = found_metadata_in_action or has_rotation or has_location
        if bone_present_in_action and not found_metadata_in_action:
            missing_metadata_bones.append(bone_name)

        if not bone_present_in_action:
            continue

        metadata_dict[bone_name] = TrackMetaData(
            track_name=bone_name,
            segment_types=segment_types,
            unit_flags=unit_flags,
            name_hash=StrCode32.from_string(bone_name).to_int(),
            component_bit_sizes=component_bit_sizes,
            rig_unit_type=None,
        )

    if missing_metadata_bones:
        Debug.log_warning(
            f"  Warning: No metadata found for {len(missing_metadata_bones)} "
            f"motion point(s) in armature '{motion_points_armature.name}': "
            + ", ".join(missing_metadata_bones)
        )

    return metadata_dict


def collect_motion_point_actions(
    motion_points_armature: bpy.types.Object,
    use_nla: bool,
    export_clean_threshold: float = 0.0,
) -> List[ExportActionData]:
    """Collect motion-point animation actions from *motion_points_armature*.

    Mirrors the logic of ``collect_actions_for_export()`` in the main exporter
    but targets the motion-points armature.

    Args:
        motion_points_armature: Motion points armature object.
        use_nla:                 If ``True``, collect from NLA strips; if
                                 ``False``, use the active action.
        export_clean_threshold:  FCurve cleaning threshold (0 = disabled).

    Returns:
        List of :class:`ExportActionData` objects.
    """
    if not motion_points_armature:
        return []

    Debug.log(
        f"\nCollecting motion point actions from "
        f"'{motion_points_armature.name}'..."
    )

    actions: List[ExportActionData] = []

    if (
        use_nla
        and motion_points_armature.animation_data
        and motion_points_armature.animation_data.nla_tracks
    ):
        Debug.log("  Using NLA strips for motion points")
        for track in motion_points_armature.animation_data.nla_tracks:
            if track.mute:
                continue
            for strip in track.strips:
                if not is_relevant_strip(strip):
                    if strip.action:
                        Debug.log(
                            f"    Skipping motion point strip "
                            f"'{getattr(strip, 'name', '<unknown>')}' "
                            f"(not a GANI strip)"
                        )
                    continue

                action_data = ExportActionData(
                    action=strip.action,
                    frame_start=int(strip.frame_start),
                    frame_end=int(strip.frame_end),
                    source=f"NLA strip '{strip.name}' on track '{track.name}'",
                    export_clean_threshold=export_clean_threshold,
                )
                actions.append(action_data)
                Debug.log(f"    {action_data.to_string()}")

    elif (
        motion_points_armature.animation_data
        and motion_points_armature.animation_data.action
    ):
        Debug.log("  Using active action for motion points")
        action = motion_points_armature.animation_data.action

        if action_has_fcurves(action):
            frame_start = int(
                min(kp.co.x for fc in iter_action_fcurves(action) for kp in fc.keyframe_points)
            )
            frame_end = int(
                max(kp.co.x for fc in iter_action_fcurves(action) for kp in fc.keyframe_points)
            )
        else:
            frame_start = 0
            frame_end = 0

        action_data = ExportActionData(
            action=action,
            frame_start=frame_start,
            frame_end=frame_end,
            source="Active action",
            export_clean_threshold=export_clean_threshold,
        )
        actions.append(action_data)
        Debug.log(f"    {action_data.to_string()}")

    else:
        Debug.log("  No motion point actions found")

    return actions
