"""
Blender operators for MTAR import/export functionality.
"""
import os
import io
from typing import Set, Optional, List, TYPE_CHECKING
import traceback

import bpy
from bpy.types import Operator, Context, Event
from bpy.props import StringProperty

from .py_utilities.logging_utilities import Debug
from .py_utilities.hash_utilities import unhash_rig_type

from .py_fox.fox_mtar_types import MtarHeader
from .py_fox.fox_frig_types import FrigFile, RigUnitDef

from .py_foxwrap.foxwrap_misc_import import CommonInfo
from .py_foxwrap.foxwrap_misc_export import TrackSegmentBoneMapping, BoneParameters, IkUpParameters
from .py_foxwrap.foxwrap_mapping import parse_track_mapping_file
from .py_foxwrap.foxwrap_metadata import get_segments_for_track_type, iter_track_properties
from .py_foxwrap.foxwrap_metadata import get_all_track_metadata_from_action

from .mtar_importer import import_mtar
from .mtar_exporter import export_mtar, find_layout_track_action
from .py_tools.bake_armature import bake_armature_action, bake_armature_nla_strips

if TYPE_CHECKING:
    from bpy.types import Object


def build_track_segment_bone_mapping_from_file(mapping_filepath: str, layout_action: bpy.types.Action, 
                                              armature: bpy.types.Object) -> tuple[TrackSegmentBoneMapping, List[str]]:
    """Build TrackSegmentBoneMapping from mapping file and layout action.
    
    Args:
        mapping_filepath: Path to the track mapping file
        layout_action: Layout action containing track structure metadata
        armature: Armature object to validate bone names against
        
    Returns:
        Tuple of (TrackSegmentBoneMapping, missing_bones_list)
    """
    from collections import defaultdict
    
    Debug.log(f"Loading bone mapping from: {mapping_filepath}")
    mapping_data = parse_track_mapping_file(mapping_filepath)
    if mapping_data.blender_to_fox:
        Debug.log(f"Loaded {len(mapping_data.blender_to_fox)} blender-to-fox bone mapping(s)")
    
    # Build track_segment_bone_mapping using track indices from metadata
    # The mapping file defines fox_name -> blender_name mappings
    # Track indices come from the layout action metadata (stored during import)
    track_segment_bone_mapping = TrackSegmentBoneMapping()
    missing_bones = []
    
    Debug.log("\nBuilding track mapping from mapping file and layout action metadata...")
    
    # Parse track indices from layout action custom properties using utility function
    track_name_to_idx = {}
    for track_idx, track_name, _ in iter_track_properties(layout_action):
        track_name_to_idx[track_name] = track_idx
    
    Debug.log(f"  Found {len(track_name_to_idx)} track(s) in layout action")
    for track_name, track_idx in sorted(track_name_to_idx.items(), key=lambda x: x[1]):
        Debug.log(f"    Track {track_idx}: {track_name}")
    
    # Build track_bone_mapping in the order defined by the layout action
    # The layout action stores track indices in property keys: "track_<padded_idx>_<track_name>"
    # For multi-segment tracks, the mapping file has entries like "LArm_0", "LArm_1", "LArm_2"
    # but the layout action stores only the base track name "LArm"
    # We need to collect all segment bones for each track
    
    # First, group bones by their base track name
    track_segments = defaultdict(list)  # base_track_name -> [(segment_idx, blender_bone_name, fox_mapping)]
    
    Debug.log(f"  Processing {len(mapping_data.fox_to_blender)} fox-to-blender mapping(s) from file...")
    
    # Use fox_to_blender to preserve all Fox bone names (multiple Fox bones can map to same Blender bone)
    for fox_name, fox_mapping in mapping_data.fox_to_blender.items():
        blender_bone_name = mapping_data.fox_to_blender_names[fox_name]
        
        # Check if this bone exists in the armature
        if blender_bone_name not in armature.data.bones:
            missing_bones.append(blender_bone_name)
            continue
        
        # Multi-segment tracks have segment suffixes (e.g., "LArm_0", "LArm_1", "LArm_2")
        # but the layout action stores the base track name (e.g., "LArm")
        # Strip the segment suffix to find the track index
        base_track_name = fox_name
        segment_idx = None
        
        # Check if fox_name ends with _<digit> pattern (segment suffix)
        if '_' in fox_name:
            parts = fox_name.rsplit('_', 1)
            if len(parts) == 2 and parts[1].isdigit():
                base_track_name = parts[0]
                segment_idx = int(parts[1])
        
        # Debug: Show what we're processing
        seg_info = f" (seg {segment_idx})" if segment_idx is not None else ""
        Debug.log(f"    Mapping: {blender_bone_name} -> {fox_name} -> track: {base_track_name}{seg_info}")
        
        # Find the track index using the base track name
        if base_track_name in track_name_to_idx:
            # Create BoneParameters from mapping data
            # Handle as_ik_up conversion if present
            as_ik_up = None
            if 'as_ik_up' in fox_mapping and fox_mapping['as_ik_up']:
                ik_data = fox_mapping['as_ik_up']
                as_ik_up = IkUpParameters(
                    bone_base=ik_data['bone_base'],
                    axis=ik_data['axis'],
                    distance=ik_data['distance']
                )
            
            # Extract custom_bone from space_r if present (convert dict to string)
            space_r_value = None
            if 'space_r' in fox_mapping and fox_mapping['space_r']:
                space_r_dict = fox_mapping['space_r']
                if isinstance(space_r_dict, dict):
                    # Extract custom_bone if present, otherwise use 'ws' for world space
                    space_r_value = space_r_dict.get('custom_bone', 'ws')
                else:
                    # Legacy format: already a string
                    space_r_value = space_r_dict
            
            # Extract custom_bone from space_l if present (convert dict to string)
            space_l_value = None
            if 'space_l' in fox_mapping and fox_mapping['space_l']:
                space_l_dict = fox_mapping['space_l']
                if isinstance(space_l_dict, dict):
                    # Extract custom_bone if present, otherwise use 'ws' for world space
                    space_l_value = space_l_dict.get('custom_bone', 'ws')
                else:
                    # Legacy format: already a string
                    space_l_value = space_l_dict
            
            # Create BoneParameters instance
            bone_params = BoneParameters(
                fox_name=fox_name,
                rotation_offset=fox_mapping.get('rotation_offset'),
                rotation_axis_map=fox_mapping.get('rotation_axis_map'),
                space_r=space_r_value,
                space_l=space_l_value,
                as_ik_up=as_ik_up,
                track_name=fox_mapping.get('track_name', '')
            )
            
            track_segments[base_track_name].append((segment_idx if segment_idx is not None else 0, blender_bone_name, bone_params))
        else:
            Debug.log_warning(f"  Warning: Fox bone '{fox_name}' (base: '{base_track_name}') not found in layout action, skipping")
    
    Debug.log(f"  Collected segments for {len(track_segments)} base track(s)")
    
    # Build unified track segment bone mapping
    # All segments use the same key format: (track_idx, segment_idx)
    for base_track_name, segments in track_segments.items():
        track_idx = track_name_to_idx[base_track_name]
        
        # Sort segments by segment index
        segments.sort(key=lambda x: x[0])
        
        # Store all segments using unified format
        for segment_idx, blender_bone_name, fox_mapping in segments:
            track_segment_bone_mapping.set_segment_mapping(track_idx, segment_idx, blender_bone_name, fox_mapping)
        
        if len(segments) > 1:
            segment_names = [f"{seg[1]} (seg {seg[0]})" for seg in segments]
            Debug.log(f"  Track {track_idx}: {base_track_name} -> {len(segments)} segments: {', '.join(segment_names)}")
        else:
            Debug.log(f"  Track {track_idx}: {base_track_name} -> {segments[0][1]}")
    
    # Show which tracks from layout action are missing mappings
    Debug.log("  Checking for unmapped tracks...")
    for track_name, track_idx in sorted(track_name_to_idx.items(), key=lambda x: x[1]):
        if track_name not in track_segments:
            Debug.log_warning(f"    Warning: Layout track {track_idx} '{track_name}' has no mapping in mapping file")
    
    # Parse layout metadata to get expected segment counts
    Debug.log("  Parsing layout metadata for segment structure...")
    
    metadata_dict = get_all_track_metadata_from_action(layout_action)
    
    # Finalize mappings by populating missing segments using layout metadata
    Debug.log("  Finalizing mappings with layout metadata...")
    track_segment_bone_mapping.finalize_with_layout_metadata(metadata_dict)
    
    # Report final track count
    track_count = track_segment_bone_mapping.get_total_track_count()
    Debug.log(f"  Built {track_count} track mapping(s) for export")
    
    return track_segment_bone_mapping, missing_bones


