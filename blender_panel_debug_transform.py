"""Debug Transform page extracted from blender_panel_debug."""

import bpy
from bpy.types import UILayout, Context

from .py_core.core_logging import Debug
from .py_utilities import util_transforms, util_debug


def draw_transform_page(layout: UILayout, context: Context) -> None:
    """Draw the transform debug page."""
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
        row = col.row(align=True)
        row.prop(props, "debug_bone_name", text="Bone")
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

    buttons_enabled = bool(props.debug_armature and props.debug_bone_name)
    row = col.row(align=True)
    row.enabled = buttons_enabled
    row.operator("mtar.inspect_world_space_transform", text="World Space", icon='WORLD')

    row = col.row(align=True)
    row.enabled = buttons_enabled
    row.operator("mtar.inspect_local_space_transform", text="Local Space", icon='BONE_DATA')

    row = col.row(align=True)
    row.enabled = buttons_enabled
    row.operator("mtar.create_transform_dummies", text="Create Dummies", icon='MESH_CIRCLE')

    # Results display
    results_box = layout.box()
    results_box.label(text="Results", icon='CHECKMARK')

    if props.debug_world_space_result:
        world_box = results_box.box()
        row = world_box.row(align=True)
        row.label(text="World Space:", icon='WORLD')
        row.operator("mtar.copy_single_result", text="", icon='COPYDOWN').result_type = 'WORLD'
        col = world_box.column()
        col.label(text=props.debug_world_space_result, icon='NONE')
    else:
        results_box.label(text="World Space: (no result yet)", icon='WORLD')

    if props.debug_local_space_result:
        local_box = results_box.box()
        row = local_box.row(align=True)
        row.label(text="Local Space:", icon='BONE_DATA')
        row.operator("mtar.copy_single_result", text="", icon='COPYDOWN').result_type = 'LOCAL'
        col = local_box.column()
        col.label(text=props.debug_local_space_result, icon='NONE')
    else:
        results_box.label(text="Local Space: (no result yet)", icon='BONE_DATA')

    if props.debug_world_space_result or props.debug_local_space_result:
        results_box.operator("mtar.copy_transform_debug_results", text="Copy All Results", icon='COPYDOWN')
