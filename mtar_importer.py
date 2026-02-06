import os
import math
from typing import Optional, List, Dict, Union, Tuple

import bpy
from mathutils import Quaternion, Vector

import time
from .py_utilities.utilities_logging import Debug
from .py_utilities.utilities_rig_hash import unhash_rig_type
from .py_utilities.utilities_transforms import (
    calculate_directional_location, 
    prepare_rotation_offset_quats, 
    apply_rotation_transforms, 
    fox_to_blender_vector,
    apply_rest_pose_correction_local
)
from .py_utilities.utilities_blender_animation import add_dummy_keyframes_to_action, configure_action, remove_action_from_datablock, ensure_action_fcurve, iter_action_fcurves

from .py_foxwrap.foxwrap_metadata import store_track_header_properties_on_action, TrackMetaData, make_track_property_key
from .py_foxwrap.foxwrap_misc import TrackUnitWrapper, TrackDataBlobWrapper, Tracks
from .py_foxwrap.foxwrap_motionevent import store_motion_events_on_action
from .py_foxwrap.foxwrap_mtar_reader import MtarReader
from .py_foxwrap.foxwrap_mapping import BoneParameters

from .py_fox.fox_mtar_types import MotionPointList2, MtarTableList2
from .py_fox.fox_gani_types import SegmentType, TrackUnitFlags, TrackHeader, TrackMiniHeader, EvpHeader
from .py_fox.fox_frig_types import RigUnitType, FrigFile
from .py_fox.fox_misc_types import StrCode32


FPS_59_94: float = 59.94

# Layout and MetaData #############################################################

def store_track_metadata_on_action(
    action: bpy.types.Action, 
    track_metadata_list: List[TrackMetaData],
    include_segments: bool = True,
    include_hash: bool = True
) -> None:
    """Store track metadata from TrackMetaData objects as custom properties on an action.
    
    Stores metadata in @track format matching the mapping file syntax.
    
    Layout track format: @track <name> : segments=<segments> ; bits=<bit_sizes> ; flags=<flags> ; hash=<hash>
    GANI track format:   @track <name> : bits=<bit_sizes> ; flags=<flag_names>
    
    Args:
        action: The Blender action to store metadata on
        track_metadata_list: List of TrackMetaData objects
        include_segments: Whether to include segment type abbreviations (True for layout tracks, False for GANI)
        include_hash: Whether to include name hash if present (True for layout tracks, False for GANI)
    """
    track_type = "layout" if include_segments else "GANI"
    Debug.log(f"Storing {track_type} track metadata for {len(track_metadata_list)} track(s) on action '{action.name}'")
    
    for track_idx, track_meta in enumerate(track_metadata_list):
        track_name = track_meta.track_name
        
        metadata_parts = []
        
        # Build segment type abbreviations (layout tracks only)
        if include_segments:
            segment_types = []
            for seg_type in track_meta.segment_types:
                if seg_type == SegmentType.QUAT:
                    segment_types.append('q')
                elif seg_type == SegmentType.QUAT_DIFF:
                    segment_types.append('qd')
                elif seg_type == SegmentType.VECTOR3:
                    segment_types.append('v')
                elif seg_type == SegmentType.VECTOR_DIFF:
                    segment_types.append('vd')
                elif seg_type == SegmentType.FLOAT:
                    segment_types.append('f')
                else:
                    segment_types.append('?')
            
            segment_str = ','.join(segment_types)
            metadata_parts.append(f"segments={segment_str}")
        
        # Build component bit sizes string
        bit_sizes_str = ''
        if track_meta.component_bit_sizes:
            bit_sizes_str = ','.join(str(b) for b in track_meta.component_bit_sizes)
        
        if bit_sizes_str:
            metadata_parts.append(f"bits={bit_sizes_str}")
        
        # Build flags string
        if track_meta.unit_flags is not None:
            flags_list = TrackUnitFlags.int_to_track_unit_flags(track_meta.unit_flags)
            flag_names = [flag.name for flag in flags_list]
            flags_str = ','.join(flag_names) if flag_names else ('NONE' if not include_segments else '')
        else:
            flags_str = 'NONE' if not include_segments else ''
        
        if flags_str:
            metadata_parts.append(f"flags={flags_str}")
        
        # Build hash field (layout tracks only)
        if include_hash and track_meta.name_hash is not None and track_meta.name_hash != 0:
            metadata_parts.append(f"hash={track_meta.name_hash}")
        
        # Build type field (rig_unit_type)
        if track_meta.rig_unit_type is not None:
            metadata_parts.append(f"type={track_meta.rig_unit_type.name}")
        
        # Build final @track format string
        metadata_value = f"@track {track_name} : {' ; '.join(metadata_parts)}"
        
        # Store metadata as custom property using standardized key format
        property_key = make_track_property_key(track_idx, track_name)
        action[property_key] = metadata_value
        
        # Set custom property metadata for UI display
        action.id_properties_ui(property_key).update(
            description=f"Track metadata for {track_name} in @track format"
        )
        
        if include_segments:
            Debug.log(f"  Stored: {property_key} = {metadata_value}")
        else:
            Debug.log(f"  Track {track_idx} ({track_name}): bits=[{bit_sizes_str}], flags={flags_str}")


# Mapping #############################################################

def apply_track_mapping_transformation(track_blob: TrackDataBlobWrapper, mapping_data: BoneParameters, old_name: str) -> None:
    """Apply transformation parameters from track mapping to a TrackDataBlobWrapper.
    
    This helper function extracts and applies all transformation parameters
    (name change, rotation offset, axis mapping, rotation addition) to a track.
    
    Args:
        track_blob: The TrackDataBlobWrapper to transform
        mapping_data: BoneParameters containing transformation parameters
        old_name: Original track name (for logging)
    """
    # Use track_name from BoneParameters (defaults to fox_name if not set)
    new_name: str = mapping_data.track_name if mapping_data.track_name else mapping_data.fox_name
    
    track_blob.name = new_name
    Debug.log(f"  '{old_name}' -> '{new_name}'")
    
    # Store rotation offset transformation if present (will be applied during import)
    if mapping_data.rotation_offset:
        track_blob.rotation_offset = mapping_data.rotation_offset
        # rotation_offset is now a list of offsets
        offset_list = mapping_data.rotation_offset
        for i, offset in enumerate(offset_list, 1):
            Debug.log(f"    Rotation offset #{i}: ({offset['euler'][0]}, {offset['euler'][1]}, {offset['euler'][2]}) {offset['order']}")
    
    # Store rotation axis mapping transformation if present (will be applied during import)
    if mapping_data.rotation_axis_map:
        track_blob.rotation_axis_map = mapping_data.rotation_axis_map
        axis_str = ','.join([('-' if m['negate'] else '') + m['axis'] for m in mapping_data.rotation_axis_map])
        Debug.log(f"    Rotation axis mapping: {axis_str}")
    
    # Store directional vector IK transformation if present (will be applied during import)
    if mapping_data.as_ik_up:
        track_blob.as_ik_up = mapping_data.as_ik_up
        ik_data = mapping_data.as_ik_up
        Debug.log(f"    Directional vector IK: base='{ik_data.bone_base}', axis={ik_data.axis}")
    
    # Store map_r rest pose transformation if present (for LOCAL space tracks - similarity transformation)
    if mapping_data.map_r:
        track_blob.map_r_rest_pose = mapping_data.map_r
        euler = mapping_data.map_r['euler']
        Debug.log(f"    Rest pose (map_r): ({euler[0]}, {euler[1]}, {euler[2]}) {mapping_data.map_r['order']}")
    
    # Store space_r indicator if present (for WORLD space tracks - simple multiplication)
    if mapping_data.space_r:
        track_blob.space_r = mapping_data.space_r
        Debug.log(f"    Track space: {mapping_data.space_r['space']}")

def apply_track_transformations(all_gani_tracks: List[List[TrackUnitWrapper]], track_mapping: Optional[Dict[str, BoneParameters]] = None) -> None:
    """Apply rig-based naming and track mapping transformations to all tracks.
    
    First applies rig unit type based naming (e.g., appending segment indices for ARM/LIST types).
    Then applies user-defined track mapping transformations if provided.
    
    For ARM, TWO_BONE and LIST rig types, appends segment index to each
    keyframes track name to differentiate multiple segments.
    
    Args:
        all_gani_tracks: List of lists of GaniTrack objects
        track_mapping: Optional dictionary mapping source track name to BoneParameters
    """
    Debug.log("Applying rig unit type based naming...")
    for gani_tracks in all_gani_tracks:
        for gani_track in gani_tracks:
            if gani_track.rig_unit_type:
                rig_type: RigUnitType = gani_track.rig_unit_type
                
                # All multi-segment tracks get segment index suffix
                if rig_type in [RigUnitType.ARM, RigUnitType.TWO_BONE, RigUnitType.MULTI_LOCAL_ORIENTATION]:
                    for segment_index, track_blob in enumerate(gani_track.segments_track_data):
                        original_name: str = track_blob.name
                        modified_name: str = f"{original_name}_{segment_index}"
                        track_blob.name = modified_name
                        Debug.log(f"  '{original_name}' -> '{modified_name}' (RigUnitType.{rig_type.name})")
    
    # Apply track mapping transformations if provided
    if track_mapping:
        Debug.log("Applying track mapping transformations...")
        for gani_tracks in all_gani_tracks:
            for gani_track in gani_tracks:
                for track_blob in gani_track.segments_track_data:
                    if track_blob.name in track_mapping:
                        old_name: str = track_blob.name
                        mapping_data: BoneParameters = track_mapping[old_name]
                        apply_track_mapping_transformation(track_blob, mapping_data, old_name)


