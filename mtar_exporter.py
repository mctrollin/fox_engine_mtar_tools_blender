"""
MTAR animation exporter for Metal Gear Solid V.

This module handles the export of Blender animation data to MTAR format.
"""

from typing import Optional, Dict, List
from pathlib import Path

import bpy
from mathutils import Quaternion

from .py_utilities.utilities_logging import Debug, start_timer, stop_timer
from .py_utilities.utilities_transforms import reverse_directional_location, apply_reverse_transforms, get_local_space_transform, get_world_space_transform, blender_to_fox_vector, blender_to_fox_quaternion
from .py_utilities.utilities_blender_animation import FCurveCache

from .py_foxwrap.foxwrap_motionevent import read_motion_events_from_action
from .py_foxwrap.foxwrap_metadata import parse_action_track_metadata, read_track_header_properties_from_action
from .py_foxwrap.foxwrap_metadata import TrackMetaData, merge_track_metadata, iter_track_properties, get_all_track_metadata_from_action
from .py_foxwrap.foxwrap_misc import TrackUnitWrapper, Tracks, TrackDataBlobWrapper
from .py_foxwrap.foxwrap_mtar_writer import MtarWriter
from .py_foxwrap.foxwrap_misc_export import (
    GaniData, GaniTracksData, GaniMotionPointsData, GaniMotionEventsData,
    TrackSegmentBoneMapping, ExportActionData, BoneParameters
)

from .py_fox.fox_gani_types import AnimKeyframe, SegmentType, TrackUnitFlags, TrackHeader, TrackUnit, TrackData, TrackDataBlob
from .py_fox.fox_mtar_types import MotionPointList2, MotionPointEntry
from .py_fox.fox_frig_types import RigUnitType
from .py_fox.fox_misc_types import StrCode32


# Layout and MetaData #############################################################

def find_layout_track_action() -> Optional[bpy.types.Action]:
    """Find the layout track action in the scene.
    
    Searches for an action with a name containing 'layout' or 'LAYOUT_TRACK'.
        
    Returns:
        Layout track action if found, None otherwise
    """
    # Search in all actions
    for action in bpy.data.actions:
        # Check for layout track naming patterns
        if 'layout' in action.name.lower() or 'LAYOUT_TRACK' in action.name:
            Debug.log(f"  Found layout track action: '{action.name}'")
            return action
    
    Debug.log_warning("  Warning: No layout track action found")
    return None

