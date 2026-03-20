"""
Blender operators for MTAR import functionality.
"""
import os
from typing import Set, Optional, List, Dict, Tuple
import traceback

import bpy
from bpy.types import Operator, Context

from .py_core.core_logging import Debug

from .py_utilities.utilities_blender_state import nla_tweak_guard
from .py_utilities.utilities_parsing import parse_index_selection
from .py_utilities.utilities_blender_animation import try_find_layout_track_action

from .py_fox.fox_frig_types import FrigFile

from .py_foxwrap.foxwrap_mapping import parse_track_mapping_file, TrackMappingData, BoneParameters
from .py_foxwrap.foxwrap_mtar_reader import MtarReader

from .py_tools.tools_hash_generator import build_gani_hash_dictionary
from .py_tools.tools_mapping import generate_mapping_template
from .py_tools.tools_mtar_importer import import_mtar
from .py_tools.tools_animation_bake import bake_constraints_and_decimate_fcurves
from .blender_properties import get_effective_import_bake_decimate_error


class MTAR_OT_GenerateTrackMappingTemplateFile(Operator):
    """Generate a barebone track mapping file from FRIG skeleton structure and MTAR animation data."""
    bl_idname = "mtar.generate_track_mapping_template_file"
    bl_label = "Generate Track Mapping Template File"
    bl_description = "Create a track mapping file template from FRIG skeleton structure and MTAR animation data as starting point for a custom mapping file."
    bl_options = {'REGISTER'}
    
    def execute(self, context: Context) -> Set[str]:
        Debug.start_timer("Generate Mapping Template")
        props = context.scene.mtar_properties
        import_props = props.import_props
        
        frig_path = bpy.path.abspath(import_props.frig_filepath) if import_props.frig_filepath else None
        mtar_path = bpy.path.abspath(import_props.mtar_filepath) if import_props.mtar_filepath else None
        
        try:
            output = generate_mapping_template(frig_path, mtar_path)
            Debug.report_and_log(self,'INFO', f"Mapping file created: {output}")
            props.mapping_filepath = output
            Debug.stop_timer("Generate Mapping Template")
            return {'FINISHED'}
        except Exception as e:
            Debug.report_and_log(self,'ERROR', f"Failed to generate mapping file: {e}")
            traceback.print_exc()
            Debug.stop_timer("Generate Mapping Template")
            return {'CANCELLED'}

