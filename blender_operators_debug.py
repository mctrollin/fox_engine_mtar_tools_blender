"""
Debug operators for MTAR tools - transform inspection and external hash generator utilities.

This module contains operator classes for debugging and inspecting transforms,
as well as interfacing with the external hash generator executable.
"""

# pyright: reportInvalidTypeForm=false

from typing import Set
import math
import os
import re
import traceback

import bpy
from bpy.types import Operator, Context
from bpy.props import StringProperty

from .py_utilities.utilities_transforms import get_world_space_transform, get_local_space_transform
from .py_core.core_logging import Debug
from .py_utilities.utilities_debug import create_or_update_dummy_object
from .py_utilities.utilities_blender_animation import assign_action_to_datablock, try_find_layout_track_action, remove_action_from_datablock
from .py_utilities.utilities_fcurve_processing import decimate_import_fcurves_to_bezier, debug_setup_graph_context_for_manual_test
from .py_foxwrap.foxwrap_metadata import build_blender_bone_decimation_skip_map
from .py_utilities.utilities_hashing_cityhash import (
    hash_file_name,
    hash_file_name_legacy,
    hash_file_extension,
    hash_file_name_with_ext,
    strcode32_path,
)
# Import bake helpers from tools module (keep top-level to prevent import loops)
from .py_tools.tools_animation_bake import (
    bake_armature_constraints_to_keyframes,
    remove_bone_constraints,
    get_bones_with_keyframes,
    bake_constraints_and_decimate_fcurves,
    clear_armature_transforms,
)
from .blender_properties import get_effective_import_bake_decimate_error
from .py_tools.tools_hash_generator import hash_filename_all_modes_by_external_generator, validate_executable_path_by_external_generator


# Transform Debug Panel Operators ##################################################################

class MTAR_OT_InspectWorldSpaceTransform(Operator):
    """Inspect world space transform for a bone at the current frame."""
    bl_idname = "mtar.inspect_world_space_transform"
    bl_label = "Inspect World Space"
    bl_description = "Get world space transform (relative to scene origin 0,0,0)"
    
    def execute(self, context: Context) -> set:
        """Execute the inspection."""
        props = context.scene.mtar_debug_transform_properties
        
        # Validate inputs
        if not props.debug_armature:
            Debug.report_and_log(self, 'ERROR', "No armature selected")
            return {'FINISHED'}
        
        if not props.debug_bone_name:
            Debug.report_and_log(self, 'ERROR', "No bone selected")
            return {'FINISHED'}
        
        armature = props.debug_armature
        bone_name = props.debug_bone_name
        frame = context.scene.frame_current
        
        # Validate bone exists
        if bone_name not in armature.pose.bones:
            Debug.report_and_log(self, 'ERROR', f"Bone '{bone_name}' not found in armature")
            return {'FINISHED'}
        
        try:
            # Set frame explicitly (as it's no longer done inside transform getters)
            context.scene.frame_set(frame)
            
            # Get world space transform
            location, rotation = get_world_space_transform(
                armature, bone_name, frame,
                space_bone=None
            )
            
            # Format result
            result_str = (
                f"Frame {frame} | "
                f"Loc: ({location.x:.4f}, {location.y:.4f}, {location.z:.4f}) | "
                f"Rot: ({rotation.x:.4f}, {rotation.y:.4f}, {rotation.z:.4f}, {rotation.w:.4f})"
            )
            
            props.debug_world_space_result = result_str
            
            Debug.report_and_log(self, 'INFO', f"World space transform retrieved: {result_str}")
            
        except Exception as e:
            Debug.report_and_log(self, 'ERROR', f"Error getting world space transform: {str(e)}")
            return {'FINISHED'}
        
        return {'FINISHED'}


class MTAR_OT_InspectLocalSpaceTransform(Operator):
    """Inspect local space transform for a bone at the current frame."""
    bl_idname = "mtar.inspect_local_space_transform"
    bl_label = "Inspect Local Space"
    bl_description = "Get local space transform (relative to parent bone)"
    
    def execute(self, context: Context) -> set:
        """Execute the inspection."""
        props = context.scene.mtar_debug_transform_properties