def build_layout_track_from_metadata(track_segment_bone_mapping: TrackSegmentBoneMapping, 
                                     metadata_dict: Dict[str, TrackMetaData],
                                     layout_action: Optional[bpy.types.Action] = None) -> 'Tracks':
    """Build a Tracks (layout track) object from metadata.
    
    Args:
        track_segment_bone_mapping: Unified mapping from (track_idx, segment_idx) to (bone_name, fox_mapping_params)
        metadata_dict: Dictionary of fox_track_name -> TrackMetaData
        layout_action: Optional layout action containing header properties (t_id, unknown_a, unknown_b, frame_rate)
        
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
        
        # Strip segment suffix if present (e.g., "LArm_0" -> "LArm")
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
    
    Debug.log(f"    Using layout header from action: t_id={header_props['t_id']}, unknown_a={header_props['unknown_a']}, unknown_b={header_props['unknown_b']}, frame_count={header_props['frame_count']}, frame_rate={header_props['frame_rate']}")
    
    # Create TrackHeader
    header = TrackHeader(
        unit_count=len(track_units),
        segment_count=total_segments,
        t_id=header_props['t_id'],
        unknown_a=header_props['unknown_a'],
        unknown_b=header_props['unknown_b'],
        frame_count=header_props['frame_count'],
        frame_rate=header_props['frame_rate'],
        unit_offsets=[]
    )
    
    # Create Tracks object
    layout_track = Tracks(
        header=header,
        track_units=track_units
    )
    
    return layout_track


# Animation #############################################################

def collect_actions_for_export_from_armature(armature: bpy.types.Object, use_nla: bool = True) -> List[ExportActionData]:
    """Collect actions to export based on NLA tracks or active action.
    
    Args:
        armature: Armature object
        use_nla: If True, check NLA tracks first; if False, use only active action
        
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
                if strip.mute:
                    Debug.log(f"    Strip {strip_idx} '{strip.name}': Muted (skipping)")
                    continue
                
                if not strip.action:
                    Debug.log(f"    Strip {strip_idx} '{strip.name}': No action (skipping)")
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
                    source=source
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
        frame_start = int(action.frame_range[0])
        frame_end = int(action.frame_range[1])
        
        # Create export action data
        export_action = ExportActionData(
            action=action,
            frame_start=frame_start,
            frame_end=frame_end,
            source='Active Action'
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
    elif segment_type in [SegmentType.VECTOR3, SegmentType.VECTOR_DIFF]:
        # Location
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
        data_paths = [f'pose.bones["{bone_name}"].{prop}' for prop in property_names]
        for fcurve in action.fcurves:
            if fcurve.data_path in data_paths:
                for keyframe_point in fcurve.keyframe_points:
                    # keyframe_point.co[0] is always relative to action's internal frame range
                    action_relative_frame = int(keyframe_point.co[0])
                    
                    # Convert to export coordinate system (absolute for NLA, action-relative for active)
                    export_frame = action_relative_frame + frame_offset
                    
                    # Filter by export range
                    if frame_start <= export_frame <= frame_end:
                        keyframe_frames.add(export_frame)
    
    # If no keyframes found, export at least the first frame
    if not keyframe_frames:
        keyframe_frames.add(frame_start)
    
    return sorted(list(keyframe_frames))


def export_keyframes_track(armature: bpy.types.Object, blender_bone_name: str,
                          bone_params: BoneParameters, segment_type: SegmentType,
                          frame_start: int, frame_end: int,
                          is_static: bool, action: bpy.types.Action = None,
                          rig_unit_type: Optional[RigUnitType] = None,
                          fcurve_cache: Optional[FCurveCache] = None) -> List['AnimKeyframe']:
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
    
    Debug.log(f"    Collected keyed frames {len(export_frames)}")

    if segment_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
        # Rotation segment
        return export_rotation_segment(
            armature, blender_bone_name, bone_params,
            export_frames, frame_start, is_static, rig_unit_type
        )
    
    elif segment_type in [SegmentType.VECTOR3, SegmentType.VECTOR_DIFF]:
        # Location segment
        return export_location_segment(
            armature, blender_bone_name, bone_params,
            export_frames, frame_start, is_static, rig_unit_type
        )
    
    else:
        # Unsupported segment type
        Debug.log_warning(f"    Warning: Unsupported segment type {segment_type}")
        return []

def _get_rotation_transform_fn(bone_params: BoneParameters, armature: bpy.types.Object,
                               blender_bone_name: str, space_bone: Optional[str],
                               rig_unit_type: Optional[RigUnitType]):
    """Return a callable that produces rotation quaternion for a given frame.
    
    This helper eliminates code duplication between as_ik_up and normal rotation paths.
    The returned function captures the context needed to compute rotation at any frame.
    
    Args:
        bone_params: Bone parameters (contains as_ik_up data if applicable)
        armature: Armature object
        blender_bone_name: Name of the bone in Blender
        space_bone: Custom space bone name (or None for default space)
        rig_unit_type: Rig unit type (determines local vs world space for normal tracks)
        
    Returns:
        Callable that takes (frame: int) and returns Quaternion
    """
    if bone_params.as_ik_up:
        # as_ik_up path: convert directional location to rotation
        as_ik_up_data = bone_params.as_ik_up
        axis = as_ik_up_data.axis
        distance = as_ik_up_data.distance
        base_bone_name = as_ik_up_data.bone_base
        
        def get_rotation_as_ik_up(frame: int) -> Quaternion:
            ik_location, _ = get_world_space_transform(armature, blender_bone_name, frame, space_bone)
            base_location, _ = get_world_space_transform(armature, base_bone_name, frame, space_bone)
            return reverse_directional_location(ik_location, base_location, axis, distance)
        
        return get_rotation_as_ik_up
    else:
        # Normal rotation path: read quaternion directly
        use_world_space = RigUnitType.is_world_space_unit_type(rig_unit_type)
        
        def get_rotation_normal(frame: int) -> Quaternion:
            if use_world_space:
                _, quat = get_world_space_transform(armature, blender_bone_name, frame, space_bone)
            else:
                _, quat = get_local_space_transform(armature, blender_bone_name, frame)
            return quat
        
        return get_rotation_normal


def export_rotation_segment(armature: bpy.types.Object, blender_bone_name: str,
                            bone_params: BoneParameters, export_frames: List[int],
                            frame_start: int, is_static: bool, 
                            rig_unit_type: Optional[RigUnitType] = None) -> List['AnimKeyframe']:
    """Export rotation segment keyframes."""
    keyframes = []
    start_timer("export_rotation_segment")
    
    # POINT 4 OPTIMIZATION: Extract loop-invariant setup and use pluggable transform function
    # These are constant across all frames, so extract once to avoid redundant lookups
    rotation_offset = bone_params.rotation_offset
    rotation_axis_map = bone_params.rotation_axis_map
    space_bone = TrackMetaData.extract_space_bone(bone_params.space_r)
    
    # Get rotation transform function (varies by as_ik_up and space type)
    # This eliminates ~40 lines of code duplication between two paths
    get_rotation = _get_rotation_transform_fn(bone_params, armature, blender_bone_name,
                                              space_bone, rig_unit_type)
    
    # Unified frame loop for both as_ik_up and normal rotation
    for frame in export_frames:
        # Get rotation using appropriate method (as_ik_up or normal)
        blender_quat = get_rotation(frame)
        
        # Apply reverse transformations (offsets, axis mapping)
        fox_quat = apply_reverse_transforms(blender_quat, rotation_offset, rotation_axis_map)
        
        # Convert to Fox Engine coordinate system
        fox_quat_final = blender_to_fox_quaternion(fox_quat)
        
        # Create keyframe
        frame_delta = frame - frame_start if not is_static else 0
        keyframe = AnimKeyframe(frame=frame_delta, value=fox_quat_final)
        keyframes.append(keyframe)
    
    stop_timer("export_rotation_segment")
    return keyframes

def export_location_segment(armature: bpy.types.Object, blender_bone_name: str,
                            bone_params: BoneParameters, export_frames: List[int],
                            frame_start: int, is_static: bool,
                            rig_unit_type: Optional[RigUnitType] = None) -> List['AnimKeyframe']:
    """Export location segment keyframes."""
    keyframes = []
    start_timer("export_location_segment")
    
    # Get custom space if specified (constant across all frames)
    space_bone = None
    if bone_params.space_l:
        space_l_value = bone_params.space_l
        if isinstance(space_l_value, str) and not space_l_value.startswith('ws'):
            space_bone = space_l_value
    
    # is_world_space result is constant across all frames
    use_world_space = RigUnitType.is_world_space_unit_type(rig_unit_type)
    
    # For regular location: read and convert per frame
    for frame in export_frames:
        # Read location (using pre-determined space)
        if use_world_space:
            # Use world space transforms for ORIENTATION, TWO_BONE, ARM
            blender_location, _ = get_world_space_transform(armature, blender_bone_name, frame, space_bone)
        else:
            # Use local space transforms for other types (LOCAL_ORIENTATION, TRANSFORM, ROOT, etc.)
            blender_location, _ = get_local_space_transform(armature, blender_bone_name, frame)
        
        # Convert to Fox Engine coordinate system
        fox_location = blender_to_fox_vector(blender_location)
        
        # Create keyframe
        frame_delta = frame - frame_start if not is_static else 0
        keyframe = AnimKeyframe(frame=frame_delta, value=fox_location)
        keyframes.append(keyframe)
    
    stop_timer("export_location_segment")
    return keyframes


def export_gani_track_from_action(armature: bpy.types.Object, track_idx: int,
                     track_segment_bone_mapping: TrackSegmentBoneMapping, frame_start: int, frame_end: int,
                     action: bpy.types.Action, layout_metadata: Optional[TrackMetaData],
                     fcurve_cache: Optional[FCurveCache] = None) -> 'TrackUnitWrapper':
    """Export a GaniTrack (all segments for one track).
    
    This is the export counterpart to import_gani_track().
    Gets track structure (segments) from layout action and animation-specific
    unit flags from the animation action.
    
    For multi-segment tracks, each segment maps to a different Blender bone
    (e.g., "LArm_0", "LArm_1", "LArm_2") as defined in the track mapping file.
    
    Args:
        armature: Armature object
        track_idx: Index of this track in the layout
        track_segment_bone_mapping: Unified mapping from (track_idx, segment_idx) to (bone_name, fox_mapping_params)
        frame_start: First frame to export
        frame_end: Last frame to export
        action: Animation action containing keyframes
        layout_metadata: TrackMetaData instance containing track structure metadata for this track
        fcurve_cache: Optional pre-built FCurveCache for fast lookups
        
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
    
    # Strip segment suffix if present (e.g., "LArm_0" -> "LArm")
    # Metadata is stored under the base track name for multi-segment tracks
    base_fox_track_name = fox_track_name
    if '_' in fox_track_name:
        parts = fox_track_name.rsplit('_', 1)
        if len(parts) == 2 and parts[1].isdigit():
            base_fox_track_name = parts[0]
    

    # layout_metadata is passed in directly (TrackMetaData instance for this track)
    
    if layout_metadata is None:
        # No metadata found - cannot export this track
        Debug.log_error(f"      Error: No layout metadata found for fox track '{base_fox_track_name}' (blender bone: '{base_blender_bone_name}') in layout action")
        return TrackUnitWrapper(
            name=base_blender_bone_name,
            segments_track_data=[],
            unit_flags=[TrackUnitFlags.NONE]
        )
    
    start_timer(f"export_gani_track_from_action(track={track_idx})")

    # Merge per-action overrides into layout metadata (if any)
    merged_metadata = layout_metadata
    if action:
        action_meta = TrackMetaData.from_action(action, base_fox_track_name)
        if action_meta:
            Debug.log(f"      Applying action-level overrides for track '{base_fox_track_name}' from action '{action.name}'")
            merged_metadata = merge_track_metadata(layout_metadata, action_meta)

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
        # Look up the specific bone and parameters for this segment
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
        
        # Check if this bone exists in the armature
        if segment_bone_name and segment_bone_name in armature.pose.bones:
            start_timer(f"export_keyframes_track(segment_bone_name={segment_bone_name})")
            # Export keyframes for this segment
            keyframes = export_keyframes_track(
                armature, segment_bone_name, segment_fox_mapping_params,
                segment_type, frame_start, frame_end, is_static, action,
                merged_metadata.rig_unit_type, fcurve_cache
            )
            stop_timer(f"export_keyframes_track(segment_bone_name={segment_bone_name})")

            # Get component_bit_size from metadata if available, otherwise use default
            component_bit_size = 16  # Default for export
            if merged_metadata.component_bit_sizes and segment_idx < len(merged_metadata.component_bit_sizes):
                component_bit_size = merged_metadata.component_bit_sizes[segment_idx]
            
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
    
    stop_timer(f"export_gani_track_from_action(track={track_idx})")

    # Create GaniTrack - use base track name for the track itself
    return TrackUnitWrapper(
        name=base_fox_track_name,
        segments_track_data=keyframes_tracks,
        unit_flags=unit_flags_list
    )


def export_gani_tracks_from_action(armature: bpy.types.Object,
                       action_data: ExportActionData,
                       track_segment_bone_mapping: Optional[TrackSegmentBoneMapping],
                       layout_metadata_dict: Dict[str, TrackMetaData]) -> List['TrackUnitWrapper']:
    """Export a single action as GANI track data.
    
    This is the export counterpart to the per-GANI processing in import_track_data().
    If both mapping and layout metadata dict are None, the function falls back to exporting
    tracks based on the armature bone list and any fcurves present on the action.
    
    Args:
        armature: Armature object
        action_data: ExportActionData containing action and export parameters
    track_segment_bone_mapping: Optional unified mapping from (track_idx, segment_idx) to (bone_name, fox_mapping_params). If None, fallback mode is used.
    layout_metadata_dict: Dictionary mapping fox track name to TrackMetaData. If empty, fallback mode is used.
        
    Returns:
        List of GaniTrack objects
    """
    action = action_data.action
    frame_start = action_data.frame_start
    frame_end = action_data.frame_end
    
    Debug.log(f"\n  Exporting action as gani: {action_data.to_string()}")
    
    # Check if we're in NLA tweak mode (happens when user double-clicks an NLA strip)
    # In tweak mode, the action attribute is read-only
    was_in_tweak_mode = False
    if armature.animation_data:
        # Check if NLA is in tweak mode
        was_in_tweak_mode = armature.animation_data.use_tweak_mode
        if was_in_tweak_mode:
            Debug.log_warning("    Warning: Armature is in NLA tweak mode, exiting tweak mode temporarily")
            # Exit tweak mode to allow action changes
            armature.animation_data.use_tweak_mode = False
    
    try:
        gani_tracks = []
        
        # Build fcurve cache once for this action (major performance optimization)
        # This eliminates 20-100× redundancy from scanning action.fcurves for every bone
        start_timer("build_fcurve_cache")
        fcurve_cache = FCurveCache.build(action) if action else None
        stop_timer("build_fcurve_cache")
        
        if fcurve_cache and not fcurve_cache.is_empty():
            Debug.log(f"    Built fcurve cache: {len(fcurve_cache.get_bones())} bones indexed")

        # If a mapping and layout metadata dict are provided, use the mapping to export tracks
        if track_segment_bone_mapping and layout_metadata_dict:
            # Process each track in the mapping
            track_indices = track_segment_bone_mapping.get_track_indices()
            Debug.log(f"    Processing {len(track_indices)} track(s)...")
            for track_idx in track_indices:
                # Find base fox track name for this track index to lookup metadata
                base_mapping = track_segment_bone_mapping.get_base_mapping(track_idx)
                if base_mapping:
                    _blender_bone_name, fox_mapping_params = base_mapping
                    fox_track_name = fox_mapping_params.fox_name
                    # Strip multi-segment suffix if present
                    base_fox_track_name = fox_track_name
                    if '_' in fox_track_name:
                        parts = fox_track_name.rsplit('_', 1)
                        if len(parts) == 2 and parts[1].isdigit():
                            base_fox_track_name = parts[0]
                    layout_metadata = None
                    if layout_metadata_dict and base_fox_track_name in layout_metadata_dict:
                        layout_metadata = layout_metadata_dict[base_fox_track_name]
                else:
                    layout_metadata = None

                gani_track = export_gani_track_from_action(
                    armature, track_idx,
                    track_segment_bone_mapping, frame_start, frame_end, action, layout_metadata, fcurve_cache
                )
                gani_tracks.append(gani_track)
        else:
            # Fallback: No mapping provided (e.g., exporting motion points)
            # Build a synthetic mapping and reuse export_gani_track_from_action()
            Debug.log("    No mapping provided; exporting tracks from armature bones (fallback mode)")
            
            # Step 1: Build synthetic mapping and prepare metadata for all bones
            bones_iterable = armature.pose.bones if armature.pose else armature.data.bones
            temp_mapping = TrackSegmentBoneMapping()
            bones_to_export = []  # List of (track_idx, bone_name, bone_metadata)
            
            for track_idx, bone in enumerate(bones_iterable):
                bone_name = bone.name
                
                # Get metadata for this bone (from layout_metadata_dict or by analyzing fcurves)
                bone_metadata: TrackMetaData = None
                if layout_metadata_dict and bone_name in layout_metadata_dict:
                    # Use provided metadata
                    bone_metadata = layout_metadata_dict[bone_name]
                else:
                    # Build minimal metadata by analyzing fcurves (legacy fallback)
                    bone_metadata = TrackMetaData.from_fcurves(bone_name=bone_name, action=action)
                
                # Skip bones with no metadata (no fcurves and not in metadata_dict)
                if not bone_metadata:
                    continue
                
                # Merge per-action overrides if available
                if action:
                    action_meta_bone = TrackMetaData.from_action(action, bone_name)
                    if action_meta_bone:
                        bone_metadata = merge_track_metadata(bone_metadata, action_meta_bone)
                
                # Create single-segment mapping for this bone
                # Each bone becomes a single track with one segment (segment 0)
                temp_mapping.set_segment_mapping(
                    track_idx, 0, bone_name,
                    BoneParameters(fox_name=bone_name)
                )
                
                bones_to_export.append((track_idx, bone_name, bone_metadata))
            
            # Step 2: Call export_gani_track_from_action for each bone
            Debug.log(f"    Processing {len(bones_to_export)} bone(s) with fcurves...")
            for track_idx, bone_name, bone_metadata in bones_to_export:
                gani_track = export_gani_track_from_action(
                    armature, track_idx,
                    temp_mapping, frame_start, frame_end, action,
                    bone_metadata, fcurve_cache
                )
                
                # Only add tracks that have segments
                if gani_track.segments_track_data:
                    gani_tracks.append(gani_track)
        
        return gani_tracks

    finally:
        # Restore original action and NLA state
        if armature.animation_data:
            # Restore tweak mode if it was active
            if was_in_tweak_mode:
                armature.animation_data.use_tweak_mode = True


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
                if strip.action:
                    for fcurve in strip.action.fcurves:
                        # Extract bone name from data_path (e.g., 'pose.bones["BoneName"].location')
                        if 'pose.bones[' in fcurve.data_path:
                            start = fcurve.data_path.find('["') + 2
                            end = fcurve.data_path.find('"]', start)
                            if start > 1 and end > start:
                                bone_name = fcurve.data_path[start:end]
                                bones_with_animation.add(bone_name)
    
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
        
        if action.fcurves:
            for fc in action.fcurves:
                if f'pose.bones["{bone_name}"].rotation_quaternion' in fc.data_path or \
                   f'pose.bones["{bone_name}"].rotation_euler' in fc.data_path:
                    has_rotation = True
                elif f'pose.bones["{bone_name}"].location' in fc.data_path:
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
        for track_idx, track_name, metadata_str in iter_track_properties(action):
            if track_name == bone_name:
                # Found metadata for this bone
                found_metadata_in_action = True
                if isinstance(metadata_str, str) and metadata_str.startswith('@track'):
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

def collect_motion_point_actions(motion_points_armature: bpy.types.Object, use_nla: bool) -> List[ExportActionData]:
    """Collect motion point animation actions from the motion points armature.
    
    Follows the same logic as collect_actions_for_export() but for motion points.
    
    Args:
        motion_points_armature: Motion points armature object
        use_nla: If True, collect from NLA strips; if False, use active action
        
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
                if strip.mute or not strip.action:
                    continue
                
                action_data = ExportActionData(
                    action=strip.action,
                    frame_start=int(strip.action_frame_start),
                    frame_end=int(strip.action_frame_end),
                    source=f"NLA strip '{strip.name}' on track '{track.name}'"
                )
                actions.append(action_data)
                Debug.log(f"    {action_data.to_string()}")
    
    elif motion_points_armature.animation_data and motion_points_armature.animation_data.action:
        # Use active action
        Debug.log("  Using active action for motion points")
        action = motion_points_armature.animation_data.action
        
        # Determine frame range from action
        if action.fcurves:
            frame_start = int(min(kp.co.x for fc in action.fcurves for kp in fc.keyframe_points))
            frame_end = int(max(kp.co.x for fc in action.fcurves for kp in fc.keyframe_points))
        else:
            frame_start = 0
            frame_end = 0
        
        action_data = ExportActionData(
            action=action,
            frame_start=frame_start,
            frame_end=frame_end,
            source="Active action"
        )
        actions.append(action_data)
        Debug.log(f"    {action_data.to_string()}")
    
    else:
        Debug.log("  No motion point actions found")
    
    return actions


# MTAR export #############################################################

def export_mtar(context: bpy.types.Context, filepath: str, armature: Optional[bpy.types.Object] = None,
                track_segment_bone_mapping: Optional[TrackSegmentBoneMapping] = None, use_nla: bool = True) -> Dict[str, str]:
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
    start_timer("MTAR Export")
    
    # Mark context as used so static analysis doesn't flag it as unused
    _ = context
    # Scene properties (export options) from the UI
    props = context.scene.mtar_properties
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
    start_timer("1. Mapping")

    # Use provided track_segment_bone_mapping or create default mapping from armature
    if track_segment_bone_mapping is None:
        # No mapping provided - create default mapping using armature bone order
        track_segment_bone_mapping = TrackSegmentBoneMapping()
        Debug.log("\nNo track mapping provided, using armature bone order...")
        for idx, bone in enumerate(armature.data.bones):
            bone_name = bone.name
            track_segment_bone_mapping.set_segment_mapping(idx, 0, bone_name, {})
            Debug.log(f"  Track {idx}: {bone_name}")
    stop_timer("1. Mapping")

    # =============================
    # =============================

    # Meta Data 
    Debug.log("\n2. Meta Data ++++++++++++++++++++++++++++++++++++++++++++")
    start_timer("2. Meta Data")

    # Find and parse layout track action
    Debug.log("\nSearching for layout track action...")
    layout_action = find_layout_track_action()
    metadata_dict = None
    
    if layout_action:
        # Parse metadata from layout track action
        Debug.log("\nParsing layout track metadata...")
        metadata_dict = get_all_track_metadata_from_action(layout_action)
        
        # Build layout track from metadata (including header properties)
        Debug.log("\nBuilding layout track structure...")
        layout_track = build_layout_track_from_metadata(track_segment_bone_mapping, metadata_dict, layout_action)
    else:
        # Create placeholder layout track without metadata
        
        Debug.log("\nCreating placeholder layout track...")
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
    actions_to_export = collect_actions_for_export_from_armature(armature, use_nla)
    
    if not actions_to_export:
        Debug.log_error("  Error: No actions found to export")
        return {'CANCELLED': 'No animation data'}
    
    Debug.log(f"\n=== Exporting {len(actions_to_export)} action(s) ===")

    # Create MTAR writer with custom path hash settings
    # (The writer will get hash_generator_exe_path from Blender properties when needed)
    writer = MtarWriter(
        filepath,
        export_custom_path_hashes=props.export_custom_path_hashes,
        export_custom_path_base=props.export_custom_path_base
    )
    
    # Set the layout track on the writer
    writer.set_layout_track(layout_track)

    stop_timer("2. Meta Data")

    # =============================
    # =============================

    # Motion Points
    Debug.log("\n3. Motion Points ++++++++++++++++++++++++++++++++++++++++++++")
    start_timer("3. Motion Points")

    # Find motion points armature and collect motion point data
    Debug.log("\n=== Motion Points Detection ===")
    motion_points_armature = props.export_motion_points_armature
    
    motion_point_actions_data: List[ExportActionData] = []
    
    if motion_points_armature:
        Debug.log(f"Found motion points armature: {motion_points_armature.name}")
        
        # Build MotionPointsList from armature bones
        motion_points_list : MotionPointList2 = build_motion_points_list_from_armature(motion_points_armature)
        writer.set_motion_points_list(motion_points_list)
        
        # Collect motion point actions
        motion_point_actions_data = collect_motion_point_actions(motion_points_armature, use_nla)
        
        if motion_point_actions_data:
            Debug.log(f"Found {len(motion_point_actions_data)} motion point action(s)")
        else:
            Debug.log("No motion point actions found (motion points list will be exported without animations)")
    else:
        if props.export_motion_points_armature:
            Debug.log(f"The selected object is not a motion points armature or the armature is invalid: {props.export_motion_points_armature}")
        else:
            Debug.log("No motion points armature selected")
        Debug.log("Motion points will not be exported")
    
    stop_timer("3. Motion Points")

    # =============================
    # =============================

    # Export each action as a GaniData object
    Debug.log("\n4. Animations ++++++++++++++++++++++++++++++++++++++++++++")
    start_timer("4. Animations")

    for action_idx, action_data in enumerate(actions_to_export):

        # Get frame info from action data
        frame_start = action_data.frame_start
        frame_end = action_data.frame_end
        # FrameCount is the last frame index (relative to frame_start), not the total number of frames
        frame_count = frame_end - frame_start
        gani_name = action_data.action.name
        gani_action = action_data.action

        # =============================

        # Main animation tracks
        Debug.log(f"\n4.{action_idx}.1 Main Animation Tracks ----------------------------------------")
        start_timer(f"4.{action_idx}.1 Main Animation Tracks")

        gani_tracks: List[TrackUnitWrapper] = export_gani_tracks_from_action(
            armature, action_data,
            track_segment_bone_mapping, metadata_dict
        )

        tracks_data = GaniTracksData(
            gani_tracks=gani_tracks,
            action=gani_action,
            source=action_data.source
        )

        stop_timer(f"4.{action_idx}.1 Main Animation Tracks")
        
        # =============================
        
        # Motion Points
        Debug.log(f"\n4.{action_idx}.2 Motion Points ----------------------------------------")
        start_timer(f"4.{action_idx}.2 Motion Points")

        # Export motion point tracks for this GANI (if corresponding motion point action exists)
        motion_point_tracks: List[TrackUnitWrapper] = None
        if motion_point_actions_data and action_idx < len(motion_point_actions_data):
            motion_point_action_data: ExportActionData = motion_point_actions_data[action_idx]
            Debug.log(f"\n  Exporting motion points for GANI #{action_idx}: {motion_point_action_data.action.name}")
            
            # MetaData: Build metadata dict for motion points by analyzing the action and armature
            motion_point_metadata_dict: Dict[str, TrackMetaData] = build_motion_point_metadata_dict(
                motion_points_armature, 
                motion_point_action_data.action
            )
            Debug.log(f"    Built metadata from {len(motion_point_metadata_dict)} motion point bone(s)")
            
            # Export motion point tracks
            motion_point_tracks = export_gani_tracks_from_action(
                motion_points_armature,
                motion_point_action_data,
                None,  # No bone mapping needed yet for motion points
                motion_point_metadata_dict  # Pass the built metadata dict
            )
            Debug.log(f"    Exported {len(motion_point_tracks)} motion point track(s)")
        
        motion_points_data = None
        if motion_point_tracks:
            motion_points_data = GaniMotionPointsData(
                motion_point_tracks=motion_point_tracks,
                action=motion_point_action_data.action if motion_point_action_data else None
            )
        
        stop_timer(f"4.{action_idx}.2 Motion Points")

        # =============================
        
        # Motion Events
        Debug.log(f"\n4.{action_idx}.3 Motion Events ----------------------------------------")
        start_timer(f"4.{action_idx}.3 Motion Events")

        # Read motion events from the action if present
        motion_events = read_motion_events_from_action(gani_action)

        motion_events_data = None
        if motion_events:
            motion_events_data = GaniMotionEventsData(
                motion_events=motion_events,
                action=gani_action
            )
            Debug.log(f"  Found {motion_events.count} motion event categor(ies) in action")
        
        stop_timer(f"4.{action_idx}.3 Motion Events")

        # =============================

        Debug.log(f"\n4.{action_idx}.4 Storing Data ----------------------------------------")

        # Create GaniData object
        gani_data: GaniData = GaniData(
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
    
    stop_timer("4. Animations")

    # Write the MTAR file
    Debug.log("\n5. Writing MTAR file... ++++++++++++++++++++++++++++++++++++++++++++")
    start_timer("5. Writing MTAR file")
    writer.write()
    stop_timer("5. Writing MTAR file")

    # Build animation names for the info file using the writer helper so we
    # reuse the same naming logic (handles NLA strips and active actions).
    animation_names = [writer._get_animation_name_for_gani(gd) for gd in writer.gani_data_list]

    # Write the info file with animation names
    Debug.log("\n6. Writing animation info file... ++++++++++++++++++++++++++++++++++++++++++++")
    start_timer("6. Writing animation info file")
    
    # Only write the info file if the export setting is enabled
    if props.export_info_file:
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
    
    stop_timer("6. Writing animation info file")
    
    Debug.log("\n=== MTAR Data Export Complete ===")
    Debug.log(f"Exported {len(actions_to_export)} action(s) to {filepath}\n")
    stop_timer("MTAR Export")
    
    return {'FINISHED': f'Exported to {filepath}'}
