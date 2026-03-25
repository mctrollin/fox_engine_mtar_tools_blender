"""NLA tracks related debug tools."""

import bpy
from bpy.types import UILayout, Context


def draw_nla_page(layout: UILayout, context: Context) -> None:
    """Draw nla tracks related debug tools."""

    cfg_box = layout.box()
    cfg_box.label(text="NLA Tools", icon='FILE_TICK')

    row = cfg_box.row(align=True)
    row.prop(context.scene.mtar_debug_nla_properties, "debug_nla_input_mode", text="")

    if context.scene.mtar_debug_nla_properties.debug_nla_input_mode == 'FILTER_FILE':
        row = cfg_box.row(align=True)
        row.prop(context.scene.mtar_properties, "gani_filter_txt_filepath", text="", icon='FILE_TEXT')
    elif context.scene.mtar_debug_nla_properties.debug_nla_input_mode == 'CSV':
        row = cfg_box.row(align=True)
        row.prop(context.scene.mtar_debug_nla_properties, "debug_nla_csv_input", text="")

    col = cfg_box.column(align=True)
    col.label(text="Supports hashes (hex/dec), paths, hN/dN", icon='INFO')
    col.label(text="Use ! prefix to exclude entries", icon='INFO')

    row = cfg_box.row(align=True)
    row.label(text="Filtered")
    row = cfg_box.row(align=True)
    row.operator("mtar.debug_copy_nla_path_by_filter", text="Paths", icon='COPYDOWN')
    row.operator("mtar.debug_copy_nla_d_by_filter", text="dN", icon='COPYDOWN')
    row.operator("mtar.debug_copy_nla_h_by_filter", text="hN", icon='COPYDOWN')

    row = cfg_box.row(align=True)

    row = cfg_box.row(align=True)
    row.operator("mtar.debug_select_nla_by_filter", text="Select All", icon='RESTRICT_SELECT_OFF')
    row = cfg_box.row(align=True)
    row.operator("mtar.debug_mute_nla_by_filter", text="Mute", icon='HIDE_ON')
    row.operator("mtar.debug_unmute_nla_by_filter", text="Unmute", icon='HIDE_OFF')

    row = cfg_box.row(align=True)
    row.label(text="All")
    row = cfg_box.row(align=True)
    row.operator("mtar.debug_mute_all_nla", text="Mute", icon='HIDE_ON')
    row.operator("mtar.debug_unmute_all_nla", text="Unmute", icon='HIDE_OFF')