def extract_rest_pose_from_custom_rig(all_gani_tracks: List[List[TrackUnitWrapper]], custom_rig: Optional[bpy.types.Object]) -> None:
    """Extract rest pose rotations from custom rig and merge with existing transformations.
    
    For each rotation track, extracts the bone's rest pose from the custom rig and:
    - For LOCAL space tracks: Merges with existing map_r_rest_pose (or creates if missing)
    - For WORLD space tracks: Adds to rotation_offset list
    
    This allows combining mapping file transformations with custom rig rest pose.
    
    Args:
        all_gani_tracks: All imported track wrappers
        custom_rig: Optional target armature to extract rest pose from
    """
    if not custom_rig or custom_rig.type != 'ARMATURE':
        return
    
    Debug.log("\n=== Extracting Rest Pose from custom rig ===")
    rest_pose_count = 0
    
    for gani_tracks in all_gani_tracks:
        for track_unit in gani_tracks:
            for track_blob in track_unit.segments_track_data:
                # Only apply to rotation segments
                if track_blob.data_blob.type not in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
                    continue
                
                # Skip as_ik_up bones - they should not be affected by rest pose corrections
                if track_blob.as_ik_up:
                    continue
                
                # Check if bone exists in custom rig
                if track_blob.name not in custom_rig.data.bones:
                    continue
                
                # Extract rest pose rotation from custom rig
                bone = custom_rig.data.bones[track_blob.name]
                euler = bone.matrix_local.to_euler('XYZ')
                euler_deg = [math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z)]
                
                rest_pose_dict = {
                    'euler': euler_deg,
                    'order': 'XYZ'
                }
                
                # Determine how to apply based on track space type
                if track_blob.space_r:
                    # WORLD space track - add to rotation_offset list
                    if track_blob.rotation_offset is None:
                        track_blob.rotation_offset = []
                    track_blob.rotation_offset.append(rest_pose_dict)
                    Debug.log(f"  {track_blob.name} [WORLD]: Added rest pose to offset_r: ({euler_deg[0]:.1f}, {euler_deg[1]:.1f}, {euler_deg[2]:.1f})")
                else:
                    # LOCAL space track - merge with existing map_r_rest_pose or set if missing
                    if track_blob.map_r_rest_pose is None:
                        track_blob.map_r_rest_pose = rest_pose_dict
                        Debug.log(f"  {track_blob.name} [LS]: Set rest pose from rig: ({euler_deg[0]:.1f}, {euler_deg[1]:.1f}, {euler_deg[2]:.1f})")
                    else:
                        # Already has map_r from mapping file - combine them
                        # For now, use custom rig (this could be additive in future)
                        existing_euler = track_blob.map_r_rest_pose['euler']
                        Debug.log(f"  {track_blob.name} [LS]: Mapping file has map_r=({existing_euler[0]:.1f}, {existing_euler[1]:.1f}, {existing_euler[2]:.1f}), using custom rig instead")
                        track_blob.map_r_rest_pose = rest_pose_dict
                
                rest_pose_count += 1
    
    Debug.log(f"Extracted rest pose for {rest_pose_count} track(s) from custom rig")


# Animation #############################################################

def import_keyframes_track(context: bpy.types.Context, action: bpy.types.Action, keyframes_track: TrackDataBlobWrapper) -> int:
    """Import a single track data blob into a Blender action.
    
    Args:
        context: Blender context (used to access import properties like ik_up_distance)
        action: Blender action to add keyframes to
        keyframes_track: TrackDataBlobWrapper object containing animation data
        
    Returns:
        Maximum frame number encountered in this track
    """
    max_frame: int = 0
    
    Debug.log(f"  - Import Track '{keyframes_track.name}' ({keyframes_track.data_blob.type.name}): {len(keyframes_track.data_blob.keyframes)} keyframe(s)")

    # Determine preferred interpolation from import properties (fall back to BEZIER)
    interpolation_mode: str = 'BEZIER'
    try:
        props = getattr(context.scene, 'mtar_properties', None)
        if props is not None and getattr(props, 'import_props', None) is not None:
            interpolation_mode = getattr(props.import_props, 'interpolation_mode', interpolation_mode)
    except Exception:
        pass
    
    # Get or create FCurve group for this handle (Blender <5.0)
    # Ensure group_name is always a string (keyframes_track.name can be an integer hash)
    group_name: str = str(keyframes_track.name)
    # NOTE: Group creation via ensure_action_group removed. We rely on creation-time grouping
    # when creating FCurves by passing `action_group_name=group_name` to `ensure_action_fcurve`.
    
    # Prepare rotation transformations (only applies to rotation tracks)
    rotation_offset_quats: List[Quaternion] = []
    rotation_axis_map: Optional[List[Dict[str, Union[str, bool]]]] = None
    
    if keyframes_track.data_blob.type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
        # Prepare rotation offset quaternions
        if keyframes_track.rotation_offset:
            rotation_offset_quats = prepare_rotation_offset_quats(keyframes_track.rotation_offset)
        
        # Prepare rotation axis mapping
        if keyframes_track.rotation_axis_map:
            rotation_axis_map = keyframes_track.rotation_axis_map
            axis_str = ','.join([('-' if m['negate'] else '') + m['axis'] for m in rotation_axis_map])
            Debug.log(f"    Applying rotation axis mapping transformation: {axis_str}")    # Check if this is a directional vector track (quaternion converted to location)
    
        # IK special case
        if keyframes_track.as_ik_up:
            # Convert quaternion rotation data to location data using directional vector
            # Apply all rotation transformations BEFORE converting to location
            ik_data = keyframes_track.as_ik_up
            axis = ik_data.axis
            
            # Get distance from import properties (shared setting)
            distance: float = 1.0  # Default value
            if hasattr(context.scene, 'mtar_properties'):
                distance = context.scene.mtar_properties.import_props.ik_up_distance
            
            Debug.log(f"    Converting rotation to directional location (axis={axis}, distance={distance})")
            
            # Pre-convert all quaternions and calculate directional locations
            converted_locations = []
            for keyframe in keyframes_track.data_blob.keyframes:
                # Apply all rotation transformations (offset first for as_ik_up)
                quat = apply_rotation_transforms(
                    keyframe.data.value,  # Fox quaternion [x, y, z, w]
                    rotation_axis_map,
                    rotation_offset_quats,
                    offset_first=True  # For as_ik_up: offset @ quat
                )
                
                # Apply rest pose correction based on track space type (if applicable)
                if keyframes_track.space_r:
                    # World space - offset_r already applied above
                    pass
                elif keyframes_track.map_r_rest_pose:
                    # Local space - apply similarity transformation
                    quat = apply_rest_pose_correction_local(quat, keyframes_track.map_r_rest_pose)
                
                # Convert to directional location
                # Base location is 0,0,0 during import (will be offset by constraints later)
                bone_base_location = Vector((0.0, 0.0, 0.0))
                target_location = calculate_directional_location(
                    bone_location=bone_base_location,
                    bone_rotation_quat=quat,
                    axis=axis,
                    distance=distance
                )
                
                converted_locations.append((keyframe.frame_count, target_location))
                max_frame = max(max_frame, keyframe.frame_count)
            
            # Create location curves
            for i in range(3):  # XYZ location
                try:
                    fcurve: bpy.types.FCurve = ensure_action_fcurve(
                        action,
                        data_path=f'pose.bones["{keyframes_track.name}"].location',
                        index=i,
                        action_group_name=group_name,
                        slot_name='mtar_import_armature'
                    )
                except Exception as e:
                    data_path_str = f'pose.bones["{keyframes_track.name}"].location'
                    Debug.log_warning(f"Could not create fcurve '{data_path_str}[{i}]' on action '{getattr(action, 'name', '<unknown>')}': {e}")
                    continue

                # Add keyframes from pre-converted locations
                for frame_count, target_location in converted_locations:
                    kf_point: bpy.types.Keyframe = fcurve.keyframe_points.insert(frame_count, target_location[i])
                    kf_point.interpolation = interpolation_mode
            
            Debug.log(f"    Added directional location keyframes (frames 0-{max_frame})")
        
        # Normal rotation
        else:
            # Pre-convert all quaternions to avoid recalculation for each component
            converted_quaternions = []
            for keyframe in keyframes_track.data_blob.keyframes:
                # Apply all rotation transformations (offset last for regular rotation)
                quat = apply_rotation_transforms(
                    keyframe.data.value,  # Fox quaternion [x, y, z, w]
                    rotation_axis_map,
                    rotation_offset_quats,
                    offset_first=False  # For regular rotation: quat @ offset
                )
                
                # Apply rest pose correction based on track space type
                # World space tracks (space_r=world): use offset_r with simple multiplication
                # Local space tracks (default): use map_r with similarity transformation
                if keyframes_track.space_r:
                    # World space track - use offset_r if present
                    if keyframes_track.rotation_offset:
                        # offset_r is already applied via rotation_offset_quats above
                        # This is the correct behavior for world space
                        pass
                    Debug.log(f"    Applied world space transformation (space_r)")
                    
                elif keyframes_track.map_r_rest_pose:
                    # Local space track - apply similarity transformation
                    quat = apply_rest_pose_correction_local(quat, keyframes_track.map_r_rest_pose)
                    euler = keyframes_track.map_r_rest_pose['euler']
                    Debug.log(f"    Applied local space rest pose correction: ({euler[0]}, {euler[1]}, {euler[2]})")
                
                converted_quaternions.append((keyframe.frame_count, quat))
                max_frame = max(max_frame, keyframe.frame_count)
            
            # Create quaternion rotation curves (WXYZ)
            for i in range(4):  # WXYZ quaternion components
                try:
                    fcurve: bpy.types.FCurve = ensure_action_fcurve(
                        action,
                        data_path=f'pose.bones["{keyframes_track.name}"].rotation_quaternion',
                        index=i,
                        action_group_name=group_name,
                        slot_name='mtar_import_armature'
                    )
                except Exception as e:
                    data_path_str = f'pose.bones["{keyframes_track.name}"].rotation_quaternion'
                    Debug.log_warning(f"Could not create fcurve '{data_path_str}[{i}]' on action '{getattr(action, 'name', '<unknown>')}': {e}")
                    continue

                # Add keyframes from pre-converted quaternions
                for frame_count, quat in converted_quaternions:
                    quat_component: float = quat[i]  # Quaternion indexing: 0=w, 1=x, 2=y, 3=z
                    kf_point: bpy.types.Keyframe = fcurve.keyframe_points.insert(frame_count, quat_component)
                    kf_point.interpolation = interpolation_mode
            
            Debug.log(f"    Added quaternion rotation keyframes (frames 0-{max_frame})")

    elif keyframes_track.data_blob.type in [SegmentType.VECTOR3, SegmentType.VECTOR_DIFF]:
        # Create location curves
        for i in range(3):  # XYZ location
            try:
                fcurve: bpy.types.FCurve = ensure_action_fcurve(
                    action,
                    data_path=f'pose.bones["{keyframes_track.name}"].location',
                    index=i,
                    action_group_name=group_name
                )
            except Exception as e:
                data_path_str = f'pose.bones["{keyframes_track.name}"].location'
                Debug.log_warning(f"Could not create fcurve '{data_path_str}[{i}]' on action '{getattr(action, 'name', '<unknown>')}': {e}")
                continue

            for keyframe in keyframes_track.data_blob.keyframes:
                # Convert from Fox Engine coordinate system to Blender
                blender_vec: List[float] = fox_to_blender_vector(keyframe.data.value)

                # Add keyframe
                kf_point: bpy.types.Keyframe = fcurve.keyframe_points.insert(keyframe.frame_count, blender_vec[i])
                kf_point.interpolation = interpolation_mode
                
                max_frame = max(max_frame, keyframe.frame_count)
        
        Debug.log(f"    Added location keyframes (frames 0-{max_frame})")
    
    return max_frame

