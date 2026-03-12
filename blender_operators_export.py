"""
Blender operators for MTAR export functionality.
"""
import os
import traceback
from typing import Set, List
from collections import defaultdict

import bpy
from bpy.types import Operator, Context

from .py_utilities.utilities_logging import Debug
from .py_utilities.utilities_blender_state import nla_tweak_guard
from .py_utilities.utilities_blender_armature import (
    auto_detect_motion_points_armature,
    auto_detect_shader_nodes_armature,
    auto_detect_aux_armatures,
)

from .py_foxwrap.foxwrap_misc_export import TrackSegmentBoneMapping
from .py_foxwrap.foxwrap_mapping import parse_segment_suffix
from .py_foxwrap.foxwrap_mapping import parse_track_mapping_file
from .py_foxwrap.foxwrap_metadata import iter_track_properties

from .py_tools.tools_mtar_exporter import export_mtar, try_find_layout_track_action


def build_track_segment_bone_mapping_from_file(mapping_filepath: str,
                                               layout_action: bpy.types.Action,
                                               armature: bpy.types.Object
                                              ) -> tuple[TrackSegmentBoneMapping, List[str]]:
    """Build TrackSegmentBoneMapping from mapping file and layout action.
    
    Args:
        mapping_filepath: Path to the track mapping file
        layout_action: Layout action containing track structure metadata
        armature: Armature object to validate bone names against
        
    Returns:
        Tuple of (TrackSegmentBoneMapping, missing_bones_list)
    """
    
    Debug.log(f"Loading bone mapping from: {mapping_filepath}")
    mapping_data = parse_track_mapping_file(mapping_filepath)
    if mapping_data.fox_to_blender:
        Debug.log(f"Loaded {len(mapping_data.fox_to_blender)} fox-to-blender bone mapping(s)")
    
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
    # First, group bones by their base track name
    track_segments = defaultdict(list)  # base_track_name -> [(segment_idx, blender_bone_name, BoneParameters)]
    
    Debug.log(f"  Processing {len(mapping_data.fox_to_blender)} fox-to-blender mapping(s) from file...")
    
    # Use fox_to_blender to preserve all Fox bone names (multiple Fox bones can map to same Blender bone)
    for fox_name, bone_params in mapping_data.fox_to_blender.items():
        blender_bone_name = mapping_data.fox_to_blender_names[fox_name]
        
        # Check if this bone exists in the armature
        if blender_bone_name not in armature.data.bones:
            missing_bones.append(blender_bone_name)
            continue
        
        # Multi-segment tracks have numeric suffixes (Option D naming).
        base_track_name, segment_idx = parse_segment_suffix(fox_name)
        # For single-segment tracks parse_segment_suffix returns index -1; clamp to 0
        if segment_idx < 0:
            segment_idx = 0
        
        # Store segment info with BoneParameters object
        track_segments[base_track_name].append((segment_idx, blender_bone_name, bone_params))
    
    # Now add them to the mapping object in the correct track order
    for track_name, track_idx in sorted(track_name_to_idx.items(), key=lambda x: x[1]):
        if track_name in track_segments:
            # Sort segments by index
            segments = sorted(track_segments[track_name], key=lambda x: x[0])
            for seg_idx, blender_bone, bone_params in segments:
                track_segment_bone_mapping.set_segment_mapping(track_idx, seg_idx, blender_bone, bone_params)
                # Debug.log(f"    Mapped Track {track_idx} Seg {seg_idx}: {track_name} -> {blender_bone}")
        else:
            Debug.log_warning(f"    Warning: No mapping found for track '{track_name}' (index {track_idx})")
            
    return track_segment_bone_mapping, missing_bones


