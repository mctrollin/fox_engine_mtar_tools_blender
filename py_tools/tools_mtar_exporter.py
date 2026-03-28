"""
MTAR animation exporter for Metal Gear Solid V.

This module handles the export of Blender animation data to MTAR format.
"""

import os
from typing import Optional, Dict, List, Set, Tuple
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector

from ..py_core.core_logging import Debug

from .. import blender_properties

from ..py_utilities import util_transforms, util_blender_animation, util_parsing, util_blender_armature, util_fcurve_processing

from ..py_fox import fox_gani_constants as gani_const
from ..py_fox import fox_mtar_constants as mtar_const
from ..py_fox.fox_gani_types import AnimKeyframe, SegmentType, TrackUnitFlags, TrackHeader, TrackUnit, TrackData, TrackDataBlob
from ..py_fox.fox_frig_types import RigUnitType
from ..py_fox.fox_hash_types import StrCode32
from ..py_fox.fox_mtar_types import is_new_mtar_format

from ..py_foxwrap_utilities import futil_filtering, futil_rest_pose_correction
from ..py_foxwrap_utilities.futil_action_types import ExportActionData

from ..py_foxwrap.fwrap_mtar_import_types import GaniImportData
from ..py_foxwrap.fwrap_track_types import TrackUnitWrapper, Tracks, TrackDataBlobWrapper
from ..py_foxwrap.fwrap_mtar_export_types import (
    GaniExportData, 
    GaniExportTracksData, 
    GaniExportMotionPointsData, 
    GaniMotionEventsData,
    Gani1ExportShaderData,
)
from ..py_foxwrap.fwrap_mapping_export_types import TrackSegmentBoneMapping
from ..py_foxwrap.fwrap_mapping_types import BoneParameters
from ..py_foxwrap import fwrap_motionevent, fwrap_metadata, fwrap_misc_export, fwrap_mapping
from ..py_foxwrap.fwrap_mtar_writer import MtarWriter
from ..py_foxwrap.fwrap_mtar_reader import MtarReader

# TODO: don't import tools into other tools
from . import tools_mtar_importer
from ..py_foxwrap import fwrap_motionpoint_export
from . import tools_gani1_shader_exporter


# Utility Functions ###############################################################

def get_highest_bit_size_for_segment(segment_type: SegmentType) -> int:
    """Return the highest available bit encoding for a given segment type.
    
    Args:
        segment_type: The segment type to get bit size for
        
    Returns:
        Maximum component_bit_size for the segment type:
        - QUAT/QUAT_DIFF: 15 bits
        - VECTOR2/3/4/VECTOR_DIFF/FLOAT: 32 bits
        - Other types: 0 (no override)
    """
    if segment_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
        return 15
    elif segment_type in [SegmentType.VECTOR2, SegmentType.VECTOR3, SegmentType.VECTOR4, SegmentType.VECTOR_DIFF, SegmentType.FLOAT]:
        return 32
    return 0


def get_default_bit_size_for_segment(segment_type: SegmentType) -> int:
    """Return the safe default component bit size for a given segment type.

    Quaternion types only support 12, 13, or 15 bits. Using 16 (a common
    vector default) would cause a ValueError in write_unaligned_quaternion.
    This function returns a type-correct default so callers never accidentally
    use an invalid size.

    Returns:
        - QUAT/QUAT_DIFF: 15
        - Everything else: 16
    """
    if segment_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
        return 15
    return 16



def _convert_import_gani_to_export_gani(
    import_data: GaniImportData,
    reference_path_hash: Optional[int] = None,
) -> GaniExportData:
    """Convert an imported GANI object to export data for writer re-export."""

    frame_count, frame_rate = fwrap_metadata.resolve_gani_frame_info(
        import_data.gani_layout_track,
        import_data.gani_track_mini_header,
        import_data.gani_motion_point_track_header,
    )

    if frame_count == 0:
        hash_str = f"0x{reference_path_hash:016X}" if reference_path_hash is not None else "unknown"
        Debug.log_warning(f"Reference GANI (hash={hash_str}) has frame_count=0; this may indicate an empty GANI or format mismatch")

    tracks_data = GaniExportTracksData(
        gani_tracks=import_data.gani_bone_tracks,
        source="reference"
    )

    motion_points_data = None
    if import_data.gani_mtp_tracks:
        motion_points_data = GaniExportMotionPointsData(
            motion_point_tracks=import_data.gani_mtp_tracks,
            motion_point_track_header=import_data.gani_motion_point_track_header
        )

    motion_events_data = None
    if import_data.gani_events is not None:
        motion_events_data = GaniMotionEventsData(
            motion_events=import_data.gani_events
        )

    shader_nodes_data = None
    if import_data.gani1_shader_tracks:
        shader_nodes_data = Gani1ExportShaderData(
            property_tracks=[s.tracks for s in import_data.gani1_shader_tracks],
            property_names=[s.property_name for s in import_data.gani1_shader_tracks],
            property_headers=[None] * len(import_data.gani1_shader_tracks)
        )

    # Old-format file table unknown (MtarTableList.unknown, ushort).
    table_unknown = None
    if import_data.file_header is not None and hasattr(import_data.file_header, 'unknown'):
        table_unknown = import_data.file_header.unknown

    return GaniExportData(
        gani_name=f"reference_{reference_path_hash:016X}" if reference_path_hash is not None else "reference",
        gani_frame_count=frame_count,
        gani_frame_rate=frame_rate,
        gani_frame_start=0,
        gani_frame_end=frame_count,
        gani_tracks_data=tracks_data,
        gani_motion_points_data=motion_points_data,
        gani_motion_events_data=motion_events_data,
        gani_node_params=import_data.gani_node_params,
        gani_path_hash=reference_path_hash,
        gani1_shader_nodes_data=shader_nodes_data,
        gani1_table_unknown=table_unknown,
        gani1_skeleton_list=import_data.gani_skeleton_list,
        gani1_motion_point_list=import_data.gani1_motion_point_list,
        gani1_motion_point_parent_list=import_data.gani1_motion_point_parent_list,
        gani1_no_skl_list=(import_data.gani_skeleton_list is None),
    )



def _get_object_keyframe_numbers(
    action: bpy.types.Action,
    segment_type: SegmentType,
    frame_start: int,
    frame_end: int,
) -> List[int]:
    """Return sorted integer keyframe times from the object-level FCurves.

    Used as the export frame list when a track maps to the armature object
    itself via the ``[armature]`` mapping target.

    Mirrors the NLA-offset logic of ``get_bone_keyframe_numbers_from_action``:
    object-level FCurve keypoints are always stored at **action-relative** frame
    numbers, but when the action lives in an NLA strip ``frame_start`` is an
    **absolute timeline** position.  The same ``frame_offset`` applied to bone
    FCurves must therefore also be applied here.
    """
    if segment_type in (SegmentType.QUAT, SegmentType.QUAT_DIFF):
        data_path = "rotation_quaternion"
        num_components = 4
    else:  # VECTOR3 / VECTOR_DIFF
        data_path = "location"
        num_components = 3

    # Convert action-relative keyframe times to the export coordinate system.
    # For NLA exports frame_start is the strip's absolute position; for direct
    # action exports both sides are action-relative, so offset = 0.
    action_frame_start = int(action.frame_range[0])
    frame_offset = frame_start - action_frame_start

    frame_set: set = set()
    for i in range(num_components):
        fc = util_blender_animation.find_action_fcurve(action, data_path, i)
        if fc is None:
            continue
        for kp in fc.keyframe_points:
            export_frame = int(round(kp.co[0])) + frame_offset
            if frame_start <= export_frame <= frame_end:
                frame_set.add(export_frame)

    # Always include frame_start (the mandatory first keyframe) so the frame
    # list is never empty, matching the contract of get_bone_keyframe_numbers_from_action.
    frame_set.add(frame_start)
    # Include frame_end so the accumulated deltas reach FrameCount.
    frame_set.add(frame_end)

    return sorted(frame_set)


def clamp_bit_size_for_segment(segment_type: SegmentType, component_bit_size: int) -> int:
    """Validate and clamp component_bit_size to a value supported by the writer.

    Fox Engine quaternion encoding only supports 12, 13, or 15 bits.
    If a stored metadata value (e.g. 32 from a VECTOR track mis-classified
    during import, or 16 from a legacy default) is passed for a QUAT segment
    the writer will raise a ValueError.  This function silently clamps the
    value and emits a warning.

    Args:
        segment_type: The segment type that will be written.
        component_bit_size: The requested bit size (possibly invalid).

    Returns:
        A valid component_bit_size for the given segment type.
    """
    if segment_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
        if component_bit_size not in (12, 13, 15):
            valid = 15
            Debug.log_warning(
                f"  Warning: component_bit_size {component_bit_size} is not valid for QUAT "
                f"(must be 12, 13 or 15) — clamping to {valid}"
            )
            return valid
    return component_bit_size


def _merge_metadata_from_actions(actions: List[bpy.types.Action]) -> Dict[str, fwrap_metadata.TrackMetaData]:
    """Builds a union metadata dict from multiple per-GANI actions.
    
    For each track name, takes the entry with the most segments (widest definition).
    Used for old-format GANI1 export where no shared layout action exists.
    
    Args:
        actions: List of GANI actions to merge metadata from
        
    Returns:
        Dictionary mapping fox_track_name -> fwrap_metadata.TrackMetaData with union of segments
    """
    merged: Dict[str, fwrap_metadata.TrackMetaData] = {}
    for action in actions:
        if action is None:
            continue
        per_action_dict = fwrap_metadata.get_all_track_metadata_from_action(action)
        for track_name, meta in per_action_dict.items():
            existing = merged.get(track_name)
            if existing is None:
                merged[track_name] = meta
            elif len(meta.segment_types) > len(existing.segment_types):
                # Take the entry with more segments (union)
                merged[track_name] = meta
    return merged


# Layout and MetaData #############################################################

def build_layout_track_from_metadata(track_segment_bone_mapping: TrackSegmentBoneMapping, 
                                     metadata_dict: Dict[str, fwrap_metadata.TrackMetaData],
                                     layout_action: Optional[bpy.types.Action] = None,
                                     force_highest_bit_encoding: bool = False) -> 'Tracks':
    """Build a Tracks (layout track) object from metadata.
    
    Args:
        track_segment_bone_mapping: Unified mapping from (track_idx, segment_idx) to (bone_name, fox_mapping_params)
        metadata_dict: Dictionary of fox_track_name -> fwrap_metadata.TrackMetaData
        layout_action: Optional layout action containing header properties (t_id, unknown_a, unknown_b, frame_rate)
        force_highest_bit_encoding: If True, use highest available bit sizes for all segments
        
    Returns:
        Tracks object with TrackUnits built from metadata
    """
    track_units = []
    total_segments = 0
    
    # Build TrackUnits in order of track indices
    track_indices = track_segment_bone_mapping.get_track_indices()
    for track_idx in track_indices:
        base_mapping = track_segment_bone_mapping.get_base_mapping(track_idx)
        if not base_mapping:
            continue  # Skip tracks without base mapping
        blender_bone_name, fox_mapping_params = base_mapping
        
        # Get the fox track name from the mapping params
        fox_track_name = fox_mapping_params.fox_name
        
        # Strip multi-segment suffix if present
        base_fox_track_name, _ = util_parsing.parse_segment_suffix(fox_track_name)
        
        if base_fox_track_name in metadata_dict:
            metadata = metadata_dict[base_fox_track_name]
            
            # Create TrackData objects for each segment
            track_data_list = []
            for segment_idx, segment_type in enumerate(metadata.segment_types):
                # Calculate absolute segment index across all tracks
                segment_idx_abs = total_segments + segment_idx
                
                # Determine next_entry_offset: 0 for last segment, TrackData.ENTRY_SIZE (8) for others
                is_last_segment = (segment_idx == len(metadata.segment_types) - 1)
                next_entry_offset = 0 if is_last_segment else TrackData.ENTRY_SIZE
                
                # Get component bit size from metadata if available
                component_bit_size = 0
                if metadata.component_bit_sizes and segment_idx < len(metadata.component_bit_sizes):
                    component_bit_size = metadata.component_bit_sizes[segment_idx]

                # If export setting forces highest bit encoding, override component_bit_size accordingly
                if force_highest_bit_encoding:
                    highest_bits = get_highest_bit_size_for_segment(segment_type)
                    if highest_bits > 0:
                        component_bit_size = max(component_bit_size, highest_bits)

                # Create TrackData with proper fields
                track_data = TrackData(
                    data_offset=0,  # Not used in layout track (disableTrackData=true in template)
                    ms_id=segment_idx_abs,  # Absolute segment index
                    td_type=segment_type,
                    next_entry_offset=next_entry_offset,
                    component_bit_size=component_bit_size
                )
                track_data_list.append(track_data)
            
            # Create TrackUnit
            track_unit = TrackUnit(
                name=StrCode32(metadata.name_hash),  # Convert int to StrCode32
                segment_count=len(track_data_list),
                unit_flags=metadata.unit_flags,
                padding=0,
                segments_data=track_data_list
            )
            track_units.append(track_unit)
            total_segments += len(metadata.segment_types)
        else:
            # No metadata for this track - create empty track unit
            Debug.log_warning(f"    Warning: No metadata for fox track '{base_fox_track_name}' (blender bone: '{blender_bone_name}'), creating empty track unit")
            track_unit = TrackUnit(
                name=StrCode32(0),  # Convert int to StrCode32
                segment_count=0,
                unit_flags=0,
                padding=0,
                segments_data=[]
            )
            track_units.append(track_unit)
    
    # Get header properties from layout_action if available
    header_props = fwrap_metadata.read_track_header_properties_from_action(layout_action)
    
    Debug.log(
        f"    Using layout header from action: "
        f"{gani_const.TRKH_ID}={header_props[gani_const.TRKH_ID]}, "
        f"{gani_const.TRKH_UNKNOWN_A}={header_props[gani_const.TRKH_UNKNOWN_A]}, "
        f"{gani_const.TRKH_UNKNOWN_B}={header_props[gani_const.TRKH_UNKNOWN_B]}, "
        f"{gani_const.TRKH_FRAME_COUNT}={header_props[gani_const.TRKH_FRAME_COUNT]}, "
        f"{gani_const.TRKH_FRAME_RATE}={header_props[gani_const.TRKH_FRAME_RATE]}"
    )
    
    # Create TrackHeader
    header = TrackHeader(
        unit_count=len(track_units),
        segment_count=total_segments,
        t_id=header_props[gani_const.TRKH_ID],
        unknown_a=header_props[gani_const.TRKH_UNKNOWN_A],
        unknown_b=header_props[gani_const.TRKH_UNKNOWN_B],
        frame_count=header_props[gani_const.TRKH_FRAME_COUNT],
        frame_rate=header_props[gani_const.TRKH_FRAME_RATE],
        unit_offsets=[]
    )
    
    # Create Tracks object
    layout_track = Tracks(
        header=header,
        track_units=track_units
    )
    
    return layout_track


