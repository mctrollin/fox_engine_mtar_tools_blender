"""
MTAR animation exporter for Metal Gear Solid V.

This module handles the export of Blender animation data to MTAR format.
"""

import math
from typing import Optional, Dict, List
from pathlib import Path

import bpy
from mathutils import Quaternion

from ..py_utilities.utilities_logging import Debug
from ..py_utilities.utilities_transforms import (
    reverse_directional_location, 
    apply_reverse_transforms, 
    get_local_space_transform, 
    get_world_space_transform, 
    blender_to_fox_vector, 
    blender_to_fox_quaternion,
    reverse_rest_pose_correction_local,
    reverse_rest_pose_correction_world,
    TransformsCache
)
from ..py_utilities.utilities_blender_animation import (
    FCurveCache, action_has_fcurves, iter_action_fcurves, is_relevant_strip, try_find_layout_track_action,
    build_data_path_for_bone, extract_bone_name_from_data_path
)
from ..py_utilities.utilities_fcurve_processing import bake_and_clean_export_fcurves

from ..py_foxwrap.foxwrap_motionevent import read_motion_events_from_action
from ..py_foxwrap.foxwrap_metadata import parse_action_track_metadata, read_track_header_properties_from_action, read_mtar_properties_from_action
from ..py_fox import fox_gani_constants as gani_const
from ..py_fox import fox_mtar_constants as mtar_const
from ..py_foxwrap.foxwrap_metadata import TrackMetaData, merge_track_metadata, iter_track_properties, get_all_track_metadata_from_action
from ..py_foxwrap.foxwrap_misc import TrackUnitWrapper, Tracks, TrackDataBlobWrapper
from ..py_foxwrap.foxwrap_mtar_writer import MtarWriter
from ..py_foxwrap.foxwrap_misc_export import (
    GaniExportData, GaniExportTracksData, GaniExportMotionPointsData, GaniMotionEventsData,
    TrackSegmentBoneMapping, ExportActionData, create_synthetic_mapping, build_motion_point_action_maps, find_motion_point_action_for_gani
)
from ..py_foxwrap.foxwrap_mapping import BoneParameters

from ..py_fox.fox_gani_types import AnimKeyframe, SegmentType, TrackUnitFlags, TrackHeader, TrackUnit, TrackData, TrackDataBlob
from ..py_fox.fox_mtar_types import MotionPointList2, MotionPointEntry
from ..py_fox.fox_frig_types import RigUnitType
from ..py_fox.fox_misc_types import StrCode32

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


# Layout and MetaData #############################################################

