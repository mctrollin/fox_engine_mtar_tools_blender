import os
from typing import Optional, List, Dict, Tuple

import bpy

from ..py_core.core_logging import Debug

from ..py_utilities import util_blender_animation, util_blender_armature
from ..py_utilities.util_blender_armature_types import BoneSpec

from ..py_fox import fox_mtar_constants as mtar_const
from ..py_fox.fox_mtar_types import MtarTableList, MtarTableList2, MtarHeader, is_new_mtar_format
from ..py_fox.fox_gani_types import TrackUnitFlags
from ..py_fox.fox_frig_types import FrigFile

from ..py_foxwrap_utilities import futil_filtering, futil_naming, futil_rest_pose_correction

from ..py_foxwrap.fwrap_track_types import Tracks
from ..py_foxwrap.fwrap_mtar_import_types import GaniImportData
from ..py_foxwrap.fwrap_mapping_types import BoneParameters, TransformConstraintEntry
from ..py_foxwrap.fwrap_motionpoint_types import MotionPointWrapper
from ..py_foxwrap import fwrap_metadata, fwrap_motionevent, fwrap_mapping, fwrap_mapping_import, fwrap_motionpoint_import
from ..py_foxwrap.fwrap_mtar_reader import MtarReader

# TODO: tools should not import other tools
from .tools_gani_track_importer import import_gani_track
from .tools_motion_points_importer import (
    create_nla_strips_for_actions,
    create_motion_points_animation_actions,
    create_and_setup_motion_points_armature,
)
from ..py_foxwrap.fwrap_gani1_shader_import import (
    create_shader_animation_actions,
    create_and_setup_shader_nodes_armature,
)


FPS_59_94: float = 59.94



# ... #############################################################


# Animation #############################################################

