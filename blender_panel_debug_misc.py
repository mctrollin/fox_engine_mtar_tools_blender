"""Debug Misc page extracted from blender_panel_debug."""

import bpy
from bpy.types import UILayout, Context


def draw_misc_page(layout: UILayout, context: Context) -> None:
    """Draw miscellaneous debug tools (filter file access)."""

    cfg_box = layout.box()
    cfg_box.label(text="GANI Filter File Scanner", icon='FILE_TICK')

    row = cfg_box.row(align=True)
    row.prop(context.scene.mtar_debug_transform_properties, "debug_misc_input_mode", text="")

    if context.scene.mtar_debug_transform_properties.debug_misc_input_mode == 'FILTER_FILE':
        row = cfg_box.row(align=True)
        row.prop(context.scene.mtar_properties, "gani_filter_txt_filepath", text="", icon='FILE_TEXT')
    elif context.scene.mtar_debug_transform_properties.debug_misc_input_mode == 'CSV':
        row = cfg_box.row(align=True)
        row.prop(context.scene.mtar_debug_transform_properties, "debug_misc_csv_input", text="")

    col = cfg_box.column(align=True)
    col.label(text="Supports hashes (hex/dec), paths, hN/dN", icon='INFO')
    col.label(text="Use ! prefix to exclude entries", icon='INFO')

    row = cfg_box.row(align=True)
    row.operator("mtar.debug_copy_nla_path_by_filter", text="Copy Paths", icon='COPYDOWN')
    row.operator("mtar.debug_copy_nla_d_by_filter", text="Copy dN", icon='SNAP_FACE')
    row.operator("mtar.debug_copy_nla_h_by_filter", text="Copy hN", icon='SNAP_VERTEX')

    row = cfg_box.row(align=True)
    row.operator("mtar.debug_select_nla_by_filter", text="Select", icon='RESTRICT_SELECT_OFF')
    row.operator("mtar.debug_toggle_mute_nla_by_filter", text="Toggle Mute", icon='PAUSE')
    row.operator("mtar.debug_mute_nla_by_filter", text="Mute", icon='HIDE_ON')
    row.operator("mtar.debug_unmute_nla_by_filter", text="Unmute", icon='HIDE_OFF')

    row = cfg_box.row(align=True)
    row.operator("mtar.debug_mute_all_nla", text="Mute All", icon='HIDE_ON')
    row.operator("mtar.debug_unmute_all_nla", text="Unmute All", icon='HIDE_OFF')



