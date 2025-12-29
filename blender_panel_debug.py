"""
Debug utilities for MTAR tools - provides transform inspection panel and operators.

This module adds debugging capabilities to inspect local and world space transforms
for bones at specific frames, useful for verifying export/import transform correctness.
"""


import bpy
from bpy.types import Panel, PropertyGroup, Context, Object
from bpy.props import PointerProperty, StringProperty

from . import blender_panel_debug_map_r

from .blender_operators_debug import (
    MTAR_OT_InspectWorldSpaceTransform,
    MTAR_OT_InspectLocalSpaceTransform,
    MTAR_OT_CreateTransformDummies,
    MTAR_OT_CopySingleResult,
    MTAR_OT_CopyTransformDebugResults,
    MTAR_OT_GenerateHashWithExternalExe,
    MTAR_OT_CopyHashGeneratorOutput,
    MTAR_OT_ClearHashGeneratorResults,
)


# pyright: reportInvalidTypeForm=false

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


class MTAR_PG_DebugTransformProperties(PropertyGroup):
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



class MTAR_PT_DebugTransformPanel(Panel):
    """N-Panel for transform debugging and inspection."""
    bl_label = "Debug - Transform"
    bl_idname = "MTAR_PT_debug_transform_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MTAR'
    
    def draw(self, context: Context) -> None:
        """Draw the debug panel."""
        layout = self.layout
        props = context.scene.mtar_debug_transform_properties
        
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


# External Hash Generator Panel ################################################################

class MTAR_PG_DebugHashProperties(PropertyGroup):
    """Property group for external hash generator settings."""
    
    # Note: the executable path is stored in scene.mtar_properties.settings_props.hash_generator_exe_path
    
    hash_generator_input: StringProperty(
        name="Input",
        description="Input filename (with or without extension)",
        default="",
        maxlen=4096
    )
    
    # Results for each hash mode
    hash_generator_hash_filename: StringProperty(
        name="Hash Filename",
        description="Hashed filename without extension (-d -h)",
        default="",
        maxlen=4096
    )
    
    hash_generator_hash_extension: StringProperty(
        name="Hash Extension",
        description="Hashed extension digits (-d -he)",
        default="",
        maxlen=4096
    )
    
    hash_generator_hash_with_extension: StringProperty(
        name="Hash With Extension",
        description="Hashed filename with extension (-d -hwe)",
        default="",
        maxlen=4096
    )
    
    hash_generator_hash_legacy: StringProperty(
        name="Hash Legacy",
        description="Legacy hash function (-d -hl)",
        default="",
        maxlen=4096
    )
    
    hash_generator_error: StringProperty(
        name="Error",
        description="Error message if conversion failed",
        default="",
        maxlen=4096
    )

    # Decimal representations
    hash_generator_hash_filename_dec: StringProperty(
        name="Hash Filename (dec)",
        description="Decimal representation of hashed filename",
        default="",
        maxlen=4096
    )
    hash_generator_hash_extension_dec: StringProperty(
        name="Hash Extension (dec)",
        description="Decimal representation of hashed extension",
        default="",
        maxlen=4096
    )
    hash_generator_hash_with_extension_dec: StringProperty(
        name="Hash With Extension (dec)",
        description="Decimal representation of hashed filename with extension",
        default="",
        maxlen=4096
    )
    hash_generator_hash_legacy_dec: StringProperty(
        name="Hash Legacy (dec)",
        description="Decimal representation of legacy hash",
        default="",
        maxlen=4096
    )