# Mapping #############################################################

def extract_rest_pose_correction_mapping_from_armature(track_segment_bone_mapping: TrackSegmentBoneMapping, armature: bpy.types.Object) -> None:
    """Extract rest pose rotations from armature and merge with existing transformations.
    
    For each bone in the mapping, extracts its rest pose from the armature and:
    - For LOCAL space tracks: Merges with existing map_r (or creates if missing)
    - For WORLD space tracks: Adds to rotation_offset list
    
    This allows combining mapping file transformations with armature rest pose.
    
    Args:
        track_segment_bone_mapping: Mapping structure to update
        armature: Source armature
    """
    if not armature or armature.type != 'ARMATURE':
        return
    
    Debug.log("\n=== Extracting Rest Pose from Armature ===")
    rest_pose_count = 0
    
    # Iterate through all mapped bones
    for track_idx in track_segment_bone_mapping.get_track_indices():
        segments = track_segment_bone_mapping.get_track_segments(track_idx)
        for _seg_idx, blender_bone_name, bone_params in segments:
            # Skip as_ik_up bones - they should not be affected by rest pose corrections
            if bone_params.as_ik_up:
                continue
            
            # Check if bone exists in armature
            if blender_bone_name not in armature.data.bones:
                continue
            
            # Extract rest pose rotation from armature
            bone = armature.data.bones[blender_bone_name]
            rest_pose_dict = util_blender_armature.get_rest_pose_dict_from_bone(bone)

            had_map_r = bone_params.map_r is not None
            existing_euler = bone_params.map_r['euler'] if had_map_r else None

            # Apply helper updates for both import/export semantics
            euler_deg = futil_rest_pose_correction.apply_rest_pose_correction_to_target(bone_params, rest_pose_dict)

            if bone_params.space_r:
                Debug.log(f"  {blender_bone_name} [WORLD]: Added rest pose to offset_r: ({euler_deg[0]:.1f}, {euler_deg[1]:.1f}, {euler_deg[2]:.1f})")
            else:
                if not had_map_r:
                    Debug.log(f"  {blender_bone_name} [LS]: Set rest pose from armature: ({euler_deg[0]:.1f}, {euler_deg[1]:.1f}, {euler_deg[2]:.1f})")
                else:
                    Debug.log(f"  {blender_bone_name} [LS]: Mapping file has map_r=({existing_euler[0]:.1f}, {existing_euler[1]:.1f}, {existing_euler[2]:.1f}), using armature instead")

            rest_pose_count += 1            
            rest_pose_count += 1
    
    Debug.log(f"Extracted rest pose for {rest_pose_count} bone(s) from armature")


# Animation #############################################################

def collect_actions_for_export_from_armature(armature: bpy.types.Object,
                                            use_nla: bool = True,
                                            export_clean_threshold: float = 0.0
                                            ) -> List[ExportActionData]:
    """Collect actions to export based on NLA tracks or active action.
    
    Args:
        armature: Armature object
        use_nla: If True, check NLA tracks first; if False, use only active action
        export_clean_threshold: Threshold for FCurve cleaning (0 = disabled)
        
    Returns:
        List of ExportActionData objects containing action export information
    """
    actions_to_export = []
    
    if not armature.animation_data:
        Debug.log_warning("  Warning: No animation data on armature")
        return actions_to_export
    
    # Try to get actions from NLA tracks
    if use_nla and armature.animation_data.nla_tracks:
        Debug.log("\nCollecting actions from NLA tracks:")
        
        for track_idx, track in enumerate(armature.animation_data.nla_tracks):
            if track.mute:
                Debug.log(f"  Track {track_idx} '{track.name}': Muted (skipping)")
                continue
            
            Debug.log(f"  Track {track_idx} '{track.name}':")
            
            for strip_idx, strip in enumerate(track.strips):
                # Skip non-GANI strips (includes muted, layout, or negative-time strips)
                if not util_blender_animation.is_relevant_strip(strip):
                    Debug.log(f"    Strip {strip_idx} '{getattr(strip, 'name', '<unknown>')}': Skipping (not a GANI strip)")
                    continue

                # Calculate frame range (use strip's frame range)
                frame_start = int(strip.frame_start)
                frame_end = int(strip.frame_end)

                
                # Use strip name if available, otherwise action name
                source = f'NLA Track "{track.name}" Strip "{strip.name}"'
                
                # Create export action data
                export_action = ExportActionData(
                    action=strip.action,
                    frame_start=frame_start,
                    frame_end=frame_end,
                    source=source,
                    export_clean_threshold=export_clean_threshold
                )
                
                actions_to_export.append(export_action)
                Debug.log(f"    Strip {strip_idx}: {export_action.to_string()}")
        
        if actions_to_export:
            Debug.log(f"\nFound {len(actions_to_export)} action(s) in NLA tracks")
            return actions_to_export
        else:
            Debug.log("\nNo unmuted NLA strips found, falling back to active action")
    
    # Fallback to active action
    if armature.animation_data.action:
        action = armature.animation_data.action
        
        # Skip layout track action (metadata only, not animation data)
        if '.layout.' in action.name.lower():
            Debug.log(f"\nActive action '{action.name}' is a layout track (skipping - metadata only)")
        else:
            frame_start = int(action.frame_range[0])
            frame_end = int(action.frame_range[1])
            
            # Skip animations in negative time range
            if frame_end <= 0:
                Debug.log(f"\nActive action '{action.name}' is in negative time range {frame_start} to {frame_end} (skipping)")
            else:
                # Create export action data
                export_action = ExportActionData(
                    action=action,
                    frame_start=frame_start,
                    frame_end=frame_end,
                    source='Active Action',
                    export_clean_threshold=export_clean_threshold
                )
                
                actions_to_export.append(export_action)
                Debug.log(f"\nUsing active action: {export_action.to_string()}")
    else:
        Debug.log_warning("\n  Warning: No active action and no NLA strips found")
    
    return actions_to_export


def get_bone_keyframe_numbers_from_action(action: bpy.types.Action, 
                                          bone_name: str, 
                                          segment_type: SegmentType, 
                                          frame_start: int, 
                                          frame_end: int,
                                          bone_params: Optional[BoneParameters] = None,
                                          fcurve_cache: Optional[util_blender_animation.FCurveCache] = None
                                          ) -> List[int]:
    """Get the actual frames that have keyframes for a specific bone and segment type.
    
    Note: This function returns frames in the same coordinate system as frame_start/frame_end.
    When exporting from NLA strips, frame_start/frame_end are absolute timeline positions,
    so returned frames are also absolute. When exporting active actions, both are relative
    to the action's frame range.
    
    Args:
        action: Blender action to check
        bone_name: Name of the bone
        segment_type: Type of segment (rotation or location)
        frame_start: First frame in export range (absolute for NLA, action-relative for active action)
        frame_end: Last frame in export range (absolute for NLA, action-relative for active action)
        bone_params: Optional bone parameters to check for special cases (e.g., as_ik_up)
        fcurve_cache: Optional pre-built util_blender_animation.FCurveCache for fast lookups (20-100× faster than scanning action.fcurves)
        
    Returns:
        Sorted list of frame numbers that have keyframes (in same coordinate system as frame_start/frame_end)
    """
    keyframe_frames = set()
    
    # Check for special case: as_ik_up vectors are stored as location in Blender
    # but exported as rotation (quaternion) tracks
    is_ik_up_vector = (bone_params and bone_params.as_ik_up and 
                       segment_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF])
    
    # Determine which properties to check based on segment type
    if is_ik_up_vector:
        # IK up vector: stored as location in Blender, exported as quaternion
        property_names = ['location']
    elif segment_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
        # Rotation - check rotation_quaternion or rotation_euler
        property_names = ['rotation_quaternion', 'rotation_euler']
    elif segment_type in [SegmentType.VECTOR3, SegmentType.VECTOR_DIFF,
                          SegmentType.FLOAT, SegmentType.VECTOR2]:
        # Location (FLOAT and VECTOR2 share the same location data_path)
        property_names = ['location']
    else:
        return []
    
    # Get action's internal frame range to determine if we need offset conversion
    # When action is in NLA strip, keyframes are at action-relative frames but we need absolute
    action_frame_start = int(action.frame_range[0])
    
    # Calculate offset: difference between export range start and action's internal start
    # For NLA strips: frame_start is absolute (strip.frame_start), action_frame_start is action-relative
    # For active action: both are the same, so offset = 0
    frame_offset = frame_start - action_frame_start
    
    # Collect all keyframe frames from relevant fcurves
    if fcurve_cache and not fcurve_cache.is_empty():
        # Use cache (fast path - 20-100× faster)
        for property_name in property_names:
            for fcurve in fcurve_cache.get_fcurves_for_bone(bone_name, property_name):
                for keyframe_point in fcurve.keyframe_points:
                    # keyframe_point.co[0] is always relative to action's internal frame range
                    action_relative_frame = int(keyframe_point.co[0])
                    
                    # Convert to export coordinate system (absolute for NLA, action-relative for active)
                    export_frame = action_relative_frame + frame_offset
                    
                    # Filter by export range
                    if frame_start <= export_frame <= frame_end:
                        keyframe_frames.add(export_frame)
    else:
        # Fall back to scanning action.fcurves (slow path - for backward compatibility)
        data_paths = [util_blender_animation.build_data_path_for_bone(bone_name, prop) for prop in property_names]
        for fcurve in util_blender_animation.iter_action_fcurves(action):
            if fcurve.data_path in data_paths:
                for keyframe_point in fcurve.keyframe_points:
                    # keyframe_point.co[0] is always relative to action's internal frame range
                    action_relative_frame = int(keyframe_point.co[0])
                    
                    # Convert to export coordinate system (absolute for NLA, action-relative for active)
                    export_frame = action_relative_frame + frame_offset
                    
                    # Filter by export range
                    if frame_start <= export_frame <= frame_end:
                        keyframe_frames.add(export_frame)
    
    # Validate and add mandatory start frame
    if frame_start not in keyframe_frames:
        Debug.log_warning(f"No keyframe at frame_start {frame_start} for bone '{bone_name}' {segment_type}. Sampling from frame_start.")
    keyframe_frames.add(frame_start)
    
    # Add end frame for animated tracks (static tracks have only start frame)
    if len(keyframe_frames) > 1:
        keyframe_frames.add(frame_end)
    
    return sorted(list(keyframe_frames))


