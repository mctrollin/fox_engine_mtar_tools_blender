"""
Debug utilities for MTAR tools - provides transform inspection panel and operators.

This module adds debugging capabilities to inspect local and world space transforms
for bones at specific frames, useful for verifying export/import transform correctness.
"""

from typing import TYPE_CHECKING

import bpy
from bpy.types import Operator, Panel, PropertyGroup, Context
from bpy.props import PointerProperty, BoolProperty, StringProperty

from .py_utilities.utilities_transforms import get_world_space_transform, get_local_space_transform
from .py_utilities.utilities_logging import Debug

if TYPE_CHECKING:
    from bpy.types import Object


# Utility functions ############################################################################

def create_or_update_dummy_object(
    object_name: str,
    vertices: list,
    edges: list,
    location: tuple,
    rotation: tuple,
    collection: 'bpy.types.Collection'
) -> 'bpy.types.Object':
    """Create or update a dummy object with the given mesh and transform.
    
    Args:
        object_name: Name of the object to create/update
        vertices: List of vertex coordinates (tuples)
        edges: List of edge definitions (tuples of vertex indices)
        location: 3D location vector
        rotation: Rotation quaternion (w, x, y, z)
        collection: Collection to add the object to
        
    Returns:
        The created/updated object
    """
    # Create or get object
    if object_name in bpy.data.objects:
        dummy_obj = bpy.data.objects[object_name]
    else:
        mesh = bpy.data.meshes.new(f"{object_name}_mesh")
        dummy_obj = bpy.data.objects.new(object_name, mesh)
    
    # Update mesh geometry
    mesh = dummy_obj.data
    mesh.clear_geometry()
    mesh.from_pydata(vertices, edges, [])
    
    # Set transform
    dummy_obj.location = location
    dummy_obj.rotation_quaternion = rotation
    
    # Add to collection
    if dummy_obj.name not in collection.objects:
        collection.objects.link(dummy_obj)
    
    return dummy_obj


class MTAR_PG_DebugProperties(PropertyGroup):
    """Property group for debug transform inspection settings."""
    
    debug_armature: PointerProperty(
        name="Armature",
        description="Armature to inspect",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE'
    )
    
    debug_bone_name: StringProperty(
        name="Bone",
        description="Bone to inspect",
        default="",
        maxlen=1024
    )
    
    debug_use_evaluated: BoolProperty(
        name="Use Evaluated",
        description="Use evaluated transforms (after constraints/IK) instead of raw keyframes",
        default=False
    )
    
    debug_world_space_result: bpy.props.StringProperty(
        name="World Space Result",
        description="Last world space transform result",
        default="",
    )

    debug_local_space_result: bpy.props.StringProperty(
        name="Local Space Result",
        description="Last local space transform result",
        default="",
    )

    debug_dummy_collection_name: StringProperty(
        name="Dummy Collection",
        description="Collection name for dummy transform objects",
        default="MTAR_Debug_Dummies",
        maxlen=1024
    )


