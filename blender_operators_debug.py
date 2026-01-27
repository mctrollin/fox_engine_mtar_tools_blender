"""
Debug operators for MTAR tools - transform inspection and external hash generator utilities.

This module contains operator classes for debugging and inspecting transforms,
as well as interfacing with the external hash generator executable.
"""

from typing import TYPE_CHECKING

import bpy
from bpy.types import Operator, Context
from bpy.props import StringProperty

from .py_utilities.utilities_transforms import get_world_space_transform, get_local_space_transform
from .py_utilities.utilities_logging import Debug


# Transform Debug Operators ##################################################################

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
            # Get local space transform
            location, rotation = get_local_space_transform(
                armature, bone_name, frame
            )
            
            # Format result
            result_str = (
                f"Frame {frame} | "
                f"Loc: ({location.x:.4f}, {location.y:.4f}, {location.z:.4f}) | "
                f"Rot: ({rotation.x:.4f}, {rotation.y:.4f}, {rotation.z:.4f}, {rotation.w:.4f})"
            )
            
            props.debug_local_space_result = result_str
            
            Debug.report_and_log(self, 'INFO', f"Local space transform retrieved: {result_str}")
            
        except Exception as e:
            Debug.report_and_log(self, 'ERROR', f"Error getting local space transform: {str(e)}")
            return {'FINISHED'}
        
        return {'FINISHED'}


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
            from .blender_panel_debug import create_or_update_dummy_object
            create_or_update_dummy_object(
                object_name=local_dummy_name,
                vertices=local_verts,
                edges=local_edges,
                location=local_location,
                rotation=local_rotation,
                collection=debug_collection
            )
            
            # Create 12-sided circle mesh vertices/edges for world space
            import math
            world_verts = []
            for i in range(12):
                angle = (i / 12) * 2 * math.pi
                world_verts.append((0.5 * math.cos(angle), 0.5 * math.sin(angle), 0))
            
            world_edges = [(i, (i + 1) % 12) for i in range(12)]
            
            # Create world space dummy (no rotation, in world space)
            world_dummy_name = f"{bone_name}_world_space"
            create_or_update_dummy_object(
                object_name=world_dummy_name,
                vertices=world_verts,
                edges=world_edges,
                location=world_location,
                rotation=world_rotation,
                collection=debug_collection
            )
            
            Debug.report_and_log(self, 'INFO', f"Created dummies for '{bone_name}' at frame {frame}")
            
        except RuntimeError as e:
            Debug.report_and_log(self, 'ERROR', f"Error creating dummies: {e}")
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


# External Hash Generator Operators ############################################################

class MTAR_OT_GenerateHashWithExternalExe(Operator):
    """Generate hash for input filename using external executable."""
    bl_idname = "mtar.generate_hash_with_external_exe"
    bl_label = "Hash"
    bl_description = "Hash input filename using the specified external executable (all modes)"
    
    def execute(self, context: Context) -> set:
        """Execute the hash conversion."""
        from .py_tools.tools_hash_generator import hash_filename_all_modes
        
        props = context.scene.mtar_debug_hash_properties
        # The executable path is read strictly from main scene settings
        if not hasattr(context.scene, 'mtar_properties') or not context.scene.mtar_properties.settings_props.hash_generator_exe_path:
            Debug.report_and_log(self, 'ERROR', "Hash Generator executable path not configured in MTAR Settings")
            props.hash_generator_error = "Hash Generator executable path not configured in MTAR Settings"
            self._clear_results(props)
            return {'CANCELLED'}
        exe_path = context.scene.mtar_properties.settings_props.hash_generator_exe_path
        
        # Validate inputs
        if not exe_path:
            Debug.report_and_log(self, 'ERROR', "No executable path specified")
            props.hash_generator_error = "No executable path specified"
            self._clear_results(props)
            return {'CANCELLED'}
        
        if not props.hash_generator_input:
            Debug.report_and_log(self, 'ERROR', "No input filename provided")
            props.hash_generator_error = "No input filename provided"
            self._clear_results(props)
            return {'CANCELLED'}
        
        # Run hash conversion (all modes)
        success, results, error = hash_filename_all_modes(
            exe_path,
            props.hash_generator_input
        )
        
        # Store results
        props.hash_generator_hash_filename = results.get('filename', '')
        props.hash_generator_hash_extension = results.get('extension', '')
        props.hash_generator_hash_with_extension = results.get('with_extension', '')
        props.hash_generator_hash_legacy = results.get('legacy', '')
        # Decimal representations (may be empty strings if parsing failed)
        props.hash_generator_hash_filename_dec = results.get('filename_dec', '')
        props.hash_generator_hash_extension_dec = results.get('extension_dec', '')
        props.hash_generator_hash_with_extension_dec = results.get('with_extension_dec', '')
        props.hash_generator_hash_legacy_dec = results.get('legacy_dec', '')
        
        if success:
            props.hash_generator_error = ""
            Debug.report_and_log(self, 'INFO', "Hash conversion successful")
            return {'FINISHED'}
        else:
            props.hash_generator_error = error
            Debug.report_and_log(self, 'ERROR', f"Hash conversion failed: {error}")
            return {'CANCELLED'}
    
    def _clear_results(self, props) -> None:
        """Clear all result properties."""
        props.hash_generator_hash_filename = ""
        props.hash_generator_hash_extension = ""
        props.hash_generator_hash_with_extension = ""
        props.hash_generator_hash_legacy = ""


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
            'legacy_dec': props.hash_generator_hash_legacy_dec
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
        props.hash_generator_hash_filename = ""
        props.hash_generator_hash_extension = ""
        props.hash_generator_hash_with_extension = ""
        props.hash_generator_hash_legacy = ""
        props.hash_generator_error = ""
        
        Debug.report_and_log(self, 'INFO', "Hash Generator cleared")
        return {'FINISHED'}



