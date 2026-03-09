import os
import math
from typing import Optional, List, Dict, Tuple

import bpy

from ..py_utilities.utilities_logging import Debug
from ..py_utilities.utilities_blender_animation import (
    add_dummy_keyframes_to_action,
    configure_action,
    remove_action_from_datablock,
    iter_action_fcurves,
    build_data_path_for_bone
)
from ..py_utilities.utilities_blender_armature import BoneSpec, create_track_armature
from ..py_utilities.utilities_naming import format_action_name, format_strip_name, resolve_gani_name_segment

from ..py_foxwrap.foxwrap_metadata import (
    PROP_NO_SKL_LIST,
    TrackMetaData,
    store_track_header_properties_on_action,
    store_track_metadata_on_action,
    store_mtar_properties_on_action,
    store_node_params_on_action,
)
from ..py_fox import fox_mtar_constants as mtar_const
from ..py_foxwrap.foxwrap_misc import TrackUnitWrapper, TrackDataBlobWrapper, Tracks
from ..py_foxwrap.foxwrap_misc_import import ShaderTrackWrapper
from ..py_foxwrap.foxwrap_motionevent import store_motion_events_on_action
from ..py_foxwrap.foxwrap_mtar_reader import MtarReader
from ..py_foxwrap.foxwrap_mapping import BoneParameters

from ..py_fox.fox_mtar_types import MtarTableList, MtarTableList2, MtarHeader
from ..py_fox.fox_gani_types import SegmentType, TrackHeader, TrackMiniHeader, EvpHeader
from ..py_fox.fox_frig_types import FrigFile

from ..py_foxwrap.foxwrap_motionpoint import MotionPointWrapper, store_motion_point_stringlists_on_action
from .tools_gani_track_importer import import_gani_track
from .tools_motion_points_importer import (
    create_nla_strips_for_actions,
    create_motion_points_animation_actions,
    create_and_setup_motion_points_armature,
)
from .tools_gani_shader_importer import (
    create_shader_animation_actions,
    create_and_setup_shader_nodes_armature,
)

FPS_59_94: float = 59.94

# Layout and MetaData #############################################################

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


def validate_track_mapping_collisions(
    all_gani_tracks: List[List[TrackUnitWrapper]],
    track_mapping: Dict[str, BoneParameters],
) -> None:
    """Warn if the mapping would assign multiple segments of the same type to one bone.

    The previous implementation accumulated tracks from *all* GANI files which
    could produce false positives when the same track name appears in multiple
    separate GANIs.  In practice collisions are only problematic *within* a
    single animation file, so we perform the check per `gani_tracks` list.

    Args:
        all_gani_tracks: All GANI track units produced by the reader; outer list
            corresponds to individual GANI files.
        track_mapping: Mapping dictionary keyed by source track name.
    """
    if not track_mapping:
        return

    Debug.log("Validating track mapping collisions...")
    # iterate each GANI independently to avoid cross-file warnings
    for index, gani_tracks in enumerate(all_gani_tracks):
        collision_map: Dict[Tuple[str, SegmentType], List[str]] = {}
        for gani_track in gani_tracks:
            for track_blob in gani_track.segments_track_data:
                name = track_blob.name
                if name not in track_mapping:
                    continue
                mapping_data: BoneParameters = track_mapping[name]
                target_name: str = (
                    mapping_data.track_name if mapping_data.track_name else mapping_data.fox_name
                )
                key = (target_name, track_blob.data_blob.type)
                collision_map.setdefault(key, []).append(name)

        for (target_name, seg_type), sources in collision_map.items():
            if len(sources) > 1:
                Debug.log_warning(
                    f"GANI#{index}: mapping would apply multiple {seg_type.name} segments {sources} to bone '{target_name}'"
                )

