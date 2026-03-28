"""
MTAR animation exporter for Metal Gear Solid V.

This module handles the export of Blender animation data to MTAR format.
"""

import os
from typing import Optional, Dict, List, Set
from pathlib import Path

import bpy

from ..py_core.core_logging import Debug

from .. import blender_properties

from ..py_utilities import util_transforms, util_blender_animation, util_parsing, util_blender_armature, util_fcurve_processing

from ..py_fox import fox_gani_constants as gani_const
from ..py_fox import fox_mtar_constants as mtar_const
from ..py_fox.fox_gani_types import SegmentType, TrackUnitFlags, TrackHeader, TrackUnit, TrackData, TrackDataBlob
from ..py_fox.fox_hash_types import StrCode32
from ..py_fox.fox_mtar_types import is_new_mtar_format
from ..py_fox import fox_gani_enums

from ..py_foxwrap_utilities import futil_action, futil_filtering, futil_rest_pose_correction
from ..py_foxwrap_utilities.futil_action_types import ExportActionData

from ..py_foxwrap.fwrap_metadata_types import TrackMetaData
from ..py_foxwrap.fwrap_mtar_import_types import GaniImportData
from ..py_foxwrap.fwrap_gani_track_types import TrackUnitWrapper, Tracks, TrackDataBlobWrapper
from ..py_foxwrap.fwrap_mtar_export_types import (
    GaniExportData, 
    GaniExportTracksData, 
    GaniExportMotionPointsData, 
    GaniMotionEventsData,
    Gani1ExportShaderData,
)
from ..py_foxwrap.fwrap_mapping_export_types import TrackSegmentBoneMapping
from ..py_foxwrap.fwrap_mapping_types import BoneParameters
from ..py_foxwrap import fwrap_gani_motionevent, fwrap_gani_motionpoint_export, fwrap_gani_track, fwrap_metadata, fwrap_mapping
from ..py_foxwrap.fwrap_mtar_writer import MtarWriter
from ..py_foxwrap.fwrap_mtar_reader import MtarReader
from ..py_foxwrap import fwrap_mapping_export
from ..py_foxwrap import fwrap_gani1_shader_export

from . import tools_mtar_importer


# Conversion helper ###############################################################

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


# Layout and MetaData helper #############################################################

def build_layout_track_from_metadata(
    track_segment_bone_mapping: TrackSegmentBoneMapping,
    metadata_dict: Dict[str, TrackMetaData],
    layout_action: Optional[bpy.types.Action] = None,
    force_highest_bit_encoding: bool = False
) -> Tracks:
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
                    highest_bits = fox_gani_enums.get_highest_bit_size_for_segment(segment_type)
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


# Animation export helper #############################################################