class MTAR_OT_InspectWorldSpaceTransform(Operator):
    """Inspect world space transform for a bone at the current frame."""
    bl_idname = "mtar.inspect_world_space_transform"
    bl_label = "Inspect World Space"
    bl_description = "Get world space transform (relative to scene origin 0,0,0)"
    
    def execute(self, context: Context) -> set:
        """Execute the inspection."""
        props = context.scene.mtar_debug_properties
        
        # Validate inputs
        if not props.debug_armature:
            self.report({'ERROR'}, "No armature selected")
            return {'FINISHED'}
        
        if not props.debug_bone_name:
            self.report({'ERROR'}, "No bone selected")
            return {'FINISHED'}
        
        armature = props.debug_armature
        bone_name = props.debug_bone_name
        frame = context.scene.frame_current
        use_evaluated = props.debug_use_evaluated
        
        # Validate bone exists
        if bone_name not in armature.pose.bones:
            self.report({'ERROR'}, f"Bone '{bone_name}' not found in armature")
            return {'FINISHED'}
        
        try:
            # Get world space transform
            location, rotation = get_world_space_transform(
                armature, bone_name, frame,
                space_bone=None,
                evaluated=use_evaluated
            )
            
            # Format result
            result_str = (
                f"Frame {frame} | "
                f"Loc: ({location.x:.4f}, {location.y:.4f}, {location.z:.4f}) | "
                f"Rot: ({rotation.x:.4f}, {rotation.y:.4f}, {rotation.z:.4f}, {rotation.w:.4f})"
            )
            
            props.debug_world_space_result = result_str
            
            Debug.log(f"World Space Transform for '{bone_name}': {result_str}")
            self.report({'INFO'}, f"World space transform retrieved: {result_str}")
            
        except Exception as e:
            error_msg = f"Error getting world space transform: {str(e)}"
            Debug.log_error(error_msg)
            self.report({'ERROR'}, error_msg)
            return {'FINISHED'}
        
        return {'FINISHED'}


class MTAR_OT_InspectLocalSpaceTransform(Operator):
    """Inspect local space transform for a bone at the current frame."""
    bl_idname = "mtar.inspect_local_space_transform"
    bl_label = "Inspect Local Space"
    bl_description = "Get local space transform (relative to parent bone)"
    
    def execute(self, context: Context) -> set:
        """Execute the inspection."""
        props = context.scene.mtar_debug_properties
        
        # Validate inputs
        if not props.debug_armature:
            self.report({'ERROR'}, "No armature selected")
            return {'FINISHED'}
        
        if not props.debug_bone_name:
            self.report({'ERROR'}, "No bone selected")
            return {'FINISHED'}
        
        armature = props.debug_armature
        bone_name = props.debug_bone_name
        frame = context.scene.frame_current
        use_evaluated = props.debug_use_evaluated
        
        # Validate bone exists
        if bone_name not in armature.pose.bones:
            self.report({'ERROR'}, f"Bone '{bone_name}' not found in armature")
            return {'FINISHED'}
        
        try:
            # Get local space transform
            location, rotation = get_local_space_transform(
                armature, bone_name, frame,
                evaluated=use_evaluated
            )
            
            # Format result
            result_str = (
                f"Frame {frame} | "
                f"Loc: ({location.x:.4f}, {location.y:.4f}, {location.z:.4f}) | "
                f"Rot: ({rotation.x:.4f}, {rotation.y:.4f}, {rotation.z:.4f}, {rotation.w:.4f})"
            )
            
            props.debug_local_space_result = result_str
            
            Debug.log(f"Local Space Transform for '{bone_name}': {result_str}")
            self.report({'INFO'}, f"Local space transform retrieved: {result_str}")
            
        except Exception as e:
            error_msg = f"Error getting local space transform: {str(e)}"
            Debug.log_error(error_msg)
            self.report({'ERROR'}, error_msg)
            return {'FINISHED'}
        
        return {'FINISHED'}