def apply_track_transformations(all_gani_tracks: List[List[TrackUnitWrapper]], track_mapping: Optional[Dict[str, BoneParameters]] = None) -> None:
    """Apply track mapping transformations to all tracks.
    
    Applies user-defined track mapping transformations if provided.
    
    Note: Multi-segment track naming (appending segment indices for ARM/TWO_BONE/LIST rig types)
    is now handled at read time by apply_segment_suffixes() in both readers, so it is no longer
    done here. This function focuses purely on mapping-based transformations.
    
    Args:
        all_gani_tracks: List of lists of GaniTrack objects
        track_mapping: Optional dictionary mapping source track name to BoneParameters
    """
    # Apply track mapping transformations if provided
    if track_mapping:
        Debug.log("Applying track mapping transformations...")
        validate_track_mapping_collisions(all_gani_tracks, track_mapping)

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

def create_animation_actions(
    context: bpy.types.Context,
    mtar_file_name: str,
    all_gani_tracks: List[List[TrackUnitWrapper]],
    all_track_mini_headers: List[TrackMiniHeader],
    all_file_headers: List[MtarTableList2],
    layout_track: Optional['Tracks'],
    all_gani_layout_tracks: Optional[List[Optional['Tracks']]] = None,
    all_motion_events: List[Optional[EvpHeader]] = None,
    path_to_indices: Dict[int, Tuple[int, int]] = None,
    use_verbose_naming: bool = False,
    gani_hash_dict: Optional[Dict[int, str]] = None,
    mtar_version: int = 201403250,
    mtar_flags: int = 0x1000,
    all_skl_lists: Optional[List[Optional[List[str]]]] = None,
    all_mtp_lists: Optional[List[Optional[List[str]]]] = None,
    all_mtp_parent_lists: Optional[List[Optional[List[str]]]] = None,
    all_node_params: Optional[List[Dict]] = None,
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
        path_to_indices: Mapping from path hash to (h_index, d_index) tuples
        use_verbose_naming: Whether to include h/d indices in names
        mtar_version: MTAR version (e.g., 201304220 for old, 201403250 for new)
        mtar_flags: MTAR flags (e.g., 0x1000 for new format, 0x0 for old)
        all_skl_lists: Optional bone name lists per GANI (old-format only, for SKL_LIST round-trip)
        all_mtp_lists: Optional motion point name lists per GANI (old-format only, for MTP_LIST round-trip)
        all_mtp_parent_lists: Optional MTP parent name lists per GANI (old-format only, for MTP_PARENT_LIST round-trip)
        
    Returns:
        Tuple of (layout_action, gani_actions_list, max_frame_end)
        Note: max_frame_end is the sum of action frame counts without padding.
        Padding is applied separately when creating NLA strips.
    """
    # Debug: Log list lengths to diagnose IndexError
    Debug.log("create_animation_actions received lists with lengths:")
    Debug.log(f"  all_gani_tracks: {len(all_gani_tracks)}")
    Debug.log(f"  all_track_mini_headers: {len(all_track_mini_headers)}")
    Debug.log(f"  all_file_headers: {len(all_file_headers)}")
    Debug.log(f"  all_motion_events: {len(all_motion_events)}")
    
    # Detect old-format vs new-format MTAR
    is_old_format = not bool(mtar_flags & 0x1000)  # UseMini flag absent = old format
    Debug.log(f"Format detected: {'old (GANI1/FoxData)' if is_old_format else 'new (GANI2/CommonInfo)'}")
    
    # Create layout track action to store metadata
    layout_action: Optional[bpy.types.Action] = None
    if layout_track and layout_track.track_units and not is_old_format:
        # GANI2 / new-format: create layout action (unchanged)
        Debug.log("Creating layout track action for metadata storage (GANI2)...")
        layout_action_name = format_action_name(mtar_file_name, 0, 0, 0, False, is_layout=True)
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
        
        # Store MTAR-level version and flags for export
        store_mtar_properties_on_action(layout_action, mtar_version, mtar_flags)
        
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

        # Resolve GANI path hash to readable name if dictionary provided
        file_header = all_file_headers[gani_index]
        gani_full_path, gani_name_segment = resolve_gani_name_segment(file_header, gani_hash_dict)

        # -----------------------------------------------------
        # Update UI progress for per-GANI processing (keeps overall 'Creating Actions...' stage)
        try:
            total_ganis = len(all_gani_tracks) if len(all_gani_tracks) > 0 else 1
            # Map per-GANI progress into a small slice (30 -> 49)
            progress = 30 + min(19, int(((gani_index + 1) / total_ganis) * 20))
            # Prefer resolved name, then hex hash, then index
            if gani_name_segment:
                display_name = gani_name_segment
            elif hasattr(file_header, 'path'):
                display_name = f"0x{int(file_header.path):016X}"
            else:
                display_name = f"Gani_{gani_index+1:03d}"
            Debug.update_progress(progress, f"GANI {gani_index + 1}/{total_ganis}: {display_name}")
        except Exception:
            # Best-effort progress update; do not interrupt import on failure
            pass
        # -----------------------------------------------------

        # Create one action per GANI file
        # Look up h/d indices from path hash
        h_idx, d_idx = path_to_indices.get(file_header.path, (0, 0))
        if file_header.path not in path_to_indices:
            Debug.log_warning(f"Missing path hash mapping for GANI: 0x{file_header.path:016X}, using h0_d0")
        
        action_name: str = format_action_name(mtar_file_name, gani_index, h_idx, d_idx, use_verbose_naming, gani_name=gani_name_segment)
        action: bpy.types.Action = bpy.data.actions.new(name=action_name)
        gani_actions.append(action)
        Debug.log(f"Created action: {action_name}")
        
        # =============================

        # Store metadata from the actual animation data (GaniTracks) on this action
        # Convert to TrackMetaData and store
        track_mini_header = all_track_mini_headers[gani_index]
        
        # For old-format: store full metadata (including segment types) from per-GANI layout
        # For GANI2: store only bits and flags (segments=False) since layout action has full info
        if is_old_format and all_gani_layout_tracks and gani_index < len(all_gani_layout_tracks):
            per_gani_layout = all_gani_layout_tracks[gani_index]
            if per_gani_layout is not None:
                # Old-format: use per-GANI layout_track which has full segment information
                track_metadata_list = TrackMetaData.from_layout_track_units(
                    per_gani_layout.track_units,
                    gani_tracks=gani_tracks)  # Pass gani_tracks to resolve rig_unit_type from FRIG
                store_track_metadata_on_action(
                    action, track_metadata_list,
                    include_segments=True,  # Full segment types stored for old-format
                    include_hash=True)      # Name hash stored
                store_track_header_properties_on_action(action, per_gani_layout.header)
                store_mtar_properties_on_action(action, mtar_version, mtar_flags)
                Debug.log(f"Stored full metadata on old-format GANI action from per-GANI layout")
            else:
                # Fallback: no per-GANI layout available
                track_metadata_list = TrackMetaData.from_gani_tracks(gani_tracks, track_mini_header.segment_headers)
                store_track_metadata_on_action(action, track_metadata_list, include_segments=False, include_hash=False)
                Debug.log_warning(f"No per-GANI layout available for old-format GANI {gani_index}, using FCurve inference fallback")
        else:
            # GANI2 path: metadata already on layout action; per-GANI only stores bits+flags
            track_metadata_list = TrackMetaData.from_gani_tracks(gani_tracks, track_mini_header.segment_headers)
            store_track_metadata_on_action(action, track_metadata_list, include_segments=False, include_hash=False)
        # Store all non-SHADER node params from this GANI (MOTION, ROOT, etc.) for lossless round-trip
        gani_node_params = all_node_params[gani_index] if all_node_params and gani_index < len(all_node_params) else {}
        for node_key, params in gani_node_params.items():
            if not node_key.startswith("SHADER"):
                store_node_params_on_action(action, node_key, params)
        
        # Store mtar_const.TABL_PATH for re-export: full asset path if unhashed, raw decimal hash string otherwise
        if hasattr(file_header, 'path'):
            if gani_full_path is not None:
                action[mtar_const.TABL_PATH] = gani_full_path
                action.id_properties_ui(mtar_const.TABL_PATH).update(
                    description="Full asset path for this GANI (unhashed from MTAR file header)"
                )
                Debug.log(f"  Stored {mtar_const.TABL_PATH} (unhashed): {gani_full_path}")
            else:
                action[mtar_const.TABL_PATH] = str(file_header.path)
                action.id_properties_ui(mtar_const.TABL_PATH).update(
                    description="PathCode64 hash from MTAR file header (stored as decimal string)"
                )
                Debug.log(f"  Stored {mtar_const.TABL_PATH} (hash): 0x{file_header.path:016X}")

        # M12: Store old-format MtarTableList.unknown for lossless re-export
        if hasattr(file_header, 'unknown'):
            action[mtar_const.TABL_UNKNOWN] = file_header.unknown
            action.id_properties_ui(mtar_const.TABL_UNKNOWN).update(
                description="Old-format MTAR file table 'unknown' field (ushort, typically 7)"
            )
            Debug.log(f"  Stored {mtar_const.TABL_UNKNOWN}: {file_header.unknown}")

        # M10: Store FoxData StringData name lists (old-format only) for lossless re-export
        # Note: SKL_LIST names are applied directly to bone track names during import
        # (see foxwrap_gani_reader.py), so gfox_skl_list is no longer stored here.
        # Instead we store a flag indicating whether the original GANI had NO SKL_LIST node.
        if all_skl_lists and gani_index < len(all_skl_lists) and all_skl_lists[gani_index] is None:
            action[PROP_NO_SKL_LIST] = 1
            action.id_properties_ui(PROP_NO_SKL_LIST).update(
                description="Original GANI had no SKL_LIST node — suppress on re-export"
            )
            Debug.log(f"  Stored {PROP_NO_SKL_LIST}: 1 (original had no SKL_LIST)")
        store_motion_point_stringlists_on_action(
            action,
            all_mtp_lists[gani_index] if (all_mtp_lists and gani_index < len(all_mtp_lists)) else None,
            all_mtp_parent_lists[gani_index] if (all_mtp_parent_lists and gani_index < len(all_mtp_parent_lists)) else None,
        )

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

        # Update offset for next strip (used for calculating total frame range)
        current_frame_offset += gani_frame_count
        max_frame_end = current_frame_offset

    return layout_action, gani_actions, max_frame_end

# Armature #############################################################

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
            rotation_data_path = build_data_path_for_bone(source_name, 'rotation_quaternion')
            for fcurve in iter_action_fcurves(imported_armature.animation_data.action):
                # Check if this fcurve belongs to this bone and is a rotation curve
                if fcurve.data_path == rotation_data_path:
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
            # World space constraint: check if imported armature has bone with the mapped name.
            # After apply_track_transformations the imported armature uses the mapped target names.
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
            # After apply_track_transformations the imported armature uses the mapped target names.
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
    all_file_headers: List[MtarTableList2],
    path_to_indices: Dict[int, Tuple[int, int]],
    use_verbose_naming: bool,
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
        all_file_headers: File headers for path hash lookup
        path_to_indices: Mapping from path hash to (h_index, d_index) tuples
        use_verbose_naming: Whether to include h/d indices in names
        strip_padding: Frames to add between animation strips (default: 10)
        
    Returns:
        Main armature object
    """
    # Create fresh armature (Blender will auto-rename if name already exists)
    Debug.log(f"Creating new armature: {mtar_file_name}")

    # Collect all unique bone names from all GANI files
    all_bone_names = []
    for gani_tracks in all_gani_tracks:
        for gani_track in gani_tracks:
            for keyframes_track in gani_track.segments_track_data:
                bone_name_str = str(keyframes_track.name)
                if bone_name_str not in all_bone_names:
                    all_bone_names.append(bone_name_str)

    Debug.log(f"Found {len(all_bone_names)} unique handle(s)")

    bone_specs = [BoneSpec(name=n) for n in all_bone_names]
    armature: bpy.types.Object = create_track_armature(context, mtar_file_name, bone_specs)

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
        layout_strip.name = format_strip_name(mtar_file_name, 0, 0, 0, False, is_layout=True)
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
            layout_strip.name = format_strip_name(mtar_file_name, 0, 0, 0, False, is_layout=True)
            layout_strip.frame_start = -100
            layout_strip.frame_end = -50
            layout_strip.blend_type = 'REPLACE'
            Debug.log("    Layout strip placed at frames -100 to -50 on custom rig")

    # Create NLA strips for animations on imported armature
    final_frame_offset = create_nla_strips_for_actions(
        nla_track,
        gani_actions,
        mtar_file_name,
        all_file_headers,
        path_to_indices,
        use_verbose_naming,
        is_motion_points=False,
        strip_padding=strip_padding
    )
    
    # Create NLA strips on custom rig if provided
    if target_nla_track:
        create_nla_strips_for_actions(
            target_nla_track,
            gani_actions,
            mtar_file_name,
            all_file_headers,
            path_to_indices,
            use_verbose_naming,
            is_motion_points=False,
            strip_padding=strip_padding
        )

    # Update scene frame range to include all strips and their padding
    # Use the final offset from NLA strip creation (includes padding)
    if final_frame_offset > 0:
        context.scene.frame_end = int(final_frame_offset)
        Debug.log(f"\nSet scene frame range: 0 - {final_frame_offset} (includes {len(gani_actions)} strips + padding)")

    return armature

# MTAR import #############################################################

def sort_gani_data_by_file_offset(
    all_gani_tracks: List[List[TrackUnitWrapper]],
    all_motion_point_gani_tracks: List[List[TrackUnitWrapper]],
    all_motion_events: List[Optional[EvpHeader]],
    all_track_mini_headers: List[TrackMiniHeader],
    all_motion_point_layouts: List[Optional[Tracks]],
    all_file_headers: List[MtarTableList2],
    all_motion_point_track_headers: List[Optional[TrackHeader]],
    all_skl_lists: List[Optional[List[str]]],
    all_mtp_lists: List[Optional[List[str]]],
    all_mtp_parent_lists: List[Optional[List[str]]],
    all_shader_gani_tracks: List[List[ShaderTrackWrapper]],
    all_node_params: List[Dict],
    all_gani_layout_tracks: Optional[List] = None,
) -> Tuple[
    List[List[TrackUnitWrapper]],
    List[List[TrackUnitWrapper]],
    List[Optional[EvpHeader]],
    List[TrackMiniHeader],
    List[Optional[Tracks]],
    List[MtarTableList2],
    List[Optional[TrackHeader]],
    List[Optional[List[str]]],
    List[Optional[List[str]]],
    List[Optional[List[str]]],
    List[List],
    List[Dict],
    Optional[List],
]:
    """Sort all GANI data lists by tracks_offset from file headers.
    
    Sorts data in the order GANIs appear in the MTAR file (by file offset).
    This affects action names and NLA strip ordering.
    
    Args:
        All GANI data lists (must have same length).
        all_shader_gani_tracks: Per-GANI shader track lists (old-format only).
        all_gani_layout_tracks: Per-GANI layout tracks (old-format only).
        
    Returns:
        Same lists sorted by tracks_offset, including all_shader_gani_tracks and all_gani_layout_tracks.
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
            all_motion_point_track_headers[i],
            all_skl_lists[i] if all_skl_lists and i < len(all_skl_lists) else None,
            all_mtp_lists[i] if all_mtp_lists and i < len(all_mtp_lists) else None,
            all_mtp_parent_lists[i] if all_mtp_parent_lists and i < len(all_mtp_parent_lists) else None,
            all_shader_gani_tracks[i] if all_shader_gani_tracks and i < len(all_shader_gani_tracks) else [],
            all_node_params[i] if all_node_params and i < len(all_node_params) else {},
            all_gani_layout_tracks[i] if all_gani_layout_tracks and i < len(all_gani_layout_tracks) else None,
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
    sorted_skl_lists = [item[9] for item in combined]
    sorted_mtp_lists = [item[10] for item in combined]
    sorted_mtp_parent_lists = [item[11] for item in combined]
    sorted_shader_gani_tracks = [item[12] for item in combined]
    sorted_node_params = [item[13] for item in combined]
    sorted_gani_layout_tracks = [item[14] for item in combined]

    return (
        sorted_gani_tracks,
        sorted_motion_point_gani_tracks,
        sorted_motion_events,
        sorted_track_mini_headers,
        sorted_motion_point_layouts,
        sorted_file_headers,
        sorted_motion_point_track_headers,
        sorted_skl_lists,
        sorted_mtp_lists,
        sorted_mtp_parent_lists,
        sorted_shader_gani_tracks,
        sorted_node_params,
        sorted_gani_layout_tracks,
    )

def import_mtar(
        context: bpy.types.Context, 
        filepath: str, 
        frig: Optional[FrigFile], 
        track_mapping: Optional[Dict[str, BoneParameters]] = None, 
        gani_indices: Optional[List[int]] = None, 
        custom_rig: Optional[bpy.types.Object] = None, 
        strip_padding: int = 10,
        gani_hash_dict: Optional[Dict[int, str]] = None) -> Tuple[Dict[str, str], bpy.types.Object]:
    """Import MTAR animation data and create corresponding objects and animations.
    
    Args:
        context: Blender context
        filepath: Path to the MTAR file
        frig: FrigFile object containing rig data (can be None)
        track_mapping: Optional dictionary mapping source track name to BoneParameters (transformation data)
        gani_indices: List of GANI indices to import (None = import all, [] = import nothing)
        custom_rig: Optional Rigify armature to connect imported animation to
        strip_padding: Number of frames to insert between animation strips (default: 10)
        gani_hash_dict: Optional pre-loaded GANI path hash dictionary for name resolution
    """
    # Import the mtar data
    result, imported_armature = import_mtar_data(context, filepath, frig, track_mapping, gani_indices, custom_rig, strip_padding, gani_hash_dict=gani_hash_dict)
    
    # Set up rig constraints if custom rig is provided
    if custom_rig and imported_armature:
        setup_rig(imported_armature, custom_rig, track_mapping)
    
    return result, imported_armature

def import_mtar_data(
        context: bpy.types.Context, 
        filepath: str, 
        frig: Optional[FrigFile], 
        track_mapping: Optional[Dict[str, BoneParameters]] = None, 
        gani_indices: Optional[List[int]] = None, 
        custom_rig: Optional[bpy.types.Object] = None, 
        strip_padding: int = 10,
        gani_hash_dict: Optional[Dict[int, str]] = None) -> Tuple[Dict[str, str], bpy.types.Object]:
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
    all_gani_tracks: List[List[TrackUnitWrapper]] = []
    all_motion_point_gani_tracks: List[List[TrackUnitWrapper]] = []  # Motion point animation tracks
    all_motion_events: List[Optional['EvpHeader']] = []  # Motion events for each GANI
    all_track_mini_headers: List[TrackMiniHeader] = []  # List of TrackMiniHeader objects with segment_headers (main tracks)
    all_motion_point_layouts: List[Optional[Tracks]] = []  # List of Tracks objects (motion point track structures)
    all_file_headers: List[MtarTableList2] = []  # List of MtarTable2 objects with path hash
    all_motion_point_track_headers: List[Optional['TrackHeader']] = []  # List of TrackHeader objects for motion points
    # FoxData StringData name lists (old-format only; None entries for new-format or unavailable GANIs)
    all_skl_lists: List[Optional[List[str]]] = []
    all_mtp_lists: List[Optional[List[str]]] = []
    all_mtp_parent_lists: List[Optional[List[str]]] = []
    all_shader_gani_tracks: List[List[ShaderTrackWrapper]] = []  # ShaderTrackWrapper lists (old-format only)
    all_node_params: List[Dict] = []  # Per-GANI node_params dicts (old-format only; {} for new-format)

    if gani_indices is not None:
        if gani_indices:
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
            all_skl_lists = [results_dict[i][7] for i in sorted(results_dict.keys())]
            all_mtp_lists = [results_dict[i][8] for i in sorted(results_dict.keys())]
            all_mtp_parent_lists = [results_dict[i][9] for i in sorted(results_dict.keys())]
            all_shader_gani_tracks = [results_dict[i][10] for i in sorted(results_dict.keys())]
            all_node_params = [results_dict[i][11] for i in sorted(results_dict.keys())]
            Debug.log(f"Imported {len(all_gani_tracks)} GANI file(s)")
            Debug.log(f"List lengths: gani_tracks={len(all_gani_tracks)}, motion_point_tracks={len(all_motion_point_gani_tracks)}, "
                     f"motion_events={len(all_motion_events)}, track_mini_headers={len(all_track_mini_headers)}, "
                     f"motion_point_layouts={len(all_motion_point_layouts)}, file_headers={len(all_file_headers)}, "
                     f"motion_point_track_headers={len(all_motion_point_track_headers)}")
    else:
        # Import all GANIs
        Debug.log("Importing all GANIs")
        all_gani_tracks, all_motion_point_gani_tracks, all_motion_events, all_track_mini_headers, all_motion_point_layouts, all_file_headers, all_motion_point_track_headers, all_skl_lists, all_mtp_lists, all_mtp_parent_lists, all_shader_gani_tracks, all_node_params = reader.read_all_ganies()
        Debug.log(f"Found {len(all_gani_tracks)} GANI file(s)")

    # Reverse-sort GANIs to match the order of the data in the file instead of the order in the header
    try:
        sort_enabled = bool(context.scene.mtar_properties.settings_props.sort_gani)
    except Exception:
        Debug.log_warning("Missing settings property: context.scene.mtar_properties.settings_props.sort_gani")
        sort_enabled = False
    if sort_enabled and all_file_headers:
        all_gani_tracks, all_motion_point_gani_tracks, all_motion_events, all_track_mini_headers, all_motion_point_layouts, all_file_headers, all_motion_point_track_headers, all_skl_lists, all_mtp_lists, all_mtp_parent_lists, all_shader_gani_tracks, all_node_params, all_gani_layout_tracks = sort_gani_data_by_file_offset(
            all_gani_tracks,
            all_motion_point_gani_tracks,
            all_motion_events,
            all_track_mini_headers,
            all_motion_point_layouts,
            all_file_headers,
            all_motion_point_track_headers,
            all_skl_lists,
            all_mtp_lists,
            all_mtp_parent_lists,
            all_shader_gani_tracks,
            all_node_params,
            all_gani_layout_tracks=reader.all_gani_layout_tracks,
        )

    # Get layout track for metadata storage
    # For new format, get from CommonInfo; for old format, get from reader.layout_track
    layout_track = None
    motion_points = None
    if reader.common_info and reader.common_info.layout_track:
        layout_track = reader.common_info.layout_track
        Debug.log(f"Layout track has {len(layout_track.track_units)} track units")
    elif reader.layout_track:
        # Old format: layout_track is set on reader from first GANI file
        layout_track = reader.layout_track
        Debug.log(f"Layout track (old format) has {len(layout_track.track_units)} track units")
    
    # Get motion points if present
    if reader.common_info and reader.common_info.motion_points:
        motion_points = reader.common_info.motion_points
        Debug.log(f"Motion points found: {motion_points.count} point(s)")
        for entry in motion_points.entries:
            Debug.log(f"  MotionPoint {entry.hash_value}: name={entry.name}, parent={entry.parent_name}")
    elif any(all_motion_point_gani_tracks):
        # Old-format: synthesise MotionPointWrapper from MTP track names and parent strings
        motion_points = MotionPointWrapper.from_old_format(
            all_motion_point_gani_tracks,
            all_mtp_parent_lists,
        )
        if motion_points:
            Debug.log(f"Old-format: synthesised motion points: {motion_points.count} point(s)")
        else:
            Debug.log("Old-format: no motion point tracks found for synthesis")
    
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
    apply_track_transformations(all_motion_point_gani_tracks, track_mapping)
    
    # Extract rest pose from custom rig if provided (merges with mapping file transformations)
    # Check settings to see if rest pose correction is enabled
    enable_rest_pose = context.scene.mtar_properties.settings_props.enable_rest_pose_correction
    if custom_rig and enable_rest_pose:
        extract_rest_pose_from_custom_rig(all_gani_tracks, custom_rig)
    elif custom_rig and not enable_rest_pose:
        Debug.log("\nRest pose correction disabled in settings - skipping extraction")
    
    # Build h/d index mapping for naming (maps path hash to (header_index, data_index))
    Debug.log("Building h/d index mapping for action/strip naming...")
    path_to_indices: Dict[int, Tuple[int, int]] = {}
    with open(filepath, 'rb') as f:
        # Read MTAR header to get file count and format
        header = MtarHeader.read(f)
        # Dispatch based on format: old format uses 16-byte MtarTableList, new format uses 32-byte MtarTableList2
        is_new_format = (header.flags & 0x1000) != 0
        read_func = MtarTableList2.read if is_new_format else MtarTableList.read
        all_headers_raw = [read_func(f) for _ in range(header.file_count)]
    
    # Create sorted index list by tracks_offset to determine d_index
    sorted_h_indices = sorted(range(len(all_headers_raw)), key=lambda i: all_headers_raw[i].tracks_offset)
    for d_idx, h_idx in enumerate(sorted_h_indices):
        path_to_indices[all_headers_raw[h_idx].path] = (h_idx, d_idx)
    Debug.log(f"Built mapping for {len(path_to_indices)} GANI files")
    
    # Get verbose naming setting from import properties
    try:
        use_verbose_naming = bool(context.scene.mtar_properties.import_props.use_verbose_naming)
    except Exception:
        Debug.log_warning("Missing use_verbose_naming property, defaulting to True")
        use_verbose_naming = True
    
    # Use the MTAR filename (without extension) as the armature name
    mtar_file_name: str = os.path.splitext(os.path.basename(filepath))[0]
    
    # Create animation actions first (primary task - can work without armature)
    Debug.update_progress(30, "Creating Actions...")
    layout_action, gani_actions, _ = create_animation_actions(
        context,
        mtar_file_name,
        all_gani_tracks,
        all_track_mini_headers,
        all_file_headers,
        layout_track,
        all_gani_layout_tracks=all_gani_layout_tracks,
        all_motion_events=all_motion_events,
        path_to_indices=path_to_indices,
        use_verbose_naming=use_verbose_naming,
        gani_hash_dict=gani_hash_dict,
        mtar_version=reader.mtar_version,
        mtar_flags=reader.mtar_flags,
        all_skl_lists=all_skl_lists,
        all_mtp_lists=all_mtp_lists,
        all_mtp_parent_lists=all_mtp_parent_lists,
        all_node_params=all_node_params,
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
        all_file_headers,
        path_to_indices,
        use_verbose_naming,
        strip_padding
    )
    
    # Create motion points animation actions (primary task for motion points)
    Debug.update_progress(60, "Creating Motion Points...")
    motion_point_actions = create_motion_points_animation_actions(
        context,
        mtar_file_name,
        all_motion_point_gani_tracks,
        all_motion_point_layouts,
        all_motion_point_track_headers,
        all_file_headers,
        path_to_indices,
        use_verbose_naming,
        gani_hash_dict=gani_hash_dict,
        motion_points=motion_points,
    )
    
    # Create and setup motion points armature with animation data (optional secondary task)
    # Pass gani_actions as reference to synchronize frame offsets across armatures
    Debug.update_progress(65, "Setting up Motion Points...")
    _motion_points_armature = create_and_setup_motion_points_armature(
        context,
        mtar_file_name,
        motion_points,
        motion_point_actions,
        all_file_headers,
        path_to_indices,
        use_verbose_naming,
        strip_padding,
        gani_actions  # Reference actions for frame synchronization
    )

    # Create shader nodes animation actions (old-format only; empty lists for new-format)
    Debug.update_progress(67, "Creating Shader Nodes...")
    shader_actions = create_shader_animation_actions(
        context,
        mtar_file_name,
        all_shader_gani_tracks,
        all_file_headers,
        path_to_indices,
        use_verbose_naming,
        gani_hash_dict=gani_hash_dict,
        all_node_params=all_node_params,
    )

    # Create and setup shader nodes armature with animation data
    # Pass gani_actions as reference to synchronize frame offsets across armatures
    Debug.update_progress(68, "Setting up Shader Nodes...")
    _shader_nodes_armature = create_and_setup_shader_nodes_armature(
        context,
        mtar_file_name,
        all_shader_gani_tracks,
        shader_actions,
        all_file_headers,
        path_to_indices,
        use_verbose_naming,
        strip_padding,
        gani_actions  # Reference actions for frame synchronization
    )

    Debug.log("\n=== MTAR Import Completed Successfully ===")
    Debug.update_progress(70, "Import MTAR Data Finished")

    Debug.stop_timer("MTAR Import")
    return {'FINISHED'}, armature