def import_gani_track(context: bpy.types.Context, action: bpy.types.Action, gani_track: TrackUnitWrapper) -> int:
    """Import a GaniTrack (containing multiple segments) into a Blender action.
    
    Args:
        context: Blender context (passed to import_keyframes_track)
        action: Blender action to add keyframes to
        gani_track: GaniTrack object containing multiple keyframes tracks (segments)
        
    Returns:
        Maximum frame number encountered in this GaniTrack
    """
    max_frame: int = 0
    
    Debug.log(f"  - Import GaniTrack '{gani_track.name}' (RigUnitType: {gani_track.rig_unit_type.name if gani_track.rig_unit_type else 'None'}) Segments: {len(gani_track.segments_track_data)}")
    
    # Process each keyframes track (segment) in the GaniTrack
    for keyframes_track in gani_track.segments_track_data:
        track_max_frame: int = import_keyframes_track(context, action, keyframes_track)
        max_frame = max(max_frame, track_max_frame)
    
    return max_frame

def create_animation_actions(
    context: bpy.types.Context,
    mtar_file_name: str,
    all_gani_tracks: List[List[TrackUnitWrapper]],
    all_track_mini_headers: List[TrackMiniHeader],
    all_file_headers: List[MtarTableList2],
    layout_track: Optional['Tracks'],
    all_motion_events: List[Optional[EvpHeader]]
) -> Tuple[Optional[bpy.types.Action], List[bpy.types.Action], int]:
    """Create Blender animation actions from MTAR data.
    
    This function creates all animation actions (layout track and GANI actions)
    without requiring an armature. The actions can later be linked to armatures
    through NLA tracks and strips.
    
    Args:
        mtar_file_name: Base name for actions
        all_gani_tracks: List of GaniTrack lists (one per GANI file)
        all_track_mini_headers: Track mini headers for metadata
        all_file_headers: File headers for path hashes
        layout_track: Optional layout track for metadata
        all_motion_events: All motion event headers
        context: Blender context (passed to import functions for settings access)
        
    Returns:
        Tuple of (layout_action, gani_actions_list, max_frame_end)
        Note: max_frame_end is the sum of action frame counts without padding.
        Padding is applied separately when creating NLA strips.
    """
    # Debug: Log list lengths to diagnose IndexError
    Debug.log(f"create_animation_actions received lists with lengths:")
    Debug.log(f"  all_gani_tracks: {len(all_gani_tracks)}")
    Debug.log(f"  all_track_mini_headers: {len(all_track_mini_headers)}")
    Debug.log(f"  all_file_headers: {len(all_file_headers)}")
    Debug.log(f"  all_motion_events: {len(all_motion_events)}")
    
    # Create layout track action to store metadata
    layout_action: Optional[bpy.types.Action] = None
    if layout_track and layout_track.track_units:
        Debug.log("Creating layout track action for metadata storage...")
        layout_action_name = f"{mtar_file_name}_LayoutTrack"
        layout_action = bpy.data.actions.new(name=layout_action_name)
        layout_action.use_fake_user = True
        
        # Convert layout track to TrackMetaData and store metadata
        # Pass first gani_tracks (if available) to preserve rig_unit_type from FRIG
        first_gani_tracks = all_gani_tracks[0] if all_gani_tracks and len(all_gani_tracks) > 0 else None
        track_metadata_list = TrackMetaData.from_layout_track_units(layout_track.track_units, gani_tracks=first_gani_tracks)
        store_track_metadata_on_action(layout_action, track_metadata_list)
        
        # Store header properties separately
        if layout_track.header:
            store_track_header_properties_on_action(layout_action, layout_track.header)
        
        # Add dummy keyframes at frames -100 and -50
        add_dummy_keyframes_to_action(layout_action)
        
        Debug.log(f"Created layout track action: {layout_action_name}")

    # Process each GANI file individually to create actions
    gani_actions: List[bpy.types.Action] = []
    current_frame_offset: int = 0
    max_frame_end: int = 0

    Debug.log(f"\nProcessing {len(all_gani_tracks)} GANI file(s)...")
    for gani_index, gani_tracks in enumerate(all_gani_tracks):
        Debug.log(f"\n--- GANI {gani_index + 1}/{len(all_gani_tracks)} ---")

        # -----------------------------------------------------
        # Update UI progress for per-GANI processing (keeps overall 'Creating Actions...' stage)
        try:
            total_ganis = len(all_gani_tracks) if len(all_gani_tracks) > 0 else 1
            # Map per-GANI progress into a small slice (30 -> 49)
            progress = 30 + min(19, int(((gani_index + 1) / total_ganis) * 20))
            # Prefer a human-readable display name from file header path hash if available
            if gani_index < len(all_file_headers) and hasattr(all_file_headers[gani_index], 'path'):
                display_name = f"0x{int(all_file_headers[gani_index].path):016X}"
            else:
                display_name = f"Gani_{gani_index+1:03d}"
            Debug.update_progress(progress, f"GANI {gani_index + 1}/{total_ganis}: {display_name}")
        except Exception:
            # Best-effort progress update; do not interrupt import on failure
            pass
        # -----------------------------------------------------

        # Create one action per GANI file
        action_name: str = f"{mtar_file_name}_Gani_{gani_index:03d}"
        action: bpy.types.Action = bpy.data.actions.new(name=action_name)
        gani_actions.append(action)
        Debug.log(f"Created action: {action_name}")
        
        # =============================

        # Store metadata from the actual animation data (GaniTracks) on this action
        # Convert to TrackMetaData and store
        track_mini_header = all_track_mini_headers[gani_index]
        track_metadata_list = TrackMetaData.from_gani_tracks(gani_tracks, track_mini_header.segment_headers)
        store_track_metadata_on_action(action, track_metadata_list, include_segments=False, include_hash=False)
        
        # Store the path hash from the file header for re-export
        file_header = all_file_headers[gani_index]
        if hasattr(file_header, 'path'):
            # Store as string because PathCode64 is too large for Blender's int type
            action["gani_path_hash"] = str(file_header.path)
            action.id_properties_ui("gani_path_hash").update(
                description="PathCode64 hash from MTAR file header (stored as string)"
            )
            Debug.log(f"  Stored path hash: 0x{file_header.path:016X}")

        # Store motion events if present
        if gani_index < len(all_motion_events):
            motion_events = all_motion_events[gani_index]
            if motion_events:
                store_motion_events_on_action(action, motion_events)

        # =============================

        # Get frame count from TrackMiniHeader (imported from MTAR file)
        track_mini_header = all_track_mini_headers[gani_index]
        gani_frame_count: int = track_mini_header.frame_count

        # Process each GaniTrack in this GANI file
        Debug.log(f"Processing {len(gani_tracks)} GaniTrack(s)...")
        for gani_track in gani_tracks:
            import_gani_track(context, action, gani_track)

        Debug.log(f"Track frame range: 0 - {gani_frame_count}")
        
        # Configure action with frame range from MTAR file header
        configure_action(action, frame_start=0, frame_end=gani_frame_count)
        Debug.log(f"  Configured action frame range: 0 - {gani_frame_count}")
        
        # # Yield briefly to let Blender process events (prevents UI lockups on long imports)
        # try:
        #     time.sleep(0.001)
        # except Exception:
        #     pass

        # Update offset for next strip (used for calculating total frame range)
        current_frame_offset += gani_frame_count
        max_frame_end = current_frame_offset

    return layout_action, gani_actions, max_frame_end