class MTAR_OT_CreateTransformDummies(Operator):
    """Create dummy objects showing local and world space transforms."""
    bl_idname = "mtar.create_transform_dummies"
    bl_label = "Create Transform Dummies"
    bl_description = "Create dummy objects to visualize local (3-sided) and world (12-sided) space transforms"
    
    def execute(self, context: Context) -> set:
        """Execute the dummy creation."""
        props = context.scene.mtar_debug_properties
        
        # Validate inputs
        if not props.debug_armature:
            self.report({'ERROR'}, "No armature selected")
            return {'FINISHED'}
        
        if not props.debug_bone_name:
            self.report({'ERROR'}, "No bone selected")
            return {'FINISHED'}
        
        armature = props.debug_armature
        bone_name = props.debug_bone_name
        frame = context.scene.frame_current
        
        # Check if bone exists
        if bone_name not in armature.pose.bones:
            self.report({'ERROR'}, f"Bone '{bone_name}' not found in armature")
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
                frame=frame,
                evaluated=props.debug_use_evaluated
            )
            
            local_result = get_local_space_transform(
                obj=armature,
                bone_name=bone_name,
                frame=frame,
                evaluated=props.debug_use_evaluated
            )
            
            if not world_result or not local_result:
                self.report({'ERROR'}, "Could not get transform data")
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
            
            self.report({'INFO'}, f"Created dummies for '{bone_name}' at frame {frame}")
            
        except RuntimeError as e:
            Debug.log_error(f"Error creating dummies: {e}")
            self.report({'ERROR'}, f"Error: {e}")
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
        props = context.scene.mtar_debug_properties
        
        # Get the appropriate result
        if self.result_type == 'WORLD':
            result_text = props.debug_world_space_result
            label = "World Space"
        elif self.result_type == 'LOCAL':
            result_text = props.debug_local_space_result
            label = "Local Space"
        else:
            self.report({'ERROR'}, f"Unknown result type: {self.result_type}")
            return {'FINISHED'}
        
        if not result_text:
            self.report({'WARNING'}, f"No {label} result to copy yet")
            return {'FINISHED'}
        
        # Copy to clipboard
        context.window_manager.clipboard = result_text
        
        self.report({'INFO'}, f"{label} result copied to clipboard")
        Debug.log(f"Copied {label} result to clipboard:\n{result_text}")
        
        return {'FINISHED'}


class MTAR_OT_CopyTransformDebugResults(Operator):
    """Copy current debug transform results to clipboard."""
    bl_idname = "mtar.copy_transform_debug_results"
    bl_label = "Copy Results"
    bl_description = "Copy world and local space transform results to clipboard"
    
    def execute(self, context: Context) -> set:
        """Execute the copy operation."""
        props = context.scene.mtar_debug_properties
        
        # Collect results
        results_lines = []
        
        if props.debug_world_space_result:
            results_lines.append(f"World Space: {props.debug_world_space_result}")
        
        if props.debug_local_space_result:
            results_lines.append(f"Local Space: {props.debug_local_space_result}")
        
        if not results_lines:
            self.report({'WARNING'}, "No results to copy yet")
            return {'FINISHED'}
        
        # Combine results
        clipboard_text = "\n".join(results_lines)
        
        # Copy to clipboard
        context.window_manager.clipboard = clipboard_text
        
        self.report({'INFO'}, "Transform results copied to clipboard")
        Debug.log(f"Copied to clipboard:\n{clipboard_text}")
        
        return {'FINISHED'}