def build_layout_track_from_metadata(track_segment_bone_mapping: TrackSegmentBoneMapping, 
                                     metadata_dict: Dict[str, TrackMetaData],
                                     layout_action: Optional[bpy.types.Action] = None,
                                     force_highest_bit_encoding: bool = False) -> 'Tracks':
    """Build a Tracks (layout track) object from metadata.
    
    Args:
        track_segment_bone_mapping: Unified mapping from (track_idx, segment_idx) to (bone_name, fox_mapping_params)
        metadata_dict: Dictionary of fox_track_name -> TrackMetaData
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
        
        # Strip segment suffix if present (e.g., "RIG_SKL_010_LSHLD_0" -> "RIG_SKL_010_LSHLD")
        # Multi-segment tracks store metadata under the base track name
        base_fox_track_name = fox_track_name
        if '_' in fox_track_name:
            parts = fox_track_name.rsplit('_', 1)
            if len(parts) == 2 and parts[1].isdigit():
                base_fox_track_name = parts[0]
        
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
    header_props = read_track_header_properties_from_action(layout_action)
    
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

def extract_rest_pose_correction_mapping_from_armature(track_segment_bone_mapping: 'TrackSegmentBoneMapping', armature: bpy.types.Object) -> None:
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
        for seg_idx, blender_bone_name, bone_params in segments:
            # Skip as_ik_up bones - they should not be affected by rest pose corrections
            # Duck-typed access to handle dict or dataclass
            as_ik_up_value = getattr(bone_params, 'as_ik_up', None) if hasattr(bone_params, 'as_ik_up') else bone_params.get('as_ik_up') if isinstance(bone_params, dict) else None
            if as_ik_up_value:
                continue
            
            # Check if bone exists in armature
            if blender_bone_name not in armature.data.bones:
                continue
            
            # Extract rest pose rotation from armature
            bone = armature.data.bones[blender_bone_name]
            euler = bone.matrix_local.to_euler('XYZ')
            euler_deg = [math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z)]
            
            rest_pose_dict = {
                'euler': euler_deg,
                'order': 'XYZ'
            }
            
            # Determine how to apply based on track space type
            # Duck-typed access to handle dict or dataclass
            space_r_value = getattr(bone_params, 'space_r', None) if hasattr(bone_params, 'space_r') else bone_params.get('space_r') if isinstance(bone_params, dict) else None
            
            if space_r_value:
                # WORLD space track - add to rotation_offset list
                if hasattr(bone_params, 'rotation_offset'):
                    if bone_params.rotation_offset is None:
                        bone_params.rotation_offset = []
                    bone_params.rotation_offset.append(rest_pose_dict)
                elif isinstance(bone_params, dict):
                    if 'rotation_offset' not in bone_params or bone_params['rotation_offset'] is None:
                        bone_params['rotation_offset'] = []
                    bone_params['rotation_offset'].append(rest_pose_dict)
                Debug.log(f"  {blender_bone_name} [WORLD]: Added rest pose to offset_r: ({euler_deg[0]:.1f}, {euler_deg[1]:.1f}, {euler_deg[2]:.1f})")
            else:
                # LOCAL space track - merge with existing map_r or set if missing
                existing_map_r = getattr(bone_params, 'map_r', None) if hasattr(bone_params, 'map_r') else bone_params.get('map_r') if isinstance(bone_params, dict) else None
                
                if existing_map_r is None:
                    # No map_r from mapping file - use rest pose from armature
                    if hasattr(bone_params, 'map_r'):
                        bone_params.map_r = rest_pose_dict
                    elif isinstance(bone_params, dict):
                        bone_params['map_r'] = rest_pose_dict
                    Debug.log(f"  {blender_bone_name} [LS]: Set rest pose from armature: ({euler_deg[0]:.1f}, {euler_deg[1]:.1f}, {euler_deg[2]:.1f})")
                else:
                    # Already has map_r from mapping file - use armature instead
                    existing_euler = existing_map_r['euler']
                    Debug.log(f"  {blender_bone_name} [LS]: Mapping file has map_r=({existing_euler[0]:.1f}, {existing_euler[1]:.1f}, {existing_euler[2]:.1f}), using armature instead")
                    if hasattr(bone_params, 'map_r'):
                        bone_params.map_r = rest_pose_dict
                    elif isinstance(bone_params, dict):
                        bone_params['map_r'] = rest_pose_dict
            
            rest_pose_count += 1
    
    Debug.log(f"Extracted rest pose for {rest_pose_count} bone(s) from armature")


# Animation #############################################################

def collect_actions_for_export_from_armature(armature: bpy.types.Object, 
                                            use_nla: bool = True,
                                            export_clean_threshold: float = 0.0) -> List[ExportActionData]:
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
                if not is_relevant_strip(strip):
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


def get_bone_keyframe_numbers_from_action(action: bpy.types.Action, bone_name: str, 
                              segment_type: SegmentType, frame_start: int, frame_end: int,
                              bone_params: Optional[BoneParameters] = None,
                              fcurve_cache: Optional[FCurveCache] = None) -> List[int]:
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
        fcurve_cache: Optional pre-built FCurveCache for fast lookups (20-100× faster than scanning action.fcurves)
        
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
        data_paths = [build_data_path_for_bone(bone_name, prop) for prop in property_names]
        for fcurve in iter_action_fcurves(action):
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
                           fcurve_cache: Optional[FCurveCache] = None,
                           transform_cache: Optional[TransformsCache] = None) -> List['AnimKeyframe']:
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
        fcurve_cache: Optional pre-built FCurveCache for fast lookups
        transform_cache: Optional pre-computed transform cache for all bones/frames
        
    Returns:
        List of AnimKeyframe objects
    """
    # Determine frame range
    if is_static:
        export_frames = [frame_start]
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
        fixed_frames = [export_frames[0]]
        for i in range(1, len(export_frames)):
            gap = export_frames[i] - export_frames[i - 1]
            if gap > 255:
                current = export_frames[i - 1]
                while current + 255 < export_frames[i]:
                    current += 255
                    fixed_frames.append(current)
            fixed_frames.append(export_frames[i])

        if len(fixed_frames) != len(export_frames):
            Debug.log_warning(
                f"Non-static track '{blender_bone_name}' ({segment_type.name}): "
                f"inserted {len(fixed_frames) - len(export_frames)} intermediate frame(s) "
                f"to keep frame deltas within the 255-frame binary limit."
            )
            export_frames = fixed_frames
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
            transform_cache
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
            transform_cache
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

def _get_rotation_transform_fn(bone_params: BoneParameters, armature: bpy.types.Object,
                               blender_bone_name: str, space_bone: Optional[str],
                               rig_unit_type: Optional[RigUnitType],
                               transform_cache: Optional[TransformsCache] = None):
    """Return a callable that produces rotation quaternion for a given frame.
    
    This helper eliminates code duplication between as_ik_up and normal rotation paths.
    The returned function captures the context needed to compute rotation at any frame.
    
    Args:
        bone_params: Bone parameters (contains as_ik_up data if applicable)
        armature: Armature object
        blender_bone_name: Name of the bone in Blender
        space_bone: Custom space bone name (or None for default space)
        rig_unit_type: Rig unit type (determines local vs world space for normal tracks)
        transform_cache: Optional pre-computed transform cache
        
    Returns:
        Callable that takes (frame: int) and returns Quaternion
    """
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
                ik_location, _ = get_world_space_transform(armature, blender_bone_name, frame, space_bone)
                base_location, _ = get_world_space_transform(armature, base_bone_name, frame, space_bone)
            return reverse_directional_location(ik_location, base_location, axis)
        
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
                    _, quat = get_world_space_transform(armature, blender_bone_name, frame, space_bone)
                else:
                    _, quat = get_local_space_transform(armature, blender_bone_name, frame)
            return quat
        
        return get_rotation_normal