def export_keyframes_track(armature: bpy.types.Object, 
                           blender_bone_name: str,
                           bone_params: BoneParameters, 
                           segment_type: SegmentType,
                           frame_start: int, 
                           frame_end: int,
                           is_static: bool, 
                           action: bpy.types.Action = None,
                           rig_unit_type: Optional[RigUnitType] = None,
                           fcurve_cache: Optional[util_blender_animation.FCurveCache] = None,
                           transform_cache: Optional[util_transforms.TransformsCache] = None,
                           use_object_level: bool = False) -> List['AnimKeyframe']:
    """Export a single track data segment (one segment of a bone's animation).
    
    This is the export counterpart to import_keyframes_track().
    
    Args:
        armature: Armature object
        blender_bone_name: Name of the bone in Blender
        bone_params: BoneParameters from mapping file (rotation_offset, axis_map, space_r, space_l, as_ik_up)
        segment_type: Type of this segment (from layout track metadata)
        frame_start: First frame to export
        frame_end: Last frame to export
        is_static: Whether this is a static track (single frame)
        action: Blender action to get actual keyframe frames from
        rig_unit_type: Type of rig unit (determines if world space transforms are needed)
        fcurve_cache: Optional pre-built util_blender_animation.FCurveCache for fast lookups
        transform_cache: Optional pre-computed transform cache for all bones/frames
        
    Returns:
        List of AnimKeyframe objects
    """
    # Determine frame range
    if is_static:
        export_frames = [frame_start]
    elif use_object_level and action:
        # Root motion is on the armature object: get frame list from object FCurves
        export_frames = _get_object_keyframe_numbers(action, segment_type, frame_start, frame_end)
    elif action:
        # Get actual keyframe frames from Blender fcurves
        export_frames = get_bone_keyframe_numbers_from_action(action, blender_bone_name, segment_type, frame_start, frame_end, bone_params, fcurve_cache)
    else:
        # Fallback: export all frames
        export_frames = list(range(frame_start, frame_end + 1))
    
    # ── Non-static track validation ──────────────────────────────────────────
    # The GANI binary format reads animated keyframes in a loop:
    #   do { read AnimKeyframe; frameIndex += FrameCount; } while (frameIndex < FrameCount);
    # so the accumulated frame deltas MUST reach at least FrameCount
    # (= frame_end - frame_start). If after FCurve cleaning only 1 keyframe
    # remains (frame_start), the loop has no data to read and parses garbage.
    if not is_static:
        # 1. Ensure frame_end is always present so deltas sum to FrameCount
        if len(export_frames) < 2 or export_frames[-1] < frame_end:
            if frame_end not in export_frames:
                Debug.log(
                    f"Non-static track '{blender_bone_name}' ({segment_type.name}): "
                    f"only {len(export_frames)} keyframe(s) found after FCurve cleaning, "
                    f"missing frame_end ({frame_end}). Adding it to prevent invalid binary output."
                )
                export_frames.append(frame_end)
                export_frames = sorted(set(export_frames))

        # 2. Fill gaps > 255 with intermediate frames (8-bit delta limit)
        export_frames, inserted = util_fcurve_processing.insert_intermediate_frames(export_frames)
        if inserted:
            Debug.log(
                f"Non-static track '{blender_bone_name}' ({segment_type.name}): "
                f"inserted {inserted} intermediate frame(s) to keep frame deltas within the 255-frame binary limit."
            )
    # ────────────────────────────────────────────────────────────────────────

    Debug.log(f"    Collected keyed frames {len(export_frames)}")

    if segment_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
        # Rotation segment
        return export_rotation_segment(
            armature, 
            blender_bone_name, 
            bone_params,
            export_frames, 
            frame_start, 
            is_static, 
            rig_unit_type,
            transform_cache,
            use_object_level=use_object_level,
        )
    
    elif segment_type in [SegmentType.VECTOR3, SegmentType.VECTOR_DIFF]:
        # Location segment
        return export_location_segment(
            armature, 
            blender_bone_name, 
            bone_params,
            export_frames, 
            frame_start, 
            is_static, 
            rig_unit_type,
            transform_cache,
            use_object_level=use_object_level,
        )

    elif segment_type == SegmentType.FLOAT:
        # Raw scalar channel stored as location[0] — no axis-swap, local space only.
        return export_location_segment(
            armature, blender_bone_name, bone_params,
            export_frames, frame_start, is_static,
            rig_unit_type, transform_cache,
            no_coordinate_transform=True, num_components=1
        )

    elif segment_type == SegmentType.VECTOR2:
        # Raw [x, y] channel stored as location[0,1] — no axis-swap, local space only.
        return export_location_segment(
            armature, blender_bone_name, bone_params,
            export_frames, frame_start, is_static,
            rig_unit_type, transform_cache,
            no_coordinate_transform=True, num_components=2
        )

    else:
        # Unsupported segment type
        if segment_type == SegmentType.VECTOR4:
            # VECTOR4 has no FCurve representation; export produces a zeroed segment.
            # Round-trip fidelity requires the layout action to preserve the VECTOR4
            # segment type so the exporter knows to include it.
            Debug.log_warning(
                f"    Segment type VECTOR4 on bone '{blender_bone_name}' is not supported "
                f"as Blender FCurves. Exporting zeroed segment. Round-trip fidelity "
                f"requires the layout action to contain this track's VECTOR4 segment type."
            )
        else:
            Debug.log_warning(f"    Warning: Unsupported segment type {segment_type}")
        return []

def _get_rotation_transform_fn(bone_params: BoneParameters, 
                               armature: bpy.types.Object,
                               blender_bone_name: str, 
                               space_bone: Optional[str],
                               rig_unit_type: Optional[RigUnitType],
                               transform_cache: Optional[util_transforms.TransformsCache] = None,
                               use_object_level: bool = False
                               ):
    """Return a callable that produces rotation quaternion for a given frame.

    This helper eliminates code duplication between object-level root motion
    tracks, as_ik_up conversion, and normal bone rotation paths.

    Args:
        bone_params: Bone parameters (contains as_ik_up data if applicable)
        armature: Armature object
        blender_bone_name: Name of the bone in Blender
        space_bone: Custom space bone name (or None for default space)
        rig_unit_type: Rig unit type (determines local vs world space for normal tracks)
        transform_cache: Optional pre-computed transform cache
        use_object_level: If True, read rotation from the armature object instead of a pose bone

    Returns:
        Callable that takes (frame: int) and returns Quaternion
    """
    if use_object_level:
        # Object-level rotation (root motion) is stored on the armature object
        # rather than a pose bone.
        def get_rotation_object_level(frame: int) -> Quaternion:
            if transform_cache:
                rot = transform_cache.get_object_rotation(frame)
                if rot is not None:
                    return rot
                Debug.log_warning(
                    f"Export rotation: TransformCache missing armature object rotation for frame {frame}; "
                    f"falling back to armature.matrix_world"
                )
            bpy.context.scene.frame_set(frame)
            return armature.matrix_world.to_quaternion()

        return get_rotation_object_level

    if bone_params.as_ik_up:
        # as_ik_up path: convert directional location to rotation
        as_ik_up_data = bone_params.as_ik_up
        axis = as_ik_up_data.axis
        base_bone_name = as_ik_up_data.bone_base
        
        def get_rotation_as_ik_up(frame: int) -> Quaternion:
            if transform_cache:
                ik_location, _ = transform_cache.get_world(blender_bone_name, frame, space_bone)
                base_location, _ = transform_cache.get_world(base_bone_name, frame, space_bone)
            else:
                ik_location, _ = util_transforms.get_world_space_transform(armature, blender_bone_name, frame, space_bone)
                base_location, _ = util_transforms.get_world_space_transform(armature, base_bone_name, frame, space_bone)
            return util_transforms.reverse_directional_location(ik_location, base_location, axis)
        
        return get_rotation_as_ik_up
    else:
        # Normal rotation path: read quaternion directly
        use_world_space = RigUnitType.is_world_space_unit_type(rig_unit_type)
        
        def get_rotation_normal(frame: int) -> Quaternion:
            if transform_cache:
                if use_world_space:
                    _, quat = transform_cache.get_world(blender_bone_name, frame, space_bone)
                else:
                    _, quat = transform_cache.get_local(blender_bone_name, frame)
            else:
                if use_world_space:
                    _, quat = util_transforms.get_world_space_transform(armature, blender_bone_name, frame, space_bone)
                else:
                    _, quat = util_transforms.get_local_space_transform(armature, blender_bone_name, frame)
            return quat
        
        return get_rotation_normal

def export_rotation_segment(armature: bpy.types.Object, 
                            blender_bone_name: str,
                            bone_params: BoneParameters, 
                            export_frames: List[int],
                            frame_start: int, 
                            is_static: bool, 
                            rig_unit_type: Optional[RigUnitType] = None,
                            transform_cache: Optional[util_transforms.TransformsCache] = None,
                            use_object_level: bool = False,
                            ) -> List['AnimKeyframe']:
    """Export rotation segment keyframes."""
    keyframes = []
    # Debug.start_timer("export_rotation_segment")
    
    # POINT 4 OPTIMIZATION: Extract loop-invariant setup and use pluggable transform function
    # These are constant across all frames, so extract once to avoid redundant lookups
    rotation_offset = bone_params.rotation_offset
    rotation_axis_map = bone_params.rotation_axis_map
    
    if use_object_level:
        # Track maps to the armature object via [armature] mapping target.
        # Rotation FCurves are stored as bare 'rotation_quaternion' on the object.
        # Read the object rotation directly — no bone-space or rest-pose corrections.
        space_bone = None
        space_r_value = None
        map_r_dict = None
    else:
        # For as_ik_up bones, use space_ik instead of space_r for the space bone
        # This is because space_ik defines the transformation constraint space for IK targets
        if bone_params.as_ik_up:
            space_bone = fwrap_metadata.extract_space_bone_name(bone_params.space_ik)
        else:
            space_bone = fwrap_metadata.extract_space_bone_name(bone_params.space_r)
        
        # Extract rest pose correction parameters
        space_r_value = bone_params.space_r
        map_r_dict = bone_params.map_r

    # Get rotation transform function (varies by mode / space type)
    get_rotation = _get_rotation_transform_fn(
        bone_params, armature, blender_bone_name, space_bone,
        rig_unit_type, transform_cache, use_object_level=use_object_level
    )

    # Unified frame loop for both as_ik_up, normal rotation, and object-level rotation
    prev_frame = frame_start  # Track previous frame for relative delta computation
    prev_blender_quat_transformed: Quaternion = None
    for frame in export_frames:
        # Set frame explicitly for performance (if no cache present)
        if not transform_cache:
            bpy.context.scene.frame_set(frame)
        
        # Get rotation using appropriate method (as_ik_up, normal, or object-level)
        blender_quat: Quaternion = get_rotation(frame)

        # Apply reverse rest pose corrections (must happen BEFORE axis mapping and offsets)
        # World space tracks (space_r=world): reverse offset_r using simple multiplication
        # Local space tracks (default): reverse map_r using similarity transformation
        if space_r_value and isinstance(space_r_value, dict) and space_r_value.get('space') == 'WORLD':
            # World space track - reverse offset_r if present
            if rotation_offset:
                # Use first offset as the offset_r (world space offset)
                blender_quat = util_transforms.reverse_rest_pose_correction_world(blender_quat, rotation_offset[0])
        elif map_r_dict:
            # Local space track - reverse similarity transformation
            blender_quat = util_transforms.reverse_rest_pose_correction_local(blender_quat, map_r_dict)

        # Apply reverse transformations (offsets, axis mapping)
        blender_quat_transformed = util_transforms.apply_reverse_transforms(blender_quat, rotation_offset, rotation_axis_map)
        if prev_blender_quat_transformed is not None:
            blender_quat_transformed.make_compatible(prev_blender_quat_transformed)
        prev_blender_quat_transformed = blender_quat_transformed.copy()
        
        # Convert to Fox Engine coordinate system
        fox_quat_final = util_transforms.blender_to_fox_quaternion(blender_quat_transformed)

        # Create keyframe with relative frame delta from previous frame
        if is_static:
            frame_delta = 0
        elif not keyframes:
            frame_delta = 0  # First keyframe is always delta=0
        else:
            frame_delta = frame - prev_frame
            if frame_delta < 1:
                Debug.log_warning(f"Export rotation: Invalid frame_delta {frame_delta} at frame {frame} for bone '{blender_bone_name}'. Clamping to 1.")
                frame_delta = 1
            elif frame_delta > 255:
                Debug.log_error(f"Export rotation: INVALID FILE - frame_delta {frame_delta} exceeds the 255-frame binary limit at frame {frame} for bone '{blender_bone_name}'. Delta clamped to 255 but this corrupts all subsequent keyframe timings. Reduce the export clean threshold.")
                frame_delta = 255
        prev_frame = frame
        keyframe = AnimKeyframe(frame=frame_delta, value=fox_quat_final)
        keyframes.append(keyframe)
    
    # Debug.stop_timer("export_rotation_segment")
    return keyframes