class MTAR_OT_CreateTransformDummies(Operator):
    """Create dummy objects showing local and world space transforms."""
    bl_idname = "mtar.create_transform_dummies"
    bl_label = "Create Transform Dummies"
    bl_description = "Create dummy objects to visualize local (3-sided) and world (12-sided) space transforms"
    
    def execute(self, context: Context) -> set:
        """Execute the dummy creation."""
        props = context.scene.mtar_debug_transform_properties
        
        # Validate inputs
        if not props.debug_armature:
            Debug.report_and_log(self, 'ERROR', "No armature selected")
            return {'FINISHED'}
        
        if not props.debug_bone_name:
            Debug.report_and_log(self, 'ERROR', "No bone selected")
            return {'FINISHED'}
        
        armature = props.debug_armature
        bone_name = props.debug_bone_name
        frame = context.scene.frame_current
        
        # Check if bone exists
        if bone_name not in armature.pose.bones:
            Debug.report_and_log(self, 'ERROR', f"Bone '{bone_name}' not found in armature")
            return {'FINISHED'}
        
        try:
            # Get or create collection
            collection_name = props.debug_dummy_collection_name
            scene_collection = context.scene.collection
            
            # Try to find existing collection
            debug_collection = None
            for coll in bpy.data.collections:
                if coll.name == collection_name:
                    debug_collection = coll
                    break
            
            # Create collection if it doesn't exist
            if debug_collection is None:
                debug_collection = bpy.data.collections.new(collection_name)
                scene_collection.children.link(debug_collection)
            
            # Set frame
            context.scene.frame_set(frame)
            
            # Get transforms (returns tuple of (location, rotation))
            world_result = get_world_space_transform(
                obj=armature,
                bone_name=bone_name,
                frame=frame
            )
            
            local_result = get_local_space_transform(
                obj=armature,
                bone_name=bone_name,
                frame=frame
            )
            
            if not world_result or not local_result:
                Debug.report_and_log(self, 'ERROR', "Could not get transform data")
                return {'FINISHED'}
            
            world_location, world_rotation = world_result
            local_location, local_rotation = local_result
            
            # Create 3-sided circle mesh vertices/edges for local space
            local_verts = [
                (0, 0, 0),
                (0.5, 0, 0),
                (0, 0.5, 0),
            ]
            local_edges = [(0, 1), (0, 2), (1, 2)]
            
            # Create local space dummy (place at local space location as if it were world space)
            local_dummy_name = f"{bone_name}_local_space"
            create_or_update_dummy_object(
                object_name=local_dummy_name,
                vertices=local_verts,
                edges=local_edges,
                location=local_location,
                rotation=local_rotation,
                collection=debug_collection
            )
            
            # Create 12-sided circle mesh vertices/edges for world space
            world_verts = []
            for i in range(12):
                angle = (i / 12) * 2 * math.pi
                world_verts.append((0.5 * math.cos(angle), 0.5 * math.sin(angle), 0))
            
            world_edges = [(i, (i + 1) % 12) for i in range(12)]

            # Create world space dummy
            world_dummy_name = f"{bone_name}_world_space"
            create_or_update_dummy_object(
                object_name=world_dummy_name,
                vertices=world_verts,
                edges=world_edges,
                location=world_location,
                rotation=world_rotation,
                collection=debug_collection
            )

            Debug.report_and_log(self, 'INFO', "Created transform dummy objects")
        except Exception as e:
            Debug.report_and_log(self, 'ERROR', f"Failed to create dummy objects: {e}")
            return {'FINISHED'}

        return {'FINISHED'}


class MTAR_OT_CopySingleResult(Operator):
    """Copy a single debug transform result to clipboard."""
    bl_idname = "mtar.copy_single_result"
    bl_label = "Copy Result"
    bl_description = "Copy this transform result to clipboard"
    
    result_type: StringProperty(
        name="Result Type",
        description="Which result to copy (WORLD or LOCAL)",
        default="WORLD",
        maxlen=10
    )
    
    def execute(self, context: Context) -> set:
        """Execute the copy operation."""
        props = context.scene.mtar_debug_transform_properties
        
        # Get the appropriate result
        if self.result_type == 'WORLD':
            result_text = props.debug_world_space_result
            label = "World Space"
        elif self.result_type == 'LOCAL':
            result_text = props.debug_local_space_result
            label = "Local Space"
        else:
            Debug.report_and_log(self, 'ERROR', f"Unknown result type: {self.result_type}")
            return {'FINISHED'}
        
        if not result_text:
            Debug.report_and_log(self, 'WARNING', f"No {label} result to copy yet")
            return {'FINISHED'}
        
        # Copy to clipboard
        context.window_manager.clipboard = result_text
        
        Debug.report_and_log(self, 'INFO', f"{label} result copied to clipboard")
        
        return {'FINISHED'}


