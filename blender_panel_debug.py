"""
Debug utilities for MTAR tools - provides transform inspection panel and operators.

This module adds debugging capabilities to inspect local and world space transforms
for bones at specific frames, useful for verifying export/import transform correctness.
"""

# pyright: reportInvalidTypeForm=false

import bpy
from bpy.types import Panel, PropertyGroup, Context
from bpy.props import PointerProperty, StringProperty

from .blender_properties import _file_path_kwargs
from . import blender_panel_debug_map_r

from .blender_operators_debug import (
    MTAR_OT_InspectWorldSpaceTransform,
    MTAR_OT_InspectLocalSpaceTransform,
    MTAR_OT_CreateTransformDummies,
    MTAR_OT_CopySingleResult,
    MTAR_OT_CopyTransformDebugResults,
    MTAR_OT_DebugRunBake,
    MTAR_OT_DebugSetupGraphContext,
    MTAR_OT_ValidateHashGeneratorExe,
    MTAR_OT_GenerateHash,
    MTAR_OT_CopyHashGeneratorOutput,
    MTAR_OT_ClearHashGeneratorResults,
)


class MTAR_PG_DebugTransformProperties(PropertyGroup):
    """Property group for debug transform inspection settings."""
    
    debug_armature: PointerProperty(
        name="Armature",
        description="Armature to inspect",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE'
    )

    debug_source_armature: PointerProperty(
        name="Source Armature",
        description="Source armature to sync (imported rig)",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE'
    )
    
    debug_bake_gani_index: bpy.props.IntProperty(
        name="GANI Index",
        description="GANI index to bake (-1 = all)",
        default=-1,
        min=-1
    )

    debug_prepare_only: bpy.props.BoolProperty(
        name="Prepare Only",
        description="Only prepare the scene (mute source NLA, assign actions) without baking",
        default=False
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


class MTAR_PT_DebugBakePanel(Panel):
    """N-Panel for baking debug controls."""
    bl_label = "Debug - Bake"
    bl_idname = "MTAR_PT_debug_bake_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MTAR'
    
    def draw(self, context: Context) -> None:
        """Draw the bake debug panel."""
        layout = self.layout
        props = context.scene.mtar_debug_transform_properties
        
        # Header
        box = layout.box()
        box.label(text="Animation Bake", icon='RENDER_ANIMATION')
        
        # Configuration section
        config_box = layout.box()
        config_box.label(text="Configuration", icon='SETTINGS')
        col = config_box.column(align=True)
        
        # Duplicate armature field
        col.prop(props, "debug_armature", text="Target Armature")
        col.prop(props, "debug_source_armature", text="Source Armature")
        
        # GANI index and prepare only options
        row = col.row(align=True)
        row.prop(props, "debug_bake_gani_index")
        row.prop(props, "debug_prepare_only")
        
        # Bake button
        button_box = layout.box()
        button_box.label(text="Actions", icon='PLAY')
        
        row = button_box.row(align=True)
        row.scale_y = 1.3
        row.enabled = bool(props.debug_armature)
        row.operator("mtar.debug_run_bake", text="Run Bake", icon='FILE_REFRESH')
        
        # Debug context setup button
        debug_box = layout.box()
        debug_box.label(text="Debug Tools", icon='CONSOLE')
        row = debug_box.row(align=True)
        row.enabled = bool(props.debug_armature)
        row.operator("mtar.debug_setup_graph_context", text="Setup Graph Context", icon='GRAPH')
        debug_box.label(text="Sets up graph editor for manual testing", icon='INFO')


# External Hash Generator Panel ################################################################

class MTAR_PG_DebugHashProperties(PropertyGroup):
    """Property group for external hash generator settings."""
    
    hash_generator_exe_path: StringProperty(**_file_path_kwargs(
        name="Hash Generator Executable",
        description="Path to the external hash generator executable (GzsTool fork with debug output)",
        default="",
        maxlen=1024
    ))

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

    # Python CityHash results (mirrors exe results for side-by-side comparison)
    hash_generator_py_hash_filename: StringProperty(
        name="Python Hash Filename",
        description="Python CityHash: hashed filename without extension (-d -h)",
        default="",
        maxlen=4096
    )
    hash_generator_py_hash_filename_dec: StringProperty(
        name="Python Hash Filename (dec)",
        description="Python CityHash: decimal of hashed filename",
        default="",
        maxlen=4096
    )
    hash_generator_py_hash_extension: StringProperty(
        name="Python Hash Extension",
        description="Python CityHash: hashed extension digits (-d -he)",
        default="",
        maxlen=4096
    )
    hash_generator_py_hash_extension_dec: StringProperty(
        name="Python Hash Extension (dec)",
        description="Python CityHash: decimal of hashed extension",
        default="",
        maxlen=4096
    )
    hash_generator_py_hash_with_extension: StringProperty(
        name="Python Hash With Extension",
        description="Python CityHash: hashed filename with extension (-d -hwe)",
        default="",
        maxlen=4096
    )
    hash_generator_py_hash_with_extension_dec: StringProperty(
        name="Python Hash With Extension (dec)",
        description="Python CityHash: decimal of hashed filename with extension",
        default="",
        maxlen=4096
    )
    hash_generator_py_hash_legacy: StringProperty(
        name="Python Hash Legacy",
        description="Python CityHash: legacy hash function (-d -hl)",
        default="",
        maxlen=4096
    )
    hash_generator_py_hash_legacy_dec: StringProperty(
        name="Python Hash Legacy (dec)",
        description="Python CityHash: decimal of legacy hash",
        default="",
        maxlen=4096
    )
    hash_generator_py_error: StringProperty(
        name="Python Hash Error",
        description="Error from Python CityHash computation",
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

        # Resolve exe availability (exe is optional — Python always runs)
        exe_configured = bool(props.hash_generator_exe_path)

        # Show executable input when needed
        exe_box = layout.box()
        exe_box.label(text="External Hash Generator", icon='FILE_SCRIPT')
        exe_box.label(text="Needed for custom hashes.")
        row = exe_box.row(align=True)
        row.prop(props, "hash_generator_exe_path", text="")
        row.operator("mtar.validate_hash_generator_exe", text="", icon='FORCE_HARMONIC')
        exe_box.label(text="https://mgsvmoddingwiki.github.io/GzsTool/")

        if not exe_configured:
            info_box = layout.box()
            info_box.label(text="Exe not configured — Python only", icon='INFO')
            info_box.label(text="Configure path above for exe column")

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
        row.operator("mtar.generate_hash", text="Hash", icon='PLAY')
        row.operator("mtar.clear_hash_generator_results", text="Clear", icon='X')

        # Results
        results_box = layout.box()
        results_box.label(text="Hash Results", icon='INFO')

        has_py_results = bool(
            props.hash_generator_py_hash_filename
            or props.hash_generator_py_hash_with_extension
            or props.hash_generator_py_hash_legacy
        )
        has_exe_results = bool(
            props.hash_generator_hash_filename
            or props.hash_generator_hash_with_extension
            or props.hash_generator_hash_legacy
        )

        if has_py_results or has_exe_results:
            # Column header row
            header = results_box.row(align=False)
            header.label(text="")         # mode label placeholder
            header.label(text="Python")
            header.label(text="Exe")
            header.label(text="")         # match icon placeholder

            self._draw_comparison_row(
                results_box,
                "Hash Filename  (-d -h)",
                exe_hex=props.hash_generator_hash_filename,
                exe_dec=props.hash_generator_hash_filename_dec,
                exe_key='filename',
                py_hex=props.hash_generator_py_hash_filename,
                py_dec=props.hash_generator_py_hash_filename_dec,
                py_key='py_filename',
                exe_configured=exe_configured,
            )
            self._draw_comparison_row(
                results_box,
                "Hash Extension  (-d -he)",
                exe_hex=props.hash_generator_hash_extension,
                exe_dec=props.hash_generator_hash_extension_dec,
                exe_key='extension',
                py_hex=props.hash_generator_py_hash_extension,
                py_dec=props.hash_generator_py_hash_extension_dec,
                py_key='py_extension',
                exe_configured=exe_configured,
            )
            self._draw_comparison_row(
                results_box,
                "Hash With Ext  (-d -hwe)",
                exe_hex=props.hash_generator_hash_with_extension,
                exe_dec=props.hash_generator_hash_with_extension_dec,
                exe_key='with_extension',
                py_hex=props.hash_generator_py_hash_with_extension,
                py_dec=props.hash_generator_py_hash_with_extension_dec,
                py_key='py_with_extension',
                exe_configured=exe_configured,
            )
            self._draw_comparison_row(
                results_box,
                "Hash Legacy  (-d -hl)",
                exe_hex=props.hash_generator_hash_legacy,
                exe_dec=props.hash_generator_hash_legacy_dec,
                exe_key='legacy',
                py_hex=props.hash_generator_py_hash_legacy,
                py_dec=props.hash_generator_py_hash_legacy_dec,
                py_key='py_legacy',
                exe_configured=exe_configured,
            )
        else:
            results_box.label(text="No results yet — enter a filename and click Hash")

        # Errors
        for err_label, err_val in (
            ("Exe error", props.hash_generator_error),
            ("Python error", props.hash_generator_py_error),
        ):
            if err_val:
                error_box = results_box.box()
                error_box.alert = True
                error_box.label(text=f"{err_label}:", icon='ERROR')
                for line in err_val.split(';'):
                    error_box.label(text=line.strip(), icon='NONE')

    def _draw_comparison_row(
        self,
        parent,
        label: str,
        exe_hex: str,
        exe_dec: str,
        exe_key: str,
        py_hex: str,
        py_dec: str,
        py_key: str,
        exe_configured: bool,
    ) -> None:
        """Draw one result row with Python and exe sub-columns plus a match/mismatch icon."""
        row_box = parent.box()
        row_box.label(text=label, icon='NONE')

        split = row_box.row(align=False)

        # ---- Python column ----
        py_col = split.column(align=True)
        py_col.label(text="Python", icon='SCRIPTPLUGINS')
        if py_hex:
            py_row = py_col.row(align=True)
            py_row.label(text=py_hex)
            op = py_row.operator("mtar.copy_hash_generator_output", text="", icon='COPYDOWN')
            op.result_key = py_key
            if py_dec:
                op_dec = py_row.operator("mtar.copy_hash_generator_output", text="", icon='SORTBYEXT')
                op_dec.result_key = f"{py_key}_dec"
            if py_dec:
                py_col.label(text=f"Dec: {py_dec}", icon='NONE')
        else:
            py_col.label(text="—", icon='NONE')

        # ---- Exe column ----
        exe_col = split.column(align=True)
        if exe_configured:
            exe_col.label(text="Exe", icon='FILE_SCRIPT')
            if exe_hex:
                exe_row = exe_col.row(align=True)
                exe_row.label(text=exe_hex)
                op = exe_row.operator("mtar.copy_hash_generator_output", text="", icon='COPYDOWN')
                op.result_key = exe_key
                if exe_dec:
                    op_dec = exe_row.operator("mtar.copy_hash_generator_output", text="", icon='SORTBYEXT')
                    op_dec.result_key = f"{exe_key}_dec"
                if exe_dec:
                    exe_col.label(text=f"Dec: {exe_dec}", icon='NONE')
            else:
                exe_col.label(text="—", icon='NONE')
        else:
            exe_col.label(text="Exe", icon='FILE_SCRIPT')
            exe_col.label(text="(not configured)", icon='NONE')

        # ---- Match / mismatch icon ----
        icon_col = split.column()
        if exe_configured and py_hex and exe_hex:
            match = py_hex.lstrip('0') == exe_hex.lstrip('0')
            icon_col.label(text="", icon='CHECKMARK' if match else 'ERROR')
        else:
            icon_col.label(text="", icon='NONE')


# Registration
classes = (
    MTAR_PG_DebugTransformProperties,
    MTAR_OT_InspectWorldSpaceTransform,
    MTAR_OT_InspectLocalSpaceTransform,
    MTAR_OT_CreateTransformDummies,
    MTAR_OT_CopySingleResult,
    MTAR_OT_CopyTransformDebugResults,
    MTAR_OT_DebugRunBake,
    MTAR_OT_DebugSetupGraphContext,
    MTAR_PT_DebugTransformPanel,
    MTAR_PT_DebugBakePanel,
    MTAR_PG_DebugHashProperties,
    MTAR_OT_ValidateHashGeneratorExe,
    MTAR_OT_GenerateHash,
    MTAR_OT_CopyHashGeneratorOutput,
    MTAR_OT_ClearHashGeneratorResults,
    MTAR_PT_DebugHashPanel,
)

def register() -> None:
    """Register debug classes."""
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception:
            # Ignore errors (likely already registered from a previous reload)
            pass

    # Add debug properties to scene (only if not already present)
    if not hasattr(bpy.types.Scene, 'mtar_debug_transform_properties'):
        bpy.types.Scene.mtar_debug_transform_properties = PointerProperty(type=MTAR_PG_DebugTransformProperties)
    if not hasattr(bpy.types.Scene, 'mtar_debug_hash_properties'):
        bpy.types.Scene.mtar_debug_hash_properties = PointerProperty(type=MTAR_PG_DebugHashProperties)
    
    # Register map_r debug module
    try:
        blender_panel_debug_map_r.register()
    except Exception:
        # ignore if already registered or missing
        pass


def unregister() -> None:
    """Unregister debug classes."""
    # Unregister map_r debug module first
    try:
        blender_panel_debug_map_r.unregister()
    except Exception:
        pass
    
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            # Ignore errors during unregister
            pass

    # Remove debug properties from scene
    if hasattr(bpy.types.Scene, 'mtar_debug_transform_properties'):
        del bpy.types.Scene.mtar_debug_transform_properties
    if hasattr(bpy.types.Scene, 'mtar_debug_hash_properties'):
        del bpy.types.Scene.mtar_debug_hash_properties
