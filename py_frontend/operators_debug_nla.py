"""NLA tracks related debug operators for MTAR tools."""

import bpy
from bpy.types import Operator, Context

from ..py_tools import tools_nla


class MTAR_PG_DebugNLAProperties(bpy.types.PropertyGroup):
    debug_nla_input_mode: bpy.props.EnumProperty(
        name="Input Source",
        description="NLA debug input source",
        items=[
            ('CLIPBOARD', 'Clipboard', 'Use clipboard input'),
            ('FILTER_FILE', 'Filter File', 'Use GANI filter file'),
            ('CSV', 'CSV', 'Use CSV input'),
        ],
        default='FILTER_FILE'
    )

    debug_nla_csv_input: bpy.props.StringProperty(
        name="CSV Input",
        description="Comma-separated indices for CSV debugging",
        default="",
        maxlen=4096,
    )

    debug_clipboard_index_mode: bpy.props.EnumProperty(
        name="Clipboard Mode",
        description="Interpret index tokens as header/data/auto",
        items=[
            ('HEADER', 'Header (hN)', 'Prefer hN values'),
            ('DATA', 'Data (dN)', 'Prefer dN values'),
            ('AUTO', 'Auto', 'Auto detect indexes'),
        ],
        default='AUTO'
    )



# Copy to clipboard ###########################################################

class MTAR_OT_DebugCopyPathsByFilter(Operator):
    """Collect filtered NLA path entries and copy them to clipboard."""
    bl_idname = "mtar.debug_copy_nla_path_by_filter"
    bl_label = "Paste to clipboard NLA Path by Filter"

    def execute(self, context: Context):
        return tools_nla.collect_nla_by_filter(context, output_type='PATH')


class MTAR_OT_DebugCopyDataIndicesByFilter(Operator):
    """Collect filtered NLA data indices (dN) and copy them to clipboard."""

    bl_idname = "mtar.debug_copy_nla_d_by_filter"
    bl_label = "Paste to clipboard NLA data indices (dN) by Filter"

    def execute(self, context: Context):
        return tools_nla.collect_nla_by_filter(context, output_type='D')


class MTAR_OT_DebugCopyHeaderIndicesByFilter(Operator):
    """Collect filtered NLA header indices (hN) and copy them to clipboard."""
    bl_idname = "mtar.debug_copy_nla_h_by_filter"
    bl_label = "Paste to clipboard NLA header indices (hN) by Filter"

    def execute(self, context: Context):
        return tools_nla.collect_nla_by_filter(context, output_type='H')


# Select ###########################################################

class MTAR_OT_DebugSelectNLAByFilter(Operator):
    """Select all NLA strips that match the current filter settings."""
    bl_idname = "mtar.debug_select_nla_by_filter"
    bl_label = "Select NLA by Filter"

    def execute(self, context: Context):
        return tools_nla.select_nla_by_filter(context)


# Mute ###########################################################

class MTAR_OT_DebugMuteNLAByFilter(Operator):
    """Mute all NLA strips matching the selected filter."""
    bl_idname = "mtar.debug_mute_nla_by_filter"
    bl_label = "Mute NLA by Filter"

    def execute(self, context: Context):
        return tools_nla.set_mute_by_filter(context, mute_value=True)


class MTAR_OT_DebugUnmuteNLAByFilter(Operator):
    """Unmute all NLA strips matching the selected filter."""
    bl_idname = "mtar.debug_unmute_nla_by_filter"
    bl_label = "Unmute NLA by Filter"

    def execute(self, context: Context):
        return tools_nla.set_mute_by_filter(context, mute_value=False)


class MTAR_OT_DebugMuteAllNLA(Operator):
    """Mute all NLA strips."""
    bl_idname = "mtar.debug_mute_all_nla"
    bl_label = "Mute All NLA"

    def execute(self, context: Context):
        return tools_nla.set_all_nla_mute(context, True)


class MTAR_OT_DebugUnmuteAllNLA(Operator):
    """Unmute all NLA strips."""
    bl_idname = "mtar.debug_unmute_all_nla"
    bl_label = "Unmute All NLA"

    def execute(self, context: Context):
        return tools_nla.set_all_nla_mute(context, False)