def create_animation_actions(
    context: bpy.types.Context,
    mtar_file_name: str,
    all_gani_data: List[GaniImportData],
    layout_track: Optional['Tracks'],
    path_to_indices: Dict[int, Tuple[int, int]] = None,
    use_verbose_naming: bool = False,
    gani_hash_dict: Optional[Dict[int, str]] = None,
    mtar_version: int = 201403250,
    mtar_flags: int = 0x1000,
) -> Tuple[Optional[bpy.types.Action], List[bpy.types.Action], int]:
    """Create Blender animation actions from MTAR data.
    
    Args:
        mtar_file_name: Base name for actions
        all_gani_data: List of GaniImportData objects (one per GANI file)
        layout_track: Optional layout track for metadata
        context: Blender context (used for settings and logging)
        path_to_indices: Mapping from path hash to (h_index, d_index) tuples
        use_verbose_naming: Whether to include h/d indices in names
        gani_hash_dict: Optional hash-to-name dictionary for GANI file paths
        mtar_version: MTAR version used by the file
        mtar_flags: MTAR flags used by the file
    
    Returns:
        Tuple of (layout_action, gani_actions_list, max_frame_end)
    """
    # Debug: print number of GANIs for sanity
    Debug.log(f"create_animation_actions received {len(all_gani_data)} GANI data objects")
    
    # Detect old-format vs new-format MTAR
    is_old_format = not is_new_mtar_format(mtar_flags)  # UseMini flag absent = old format
    Debug.log(f"Format detected: {'old (GANI1/FoxData)' if is_old_format else 'new (GANI2/CommonInfo)'}")
    
    # Create layout track action to store metadata
    layout_action: Optional[bpy.types.Action] = None
    if layout_track and layout_track.track_units and not is_old_format:
        # GANI2 / new-format: create layout action (unchanged)
        Debug.log("Creating layout track action for metadata storage (GANI2)...")
        layout_action_name = futil_naming.format_action_name(mtar_file_name, 0, 0, 0, False, is_layout=True)
        layout_action = bpy.data.actions.new(name=layout_action_name)
        layout_action.use_fake_user = True
        
        # Convert layout track to TrackMetaData and store metadata
        # Pass first gani_tracks (if available) to preserve rig_unit_type from FRIG
        first_gani_tracks = all_gani_data[0].gani_bone_tracks if all_gani_data else None
        track_metadata_list = fwrap_metadata.build_track_metadata_from_layout_track_units(layout_track.track_units, gani_tracks=first_gani_tracks)
        fwrap_metadata.store_track_metadata_on_action(layout_action, track_metadata_list)
        
        # Store header properties separately
        if layout_track.header:
            fwrap_metadata.store_track_header_properties_on_action(layout_action, layout_track.header)
        
        # Store MTAR-level version and flags for export
        fwrap_metadata.store_mtar_properties_on_action(layout_action, mtar_version, mtar_flags)
        
        # Add dummy keyframes at frames -100 and -50
        dummy_frames = [-100.0, -50.0]
        util_blender_animation.add_dummy_keyframes_to_action(layout_action, frames=dummy_frames)
        
        # ensure action frame range reflects the dummy frames as well
        util_blender_animation.configure_action(layout_action, frame_start=dummy_frames[0], frame_end=dummy_frames[-1])
        
        Debug.log(f"Created layout track action: {layout_action_name}")

    # Process each GANI file individually to create actions
    gani_actions: List[bpy.types.Action] = []
    current_frame_offset: int = 0
    max_frame_end: int = 0

    Debug.log(f"\nProcessing {len(all_gani_data)} GANI file(s)...")
    for gani_index, data in enumerate(all_gani_data):
        Debug.log(f"\n--- GANI {gani_index + 1}/{len(all_gani_data)} ---")

        # Resolve GANI path hash to readable name if dictionary provided
        file_header = data.file_header
        gani_full_path, gani_name_segment = futil_naming.resolve_gani_name_segment(file_header, gani_hash_dict)

        # -----------------------------------------------------
        # Update UI progress for per-GANI processing (keeps overall 'Creating Actions...' stage)
        try:
            total_ganis = len(all_gani_data) if len(all_gani_data) > 0 else 1
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
        
        action_name: str = futil_naming.format_action_name(mtar_file_name, gani_index, h_idx, d_idx, use_verbose_naming, gani_name=gani_name_segment)
        action: bpy.types.Action = bpy.data.actions.new(name=action_name)
        gani_actions.append(action)
        Debug.log(f"Created action: {action_name}")
        
        # =============================

        # Store metadata from the actual animation data (GaniTracks) on this action
        track_mini_header = data.gani_track_mini_header
        
        # For old-format: store full metadata (including segment types) from per-GANI layout
        # For GANI2: store only bits and flags (segments=False) since layout action has full info
        if is_old_format and data.gani_layout_track:
            per_gani_layout = data.gani_layout_track
            # Old-format: use per-GANI layout_track which has full segment information
            track_metadata_list = fwrap_metadata.build_track_metadata_from_layout_track_units(
                per_gani_layout.track_units,
                gani_tracks=data.gani_bone_tracks)  # Pass gani_tracks to resolve rig_unit_type from FRIG
            fwrap_metadata.store_track_metadata_on_action(
                action, track_metadata_list,
                include_segments=True)  # Full segment types stored for old-format
            fwrap_metadata.store_track_header_properties_on_action(action, per_gani_layout.header)
            fwrap_metadata.store_mtar_properties_on_action(action, mtar_version, mtar_flags)
            Debug.log(f"Stored full metadata on old-format GANI action (idx={gani_index}) from per-GANI layout")
        else:
            # GANI2 path or missing layout: metadata already on layout action; per-GANI only stores bits+flags
            track_metadata_list = fwrap_metadata.build_track_metadata_from_gani_tracks(data.gani_bone_tracks, track_mini_header.segment_headers)
            fwrap_metadata.store_track_metadata_on_action(action, track_metadata_list, include_segments=False)
        # Store all non-SHADER node params from this GANI (MOTION, ROOT, etc.) for lossless round-trip
        gani_node_params = fwrap_metadata.merge_node_params(data.gani_node_params or {})
        for node_key, params in gani_node_params.items():
            fwrap_metadata.store_node_params_on_action(action, node_key, params)
        
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
        if file_header and hasattr(file_header, 'unknown'):
            action[mtar_const.TABL_UNKNOWN] = file_header.unknown
            action.id_properties_ui(mtar_const.TABL_UNKNOWN).update(
                description="Old-format MTAR file table 'unknown' field (ushort, typically 7)"
            )
            Debug.log(f"  Stored {mtar_const.TABL_UNKNOWN}: {file_header.unknown}")

        # M10: Store FoxData StringData name lists (old-format only) for lossless re-export
        # Note: SKL_LIST names are applied directly to bone track names during import
        # (see foxwrap_gani_reader.py), so gfox_skl_list is no longer stored here.
        # Instead we store a flag indicating whether the original GANI had NO SKL_LIST node.
        if data.gani_skeleton_list is None:
            action[fwrap_metadata.PROP_NO_SKL_LIST] = 1
            action.id_properties_ui(fwrap_metadata.PROP_NO_SKL_LIST).update(
                description="Original GANI had no SKL_LIST node — suppress on re-export"
            )
            Debug.log(f"  Stored {fwrap_metadata.PROP_NO_SKL_LIST}: 1 (original had no SKL_LIST)")
        fwrap_motionpoint_import.store_motion_point_stringlists_on_action(
            action,
            data.gani1_motion_point_list,
            data.gani1_motion_point_parent_list,
        )

        # Store motion events if present
        if data.gani_events:
            fwrap_motionevent.store_motion_events_on_action(action, data.gani_events)

        # =============================

        # Get frame count from TrackMiniHeader (imported from MTAR file)
        track_mini_header = data.gani_track_mini_header
        gani_frame_count: int = track_mini_header.frame_count

        # Process each GaniTrack in this GANI file
        Debug.log(f"Processing {len(data.gani_bone_tracks)} GaniTrack(s)...")
        for gani_track in data.gani_bone_tracks:
            import_gani_track(context, action, gani_track)

        Debug.log(f"Track frame range: 0 - {gani_frame_count}")
        
        # Detect LOOP flag: any track with TrackUnitFlags.LOOP sets use_cyclic on the action
        is_loop = any(TrackUnitFlags.LOOP in gani_track.unit_flags for gani_track in data.gani_bone_tracks)

        # Configure action with frame range from MTAR file header
        util_blender_animation.configure_action(action, frame_start=0, frame_end=gani_frame_count, use_cyclic=is_loop)
        Debug.log(f"  Configured action frame range: 0 - {gani_frame_count}, use_cyclic={is_loop}")

        # Update offset for next strip (used for calculating total frame range)
        current_frame_offset += gani_frame_count
        max_frame_end = current_frame_offset

    return layout_action, gani_actions, max_frame_end

