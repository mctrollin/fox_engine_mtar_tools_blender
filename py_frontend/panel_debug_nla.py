"""NLA tracks related debug tools."""

import bpy
from bpy.types import UILayout, Context


def draw_nla_page(layout: UILayout, context: Context) -> None:
    """Draw nla tracks related debug tools."""

    cfg_box = layout.box()
    cfg_box.label(text="NLA Tools", icon='NLA')

    box_filter = cfg_box.box()
    row = box_filter.row(align=True)
    row.prop(context.scene.mtar_debug_nla_properties, "debug_nla_input_mode", text="")

    if context.scene.mtar_debug_nla_properties.debug_nla_input_mode == 'FILTER_FILE':
        row = box_filter.row(align=True)
        row.prop(context.scene.mtar_properties, "gani_filter_txt_filepath", text="", icon='FILE_TEXT')
    elif context.scene.mtar_debug_nla_properties.debug_nla_input_mode == 'CSV':
        row = box_filter.row(align=True)
        row.prop(context.scene.mtar_debug_nla_properties, "debug_nla_csv_input", text="")

    col = box_filter.column(align=True)
    col.label(text="Use paths (hash/string), indices (hN/dN)",)
    col.label(text="Use ! prefix to exclude entries",)

    has_armature = bool(context.active_object and context.active_object.type == 'ARMATURE')
    if not has_armature:
        cfg_box.label(text="Requires selected armature!", icon='INFO')

    box_filtered = cfg_box.box()
    box_filtered.enabled = has_armature
    row = box_filtered.row(align=True)
    row.operator("mtar.debug_copy_nla_path_by_filter", text="Paths", icon='COPYDOWN')
    row.operator("mtar.debug_copy_nla_d_by_filter", text="dN", icon='COPYDOWN')
    row.operator("mtar.debug_copy_nla_h_by_filter", text="hN", icon='COPYDOWN')
    row = box_filtered.row(align=True)
    row.operator("mtar.debug_select_nla_by_filter", text="Select", icon='RESTRICT_SELECT_OFF')
    row = box_filtered.row(align=True)
    row.operator("mtar.debug_mute_nla_by_filter", text="Mute", icon='HIDE_ON')
    row.operator("mtar.debug_unmute_nla_by_filter", text="Unmute", icon='HIDE_OFF')

    box_all = cfg_box.box()
    box_all.enabled = has_armature
    row = box_all.row(align=True)
    row.operator("mtar.debug_mute_all_nla", text="Mute All", icon='HIDE_ON')
    row.operator("mtar.debug_unmute_all_nla", text="Unmute All", icon='HIDE_OFF')



