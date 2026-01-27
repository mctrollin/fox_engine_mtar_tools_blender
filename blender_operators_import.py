"""
Blender operators for MTAR import functionality.
"""
import os
import io
from typing import Set, Optional, List, Dict, Any, Tuple
import traceback

import bpy
from bpy.types import Operator, Context, Event
from bpy.props import StringProperty

from .py_utilities.utilities_logging import Debug, update_progress
from .py_utilities.utilities_rig_hash import unhash_rig_type
from .py_utilities.utilities_parsing import parse_index_selection

from .py_fox.fox_mtar_types import MtarHeader
from .py_fox.fox_frig_types import FrigFile, RigUnitDef

from .py_foxwrap.foxwrap_misc_import import CommonInfo
from .py_foxwrap.foxwrap_mapping import parse_track_mapping_file, TrackMappingData, BoneParameters
from .py_foxwrap.foxwrap_metadata import get_segments_for_track_type
from .py_foxwrap.foxwrap_mtar_reader import MtarReader

from .mtar_importer import import_mtar
from .py_tools.tools_blender_animation_bake import bake_armature_action, bake_armature_nla_strips


def clear_armature_transforms(armature: bpy.types.Object) -> bool:
    """Clear all pose transforms from an armature.
    
    Args:
        armature: Armature object to clear transforms from
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Make sure the armature is selected and in the scene
        for obj in bpy.context.scene.objects:
            obj.select_set(False)
        armature.select_set(True)
        bpy.context.view_layer.objects.active = armature
        
        # Enter pose mode
        bpy.ops.object.mode_set(mode='POSE')
        
        # Select all bones
        bpy.ops.pose.select_all(action='SELECT')
        
        # Clear all transforms
        bpy.ops.pose.transforms_clear()
        
        # Return to object mode
        bpy.ops.object.mode_set(mode='OBJECT')
        
        return True
    except Exception as e:  # noqa: E722
        Debug.log_warning(f"Failed to clear transforms from armature: {e}")
        return False


def delete_imported_armature(imported_armature: Optional[bpy.types.Object], 
                            custom_rig: Optional[bpy.types.Object] = None) -> bool:
    """Delete an imported armature after bake if requested.
    
    Args:
        imported_armature: Armature to delete
        custom_rig: custom rig (if same as imported, skip deletion)
        
    Returns:
        True if deletion successful or not needed, False if failed
    """
    if not imported_armature or imported_armature == custom_rig:
        return True
    
    try:
        Debug.log(f"Deleting imported armature: {imported_armature.name}")
        for col in list(imported_armature.users_collection):
            col.objects.unlink(imported_armature)
        bpy.data.objects.remove(imported_armature, do_unlink=True)
        return True
    except Exception as e:  # noqa: E722
        Debug.log_warning(f"Failed to delete imported armature: {e}")
        return False


def handle_bake_result(bake_result: dict, custom_rig: bpy.types.Object, 
                      imported_armature: Optional[bpy.types.Object],
                      props: bpy.types.Scene, operator: Operator) -> None:
    """Handle post-bake cleanup and reporting for both NLA and action bakes.
    
    Args:
        bake_result: Result dict from bake function with 'success', 'message' keys
        custom_rig: Target armature that was baked
        imported_armature: Optional imported armature to delete after bake
        props: Scene properties with delete_import_armature flag
        operator: Operator instance for reporting
    """
    # Extract failed_strips from bake_result if present (NLA bakes)
    failed_strips: Optional[List[str]] = bake_result.get('failed_strips') if isinstance(bake_result, dict) else None

    if bake_result['success']:
        Debug.log(bake_result['message'])
        operator.report({'INFO'}, f"Bake completed: {bake_result['message']}")
        
        # Report failed strips if any (for NLA bakes)
        if failed_strips:
            Debug.log_warning(f"  Failed strips: {', '.join(failed_strips)}")
            operator.report({'WARNING'}, f"{len(failed_strips)} strip(s) failed to bake")
        
        # Clear transforms from custom rig after successful bake
        if clear_armature_transforms(custom_rig):
            Debug.log("Transforms cleared from custom rig")
            operator.report({'INFO'}, "Cleared transforms from custom rig")
        else:
            operator.report({'WARNING'}, "Could not clear transforms from custom rig")
        
        # Delete imported armature if requested
        import_props = props.import_props
        if import_props.delete_import_armature:
            if delete_imported_armature(imported_armature, custom_rig):
                operator.report({'INFO'}, "Deleted imported armature after bake")
            else:
                operator.report({'WARNING'}, "Could not delete imported armature")
    else:
        Debug.log_warning(f"Bake failed: {bake_result['message']}")
        operator.report({'WARNING'}, f"Bake failed: {bake_result['message']}")

class MTAR_OT_GenerateTrackMappingTemplateFile(Operator):
    """Generate a barebone track mapping file from FRIG skeleton structure and MTAR animation data."""
    bl_idname = "mtar.generate_track_mapping_template_file"
    bl_label = "Generate Track Mapping Template File"
    bl_description = "Create a track mapping file template from FRIG skeleton structure and MTAR animation data as starting point for a custom mapping file."
    bl_options = {'REGISTER'}
    
    def execute(self, context: Context) -> Set[str]:
        props = context.scene.mtar_properties
        import_props = props.import_props
        
        # Validate FRIG file path
        if not import_props.frig_filepath:
            self.report({'ERROR'}, "No FRIG file selected")
            return {'CANCELLED'}
        
        if not os.path.exists(import_props.frig_filepath):
            self.report({'ERROR'}, f"FRIG file not found: {import_props.frig_filepath}")
            return {'CANCELLED'}
        
        # Validate MTAR file path (optional but recommended)
        mtar_data: Optional[Dict[str, Any]] = None
        mtar_filepath_abs = bpy.path.abspath(import_props.mtar_filepath) if import_props.mtar_filepath else ""
        if mtar_filepath_abs and os.path.exists(mtar_filepath_abs):
            try:
                Debug.log(f"Reading MTAR file: {mtar_filepath_abs}")
                # Read MTAR to get CommonInfo with layout track
                with open(import_props.mtar_filepath, 'rb') as f:
                    file_data: bytes = f.read()
                    br: io.BytesIO = io.BytesIO(file_data)
                    header: MtarHeader = MtarHeader.read(br)
                    
                    if header.common_info_offset != 0:
                        mtar_data = {
                            'header': header,
                            'common_info': CommonInfo.read(br, header)
                        }
                        Debug.log(f"MTAR CommonInfo loaded: {header.track_count} tracks, {header.segment_count} segments")
            except (OSError, ValueError) as e:
                Debug.log_warning(f"  Warning: Could not read MTAR file: {e}")
                # Continue without MTAR data
        elif import_props.mtar_filepath:
            Debug.log_warning(f"  Warning: MTAR file not found: {mtar_filepath_abs}")
        
        try:
            # Read FRIG file
            Debug.log(f"Reading FRIG file: {import_props.frig_filepath}")
            with open(import_props.frig_filepath, 'rb') as f:
                frig: FrigFile = FrigFile.read(f)
            
            if not frig or not frig.rig_def:
                self.report({'ERROR'}, "Failed to read FRIG rig data")
                return {'CANCELLED'}
            
            # Generate output filepath
            frig_dir: str = os.path.dirname(import_props.frig_filepath)
            frig_name: str = os.path.splitext(os.path.basename(import_props.frig_filepath))[0]
            output_path: str = os.path.join(frig_dir, f"{frig_name}_track_mapping.txt")
            
            # Check if file already exists
            if os.path.exists(output_path):
                self.report({'WARNING'}, f"Mapping file already exists: {output_path}")
                return {'CANCELLED'}
            
            # Generate mapping file content
            lines: List[str] = []
            lines.append("# Track Mapping File")
            lines.append(f"# Generated from: {os.path.basename(import_props.frig_filepath)}")
            if mtar_data:
                lines.append(f"# MTAR reference: {os.path.basename(import_props.mtar_filepath)}")
            lines.append("#")
            lines.append("# Edit this file to customize bone mappings and transformations")
            lines.append("# See example_track_mapping.txt for detailed documentation")
            lines.append("")
            
            # Get track units from rig_def
            if frig.rig_def and frig.rig_def.unit_defs:
                unit_defs: List[RigUnitDef] = frig.rig_def.unit_defs
                
                # Get layout track units from MTAR if available
                layout_track_units: Optional[List[Any]] = None
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
                            track_hash_int: int = track_hash.to_int() if hasattr(track_hash, 'to_int') else int(track_hash)
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
                            segments: List[Dict[str, Any]] = get_segments_for_track_type(track_type)
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
                            count: int = len(segments_shorthand)
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
            import_props.mapping_filepath = output_path
            
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
        import_props = props.import_props
        execution_props = props.execution_props
        
        # Validate MTAR file path
        if not import_props.mtar_filepath:
            self.report({'ERROR'}, "No MTAR file selected")
            return {'CANCELLED'}
        
        mtar_filepath_abs = bpy.path.abspath(import_props.mtar_filepath)
        if not os.path.exists(mtar_filepath_abs):
            self.report({'ERROR'}, f"MTAR file not found: {mtar_filepath_abs}")
            return {'CANCELLED'}
        
        # Load FRIG file if provided
        frig_data: Optional[FrigFile] = None
        if import_props.frig_filepath:
            frig_filepath_abs = bpy.path.abspath(import_props.frig_filepath)
            if not os.path.exists(frig_filepath_abs):
                self.report({'WARNING'}, f"FRIG file not found: {frig_filepath_abs}")
            else:
                try:
                    Debug.log(f"Loading FRIG file: {frig_filepath_abs}")
                    with open(frig_filepath_abs, 'rb') as f:
                        frig_data = FrigFile.read(f)
                    
                        Debug.log("FRIG loaded successfully:")
                    Debug.log(f"  - Version: {frig_data.header.version}")
                    Debug.log(f"  - Rig units: {frig_data.header.rig_unit_count}")
                    Debug.log(f"  - Bones: {frig_data.bone_list.bone_count}")
                    Debug.log(f"  - Segments: {frig_data.header.segment_count}")
                    
                except (OSError, ValueError) as e:
                    self.report({'ERROR'}, f"Failed to load FRIG file: {str(e)}")
                    Debug.log_error(f"FRIG load error: {e}")
                    traceback.print_exc()
                    return {'CANCELLED'}
        else:
            # No FRIG file specified
            frig_data = None
            Debug.log("No FRIG file specified, importing without rig data")
        
        # Load track mapping file if provided
        track_mapping: Optional[Dict[str, BoneParameters]] = None
        if import_props.mapping_filepath:
            mapping_filepath_abs = bpy.path.abspath(import_props.mapping_filepath)
            if not os.path.exists(mapping_filepath_abs):
                self.report({'WARNING'}, f"Track mapping file not found: {mapping_filepath_abs}")
            else:
                try:
                    mapping_data: TrackMappingData = parse_track_mapping_file(mapping_filepath_abs)
                    track_mapping = mapping_data.fox_to_blender
                    if track_mapping:
                        Debug.log(f"Loaded {len(track_mapping)} track mapping(s)")
                    if mapping_data.track_metadata:
                        Debug.log(f"Loaded {len(mapping_data.track_metadata)} track metadata definition(s)")
                except Exception as e:  # noqa: E722
                    self.report({'WARNING'}, f"Failed to load track mapping file: {str(e)}")
                    Debug.log_error(f"Track mapping load error: {e}")
        
        # Get custom rig if specified
        custom_rig: Optional[bpy.types.Object] = import_props.custom_rig if import_props.custom_rig else None
        
        # Parse GANI indices from user input
        gani_indices: Optional[List[int]] = None
        if import_props.gani_indices_str.strip():
            try:
                # Get total GANI count from MTAR header
                reader = MtarReader(mtar_filepath_abs)
                header_info = reader.get_header_info()
                
                # Parse selection with validation
                gani_indices = parse_index_selection(import_props.gani_indices_str, header_info.file_count)
                Debug.log(f"Parsed GANI selection: {gani_indices}")
            except ValueError as e:
                self.report({'ERROR'}, f"Invalid GANI selection: {e}")
                return {'CANCELLED'}
            except Exception as e:
                self.report({'ERROR'}, f"Error parsing GANI selection: {e}")
                Debug.log_error(f"GANI selection parse error: {e}")
                traceback.print_exc()
                return {'CANCELLED'}
        
        # Initialize progress bar
        wm: bpy.types.WindowManager = context.window_manager
        wm.progress_begin(0, 100)
        execution_props.operation_type = 'IMPORT'
        
        # Import MTAR animation
        try:
            with Debug.busy_cursor():
                import_result: Tuple[Set[str], Optional[bpy.types.Object]] = import_mtar(context, mtar_filepath_abs, frig_data, track_mapping, gani_indices, custom_rig, import_props.strip_padding)
                
                # Extract result and imported armature
                if isinstance(import_result, tuple):
                    result: Set[str]
                    imported_armature: Optional[bpy.types.Object]
                    result, imported_armature = import_result
                else:
                    result = import_result
                    imported_armature = None
                
                Debug.log("\n========= Finished IMPORT MTAR OPERATION =========\n")

                if result == {'FINISHED'}:
                    self.report({'INFO'}, "MTAR animation imported successfully")
                    
                    # Bake custom rig if requested
                    if import_props.bake_after_import and custom_rig:
                        try:
                            Debug.log("\n========= STARTING BAKE OPERATION =========\n")
                            update_progress(75, "Baking...")
                            
                            # Check if custom rig has NLA tracks (common after import)
                            if custom_rig.animation_data and custom_rig.animation_data.nla_tracks:
                                Debug.log("Baking NLA strips...")
                                bake_result: Dict[str, Any] = bake_armature_nla_strips(
                                    custom_rig, 
                                    remove_constraints=True,
                                    new_action_suffix="_baked",
                                    only_unmuted=True,
                                    source_armature=imported_armature,
                                    create_new_action=not import_props.delete_import_armature
                                )
                                
                                handle_bake_result(bake_result, custom_rig, imported_armature, props, self)
                            
                            # Fall back to baking active action if no NLA tracks
                            elif custom_rig.animation_data and custom_rig.animation_data.action:
                                Debug.log("Baking active action...")
                                bake_result = bake_armature_action(
                                    custom_rig, 
                                    custom_rig.animation_data.action, 
                                    remove_constraints=True,
                                    create_new_action=True,
                                    new_action_suffix="_baked",
                                    source_armature=imported_armature
                                )
                                
                                handle_bake_result(bake_result, custom_rig, imported_armature, props, self)
                            else:
                                self.report({'WARNING'}, "custom rig has no NLA tracks or active action to bake")
                                Debug.log_warning("custom rig has no NLA tracks or active action to bake")
                            
                            Debug.log("\n========= Finished BAKE OPERATION =========\n")
                            
                        except Exception as e:  # noqa: E722
                            self.report({'ERROR'}, f"Failed to bake custom rig: {str(e)}")
                            Debug.log_error(f"Bake error: {e}")
                            traceback.print_exc()
                            # Continue regardless of bake failure
                    
                    # Clear all transforms from custom rig after import/bake
                    if custom_rig:
                        update_progress(95, "Cleaning up...")
                        if clear_armature_transforms(custom_rig):
                            Debug.log("Transforms cleared from custom rig")
                            self.report({'INFO'}, "Cleared transforms from custom rig")
                        else:
                            self.report({'WARNING'}, "Could not clear transforms from custom rig")
                    
                    update_progress(100, "Done")
                    return {'FINISHED'}
                else:
                    self.report({'WARNING'}, "MTAR import completed with warnings")
                    return {'FINISHED'}
        
        except (OSError, ValueError) as e:  # noqa: E722
            self.report({'ERROR'}, f"Failed to import MTAR: {str(e)}")
            Debug.log_error(f"MTAR import error: {e}")
            traceback.print_exc()
            return {'CANCELLED'}
        finally:
            wm.progress_end()
            execution_props.operation_type = 'NONE'
            update_progress(0, "")


class MTAR_OT_ValidateHashGeneratorExe(Operator):
    """Validate hash generator executable path."""
    bl_idname = "mtar.validate_hash_generator_exe"
    bl_label = "Validate Executable"
    bl_description = "Validate that the executable path is valid and accessible"
    
    def execute(self, context: Context) -> Set[str]:
        """Execute the validation."""
        from .py_tools.tools_hash_generator import validate_executable_path
        
        # Read exe path from main scene properties (no fallback)
        scene: bpy.types.Scene = context.scene
        if not hasattr(scene, 'mtar_properties') or not scene.mtar_properties.settings_props.hash_generator_exe_path:
            self.report({'ERROR'}, "Executable path not configured in MTAR Settings")
            return {'CANCELLED'}
        exe_path: str = scene.mtar_properties.settings_props.hash_generator_exe_path
        
        is_valid: bool
        error_msg: str
        is_valid, error_msg = validate_executable_path(exe_path)
        
        if is_valid:
            self.report({'INFO'}, "Executable path is valid")
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, f"Invalid executable: {error_msg}")
            return {'CANCELLED'}