def create_motion_points_animation_actions(
    context: bpy.types.Context,
    mtar_file_name: str,
    all_motion_point_gani_tracks: List[List[TrackUnitWrapper]],
    all_motion_point_layouts: List[Optional[Tracks]],
    all_motion_point_track_headers: List[Optional[TrackHeader]]
) -> List[Optional[bpy.types.Action]]:
    """Create Blender animation actions for motion points from MTAR data.
    
    This function creates animation actions for motion points without requiring
    an armature. The actions can later be linked to motion points armatures
    through NLA tracks and strips.
    
    Args:
        mtar_file_name: Base name for actions
        all_motion_point_gani_tracks: Motion point animation tracks
        all_motion_point_layouts: Tracks objects for motion point metadata (like layout track)
        all_file_headers: File headers for path hashes
        all_motion_point_track_headers: Motion point track headers
        context: Blender context (passed to import functions for settings access)
        
    Returns:
        List of motion point animation actions (may contain None for GANIs without motion points)
    """
    motion_point_actions: List[Optional[bpy.types.Action]] = []
    
    Debug.log(f"\nProcessing {len(all_motion_point_gani_tracks)} GANI file(s) for motion points...")
    for gani_index, motion_point_tracks in enumerate(all_motion_point_gani_tracks):
        if not motion_point_tracks:
            Debug.log(f"  GANI {gani_index + 1}: No motion point tracks")
            # Add None placeholder to maintain index alignment with animation actions
            motion_point_actions.append(None)
            continue
            
        Debug.log(f"\n  --- Motion Points GANI {gani_index + 1}/{len(all_motion_point_gani_tracks)} ---")
        # -----------------------------------------------------
        # Update UI progress for per-motion-point-GANI processing (keeps overall 'Creating Motion Points...' stage)
        try:
            total_mp = len(all_motion_point_gani_tracks) if len(all_motion_point_gani_tracks) > 0 else 1
            progress = 60 + min(4, int(((gani_index + 1) / total_mp) * 5))
            display_name = f"MotionPoints_Gani_{gani_index+1:03d}"
            Debug.update_progress(progress, f"MotionPoints GANI {gani_index + 1}/{total_mp}: {display_name}")
        except Exception:
            pass
        # -----------------------------------------------------

        # Create action for this GANI file's motion point animation
        action_name: str = f"{mtar_file_name}_MotionPoints_Gani_{gani_index:03d}"
        action: bpy.types.Action = bpy.data.actions.new(name=action_name)
        motion_point_actions.append(action)
        Debug.log(f"  Created action: {action_name}")
        
        # =============================

        # Store metadata for motion point tracks
        # Motion points use Tracks structure (like layout track)
        motion_point_layout = all_motion_point_layouts[gani_index]
        if motion_point_layout is not None:
            track_metadata_list = TrackMetaData.from_layout_track_units(motion_point_layout.track_units, track_name_prefix="MotionPoint")
            store_track_metadata_on_action(action, track_metadata_list, include_segments=False, include_hash=False)
        
        # Store TrackHeader fields (t_id, unknown_a, unknown_b) if available
        motion_point_track_header: TrackHeader = all_motion_point_track_headers[gani_index]
        if motion_point_track_header is not None:
            store_track_header_properties_on_action(action, motion_point_track_header)
        
        # =============================

        # Get frame count from TrackHeader (imported from MTAR file)
        gani_frame_count: int = motion_point_track_header.frame_count if motion_point_track_header is not None else 0

        Debug.log(f"  Processing {len(motion_point_tracks)} motion point track(s)...")
        for gani_track in motion_point_tracks:
            track_max_frame: int = import_gani_track(context, action, gani_track)
            if motion_point_track_header is None:
                gani_frame_count = max(gani_frame_count, track_max_frame)
        
        Debug.log(f"  Motion point frame range: 0 - {gani_frame_count}")
        
        # Configure action with frame range from MTAR file header
        configure_action(action, frame_start=0, frame_end=gani_frame_count)
        Debug.log(f"  Configured motion point action frame range: 0 - {gani_frame_count}")
        # # Yield briefly to let Blender process events (prevents UI lockups on long imports)
        # try:
        #     time.sleep(0.001)
        # except Exception:
        #     pass
    
    return motion_point_actions


# Armature #############################################################

def get_action_length(action: bpy.types.Action) -> int:
    """Get the frame end value from an action.
    
    First tries to use the action's manual frame_end if set.
    Falls back to calculating from keyframes if manual frame range not set.
    
    Args:
        action: Blender action to get frame end from
        
    Returns:
        Frame end value, or 0 if no keyframes
    """
    if action.use_frame_range:
        return int(action.frame_end)
    
    # Fallback: calculate from keyframes
    action_frame_end: int = 0
    for fcurve in iter_action_fcurves(action):
        for keyframe in fcurve.keyframe_points:
            action_frame_end = max(action_frame_end, int(keyframe.co.x))
    
    return action_frame_end

def create_nla_strips_for_actions(
    nla_track: bpy.types.NlaTrack,
    actions: List[Optional[bpy.types.Action]],
    mtar_file_name: str,
    strip_name_prefix: str,
    strip_suffix: str = "",
    strip_padding: int = 10,
    reference_actions: Optional[List[bpy.types.Action]] = None
) -> int:
    """Create NLA strips for a list of actions on an NLA track.
    
    This utility function reduces code duplication by handling the common pattern
    of creating NLA strips from actions with consistent naming and padding.
    
    Args:
        nla_track: NLA track to add strips to
        actions: List of actions to create strips from (may contain None for empty GANIs)
        mtar_file_name: Base name for strip naming
        strip_name_prefix: Prefix for strip names (e.g., "Strip", "MotionPoints_Strip")
        strip_suffix: Optional suffix to append to strip names (default: "")
        strip_padding: Frames to add between strips (default: 10)
        reference_actions: Optional list of reference actions to determine frame offsets.
                          If provided, offsets are calculated based on reference action lengths
                          to maintain synchronization even when current actions are empty.
                          Used to sync motion point strips with animation strips.
        
    Returns:
        Total offset reached after all strips (for chaining operations)
    """
    current_frame_offset: int = 0
    
    for index, action in enumerate(actions):
        # Skip None actions (GANIs without data)
        if action is None:
            Debug.log(f"  Skipped GANI {index} (no action data)")
            # Still need to advance offset based on reference if available
            if reference_actions and index < len(reference_actions):
                reference_action_length = get_action_length(reference_actions[index])
                if reference_action_length > 0:
                    current_frame_offset += reference_action_length + strip_padding
            continue
        
        action_length = get_action_length(action)
        
        # Determine the reference frame length for offset calculation
        # If reference_actions is provided, use the corresponding reference action's length
        # Otherwise, use the current action's length
        if reference_actions and index < len(reference_actions):
            reference_action_length = get_action_length(reference_actions[index])
        else:
            reference_action_length = action_length
        
        if action_length > 0:
            strip: bpy.types.NlaStrip = nla_track.strips.new(
                name="tmp",
                start=int(current_frame_offset),
                action=action
            )
            strip.name = f"{mtar_file_name}_{strip_name_prefix}_{index:03d}{strip_suffix}"
            # strip.frame_start = int(current_frame_offset)
            strip.frame_end = strip.frame_start + action_length
            strip.action_frame_start = 0
            strip.action_frame_end = action_length
            
            Debug.log(f"  Created NLA strip '{strip.name}' at frame {current_frame_offset} (length: {action_length})")
        else:
            Debug.log(f"  Skipped GANI {index} (no animation data)")
        
        # Update offset for next strip (add padding to prevent overlap)
        # Use reference frame length to maintain synchronization across armatures
        if reference_action_length > 0:
            current_frame_offset += reference_action_length + strip_padding
    
    return current_frame_offset