class MTAR_OT_GenerateTrackMappingTemplateFile(Operator):
    """Generate a barebone track mapping file from FRIG skeleton structure and MTAR animation data."""
    bl_idname = "mtar.generate_track_mapping_template_file"
    bl_label = "Generate Track Mapping Template File"
    bl_description = "Create a track mapping file template from FRIG skeleton structure and MTAR animation data as starting point for a custom mapping file."
    bl_options = {'REGISTER'}
    
    def execute(self, context: Context) -> Set[str]:
        props = context.scene.mtar_properties
        
        # Validate FRIG file path
        if not props.import_frig_filepath:
            self.report({'ERROR'}, "No FRIG file selected")
            return {'CANCELLED'}
        
        if not os.path.exists(props.import_frig_filepath):
            self.report({'ERROR'}, f"FRIG file not found: {props.import_frig_filepath}")
            return {'CANCELLED'}
        
        # Validate MTAR file path (optional but recommended)
        mtar_data = None
        if props.import_mtar_filepath and os.path.exists(props.import_mtar_filepath):
            try:
                Debug.log(f"Reading MTAR file: {props.import_mtar_filepath}")
                # Read MTAR to get CommonInfo with layout track
                with open(props.import_mtar_filepath, 'rb') as f:
                    file_data = f.read()
                    br = io.BytesIO(file_data)
                    header = MtarHeader.read(br)
                    
                    if header.common_info_offset != 0:
                        mtar_data = {
                            'header': header,
                            'common_info': CommonInfo.read(br, header)
                        }
                        Debug.log(f"MTAR CommonInfo loaded: {header.track_count} tracks, {header.segment_count} segments")
            except (OSError, ValueError) as e:
                Debug.log_warning(f"  Warning: Could not read MTAR file: {e}")
                # Continue without MTAR data
        elif props.import_mtar_filepath:
            Debug.log_warning(f"  Warning: MTAR file not found: {props.import_mtar_filepath}")
        
        try:
            # Read FRIG file
            Debug.log(f"Reading FRIG file: {props.import_frig_filepath}")
            with open(props.import_frig_filepath, 'rb') as f:
                frig: FrigFile = FrigFile.read(f)
            
            if not frig or not frig.rig_def:
                self.report({'ERROR'}, "Failed to read FRIG rig data")
                return {'CANCELLED'}
            
            # Generate output filepath
            frig_dir: str = os.path.dirname(props.import_frig_filepath)
            frig_name: str = os.path.splitext(os.path.basename(props.import_frig_filepath))[0]
            output_path: str = os.path.join(frig_dir, f"{frig_name}_track_mapping.txt")
            
            # Check if file already exists (TODO: for now we override it bc easier testing, later we should re-enalbe the check.)
            # if os.path.exists(output_path):
            #     self.report({'WARNING'}, f"Mapping file already exists: {output_path}")
            #     return {'CANCELLED'}
            
            # Generate mapping file content
            lines: List[str] = []
            lines.append("# Track Mapping File")
            lines.append(f"# Generated from: {os.path.basename(props.import_frig_filepath)}")
            if mtar_data:
                lines.append(f"# MTAR reference: {os.path.basename(props.import_mtar_filepath)}")
            lines.append("#")
            lines.append("# Edit this file to customize bone mappings and transformations")
            lines.append("# See example_track_mapping.txt for detailed documentation")
            lines.append("")
            
            # Get track units from rig_def
            if frig.rig_def and frig.rig_def.unit_defs:
                unit_defs: List[RigUnitDef] = frig.rig_def.unit_defs
                
                # Get layout track units from MTAR if available
                layout_track_units = None
                if mtar_data and mtar_data['common_info'] and mtar_data['common_info'].layout_track:
                    layout_track_units = mtar_data['common_info'].layout_track.track_units
                    Debug.log(f"Using MTAR layout track with {len(layout_track_units)} units")
                
                for track_idx, unit_def in enumerate(unit_defs):
                    # Get track name from layout track (MTAR) if available
                    track_name: str = f"Track{track_idx}"
                    track_hash: Optional[int] = None
                    
                    # Read track info from MTAR layout track
                    if layout_track_units and track_idx < len(layout_track_units):
                        layout_unit = layout_track_units[track_idx]
                        if layout_unit.name:  # layout_unit.name is the StrCode32 hash
                            track_hash = layout_unit.name
                            # Try to resolve the hash to a track name
                            track_hash_int = track_hash.to_int() if hasattr(track_hash, 'to_int') else int(track_hash)
                            resolved_name: str = unhash_rig_type(track_hash_int)
                            if resolved_name:
                                track_name = resolved_name
                    
                    # Get track type from unit_type
                    track_type: Optional[str] = None
                    if unit_def.unit_type is not None:
                        try:
                            track_type = unit_def.unit_type.name
                        except (ValueError, AttributeError):
                            track_type = f"UNKNOWN_{unit_def.unit_type}"
                    
                    # Get expected segments from track type
                    segments_shorthand: List[str] = []
                    
                    # If we have layout track units from MTAR, use actual segment count
                    actual_segment_count: Optional[int] = None
                    if layout_track_units and track_idx < len(layout_track_units):
                        layout_unit = layout_track_units[track_idx]
                        actual_segment_count = len(layout_unit.track_data)
                        Debug.log(f"Track {track_idx}: MTAR reports {actual_segment_count} segments")
                    
                    if track_type:
                        # For MULTI_LOCAL_ORIENTATION type, check if we can get segment count from unit_def or MTAR
                        if track_type == 'MULTI_LOCAL_ORIENTATION':
                            # Prefer actual segment count from MTAR
                            segment_count: int = actual_segment_count if actual_segment_count else 1
                            # Fall back to unit_def counts if no MTAR data
                            if not actual_segment_count:
                                if unit_def.bone_count:
                                    segment_count = unit_def.bone_count
                                elif unit_def.track_count:
                                    segment_count = unit_def.track_count
                            # Generate segment shorthand for MULTI_LOCAL_ORIENTATION (typically all 'q')
                            segments_shorthand = ['q'] * segment_count
                        else:
                            # Get segments from track type
                            segments = get_segments_for_track_type(track_type)
                            # Convert to shorthand notation
                            for seg in segments:
                                data_type: str = seg.get('data_type', '')
                                if data_type == 'quatdiff':
                                    segments_shorthand.append('qd')
                                elif data_type == 'quat':
                                    segments_shorthand.append('q')
                                elif data_type == 'vec3diff':
                                    segments_shorthand.append('vd')
                                elif data_type == 'vec3':
                                    segments_shorthand.append('v')
                                elif data_type == 'float':
                                    segments_shorthand.append('f')
                                else:
                                    segments_shorthand.append('?')
                            # Validate against MTAR if available
                            if actual_segment_count and len(segments_shorthand) != actual_segment_count:
                                Debug.log_warning(f"  Warning: Track {track_idx} type {track_type} expects {len(segments_shorthand)} segments, but MTAR has {actual_segment_count}")
                                # Use MTAR count as authoritative
                                if actual_segment_count > len(segments_shorthand):
                                    segments_shorthand.extend(['?'] * (actual_segment_count - len(segments_shorthand)))
                                else:
                                    segments_shorthand = segments_shorthand[:actual_segment_count]
                    
                    segment_str: str = ', '.join(segments_shorthand) if segments_shorthand else '?'
                    
                    # For multi-orientation tracks, use "q * count" format
                    if track_type == 'MULTI_LOCAL_ORIENTATION' and len(segments_shorthand) > 3 and all(s == 'q' for s in segments_shorthand):
                        segment_str = f"q * {len(segments_shorthand)}"
                    
                    # Write track comment with segment info and hash (if available)
                    if track_hash:
                        lines.append(f"# Track {track_idx} ({segment_str}) - Hash: {track_hash} (0x{track_hash:X})")
                    else:
                        lines.append(f"# Track {track_idx} ({segment_str})")
                    # Write @track directive
                    if track_type:
                        # Detect MULTI_LOCAL_ORIENTATION: type with many quaternion segments
                        if track_type == 'MULTI_LOCAL_ORIENTATION' and len(segments_shorthand) > 3:
                            count = len(segments_shorthand)
                            lines.append(f"@track {track_name} : type=MULTI_LOCAL_ORIENTATION ; count={count}")
                        else:
                            lines.append(f"@track {track_name} : type={track_type}")
                    else:
                        # If no track type, just add placeholder
                        lines.append(f"@track {track_name} : type=UNKNOWN")
                    
                    # Write bone mapping template
                    # For multi-segment tracks, add suffix to each bone
                    if len(segments_shorthand) > 1:
                        for seg_idx in range(len(segments_shorthand)):
                            lines.append(f"{track_name}_{seg_idx} : {track_name}_{seg_idx}")
                    else:
                        lines.append(f"{track_name} : {track_name}")
                    
                    lines.append("")
            
            # Write file
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            
            self.report({'INFO'}, f"Mapping file created: {output_path}")
            Debug.log(f"Generated mapping file: {output_path}")
            
            # Auto-fill the mapping file path
            props.import_mapping_filepath = output_path
            
            return {'FINISHED'}
            
        except Exception as e:  # noqa: E722
            self.report({'ERROR'}, f"Failed to generate mapping file: {str(e)}")
            Debug.log_error(f"Error generating mapping file: {e}")
            traceback.print_exc()
            return {'CANCELLED'}