class MTAR_OT_ImportAnimationFromMTAR(Operator):
    """Import MTAR animation with FRIG rig data."""
    bl_idname = "mtar.import_animation"
    bl_label = "Import MTAR Animation"
    bl_description = "Import animation from MTAR file using FRIG rig structure"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context: Context) -> Set[str]:

        Debug.log("========= STARTING IMPORT MTAR OPERATION =========")

        # Start operator-level timer
        Debug.start_timer("Import Operator")

        props = context.scene.mtar_properties
        import_props = props.import_props
        
        # Validate MTAR file path
        if not import_props.mtar_filepath:
            Debug.report_and_log(self, 'ERROR', "No MTAR file selected")
            Debug.stop_timer("Import Operator")
            return {'CANCELLED'}
        
        mtar_filepath_abs = bpy.path.abspath(import_props.mtar_filepath)
        if not os.path.exists(mtar_filepath_abs):
            Debug.report_and_log(self, 'ERROR', f"MTAR file not found: {mtar_filepath_abs}")
            Debug.stop_timer("Import Operator")
            return {'CANCELLED'}
        
        # Load FRIG file if provided
        frig_data: Optional[FrigFile] = None
        if import_props.frig_filepath:
            frig_filepath_abs = bpy.path.abspath(import_props.frig_filepath)
            if not os.path.exists(frig_filepath_abs):
                Debug.report_and_log(self, 'WARNING', f"FRIG file not found: {frig_filepath_abs}")
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
                    Debug.report_and_log(self, 'ERROR', f"Failed to load FRIG file: {str(e)}")
                    traceback.print_exc()
                    Debug.stop_timer("Import Operator")
                    return {'CANCELLED'}
        else:
            # No FRIG file specified
            frig_data = None
            Debug.log("No FRIG file specified, importing without rig data")
        
        # Load track mapping file if provided
        track_mapping: Optional[Dict[str, BoneParameters]] = None
        blender_to_fox_map: Optional[Dict[str, str]] = None
        transform_constraints = None
        if props.mapping_filepath:
            mapping_filepath_abs = bpy.path.abspath(props.mapping_filepath)
            if not os.path.exists(mapping_filepath_abs):
                Debug.report_and_log(self, 'WARNING', f"Track mapping file not found: {mapping_filepath_abs}")
            else:
                try:
                    mapping_data: TrackMappingData = parse_track_mapping_file(mapping_filepath_abs)
                    track_mapping = mapping_data.fox_to_blender
                    blender_to_fox_map = mapping_data.blender_to_fox_names
                    transform_constraints = mapping_data.transform_constraints or None
                    if track_mapping:
                        Debug.log(f"Loaded {len(track_mapping)} track mapping(s)")
                    if transform_constraints:
                        Debug.log(f"Loaded {len(transform_constraints)} transform constraint(s)")
                except Exception as e:  # noqa: E722
                    Debug.report_and_log(self, 'WARNING', f"Failed to load track mapping file: {str(e)}")
        
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
                Debug.report_and_log(self, 'ERROR', f"Invalid GANI selection: {e}")
                Debug.stop_timer("Import Operator")
                return {'CANCELLED'}
            except Exception as e:
                Debug.report_and_log(self, 'ERROR', f"Error parsing GANI selection: {e}")
                traceback.print_exc()
                Debug.stop_timer("Import Operator")
                return {'CANCELLED'}
        
        # Initialize progress bar
        wm: bpy.types.WindowManager = context.window_manager
        wm.progress_begin(0, 100)
        # Initialize UI progress state
        Debug.update_progress(0, "Starting import...")

        # Build GANI hash dictionary on-the-fly from mtar_dictionary.txt using Python CityHash
        gani_hash_dict = None
        if import_props.import_use_hash_dictionary:
            addon_dir = os.path.dirname(os.path.abspath(__file__))
            dict_path = os.path.join(addon_dir, 'dic', 'path64', 'mtar_dictionary.txt')
            Debug.start_timer("Build GANI hash dict (import)")
            gani_hash_dict = build_gani_hash_dictionary(dict_path)
            Debug.stop_timer("Build GANI hash dict (import)")
            Debug.log(f"Built GANI hash dictionary: {len(gani_hash_dict)} entries")

        # NLA tweak mode guard — AnimData.action is read-only while use_tweak_mode is True.
        with nla_tweak_guard(custom_rig):
            try:
                with Debug.busy_cursor():
                    # Import MTAR animation
                    import_result: Tuple[Set[str], Optional[bpy.types.Object]] = import_mtar(context, mtar_filepath_abs, frig_data, track_mapping, gani_indices, custom_rig, import_props.strip_padding, gani_hash_dict=gani_hash_dict, transform_constraints=transform_constraints)
                    
                    # Extract result and imported armature
                    if isinstance(import_result, tuple):
                        result: Set[str]
                        imported_armature: Optional[bpy.types.Object]
                        result, imported_armature = import_result
                    else:
                        result = import_result
                        imported_armature = None
                    
                    Debug.log("\n========= Finished IMPORT MTAR OPERATION =========\n")
                    if result != {'FINISHED'}:
                        Debug.report_and_log(self, 'WARNING', "MTAR import completed with warnings")
                        Debug.stop_timer("Import Operator")
                        return {'FINISHED'}
                    
                    Debug.report_and_log(self, 'INFO', "MTAR animation imported successfully")
                    
                    # Custom rig post processing
                    if custom_rig:
                        layout_action = try_find_layout_track_action()

                        # Bake custom rig if requested + decimation
                        bake_result = None  # ensure defined even when try raises
                        if import_props.import_bake_constraints:
                            try:
                                Debug.log("\n========= STARTING BAKE OPERATION =========\n")
                                Debug.update_progress(75, "Baking...")
                                Debug.start_timer("Bake + Decimate")

                                # Delegate constraint-baking to shared utility (decimation deferred until after root motion)
                                bake_result = bake_constraints_and_decimate_fcurves(
                                    rig_armature=custom_rig,
                                    source_armature=imported_armature,
                                    create_new_action=not import_props.delete_import_armature,
                                    new_action_suffix="_baked",
                                    remove_constraints=True,
                                    delete_import_armature=import_props.delete_import_armature,
                                    bake_decimate_fcurve_error=get_effective_import_bake_decimate_error(import_props),
                                    decimate_skip_types=getattr(import_props, 'import_bake_decimate_skip_types', ''),
                                    layout_action=layout_action,
                                    blender_to_fox_map=blender_to_fox_map,
                                )
                            except Exception as e:
                                Debug.report_and_log(self, 'ERROR', f"Failed to bake custom rig: {str(e)}")
                                traceback.print_exc()
                            finally:
                                Debug.stop_timer("Bake + Decimate")
                                if bake_result and bake_result.get('success'):
                                    Debug.log(f"Bake result: {bake_result.get('message')}")
                                    Debug.log(f"  Decimated {bake_result.get('fcurves_decimated', 0)} FCurves")
                                else:
                                    Debug.report_and_log(self, 'WARNING', f"Bake failed: {bake_result.get('message')}")

                    # Ensure Blender refreshes pose/constraint evaluation after import.
                    try:
                        context.scene.frame_set(context.scene.frame_current)
                    except Exception:
                        pass

                    Debug.stop_timer("Import Operator")
                    return {'FINISHED'}

            except (OSError, ValueError) as e:  # noqa: E722
                Debug.report_and_log(self, 'ERROR', f"Failed to import MTAR: {str(e)}")
                traceback.print_exc()
                Debug.stop_timer("Import Operator")
                return {'CANCELLED'}
            finally:
                wm.progress_end()
                Debug.update_progress(0, "")