def setup_rig(imported_armature: bpy.types.Object, custom_rig: bpy.types.Object, track_mapping: Optional[Dict[str, BoneParameters]] = None) -> None:
    """Set up constraints on a Rigify rig to follow the imported animation armature.
    
    This function processes the track mapping data to create constraints on the custom rig
    that connect to bones in the imported armature. The specific constraints and settings
    are defined in the track mapping file.
    
    Supported mapping file parameters:
        space_r=world : Creates a world space Copy Rotation constraint
                     Requires bone with the renamed name to exist in both armatures
                     Uses World Space for Target and Owner
        
        space_r=custom,<custom_bone> : Creates a world-space Copy Rotation constraint with custom owner space
                     Target space is World, Owner space is Custom (using specified bone)
        
        space_l=world : Creates a world space Copy Location constraint
                     Requires bone with the renamed name to exist in both armatures
                     Uses World Space for both Target and Owner
                     X and Y axes are inverted
        
        space_l=custom,<custom_bone> : Creates a world space Copy Location constraint with custom owner space
                     Target space is World, Owner space is Custom (using specified bone)
                     X and Y axes are inverted
                     Example: space_l=custom,torso_root
        
        Multiple source tracks can map to the same target bone (e.g., one with rotation data
        using space_r=world, another with location data using space_l=world). Parameters are merged
        automatically during parsing.
        
        Note: Constraints are created based solely on the mapping parameters. The presence or
        absence of actual animation data on the tracks is not checked.
    
    Future parameters:
        constraint_<constraint_type>=<settings>
        Example: constraint_copy_rotation=influence:1.0,mix_mode:REPLACE
    
    Args:
        imported_armature: The armature created during MTAR import with animation data
        custom_rig: The Rigify rig that should follow the imported animation
        track_mapping: Optional dictionary with constraint configuration from mapping file
    """
    if not custom_rig or not imported_armature:
        return
    
    if custom_rig.type != 'ARMATURE' or imported_armature.type != 'ARMATURE':
        Debug.log_error("  Error: Both objects must be armatures")
        return
    
    Debug.log("\n=== Setting up Rigify constraints ===")
    Debug.log(f"Source armature: {imported_armature.name}")
    Debug.log(f"custom rig: {custom_rig.name}")
    
    # Remove any action currently assigned to the custom rig to ensure constraints and
    # baked animations applied during import do not accidentally modify an existing action.
    try:
        if hasattr(custom_rig, 'animation_data') and custom_rig.animation_data and custom_rig.animation_data.action:
            Debug.log(f"Removing existing action '{custom_rig.animation_data.action.name}' from custom rig '{custom_rig.name}'")
            remove_action_from_datablock(custom_rig)
    except Exception:
        # Best-effort: do not fail the import if we cannot modify the rig's animation_data
        pass

    if not track_mapping:
        Debug.log("No track mapping provided - skipping constraint setup")
        return
    
    # First pass: Set rotation mode to QUATERNION for bones with rotation tracks
    Debug.log("\n--- Setting rotation modes ---")
    rotation_modes_changed = 0
    
    for source_name, mapping_data in track_mapping.items():
        # Get target bone name from mapping
        target_bone_name = mapping_data.track_name if mapping_data.track_name else mapping_data.fox_name
        if not target_bone_name:
            continue
        
        # Check if target bone exists in rig
        if target_bone_name not in custom_rig.pose.bones:
            continue
        
        # Check if this mapping has rotation data (has rotation track in imported animation)
        target_bone = custom_rig.pose.bones[target_bone_name]
        
        # Check for rotation FCurves
        has_rotation = False
        if imported_armature.animation_data and imported_armature.animation_data.action:
            for fcurve in iter_action_fcurves(imported_armature.animation_data.action):
                # Check if this fcurve belongs to this bone and is a rotation curve
                if fcurve.data_path == f'pose.bones["{source_name}"].rotation_quaternion':
                    has_rotation = True
                    break
        
        if has_rotation and target_bone.rotation_mode != 'QUATERNION':
            target_bone.rotation_mode = 'QUATERNION'
            rotation_modes_changed += 1
            Debug.log(f"  Set '{target_bone_name}' to QUATERNION rotation mode")
    
    Debug.log(f"Changed rotation mode for {rotation_modes_changed} bones")
    
    # Second pass: Process track_mapping to create constraints based on parameters
    Debug.log("\n--- Creating constraints ---")
    constraints_added = 0
    
    for source_name, mapping_data in track_mapping.items():
        # Get target bone name from mapping
        target_bone_name = mapping_data.track_name if mapping_data.track_name else mapping_data.fox_name
        if not target_bone_name:
            continue
        
        # Check if target bone exists in rig
        if target_bone_name not in custom_rig.pose.bones:
            Debug.log_warning(f"  Warning: Target bone '{target_bone_name}' not found in custom rig, skipping")
            continue
        
        # Check for space_r and space_l parameters (world space constraints)
        space_r = mapping_data.space_r
        space_l = mapping_data.space_l
        
        # Check if we have space_r or space_l (now they're dicts with 'space' and optional 'custom_bone')
        has_space_r = space_r and isinstance(space_r, dict) and space_r.get('space') in ('WORLD', 'CUSTOM')
        has_space_l = space_l and isinstance(space_l, dict) and space_l.get('space') in ('WORLD', 'CUSTOM')
        
        if has_space_r or has_space_l:
            # World space constraint: check if imported armature has bone with exact renamed name
            if target_bone_name not in imported_armature.pose.bones:
                Debug.log_warning(f"  Warning: World space constraint requires bone '{target_bone_name}' in imported armature, not found")
                continue
            
            # Get target pose bone
            target_pose_bone = custom_rig.pose.bones[target_bone_name]
            
            # Create Copy Rotation constraint if space_r is set
            if has_space_r:
                space_type = space_r.get('space')
                custom_bone = space_r.get('custom_bone') if space_type == 'CUSTOM' else None

                if custom_bone:
                    Debug.log(f"  Creating world-space Copy Rotation constraint: {custom_rig.name}['{target_bone_name}'] <- {imported_armature.name}['{target_bone_name}'] (owner custom bone: '{custom_bone}')")
                else:
                    Debug.log(f"  Creating world-space Copy Rotation constraint: {custom_rig.name}['{target_bone_name}'] <- {imported_armature.name}['{target_bone_name}']")

                constraint = target_pose_bone.constraints.new('COPY_ROTATION')
                constraint.name = f"MTAR_WS_Rot_{target_bone_name}"
                constraint.target = imported_armature
                constraint.subtarget = target_bone_name

                # Set target space to World
                constraint.target_space = 'WORLD'

                # Set owner space - custom only if provided
                if custom_bone:
                    if custom_bone not in custom_rig.pose.bones:
                        Debug.log_warning(f"    Warning: Custom owner bone '{custom_bone}' not found in custom rig, using world owner space")
                        constraint.owner_space = 'WORLD'
                    else:
                        constraint.owner_space = 'CUSTOM'
                        constraint.space_object = custom_rig
                        constraint.space_subtarget = custom_bone
                else:
                    constraint.owner_space = 'WORLD'

                # Set Mix to Replace
                constraint.mix_mode = 'REPLACE'

                # Rest use defaults (influence=1.0, all axes enabled, etc.)

                constraints_added += 1
            
            # Create Copy Location constraint if space_l is set
            if has_space_l:
                space_type_l = space_l.get('space')
                custom_bone = space_l.get('custom_bone') if space_type_l == 'CUSTOM' else None

                if custom_bone:
                    Debug.log(f"  Creating world-space Copy Location constraint: {custom_rig.name}['{target_bone_name}'] <- {imported_armature.name}['{target_bone_name}'] (owner custom bone: '{custom_bone}')")
                else:
                    Debug.log(f"  Creating world-space Copy Location constraint: {custom_rig.name}['{target_bone_name}'] <- {imported_armature.name}['{target_bone_name}']")

                constraint = target_pose_bone.constraints.new('COPY_LOCATION')
                constraint.name = f"MTAR_WS_Loc_{target_bone_name}"
                constraint.target = imported_armature
                constraint.subtarget = target_bone_name

                # Set target space to World
                constraint.target_space = 'WORLD'

                # Set owner space - custom only if provided
                if custom_bone:
                    if custom_bone not in custom_rig.pose.bones:
                        Debug.log_warning(f"    Warning: Custom owner bone '{custom_bone}' not found in custom rig, using world owner space")
                        constraint.owner_space = 'WORLD'
                    else:
                        constraint.owner_space = 'CUSTOM'
                        constraint.space_object = custom_rig
                        constraint.space_subtarget = custom_bone
                else:
                    constraint.owner_space = 'WORLD'

                # Invert X and Y axes
                if custom_bone:
                    constraint.invert_x = True
                    constraint.invert_y = True

                # Rest use defaults (influence=1.0, Z not inverted, etc.)

                constraints_added += 1
        
        # Check for as_ik_up parameter (directional vector IK)
        as_ik_up = mapping_data.as_ik_up
        if as_ik_up:
            bone_base = as_ik_up.bone_base
            
            # Check if base bone exists in custom rig
            if bone_base not in custom_rig.pose.bones:
                Debug.log_warning(f"  Warning: as_ik_up base bone '{bone_base}' not found in custom rig")
                continue
            
            # Check if target bone exists in imported armature (should have location animation)
            if target_bone_name not in imported_armature.pose.bones:
                Debug.log_warning(f"  Warning: as_ik_up target bone '{target_bone_name}' not found in imported armature")
                continue
            
            # Get target pose bone
            target_pose_bone = custom_rig.pose.bones[target_bone_name]
            
            Debug.log(f"  Creating directional IK constraints for '{target_bone_name}': base='{bone_base}', axis={as_ik_up.axis}")
            
            # Constraint 1: Copy Location (World Space) from base bone
            constraint1 = target_pose_bone.constraints.new('COPY_LOCATION')
            constraint1.name = f"MTAR_IK_Base_{bone_base}"
            constraint1.target = custom_rig
            constraint1.subtarget = bone_base
            constraint1.target_space = 'WORLD'
            constraint1.owner_space = 'WORLD'
            constraints_added += 1
            
            # Constraint 2: Transformation constraint (Add mix) from imported armature
            constraint2 = target_pose_bone.constraints.new('TRANSFORM')
            constraint2.name = f"MTAR_IK_Offset_{target_bone_name}"
            constraint2.target = imported_armature
            constraint2.subtarget = target_bone_name
            
            # Source space - always world
            constraint2.target_space = 'WORLD'
            
            # Owner space - use custom owner only when space_ik indicates CUSTOM
            space_ik = mapping_data.space_ik
            custom_bone = None
            if space_ik and isinstance(space_ik, dict) and space_ik.get('space') == 'CUSTOM':
                custom_bone = space_ik.get('custom_bone')

            if custom_bone:
                if custom_bone not in custom_rig.pose.bones:
                    Debug.log_warning(f"    Warning: Custom owner bone '{custom_bone}' not found in custom rig, using world owner space for transformation")
                    constraint2.owner_space = 'WORLD'
                else:
                    constraint2.owner_space = 'CUSTOM'
                    constraint2.space_object = custom_rig
                    constraint2.space_subtarget = custom_bone
                    Debug.log(f"    Using custom space '{custom_bone}' for IK transformation constraint")
            else:
                constraint2.owner_space = 'WORLD'
            
            # Map from Location to Location (1:1 pass-through with range -100 to 100)
            constraint2.map_from = 'LOCATION'
            constraint2.map_to = 'LOCATION'
            
            # Set source (from) ranges for X, Y, Z
            constraint2.from_min_x = -100.0
            constraint2.from_max_x = 100.0
            constraint2.from_min_y = -100.0
            constraint2.from_max_y = 100.0
            constraint2.from_min_z = -100.0
            constraint2.from_max_z = 100.0
            
            # Set destination (to) ranges for X, Y, Z (same as source for 1:1 mapping)
            constraint2.to_min_x = -100.0
            constraint2.to_max_x = 100.0
            constraint2.to_min_y = -100.0
            constraint2.to_max_y = 100.0
            constraint2.to_min_z = -100.0
            constraint2.to_max_z = 100.0
            
            # Set Mix mode to Add (adds the transformed location to existing location)
            constraint2.mix_mode = 'ADD'
            
            constraints_added += 1
    
    Debug.log(f"Constraints setup complete: {constraints_added} constraint(s) added")