class MTAR_OT_ImportAnimationFromMTAR(Operator):
    """Import MTAR animation with FRIG rig data."""
    bl_idname = "mtar.import_animation"
    bl_label = "Import MTAR Animation"
    bl_description = "Import animation from MTAR file using FRIG rig structure"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context: Context) -> Set[str]:

        Debug.log("========= STARTING IMPORT MTAR OPERATION =========")

        props = context.scene.mtar_properties
        
        # Validate MTAR file path
        if not props.import_mtar_filepath:
            self.report({'ERROR'}, "No MTAR file selected")
            return {'CANCELLED'}
        
        if not os.path.exists(props.import_mtar_filepath):
            self.report({'ERROR'}, f"MTAR file not found: {props.import_mtar_filepath}")
            return {'CANCELLED'}
        
        # Load FRIG file if provided
        frig_data = None
        if props.import_frig_filepath:
            if not os.path.exists(props.import_frig_filepath):
                self.report({'WARNING'}, f"FRIG file not found: {props.import_frig_filepath}")
            else:
                try:
                    Debug.log(f"Loading FRIG file: {props.import_frig_filepath}")
                    with open(props.import_frig_filepath, 'rb') as f:
                        frig_data = FrigFile.read(f)
                    
                        Debug.log("FRIG loaded successfully:")
                    Debug.log(f"  - Version: {frig_data.header.version}")
                    Debug.log(f"  - Rig units: {frig_data.header.rig_unit_count}")
                    Debug.log(f"  - Bones: {frig_data.bone_list.bone_count}")
                    Debug.log(f"  - Segments: {frig_data.header.segment_count}")
                    
                except (OSError, ValueError) as e:
                    self.report({'ERROR'}, f"Failed to load FRIG file: {str(e)}")
                    Debug.log(f"FRIG load error: {e}")
                    traceback.print_exc()
                    return {'CANCELLED'}
        else:
            # No FRIG file specified
            frig_data = None
            Debug.log("No FRIG file specified, importing without rig data")
        
        # Load track mapping file if provided
        track_mapping = None
        if props.import_mapping_filepath:
            if not os.path.exists(props.import_mapping_filepath):
                self.report({'WARNING'}, f"Track mapping file not found: {props.import_mapping_filepath}")
            else:
                try:
                    mapping_data = parse_track_mapping_file(props.import_mapping_filepath)
                    track_mapping = mapping_data.fox_to_blender
                    if track_mapping:
                        Debug.log(f"Loaded {len(track_mapping)} track mapping(s)")
                    if mapping_data.track_metadata:
                        Debug.log(f"Loaded {len(mapping_data.track_metadata)} track metadata definition(s)")
                except Exception as e:  # noqa: E722
                    self.report({'WARNING'}, f"Failed to load track mapping file: {str(e)}")
                    Debug.log(f"Track mapping load error: {e}")
        
        # Get target rig if specified
        target_rig = props.import_target_rig if props.import_target_rig else None
        
        # Import MTAR animation
        try:
            import_result = import_mtar(context, props.import_mtar_filepath, frig_data, track_mapping, props.import_gani_index, target_rig)
            
            # Extract result and imported armature
            if isinstance(import_result, tuple):
                result, imported_armature = import_result
            else:
                result = import_result
                imported_armature = None
            
            Debug.log("\n========= Finished IMPORT MTAR OPERATION =========\n")

            if result == {'FINISHED'}:
                self.report({'INFO'}, "MTAR animation imported successfully")
                
                # Bake target rig if requested
                if props.import_bake_after_import and target_rig:
                    try:
                        Debug.log("\n========= STARTING BAKE OPERATION =========\n")
                        
                        # Check if target rig has NLA tracks (common after import)
                        if target_rig.animation_data and target_rig.animation_data.nla_tracks:
                            Debug.log("Baking NLA strips...")
                            bake_result = bake_armature_nla_strips(
                                target_rig, 
                                remove_constraints=True,
                                new_action_suffix="_baked",
                                only_unmuted=True,
                                source_armature=imported_armature,
                                create_new_action=not props.delete_import_armature
                            )
                            
                            if bake_result['success']:
                                Debug.log(bake_result['message'])
                                self.report({'INFO'}, f"Bake completed: {bake_result['message']}")
                                
                                if bake_result['failed_strips']:
                                    Debug.log_warning(f"  Failed strips: {', '.join(bake_result['failed_strips'])}")
                                    self.report({'WARNING'}, f"{len(bake_result['failed_strips'])} strip(s) failed to bake")
                                # Optionally delete the imported armature after successful bake
                                if props.delete_import_armature and imported_armature and imported_armature != target_rig:
                                    try:
                                        # Unlink from any collections first
                                        for col in list(imported_armature.users_collection):
                                            col.objects.unlink(imported_armature)
                                        bpy.data.objects.remove(imported_armature, do_unlink=True)
                                        Debug.log(f"Deleted imported armature: {imported_armature.name}")
                                        self.report({'INFO'}, "Deleted imported armature after bake")
                                    except Exception as e:  # noqa: E722
                                        Debug.log_warning(f"Failed to delete imported armature: {e}")
                                        self.report({'WARNING'}, f"Failed to delete imported armature: {str(e)}")
                            else:
                                Debug.log_warning(f"Bake failed: {bake_result['message']}")
                                self.report({'WARNING'}, f"Bake failed: {bake_result['message']}")
                        
                        # Fall back to baking active action if no NLA tracks
                        elif target_rig.animation_data and target_rig.animation_data.action:
                            Debug.log("Baking active action...")
                            bake_result = bake_armature_action(
                                target_rig, 
                                target_rig.animation_data.action, 
                                remove_constraints=True,
                                create_new_action=True,
                                new_action_suffix="_baked",
                                source_armature=imported_armature
                            )
                            
                            if bake_result['success']:
                                Debug.log(bake_result['message'])
                                self.report({'INFO'}, f"Bake completed: {bake_result['message']}")
                                # Optionally delete the imported armature after successful bake
                                if props.delete_import_armature and imported_armature and imported_armature != target_rig:
                                    try:
                                        for col in list(imported_armature.users_collection):
                                            col.objects.unlink(imported_armature)
                                        bpy.data.objects.remove(imported_armature, do_unlink=True)
                                        Debug.log(f"Deleted imported armature: {imported_armature.name}")
                                        self.report({'INFO'}, "Deleted imported armature after bake")
                                    except Exception as e:  # noqa: E722
                                        Debug.log_warning(f"Failed to delete imported armature: {e}")
                                        self.report({'WARNING'}, f"Failed to delete imported armature: {str(e)}")
                            else:
                                Debug.log_warning(f"Bake failed: {bake_result['message']}")
                                self.report({'WARNING'}, f"Bake failed: {bake_result['message']}")
                        else:
                            self.report({'WARNING'}, "Target rig has no NLA tracks or active action to bake")
                            Debug.log_warning("Target rig has no NLA tracks or active action to bake")
                        
                        Debug.log("\n========= Finished BAKE OPERATION =========\n")
                        
                    except Exception as e:  # noqa: E722
                        self.report({'ERROR'}, f"Failed to bake target rig: {str(e)}")
                        Debug.log_error(f"Bake error: {e}")
                        traceback.print_exc()
                        # Continue regardless of bake failure
                
                return {'FINISHED'}
            else:
                self.report({'WARNING'}, "MTAR import completed with warnings")
                return {'FINISHED'}
                
        except (OSError, ValueError) as e:  # noqa: E722
            self.report({'ERROR'}, f"Failed to import MTAR: {str(e)}")
            Debug.log(f"MTAR import error: {e}")
            traceback.print_exc()
            return {'CANCELLED'}