class MTAR_PT_DebugPanel(Panel):
    """N-Panel for transform debugging and inspection."""
    bl_label = "Transform Debug"
    bl_idname = "MTAR_PT_debug_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MTAR'
    
    def draw(self, context: Context) -> None:
        """Draw the debug panel."""
        layout = self.layout
        props = context.scene.mtar_debug_properties
        
        # Header
        box = layout.box()
        box.label(text="Transform Inspector", icon='OUTLINER_OB_ARMATURE')
        
        # Armature and Bone selection
        config_box = layout.box()
        config_box.label(text="Configuration", icon='SETTINGS')
        
        col = config_box.column(align=True)
        col.prop(props, "debug_armature", text="Armature")
        
        # Only show bone selector if armature is selected
        if props.debug_armature and props.debug_armature.type == 'ARMATURE':
            # Create a search menu for bone selection
            row = col.row(align=True)
            row.prop(props, "debug_bone_name", text="Bone")
            
            # Add a dropdown to select bones
            if props.debug_armature.pose.bones:
                row.operator("wm.search_menu", text="", icon='DOWNARROW_HLT')
        
        config_box.prop(props, "debug_use_evaluated", toggle=True)
        
        # Current frame info
        info_box = layout.box()
        col = info_box.column()
        col.label(text=f"Current Frame: {context.scene.frame_current}", icon='PREVIEW_RANGE')
        
        # Action buttons
        button_box = layout.box()
        button_box.label(text="Inspect", icon='EYEDROPPER')
        
        col = button_box.column(align=True)
        col.scale_y = 1.3
        
        # Enable buttons only if armature and bone are selected
        buttons_enabled = bool(props.debug_armature and props.debug_bone_name)
        
        row = col.row(align=True)
        row.enabled = buttons_enabled
        row.operator("mtar.inspect_world_space_transform", text="World Space", icon='WORLD')
        
        row = col.row(align=True)
        row.enabled = buttons_enabled
        row.operator("mtar.inspect_local_space_transform", text="Local Space", icon='BONE_DATA')
        
        # Create dummies button
        row = col.row(align=True)
        row.enabled = buttons_enabled
        row.operator("mtar.create_transform_dummies", text="Create Dummies", icon='MESH_CIRCLE')
        
        # Results display
        results_box = layout.box()
        results_box.label(text="Results", icon='CHECKMARK')
        
        # World space result
        if props.debug_world_space_result:
            world_box = results_box.box()
            row = world_box.row(align=True)
            row.label(text="World Space:", icon='WORLD')
            row.operator("mtar.copy_single_result", text="", icon='COPYDOWN').result_type = 'WORLD'
            col = world_box.column()
            col.label(text=props.debug_world_space_result, icon='NONE')
        else:
            results_box.label(text="World Space: (no result yet)", icon='WORLD')
        
        # Local space result
        if props.debug_local_space_result:
            local_box = results_box.box()
            row = local_box.row(align=True)
            row.label(text="Local Space:", icon='BONE_DATA')
            row.operator("mtar.copy_single_result", text="", icon='COPYDOWN').result_type = 'LOCAL'
            col = local_box.column()
            col.label(text=props.debug_local_space_result, icon='NONE')
        else:
            results_box.label(text="Local Space: (no result yet)", icon='BONE_DATA')
        
        # Copy all results button
        if props.debug_world_space_result or props.debug_local_space_result:
            results_box.operator("mtar.copy_transform_debug_results", text="Copy All Results", icon='COPYDOWN')


# External Converter Panel ####################################################################

class MTAR_PG_ConverterProperties(PropertyGroup):
    """Property group for external converter settings."""
    
    converter_exe_path: StringProperty(
        name="Executable Path",
        description="Path to the hash converter executable",
        default="",
        maxlen=1024,
        subtype='FILE_PATH'
    )
    
    converter_input: StringProperty(
        name="Input",
        description="Input filename (with or without extension)",
        default="",
        maxlen=4096
    )
    
    # Results for each hash mode
    converter_hash_filename: StringProperty(
        name="Hash Filename",
        description="Hashed filename without extension (-d -h)",
        default="",
        maxlen=4096
    )
    
    converter_hash_extension: StringProperty(
        name="Hash Extension",
        description="Hashed extension digits (-d -he)",
        default="",
        maxlen=4096
    )
    
    converter_hash_with_extension: StringProperty(
        name="Hash With Extension",
        description="Hashed filename with extension (-d -hwe)",
        default="",
        maxlen=4096
    )
    
    converter_hash_legacy: StringProperty(
        name="Hash Legacy",
        description="Legacy hash function (-d -hl)",
        default="",
        maxlen=4096
    )
    
    converter_error: StringProperty(
        name="Error",
        description="Error message if conversion failed",
        default="",
        maxlen=4096
    )

    # Decimal representations
    converter_hash_filename_dec: StringProperty(
        name="Hash Filename (dec)",
        description="Decimal representation of hashed filename",
        default="",
        maxlen=4096
    )
    converter_hash_extension_dec: StringProperty(
        name="Hash Extension (dec)",
        description="Decimal representation of hashed extension",
        default="",
        maxlen=4096
    )
    converter_hash_with_extension_dec: StringProperty(
        name="Hash With Extension (dec)",
        description="Decimal representation of hashed filename with extension",
        default="",
        maxlen=4096
    )
    converter_hash_legacy_dec: StringProperty(
        name="Hash Legacy (dec)",
        description="Decimal representation of legacy hash",
        default="",
        maxlen=4096
    )