class MTAR_PT_DebugHashPanel(Panel):
    """N-Panel for external hash generator tool."""
    bl_label = "Debug - Hash"
    bl_idname = "MTAR_PT_debug_hash_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MTAR'
    
    def draw(self, context: Context) -> None:
        """Draw the hash generator panel."""
        layout = self.layout
        props = context.scene.mtar_debug_hash_properties
        scene = context.scene

        # If the hash generator exe path is not configured in the MTAR Settings, show an info
        # box and don't render the rest of the hash generator debug UI. This avoids confusion
        # where users might try to hash filenames without configuring the external exe path.
        if not hasattr(scene, 'mtar_properties') or not scene.mtar_properties.settings_props.hash_generator_exe_path:
            info_box = layout.box()
            info_box.label(text="Hash Generator not configured", icon='ERROR')
            info_box.label(text="Configure 'Hash Generator Executable' in Settings panel")
            return
        
        # Input
        input_box = layout.box()
        input_box.label(text="Filename", icon='IMPORT')
        col = input_box.column(align=True)
        col.prop(props, "hash_generator_input", text="")
        
        # Action buttons
        button_box = layout.box()
        col = button_box.column(align=True)
        col.scale_y = 1.3
        
        row = col.row(align=True)
        row.operator("mtar.generate_hash_with_external_exe", text="Hash", icon='PLAY')
        row.operator("mtar.clear_hash_generator_results", text="Clear", icon='X')
        
        # Results
        results_box = layout.box()
        results_box.label(text="Hash Results", icon='INFO')
        
        # Check if we have any results
        has_results = (
            props.hash_generator_hash_filename or 
            props.hash_generator_hash_extension or 
            props.hash_generator_hash_with_extension or 
            props.hash_generator_hash_legacy
        )
        
        if has_results:
            # Hash Filename result
            if props.hash_generator_hash_filename:
                self._draw_result_box(
                    results_box, 
                    "Hash Filename (-d -h)", 
                    props.hash_generator_hash_filename,
                    'filename',
                    props.hash_generator_hash_filename_dec
                )
            
            # Hash Extension result
            if props.hash_generator_hash_extension:
                self._draw_result_box(
                    results_box, 
                    "Hash Extension (-d -he)", 
                    props.hash_generator_hash_extension,
                    'extension',
                    props.hash_generator_hash_extension_dec
                )
            
            # Hash With Extension result
            if props.hash_generator_hash_with_extension:
                self._draw_result_box(
                    results_box, 
                    "Hash With Extension (-d -hwe)", 
                    props.hash_generator_hash_with_extension,
                    'with_extension',
                    props.hash_generator_hash_with_extension_dec
                )
            
            # Hash Legacy result
            if props.hash_generator_hash_legacy:
                self._draw_result_box(
                    results_box, 
                    "Hash Legacy (-d -hl)", 
                    props.hash_generator_hash_legacy,
                    'legacy',
                    props.hash_generator_hash_legacy_dec
                )
        else:
            results_box.label(text="No results yet - enter filename and click Hash")
        
        # Error
        if props.hash_generator_error:
            error_box = results_box.box()
            error_box.label(text="Error:", icon='ERROR')
            col = error_box.column()
            # Split error by semicolons for better readability
            error_lines = props.hash_generator_error.split(';')
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
            copy_op = row.operator("mtar.copy_hash_generator_output", text="", icon='COPYDOWN')
            copy_op.result_key = key
            # If decimal value is available, add a secondary copy button for decimal
            if decimal_value:
                copy_op_dec = row.operator("mtar.copy_hash_generator_output", text="", icon='SORTBYEXT')
                copy_op_dec.result_key = f"{key}_dec"
        
        col = result_box.column()
        col.label(text=value, icon='NONE')
        if decimal_value:
            col.label(text=f"Decimal: {decimal_value}", icon='NONE')


# Registration
classes = (
    MTAR_PG_DebugTransformProperties,
    MTAR_OT_InspectWorldSpaceTransform,
    MTAR_OT_InspectLocalSpaceTransform,
    MTAR_OT_CreateTransformDummies,
    MTAR_OT_CopySingleResult,
    MTAR_OT_CopyTransformDebugResults,
    MTAR_PT_DebugTransformPanel,
    MTAR_PG_DebugHashProperties,
    MTAR_OT_GenerateHashWithExternalExe,
    MTAR_OT_CopyHashGeneratorOutput,
    MTAR_OT_ClearHashGeneratorResults,
    MTAR_PT_DebugHashPanel,
)

def register() -> None:
    """Register debug classes."""
    for cls in classes:
        bpy.utils.register_class(cls)

    # Add debug properties to scene
    bpy.types.Scene.mtar_debug_transform_properties = PointerProperty(type=MTAR_PG_DebugTransformProperties)
    bpy.types.Scene.mtar_debug_hash_properties = PointerProperty(type=MTAR_PG_DebugHashProperties)
    
    # Register map_r debug module
    blender_panel_debug_map_r.register()


def unregister() -> None:
    """Unregister debug classes."""
    # Unregister map_r debug module first
    blender_panel_debug_map_r.unregister()
    
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    # Remove debug properties from scene
    if hasattr(bpy.types.Scene, 'mtar_debug_transform_properties'):
        del bpy.types.Scene.mtar_debug_transform_properties
    if hasattr(bpy.types.Scene, 'mtar_debug_hash_properties'):
        del bpy.types.Scene.mtar_debug_hash_properties
