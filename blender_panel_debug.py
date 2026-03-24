"""
Debug utilities for MTAR tools - provides transform inspection panel and operators.

This module adds debugging capabilities to inspect local and world space transforms
for bones at specific frames, useful for verifying export/import transform correctness.
"""

# pyright: reportInvalidTypeForm=false

import bpy
from bpy.types import Panel, PropertyGroup, Context, UILayout
from bpy.props import PointerProperty, StringProperty

from .blender_properties import _file_path_kwargs

from . import blender_panel_debug_map_r, blender_panel_debug_transform, blender_panel_debug_bake, blender_panel_debug_hash, blender_panel_debug_misc
from .blender_operators_debug_transform import (
    MTAR_OT_InspectWorldSpaceTransform,
    MTAR_OT_InspectLocalSpaceTransform,
    MTAR_OT_CreateTransformDummies,
    MTAR_OT_CopySingleResult,
    MTAR_OT_CopyTransformDebugResults,
)
from .blender_operators_debug import (
    MTAR_OT_DebugCollectNLAPathClipboard,
    MTAR_OT_DebugSelectNLAByClipboardIndex,
    MTAR_OT_DebugToggleMuteNLAByClipboardIndex,
    MTAR_OT_DebugMuteNLAByClipboardIndex,
    MTAR_OT_DebugUnmuteNLAByClipboardIndex,
    MTAR_OT_DebugMuteAllNLA,
    MTAR_OT_DebugUnmuteAllNLA,
    # Bake
    MTAR_OT_DebugRunBake,
    MTAR_OT_DebugSetupGraphContext,
)
from .blender_operators_debug_hash import (
    MTAR_OT_ValidateHashGeneratorExe,
    MTAR_OT_GenerateHash,
    MTAR_OT_CopyHashGeneratorOutput,
    MTAR_OT_ClearHashGeneratorResults,
    MTAR_OT_ComputeStrCode32,
    MTAR_OT_ClearStrCode32Results,
    MTAR_OT_CopyStrCode32Result,
    MTAR_OT_UnhashPath,
    MTAR_OT_ClearUnhashPath,
    MTAR_OT_UnhashStrCode32,
    MTAR_OT_ClearUnhashStrCode32,
)