def export_location_segment(armature: bpy.types.Object, 
                            blender_bone_name: str,
                            bone_params: BoneParameters, 
                            export_frames: List[int],
                            frame_start: int, 
                            is_static: bool,
                            rig_unit_type: Optional[RigUnitType] = None,
                            transform_cache: Optional[util_transforms.TransformsCache] = None,
                            no_coordinate_transform: bool = False,
                            num_components: int = 3,
                            use_object_level: bool = False,
                            ) -> List['AnimKeyframe']:
    """Export location segment keyframes.

    Args:
        no_coordinate_transform: When True (used for FLOAT and VECTOR2 segment types),
            skips the Blender↔Fox axis-swap, always uses local space, and returns only
            the first ``num_components`` raw channel values. This is correct because
            FLOAT/VECTOR2 are auxiliary data channels (e.g. blend weights, parameters)
            stored without any coordinate-system conversion during import.
        num_components: Number of output components to include in each keyframe value
            when ``no_coordinate_transform=True``. 1 for FLOAT, 2 for VECTOR2, 3 for
            VECTOR3 (default).

    Note: When space_l=custom,<custom_bone> is used, the import creates a Copy Location
    constraint with X and Y axes inverted. During export we reverse this by inverting
    X and Y again. This does NOT apply when no_coordinate_transform=True.
    """
    keyframes = []
    # Debug.start_timer("export_location_segment")

    if use_object_level:
        # Track maps to the armature object via [armature] mapping target.
        # Location FCurves are stored as bare 'location' on the object.
        # Read the object location directly — no bone-space or axis corrections.
        space_bone = None
        use_world_space = False  # not used when use_object_level=True
        invert_xy = False
    else:
        # Get custom space if specified (constant across all frames)
        # Use the same extraction logic as rotation export for consistency
        space_bone = fwrap_metadata.extract_space_bone_name(bone_params.space_l)

        # For FLOAT/VECTOR2 (no_coordinate_transform=True): raw channel, always local
        # space, no axis-swap, no custom-space or invert-XY correction.
        if no_coordinate_transform:
            use_world_space = False
            invert_xy = False
            space_bone = None
        else:
            # Check if we need to invert X and Y (when using custom space bone)
            # Import creates constraint with invert_x=True, invert_y=True when custom_bone is specified
            # So we need to reverse that during export
            invert_xy = space_bone is not None
            # is_world_space result is constant across all frames
            use_world_space = RigUnitType.is_world_space_unit_type(rig_unit_type)
    
    # For regular location: read and convert per frame
    prev_frame = frame_start  # Track previous frame for relative delta computation
    for frame in export_frames:
        # Set frame explicitly for performance (if no cache present)
        if not transform_cache:
            bpy.context.scene.frame_set(frame)

        if use_object_level:
            # Read object-level location directly from FCurves or armature transform
            if transform_cache:
                blender_location = transform_cache.get_object_location(frame)
                if blender_location is None:
                    Debug.log_warning(
                        f"Export location: TransformCache missing armature object location for frame {frame}; "
                        f"falling back to armature.matrix_world"
                    )
                    blender_location = Vector((0, 0, 0))
            else:
                blender_location = armature.matrix_world.to_translation()
        # Read location (using pre-determined space)
        elif transform_cache:
            if use_world_space:
                blender_location, _ = transform_cache.get_world(blender_bone_name, frame, space_bone)
            else:
                blender_location, _ = transform_cache.get_local(blender_bone_name, frame)
        else:
            if use_world_space:
                # Use world space transforms for ORIENTATION, TWO_BONE, ARM
                blender_location, _ = util_transforms.get_world_space_transform(armature, blender_bone_name, frame, space_bone)
            else:
                # Use local space transforms for other types (LOCAL_ORIENTATION, TRANSFORM, ROOT, etc.)
                blender_location, _ = util_transforms.get_local_space_transform(armature, blender_bone_name, frame)
        
        # Reverse X and Y inversion if custom space bone was used during import
        if invert_xy:
            blender_location = blender_location.copy()
            blender_location.x = -blender_location.x
            blender_location.y = -blender_location.y

        # Convert to Fox Engine coordinate system (or take raw channels for FLOAT/VECTOR2)
        if no_coordinate_transform:
            fox_location = list(blender_location)[:num_components]
        else:
            fox_location = util_transforms.blender_to_fox_vector(blender_location)
        
        # Create keyframe with relative frame delta from previous frame
        if is_static:
            frame_delta = 0
        elif not keyframes:
            frame_delta = 0  # First keyframe is always delta=0
        else:
            frame_delta = frame - prev_frame
            if frame_delta < 1:
                Debug.log_warning(f"Export location: Invalid frame_delta {frame_delta} at frame {frame} for bone '{blender_bone_name}'. Clamping to 1.")
                frame_delta = 1
            elif frame_delta > 255:
                Debug.log_error(f"Export location: INVALID FILE - frame_delta {frame_delta} exceeds the 255-frame binary limit at frame {frame} for bone '{blender_bone_name}'. Delta clamped to 255 but this corrupts all subsequent keyframe timings. Reduce the export clean threshold.")
                frame_delta = 255

        prev_frame = frame
        keyframe = AnimKeyframe(frame=frame_delta, value=fox_location)
        keyframes.append(keyframe)
    
    # Debug.stop_timer("export_location_segment")
    return keyframes


def bone_has_fcurves_for_segment(
        bone_name: str,
        segment_type: SegmentType,
        bone_params: Optional[BoneParameters],
        fcurve_cache: Optional[util_blender_animation.FCurveCache]) -> bool:
    """Return True if the FCurve cache contains curves for the expected property
    of a segment type on the given bone.

    Used to detect per-GANI segment variation in old-format MTARs: if the layout
    defines a segment but this particular action has no FCurves for it, the segment
    should be omitted from the exported GANI.

    Note: FLOAT and VECTOR2 both use the 'location' property (like VECTOR3) and are
    distinguished only by which channel indices are present. For *presence* detection
    (does this segment exist in this action?) we only need to know whether ANY location
    FCurve exists — the segment type is already known from the layout metadata.

    Args:
        bone_name: Blender bone name to check.
        segment_type: Expected segment type from layout metadata.
        bone_params: BoneParameters for the bone (used to detect as_ik_up special case).
        fcurve_cache: Cache to query. Returns True when cache is None (can't determine).

    Returns:
        True if FCurves are present (or cache unavailable); False if definitely absent.
    """
    if fcurve_cache is None:
        return True  # Can't determine — assume present to avoid false-negatives

    if segment_type in (SegmentType.QUAT, SegmentType.QUAT_DIFF):
        # IK-up bones store rotation data as location FCurves
        if bone_params and bone_params.as_ik_up:
            return bool(fcurve_cache.get_fcurves_for_bone(bone_name, 'location'))
        return (
            bool(fcurve_cache.get_fcurves_for_bone(bone_name, 'rotation_quaternion')) or
            bool(fcurve_cache.get_fcurves_for_bone(bone_name, 'rotation_euler'))
        )

    elif segment_type in (SegmentType.VECTOR3, SegmentType.VECTOR_DIFF,
                          SegmentType.FLOAT, SegmentType.VECTOR2):
        return bool(fcurve_cache.get_fcurves_for_bone(bone_name, 'location'))

    elif segment_type == SegmentType.VECTOR4:
        return False  # Never stored as FCurves — always pass through from layout

    return False