def export_gani_track_from_action(
    armature: bpy.types.Object,
    action: bpy.types.Action,
    track_idx: int,
    frame_start: int,
    frame_end: int,
    layout_metadata: Optional[TrackMetaData],
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
        layout_metadata: TrackMetaData instance containing track structure metadata for this track
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
    # layout_metadata is passed in directly (TrackMetaData instance for this track)
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
            if not util_blender_animation.bone_has_fcurves_for_segment(
                    segment_bone_name, 
                    segment_type, 
                    segment_fox_mapping_params and segment_fox_mapping_params.as_ik_up, 
                    fcurve_cache
                    ):
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
            keyframes = fwrap_gani_track.export_keyframes_track(
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
            component_bit_size = fox_gani_enums.get_default_bit_size_for_segment(segment_type)
            if merged_metadata.component_bit_sizes and segment_idx < len(merged_metadata.component_bit_sizes):
                component_bit_size = merged_metadata.component_bit_sizes[segment_idx]

            # Optionally force highest bit encoding based on export setting
            if force_highest_bit_encoding:
                highest_bits = fox_gani_enums.get_highest_bit_size_for_segment(segment_type)
                if highest_bits > 0:
                    component_bit_size = max(component_bit_size, highest_bits)

            # Final validation: ensure bit size is valid for this segment type
            component_bit_size = fox_gani_enums.clamp_bit_size_for_segment(segment_type, component_bit_size)

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
                highest_bits = fox_gani_enums.get_highest_bit_size_for_segment(segment_type)
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
        
        # All directly track related bones
        if bone_name:
            bones.add(bone_name)

        # + Space bones
        # (custom coordinate spaces used by mapping params)
        for space_attr in (bone_params.space_r, bone_params.space_l, bone_params.space_ik):
            if space_attr:
                space_bone = fwrap_metadata.extract_space_bone_name(space_attr)
                if space_bone:
                    bones.add(space_bone)

        # + as_ik_up
        # It uses a separate base bone whose transform is required
        if bone_params.as_ik_up and bone_params.as_ik_up.bone_base:
            bones.add(bone_params.as_ik_up.bone_base)

    # - The special [armature] target
    # It is not a real pose bone and must not be used
    # as a cache key (it's handled via object-level transforms instead).
    bones.discard(fwrap_mapping.ARMATURE_TARGET_NAME)

    return bones


def export_gani_tracks_from_action(
    armature: bpy.types.Object,
    action_data: ExportActionData,
    track_segment_bone_mapping: Optional[TrackSegmentBoneMapping],
    layout_metadata_dict: Dict[str, TrackMetaData],
    force_highest_bit_encoding: bool = False,
    discard_empty_tracks: bool = False
) -> List['TrackUnitWrapper']:
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
        fcurve_cache = util_blender_animation.build_fcurve_cache(processed_action) if processed_action else None
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
        nla_track, nla_strip = util_blender_animation.find_nla_strip_and_track_for_action(armature, action)

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
            track_segment_bone_mapping, synthetic_metadata = fwrap_mapping_export.create_synthetic_mapping(
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
        for track_idx, (_base_name, segments) in enumerate(fwrap_mapping_export.group_bones_by_segment(bone_names)):
            for seg_idx, seg_bone_name in segments:
                track_segment_bone_mapping.set_segment_mapping(
                    track_idx, seg_idx, seg_bone_name, BoneParameters(fox_name=seg_bone_name)
                )
                Debug.log(f"  Track {track_idx} Segment {seg_idx}: {seg_bone_name}")

    # Extract rest pose from armature (merges with mapping file transformations)
    # Check settings to see if rest pose correction is enabled
    enable_rest_pose = context.scene.mtar_properties.settings_props.enable_rest_pose_correction
    if enable_rest_pose:
        futil_rest_pose_correction.extract_rest_pose_correction_mapping_from_armature(track_segment_bone_mapping, armature)
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
    actions_to_export = futil_action.collect_actions_for_export_from_armature(
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
        metadata_dict = fwrap_metadata.merge_metadata_from_actions(all_export_actions)

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
            motion_points_wrapper = fwrap_gani_motionpoint_export.build_motion_points_list_from_armature(motion_points_armature)
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
                motion_point_actions_by_gani_index = futil_action.build_motion_point_action_maps(motion_point_actions_data)
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
            shader_nodes_actions_by_gani_index = futil_action.build_shader_action_maps(shader_nodes_actions_data)
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
            motion_point_action_data = futil_action.find_motion_point_action_for_gani(gani_name, motion_point_actions_by_gani_index)

        if motion_point_action_data:
            Debug.log(f"\n  Exporting motion points for GANI '{gani_name}': {motion_point_action_data.action.name}")

            # MetaData: Build metadata dict for motion points by analyzing the action and armature
            motion_point_metadata_dict: Dict[str, TrackMetaData] = fwrap_gani_motionpoint_export.build_motion_point_metadata_dict(motion_points_armature, motion_point_action_data.action)
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
        motion_events = fwrap_gani_motionevent.read_motion_events_from_action(gani_action)

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
                shader_node_action_data: Optional[ExportActionData] = futil_action.find_shader_action_for_gani(
                    gani_name, shader_nodes_actions_by_gani_index
                )

                if shader_node_action_data:
                    Debug.log(
                        f"\n  Exporting shader nodes for GANI '{gani_name}': "
                        f"{shader_node_action_data.action.name}"
                    )

                    shader_metadata_dict = fwrap_gani1_shader_export.build_shader_nodes_metadata_dict(
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
                        property_names, property_tracks = fwrap_gani1_shader_export.group_shader_tracks_by_property(
                            flat_shader_tracks, shader_nodes_armature
                        )
                        if property_names:
                            property_headers = fwrap_gani1_shader_export.collect_shader_property_headers(
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
