"""
Blender N-Panel for MTAR plugin settings.
"""
import bpy

from . import blender_panel_shared


def draw_settings_page(layout, context) -> None:
    """Draw the settings page of the unified MTAR panel."""
    props = context.scene.mtar_properties
    settings_props = props.settings_props

    # Show advanced settings toggle
    box_advanced = layout.box()
    box_advanced.alert = True
    box_advanced.label(text="Pro", icon='PREFERENCES')
    col = box_advanced.column()
    blender_panel_shared.draw_bool_prop_checkbox_icon(col, settings_props, "show_advanced_settings")

    if settings_props.show_advanced_settings:
        adv_box = box_advanced.box()
        adv_box.alert = True

        # Motion Event (Pose markers) visibility
        blender_panel_shared.draw_bool_prop_checkbox_icon(adv_box, settings_props, "show_pose_markers", text="Show Event Markers", toggle=True)

        # Rest Pose Correction toggle
        blender_panel_shared.draw_bool_prop_checkbox_icon(adv_box, settings_props, "enable_rest_pose_correction", toggle=True)
        if not settings_props.enable_rest_pose_correction:
            adv_box.label(text="Only mapping file transforms", icon='INFO')

        # Sorting option for GANI (advanced)
        blender_panel_shared.draw_bool_prop_checkbox_icon(adv_box, settings_props, "sort_gani", text="Sort GANI", toggle=True)

    box = layout.box()
    box.label(text="Logging", icon='PREFERENCES')
    box.prop(settings_props, "log_verbosity", text="", icon='INFO')



# no classes to register
classes = ()

def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