def export_gani_track_from_action(armature: bpy.types.Object,
                                  action: bpy.types.Action,
                                  track_idx: int,
                                  frame_start: int,
                                  frame_end: int,
                                  layout_metadata: Optional[fwrap_metadata.TrackMetaData],
                                  track_segment_bone_mapping: TrackSegmentBoneMapping,
                                  force_highest_bit_encoding: bool = False,
                                  fcurve_cache: Optional[util_blender_animation.FCurveCache] = None,
                                  transform_cache: Optional[util_transforms.TransformsCache] = None
                                  ) -> TrackUnitWrapper:
    """Export a GaniTrack (all segments for one track).
    
    This is the export counterpart to import_gani_track().
    Gets track structure (segments) from layout action and animation-specific
    unit flags from the animation action.
    
    For multi-segment tracks, each segment maps to a different Blender bone
    (e.g., "RIG_SKL_010_LSHLD", "RIG_SKL_010_LSHLD_1", "RIG_SKL_010_LSHLD_2") as defined in the track mapping file.
    
    Args:
        armature: Armature object
        track_idx: Index of this track in the layout
        track_segment_bone_mapping: Unified mapping from (track_idx, segment_idx) to (bone_name, fox_mapping_params)
        frame_start: First frame to export
        frame_end: Last frame to export
        action: Animation action containing keyframes
        layout_metadata: fwrap_metadata.TrackMetaData instance containing track structure metadata for this track
        fcurve_cache: Optional pre-built util_blender_animation.FCurveCache for fast lookups
        force_highest_bit_encoding: If True, use highest available bit sizes for all segments
        transform_cache: Optional pre-computed transform cache
        
    Returns:
        GaniTrack object with all keyframes tracks
    """

    # Check imputs ----------------------------------------------

    # Get the base track info (first check if track exists at all)
    if not track_segment_bone_mapping.has_track(track_idx):
        # Create empty track to preserve structure - no metadata available
        Debug.log_warning(f"      Warning: No mapping for track {track_idx}, creating empty track to skipping track")
        return TrackUnitWrapper(
            name=f"Track_{track_idx}",
            segments_track_data=[],
            unit_flags=[TrackUnitFlags.NONE]
        )
    
    # Get base track info (for segment 0)
    base_mapping = track_segment_bone_mapping.get_base_mapping(track_idx)
    if not base_mapping:
        Debug.log_warning(f"      Warning: No base segment mapping for track {track_idx}, creating empty track to skipping track")
        # Create empty track to preserve structure - no base segment
        return TrackUnitWrapper(
            name=f"Track_{track_idx}",
            segments_track_data=[],
            unit_flags=[TrackUnitFlags.NONE]
        )
    
    base_blender_bone_name, base_fox_mapping_params = base_mapping
    if not base_blender_bone_name:
        Debug.log_warning(f"      Warning: No base bone name for track {track_idx}, creating empty track to skipping track")
        # Create empty track to preserve structure
        return TrackUnitWrapper(
            name=f"Track_{track_idx}",
            segments_track_data=[],
            unit_flags=[TrackUnitFlags.NONE]
        )
    
    # Get the fox track name from the base mapping params
    fox_track_name = base_fox_mapping_params.fox_name
    
    # Strip multi-segment suffix if present
    base_fox_track_name, _ = util_parsing.parse_segment_suffix(fox_track_name)

    # Prepare meta data from layout / shared header and per action header ----------------------------------------------
    # layout_metadata is passed in directly (fwrap_metadata.TrackMetaData instance for this track)
    if layout_metadata is None:
        # No metadata found and FCurve fallback also produced nothing (bone has no animation)
        Debug.log_warning(f"      Warning: No metadata for track '{base_fox_track_name}' (blender bone: '{base_blender_bone_name}') and no FCurves found — creating empty track to skipping track")
        # Create empty track to preserve structure
        return TrackUnitWrapper(
            name=base_blender_bone_name,
            segments_track_data=[],
            unit_flags=[TrackUnitFlags.NONE]
        )
    
    # Debug.start_timer(f"export_gani_track_from_action(track={track_idx})")

    # Merge per-action overrides into layout metadata (if any)
    action_meta = None
    merged_metadata = layout_metadata
    if action:
        action_meta = fwrap_metadata.build_track_metadata_from_action(action, base_fox_track_name)
        if action_meta:
            Debug.log(f"      Applying action-level overrides for track '{base_fox_track_name}' from action '{action.name}'")
            merged_metadata = fwrap_metadata.merge_track_metadata(layout_metadata, action_meta)

    # Track whether the action explicitly overrides segment types (segments= in custom prop).
    # When True, skip FCurve-presence filtering so user-forced types are always exported.
    has_segment_override = bool(action_meta and action_meta.segment_types)

    # Use merged unit_flags in layout_metadata (action overrides applied if present)
    unit_flags_int = int(merged_metadata.unit_flags) if merged_metadata.unit_flags is not None else 0

    segment_types = merged_metadata.segment_types

    Debug.log(f"      Exporting track {track_idx}: '{base_fox_track_name}' -> {len(segment_types)} segments, flags={unit_flags_int}")

    # When the mapping file routes this track to '[armature]', keyframes are stored
    # on the armature object itself (bare 'location' / 'rotation_quaternion' FCurves)
    # rather than on a pose bone.
    use_object_level_for_track = (base_blender_bone_name == fwrap_mapping.ARMATURE_TARGET_NAME)
    if use_object_level_for_track:
        Debug.log(f"      Track '{base_fox_track_name}': [armature] mapping target — using object-level FCurves")
    
    # Extract keyframes for each segment
    keyframes_tracks = []
    
    # Check if this is a static track (only one keyframe needed)
    is_static = (unit_flags_int & TrackUnitFlags.IS_STATIC) != 0
    
    # Convert unit_flags int to list of TrackUnitFlags
    unit_flags_list = TrackUnitFlags.int_to_track_unit_flags(unit_flags_int)
    
    for segment_idx, segment_type in enumerate(segment_types):
        segment_fox_mapping_params = None

        # Look up the specific bone and parameters for this segment
        if track_segment_bone_mapping:
            segment_mapping = track_segment_bone_mapping.get_segment_mapping(track_idx, segment_idx)
            if segment_mapping:
                # Segment-specific bone found
                segment_bone_name, segment_fox_mapping_params = segment_mapping
                segment_type_str = "multi-segment" if track_segment_bone_mapping.is_multi_segment_track(track_idx) else "single-segment"
                Debug.log(f"        Mapping Segment {segment_idx}: '{segment_bone_name}' ({segment_type_str})")
            else:
                # Fallback to base bone (should not happen with proper mapping)
                segment_bone_name, segment_fox_mapping_params = base_blender_bone_name, base_fox_mapping_params
                Debug.log_warning(f"        Warning: Missing mapping. Segment {segment_idx}: '{segment_bone_name}' (fallback to base (track) bone)")

        # Skip segments absent from this action (per-GANI variation in old-format MTARs).
        # VECTOR4 is always included since it has no FCurve representation — its
        # presence is purely determined by layout metadata.
        # When the action has an explicit segments= override, skip filtering entirely.
        # When root motion is on the armature object, the bone FCurves are gone but
        # we must still export the segment — skip the presence check in that case.
        if (fcurve_cache
                and not has_segment_override
                and not use_object_level_for_track
                and segment_type != SegmentType.VECTOR4):
            if not bone_has_fcurves_for_segment(
                    segment_bone_name, segment_type, segment_fox_mapping_params, fcurve_cache):
                Debug.log(
                    f"        Skipping segment {segment_idx} ({segment_type.name}) "
                    f"for '{segment_bone_name}': no FCurves in this action"
                )
                continue

        # Check if this bone exists in the armature.
        # When use_object_level_for_track is True (i.e. [armature] mapping target),
        # segment_bone_name == ARMATURE_TARGET_NAME which is not a real pose bone —
        # allow it through so the object-level FCurve reader is reached.
        if segment_bone_name and (segment_bone_name in armature.pose.bones or use_object_level_for_track):
            # Debug.start_timer(f"export_keyframes_track(segment_bone_name={segment_bone_name})")
            # Export keyframes for this segment
            keyframes = export_keyframes_track(
                armature,
                segment_bone_name,
                segment_fox_mapping_params,
                segment_type, frame_start,
                frame_end,
                is_static,
                action,
                merged_metadata.rig_unit_type,
                fcurve_cache,
                transform_cache,
                use_object_level=use_object_level_for_track,
            )
            # Debug.stop_timer(f"export_keyframes_track(segment_bone_name={segment_bone_name})")

            # Get component_bit_size from metadata if available, otherwise use a type-safe default
            component_bit_size = get_default_bit_size_for_segment(segment_type)
            if merged_metadata.component_bit_sizes and segment_idx < len(merged_metadata.component_bit_sizes):
                component_bit_size = merged_metadata.component_bit_sizes[segment_idx]

            # Optionally force highest bit encoding based on export setting
            if force_highest_bit_encoding:
                highest_bits = get_highest_bit_size_for_segment(segment_type)
                if highest_bits > 0:
                    component_bit_size = max(component_bit_size, highest_bits)

            # Final validation: ensure bit size is valid for this segment type
            component_bit_size = clamp_bit_size_for_segment(segment_type, component_bit_size)

            # Create TrackDataBlob
            data_blob = TrackDataBlob.from_keyframes(
                segment_type=segment_type,
                component_bit_size=component_bit_size,
                is_static=is_static,
                keyframes=keyframes
            )
            
            # Create KeyframesTrack
            keyframes_track = TrackDataBlobWrapper(
                name=segment_bone_name,
                segment_index=segment_idx,
                data_blob=data_blob
            )
            keyframes_tracks.append(keyframes_track)

        else:
            # Missing bone - create empty keyframes track
            Debug.log_warning(f"        Warning: Bone '{segment_bone_name}' not found in armature, creating empty segment")
            
            # Get component_bit_size from metadata if available, otherwise use default
            component_bit_size = 16
            if merged_metadata.component_bit_sizes and segment_idx < len(merged_metadata.component_bit_sizes):
                component_bit_size = merged_metadata.component_bit_sizes[segment_idx]

            # Respect force-highest-bit setting for empty segments as well
            if force_highest_bit_encoding:
                highest_bits = get_highest_bit_size_for_segment(segment_type)
                if highest_bits > 0:
                    component_bit_size = max(component_bit_size, highest_bits)

            # Create empty TrackDataBlob
            empty_data_blob = TrackDataBlob.from_keyframes(
                segment_type=segment_type,
                component_bit_size=component_bit_size,
                is_static=True,
                keyframes=[]
            )
            
            empty_keyframes_track = TrackDataBlobWrapper(
                name=segment_bone_name if segment_bone_name else f"Missing_{track_idx}_{segment_idx}",
                segment_index=segment_idx,
                data_blob=empty_data_blob
            )
            keyframes_tracks.append(empty_keyframes_track)
    
    # Debug.stop_timer(f"export_gani_track_from_action(track={track_idx})")

    # Create GaniTrack - use base track name for the track itself
    return TrackUnitWrapper(
        name=base_fox_track_name,
        segments_track_data=keyframes_tracks,
        unit_flags=unit_flags_list
    )


def _find_nla_strip_and_track_for_action(armature: bpy.types.Object,
                                           action: bpy.types.Action
                                           ) -> Tuple[Optional[bpy.types.NlaTrack], Optional[bpy.types.NlaStrip]]:
    """Return the first NLA track and strip on *armature* whose strip.action is *action*.

    Returns:
        (track, strip) if found, otherwise (None, None).
    """
    if not armature.animation_data or not armature.animation_data.nla_tracks:
        return (None, None)
    for track in armature.animation_data.nla_tracks:
        for strip in track.strips:
            if strip.action is action:
                return (track, strip)
    return (None, None)


def _collect_bones_for_transform_cache(track_segment_bone_mapping: TrackSegmentBoneMapping) -> Set[str]:
    """Collect all bone names that must be available in the transforms cache.

    This includes:
    - All bones explicitly listed in the mapping (track/segment bones)
    - Any custom-space bones referenced via space_r/space_l/space_ik
    - Any as_ik_up base bones used for directional-up calculations

    The cache is expected to provide transforms for these bones, or export will
    fail with a clear exception when they are missing.
    """
    bones: Set[str] = set()

    for bone_name, bone_params in track_segment_bone_mapping.get_all_mappings().values():
        if bone_name:
            bones.add(bone_name)

        # Space bones (custom coordinate spaces used by mapping params)
        for space_attr in (bone_params.space_r, bone_params.space_l, bone_params.space_ik):
            if space_attr:
                space_bone = fwrap_metadata.extract_space_bone_name(space_attr)
                if space_bone:
                    bones.add(space_bone)

        # as_ik_up uses a separate base bone whose transform is required
        if bone_params.as_ik_up and bone_params.as_ik_up.bone_base:
            bones.add(bone_params.as_ik_up.bone_base)

    # The special [armature] target is not a real pose bone and must not be used
    # as a cache key (it's handled via object-level transforms instead).
    bones.discard(fwrap_mapping.ARMATURE_TARGET_NAME)

    return bones