class MTAR_OT_ExportAnimationToMTAR(Operator):
    """Export animation to MTAR format."""
    bl_idname = "mtar.export_animation"
    bl_label = "Export MTAR Animation"
    bl_description = "Export animation from selected armature to MTAR file"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context: Context) -> Set[str]:

        Debug.log("\n========= STARTING EXPORT MTAR OPERATION =========\n")

        props = context.scene.mtar_properties
        
        # Validate export armature
        if not props.export_armature:
            self.report({'ERROR'}, "No armature selected for export")
            return {'CANCELLED'}
        
        # Validate export filepath
        if not props.export_filepath:
            self.report({'ERROR'}, "No export file path specified")
            return {'CANCELLED'}
        
        # Load mapping file if provided
        track_segment_bone_mapping = None
        
        if props.export_mapping_filepath:
            if not os.path.exists(props.export_mapping_filepath):
                self.report({'ERROR'}, f"Mapping file not found: {props.export_mapping_filepath}")
                return {'CANCELLED'}
            
            try:
                # Get layout action to determine track indices
                layout_action = find_layout_track_action()
                
                if not layout_action:
                    self.report({'ERROR'}, "No layout track action found. Cannot determine track indices for export.")
                    Debug.log_error("  ERROR: Layout action is required for export to determine track order.")
                    return {'CANCELLED'}
                
                # Build track mapping using utility function
                track_segment_bone_mapping, missing_bones = build_track_segment_bone_mapping_from_file(
                    props.export_mapping_filepath, layout_action, props.export_armature
                )
                
                if missing_bones:
                    self.report({'WARNING'}, f"Mapping references {len(missing_bones)} bone(s) not in armature: {', '.join(missing_bones[:5])}")
                    Debug.log_warning(f"  Warning: {len(missing_bones)} bone(s) in mapping not found in armature:")
                    for bone_name in missing_bones:
                        Debug.log(f"  - {bone_name}")
                
                if track_segment_bone_mapping.get_total_track_count() == 0:
                    self.report({'ERROR'}, "No valid track mappings found. Check that fox bone names in mapping file match layout action.")
                    return {'CANCELLED'}
                
            except Exception as e:  # noqa: E722
                self.report({'ERROR'}, f"Failed to load mapping file: {str(e)}")
                Debug.log(f"Mapping file load error: {e}")
                traceback.print_exc()
                return {'CANCELLED'}
        else:
            # No mapping file provided - require it for export
            self.report({'ERROR'}, "Export mapping file is required. Please provide a track mapping file.")
            return {'CANCELLED'}
        
        try:
            # Export MTAR with layout track extracted from metadata
            result = export_mtar(
                context=context,
                filepath=props.export_filepath,
                armature=props.export_armature,
                track_segment_bone_mapping=track_segment_bone_mapping,
                use_nla=props.export_use_nla,
                use_evaluated=props.export_use_evaluated
            )
            
            Debug.log("\n========= Finished EXPORT MTAR OPERATION =========\n")

            # Result is a dict like {'FINISHED': 'message'} or {'CANCELLED': 'message'}
            if 'FINISHED' in result:
                self.report({'INFO'}, result['FINISHED'])
                return {'FINISHED'}
            else:
                self.report({'ERROR'}, result.get('CANCELLED', 'Export failed'))
                return {'CANCELLED'}
                
        except (OSError, ValueError) as e:  # noqa: E722
            self.report({'ERROR'}, f"Export failed: {str(e)}")
            traceback.print_exc()
            return {'CANCELLED'}