def export_rotation_segment(armature: bpy.types.Object, blender_bone_name: str,
                            bone_params: BoneParameters, export_frames: List[int],
                            frame_start: int, is_static: bool, 
                            rig_unit_type: Optional[RigUnitType] = None,
                            transform_cache: Optional[TransformsCache] = None) -> List['AnimKeyframe']:
    """Export rotation segment keyframes."""
    keyframes = []
    Debug.start_timer("export_rotation_segment")
    
    # POINT 4 OPTIMIZATION: Extract loop-invariant setup and use pluggable transform function
    # These are constant across all frames, so extract once to avoid redundant lookups
    rotation_offset = bone_params.rotation_offset
    rotation_axis_map = bone_params.rotation_axis_map
    
    # For as_ik_up bones, use space_ik instead of space_r for the space bone
    # This is because space_ik defines the transformation constraint space for IK targets
    if bone_params.as_ik_up:
        space_bone = TrackMetaData.extract_space_bone(bone_params.space_ik)
    else:
        space_bone = TrackMetaData.extract_space_bone(bone_params.space_r)
    
    # Extract rest pose correction parameters (duck-typed dict access)
    map_r_dict = getattr(bone_params, 'map_r', None) if hasattr(bone_params, 'map_r') else bone_params.get('map_r') if isinstance(bone_params, dict) else None
    space_r_value = getattr(bone_params, 'space_r', None) if hasattr(bone_params, 'space_r') else bone_params.get('space_r') if isinstance(bone_params, dict) else None
    
    # Get rotation transform function (varies by as_ik_up and space type)
    # This eliminates ~40 lines of code duplication between two paths
    get_rotation = _get_rotation_transform_fn(bone_params, armature, blender_bone_name,
                                              space_bone, rig_unit_type, transform_cache)
    
    # Unified frame loop for both as_ik_up and normal rotation
    prev_frame = frame_start  # Track previous frame for relative delta computation
    for frame in export_frames:
        # Set frame explicitly for performance (if no cache present)
        if not transform_cache:
            bpy.context.scene.frame_set(frame)
        
        # Get rotation using appropriate method (as_ik_up or normal)
        blender_quat = get_rotation(frame)
        
        # Apply reverse rest pose corrections (must happen BEFORE axis mapping and offsets)
        # World space tracks (space_r=world): reverse offset_r using simple multiplication
        # Local space tracks (default): reverse map_r using similarity transformation
        if space_r_value and isinstance(space_r_value, dict) and space_r_value.get('space') == 'WORLD':
            # World space track - reverse offset_r if present
            if rotation_offset:
                # Use first offset as the offset_r (world space offset)
                blender_quat = reverse_rest_pose_correction_world(blender_quat, rotation_offset[0])
        elif map_r_dict:
            # Local space track - reverse similarity transformation
            blender_quat = reverse_rest_pose_correction_local(blender_quat, map_r_dict)
        
        # Apply reverse transformations (offsets, axis mapping)
        fox_quat = apply_reverse_transforms(blender_quat, rotation_offset, rotation_axis_map)
        
        # Convert to Fox Engine coordinate system
        fox_quat_final = blender_to_fox_quaternion(fox_quat)
        
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
    
    Debug.stop_timer("export_rotation_segment")
    return keyframes

def export_location_segment(armature: bpy.types.Object, blender_bone_name: str,
                            bone_params: BoneParameters, export_frames: List[int],
                            frame_start: int, is_static: bool,
                            rig_unit_type: Optional[RigUnitType] = None,
                            transform_cache: Optional[TransformsCache] = None,
                            no_coordinate_transform: bool = False,
                            num_components: int = 3) -> List['AnimKeyframe']:
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
    Debug.start_timer("export_location_segment")
    
    # Get custom space if specified (constant across all frames)
    # Use the same extraction logic as rotation export for consistency
    space_bone = TrackMetaData.extract_space_bone(bone_params.space_l)

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
        
        # Read location (using pre-determined space)
        if transform_cache:
            if use_world_space:
                blender_location, _ = transform_cache.get_world(blender_bone_name, frame, space_bone)
            else:
                blender_location, _ = transform_cache.get_local(blender_bone_name, frame)
        else:
            if use_world_space:
                # Use world space transforms for ORIENTATION, TWO_BONE, ARM
                blender_location, _ = get_world_space_transform(armature, blender_bone_name, frame, space_bone)
            else:
                # Use local space transforms for other types (LOCAL_ORIENTATION, TRANSFORM, ROOT, etc.)
                blender_location, _ = get_local_space_transform(armature, blender_bone_name, frame)
        
        # Reverse X and Y inversion if custom space bone was used during import
        if invert_xy:
            blender_location = blender_location.copy()
            blender_location.x = -blender_location.x
            blender_location.y = -blender_location.y

        # Convert to Fox Engine coordinate system (or take raw channels for FLOAT/VECTOR2)
        if no_coordinate_transform:
            fox_location = list(blender_location)[:num_components]
        else:
            fox_location = blender_to_fox_vector(blender_location)
        
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
    
    Debug.stop_timer("export_location_segment")
    return keyframes