def create_and_setup_armature(
    context: bpy.types.Context,
    mtar_file_name: str,
    all_gani_tracks: List[List[TrackUnitWrapper]],
    gani_actions: List[bpy.types.Action],
    layout_action: Optional[bpy.types.Action],
    custom_rig: Optional[bpy.types.Object],
    strip_suffix: str = "",
    strip_padding: int = 10
) -> bpy.types.Object:
    """Create and set up the imported armature with pre-created animation data.
    
    This function creates the armature and links it to existing animation actions
    through NLA tracks and strips. The animation actions must be created beforehand
    using create_animation_actions().
    
    Args:
        context: Blender context
        mtar_file_name: Base name for the armature and actions
        all_gani_tracks: List of GaniTrack lists (one per GANI file)
        gani_actions: Pre-created list of GANI actions
        layout_action: Pre-created layout track action
        custom_rig: Optional custom rig for NLA tracks
        strip_suffix: Optional suffix for strip names (default: "")
        strip_padding: Frames to add between animation strips (default: 10)
        
    Returns:
        Main armature object
    """
    # Create fresh armature (Blender will auto-rename if name already exists)
    Debug.log(f"Creating new armature: {mtar_file_name}")
    arm_data: bpy.types.Armature = bpy.data.armatures.new(name=mtar_file_name)
    armature: bpy.types.Object = bpy.data.objects.new(mtar_file_name, arm_data)
    context.scene.collection.objects.link(armature)

    # Set armature as active object and enter edit mode
    Debug.log("Setting up armature bones...")
    context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='EDIT')

    # Collect all unique keyframes track names from all GANI files
    all_bone_names: set = set()
    for gani_tracks in all_gani_tracks:
        for gani_track in gani_tracks:
            for keyframes_track in gani_track.segments_track_data:
                all_bone_names.add(keyframes_track.name)
    
    Debug.log(f"Found {len(all_bone_names)} unique handle(s)")

    # Create armature bones if they don't exist
    bones_created: int = 0
    for bone_name in all_bone_names:
        if bone_name not in armature.data.edit_bones:
            bone: bpy.types.EditBone = armature.data.edit_bones.new(bone_name)
            # Set handle defaults (can be adjusted based on needs)
            bone.head = (0, 0, 0)
            bone.tail = (0, 0.1, 0)  # Small default length
            bones_created += 1
    
    if bones_created > 0:
        Debug.log(f"Created {bones_created} new armature bone(s)")

    # Exit edit mode
    bpy.ops.object.mode_set(mode='OBJECT')

    # Create animation data on imported armature
    Debug.log("Setting up animation data on armature...")
    if not armature.animation_data:
        armature.animation_data_create()

    # Create NLA track for organizing strips on imported armature
    nla_track: bpy.types.NlaTrack = armature.animation_data.nla_tracks.new()
    nla_track.name = f"{mtar_file_name}_Animations"
    Debug.log(f"Created NLA track on imported armature: {nla_track.name}")
    
    # Add layout track action as NLA strip at frames -100 to -50
    if layout_action:
        Debug.log("Adding layout track action to NLA...")
        layout_strip: bpy.types.NlaStrip = nla_track.strips.new(
            name="tmp",
            start=-100,
            action=layout_action
        )
        layout_strip.name = f"{mtar_file_name}_LayoutTrack"
        layout_strip.frame_start = -100
        layout_strip.frame_end = -50
        layout_strip.blend_type = 'REPLACE'
        Debug.log("    Layout strip placed at frames -100 to -50")
    
    # Also create NLA track on custom rig if provided
    target_nla_track: Optional[bpy.types.NlaTrack] = None
    if custom_rig:
        Debug.log(f"Setting up animation data on custom rig: {custom_rig.name}")
        if not custom_rig.animation_data:
            custom_rig.animation_data_create()
        target_nla_track = custom_rig.animation_data.nla_tracks.new()
        target_nla_track.name = f"{mtar_file_name}_Animations"
        Debug.log(f"Created NLA track on custom rig: {target_nla_track.name}")
        
        # Add layout track action to custom rig as well
        if layout_action:
            Debug.log("Adding layout track action to custom rig NLA...")
            layout_strip: bpy.types.NlaStrip = target_nla_track.strips.new(
                name="tmp",
                start=-100,
                action=layout_action
            )
            layout_strip.name = f"{mtar_file_name}_LayoutTrack"
            layout_strip.frame_start = -100
            layout_strip.frame_end = -50
            layout_strip.blend_type = 'REPLACE'
            Debug.log("    Layout strip placed at frames -100 to -50 on custom rig")

    # Create NLA strips for animations on imported armature
    final_frame_offset = create_nla_strips_for_actions(
        nla_track,
        gani_actions,
        mtar_file_name,
        "Strip",
        strip_suffix,
        strip_padding
    )
    
    # Create NLA strips on custom rig if provided
    if target_nla_track:
        create_nla_strips_for_actions(
            target_nla_track,
            gani_actions,
            mtar_file_name,
            "Strip",
            strip_suffix,
            strip_padding
        )

    # Update scene frame range to include all strips and their padding
    # Use the final offset from NLA strip creation (includes padding)
    if final_frame_offset > 0:
        context.scene.frame_end = int(final_frame_offset)
        Debug.log(f"\nSet scene frame range: 0 - {final_frame_offset} (includes {len(gani_actions)} strips + padding)")

    return armature