class MTAR_OT_SelectImportMtarFile(Operator):
    """File browser for selecting MTAR file to import."""
    bl_idname = "mtar.select_import_mtar_file"
    bl_label = "Select Import MTAR File"
    bl_options = {'INTERNAL'}
    
    filepath: StringProperty(subtype='FILE_PATH')  # type: ignore
    filter_glob: StringProperty(default="*.mtar", options={'HIDDEN'})  # type: ignore
    
    def execute(self, context: Context) -> Set[str]:
        context.scene.mtar_properties.import_mtar_filepath = self.filepath
        return {'FINISHED'}
    
    def invoke(self, context: Context, _event: Event) -> Set[str]:
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MTAR_OT_SelectFrigFile(Operator):
    """File browser for selecting FRIG file."""
    bl_idname = "mtar.select_frig_file"
    bl_label = "Select FRIG File"
    bl_options = {'INTERNAL'}
    
    filepath: StringProperty(subtype='FILE_PATH')  # type: ignore
    filter_glob: StringProperty(default="*.frig", options={'HIDDEN'})  # type: ignore
    
    def execute(self, context: Context) -> Set[str]:
        context.scene.mtar_properties.import_frig_filepath = self.filepath
        return {'FINISHED'}
    
    def invoke(self, context: Context, _event: Event) -> Set[str]:
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MTAR_OT_SelectMappingFile(Operator):
    """File browser for selecting name mapping file."""
    bl_idname = "mtar.select_mapping_file"
    bl_label = "Select Name Mapping File"
    bl_options = {'INTERNAL'}
    
    filepath: StringProperty(subtype='FILE_PATH')  # type: ignore
    filter_glob: StringProperty(default="*.txt", options={'HIDDEN'})  # type: ignore
    
    def execute(self, context: Context) -> Set[str]:
        context.scene.mtar_properties.import_mapping_filepath = self.filepath
        return {'FINISHED'}
    
    def invoke(self, context: Context, _event: Event) -> Set[str]:
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MTAR_OT_SelectExportFile(Operator):
    """File browser for selecting export MTAR file path."""
    bl_idname = "mtar.select_export_file"
    bl_label = "Select Export File"
    bl_options = {'INTERNAL'}
    
    filepath: StringProperty(subtype='FILE_PATH')  # type: ignore
    filter_glob: StringProperty(default="*.mtar", options={'HIDDEN'})  # type: ignore
    
    def execute(self, context: Context) -> Set[str]:
        context.scene.mtar_properties.export_filepath = self.filepath
        return {'FINISHED'}
    
    def invoke(self, context: Context, _event: Event) -> Set[str]:
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class MTAR_OT_SelectExportMappingFile(Operator):
    """File browser for selecting export mapping file."""
    bl_idname = "mtar.select_export_mapping_file"
    bl_label = "Select Export Mapping File"
    bl_options = {'INTERNAL'}
    
    filepath: StringProperty(subtype='FILE_PATH')  # type: ignore
    filter_glob: StringProperty(default="*.txt", options={'HIDDEN'})  # type: ignore
    
    def execute(self, context: Context) -> Set[str]:
        context.scene.mtar_properties.export_mapping_filepath = self.filepath
        return {'FINISHED'}
    
    def invoke(self, context: Context, _event: Event) -> Set[str]:
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}