class MTAR_OT_CopyTransformDebugResults(Operator):
    """Copy current debug transform results to clipboard."""
    bl_idname = "mtar.copy_transform_debug_results"
    bl_label = "Copy Results"
    bl_description = "Copy world and local space transform results to clipboard"
    
    def execute(self, context: Context) -> set:
        """Execute the copy operation."""
        props = context.scene.mtar_debug_transform_properties
        
        # Collect results
        results_lines = []
        
        if props.debug_world_space_result:
            results_lines.append(f"World Space: {props.debug_world_space_result}")
        
        if props.debug_local_space_result:
            results_lines.append(f"Local Space: {props.debug_local_space_result}")
        
        if not results_lines:
            Debug.report_and_log(self, 'WARNING', "No results to copy yet")
            return {'FINISHED'}
        
        # Combine results
        clipboard_text = "\n".join(results_lines)
        
        # Copy to clipboard
        context.window_manager.clipboard = clipboard_text
        Debug.report_and_log(self, 'INFO', "Transform results copied to clipboard")
        
        return {'FINISHED'}


# Bake Debug Panel Operators ##################################################################

class MTAR_OT_DebugRunBake(Operator):
    """Run synchronous bake using selected debug armature and optional imported armature."""
    bl_idname = "mtar.debug_run_bake"
    bl_label = "Debug: Run Bake"
    bl_description = "Run the existing synchronous bake operation for the selected armature"

    def execute(self, context: Context) -> set:
        props = context.scene.mtar_debug_transform_properties

        if not props.debug_armature:
            Debug.report_and_log(self, 'ERROR', "No target armature selected to bake into")
            return {'CANCELLED'}

        target_armature = props.debug_armature
        source_arm = props.debug_source_armature
        idx = props.debug_bake_gani_index
        prepare_only = props.debug_prepare_only

        try:
            Debug.update_progress(75, "Baking (debug)...")

            if target_armature.animation_data and target_armature.animation_data.nla_tracks:
                # Gather strips
                strips_by_index = {}
                strips_list = []
                for track in target_armature.animation_data.nla_tracks:
                    for strip in track.strips:
                        if not strip.action:
                            continue
                        m = re.search(r"_(\d{3})\b", strip.name)
                        if m:
                            stripped_idx = int(m.group(1))
                            strips_by_index.setdefault(stripped_idx, []).append((track, strip, strip.action))
                        strips_list.append((track, strip, strip.action))

                # Determine target strips
                target_strips = []
                if idx == -1:
                    # full bake or prepare all
                    if prepare_only:
                        # Prepare all: mute source NLA tracks and leave scene for inspection
                        if source_arm and source_arm.animation_data and source_arm.animation_data.nla_tracks:
                            for t in source_arm.animation_data.nla_tracks:
                                t.mute = True
                            Debug.report_and_log(self, 'INFO', "Prepared scene: muted all source NLA tracks for inspection (no bake performed)")
                            return {'FINISHED'}
                        else:
                            Debug.report_and_log(self, 'WARNING', "No source armature NLA tracks to mute for prepare")
                            return {'CANCELLED'}
                    else:
                        # Full bake using unified utility (constraint-bake + optional fcurve decimation)
                        Debug.log("Debug: Baking NLA strips on target armature")
                        bake_result = bake_constraints_and_decimate_fcurves(
                            rig_armature=target_armature,
                            source_armature=source_arm,
                            create_new_action=True,
                            new_action_suffix="_baked",
                            remove_constraints=True,
                            delete_import_armature=False,
                            bake_decimate_fcurve_error=0.01,  # Debug UI does not apply fcurve decimation by default
                            decimate_skip_types='',
                            layout_action=None,
                        )

                        if bake_result.get('success'):
                            Debug.report_and_log(self, 'INFO', f"Debug bake completed: {bake_result.get('message')} (post-processed: decimated={bake_result.get('fcurves_decimated', 0)})")
                        else:
                            Debug.report_and_log(self, 'WARNING', f"Debug bake failed: {bake_result.get('message')}")

                        Debug.update_progress(100, "Bake complete")
                        return {'FINISHED'}
                else:
                    # Bake only specific index
                    if idx not in strips_by_index:
                        Debug.report_and_log(self, 'ERROR', f"No strip found for GANI index {idx}")
                        return {'CANCELLED'}

                    target_strips = [t for t in strips_by_index[idx] if not t[0].mute and not t[1].mute]
                    if not target_strips:
                        Debug.report_and_log(self, 'WARNING', "No eligible strips to bake for this index")
                        return {'CANCELLED'}

                # Process selected strips
                success_count = 0
                baked_actions = []
                for (track, strip, action) in target_strips:
                    if prepare_only:
                        # Mute source NLA tracks and assign action for inspection
                        if source_arm and source_arm != target_armature:
                            if not source_arm.animation_data:
                                source_arm.animation_data_create()
                            if source_arm.animation_data.nla_tracks:
                                for t in source_arm.animation_data.nla_tracks:
                                    t.mute = True
                            assign_action_to_datablock(source_arm, action)
                            Debug.log(f"Prepared strip '{strip.name}': muted source NLA and assigned action '{action.name}'")
                            # Mute the target track and assign action so active action previews
                            track.mute = True
                            assign_action_to_datablock(target_armature, action)
                            Debug.report_and_log(self, 'INFO', f"Prepared scene for strip '{strip.name}' (no bake performed)")
                        continue

                    # Not prepare-only: perform legacy bake per strip (mute source tracks and assign)
                    original_source_action = None
                    original_source_nla_mute_states = None
                    try:
                        if source_arm and source_arm != target_armature:
                            if not source_arm.animation_data:
                                source_arm.animation_data_create()
                            original_source_action = source_arm.animation_data.action
                            if source_arm.animation_data.nla_tracks:
                                original_source_nla_mute_states = {t.name: t.mute for t in source_arm.animation_data.nla_tracks}
                                for t in source_arm.animation_data.nla_tracks:
                                    t.mute = True
                                Debug.log(f"  Muted {len(source_arm.animation_data.nla_tracks)} source NLA tracks for legacy bake")
                            assign_action_to_datablock(source_arm, action)

                        bake_result = bake_armature_constraints_to_keyframes(
                            rig_armature=target_armature,
                            action=action,
                            remove_constraints=False,
                            create_new_action=True,
                            new_action_suffix="_baked",
                            nla_track=track,
                            source_armature=source_arm
                        )

                        if bake_result.get('success'):
                            strip.action = bake_result.get('action')
                            baked_actions.append(bake_result.get('action'))
                            success_count += 1

                            # Run post-bake decimation for this baked action using scene settings
                            try:
                                decimate_err = 0.0
                                decimate_skip_types = ''
                                if hasattr(context.scene, 'mtar_properties'):
                                    ip = getattr(context.scene.mtar_properties, 'import_props', None)
                                    if ip is not None:
                                        decimate_err = get_effective_import_bake_decimate_error(ip)
                                        decimate_skip_types = getattr(ip, 'import_bake_decimate_skip_types', '')

                                # Decimate via decimate_import_fcurves_to_bezier (operates on armature level)
                                if decimate_err > 0.0:
                                    layout_action = try_find_layout_track_action()
                                    blender_bone_skip_map = build_blender_bone_decimation_skip_map(
                                        all_blender_bone_names=set(target_armature.data.bones.keys()) if target_armature and target_armature.data else set(),
                                        layout_action=layout_action,
                                        decimate_skip_types=decimate_skip_types,
                                        blender_to_fox_map=None,
                                        cache={},
                                    )
                                    dec_res = decimate_import_fcurves_to_bezier(
                                        armature=target_armature,
                                        bake_decimate_fcurve_error=decimate_err,
                                        decimate_skip_types=decimate_skip_types,
                                        layout_action=layout_action,
                                        blender_bone_skip_map=blender_bone_skip_map,
                                    )
                                    Debug.log(f"  Post-bake decimation: decimated={dec_res.get('fcurves_decimated', 0)}")
                            except Exception as opt_e:
                                Debug.log_warning(f"Post-bake optimize failed for '{strip.name}': {opt_e}")
                        else:
                            Debug.log_warning(f"Failed to bake strip '{strip.name}': {bake_result.get('message')}")

                    finally:
                        # Restore source armature state
                        try:
                            if source_arm and source_arm != target_armature and source_arm.animation_data:
                                if original_source_action:
                                    assign_action_to_datablock(source_arm, original_source_action)
                                else:
                                    remove_action_from_datablock(source_arm)
                                if original_source_nla_mute_states and source_arm.animation_data.nla_tracks:
                                    for tn, was in original_source_nla_mute_states.items():
                                        if tn in source_arm.animation_data.nla_tracks:
                                            source_arm.animation_data.nla_tracks[tn].mute = was
                                    Debug.log("  Restored source NLA mute states after legacy bake")
                        except Exception as restore_err:
                            Debug.log_warning(f"Error restoring source armature state after legacy bake: {restore_err}")

                # Wrap up per-strip baking
                if success_count > 0:
                    baked_bones = set()
                    for act in baked_actions:
                        baked_bones.update(get_bones_with_keyframes(act))
                    if baked_bones:
                        removed = remove_bone_constraints(target_armature, baked_bones)
                        Debug.log(f"Removed constraints from {len(baked_bones)} bones ({removed} constraints)")

                    if clear_armature_transforms(target_armature):
                        Debug.report_and_log(self, 'INFO', f"Baked {success_count} strip(s) and cleared transforms")
                        return {'FINISHED'}
                    else:
                        Debug.report_and_log(self, 'WARNING', f"Baked {success_count} strip(s) but failed to clear transforms")
                        return {'FINISHED'}
                else:
                    Debug.report_and_log(self, 'WARNING', "No strips were successfully baked")
                    return {'CANCELLED'}

            elif target_armature.animation_data and target_armature.animation_data.action:
                Debug.log("Debug: Baking active action on target armature")
                bake_result = bake_constraints_and_decimate_fcurves(
                    rig_armature=target_armature,
                    source_armature=source_arm,
                    create_new_action=True,
                    new_action_suffix="_baked",
                    remove_constraints=True,
                    bake_decimate_fcurve_error=0.0,
                    decimate_skip_types='',
                    layout_action=None,
                )

                if bake_result.get('success'):
                    Debug.report_and_log(self, 'INFO', f"Debug bake completed: {bake_result.get('message')} (post-processed: decimated={bake_result.get('fcurves_decimated', 0)})")
                else:
                    Debug.report_and_log(self, 'WARNING', f"Debug bake failed: {bake_result.get('message')}")

            else:
                Debug.report_and_log(self, 'WARNING', "Target armature has no NLA tracks or active action to bake")
                return {'CANCELLED'}

        except Exception as e:
            Debug.report_and_log(self, 'ERROR', f"Debug bake failed: {e}")
            return {'CANCELLED'}

        Debug.update_progress(100, "Bake complete")
        return {'FINISHED'}

