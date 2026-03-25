"""
Debug utilities for MTAR tools - provides transform inspection panel and operators.

This module adds debugging capabilities to inspect local and world space transforms
for bones at specific frames, useful for verifying export/import transform correctness.
"""

# pyright: reportInvalidTypeForm=false

import bpy
from bpy.types import Panel, Context

from . import blender_panel_debug_map_r, blender_panel_debug_nla, blender_panel_debug_transform, blender_panel_debug_bake, blender_panel_debug_hash
from .blender_operators_debug_transform import (
    MTAR_PG_DebugTransformProperties,
    MTAR_OT_InspectWorldSpaceTransform,
    MTAR_OT_CreateTransformDummies,
    MTAR_OT_CopySingleResult,
    MTAR_OT_CopyTransformDebugResults,
)
from .blender_operators_debug import (
    MTAR_PG_DebugPanelProperties,
)
from .blender_operators_debug_nla import (
    MTAR_PG_DebugNLAProperties,
    MTAR_OT_DebugCollectNLAPathClipboard,
    MTAR_OT_DebugSelectNLAByClipboardIndex,
    MTAR_OT_DebugToggleMuteNLAByClipboardIndex,
    MTAR_OT_DebugMuteNLAByClipboardIndex,
    MTAR_OT_DebugUnmuteNLAByClipboardIndex,
    MTAR_OT_DebugCopyNLAPathByFilterFile,
    MTAR_OT_DebugCopyNLADByFilterFile,
    MTAR_OT_DebugCopyNLAHByFilterFile,
    MTAR_OT_DebugMuteNLAByFilterFile,
    MTAR_OT_DebugUnmuteNLAByFilterFile,
    MTAR_OT_DebugSelectNLAByFilterFile,
    MTAR_OT_DebugMuteAllNLA,
    MTAR_OT_DebugUnmuteAllNLA,
)
from .blender_operators_debug_bake import (
    MTAR_OT_DebugRunBake,
    MTAR_OT_DebugSetupGraphContext,
)
from .blender_operators_debug_hash import (
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
)


class MTAR_PT_DebugMainPanel(Panel):
    """Unified debug panel with tabs."""
    bl_label = "Debug Tools"
    bl_idname = "MTAR_PT_debug_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Fox MTAR'

    def draw(self, context: Context) -> None:
        layout = self.layout
        props = context.scene.mtar_debug_panel_properties

        row = layout.row(align=True)
        row.prop(props, "debug_active_tab", expand=True)

        layout.separator()
        tab = props.debug_active_tab
        if tab == 'HASH':
            blender_panel_debug_hash.draw_hash_page(layout, context)
        elif tab == 'NLA':
            blender_panel_debug_nla.draw_nla_page(layout, context)
        elif tab == 'TRANSFORM':
            blender_panel_debug_transform.draw_transform_page(layout, context)
        elif tab == 'BAKE':
            blender_panel_debug_bake.draw_bake_page(layout, context)
        elif tab == 'MAP_R':
            blender_panel_debug_map_r.draw_map_r_page(layout, context)


# Registration
classes = (
    # Transform
    MTAR_PG_DebugTransformProperties,
    MTAR_OT_InspectWorldSpaceTransform,
    MTAR_OT_CreateTransformDummies,
    MTAR_OT_CopySingleResult,
    MTAR_OT_CopyTransformDebugResults,
    # Panel mode
    MTAR_PG_DebugPanelProperties,
    # NLA clipboard/select/mute
    MTAR_PG_DebugNLAProperties,
    MTAR_OT_DebugCollectNLAPathClipboard,
    MTAR_OT_DebugSelectNLAByClipboardIndex,
    MTAR_OT_DebugToggleMuteNLAByClipboardIndex,
    MTAR_OT_DebugMuteNLAByClipboardIndex,
    MTAR_OT_DebugUnmuteNLAByClipboardIndex,
    # NLA filter operations
    MTAR_OT_DebugMuteAllNLA,
    MTAR_OT_DebugUnmuteAllNLA,
    MTAR_OT_DebugCopyNLAPathByFilterFile,
    MTAR_OT_DebugCopyNLADByFilterFile,
    MTAR_OT_DebugCopyNLAHByFilterFile,
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
        bpy.types.Scene.mtar_debug_transform_properties = bpy.props.PointerProperty(type=MTAR_PG_DebugTransformProperties)
    if not hasattr(bpy.types.Scene, 'mtar_debug_nla_properties'):
        bpy.types.Scene.mtar_debug_nla_properties = bpy.props.PointerProperty(type=MTAR_PG_DebugNLAProperties)
    if not hasattr(bpy.types.Scene, 'mtar_debug_hash_properties'):
        bpy.types.Scene.mtar_debug_hash_properties = bpy.props.PointerProperty(type=MTAR_PG_DebugHashProperties)
    if not hasattr(bpy.types.Scene, 'mtar_debug_panel_properties'):
        bpy.types.Scene.mtar_debug_panel_properties = bpy.props.PointerProperty(type=MTAR_PG_DebugPanelProperties)
    
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
    if hasattr(bpy.types.Scene, 'mtar_debug_nla_properties'):
        del bpy.types.Scene.mtar_debug_nla_properties
    if hasattr(bpy.types.Scene, 'mtar_debug_hash_properties'):
        del bpy.types.Scene.mtar_debug_hash_properties
    if hasattr(bpy.types.Scene, 'mtar_debug_panel_properties'):
        del bpy.types.Scene.mtar_debug_panel_properties