class MTAR_OT_ExportAnimationToMTAR(Operator):
    """Export animation to MTAR format."""
    bl_idname = "mtar.export_animation"
    bl_label = "Export MTAR Animation"
    bl_description = "Export animation from selected armature to MTAR file"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context: Context) -> Set[str]:

        Debug.log("\n========= STARTING EXPORT MTAR OPERATION =========\n")

        # Start operator-level timer
        Debug.start_timer("Export Operator")

        props = context.scene.mtar_properties
        export_props = props.export_props
        execution_props = props.execution_props
        
        # Validate export armature
        if not export_props.armature:
            Debug.report_and_log(self, 'ERROR', "No armature selected for export")
            Debug.stop_timer("Export Operator")
            return {'CANCELLED'}
        
        # Validate export filepath
        if not export_props.filepath:
            Debug.report_and_log(self, 'ERROR', "No export file path specified")
            Debug.stop_timer("Export Operator")
            return {'CANCELLED'}
        
        # Load mapping file if provided
        track_segment_bone_mapping = None
        
        if export_props.mapping_filepath:
            mapping_filepath_abs = bpy.path.abspath(export_props.mapping_filepath)
            if not os.path.exists(mapping_filepath_abs):
                Debug.report_and_log(self, 'ERROR', f"Mapping file not found: {mapping_filepath_abs}")
                Debug.stop_timer("Export Operator")
                return {'CANCELLED'}
            
            try:
                # Get layout action to determine track indices
                layout_action = try_find_layout_track_action()
                
                if not layout_action:
                    Debug.report_and_log(self, 'ERROR', "No layout track action found. Cannot determine track indices for export.")
                    Debug.stop_timer("Export Operator")
                    return {'CANCELLED'}
                
                # Build track mapping using utility function
                track_segment_bone_mapping, missing_bones = build_track_segment_bone_mapping_from_file(
                    mapping_filepath=mapping_filepath_abs,
                    layout_action=layout_action,
                    armature=export_props.armature
                )
                
                if missing_bones:
                    Debug.report_and_log(self, 'WARNING', f"Mapping references {len(missing_bones)} bone(s) not in armature: {', '.join(missing_bones[:5])}")
                    Debug.log_warning(f"  Warning: {len(missing_bones)} bone(s) in mapping not found in armature:")
                    for bone_name in missing_bones:
                        Debug.log(f"  - {bone_name}")
                
                if track_segment_bone_mapping.get_total_track_count() == 0:
                    Debug.report_and_log(self, 'ERROR', "No valid track mappings found. Check that fox bone names in mapping file match layout action.")
                    Debug.stop_timer("Export Operator")
                    return {'CANCELLED'}
                
            except Exception as e:  # noqa: E722
                Debug.report_and_log(self, 'ERROR', f"Failed to load mapping file: {str(e)}")
                traceback.print_exc()
                Debug.stop_timer("Export Operator")
                return {'CANCELLED'}
        else:
            Debug.log("No mapping file provided — synthetic mapping will be derived from armature bone order.")
        
        # Initialize progress bar
        wm = context.window_manager
        wm.progress_begin(0, 100)
        execution_props.operation_type = 'EXPORT'
        # Initialize UI progress state
        Debug.update_progress(0, "Starting export...")

        # NLA tweak mode guard — AnimData.action is read-only while use_tweak_mode is True.
        # detect auxiliaries so they can be included in the guard
        mp_arm, sh_arm = auto_detect_aux_armatures(export_props.armature)
        with nla_tweak_guard(export_props.armature, mp_arm, sh_arm):
            try:
                with Debug.busy_cursor():
                    # Export MTAR with layout track extracted from metadata
                    export_filepath_abs = bpy.path.abspath(export_props.filepath)

                    result = export_mtar(
                        context=context,
                        filepath=export_filepath_abs,
                        armature=export_props.armature,
                        track_segment_bone_mapping=track_segment_bone_mapping,
                        use_nla=export_props.use_nla
                    )
                    
                    Debug.log("\n========= Finished EXPORT MTAR OPERATION =========\n")

                    # Result is a dict like {'FINISHED': 'message'} or {'CANCELLED': 'message'}
                    if 'FINISHED' in result:
                        Debug.report_and_log(self, 'INFO', result['FINISHED'])
                        Debug.update_progress(100, "Done")
                        Debug.stop_timer("Export Operator")
                        return {'FINISHED'}
                    else:
                        Debug.report_and_log(self, 'ERROR', result.get('CANCELLED', 'Export failed'))
                        Debug.stop_timer("Export Operator")
                        return {'CANCELLED'}
            
            except (OSError, ValueError) as e:  # noqa: E722
                Debug.report_and_log(self, 'ERROR', f"Export failed: {str(e)}")
                traceback.print_exc()
                Debug.stop_timer("Export Operator")
                return {'CANCELLED'}
            finally:
                wm.progress_end()
                execution_props.operation_type = 'NONE'
                Debug.update_progress(0, "")