from .blender_operators_debug_misc import (
    MTAR_OT_DebugCopyNLAPathByFilterFile,
    MTAR_OT_DebugCopyNLADByFilterFile,
    MTAR_OT_DebugCopyNLAHByFilterFile,
    MTAR_OT_DebugToggleMuteNLAByFilterFile,
    MTAR_OT_DebugMuteNLAByFilterFile,
    MTAR_OT_DebugUnmuteNLAByFilterFile,
    MTAR_OT_DebugSelectNLAByFilterFile,
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

    debug_misc_input_mode: bpy.props.EnumProperty(
        name="Input Source",
        description="Choose whether to use the system clipboard, filter file, or CSV string source for Misc debug operations",
        items=[
            ('CLIPBOARD', "Clipboard", "Use the Blender system clipboard input"),
            ('FILTER_FILE', "Filter File", "Use the configured GANI filter file"),
            ('CSV', "CSV String", "Use the custom comma-separated values in debug CSV field"),
        ],
        default='FILTER_FILE'
    )

    debug_misc_csv_input: bpy.props.StringProperty(
        name="CSV Input",
        description="Comma-separated hN/dN entries or raw indices used for Misc debug operations when CSV mode is selected",
        default="",
        maxlen=4096,
    )

    # which debug page is currently active in the unified panel
    debug_active_tab: bpy.props.EnumProperty(
        name="Page",
        items=[
            ('TRANSFORM', "Transform", "Transform inspector"),
            ('BAKE', "Bake", "Animation bake tools"),
            ('HASH', "Hash", "External hash generator"),
            ('MAP_R', "Map R", "Map_R parameter debug"),
            ('MISC', "Misc", "Miscellaneous debug tools"),
        ],
        default='TRANSFORM'
    )


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

    # StrCode32 animation name hashing
    strcode32_input: StringProperty(
        name="Input",
        description="Animation/track name to hash (e.g., bone name, event name)",
        default="",
        maxlen=4096
    )

    strcode32_remove_extension: bpy.props.BoolProperty(
        name="Remove Extension",
        description="If True, strip extension at first '.' before hashing",
        default=True
    )

    strcode32_result: StringProperty(
        name="StrCode32 Result",
        description="Computed StrCode32 hash value",
        default="",
        maxlen=4096
    )

    strcode32_result_dec: StringProperty(
        name="StrCode32 Result (dec)",
        description="Decimal representation of StrCode32 hash",
        default="",
        maxlen=4096
    )

    strcode32_error: StringProperty(
        name="StrCode32 Error",
        description="Error message if hash computation failed",
        default="",
        maxlen=4096
    )

    # Unhash PathCode64 (reverse lookup)
    unhash_path_input: StringProperty(
        name="PathCode64 Hash Input",
        description="Decimal or 0x-prefixed hex PathCode64 hash to reverse-lookup in dic/path64/mtar_dictionary.txt",
        default="",
        maxlen=64
    )
    unhash_path_result: StringProperty(
        name="PathCode64 Unhash Result",
        description="Resolved asset path from dictionary lookup",
        default="",
        maxlen=4096
    )

    # Unhash StrCode32 (reverse lookup)
    unhash_strcode32_input: StringProperty(
        name="StrCode32 Hash Input",
        description="Decimal or 0x-prefixed hex StrCode32 hash to reverse-lookup in dic/str32/*.txt dictionaries",
        default="",
        maxlen=32
    )
    unhash_strcode32_result: StringProperty(
        name="StrCode32 Unhash Result",
        description="Resolved name string from dictionary lookup",
        default="",
        maxlen=4096
    )


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


class MTAR_PT_DebugMainPanel(Panel):
    """Unified debug panel with tabs."""
    bl_label = "Debug Tools"
    bl_idname = "MTAR_PT_debug_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Fox MTAR'

    def draw(self, context: Context) -> None:
        layout = self.layout
        props = context.scene.mtar_debug_transform_properties

        row = layout.row(align=True)
        row.prop(props, "debug_active_tab", expand=True)

        layout.separator()
        tab = props.debug_active_tab
        if tab == 'TRANSFORM':
            blender_panel_debug_transform.draw_transform_page(layout, context)
        elif tab == 'BAKE':
            blender_panel_debug_bake.draw_bake_page(layout, context)
        elif tab == 'HASH':
            blender_panel_debug_hash.draw_hash_page(layout, context)
        elif tab == 'MAP_R':
            blender_panel_debug_map_r.draw_map_r_page(layout, context)
        elif tab == 'MISC':
            blender_panel_debug_misc.draw_misc_page(layout, context)

# Registration
classes = (
    # Transform
    MTAR_PG_DebugTransformProperties,
    MTAR_OT_InspectWorldSpaceTransform,
    MTAR_OT_InspectLocalSpaceTransform,
    MTAR_OT_CreateTransformDummies,
    MTAR_OT_CopySingleResult,
    MTAR_OT_CopyTransformDebugResults,
    MTAR_OT_DebugCollectNLAPathClipboard,
    MTAR_OT_DebugSelectNLAByClipboardIndex,
    MTAR_OT_DebugToggleMuteNLAByClipboardIndex,
    MTAR_OT_DebugMuteNLAByClipboardIndex,
    MTAR_OT_DebugUnmuteNLAByClipboardIndex,
    MTAR_OT_DebugMuteAllNLA,
    MTAR_OT_DebugUnmuteAllNLA,
    MTAR_OT_DebugCopyNLAPathByFilterFile,
    MTAR_OT_DebugCopyNLADByFilterFile,
    MTAR_OT_DebugCopyNLAHByFilterFile,
    MTAR_OT_DebugToggleMuteNLAByFilterFile,
    MTAR_OT_DebugMuteNLAByFilterFile,
    MTAR_OT_DebugUnmuteNLAByFilterFile,
    MTAR_OT_DebugSelectNLAByFilterFile,
    # Bake
    MTAR_OT_DebugRunBake,
    MTAR_OT_DebugSetupGraphContext,
    # Hash
    MTAR_PG_DebugHashProperties,
    MTAR_OT_ValidateHashGeneratorExe,
    MTAR_OT_GenerateHash,
    MTAR_OT_CopyHashGeneratorOutput,
    MTAR_OT_ClearHashGeneratorResults,
    MTAR_OT_ComputeStrCode32,
    MTAR_OT_ClearStrCode32Results,
    MTAR_OT_CopyStrCode32Result,
    MTAR_OT_UnhashPath,
    MTAR_OT_ClearUnhashPath,
    MTAR_OT_UnhashStrCode32,
    MTAR_OT_ClearUnhashStrCode32,
    #
    MTAR_PT_DebugMainPanel,
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