def create_and_setup_motion_points_armature(
    context: bpy.types.Context,
    mtar_file_name: str,
    motion_points: Optional['MotionPointList2'],
    motion_point_actions: List[Optional[bpy.types.Action]],
    strip_suffix: str = "",
    strip_padding: int = 10,
    reference_actions: Optional[List[bpy.types.Action]] = None
) -> Optional[bpy.types.Object]:
    """Create and set up motion points armature with pre-created animation actions.
    
    This function creates the motion points armature and links it to existing animation actions
    through NLA tracks and strips. The animation actions must be created beforehand
    using create_motion_points_animation_actions().
    
    Args:
        context: Blender context
        mtar_file_name: Base name for the armature
        motion_points: Motion points data
        motion_point_actions: Pre-created motion point animation actions
        strip_suffix: Optional suffix for NLA strip names
        strip_padding: Number of frames between NLA strips
        reference_actions: Optional list of reference actions (typically animation actions)
                          to synchronize frame offsets when motion point GANIs are missing

        motion_point_actions: Pre-created motion point animation actions
        
    Returns:
        Motion points armature object, or None if no motion points
    """
    if not motion_points or motion_points.count == 0:
        return None
    
    Debug.log("\nCreating motion points armature...")
    
    # Create armature with '_MotionPoints' suffix
    armature_name = f"{mtar_file_name}_MotionPoints"
    Debug.log(f"  Creating motion points armature: {armature_name}")
    
    arm_data: bpy.types.Armature = bpy.data.armatures.new(name=armature_name)
    armature: bpy.types.Object = bpy.data.objects.new(armature_name, arm_data)
    context.scene.collection.objects.link(armature)
    
    # Set as active and enter edit mode
    context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='EDIT')
    
    # Create a mapping of hash to bone name and parent hash
    motion_point_bones = {}  # hash -> (bone_name, parent_hash)
    
    for entry in motion_points.entries:
        # Try to unhash the motion point name
        entry_hash = entry.name.to_int() if hasattr(entry.name, 'to_int') else int(entry.name)
        bone_name = unhash_rig_type(entry_hash)
        if not bone_name:
            # Use hex hash if unhashing fails
            bone_name = str(entry.name)
        
        motion_point_bones[entry.name] = (bone_name, entry.parent_name)
    
    # Create bones and set up hierarchy
    created_bones = {}  # hash -> EditBone
    
    for point_hash, (bone_name, parent_hash) in motion_point_bones.items():
        # Create bone
        edit_bone: bpy.types.EditBone = armature.data.edit_bones.new(bone_name)
        edit_bone.head = (0, 0, 0)
        edit_bone.tail = (0, 0.1, 0)  # Small default length
        
        created_bones[point_hash] = edit_bone
        Debug.log(f"    Created motion point bone: {bone_name}")
    
    # Set up parent relationships
    # If parent is a motion point in this armature, use it.
    # If parent hash is not in motion points but is valid, create a parent bone from the hash.
    for point_hash, (bone_name, parent_hash) in motion_point_bones.items():
        if parent_hash == 0 or parent_hash == StrCode32(0):
            continue  # No parent
            
        if parent_hash in created_bones:
            # Parent is another motion point
            edit_bone = created_bones[point_hash]
            parent_bone = created_bones[parent_hash]
            edit_bone.parent = parent_bone
            Debug.log(f"    Set parent: {bone_name} -> {parent_bone.name} (motion point)")
        else:
            # Parent is not a motion point - create a parent bone from the hash
            parent_hash_int = parent_hash.to_int() if hasattr(parent_hash, 'to_int') else int(parent_hash)
            parent_bone_name = unhash_rig_type(parent_hash_int)
            if not parent_bone_name:
                # Use hex hash if unhashing fails
                parent_bone_name = str(parent_hash)
            
            # Check if we already created this parent bone
            if parent_hash not in created_bones:
                parent_edit_bone: bpy.types.EditBone = armature.data.edit_bones.new(parent_bone_name)
                parent_edit_bone.head = (0, 0, 0)
                parent_edit_bone.tail = (0, 0.1, 0)
                created_bones[parent_hash] = parent_edit_bone
                Debug.log(f"    Created parent bone from hash: {parent_bone_name} (hash: {parent_hash})")
            
            # Set parent
            edit_bone = created_bones[point_hash]
            parent_bone = created_bones[parent_hash]
            edit_bone.parent = parent_bone
            Debug.log(f"    Set parent: {bone_name} -> {parent_bone.name} (from parent hash)")
    
    # Exit edit mode
    bpy.ops.object.mode_set(mode='OBJECT')
    
    Debug.log(f"Motion points armature created with {len(motion_points.entries)} point(s)")
    
    # Process motion point animations using pre-created actions
    if motion_point_actions:
        Debug.log("\n=== Processing Motion Point Animations ===")
        Debug.log(f"Importing animations to motion points armature: {armature.name}")
        
        # Create animation data if needed
        if not armature.animation_data:
            armature.animation_data_create()
        
        # Create NLA track for motion point animations
        nla_track: bpy.types.NlaTrack = armature.animation_data.nla_tracks.new()
        nla_track.name = f"{mtar_file_name}_MotionPoints_Animations"
        Debug.log(f"Created NLA track: {nla_track.name}")
        
        # Create NLA strips for motion point actions using shared utility
        # Pass reference_actions to synchronize frame offsets with animation strips
        motion_point_final_offset = create_nla_strips_for_actions(
            nla_track,
            motion_point_actions,
            mtar_file_name,
            "MotionPoints_Strip",
            strip_suffix,
            strip_padding,
            reference_actions
        )
        
        # Update scene frame range if motion points extend beyond current end
        if motion_point_final_offset > context.scene.frame_end:
            context.scene.frame_end = int(motion_point_final_offset)
            Debug.log(f"Extended scene frame range to {motion_point_final_offset} for motion points")
        
        Debug.log("Motion point animations import complete")
    
    return armature


# MTAR import #############################################################

def sort_gani_data_by_file_offset(
    all_gani_tracks: List[List[TrackUnitWrapper]],
    all_motion_point_gani_tracks: List[List[TrackUnitWrapper]],
    all_motion_events: List[Optional[EvpHeader]],
    all_track_mini_headers: List[TrackMiniHeader],
    all_motion_point_layouts: List[Optional[Tracks]],
    all_file_headers: List[MtarTableList2],
    all_motion_point_track_headers: List[Optional[TrackHeader]]
) -> Tuple[
    List[List[TrackUnitWrapper]],
    List[List[TrackUnitWrapper]],
    List[Optional[EvpHeader]],
    List[TrackMiniHeader],
    List[Optional[Tracks]],
    List[MtarTableList2],
    List[Optional[TrackHeader]]
]:
    """Sort all GANI data lists by tracks_offset from file headers.
    
    Sorts data in the order GANIs appear in the MTAR file (by file offset).
    This affects action names and NLA strip ordering.
    
    Args:
        All GANI data lists (must have same length)
        
    Returns:
        Same lists sorted by tracks_offset
    """
    # Create list of tuples: (tracks_offset, original_index, all_data)
    combined = []
    for i in range(len(all_file_headers)):
        combined.append((
            all_file_headers[i].tracks_offset,
            i,
            all_gani_tracks[i],
            all_motion_point_gani_tracks[i],
            all_motion_events[i],
            all_track_mini_headers[i],
            all_motion_point_layouts[i],
            all_file_headers[i],
            all_motion_point_track_headers[i]
        ))
    
    # Sort by tracks_offset
    combined.sort(key=lambda x: x[0])
    
    # Unpack sorted data
    sorted_gani_tracks = [item[2] for item in combined]
    sorted_motion_point_gani_tracks = [item[3] for item in combined]
    sorted_motion_events = [item[4] for item in combined]
    sorted_track_mini_headers = [item[5] for item in combined]
    sorted_motion_point_layouts = [item[6] for item in combined]
    sorted_file_headers = [item[7] for item in combined]
    sorted_motion_point_track_headers = [item[8] for item in combined]
    
    return (
        sorted_gani_tracks,
        sorted_motion_point_gani_tracks,
        sorted_motion_events,
        sorted_track_mini_headers,
        sorted_motion_point_layouts,
        sorted_file_headers,
        sorted_motion_point_track_headers
    )

def import_mtar(context: bpy.types.Context, filepath: str, frig: Optional[FrigFile], track_mapping: Optional[Dict[str, BoneParameters]] = None, gani_indices: Optional[List[int]] = None, custom_rig: Optional[bpy.types.Object] = None, strip_padding: int = 10) -> Tuple[Dict[str, str], bpy.types.Object]:
    """Import MTAR animation data and create corresponding objects and animations.
    
    Args:
        context: Blender context
        filepath: Path to the MTAR file
        frig: FrigFile object containing rig data (can be None)
        track_mapping: Optional dictionary mapping source track name to BoneParameters (transformation data)
        gani_indices: List of GANI indices to import (None = import all, [] = import nothing)
        custom_rig: Optional Rigify armature to connect imported animation to
        strip_padding: Number of frames to insert between animation strips (default: 10)
    """
    # Import the mtar data
    result, imported_armature = import_mtar_data(context, filepath, frig, track_mapping, gani_indices, custom_rig, strip_padding)
    
    # Set up rig constraints if custom rig is provided
    if custom_rig and imported_armature:
        setup_rig(imported_armature, custom_rig, track_mapping)
    
    return result, imported_armature