def export_gani_tracks_from_action(armature: bpy.types.Object,
                       action_data: ExportActionData,
                       track_segment_bone_mapping: Optional[TrackSegmentBoneMapping],
                       layout_metadata_dict: Dict[str, fwrap_metadata.TrackMetaData],
                       force_highest_bit_encoding: bool = False,
                       discard_empty_tracks: bool = False) -> List['TrackUnitWrapper']:
    """Export a single action as GANI track data.
    
    This is the export counterpart to the per-GANI processing in import_track_data().
    If both mapping and layout metadata dict are None, the function falls back to exporting
    tracks based on the armature bone list and any fcurves present on the action.
    
    Args:
        armature: Armature object
        action_data: ExportActionData containing action and export parameters
        track_segment_bone_mapping: Optional unified mapping from (track_idx, segment_idx) to (bone_name, fox_mapping_params). If None, fallback mode is used.
        layout_metadata_dict: Dictionary mapping fox track name to fwrap_metadata.TrackMetaData. If empty, fallback mode is used.
        force_highest_bit_encoding: If True, use highest available bit sizes for all segments
        discard_empty_tracks: If True, skip tracks with no segments (used for motion points to avoid empty placeholders)
        
    Returns:
        List of GaniTrack objects
    """
    action = action_data.action
    frame_start = action_data.frame_start
    frame_end = action_data.frame_end
    
    Debug.log(f"\n  Exporting action as gani: {action_data.to_string()}")

    try:
        gani_tracks = []
        
        # Process FCurves ------------------------------------------------------
        # Process FCurves for export (bake non-linear, clean redundant keyframes)
        # This may create a modified copy of the action if non-linear fcurves are found
        processed_action = action
        if action and action_data.export_clean_threshold > 0:
            clean_threshold = action_data.export_clean_threshold
            Debug.log(f"    Processing FCurves for export (clean_threshold={clean_threshold})...")
            try:
                # Temporarily assign action to armature for processing.
                # Use assign_action_to_datablock so the slot-based API is used
                # on Blender 4.4+/5 (direct anim_data.action assignment is
                # read-only once a slot is active).
                original_slot = None
                if not armature.animation_data:
                    armature.animation_data_create()
                anim_data = armature.animation_data
                if hasattr(anim_data, 'action_slot'):
                    original_slot = anim_data.action_slot
                else:
                    original_slot = anim_data.action

                util_blender_animation.assign_action_to_datablock(armature, action, slot_name=util_blender_animation.MTAR_ARMATURE_SLOT_NAME)

                export_result = util_fcurve_processing.bake_and_clean_export_fcurves(armature=armature, fcurve_clean_threshold=clean_threshold)

                processed_action = export_result['action']

                Debug.log(f"      Non-linear FCurves baked: {export_result['fcurves_baked']}")
                Debug.log(f"      FCurves cleaned: {export_result['fcurves_cleaned']}")
                Debug.log(f"      Already linear FCurves: {export_result['fcurves_already_linear']}")

                # Restore original action / slot
                if original_slot is not None:
                    if hasattr(anim_data, 'action_slot') and hasattr(original_slot, 'identifier'):
                        try:
                            anim_data.action_slot = original_slot
                        except Exception:
                            pass
                    else:
                        # Legacy path: original_slot holds the action itself
                        try:
                            anim_data.action = original_slot
                        except Exception:
                            pass
                else:
                    if armature.animation_data:
                        try:
                            # armature.animation_data.action = None
                            # util_blender_animation.assign_action_to_datablock(armature, None, slot_name=util_blender_animation.MTAR_ARMATURE_SLOT_NAME)
                            util_blender_animation.remove_action_from_datablock(armature)
                        except Exception:
                            pass
                
            except Exception as e:
                Debug.log_warning(f"    Warning: Failed to process export FCurves: {str(e)}")
                processed_action = action
        
        # Build Fcurve Cache ------------------------------------------------------
        # Once for this action (major performance optimization)
        # This eliminates 20-100× redundancy from scanning action.fcurves for every bone
        Debug.start_timer("build_fcurve_cache")
        fcurve_cache = util_blender_animation.FCurveCache.build(processed_action) if processed_action else None
        Debug.stop_timer("build_fcurve_cache")
        
        if fcurve_cache and not fcurve_cache.is_empty():
            Debug.log(f"    Built fcurve cache: {len(fcurve_cache.get_bones())} bones indexed")
        else:
            Debug.log_warning("    Built fcurve cache is empty")

        # Build Transform Cache ------------------------------------------------------
        # Once for this action (major performance optimization)
        # This eliminates thousands of scene.frame_set() calls during track export.
        #
        # IMPORTANT: evaluate processed_action (baked + cleaned + hemisphere-fixed by
        # Layer 3 in bake_and_clean_export_fcurves), NOT the original action.
        # The bake/clean step restores the original action to the armature before
        # returning, so a temporary re-assign is needed here. Without this, Layer 3
        # fixes processed_action FCurves but util_transforms.TransformsCache still evaluates the
        # original action and the hemisphere fix has no effect on exported values.

        # Compute which bones are actually relevant so the cache can skip the rest.
        # Armature-object transforms are always cached regardless of the filter.
        #
        # Must include bones referenced by mapping parameters (space_r/space_l/space_ik, as_ik_up base)
        # because those are used as custom space bones or IK bases during export.
        if track_segment_bone_mapping is not None:
            relevant_bone_names = _collect_bones_for_transform_cache(track_segment_bone_mapping)
            Debug.log(f"    util_transforms.TransformsCache bone filter: {len(relevant_bone_names)} bones from mapping (including space bones)")
        elif fcurve_cache is not None:
            relevant_bone_names = set(fcurve_cache.get_bones())
            Debug.log(f"    util_transforms.TransformsCache bone filter: {len(relevant_bone_names)} bones from FCurve cache")
        else:
            relevant_bone_names = None
            Debug.log("    util_transforms.TransformsCache bone filter: None (caching all bones)")

        transform_cache = util_transforms.TransformsCache(armature, frame_start, frame_end, bone_filter=relevant_bone_names)

        # Always identify the NLA track/strip for this action (if it exists).
        # This lets mute_nla_tracks() unmute the correct strip/track when building
        # the transforms cache (to avoid flat/empty exports).
        nla_track, nla_strip = _find_nla_strip_and_track_for_action(armature, action)

        with util_blender_animation.set_nla_solo(armature, keep_track=nla_track, keep_strip=nla_strip):
            if processed_action is not action:
                # If the action lives inside an NLA strip, temporarily swap the
                # strip's action pointer so the evaluation uses the processed copy
                # (which is the one that has been baked/cleaned/fixed).
                if nla_strip:
                    nla_strip.action = processed_action
                    util_blender_animation.remove_action_from_datablock(armature)  # force NLA evaluation
                    try:
                        transform_cache.build()
                    finally:
                        nla_strip.action = action
                else:
                    # No strip found (active-action export): assign processed action
                    # directly to the armature so the cache evaluates it correctly.
                    util_blender_animation.assign_action_to_datablock(armature, processed_action, slot_name=util_blender_animation.MTAR_ARMATURE_SLOT_NAME)
                    try:
                        transform_cache.build()
                    finally:
                        util_blender_animation.remove_action_from_datablock(armature)
            else:
                transform_cache.build()

        # Create Synthetic Mapping ------------------------------------------------------
        # If needed (when no mapping provided)
        if not track_segment_bone_mapping or not layout_metadata_dict:
            # Use the processed action (baked, cleaned, and hemisphere-fixed) when
            # deriving synthetic mapping/metadata so the frame/keyframe set matches
            # what will ultimately be exported.
            track_segment_bone_mapping, synthetic_metadata = fwrap_misc_export.create_synthetic_mapping(
                armature, processed_action, layout_metadata_dict
            )
            # Merge synthetic metadata into layout_metadata_dict
            layout_metadata_dict = {**(layout_metadata_dict or {}), **synthetic_metadata}
        
        # Get and return TrackUnitWrapper ------------------------------------------------------
        # Export all tracks using the mapping (provided or synthetic)
        track_indices = track_segment_bone_mapping.get_track_indices()
        Debug.log(f"    Processing {len(track_indices)} track(s)...")
        
        for track_idx in track_indices:
            # Find base fox track name for this track index to lookup metadata
            base_mapping = track_segment_bone_mapping.get_base_mapping(track_idx)
            layout_metadata = None
            
            if base_mapping:
                _blender_bone_name, fox_mapping_params = base_mapping
                fox_track_name = fox_mapping_params.fox_name
                # Strip multi-segment suffix if present
                base_fox_track_name, _ = util_parsing.parse_segment_suffix(fox_track_name)
                
                if layout_metadata_dict and base_fox_track_name in layout_metadata_dict:
                    layout_metadata = layout_metadata_dict[base_fox_track_name]
                elif action:
                    # Fallback: layout action exists but has no matching entry for this bone.
                    # Derive minimal metadata from FCurves so export can proceed.
                    layout_metadata = fwrap_metadata.build_track_metadata_from_fcurves(
                        bone_name=_blender_bone_name, action=processed_action
                    )
                    if layout_metadata:
                        Debug.log_warning(
                            f"      No layout metadata for track '{base_fox_track_name}' "
                            f"(bone: '{_blender_bone_name}') — derived from FCurves"
                        )
            
            gani_track = export_gani_track_from_action(
                armature,
                processed_action,
                track_idx,
                frame_start,
                frame_end,
                layout_metadata,
                track_segment_bone_mapping,
                force_highest_bit_encoding,
                fcurve_cache,
                transform_cache
            )
            
            # Add track to output
            # For e.g. motion points (discard_empty_tracks=True), skip tracks with no segments
            # For e.g. main animation (discard_empty_tracks=False), keep empty tracks to preserve structure defined by layout track
            if discard_empty_tracks and not gani_track.segments_track_data:
                Debug.log(f"      Skipping empty track {track_idx}")
            else:
                gani_tracks.append(gani_track)
        
        return gani_tracks

    finally:
        # Clean up temporary export action if one was created
        if processed_action is not None and processed_action != action:
            try:
                bpy.data.actions.remove(processed_action)
            except (ReferenceError, RuntimeError):
                pass  # Action may already be removed


# MTAR export #############################################################