class MTAR_OT_ConvertWithExternalExe(Operator):
    """Hash input filename using external executable."""
    bl_idname = "mtar.convert_with_external_exe"
    bl_label = "Hash"
    bl_description = "Hash input filename using the specified external executable (all modes)"
    
    def execute(self, context: Context) -> set:
        """Execute the hash conversion."""
        from .py_tools.tools_hash_generator import hash_filename_all_modes
        
        props = context.scene.mtar_converter_properties
        
        # Validate inputs
        if not props.converter_exe_path:
            self.report({'ERROR'}, "No executable path specified")
            props.converter_error = "No executable path specified"
            self._clear_results(props)
            return {'CANCELLED'}
        
        if not props.converter_input:
            self.report({'ERROR'}, "No input filename provided")
            props.converter_error = "No input filename provided"
            self._clear_results(props)
            return {'CANCELLED'}
        
        # Run hash conversion (all modes)
        success, results, error = hash_filename_all_modes(
            props.converter_exe_path,
            props.converter_input
        )
        
        # Store results
        props.converter_hash_filename = results.get('filename', '')
        props.converter_hash_extension = results.get('extension', '')
        props.converter_hash_with_extension = results.get('with_extension', '')
        props.converter_hash_legacy = results.get('legacy', '')
        # Decimal representations (may be empty strings if parsing failed)
        props.converter_hash_filename_dec = results.get('filename_dec', '')
        props.converter_hash_extension_dec = results.get('extension_dec', '')
        props.converter_hash_with_extension_dec = results.get('with_extension_dec', '')
        props.converter_hash_legacy_dec = results.get('legacy_dec', '')
        
        if success:
            props.converter_error = ""
            self.report({'INFO'}, "Hash conversion successful")
            return {'FINISHED'}
        else:
            props.converter_error = error
            self.report({'ERROR'}, f"Hash conversion failed: {error}")
            return {'CANCELLED'}
    
    def _clear_results(self, props) -> None:
        """Clear all result properties."""
        props.converter_hash_filename = ""
        props.converter_hash_extension = ""
        props.converter_hash_with_extension = ""
        props.converter_hash_legacy = ""


class MTAR_OT_CopyConverterOutput(Operator):
    """Copy hash result to clipboard."""
    bl_idname = "mtar.copy_converter_output"
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
        props = context.scene.mtar_converter_properties
        
        # Get the appropriate result based on key
        result_map = {
            'filename': props.converter_hash_filename,
            'extension': props.converter_hash_extension,
            'with_extension': props.converter_hash_with_extension,
            'legacy': props.converter_hash_legacy,
            'filename_dec': props.converter_hash_filename_dec,
            'extension_dec': props.converter_hash_extension_dec,
            'with_extension_dec': props.converter_hash_with_extension_dec,
            'legacy_dec': props.converter_hash_legacy_dec
        }
        
        output = result_map.get(self.result_key, '')
        
        if not output:
            self.report({'WARNING'}, f"No result to copy for {self.result_key}")
            return {'CANCELLED'}
        
        # Skip if it's an error message
        if output.startswith('Error:'):
            self.report({'WARNING'}, "Cannot copy error message")
            return {'CANCELLED'}
        
        context.window_manager.clipboard = output
        self.report({'INFO'}, f"Copied {self.result_key} to clipboard")
        return {'FINISHED'}


class MTAR_OT_ClearConverterResults(Operator):
    """Clear converter input and results."""
    bl_idname = "mtar.clear_converter_results"
    bl_label = "Clear"
    bl_description = "Clear converter input and all hash results"
    
    def execute(self, context: Context) -> set:
        """Execute the clear."""
        props = context.scene.mtar_converter_properties
        
        props.converter_input = ""
        props.converter_hash_filename = ""
        props.converter_hash_extension = ""
        props.converter_hash_with_extension = ""
        props.converter_hash_legacy = ""
        props.converter_error = ""
        
        self.report({'INFO'}, "Converter cleared")
        return {'FINISHED'}


