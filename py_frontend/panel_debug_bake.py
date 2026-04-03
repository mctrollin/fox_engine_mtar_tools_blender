"""Debug Bake page extracted from blender_panel_debug."""

import bpy
from bpy.types import UILayout, Context



def draw_bake_page(layout: UILayout, context: Context) -> None:
    """Draw the contents originally provided by the old Bake panel."""
    props = context.scene.mtar_debug_transform_properties

    box = layout.box()
    box.label(text="Animation Bake", icon='RENDER_ANIMATION')

    config_box = layout.box()
    config_box.label(text="Configuration", icon='SETTINGS')
    col = config_box.column(align=True)

    col.prop(props, "debug_armature", text="Target Armature")
    col.prop(props, "debug_source_armature", text="Source Armature")

    row = col.row(align=True)
    row.prop(props, "debug_bake_gani_index")
    row.prop(props, "debug_prepare_only")

    button_box = layout.box()
    button_box.label(text="Actions", icon='PLAY')
    row = button_box.row(align=True)
    row.scale_y = 1.3
    row.enabled = bool(props.debug_armature)
    row.operator("mtar.debug_run_bake", text="Run Bake", icon='FILE_REFRESH')

    debug_box = layout.box()
    debug_box.label(text="Debug Tools", icon='CONSOLE')
    row = debug_box.row(align=True)
    row.enabled = bool(props.debug_armature)
    row.operator("mtar.debug_setup_graph_context", text="Setup Graph Context", icon='GRAPH')
    debug_box.label(text="Sets up graph editor for manual testing", icon='INFO')

    clean_box = layout.box()
    clean_box.label(text="Bake + Clean FCurves", icon='FCURVE')
    export_props = context.scene.mtar_properties.export_props
    col = clean_box.column(align=True)
    col.prop(export_props, "export_clean_fcurves")
    col.prop(export_props, "export_fcurve_clean_threshold")
    clean_box.label(text="Destructive — modifies active action in-place", icon='ERROR')
    row = clean_box.row(align=True)
    row.scale_y = 1.3
    row.enabled = bool(props.debug_armature)
    row.operator("mtar.debug_bake_clean_fcurves", text="Bake + Clean FCurves", icon='FCURVE')