def export_mtar(context: bpy.types.Context,
                filepath: str,
                armature: Optional[bpy.types.Object] = None,
                track_segment_bone_mapping: Optional[TrackSegmentBoneMapping] = None,
                use_nla: bool = True,
                use_reference_mtar: bool = False
                ) -> Dict[str, str]:
    """Export Blender animation data to MTAR format.

    Args:
        context: Blender context
        filepath: Path where the MTAR file should be saved
        armature: Armature object to export animation from
        track_segment_bone_mapping: Optional unified mapping from (track_idx, segment_idx) to (bone_name, bone_params)
        use_nla: If True, export NLA strips as separate GANI files; if False, export only active action

    Returns:
        Dictionary with export result information
    """
    Debug.start_timer("MTAR Export")
    
    # Mark context as used so static analysis doesn't flag it as unused
    _ = context
    # Scene properties (export options) from the UI
    props = context.scene.mtar_properties
    export_props = props.export_props

    # Get force_highest_bit_encoding once here to avoid multiple context accesses
    force_highest_bit_encoding = export_props.force_highest_bit_encoding

    # Determine whether to run FCurve cleaning based on UI toggles
    export_clean_threshold = blender_properties.get_effective_export_fcurve_clean_threshold(export_props)

    # Capture original animation state before any processing
    original_armature_action = None
    original_animation_data_exists = False
    if armature.animation_data:
        original_armature_action = armature.animation_data.action
        original_animation_data_exists = True
    else:
        original_animation_data_exists = False

    Debug.log("\n=== MTAR Data Export Started ===")
    Debug.log(f"Export path: {filepath}\n")
    
    if not armature:
        Debug.log_error("  Error: No armature specified for export")
        return {'CANCELLED': 'No armature specified'}
    
    if armature.type != 'ARMATURE':
        Debug.log_error(f"  Error: Object '{armature.name}' is not an armature")
        return {'CANCELLED': 'Object is not an armature'}

    # Reference File - check and prepare
    # Do this here to fail early before we start with the slow and long running work
    reference_reader = None
    if use_reference_mtar:
        reference_filepath = bpy.path.abspath(props.import_props.mtar_filepath)
        Debug.log(f"Using reference MTAR during export: {reference_filepath}")
        if not reference_filepath:
            Debug.log_error("  Error: Reference MTAR path is empty")
            return {'CANCELLED': 'Reference MTAR path is empty'}
        if not os.path.exists(reference_filepath):
            Debug.log_error(f"  Error: Reference MTAR file not found: {reference_filepath}")
            return {'CANCELLED': 'Reference MTAR file not found'}

        # Validate reference file
        reference_reader = MtarReader(reference_filepath)
        valid, err = reference_reader.validate_header()
        if not valid:
            Debug.log_error(f"  Error: Invalid reference MTAR: {err}")
            return {'CANCELLED': f'Invalid reference MTAR: {err}'}

        # Read reference GANIs — also populates self.common_info, self.layout_track, etc.
        ref_ganies_import_data = reference_reader.read_all_ganies()

        if not reference_reader.common_info and not reference_reader.layout_track:
            Debug.log_error("  Error: Could not read layout track from reference MTAR")
            return {'CANCELLED': 'Invalid reference MTAR layout'}
        
        # Apply sorting
        if ref_ganies_import_data and bool(context.scene.mtar_properties.settings_props.sort_gani):
            ref_ganies_import_data = tools_mtar_importer.sort_gani_data_by_file_offset(ref_ganies_import_data)

        Debug.log(f"Reference MTAR export active: {reference_filepath} ({len(ref_ganies_import_data)} GANIs)")
    
    Debug.log(f"Exporting armature: {armature.name}")

    # =============================
    # =============================

    # Mapping
    Debug.log("\n1. Mapping ++++++++++++++++++++++++++++++++++++++++++++")
    Debug.start_timer("1. Mapping")
    Debug.update_progress(5, "Mapping...")

    # Use provided track_segment_bone_mapping or create default mapping from armature
    if track_segment_bone_mapping is None:
        # No mapping provided - create default mapping using armature bone order.
        # Uses two-pass grouping: a bone named "BoneName_N" (N>=1) is treated as segment N
        # of the base track "BoneName" when that base bone also exists in the armature.
        track_segment_bone_mapping = TrackSegmentBoneMapping()
        Debug.log("No track mapping provided — falling back to armature bone order. "
                       "Original binary track order is not guaranteed without a mapping file.")
        bone_names = [bone.name for bone in armature.data.bones]
        for track_idx, (_base_name, segments) in enumerate(fwrap_misc_export.group_bones_by_segment(bone_names)):
            for seg_idx, seg_bone_name in segments:
                track_segment_bone_mapping.set_segment_mapping(
                    track_idx, seg_idx, seg_bone_name, BoneParameters(fox_name=seg_bone_name)
                )
                Debug.log(f"  Track {track_idx} Segment {seg_idx}: {seg_bone_name}")

    # Extract rest pose from armature (merges with mapping file transformations)
    # Check settings to see if rest pose correction is enabled
    enable_rest_pose = context.scene.mtar_properties.settings_props.enable_rest_pose_correction
    if enable_rest_pose:
        extract_rest_pose_correction_mapping_from_armature(track_segment_bone_mapping, armature)
    else:
        Debug.log("\nRest pose correction disabled in settings - skipping extraction")
    
    Debug.stop_timer("1. Mapping")

    # =============================
    # =============================

    # Meta Data 
    Debug.log("\n2. Meta Data ++++++++++++++++++++++++++++++++++++++++++++")
    Debug.start_timer("2. Meta Data")
    Debug.update_progress(10, "Meta Data...")

    # Find and parse layout track action
    Debug.log("\nSearching for layout track action...")
    layout_action = util_blender_animation.try_find_layout_track_action()
    metadata_dict = None
    
    # Collect actions to export first (needed for metadata merging in old-format)
    actions_to_export = collect_actions_for_export_from_armature(
        armature=armature,
        use_nla=use_nla,
        export_clean_threshold=export_clean_threshold
    )

    # Apply GANI index filter if requested (skipped when filter file mode is active)
    if not props.use_gani_filter_file:
        gani_selection_str = export_props.gani_indices_str.strip()
        if gani_selection_str and actions_to_export:
            total_actions = len(actions_to_export)
            try:
                selected_indices = util_parsing.parse_index_selection(gani_selection_str, total_actions)
                # Keep actions in original order; filter by selected indices
                actions_to_export = [action for idx, action in enumerate(actions_to_export) if idx in selected_indices]
                Debug.log(f"Filtered GANI export selection: {len(actions_to_export)} of {total_actions} actions selected")
                if not actions_to_export:
                    Debug.log_error("No actions selected for export after applying GANI index filter")
                    return {'CANCELLED': 'No GANI indices selected'}
            except ValueError as e:
                Debug.log_error(f"Invalid GANI selection string: {e}")
                return {'CANCELLED': f'Invalid GANI selection: {e}'}
    else:
        Debug.log("GANI filter file mode enabled; ignoring index string selection")

    actions_to_export = futil_filtering.filter_gani_export_actions(
        actions_to_export,
        bpy.path.abspath(props.gani_filter_txt_filepath) if props.use_gani_filter_file else None,
    )

    if not actions_to_export:
        return {'CANCELLED': 'No export animations matched the filter'}

    if layout_action:
        # GANI2 / new-format: Parse metadata from layout track action
        Debug.log("\nParsing layout track metadata (GANI2/new-format)...")
        metadata_dict = fwrap_metadata.get_all_track_metadata_from_action(layout_action)

        # Finalize mapping using layout metadata so missing per-segment mappings
        # (i.e., tracks that only had a base mapping defined) are populated.
        if track_segment_bone_mapping:
            Debug.log("  Finalizing mapping with layout metadata (populate missing segments from base mapping)")
            track_segment_bone_mapping.finalize_with_layout_metadata(metadata_dict)
        
        # Build layout track from metadata (including header properties)
        Debug.log("\nBuilding layout track structure...")
        layout_track = build_layout_track_from_metadata(track_segment_bone_mapping, metadata_dict, layout_action, force_highest_bit_encoding)
    else:
        # Old-format GANI1: Merge metadata from all per-GANI actions
        Debug.log("\nNo layout action found — assuming old-format (GANI1/FoxData)...")
        all_export_actions = [sd.action for sd in actions_to_export if sd.action]
        metadata_dict = _merge_metadata_from_actions(all_export_actions)

        # Finalize mapping with merged metadata
        if track_segment_bone_mapping and metadata_dict:
            Debug.log("  Finalizing mapping with merged per-GANI metadata...")
            track_segment_bone_mapping.finalize_with_layout_metadata(metadata_dict)
        
        # Build layout track from merged metadata
        Debug.log("\nBuilding layout track structure from merged per-GANI metadata...")
        layout_track = build_layout_track_from_metadata(
            track_segment_bone_mapping, metadata_dict if metadata_dict else {}, 
            layout_action=None,  # No layout action for old-format
            force_highest_bit_encoding=force_highest_bit_encoding)

    
    if not actions_to_export:
        Debug.log_error("  Error: No actions found to export")
        return {'CANCELLED': 'No animation data'}
    
    Debug.log(f"\n=== Exporting {len(actions_to_export)} action(s) ===")

    # Create MTAR writer with custom path hash settings
    # (The writer will hash paths directly via hash_animation_name_from_blender_context)
    writer = MtarWriter(
        filepath,
        treat_hashes_as_names=export_props.treat_hashes_as_names,
        export_custom_path_base=export_props.custom_path_base
    )
    
    # Set the layout track on the writer
    writer.set_layout_track(layout_track)
    
    # Set MTAR version and flags (Step 5a: fallback to per-GANI for old-format)
    # This determines whether to use new format (GANI2) or old format (FoxData)
    all_export_actions = [sd.action for sd in actions_to_export if sd.action]
    mtar_props = fwrap_metadata.read_mtar_properties_from_any_action(layout_action, all_export_actions)
    writer.set_mtar_version(
        mtar_props.get(mtar_const.MTAR_VERSION, 201403250),
        mtar_props.get(mtar_const.MTAR_FLAGS, 0x1000)
    )
    
    Debug.log(f"MTAR format: {'New (GANI2)' if writer.is_new_format else 'Old (FoxData)'}")
    Debug.log(f"MTAR version: {writer.version}")
    Debug.log(f"MTAR flags: 0x{writer.flags:04X}")


    Debug.stop_timer("2. Meta Data")

    # =============================
    # =============================

    # Motion Points
    Debug.log("\n3. Motion Points ++++++++++++++++++++++++++++++++++++++++++++")
    Debug.start_timer("3. Motion Points")
    Debug.update_progress(20, "Motion Points...")

    # Find motion points and shader nodes armature based purely on scene state
    Debug.log("\n=== Motion Points & Shader Nodes Detection ===")

    motion_points_armature = util_blender_armature.auto_detect_motion_points_armature(armature)
    shader_nodes_armature = util_blender_armature.auto_detect_shader_nodes_armature(armature)

    if motion_points_armature:
        Debug.log(f"Detected motion points armature: {motion_points_armature.name}")
    else:
        Debug.log("No motion points armature detected")

    if shader_nodes_armature:
        Debug.log(f"Detected shader nodes armature: {shader_nodes_armature.name}")
    else:
        Debug.log("No shader nodes armature detected")

    motion_point_actions_data: List[ExportActionData] = []
    motion_points_list: Optional[object] = None
    motion_point_actions_by_gani_index: Dict[int, ExportActionData] = {}

    if motion_points_armature:
        # Detach the auxiliary motion points armature so its export is not influenced
        # by any parenting/transform offsets from the main armature.
        parent = util_blender_armature.prepare_aux_armature_for_export(motion_points_armature)
        try:
            # Build MotionPointsList from armature bones (but do not write header count yet - it is computed at write time)
            motion_points_wrapper = fwrap_motionpoint_export.build_motion_points_list_from_armature(motion_points_armature)
            motion_points_list = motion_points_wrapper.to_motion_point_list2()

            # Collect motion point actions
            motion_point_actions_data = ExportActionData.collect_export_action_data_from_armature(
                armature=motion_points_armature, 
                use_nla=use_nla, 
                export_clean_threshold=export_clean_threshold
            )

            if motion_point_actions_data:
                Debug.log(f"Found {len(motion_point_actions_data)} motion point action(s)")
                # Build lookup map for motion-point actions by GANI index
                motion_point_actions_by_gani_index = fwrap_misc_export.build_motion_point_action_maps(motion_point_actions_data)
            else:
                Debug.log("No motion point actions found (motion points list will be exported without animations)")
        finally:
            util_blender_armature.restore_aux_armature_after_export(motion_points_armature, parent)
    else:
        Debug.log("Motion points will not be exported")
    
    Debug.stop_timer("3. Motion Points")

    # Shader Nodes (old-format only)
    Debug.log("\n3b. Shader Nodes ++++++++++++++++++++++++++++++++++++++++++++")
    Debug.start_timer("3b. Shader Nodes")
    Debug.update_progress(25, "Shader Nodes...")

    shader_nodes_actions_data: List[ExportActionData] = []
    shader_nodes_actions_by_gani_index: Dict[int, ExportActionData] = {}

    if shader_nodes_armature and not writer.is_new_format:
        Debug.log(f"Found shader nodes armature: {shader_nodes_armature.name}")
        shader_nodes_actions_data = ExportActionData.collect_export_action_data_from_armature(
            armature=shader_nodes_armature, 
            use_nla=use_nla, 
            export_clean_threshold=export_clean_threshold
        )
        if shader_nodes_actions_data:
            Debug.log(f"Found {len(shader_nodes_actions_data)} shader node action(s)")
            shader_nodes_actions_by_gani_index = fwrap_misc_export.build_shader_action_maps(shader_nodes_actions_data)
        else:
            Debug.log("No shader node actions found")
    elif shader_nodes_armature and writer.is_new_format:
        Debug.log("New format (GANI2) — shader nodes not applicable, skipping")
    else:
        Debug.log("No shader nodes armature selected - shader nodes will not be exported")

    Debug.stop_timer("3b. Shader Nodes")

    # =============================
    # =============================

    # Export each action as a GaniExportData object
    Debug.log("\n4. Animations ++++++++++++++++++++++++++++++++++++++++++++")
    Debug.start_timer("4. Animations")
    Debug.update_progress(30, "Animations...")

    # Track per-GANI motion point unit counts so we can compute the final header value at write time
    gani_motion_point_units: List[int] = []
    for action_idx, action_data in enumerate(actions_to_export):
        # -----------------------------------------------------
        # Update UI progress for each action (30-90% range)
        progress = 30 + int((action_idx / len(actions_to_export)) * 60)
        try:
            display_name = action_data.action.name if hasattr(action_data, 'action') and action_data.action else f"Gani_{action_idx+1:03d}"
        except Exception:
            display_name = f"Gani_{action_idx+1:03d}"
        Debug.update_progress(progress, f"GANI {action_idx+1}/{len(actions_to_export)}: {display_name}")
        # -----------------------------------------------------

        # Get frame info from action data
        frame_start = action_data.frame_start
        frame_end = action_data.frame_end
        # FrameCount is the last frame index (relative to frame_start), not the total number of frames
        # Use absolute value to handle any edge cases with negative ranges
        frame_count = abs(frame_end - frame_start)
        gani_name = action_data.action.name
        gani_action = action_data.action

        # =============================

        # Main animation tracks
        Debug.log(f"\n4.{action_idx}.1 Main Animation Tracks ----------------------------------------")
        Debug.start_timer(f"4.{action_idx}.1 Main Animation Tracks")

        # Step 5d: For old-format, use per-strip metadata instead of global metadata_dict
        effective_metadata_dict = metadata_dict
        if layout_action is None and action_data.action is not None:
            # Old-format: per-GANI metadata overrides the merged dict for this strip
            per_strip_dict = fwrap_metadata.get_all_track_metadata_from_action(action_data.action)
            effective_metadata_dict = per_strip_dict if per_strip_dict else metadata_dict
            if per_strip_dict:
                Debug.log("  Using per-GANI metadata for this strip (overrides merged layout)")

        gani_tracks: List[TrackUnitWrapper] = export_gani_tracks_from_action(
            armature,
            action_data,
            track_segment_bone_mapping,
            effective_metadata_dict,
            force_highest_bit_encoding
        )

        tracks_data = GaniExportTracksData(
            gani_tracks=gani_tracks,
            action=gani_action,
            source=action_data.source,
        )
        Debug.stop_timer(f"4.{action_idx}.1 Main Animation Tracks")
        
        # =============================
        
        # Motion Points
        Debug.log(f"\n4.{action_idx}.2 Motion Points ----------------------------------------")
        Debug.start_timer(f"4.{action_idx}.2 Motion Points")

        # Export motion point tracks for this GANI (match by extracted GANI index)
        motion_point_tracks: List[TrackUnitWrapper] = None
        motion_point_action_data: Optional[ExportActionData] = None

        if motion_point_actions_data:
            motion_point_action_data = fwrap_misc_export.find_motion_point_action_for_gani(gani_name, motion_point_actions_by_gani_index)

        if motion_point_action_data:
            Debug.log(f"\n  Exporting motion points for GANI '{gani_name}': {motion_point_action_data.action.name}")

            # MetaData: Build metadata dict for motion points by analyzing the action and armature
            motion_point_metadata_dict: Dict[str, fwrap_metadata.TrackMetaData] = fwrap_motionpoint_export.build_motion_point_metadata_dict(motion_points_armature, motion_point_action_data.action)
            Debug.log(f"    Built metadata from {len(motion_point_metadata_dict)} motion point bone(s)")

            # Export motion point tracks
            # discard_empty_tracks=True: skip tracks with no animation data (motion points have no layout track)
            motion_point_tracks = export_gani_tracks_from_action(motion_points_armature,
                                                                 motion_point_action_data,
                                                                 None,  # No bone mapping needed yet for motion points
                                                                 motion_point_metadata_dict,  # Pass the built metadata dict
                                                                 force_highest_bit_encoding,
                                                                 discard_empty_tracks=True)
            if motion_point_tracks:
                Debug.log(f"    Exported {len(motion_point_tracks)} motion point track(s)")
            else:
                Debug.log_warning(f"    Warning: Motion point action '{motion_point_action_data.action.name}' matched GANI '{gani_name}' but exported 0 motion point tracks")
        elif motion_point_actions_data:
            Debug.log(f"  Motion point actions exist but none matched GANI '{gani_name}' - motion points will be skipped for this GANI")
        else:
            Debug.log(f"    No motion point action for GANI '{gani_name}'")
        
        motion_points_data = None
        total_units = 0
        if motion_point_tracks:
            # Read MTP TrackHeader properties from the motion point action.
            # This preserves unknown_b and other header fields from import.
            mtp_track_header = None
            if motion_point_action_data and motion_point_action_data.action:
                mtp_header_props = fwrap_metadata.read_track_header_properties_from_action(
                    motion_point_action_data.action
                )
                mtp_track_header = TrackHeader(
                    unit_count=len(motion_point_tracks),
                    segment_count=0,  # will be computed by writer
                    t_id=mtp_header_props.get(gani_const.TRKH_ID, 0),
                    unknown_a=mtp_header_props.get(gani_const.TRKH_UNKNOWN_A, 0),
                    unknown_b=mtp_header_props.get(gani_const.TRKH_UNKNOWN_B, 0),
                    frame_count=mtp_header_props.get(gani_const.TRKH_FRAME_COUNT, frame_count),
                    frame_rate=mtp_header_props.get(gani_const.TRKH_FRAME_RATE, 60),
                    unit_offsets=[],  # will be computed by writer
                )
            motion_points_data = GaniExportMotionPointsData(
                motion_point_tracks=motion_point_tracks,
                motion_point_track_header=mtp_track_header
            )
            # Record the number of motion point units for this GANI for later validation/header calculation
            total_units = len(motion_point_tracks)
        gani_motion_point_units.append(total_units)
        
        Debug.stop_timer(f"4.{action_idx}.2 Motion Points")

        # =============================
        
        # Motion Events
        Debug.log(f"\n4.{action_idx}.3 Motion Events ----------------------------------------")
        Debug.start_timer(f"4.{action_idx}.3 Motion Events")

        # Read motion events from the action if present
        motion_events = fwrap_motionevent.read_motion_events_from_action(gani_action)

        motion_events_data = None
        if motion_events:
            motion_events_data = GaniMotionEventsData(
                motion_events=motion_events
            )
            Debug.log(f"  Found {motion_events.count} motion event categor(ies) in action")
        
        Debug.stop_timer(f"4.{action_idx}.3 Motion Events")

        # =============================

        # Shader Nodes (old-format only)
        Debug.log(f"\n4.{action_idx}.4 Shader Nodes ----------------------------------------")
        Debug.start_timer(f"4.{action_idx}.4 Shader Nodes")

        shader_nodes_data = None
        shader_node_action_data = None
        if shader_nodes_actions_data:
            # Detach the auxiliary shader nodes armature so its export is not influenced
            # by any parenting/transform offsets from the main armature.
            shader_node_parent = util_blender_armature.prepare_aux_armature_for_export(shader_nodes_armature)
            try:
                shader_node_action_data: Optional[ExportActionData] = fwrap_misc_export.find_shader_action_for_gani(
                    gani_name, shader_nodes_actions_by_gani_index
                )

                if shader_node_action_data:
                    Debug.log(
                        f"\n  Exporting shader nodes for GANI '{gani_name}': "
                        f"{shader_node_action_data.action.name}"
                    )

                    shader_metadata_dict = tools_gani1_shader_exporter.build_shader_nodes_metadata_dict(
                        shader_nodes_armature, shader_node_action_data.action
                    )
                    Debug.log(
                        f"    Built metadata from {len(shader_metadata_dict)} "
                        f"shader unit bone(s)"
                    )

                    flat_shader_tracks = export_gani_tracks_from_action(
                        shader_nodes_armature,
                        shader_node_action_data,
                        None,
                        shader_metadata_dict,
                        force_highest_bit_encoding,
                        discard_empty_tracks=True,
                    )

                    if flat_shader_tracks:
                        Debug.log(
                            f"    Exported {len(flat_shader_tracks)} shader unit track(s)"
                        )
                        property_names, property_tracks = tools_gani1_shader_exporter.group_shader_tracks_by_property(
                            flat_shader_tracks, shader_nodes_armature
                        )
                        if property_names:
                            property_headers = tools_gani1_shader_exporter.collect_shader_property_headers(
                                shader_node_action_data.action, property_names
                            )
                            shader_nodes_data = Gani1ExportShaderData(
                                property_tracks=property_tracks,
                                property_names=property_names,
                                property_headers=property_headers,
                            )
                        else:
                            Debug.log_warning(
                                f"    Warning: Shader unit tracks for GANI '{gani_name}' "
                                f"could not be grouped by property — shader data will be skipped"
                            )
                    else:
                        Debug.log_warning(
                            f"    Warning: Shader node action '{shader_node_action_data.action.name}' "
                            f"matched GANI '{gani_name}' but exported 0 shader unit tracks"
                        )
                elif shader_nodes_actions_data:
                    Debug.log(
                        f"  Shader node actions exist but none matched GANI '{gani_name}' "
                        f"- shader nodes will be skipped for this GANI"
                    )
            finally:
                util_blender_armature.restore_aux_armature_after_export(shader_nodes_armature, shader_node_parent)
        else:
            Debug.log(f"    No shader node action for GANI '{gani_name}'")

        Debug.stop_timer(f"4.{action_idx}.4 Shader Nodes")

        # =============================

        Debug.log(f"\n4.{action_idx}.5 Storing Data ----------------------------------------")

        # Read frame rate from layout action header properties (stored during import); default 60.
        # For old-format (no layout action), fall back to reading from the per-GANI action.
        header_action = layout_action if layout_action is not None else action_data.action
        header_props = fwrap_metadata.read_track_header_properties_from_action(header_action)
        frame_rate = header_props.get(gani_const.TRKH_FRAME_RATE, 60)

        # Compute path hash for this GANI based on action metadata if available
        path_hash = writer.compute_gani_path_hash_from_action(gani_action)

        # Read old-format file table unknown (MtarTableList.unknown) from action
        gani1_table_unknown = None
        if gani_action and mtar_const.TABL_UNKNOWN in gani_action.keys():
            try:
                gani1_table_unknown = int(gani_action[mtar_const.TABL_UNKNOWN])
            except (TypeError, ValueError):
                Debug.log_warning(f"Invalid {mtar_const.TABL_UNKNOWN} on action '{gani_action.name}', using default")

        # Create GaniExportData object
        gani_data: GaniExportData = GaniExportData(
            gani_name=gani_name,
            gani_frame_count=frame_count,
            gani_frame_rate=frame_rate,
            gani_frame_start=frame_start,
            gani_frame_end=frame_end,
            gani_tracks_data=tracks_data,
            gani_motion_points_data=motion_points_data,
            gani_motion_events_data=motion_events_data,
            gani_path_hash=path_hash,
            gani1_shader_nodes_data=shader_nodes_data,
            gani1_table_unknown=gani1_table_unknown,
        )

        # Merge node_params from tracks and shader actions for lossless round-trip
        node_params = fwrap_metadata.merge_node_params(
            fwrap_metadata.iter_all_node_params_from_action(gani_action),
            fwrap_metadata.iter_all_node_params_from_action(shader_node_action_data.action) if shader_node_action_data else None,
        )
        gani_data.gani_node_params = node_params or None

        # Add to writer
        writer.add_gani_data(gani_data)
        if motion_point_tracks:
            Debug.log(f"  Added GANI data: '{gani_name}' ({frame_count} frames) with {len(motion_point_tracks)} motion point track(s)")
        else:
            Debug.log(f"  Added GANI data: '{gani_name}' ({frame_count} frames)")

    # Reference mode: 
    # Merge current export GANIs into the reference set (by path hash)
    if use_reference_mtar and reference_reader:
        Debug.log(f"  Reference mode: merging {len(writer.gani_data_list)} export GANI(s) into {len(ref_ganies_import_data)} reference entries...")
        
        # Convert imported gani data to the required export gani data type.
        ref_ganies_export_data: List[GaniExportData] = []
        for gani_idx, ref_gani_import_data in enumerate(ref_ganies_import_data):
            if not ref_gani_import_data.file_header:
                Debug.log_warning(f"  Reference GANI #{gani_idx} has no file_header, skipping")
                continue
            ref_gani_path = ref_gani_import_data.file_header.path
            ref_ganies_export_data.append(_convert_import_gani_to_export_gani(ref_gani_import_data, reference_path_hash=ref_gani_path))

        ref_map = {rged.gani_path_hash: idx for idx, rged in enumerate(ref_ganies_export_data) if rged.gani_path_hash is not None}
        replaced_count = 0

        # Try to replace the to-export ganies in the reference data
        for to_export_gani_data in writer.gani_data_list:
            # We match per hash, so compute one first
            try:
                export_gani_path_hash = writer.compute_gani_path_hash(to_export_gani_data)
            except ValueError as e:
                Debug.log_warning(f"Skipping export GANI due to missing path hash: {e}")
                continue

            # Now try to find a place to put our to-export data in
            if export_gani_path_hash in ref_map:
                ref_ganies_export_data[ref_map[export_gani_path_hash]] = to_export_gani_data
                replaced_count += 1
            else:
                Debug.log_warning(f"GANI hash 0x{export_gani_path_hash:016X} not found in reference MTAR, skipping")

        if replaced_count == 0:
            Debug.log_error("No export animations matched reference MTAR hashes")
            return {'CANCELLED': 'No matching animations in reference MTAR'}

        skipped_count = len(writer.gani_data_list) - replaced_count
        Debug.log(f"  Reference mode: replaced {replaced_count} of {len(ref_ganies_export_data)} GANI(s); "
                  f"{skipped_count} export GANI(s) had no match in reference and were skipped")
        if skipped_count > 0:
            Debug.log_warning(f"  {skipped_count} export GANI(s) were not written because their hash was not found in the reference MTAR")

        # Now swap the merged data into the export pipeline
        writer.gani_data_list = ref_ganies_export_data

        # Override writer metadata with reference properties
        if reference_reader.common_info and reference_reader.common_info.layout_track:
            writer.set_layout_track(reference_reader.common_info.layout_track)
        elif reference_reader.layout_track:
            writer.set_layout_track(reference_reader.layout_track)

        if reference_reader.common_info and reference_reader.common_info.motion_points:
            writer.set_motion_points_list(reference_reader.common_info.motion_points.to_motion_point_list2())
        else:
            writer.set_motion_points_list(None)

        # Warn if reference format differs from current export format
        ref_is_new_format = is_new_mtar_format(reference_reader.mtar_flags)
        if ref_is_new_format != writer.is_new_format:
            Debug.log_error(
                f"  Reference MTAR format ({'new/GANI2' if ref_is_new_format else 'old/FoxData'}) "
                f"differs from export format ({'new/GANI2' if writer.is_new_format else 'old/FoxData'}). "
                "Output MTAR may be structurally inconsistent."
            )
            return {'CANCELLED': 'No matching animations in reference MTAR'}
        
        writer.set_mtar_version(reference_reader.mtar_version, reference_reader.mtar_flags)

    Debug.stop_timer("4. Animations")

    # Set motion points data if available
    # IMPORTANT: MTAR header count is SEPARATE from CommonInfo count!
    # - Header count: max motion point units used across all GANIs (set via set_motion_point_header_count)
    # - CommonInfo count: total motion point bone definitions (in motion_points_list.count)
    # The header value is informational only during import; CommonInfo has the actual bone data.
    if not use_reference_mtar:
        # Reference mode already sets motion points from the reference in the merge block above.
        # Normal mode: set motion points from the Blender armature.
        if motion_points_list:
            writer.set_motion_points_list(motion_points_list)
            
            # Compute max motion point units across all GANIs for the header
            max_units_from_ganis = max(gani_motion_point_units) if gani_motion_point_units else 0
            writer.set_motion_point_header_count(max_units_from_ganis)
            
            # Warn if header count exceeds available motion point entries
            if max_units_from_ganis > len(motion_points_list.entries):
                Debug.log_warning(f"Motion point header unit count ({max_units_from_ganis}) is larger than motion points list entries ({len(motion_points_list.entries)})")
            
            Debug.log(f"Motion points: {len(motion_points_list.entries)} bone definitions, {max_units_from_ganis} max units in MTAR header")
        else:
            # No motion points to export
            writer.set_motion_points_list(None)
            writer.set_motion_point_header_count(0)

    # Write the MTAR file
    Debug.log("\n5. Writing MTAR file... ++++++++++++++++++++++++++++++++++++++++++++")
    Debug.start_timer("5. Writing MTAR file")
    # Update progress bar for the writing phase (90-100%)
    Debug.update_progress(95, "Writing MTAR...")
    writer.write()
    Debug.stop_timer("5. Writing MTAR file")

    # Write the info file with animation names
    Debug.log("\n6. Writing animation info file... ++++++++++++++++++++++++++++++++++++++++++++")
    
    # Only write the info file if the export setting is enabled
    if export_props.info_file:
        # Build animation names for the info file using the writer helper so we
        # reuse the same naming logic (handles NLA strips and active actions).
        animation_names = [writer.get_animation_name_for_gani(gd) for gd in writer.gani_data_list]
        mtar_path = Path(filepath)
        info_filepath = mtar_path.with_name(f"{mtar_path.stem}.mtar.info.txt")
        try:
            with open(info_filepath, 'w', encoding='utf-8') as info_file:
                for anim_name in animation_names:
                    info_file.write(f"{anim_name}\n")
            Debug.log(f"  Wrote {len(animation_names)} animation name(s) to {info_filepath}")
        except (IOError, OSError) as e:
            Debug.log_error(f"  Error writing info file: {e}")
    else:
        Debug.log("  Skipping info file export (disabled in export settings)")
    
    # Restore armature's original animation state before returning
    if original_armature_action is not None:
        armature.animation_data.action = original_armature_action
    elif original_animation_data_exists and armature.animation_data:
        # Animation data exists but had no action - clear it
        armature.animation_data.action = None

    Debug.log("\n=== MTAR Data Export Complete ===")
    Debug.log(f"Exported {len(actions_to_export)} action(s) to {filepath}\n")
    Debug.stop_timer("MTAR Export")
    
    return {'FINISHED': f'Exported to {filepath}'}