def bone_has_fcurves_for_segment(
        bone_name: str,
        segment_type: SegmentType,
        bone_params: Optional[BoneParameters],
        fcurve_cache: Optional[FCurveCache]) -> bool:
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
                                  layout_metadata: Optional[TrackMetaData],
                                  track_segment_bone_mapping: TrackSegmentBoneMapping,
                                  force_highest_bit_encoding: bool = False,
                                  fcurve_cache: Optional[FCurveCache] = None,
                                  transform_cache: Optional[TransformsCache] = None
                                  ) -> TrackUnitWrapper:
    """Export a GaniTrack (all segments for one track).
    
    This is the export counterpart to import_gani_track().
    Gets track structure (segments) from layout action and animation-specific
    unit flags from the animation action.
    
    For multi-segment tracks, each segment maps to a different Blender bone
    (e.g., "RIG_SKL_010_LSHLD_0", "RIG_SKL_010_LSHLD_1", "RIG_SKL_010_LSHLD_2") as defined in the track mapping file.
    
    Args:
        armature: Armature object
        track_idx: Index of this track in the layout
        track_segment_bone_mapping: Unified mapping from (track_idx, segment_idx) to (bone_name, fox_mapping_params)
        frame_start: First frame to export
        frame_end: Last frame to export
        action: Animation action containing keyframes
        layout_metadata: TrackMetaData instance containing track structure metadata for this track
        fcurve_cache: Optional pre-built FCurveCache for fast lookups
        force_highest_bit_encoding: If True, use highest available bit sizes for all segments
        transform_cache: Optional pre-computed transform cache
        
    Returns:
        GaniTrack object with all keyframes tracks
    """
    # Get the base track info (first check if track exists at all)
    if not track_segment_bone_mapping.has_track(track_idx):
        # Create empty track - no metadata available
        Debug.log_warning(f"      Warning: No mapping for track {track_idx}, creating empty track")
        return TrackUnitWrapper(
            name=f"Track_{track_idx}",
            segments_track_data=[],
            unit_flags=[TrackUnitFlags.NONE]
        )
    
    # Get base track info (for segment 0)
    base_mapping = track_segment_bone_mapping.get_base_mapping(track_idx)
    if not base_mapping:
        # Create empty track - no base segment
        Debug.log_warning(f"      Warning: No base segment mapping for track {track_idx}, creating empty track")
        return TrackUnitWrapper(
            name=f"Track_{track_idx}",
            segments_track_data=[],
            unit_flags=[TrackUnitFlags.NONE]
        )
    
    base_blender_bone_name, base_fox_mapping_params = base_mapping
    
    if not base_blender_bone_name:
        # Create empty track to preserve structure
        return TrackUnitWrapper(
            name=f"Track_{track_idx}",
            segments_track_data=[],
            unit_flags=[TrackUnitFlags.NONE]
        )
    
    # Get the fox track name from the base mapping params
    fox_track_name = base_fox_mapping_params.fox_name
    
    # Strip segment suffix if present (e.g., "RIG_SKL_010_LSHLD_0" -> "RIG_SKL_010_LSHLD")
    # Metadata is stored under the base track name for multi-segment tracks
    base_fox_track_name = fox_track_name
    if '_' in fox_track_name:
        parts = fox_track_name.rsplit('_', 1)
        if len(parts) == 2 and parts[1].isdigit():
            base_fox_track_name = parts[0]
    

    # layout_metadata is passed in directly (TrackMetaData instance for this track)
    
    if layout_metadata is None:
        # No metadata found and FCurve fallback also produced nothing (bone has no animation)
        Debug.log_warning(f"      Warning: No metadata for track '{base_fox_track_name}' (blender bone: '{base_blender_bone_name}') and no FCurves found — skipping track")
        return TrackUnitWrapper(
            name=base_blender_bone_name,
            segments_track_data=[],
            unit_flags=[TrackUnitFlags.NONE]
        )
    
    Debug.start_timer(f"export_gani_track_from_action(track={track_idx})")

    # Merge per-action overrides into layout metadata (if any)
    action_meta = None
    merged_metadata = layout_metadata
    if action:
        action_meta = TrackMetaData.from_action(action, base_fox_track_name)
        if action_meta:
            Debug.log(f"      Applying action-level overrides for track '{base_fox_track_name}' from action '{action.name}'")
            merged_metadata = merge_track_metadata(layout_metadata, action_meta)

    # Track whether the action explicitly overrides segment types (segments= in custom prop).
    # When True, skip FCurve-presence filtering so user-forced types are always exported.
    has_segment_override = bool(action_meta and action_meta.segment_types)

    # Use merged unit_flags in layout_metadata (action overrides applied if present)
    unit_flags_int = int(merged_metadata.unit_flags) if merged_metadata.unit_flags is not None else 0
    
    segment_types = merged_metadata.segment_types
    Debug.log(f"      Exporting track {track_idx}: '{base_fox_track_name}' -> {len(segment_types)} segments, flags={unit_flags_int}")
    
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
        # Static tracks are also exempt: they are sampled from the rest pose, not FCurves.
        # When the action has an explicit segments= override, skip filtering entirely.
        if (not is_static
                and fcurve_cache
                and not has_segment_override
                and segment_type != SegmentType.VECTOR4):
            if not bone_has_fcurves_for_segment(
                    segment_bone_name, segment_type, segment_fox_mapping_params, fcurve_cache):
                Debug.log(
                    f"        Skipping segment {segment_idx} ({segment_type.name}) "
                    f"for '{segment_bone_name}': no FCurves in this action"
                )
                continue

        # Check if this bone exists in the armature
        if segment_bone_name and segment_bone_name in armature.pose.bones:
            Debug.start_timer(f"export_keyframes_track(segment_bone_name={segment_bone_name})")
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
                transform_cache
            )
            Debug.stop_timer(f"export_keyframes_track(segment_bone_name={segment_bone_name})")

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
    
    Debug.stop_timer(f"export_gani_track_from_action(track={track_idx})")

    # Create GaniTrack - use base track name for the track itself
    return TrackUnitWrapper(
        name=base_fox_track_name,
        segments_track_data=keyframes_tracks,
        unit_flags=unit_flags_list
    )


