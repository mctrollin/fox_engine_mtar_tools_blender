"""
Combined main UI panel for MTAR add-on.

This module holds the single `MTAR_PT_MainPanel` class which hosts the
Import/Export/Settings pages as a tabbed interface.  Pulling it out of the
other panel modules breaks the circular import that previously existed when
`blender_panel_import` tried to import `draw_export_page` from
`blender_panel_export` at module load.

The helper drawing functions live in their respective modules:
* :func:`blender_panel_import.draw_import_page`
* :func:`blender_panel_export.draw_export_page`
* :func:`blender_panel_import.draw_settings_page`

This module only defines the panel and its registration boilerplate.
"""

import bpy
from bpy.types import Panel, Context

from .blender_panel_settings import draw_settings_page
from .blender_panel_import import draw_import_page
from .blender_panel_export import draw_export_page


class MTAR_PT_MainPanel(Panel):
    """Unified N-Panel for MTAR import/export/settings."""
    bl_label = "MTAR Animation"
    bl_idname = "MTAR_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MTAR'

    def draw(self, context: Context) -> None:
        layout = self.layout
        props = context.scene.mtar_properties
        settings_props = props.settings_props

        # tab row
        row = layout.row(align=True)
        row.prop(settings_props, "active_tab", expand=True)

        layout.separator()

        # dispatch to page
        tab = settings_props.active_tab
        if tab == 'IMPORT':
            draw_import_page(layout, context)
        elif tab == 'EXPORT':
            draw_export_page(layout, context)
        else:
            draw_settings_page(layout, context)


# panel installer -------------------------------------------------------------

classes = (
    MTAR_PT_MainPanel,
)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