# Armature #############################################################

def setup_rig(imported_armature: bpy.types.Object, custom_rig: bpy.types.Object, track_mapping: Optional[Dict[str, BoneParameters]] = None, transform_constraints: Optional[List[TransformConstraintEntry]] = None) -> None:
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
    
    # Set custom rig rotation mode to QUATERNION if any mappings target [armature] with rotation data
    has_armature_rotation = False
    if track_mapping:
        for source_name, mapping_data in track_mapping.items():
            target_name = mapping_data.track_name if mapping_data.track_name else mapping_data.fox_name
            if target_name == fwrap_mapping.ARMATURE_TARGET_NAME:
                # Check if this track has rotation segments
                # (We check the data blobs from the imported tracks)
                # For now, we'll be conservative and assume it might
                has_armature_rotation = True
                break
    
    if has_armature_rotation:
        custom_rig.rotation_mode = 'QUATERNION'
        Debug.log(f"Set custom rig '{custom_rig.name}' rotation mode to QUATERNION for [armature] rotation segments")
    
    # Remove any action currently assigned to the custom rig to ensure constraints and
    # baked animations applied during import do not accidentally modify an existing action.
    try:
        if hasattr(custom_rig, 'animation_data') and custom_rig.animation_data and custom_rig.animation_data.action:
            Debug.log(f"Removing existing action '{custom_rig.animation_data.action.name}' from custom rig '{custom_rig.name}'")
            util_blender_animation.remove_action_from_datablock(custom_rig)
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
            rotation_data_path = util_blender_animation.build_data_path_for_bone(source_name, 'rotation_quaternion')
            for fcurve in util_blender_animation.iter_action_fcurves(imported_armature.animation_data.action):
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

        # Armature-object target: FCurves are written directly to the action,
        # no constraints are needed in the custom rig.
        if target_bone_name == fwrap_mapping.ARMATURE_TARGET_NAME:
            Debug.log(f"  Skipping constraint setup for '{source_name}' -> '[armature]' (object-level FCurves)")
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
                custom_bone = fwrap_metadata.extract_space_bone_name(space_r) if space_type == 'CUSTOM' else None

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
                custom_bone = fwrap_metadata.extract_space_bone_name(space_l) if space_type_l == 'CUSTOM' else None

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
                custom_bone = fwrap_metadata.extract_space_bone_name(space_ik)

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

    # Apply standalone transform constraint directives from mapping file
    if transform_constraints:
        Debug.log("\n--- Creating standalone Transform constraints ---")
        for entry in transform_constraints:
            if entry.owner_bone not in custom_rig.pose.bones:
                Debug.log_warning(f"  constraint_transform: owner bone '{entry.owner_bone}' not found in custom rig, skipping")
                continue
            if entry.target_bone not in custom_rig.pose.bones:
                Debug.log_warning(f"  constraint_transform: target bone '{entry.target_bone}' not found in custom rig, skipping")
                continue

            owner_pose_bone = custom_rig.pose.bones[entry.owner_bone]
            c = owner_pose_bone.constraints.new('COPY_TRANSFORMS')
            c.name = f"MTAR_Transform_{entry.target_bone}"
            c.target = custom_rig
            c.subtarget = entry.target_bone
            # All other settings (spaces, ranges, mix mode, influence) stay at Blender defaults.
            constraints_added += 1
            Debug.log(f"  Created Transform constraint: {custom_rig.name}['{entry.owner_bone}'] <- {custom_rig.name}['{entry.target_bone}']")

    Debug.log(f"Constraints setup complete: {constraints_added} constraint(s) added")