class MTAR_OT_DebugSetupGraphContext(Operator):
    """Setup graph editor context for manual decimation testing."""
    bl_idname = "mtar.debug_setup_graph_context"
    bl_label = "Setup Graph Context"
    bl_description = "Setup graph editor with action and armature for manual operator testing"
    
    def execute(self, context: Context) -> set:
        """Execute the setup."""
        
        props = context.scene.mtar_debug_transform_properties
        
        # Validate inputs
        if not props.debug_armature:
            Debug.report_and_log(self, 'ERROR', "No target armature selected")
            return {'FINISHED'}
        
        # Get the action from the armature
        armature = props.debug_armature
        if not armature.animation_data or not armature.animation_data.action:
            Debug.report_and_log(self, 'ERROR', f"Armature '{armature.name}' has no active action")
            return {'FINISHED'}
        
        action = armature.animation_data.action
        
        # Call the debug setup function
        debug_setup_graph_context_for_manual_test(armature.name, action.name)
        
        Debug.report_and_log(self, 'INFO', f"Graph context setup complete for '{armature.name}' / '{action.name}'")
        Debug.report_and_log(self, 'INFO', "Check console for diagnostics. Try: bpy.ops.graph.decimate(mode='ERROR', error=0.01)")
        
        return {'FINISHED'}