class MTAR_PT_ConverterPanel(Panel):
    """N-Panel for external hash converter tool."""
    bl_label = "Hash Converter"
    bl_idname = "MTAR_PT_converter_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MTAR'
    
    def draw(self, context: Context) -> None:
        """Draw the converter panel."""
        layout = self.layout
        props = context.scene.mtar_converter_properties
        
        # Header
        box = layout.box()
        box.label(text="Filename Hash Converter", icon='FILE_REFRESH')
        
        # Configuration
        config_box = layout.box()
        config_box.label(text="Configuration", icon='SETTINGS')
        
        col = config_box.column(align=True)
        row = col.row(align=True)
        row.prop(props, "converter_exe_path", text="")
        row.operator("mtar.validate_converter_exe", text="", icon='FILE_TICK')
        col.label(text="https://mgsvmoddingwiki.github.io/GzsTool/")
        
        # Input
        input_box = layout.box()
        input_box.label(text="Input Filename", icon='IMPORT')
        col = input_box.column(align=True)
        col.prop(props, "converter_input", text="")
        
        # Action buttons
        button_box = layout.box()
        col = button_box.column(align=True)
        col.scale_y = 1.3
        
        row = col.row(align=True)
        row.operator("mtar.convert_with_external_exe", text="Hash", icon='PLAY')
        row.operator("mtar.clear_converter_results", text="Clear", icon='X')
        
        # Results
        results_box = layout.box()
        results_box.label(text="Hash Results", icon='INFO')
        
        # Check if we have any results
        has_results = (
            props.converter_hash_filename or 
            props.converter_hash_extension or 
            props.converter_hash_with_extension or 
            props.converter_hash_legacy
        )
        
        if has_results:
            # Hash Filename result
            if props.converter_hash_filename:
                self._draw_result_box(
                    results_box, 
                    "Hash Filename (-d -h)", 
                    props.converter_hash_filename,
                    'filename',
                    props.converter_hash_filename_dec
                )
            
            # Hash Extension result
            if props.converter_hash_extension:
                self._draw_result_box(
                    results_box, 
                    "Hash Extension (-d -he)", 
                    props.converter_hash_extension,
                    'extension',
                    props.converter_hash_extension_dec
                )
            
            # Hash With Extension result
            if props.converter_hash_with_extension:
                self._draw_result_box(
                    results_box, 
                    "Hash With Extension (-d -hwe)", 
                    props.converter_hash_with_extension,
                    'with_extension',
                    props.converter_hash_with_extension_dec
                )
            
            # Hash Legacy result
            if props.converter_hash_legacy:
                self._draw_result_box(
                    results_box, 
                    "Hash Legacy (-d -hl)", 
                    props.converter_hash_legacy,
                    'legacy',
                    props.converter_hash_legacy_dec
                )
        else:
            results_box.label(text="No results yet - enter filename and click Hash", icon='BLANK1')
        
        # Error
        if props.converter_error:
            error_box = results_box.box()
            error_box.label(text="Error:", icon='ERROR')
            col = error_box.column()
            # Split error by semicolons for better readability
            error_lines = props.converter_error.split(';')
            for error_line in error_lines:
                col.label(text=error_line.strip(), icon='NONE')
    
    def _draw_result_box(self, parent_box, label: str, value: str, key: str, decimal_value: str = "") -> None:
        """Draw a result box with copy button."""
        is_error = value.startswith('Error:')
        
        result_box = parent_box.box()
        row = result_box.row(align=True)
        
        if is_error:
            row.label(text=label, icon='CANCEL')
        else:
            row.label(text=label, icon='CHECKMARK')
            copy_op = row.operator("mtar.copy_converter_output", text="", icon='COPYDOWN')
            copy_op.result_key = key
            # If decimal value is available, add a secondary copy button for decimal
            if decimal_value:
                copy_op_dec = row.operator("mtar.copy_converter_output", text="", icon='SORTBYEXT')
                copy_op_dec.result_key = f"{key}_dec"
        
        col = result_box.column()
        col.label(text=value, icon='NONE')
        if decimal_value:
            col.label(text=f"Decimal: {decimal_value}", icon='NONE')