def create_and_setup_armature(
    context: bpy.types.Context,
    mtar_file_name: str,
    all_gani_data: List[GaniImportData],
    gani_actions: List[bpy.types.Action],
    layout_action: Optional[bpy.types.Action],
    custom_rig: Optional[bpy.types.Object],
    path_to_indices: Dict[int, Tuple[int, int]],
    use_verbose_naming: bool,
    strip_padding: int = 10
) -> bpy.types.Object:
    """Create and set up the imported armature with pre-created animation data.
    
    Args:
        context: Blender context
        mtar_file_name: Base name for the armature and actions
        all_gani_data: List of GaniImportData objects (one per GANI file)
        gani_actions: Pre-created list of GANI actions
        layout_action: Pre-created layout track action
        custom_rig: Optional custom rig for NLA tracks
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
    for gani_track in GaniImportData.iter_bone_tracks(all_gani_data):
        for keyframes_track in gani_track.segments_track_data:
            bone_name_str = str(keyframes_track.name)
            # Skip the special armature-object target — it is not a real bone.
            if bone_name_str == fwrap_mapping.ARMATURE_TARGET_NAME:
                continue
            if bone_name_str not in all_bone_names:
                all_bone_names.append(bone_name_str)

    Debug.log(f"Found {len(all_bone_names)} unique handle(s)")

    bone_specs = [BoneSpec(name=n) for n in all_bone_names]
    armature: bpy.types.Object = util_blender_armature.create_track_armature(context, mtar_file_name, bone_specs)

    # Create animation data on imported armature
    Debug.log("Setting up animation data on armature...")
    if not armature.animation_data:
        armature.animation_data_create()

    # Add limits to prevent the imported armature from being moved or rotated
    # by anything other than animation (object-level FCurves). Setting min/max
    # to zero locks all axes.
    # This is necessary when mapping the root motion bone to the armature itself
    # for proper ik-targets (e.g. hands, feet) transforms on the custom rig bone constraints
    loc_constraint = armature.constraints.new('LIMIT_LOCATION')
    loc_constraint.name = 'MTAR_LimitLocation'
    loc_constraint.use_min_x = True
    loc_constraint.use_min_y = True
    loc_constraint.use_min_z = True
    loc_constraint.use_max_x = True
    loc_constraint.use_max_y = True
    loc_constraint.use_max_z = True
    loc_constraint.min_x = 0.0
    loc_constraint.min_y = 0.0
    loc_constraint.min_z = 0.0
    loc_constraint.max_x = 0.0
    loc_constraint.max_y = 0.0
    loc_constraint.max_z = 0.0

    rot_constraint = armature.constraints.new('LIMIT_ROTATION')
    rot_constraint.name = 'MTAR_LimitRotation'
    rot_constraint.use_limit_x = True
    rot_constraint.use_limit_y = True
    rot_constraint.use_limit_z = True
    rot_constraint.min_x = 0.0
    rot_constraint.min_y = 0.0
    rot_constraint.min_z = 0.0
    rot_constraint.max_x = 0.0
    rot_constraint.max_y = 0.0
    rot_constraint.max_z = 0.0

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
        layout_strip.name = futil_naming.format_strip_name(mtar_file_name, 0, 0, 0, False, is_layout=True)
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
            layout_strip.name = futil_naming.format_strip_name(mtar_file_name, 0, 0, 0, False, is_layout=True)
            layout_strip.frame_start = -100
            layout_strip.frame_end = -50
            layout_strip.blend_type = 'REPLACE'
            Debug.log("    Layout strip placed at frames -100 to -50 on custom rig")

    # Create NLA strips for animations on imported armature
    file_headers = [d.file_header for d in all_gani_data]
    final_frame_offset = create_nla_strips_for_actions(
        nla_track,
        gani_actions,
        mtar_file_name,
        file_headers,
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
            file_headers,
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

def sort_gani_data_by_file_offset(all_gani_data: List[GaniImportData]) -> List[GaniImportData]:
    """Return a new list of GaniImportData sorted by the MTAR file offset.

    The ordering of GANIs inside the MTAR container is defined by the
    ``tracks_offset`` field in the corresponding file header.  This function
    makes it easy to reorder the results so that actions and NLA strips are
    created in the same sequence as they appear in the file.
    """
    # if any entry lacks a header, treat its offset as zero to avoid errors
    return sorted(
        all_gani_data,
        key=lambda d: (d.file_header.tracks_offset if d.file_header else 0)
    )

def import_mtar(
        context: bpy.types.Context, 
        filepath: str, 
        frig: Optional[FrigFile], 
        track_mapping: Optional[Dict[str, BoneParameters]] = None, 
        gani_indices: Optional[List[int]] = None, 
        custom_rig: Optional[bpy.types.Object] = None, 
        strip_padding: int = 10,
        gani_hash_dict: Optional[Dict[int, str]] = None,
        transform_constraints: Optional[List[TransformConstraintEntry]] = None) -> Tuple[Dict[str, str], bpy.types.Object]:
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
        setup_rig(imported_armature, custom_rig, track_mapping, transform_constraints)
    
    return result, imported_armature

def import_mtar_data(
        context: bpy.types.Context,
        filepath: str,
        frig: Optional[FrigFile],
        track_mapping: Optional[Dict[str, BoneParameters]] = None,
        gani_filter_indices: Optional[List[int]] = None,
        custom_rig: Optional[bpy.types.Object] = None,
        strip_padding: int = 10,
        gani_hash_dict: Optional[Dict[int, str]] = None
        ) -> Tuple[Dict[str, str], bpy.types.Object]:
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
        gani_filter_indices: List of GANI indices to import (None = import all, [] = import nothing)
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

    # Read gani data
    all_gani_data: List[GaniImportData] = []

    # Simple index based filter
    if gani_filter_indices is not None:
        if gani_filter_indices:
            # Import selected GANIs
            Debug.log(f"Selective import: GANI indices {gani_filter_indices}")
            results_dict = reader.read_selected_ganis(gani_filter_indices)
            # convert to list sorted by index
            all_gani_data = [results_dict[i] for i in sorted(results_dict.keys())]
            Debug.log(f"Imported {len(all_gani_data)} GANI file(s)")
    else:
        # Import all GANIs
        Debug.log("Importing all GANIs")
        all_gani_data = reader.read_all_ganies()
        Debug.log(f"Found {len(all_gani_data)} GANI file(s)")

    # File based filter
    all_gani_data = futil_filtering.filter_gani_import_data(
        all_gani_data,
        bpy.path.abspath(context.scene.mtar_properties.gani_filter_txt_filepath) if context.scene.mtar_properties.use_gani_filter_file else None,
        gani_hash_dict=gani_hash_dict,
    )

    if not all_gani_data:
        Debug.log_warning("No GANI files available after filtering; import cancelled")
        Debug.stop_timer("MTAR Import")
        return ({'CANCELLED': 'No GANI animations matched the filter'}, None)

    # Sorting: Reverse-sort GANIs to match the order of the data in the file instead of the order in the header
    if all_gani_data and bool(context.scene.mtar_properties.settings_props.sort_gani):
        # sort the consolidated data objects by their embedded file_header offset
        all_gani_data = sort_gani_data_by_file_offset(all_gani_data)

    # convenience list of headers for downstream routines that still expect it
    file_headers = [d.file_header for d in all_gani_data]
    # also build lists used by shader importer modules
    all_shader_gani_tracks = [d.gani1_shader_tracks for d in all_gani_data]
    all_node_params = [d.gani_node_params for d in all_gani_data]

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
    else:
        # Old-format: synthesise MotionPointWrapper from per-GANI mtp_tracks & parent lists
        all_mtp_tracks = [d.gani_mtp_tracks for d in all_gani_data]
        all_mtp_parent_lists = [d.gani1_motion_point_parent_list for d in all_gani_data]
        if any(all_mtp_tracks):
            motion_points = MotionPointWrapper.from_old_format(
                all_mtp_tracks,
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
        # rig units correspond to track index within each GANI file
        for data in all_gani_data:
            for gani_track_index, gani_track in enumerate(data.gani_bone_tracks):
                # Check if we have a corresponding rig unit def
                if gani_track_index < len(frig.rig_def.unit_defs):
                    rig_unit_def = frig.rig_def.unit_defs[gani_track_index]
                    gani_track.rig_unit_type = rig_unit_def.unit_type
                    Debug.log(f"  GaniTrack {gani_track_index} '{gani_track.name}' -> RigUnitType.{gani_track.rig_unit_type.name}")
                else:
                    Debug.log_warning(f"  Warning: No rig unit def for GaniTrack {gani_track_index} '{gani_track.name}'")
    
    # Modify keyframes track names based on rig unit type and apply track mapping transformations
    Debug.update_progress(20, "Applying Mapping...")
    # apply mapping to all tracks (bone + motion points) stored in the data objects
    fwrap_mapping_import.apply_track_transformations(all_gani_data, track_mapping)
    
    # Extract rest pose from custom rig if provided (merges with mapping file transformations)
    # Check settings to see if rest pose correction is enabled
    enable_rest_pose = context.scene.mtar_properties.settings_props.enable_rest_pose_correction
    if custom_rig and enable_rest_pose:
        futil_rest_pose_correction.extract_rest_pose_from_custom_rig(all_gani_data, custom_rig)
    elif custom_rig and not enable_rest_pose:
        Debug.log("\nRest pose correction disabled in settings - skipping extraction")
    
    # Build h/d index mapping for naming (maps path hash to (header_index, data_index))
    Debug.log("Building h/d index mapping for action/strip naming...")
    path_to_indices: Dict[int, Tuple[int, int]] = {}
    with open(filepath, 'rb') as f:
        # Read MTAR header to get file count and format
        header = MtarHeader.read(f)
        # Dispatch based on format: old format uses 16-byte MtarTableList, new format uses 32-byte MtarTableList2
        is_new_format = is_new_mtar_format(header.flags)
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
        all_gani_data,
        layout_track,
        path_to_indices=path_to_indices,
        use_verbose_naming=use_verbose_naming,
        gani_hash_dict=gani_hash_dict,
        mtar_version=reader.mtar_version,
        mtar_flags=reader.mtar_flags,
    )
    
    # Create and setup the armature with animation data (optional secondary task)
    Debug.update_progress(50, "Setting up Armature...")
    armature = create_and_setup_armature(
        context,
        mtar_file_name,
        all_gani_data,
        gani_actions,
        layout_action,
        custom_rig,
        path_to_indices,
        use_verbose_naming,
        strip_padding
    )
    
    # Create motion points animation actions (primary task for motion points)
    Debug.update_progress(60, "Creating Motion Points...")
    # build helper lists from unified data objects
    all_motion_point_gani_tracks = [gani_data.gani_mtp_tracks for gani_data in all_gani_data]
    all_motion_point_layouts = [gani_data.gani_motion_point_layout for gani_data in all_gani_data]
    all_motion_point_track_headers = [gani_data.gani_motion_point_track_header for gani_data in all_gani_data]
    motion_point_actions = create_motion_points_animation_actions(
        context,
        mtar_file_name,
        all_motion_point_gani_tracks,
        all_motion_point_layouts,
        all_motion_point_track_headers,
        file_headers,
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
        file_headers,
        path_to_indices,
        use_verbose_naming,
        strip_padding,
        gani_actions  # Reference actions for frame synchronization
    )

    # parent the motion points armature to whichever rig will be exported
    # (custom rig if provided, otherwise the imported armature). This ensures
    # auto-detection works when the user chooses a custom rig later.
    parent_target = custom_rig if custom_rig else armature
    if _motion_points_armature and parent_target:
        try:
            _motion_points_armature.parent = parent_target
            Debug.log(
                f"Parented motion points armature '{_motion_points_armature.name}' to '{parent_target.name}'"
            )
        except Exception:
            Debug.log_warning(
                f"Failed to parent motion points armature '{_motion_points_armature.name}' to '{parent_target.name}'"
            )

    # Create shader nodes animation actions (old-format only; empty lists for new-format)
    Debug.update_progress(67, "Creating Shader Nodes...")
    shader_actions = create_shader_animation_actions(
        context,
        mtar_file_name,
        all_shader_gani_tracks,
        file_headers,
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
        file_headers,
        path_to_indices,
        use_verbose_naming,
        strip_padding,
        gani_actions  # Reference actions for frame synchronization
    )

    if _shader_nodes_armature and parent_target:
        try:
            _shader_nodes_armature.parent = parent_target
            Debug.log(
                f"Parented shader nodes armature '{_shader_nodes_armature.name}' to '{parent_target.name}'"
            )
        except Exception:
            Debug.log_warning(
                f"Failed to parent shader nodes armature '{_shader_nodes_armature.name}' to '{parent_target.name}'"
            )

    Debug.log("\n=== MTAR Import Completed Successfully ===")
    Debug.update_progress(70, "Import MTAR Data Finished")

    Debug.stop_timer("MTAR Import")
    return {'FINISHED'}, armature