def import_mtar_data(context: bpy.types.Context, filepath: str, frig: Optional[FrigFile], track_mapping: Optional[Dict[str, BoneParameters]] = None, gani_indices: Optional[List[int]] = None, custom_rig: Optional[bpy.types.Object] = None, strip_padding: int = 10) -> Tuple[Dict[str, str], bpy.types.Object]:
    """Import MTAR animation data and create corresponding objects and animations.
    
    Each GANI file in the MTAR becomes one Blender action.
    Each MTAR file entry becomes one animation strip in the NLA (Non-Linear Animation) editor.
    
    If a custom_rig is provided, the NLA tracks and strips are also assigned to the custom rig,
    allowing the animation to drive the rig through constraints set up by setup_rig().
    
    Args:
        context: Blender context
        filepath: Path to the MTAR file
        frig: FrigFile object containing rig data (can be None)
        track_mapping: Optional dictionary mapping source track name to transformation data
        gani_indices: List of GANI indices to import (None = import all, [] = import nothing)
        custom_rig: Optional Rigify armature to receive animation data and constraints
        strip_padding: Number of frames to insert between animation strips (default: 10)
        
    Returns:
        Tuple of (result dict, imported armature object)
    """
    Debug.start_timer("MTAR Import")
    Debug.log("=== MTAR Import Started ===")
    Debug.log(f"File: {filepath}")
    if frig:
        Debug.log(f"Using FRIG data: {frig.header.rig_unit_count} rig units, {frig.bone_list.bone_count} bones")
    
    reader: MtarReader = MtarReader(filepath)

    # Read animation tracks - selective or all
    Debug.log("Reading MTAR file data...")
    Debug.update_progress(10, "Reading MTAR...")
    all_gani_tracks: List[List[TrackUnitWrapper]]
    all_motion_point_gani_tracks: List[List[TrackUnitWrapper]]  # Motion point animation tracks
    all_motion_events: List[Optional['EvpHeader']]  # Motion events for each GANI
    all_track_mini_headers: List[TrackMiniHeader]  # List of TrackMiniHeader objects with segment_headers (main tracks)
    all_motion_point_layouts: List[Optional[Tracks]]  # List of Tracks objects (motion point track structures)
    all_file_headers: List[MtarTableList2]  # List of MtarTable2 objects with path hash
    all_motion_point_track_headers: List[Optional['TrackHeader']]  # List of TrackHeader objects for motion points

    if gani_indices is not None:
        # Selective import - use read_selected_ganis()
        if not gani_indices:
            # Empty list = import nothing
            Debug.log("Empty GANI selection - importing nothing")
            all_gani_tracks = []
            all_motion_point_gani_tracks = []
            all_motion_events = []
            all_track_mini_headers = []
            all_motion_point_layouts = []
            all_file_headers = []
            all_motion_point_track_headers = []
        else:
            # Import selected GANIs
            Debug.log(f"Selective import: GANI indices {gani_indices}")
            results_dict = reader.read_selected_ganis(gani_indices)
            
            # Convert dict to sorted lists
            all_gani_tracks = [results_dict[i][0] for i in sorted(results_dict.keys())]
            all_motion_point_gani_tracks = [results_dict[i][1] for i in sorted(results_dict.keys())]
            all_motion_events = [results_dict[i][2] for i in sorted(results_dict.keys())]
            all_track_mini_headers = [results_dict[i][3] for i in sorted(results_dict.keys())]
            all_motion_point_layouts = [results_dict[i][4] for i in sorted(results_dict.keys())]
            all_file_headers = [results_dict[i][5] for i in sorted(results_dict.keys())]
            all_motion_point_track_headers = [results_dict[i][6] for i in sorted(results_dict.keys())]
            Debug.log(f"Imported {len(all_gani_tracks)} GANI file(s)")
            Debug.log(f"List lengths: gani_tracks={len(all_gani_tracks)}, motion_point_tracks={len(all_motion_point_gani_tracks)}, "
                     f"motion_events={len(all_motion_events)}, track_mini_headers={len(all_track_mini_headers)}, "
                     f"motion_point_layouts={len(all_motion_point_layouts)}, file_headers={len(all_file_headers)}, "
                     f"motion_point_track_headers={len(all_motion_point_track_headers)}")
    else:
        # Import all GANIs
        Debug.log("Importing all GANIs")
        all_gani_tracks, all_motion_point_gani_tracks, all_motion_events, all_track_mini_headers, all_motion_point_layouts, all_file_headers, all_motion_point_track_headers = reader.read_all_tracks()
        Debug.log(f"Found {len(all_gani_tracks)} GANI file(s)")

    # ========== SORT BY FILE OFFSET (comment out to disable) ==========
    if all_file_headers:
        all_gani_tracks, all_motion_point_gani_tracks, all_motion_events, all_track_mini_headers, all_motion_point_layouts, all_file_headers, all_motion_point_track_headers = sort_gani_data_by_file_offset(
            all_gani_tracks,
            all_motion_point_gani_tracks,
            all_motion_events,
            all_track_mini_headers,
            all_motion_point_layouts,
            all_file_headers,
            all_motion_point_track_headers
        )
    # ========== END SORT BY FILE OFFSET ==========

    # Get layout track for metadata storage
    layout_track = None
    motion_points = None
    if reader.common_info and reader.common_info.layout_track:
        layout_track = reader.common_info.layout_track
        Debug.log(f"Layout track has {len(layout_track.track_units)} track units")
    
    # Get motion points if present
    if reader.common_info and reader.common_info.motion_points:
        motion_points = reader.common_info.motion_points
        Debug.log(f"Motion points found: {motion_points.count} point(s)")
        for entry in motion_points.entries:
            Debug.log(f"  MotionPoint {entry.name}: name={str(entry.name)}, parent={str(entry.parent_name)}")
    
    # If FRIG data is available, set rig_unit_type for each GaniTrack
    # The index of gani_tracks correlates with the rig unit defs in the FRIG file
    if frig:
        Debug.log("Mapping FRIG rig unit types to GaniTracks...")
        for gani_tracks in all_gani_tracks:
            for gani_track_index, gani_track in enumerate(gani_tracks):
                # Check if we have a corresponding rig unit def
                if gani_track_index < len(frig.rig_def.unit_defs):
                    rig_unit_def = frig.rig_def.unit_defs[gani_track_index]
                    gani_track.rig_unit_type = rig_unit_def.unit_type
                    Debug.log(f"  GaniTrack {gani_track_index} '{gani_track.name}' -> RigUnitType.{gani_track.rig_unit_type.name}")
                else:
                    Debug.log_warning(f"  Warning: No rig unit def for GaniTrack {gani_track_index} '{gani_track.name}'")
    
    # Modify keyframes track names based on rig unit type and apply track mapping transformations
    Debug.update_progress(20, "Applying Mapping...")
    apply_track_transformations(all_gani_tracks, track_mapping)
    
    # Extract rest pose from custom rig if provided (merges with mapping file transformations)
    # Check settings to see if rest pose correction is enabled
    enable_rest_pose = context.scene.mtar_properties.settings_props.enable_rest_pose_correction
    if custom_rig and enable_rest_pose:
        extract_rest_pose_from_custom_rig(all_gani_tracks, custom_rig)
    elif custom_rig and not enable_rest_pose:
        Debug.log("\nRest pose correction disabled in settings - skipping extraction")
    
    # Use the MTAR filename (without extension) as the armature name
    mtar_file_name: str = os.path.splitext(os.path.basename(filepath))[0]
    # If importing from a .mtar file, append .gani to NLA strip names to indicate source
    strip_suffix: str = ".gani" if os.path.splitext(filepath)[1].lower() == ".mtar" else ""
    
    # Create animation actions first (primary task - can work without armature)
    Debug.update_progress(30, "Creating Actions...")
    layout_action, gani_actions, _ = create_animation_actions(
        context,
        mtar_file_name,
        all_gani_tracks,
        all_track_mini_headers,
        all_file_headers,
        layout_track,
        all_motion_events
    )
    
    # Create and setup the armature with animation data (optional secondary task)
    Debug.update_progress(50, "Setting up Armature...")
    armature = create_and_setup_armature(
        context,
        mtar_file_name,
        all_gani_tracks,
        gani_actions,
        layout_action,
        custom_rig,
        strip_suffix,
        strip_padding
    )
    
    # Create motion points animation actions (primary task for motion points)
    Debug.update_progress(60, "Creating Motion Points...")
    motion_point_actions = create_motion_points_animation_actions(
        context,
        mtar_file_name,
        all_motion_point_gani_tracks,
        all_motion_point_layouts,
        all_motion_point_track_headers
    )
    
    # Create and setup motion points armature with animation data (optional secondary task)
    # Pass gani_actions as reference to synchronize frame offsets across armatures
    Debug.update_progress(65, "Setting up Motion Points...")
    _motion_points_armature = create_and_setup_motion_points_armature(
        context,
        mtar_file_name,
        motion_points,
        motion_point_actions,
        strip_suffix,
        strip_padding,
        gani_actions  # Reference actions for frame synchronization
    )
    
    Debug.log("\n=== MTAR Import Completed Successfully ===")
    Debug.update_progress(70, "Import MTAR Data Finished")

    Debug.stop_timer("MTAR Import")
    return {'FINISHED'}, armature