# External Hash Generator Panel Operators ############################################################

class MTAR_OT_ValidateHashGeneratorExe(Operator):
    """Validate hash generator executable path (debug panel)."""
    bl_idname = "mtar.validate_hash_generator_exe"
    bl_label = "Validate Executable"
    bl_description = "Validate that the executable path is valid and accessible"

    def execute(self, context: Context) -> Set[str]:
        props = context.scene.mtar_debug_hash_properties
        exe_path = props.hash_generator_exe_path
        if not exe_path:
            Debug.report_and_log(self, 'ERROR', "Executable path not configured")
            return {'CANCELLED'}
        is_valid, error_msg = validate_executable_path_by_external_generator(exe_path)
        if is_valid:
            Debug.report_and_log(self, 'INFO', "Executable path is valid")
            return {'FINISHED'}
        else:
            Debug.report_and_log(self, 'ERROR', f"Invalid executable: {error_msg}")
            return {'CANCELLED'}


class MTAR_OT_GenerateHash(Operator):
    """Generate hash for input filename using both Python CityHash and external executable."""
    bl_idname = "mtar.generate_hash"
    bl_label = "Hash"
    bl_description = (
        "Hash input filename using Python CityHash (always) and "
        "the external executable (when configured) — all modes"
    )

    def execute(self, context: Context) -> set:
        """Execute the hash computation."""
        props = context.scene.mtar_debug_hash_properties

        if not props.hash_generator_input:
            Debug.report_and_log(self, 'ERROR', "No input filename provided")
            props.hash_generator_error = "No input filename provided"
            self._clear_exe_results(props)
            self._clear_py_results(props)
            return {'CANCELLED'}

        self._run_python(props)
        self._run_exe(context, props)

        Debug.report_and_log(self, 'INFO', "Hash computation complete")
        return {'FINISHED'}

    # ------------------------------------------------------------------
    # Python CityHash path
    # ------------------------------------------------------------------

    def _run_python(self, props) -> None:
        """Compute all four hash variants using the pure-Python implementation."""
        text = props.hash_generator_input
        try:
            h_file = hash_file_name(text)
            props.hash_generator_py_hash_filename = format(h_file, 'x')
            props.hash_generator_py_hash_filename_dec = str(h_file)

            # Extension hash: extract extension after last '.'
            dot = text.rfind('.')
            if dot != -1:
                ext = text[dot + 1:]
                h_ext = hash_file_extension(ext)
                props.hash_generator_py_hash_extension = format(h_ext, 'x')
                props.hash_generator_py_hash_extension_dec = str(h_ext)
            else:
                props.hash_generator_py_hash_extension = ""
                props.hash_generator_py_hash_extension_dec = ""

            h_hwe = hash_file_name_with_ext(text)
            props.hash_generator_py_hash_with_extension = format(h_hwe, 'x')
            props.hash_generator_py_hash_with_extension_dec = str(h_hwe)

            h_leg = hash_file_name_legacy(text)
            props.hash_generator_py_hash_legacy = format(h_leg, 'x')
            props.hash_generator_py_hash_legacy_dec = str(h_leg)

            props.hash_generator_py_error = ""
        except Exception as exc:
            self._clear_py_results(props)
            props.hash_generator_py_error = str(exc)
            Debug.report_and_log(self, 'ERROR', f"Python hash failed: {exc}")

    # ------------------------------------------------------------------
    # External exe path
    # ------------------------------------------------------------------

    def _run_exe(self, context: Context, props) -> None:
        """Compute hash variants using the external executable (if configured)."""
        exe_path = props.hash_generator_exe_path
        if not exe_path:
            self._clear_exe_results(props)
            return

        success, results, error = hash_filename_all_modes_by_external_generator(exe_path, props.hash_generator_input)

        props.hash_generator_hash_filename = results.get('filename', '')
        props.hash_generator_hash_extension = results.get('extension', '')
        props.hash_generator_hash_with_extension = results.get('with_extension', '')
        props.hash_generator_hash_legacy = results.get('legacy', '')
        props.hash_generator_hash_filename_dec = results.get('filename_dec', '')
        props.hash_generator_hash_extension_dec = results.get('extension_dec', '')
        props.hash_generator_hash_with_extension_dec = results.get('with_extension_dec', '')
        props.hash_generator_hash_legacy_dec = results.get('legacy_dec', '')

        if success:
            props.hash_generator_error = ""
        else:
            props.hash_generator_error = error
            Debug.report_and_log(self, 'WARNING', f"Exe hash failed: {error}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clear_exe_results(self, props) -> None:
        props.hash_generator_hash_filename = ""
        props.hash_generator_hash_extension = ""
        props.hash_generator_hash_with_extension = ""
        props.hash_generator_hash_legacy = ""
        props.hash_generator_hash_filename_dec = ""
        props.hash_generator_hash_extension_dec = ""
        props.hash_generator_hash_with_extension_dec = ""
        props.hash_generator_hash_legacy_dec = ""

    def _clear_py_results(self, props) -> None:
        props.hash_generator_py_hash_filename = ""
        props.hash_generator_py_hash_filename_dec = ""
        props.hash_generator_py_hash_extension = ""
        props.hash_generator_py_hash_extension_dec = ""
        props.hash_generator_py_hash_with_extension = ""
        props.hash_generator_py_hash_with_extension_dec = ""
        props.hash_generator_py_hash_legacy = ""
        props.hash_generator_py_hash_legacy_dec = ""


class MTAR_OT_CopyHashGeneratorOutput(Operator):
    """Copy hash result to clipboard."""
    bl_idname = "mtar.copy_hash_generator_output"
    bl_label = "Copy Result"
    bl_description = "Copy the selected hash result to clipboard"
    
    result_key: StringProperty(
        name="Result Key",
        description="Which result to copy",
        default="filename",
        maxlen=64
    )
    
    def execute(self, context: Context) -> set:
        """Execute the copy."""
        props = context.scene.mtar_debug_hash_properties
        
        # Get the appropriate result based on key
        result_map = {
            'filename': props.hash_generator_hash_filename,
            'extension': props.hash_generator_hash_extension,
            'with_extension': props.hash_generator_hash_with_extension,
            'legacy': props.hash_generator_hash_legacy,
            'filename_dec': props.hash_generator_hash_filename_dec,
            'extension_dec': props.hash_generator_hash_extension_dec,
            'with_extension_dec': props.hash_generator_hash_with_extension_dec,
            'legacy_dec': props.hash_generator_hash_legacy_dec,
            # Python CityHash results
            'py_filename': props.hash_generator_py_hash_filename,
            'py_extension': props.hash_generator_py_hash_extension,
            'py_with_extension': props.hash_generator_py_hash_with_extension,
            'py_legacy': props.hash_generator_py_hash_legacy,
            'py_filename_dec': props.hash_generator_py_hash_filename_dec,
            'py_extension_dec': props.hash_generator_py_hash_extension_dec,
            'py_with_extension_dec': props.hash_generator_py_hash_with_extension_dec,
            'py_legacy_dec': props.hash_generator_py_hash_legacy_dec,
        }
        
        output = result_map.get(self.result_key, '')
        
        if not output:
            Debug.report_and_log(self, 'WARNING', f"No result to copy for {self.result_key}")
            return {'CANCELLED'}
        
        # Skip if it's an error message
        if output.startswith('Error:'):
            Debug.report_and_log(self, 'WARNING', "Cannot copy error message")
            return {'CANCELLED'}
        
        context.window_manager.clipboard = output
        Debug.report_and_log(self, 'INFO', f"Copied {self.result_key} to clipboard")
        return {'FINISHED'}


class MTAR_OT_ClearHashGeneratorResults(Operator):
    """Clear hash generator input and results."""
    bl_idname = "mtar.clear_hash_generator_results"
    bl_label = "Clear"
    bl_description = "Clear hash generator input and all hash results"
    
    def execute(self, context: Context) -> set:
        """Execute the clear."""
        props = context.scene.mtar_debug_hash_properties
        
        props.hash_generator_input = ""
        # Exe results
        props.hash_generator_hash_filename = ""
        props.hash_generator_hash_extension = ""
        props.hash_generator_hash_with_extension = ""
        props.hash_generator_hash_legacy = ""
        props.hash_generator_hash_filename_dec = ""
        props.hash_generator_hash_extension_dec = ""
        props.hash_generator_hash_with_extension_dec = ""
        props.hash_generator_hash_legacy_dec = ""
        props.hash_generator_error = ""
        # Python CityHash results
        props.hash_generator_py_hash_filename = ""
        props.hash_generator_py_hash_filename_dec = ""
        props.hash_generator_py_hash_extension = ""
        props.hash_generator_py_hash_extension_dec = ""
        props.hash_generator_py_hash_with_extension = ""
        props.hash_generator_py_hash_with_extension_dec = ""
        props.hash_generator_py_hash_legacy = ""
        props.hash_generator_py_hash_legacy_dec = ""
        props.hash_generator_py_error = ""
        
        Debug.report_and_log(self, 'INFO', "Hash Generator cleared")
        return {'FINISHED'}



# StrCode32 Animation Name Hashing Operators ##############################################
# TODO: check if they are still used or useful

class MTAR_OT_ComputeStrCode32(Operator):
    """Compute StrCode32 hash for an animation track/bone name."""
    bl_idname = "mtar.compute_strcode32"
    bl_label = "Compute StrCode32"
    bl_description = "Compute StrCode32 hash for animation track names, bone names, event names, etc."
    
    def execute(self, context: Context) -> set:
        """Execute the hash computation."""
        props = context.scene.mtar_debug_hash_properties
        
        # Get input
        input_text = props.strcode32_input.strip()
        remove_ext = props.strcode32_remove_extension
        
        # Clear previous results
        props.strcode32_result = ""
        props.strcode32_result_dec = ""
        props.strcode32_error = ""
        
        if not input_text:
            props.strcode32_error = "Input is empty"
            Debug.report_and_log(self, 'WARNING', "StrCode32: Input is empty")
            return {'FINISHED'}
        
        try:
            # Compute StrCode32
            hash_val = strcode32_path(input_text, remove_extension=remove_ext)
            
            # Format as hex (32-bit, 8 digits)
            props.strcode32_result = f"0x{hash_val:08X}"
            props.strcode32_result_dec = str(hash_val)
            
            Debug.report_and_log(self, 'INFO', 
                f"StrCode32('{input_text}', remove_ext={remove_ext}) = {props.strcode32_result} ({props.strcode32_result_dec})")
            
        except Exception as e:
            props.strcode32_error = f"Exception: {str(e)}"
            Debug.report_and_log(self, 'ERROR', f"StrCode32 computation failed: {e}")
        
        return {'FINISHED'}


class MTAR_OT_ClearStrCode32Results(Operator):
    """Clear StrCode32 results."""
    bl_idname = "mtar.clear_strcode32_results"
    bl_label = "Clear StrCode32 Results"
    bl_description = "Clear all StrCode32 results"
    
    def execute(self, context: Context) -> set:
        """Clear the results."""
        props = context.scene.mtar_debug_hash_properties
        
        props.strcode32_input = ""
        props.strcode32_result = ""
        props.strcode32_result_dec = ""
        props.strcode32_error = ""
        
        Debug.report_and_log(self, 'INFO', "StrCode32 results cleared")
        return {'FINISHED'}


class MTAR_OT_CopyStrCode32Result(Operator):
    """Copy StrCode32 result to clipboard."""
    bl_idname = "mtar.copy_strcode32_result"
    bl_label = "Copy StrCode32 Result"
    bl_description = "Copy the StrCode32 result to clipboard"
    
    is_decimal: bpy.props.BoolProperty(
        name="Is Decimal",
        description="If True, copy decimal; if False, copy hexadecimal",
        default=False
    )
    
    def execute(self, context: Context) -> set:
        """Copy to clipboard."""
        props = context.scene.mtar_debug_hash_properties
        
        text_to_copy = props.strcode32_result_dec if self.is_decimal else props.strcode32_result
        
        if not text_to_copy:
            Debug.report_and_log(self, 'WARNING', "StrCode32: No result to copy")
            return {'FINISHED'}
        
        # Copy to clipboard
        context.window_manager.clipboard = text_to_copy
        
        Debug.report_and_log(self, 'INFO', f"Copied to clipboard: {text_to_copy}")
        return {'FINISHED'}