def export_gani_tracks_from_action(armature: bpy.types.Object,
                       action_data: ExportActionData,
                       track_segment_bone_mapping: Optional[TrackSegmentBoneMapping],
                       layout_metadata_dict: Dict[str, TrackMetaData],
                       force_highest_bit_encoding: bool = False) -> List['TrackUnitWrapper']:
    """Export a single action as GANI track data.
    
    This is the export counterpart to the per-GANI processing in import_track_data().
    If both mapping and layout metadata dict are None, the function falls back to exporting
    tracks based on the armature bone list and any fcurves present on the action.
    
    Args:
        armature: Armature object
        action_data: ExportActionData containing action and export parameters
    track_segment_bone_mapping: Optional unified mapping from (track_idx, segment_idx) to (bone_name, fox_mapping_params). If None, fallback mode is used.
    layout_metadata_dict: Dictionary mapping fox track name to TrackMetaData. If empty, fallback mode is used.
        force_highest_bit_encoding: If True, use highest available bit sizes for all segments
        
    Returns:
        List of GaniTrack objects
    """
    action = action_data.action
    frame_start = action_data.frame_start
    frame_end = action_data.frame_end
    
    Debug.log(f"\n  Exporting action as gani: {action_data.to_string()}")

    try:
        gani_tracks = []
        
        # Process FCurves for export (bake non-linear, clean redundant keyframes)
        # This may create a modified copy of the action if non-linear fcurves are found
        processed_action = action
        if action and action_data.export_clean_threshold > 0:
            clean_threshold = action_data.export_clean_threshold
            Debug.log(f"    Processing FCurves for export (clean_threshold={clean_threshold})...")
            try:
                # Temporarily assign action to armature for processing
                original_action = armature.animation_data.action if armature.animation_data else None
                if not armature.animation_data:
                    armature.animation_data_create()
                armature.animation_data.action = action
                
                export_result = bake_and_clean_export_fcurves(
                    armature=armature,
                    fcurve_clean_threshold=clean_threshold
                )
                
                processed_action = export_result['action']
                
                Debug.log(f"      Non-linear FCurves baked: {export_result['fcurves_baked']}")
                Debug.log(f"      FCurves cleaned: {export_result['fcurves_cleaned']}")
                Debug.log(f"      Already linear FCurves: {export_result['fcurves_already_linear']}")
                
                # Restore original action on armature
                if original_action is not None:
                    armature.animation_data.action = original_action
                else:
                    # No former action - clear it explicitly
                    if armature.animation_data:
                        armature.animation_data.action = None
                
            except Exception as e:
                Debug.log_warning(f"    Warning: Failed to process export FCurves: {str(e)}")
                processed_action = action
        
        # Build fcurve cache once for this action (major performance optimization)
        # This eliminates 20-100× redundancy from scanning action.fcurves for every bone
        Debug.start_timer("build_fcurve_cache")
        fcurve_cache = FCurveCache.build(processed_action) if processed_action else None
        Debug.stop_timer("build_fcurve_cache")
        
        if fcurve_cache and not fcurve_cache.is_empty():
            Debug.log(f"    Built fcurve cache: {len(fcurve_cache.get_bones())} bones indexed")

        # Build transform cache once for this action (major performance optimization)
        # This eliminates thousands of scene.frame_set() calls during track export
        transform_cache = TransformsCache(armature, frame_start, frame_end)
        transform_cache.build()

        # Create synthetic mapping if needed (when no mapping provided)
        if not track_segment_bone_mapping or not layout_metadata_dict:
            track_segment_bone_mapping, synthetic_metadata = create_synthetic_mapping(
                armature, action, layout_metadata_dict
            )
            # Merge synthetic metadata into layout_metadata_dict
            layout_metadata_dict = {**(layout_metadata_dict or {}), **synthetic_metadata}
        
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
                base_fox_track_name = fox_track_name
                if '_' in fox_track_name:
                    parts = fox_track_name.rsplit('_', 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        base_fox_track_name = parts[0]
                
                if layout_metadata_dict and base_fox_track_name in layout_metadata_dict:
                    layout_metadata = layout_metadata_dict[base_fox_track_name]
                elif action:
                    # Fallback: layout action exists but has no matching entry for this bone.
                    # Derive minimal metadata from FCurves so export can proceed.
                    layout_metadata = TrackMetaData.from_fcurves(
                        bone_name=_blender_bone_name, action=action
                    )
                    if layout_metadata:
                        Debug.log_warning(
                            f"      No layout metadata for track '{base_fox_track_name}' "
                            f"(bone: '{_blender_bone_name}') — derived from FCurves"
                        )
            
            gani_track = export_gani_track_from_action(
                armature,
                action,
                track_idx,
                frame_start,
                frame_end,
                layout_metadata,
                track_segment_bone_mapping,
                force_highest_bit_encoding,
                fcurve_cache,
                transform_cache
            )
            
            # Add track to output (empty tracks are added to preserve structure)
            gani_tracks.append(gani_track)
        
        return gani_tracks

    finally:
        # Clean up temporary export action if one was created
        if processed_action is not None and processed_action != action:
            try:
                bpy.data.actions.remove(processed_action)
            except (ReferenceError, RuntimeError):
                pass  # Action may already be removed


# Motion Points #############################################################

def build_motion_points_list_from_armature(motion_points_armature: bpy.types.Object) -> MotionPointList2:
    """Build MotionPointList2 from a motion points armature.
    
    Extracts bone names and parent relationships to create the motion points list
    that will be written to the CommonInfo section.
    
    Only bones that have animation data are exported as motion points. Parent-only bones
    (bones that exist solely to provide hierarchy) are excluded from the export.
    
    During export, each motion point bone's parent is checked:
    - If the bone has a parent, the parent's name is hashed and written
    - If the bone has no parent, a warning is logged and an empty hash (0) is written
    
    Args:
        motion_points_armature: Armature containing motion point bones
        
    Returns:
        MotionPointList2 object containing motion point definitions
    """
    if not motion_points_armature or motion_points_armature.type != 'ARMATURE':
        return MotionPointList2(count=0, entries=[])
    
    Debug.log(f"\nBuilding MotionPointsList from armature '{motion_points_armature.name}'...")
    
    # First, identify which bones have animation data across all actions
    bones_with_animation = set()
    
    # Check NLA tracks for actions
    if motion_points_armature.animation_data:
        for nla_track in motion_points_armature.animation_data.nla_tracks:
            for strip in nla_track.strips:
                if strip.action and is_relevant_strip(strip):
                    for fcurve in iter_action_fcurves(strip.action):
                        # Extract bone name from data_path (e.g., 'pose.bones["BoneName"].location')
                        bone_name = extract_bone_name_from_data_path(fcurve.data_path)
                        if bone_name:
                            bones_with_animation.add(bone_name)
                else:
                    if strip.action:
                        Debug.log(f"  Skipping motion point strip '{getattr(strip, 'name', '<unknown>')}' (not a GANI strip)")
    
    if not bones_with_animation:
        Debug.log_warning("  Warning: No bones with animation data found in motion points armature. All bones in the armature will be exported as motion points")
        # If no animation data, export all bones
        bones_with_animation = {bone.name for bone in motion_points_armature.data.bones}
    
    entries = []
    bones = motion_points_armature.data.bones
    parent_only_bones = []
    
    for bone in bones:
        # Skip bones that don't have animation data (parent-only bones)
        if bone.name not in bones_with_animation:
            parent_only_bones.append(bone.name)
            continue
        
        # Convert name to StrCode32
        name_hash = StrCode32(int(bone.name))
        
        # Convert parent name StrCode32 (0 if no parent)
        parent_hash = StrCode32(0)
        if bone.parent:
            parent_hash = StrCode32(int(bone.parent.name))
            parent_str = f"→ {bone.parent.name}"
        else:
            Debug.log_warning(f"  Warning: Motion point bone '{bone.name}' has no parent; writing empty parent hash")
            parent_str = "(no parent - empty hash)"
        
        entry = MotionPointEntry(
            name=name_hash,
            parent_name=parent_hash
        )
        entries.append(entry)
        
        Debug.log(f"  {bone.name} {parent_str} (hash: {name_hash}, parent_hash: {parent_hash})")
    
    if parent_only_bones:
        Debug.log(f"  Skipped {len(parent_only_bones)} parent-only bone(s): {', '.join(parent_only_bones)}")
    
    motion_points_list = MotionPointList2(
        count=len(entries),
        entries=entries
    )
    
    Debug.log(f"MotionPointsList built: {motion_points_list.count} point(s)")
    
    return motion_points_list

def build_motion_point_metadata_dict(motion_points_armature: bpy.types.Object,
                                     action: bpy.types.Action) -> Dict[str, TrackMetaData]:
    """Build metadata dictionary for motion point tracks by analyzing armature and action.
    
    Motion points don't have a layout track action, but we can derive per-track metadata
    by inspecting which fcurves exist for each bone and reading stored metadata from the action.
    
    Args:
        motion_points_armature: Motion points armature object
        action: Action to analyze for per-track overrides (required)
        
    Returns:
        Dictionary mapping bone name -> TrackMetaData
    """
    metadata_dict = {}
    
    if not motion_points_armature or motion_points_armature.type != 'ARMATURE':
        return metadata_dict

    # Action is now a required parameter. If None is passed anyway, return empty data with a warning.
    if not action:
        Debug.log_warning(f"  Warning: No action provided to build_motion_point_metadata_dict() for armature '{motion_points_armature.name}', returning empty metadata dict")
        return metadata_dict
    
    bones = motion_points_armature.data.bones
    # Keep track of motion point bones for which we couldn't find metadata
    missing_metadata_bones = []
    
    for bone in bones:
        bone_name = bone.name
        
        # Determine which segments this bone has by checking fcurves
        has_rotation = False
        has_location = False
        
        if action_has_fcurves(action):
            for fc in iter_action_fcurves(action):
                rotation_quat_path = build_data_path_for_bone(bone_name, 'rotation_quaternion')
                rotation_euler_path = build_data_path_for_bone(bone_name, 'rotation_euler')
                location_path = build_data_path_for_bone(bone_name, 'location')
                
                if fc.data_path == rotation_quat_path or fc.data_path == rotation_euler_path:
                    has_rotation = True
                elif fc.data_path == location_path:
                    has_location = True
        
        # Build segment types based on what fcurves exist
        # Motion points typically use QUAT for rotation and VECTOR3 for location
        segment_types = []
        if has_rotation:
            segment_types.append(SegmentType.QUAT)
        if has_location:
            segment_types.append(SegmentType.VECTOR3)
        
        # Try to read stored metadata from action first
        component_bit_sizes = None
        unit_flags = 0
        
        # Look for stored metadata using utility function
        found_metadata_in_action = False
        for _, track_name, metadata_str in iter_track_properties(action):
            if track_name == bone_name:
                # Found metadata for this bone
                found_metadata_in_action = True
                if isinstance(metadata_str, str):
                    # Parse the metadata string to extract component_bit_sizes and flags
                    parsed = parse_action_track_metadata(metadata_str)
                    if parsed:
                        if parsed.get('component_bit_sizes'):
                            component_bit_sizes = parsed['component_bit_sizes']
                        if parsed.get('flags'):
                            # Convert flag names to integer
                            flag_names = parsed['flags']
                            flag_enums = []
                            for name in flag_names:
                                if name in TrackUnitFlags.__members__:
                                    flag_enums.append(TrackUnitFlags[name])
                            if flag_enums:
                                unit_flags = TrackUnitFlags.track_unit_flags_to_int(flag_enums)
                # Stop iterating over track properties when we've found metadata for this bone
                break
        # If the action is provided and bone is present in the action (via fcurves or metadata)
        # but we couldn't find per-track metadata for it, note it.
        bone_present_in_action = found_metadata_in_action or has_rotation or has_location
        if bone_present_in_action and not found_metadata_in_action:
            missing_metadata_bones.append(bone_name)
    
        # Only include metadata if this bone is present in the action
        bone_present_in_action = found_metadata_in_action or has_rotation or has_location
        if not bone_present_in_action:
            # Skip bones not referenced by the action (don't return metadata for them)
            continue

        # Create TrackMetaData for this bone
        metadata = TrackMetaData(
            track_name=bone_name,
            segment_types=segment_types,
            unit_flags=unit_flags,
            name_hash=StrCode32.from_string(bone_name).to_int(),
            component_bit_sizes=component_bit_sizes,
            rig_unit_type=None  # Motion points don't have explicit rig types
        )
        
        metadata_dict[bone_name] = metadata
    
    # Emit a consolidated warning if we couldn't find metadata for any bones
    if missing_metadata_bones:
        Debug.log_warning(f"  Warning: No metadata found for {len(missing_metadata_bones)} motion point(s) in armature '{motion_points_armature.name}': {', '.join(missing_metadata_bones)}")

    return metadata_dict

def collect_motion_point_actions(motion_points_armature: bpy.types.Object, use_nla: bool, export_clean_threshold: float = 0.0) -> List[ExportActionData]:
    """Collect motion point animation actions from the motion points armature.
    
    Follows the same logic as collect_actions_for_export() but for motion points.
    
    Args:
        motion_points_armature: Motion points armature object
        use_nla: If True, collect from NLA strips; if False, use active action
        export_clean_threshold: Threshold for FCurve cleaning (0 = disabled)
        
    Returns:
        List of ExportActionData for motion point animations
    """
    if not motion_points_armature:
        return []
    
    Debug.log(f"\nCollecting motion point actions from '{motion_points_armature.name}'...")
    
    actions = []
    
    if use_nla and motion_points_armature.animation_data and motion_points_armature.animation_data.nla_tracks:
        # Collect from NLA strips
        Debug.log("  Using NLA strips for motion points")
        for track in motion_points_armature.animation_data.nla_tracks:
            if track.mute:
                continue
            for strip in track.strips:
                if not is_relevant_strip(strip):
                    if strip.action:
                        Debug.log(f"    Skipping motion point strip '{getattr(strip, 'name', '<unknown>')}' (not a GANI strip)")
                    continue

                action_data = ExportActionData(
                    action=strip.action,
                    frame_start=int(strip.frame_start),
                    frame_end=int(strip.frame_end),
                    source=f"NLA strip '{strip.name}' on track '{track.name}'",
                    export_clean_threshold=export_clean_threshold
                )
                actions.append(action_data)
                Debug.log(f"    {action_data.to_string()}")

    
    elif motion_points_armature.animation_data and motion_points_armature.animation_data.action:
        # Use active action
        Debug.log("  Using active action for motion points")
        action = motion_points_armature.animation_data.action
        
        # Determine frame range from action
        if action_has_fcurves(action):
            frame_start = int(min(kp.co.x for fc in iter_action_fcurves(action) for kp in fc.keyframe_points))
            frame_end = int(max(kp.co.x for fc in iter_action_fcurves(action) for kp in fc.keyframe_points))
        else:
            frame_start = 0
            frame_end = 0
        
        action_data = ExportActionData(
            action=action,
            frame_start=frame_start,
            frame_end=frame_end,
            source="Active action",
            export_clean_threshold=export_clean_threshold
        )
        actions.append(action_data)
        Debug.log(f"    {action_data.to_string()}")
    
    else:
        Debug.log("  No motion point actions found")
    
    return actions


# MTAR export #############################################################

def export_mtar(context: bpy.types.Context,
                filepath: str,
                armature: Optional[bpy.types.Object] = None,
                track_segment_bone_mapping: Optional[TrackSegmentBoneMapping] = None,
                use_nla: bool = True
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
    
    Debug.log(f"Exporting armature: {armature.name}")

    # =============================
    # =============================

    # Mapping
    Debug.log("\n1. Mapping ++++++++++++++++++++++++++++++++++++++++++++")
    Debug.start_timer("1. Mapping")
    Debug.update_progress(5, "Mapping...")

    # Use provided track_segment_bone_mapping or create default mapping from armature
    if track_segment_bone_mapping is None:
        # No mapping provided - create default mapping using armature bone order
        track_segment_bone_mapping = TrackSegmentBoneMapping()
        Debug.log("\nNo track mapping provided, using armature bone order...")
        for idx, bone in enumerate(armature.data.bones):
            bone_name = bone.name
            # Create BoneParameters with bone name as fox_name
            track_segment_bone_mapping.set_segment_mapping(
                idx, 0, bone_name, BoneParameters(fox_name=bone_name)
            )
            Debug.log(f"  Track {idx}: {bone_name}")

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
    layout_action = try_find_layout_track_action()
    metadata_dict = None
    
    if layout_action:
        # Parse metadata from layout track action
        Debug.log("\nParsing layout track metadata...")
        metadata_dict = get_all_track_metadata_from_action(layout_action)

        # Finalize mapping using layout metadata so missing per-segment mappings
        # (i.e., tracks that only had a base mapping defined) are populated.
        if track_segment_bone_mapping:
            Debug.log("  Finalizing mapping with layout metadata (populate missing segments from base mapping)")
            track_segment_bone_mapping.finalize_with_layout_metadata(metadata_dict)
        
        # Build layout track from metadata (including header properties)
        Debug.log("\nBuilding layout track structure...")
        layout_track = build_layout_track_from_metadata(track_segment_bone_mapping, metadata_dict, layout_action, force_highest_bit_encoding)
    else:
        # Create placeholder layout track without metadata
        
        Debug.log_warning("\nNo Layout track action found! creating placeholder layout track but this will probably cause issues.")
        # Count number of tracks
        track_count = track_segment_bone_mapping.get_total_track_count()
        placeholder_header = TrackHeader(
            unit_count=track_count,
            segment_count=0,
            t_id=0,
            unknown_a=0,
            unknown_b=0,
            frame_count=0,
            frame_rate=60,
            unit_offsets=[]
        )
        layout_track = Tracks(
            header=placeholder_header,
            track_units=[]
        )
    
    # Collect actions to export
    actions_to_export = collect_actions_for_export_from_armature(
        armature, 
        use_nla,
        export_clean_threshold=export_props.export_fcurve_clean_threshold
    )
    
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
    
    # Set MTAR version and flags from layout action
    # This determines whether to use new format (GANI2) or old format (FoxData)
    mtar_props = read_mtar_properties_from_action(layout_action)
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

    # Find motion points armature and collect motion point data
    Debug.log("\n=== Motion Points Detection ===")
    motion_points_armature = export_props.motion_points_armature
    
    motion_point_actions_data: List[ExportActionData] = []
    motion_points_list: Optional[MotionPointList2] = None
    motion_point_actions_by_gani_index: Dict[int, ExportActionData] = {}
    
    if motion_points_armature:
        Debug.log(f"Found motion points armature: {motion_points_armature.name}")
        
        # Build MotionPointsList from armature bones (but do not write header count yet - it is computed at write time)
        motion_points_list : MotionPointList2 = build_motion_points_list_from_armature(motion_points_armature)
        
        # Collect motion point actions
        motion_point_actions_data = collect_motion_point_actions(motion_points_armature, use_nla, export_props.export_fcurve_clean_threshold)
        
        if motion_point_actions_data:
            Debug.log(f"Found {len(motion_point_actions_data)} motion point action(s)")
            # Build lookup map for motion-point actions by GANI index
            motion_point_actions_by_gani_index = build_motion_point_action_maps(motion_point_actions_data)
        else:
            Debug.log("No motion point actions found (motion points list will be exported without animations)")
    else:
        if export_props.motion_points_armature:
            Debug.log(f"The selected object is not a motion points armature or the armature is invalid: {export_props.motion_points_armature}")
        else:
            Debug.log("No motion points armature selected")
        Debug.log("Motion points will not be exported")
    
    Debug.stop_timer("3. Motion Points")

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

        gani_tracks: List[TrackUnitWrapper] = export_gani_tracks_from_action(
            armature,
            action_data,
            track_segment_bone_mapping,
            metadata_dict,
            force_highest_bit_encoding
        )

        tracks_data = GaniExportTracksData(
            gani_tracks=gani_tracks,
            action=gani_action,
            source=action_data.source
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
            motion_point_action_data = find_motion_point_action_for_gani(gani_name, motion_point_actions_by_gani_index)

        if motion_point_action_data:
            Debug.log(f"\n  Exporting motion points for GANI '{gani_name}': {motion_point_action_data.action.name}")

            # MetaData: Build metadata dict for motion points by analyzing the action and armature
            motion_point_metadata_dict: Dict[str, TrackMetaData] = build_motion_point_metadata_dict(motion_points_armature, motion_point_action_data.action)
            Debug.log(f"    Built metadata from {len(motion_point_metadata_dict)} motion point bone(s)")

            # Export motion point tracks
            motion_point_tracks = export_gani_tracks_from_action(motion_points_armature,
                                                                 motion_point_action_data,
                                                                 None,  # No bone mapping needed yet for motion points
                                                                 motion_point_metadata_dict,  # Pass the built metadata dict
                                                                 force_highest_bit_encoding)
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
            motion_points_data = GaniExportMotionPointsData(
                motion_point_tracks=motion_point_tracks,
                action=motion_point_action_data.action if motion_point_action_data else None
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
        motion_events = read_motion_events_from_action(gani_action)

        motion_events_data = None
        if motion_events:
            motion_events_data = GaniMotionEventsData(
                motion_events=motion_events,
                action=gani_action
            )
            Debug.log(f"  Found {motion_events.count} motion event categor(ies) in action")
        
        Debug.stop_timer(f"4.{action_idx}.3 Motion Events")

        # =============================

        Debug.log(f"\n4.{action_idx}.4 Storing Data ----------------------------------------")

        # Create GaniExportData object
        gani_data: GaniExportData = GaniExportData(
            name=gani_name,
            frame_count=frame_count,
            frame_rate=60,
            frame_start=frame_start,
            frame_end=frame_end,
            tracks_data=tracks_data,
            motion_points_data=motion_points_data,
            motion_events_data=motion_events_data
        )
        
        # Add to writer
        writer.add_gani_data(gani_data)
        if motion_point_tracks:
            Debug.log(f"  Added GANI data: '{gani_name}' ({frame_count} frames) with {len(motion_point_tracks)} motion point track(s)")
        else:
            Debug.log(f"  Added GANI data: '{gani_name}' ({frame_count} frames)")
    
    Debug.stop_timer("4. Animations")

    # Set motion points data if available
    # IMPORTANT: MTAR header count is SEPARATE from CommonInfo count!
    # - Header count: max motion point units used across all GANIs (set via set_motion_point_header_count)
    # - CommonInfo count: total motion point bone definitions (in motion_points_list.count)
    # The header value is informational only during import; CommonInfo has the actual bone data.
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

    # Build animation names for the info file using the writer helper so we
    # reuse the same naming logic (handles NLA strips and active actions).
    animation_names = [writer.get_animation_name_for_gani(gd) for gd in writer.gani_data_list]

    # Write the info file with animation names
    Debug.log("\n6. Writing animation info file... ++++++++++++++++++++++++++++++++++++++++++++")
    Debug.start_timer("6. Writing animation info file")
    
    # Only write the info file if the export setting is enabled
    if export_props.info_file:
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
    
    Debug.stop_timer("6. Writing animation info file")
    
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