class MTAR_OT_ValidateConverterExe(Operator):
    """Validate converter executable path."""
    bl_idname = "mtar.validate_converter_exe"
    bl_label = "Validate Executable"
    bl_description = "Validate that the executable path is valid and accessible"
    
    def execute(self, context: Context) -> set:
        """Execute the validation."""
        from .py_tools.tools_hash_generator import validate_executable_path
        
        props = context.scene.mtar_converter_properties
        
        is_valid, error_msg = validate_executable_path(props.converter_exe_path)
        
        if is_valid:
            self.report({'INFO'}, "Executable path is valid")
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, f"Invalid executable: {error_msg}")
            return {'CANCELLED'}


# Registration
def register() -> None:
    """Register debug classes."""
    # Transform debug
    bpy.utils.register_class(MTAR_PG_DebugProperties)
    bpy.utils.register_class(MTAR_OT_InspectWorldSpaceTransform)
    bpy.utils.register_class(MTAR_OT_InspectLocalSpaceTransform)
    bpy.utils.register_class(MTAR_OT_CreateTransformDummies)
    bpy.utils.register_class(MTAR_OT_CopySingleResult)
    bpy.utils.register_class(MTAR_OT_CopyTransformDebugResults)
    bpy.utils.register_class(MTAR_PT_DebugPanel)
    
    # External converter
    bpy.utils.register_class(MTAR_PG_ConverterProperties)
    bpy.utils.register_class(MTAR_OT_ConvertWithExternalExe)
    bpy.utils.register_class(MTAR_OT_CopyConverterOutput)
    bpy.utils.register_class(MTAR_OT_ClearConverterResults)
    bpy.utils.register_class(MTAR_OT_ValidateConverterExe)
    bpy.utils.register_class(MTAR_PT_ConverterPanel)
    
    # Add debug properties to scene
    bpy.types.Scene.mtar_debug_properties = PointerProperty(type=MTAR_PG_DebugProperties)
    bpy.types.Scene.mtar_converter_properties = PointerProperty(type=MTAR_PG_ConverterProperties)


def unregister() -> None:
    """Unregister debug classes."""
    # Transform debug
    bpy.utils.unregister_class(MTAR_PT_DebugPanel)
    bpy.utils.unregister_class(MTAR_OT_CopyTransformDebugResults)
    bpy.utils.unregister_class(MTAR_OT_CopySingleResult)
    bpy.utils.unregister_class(MTAR_OT_CreateTransformDummies)
    bpy.utils.unregister_class(MTAR_OT_InspectLocalSpaceTransform)
    bpy.utils.unregister_class(MTAR_OT_InspectWorldSpaceTransform)
    bpy.utils.unregister_class(MTAR_PG_DebugProperties)
    
    # External converter
    bpy.utils.unregister_class(MTAR_PT_ConverterPanel)
    bpy.utils.unregister_class(MTAR_OT_ValidateConverterExe)
    bpy.utils.unregister_class(MTAR_OT_ClearConverterResults)
    bpy.utils.unregister_class(MTAR_OT_CopyConverterOutput)
    bpy.utils.unregister_class(MTAR_OT_ConvertWithExternalExe)
    bpy.utils.unregister_class(MTAR_PG_ConverterProperties)
    
    # Remove debug properties from scene
    if hasattr(bpy.types.Scene, 'mtar_debug_properties'):
        del bpy.types.Scene.mtar_debug_properties
    if hasattr(bpy.types.Scene, 'mtar_converter_properties'):
        del bpy.types.Scene.mtar_converter_properties
