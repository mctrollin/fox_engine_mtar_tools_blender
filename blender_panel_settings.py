"""
Blender N-Panel for MTAR plugin settings.
"""
import bpy
from bpy.types import Panel, Context, UILayout

from .blender_panel_import import draw_bool_prop_checkbox_icon

class MTAR_PT_SettingsPanel(Panel):
    """N-Panel for MTAR plugin settings."""
    bl_label = "Settings"
    bl_idname = "MTAR_PT_settings_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MTAR'
    bl_options = {'DEFAULT_CLOSED'}
    
    def draw(self, context: Context) -> None:
        layout: UILayout = self.layout
        props = context.scene.mtar_properties
        settings_props = props.settings_props
        
        # Show advanced settings toggle
        box = layout.box()
        box.label(text="Pro", icon='PREFERENCES')
        col = box.column()
        draw_bool_prop_checkbox_icon(col, settings_props, "show_advanced_settings")

        # Hash Generator executable
        conv_box = layout.box()
        conv_box.label(text="External Hash Generator", icon='FILE_SCRIPT')
        row = conv_box.row(align=True)
        row.prop(settings_props, "hash_generator_exe_path", text="")
        row.operator("mtar.validate_hash_generator_exe", text="", icon='FORCE_HARMONIC')
        conv_box.label(text="https://mgsvmoddingwiki.github.io/GzsTool/")
        conv_box.label(text="Needed for custom hashes.")

        box = layout.box()
        box.label(text="Logging", icon='PREFERENCES')
        box.prop(settings_props, "log_verbosity", text="", icon='INFO')
        draw_bool_prop_checkbox_icon(box, settings_props, "enable_timer_logs", toggle=True)
        
        # Rest Pose Correction toggle
        rest_box = layout.box()
        rest_box.label(text="Rest Pose Correction", icon='ARMATURE_DATA')
        draw_bool_prop_checkbox_icon(rest_box, settings_props, "enable_rest_pose_correction", toggle=True)
        if not settings_props.enable_rest_pose_correction:
            rest_box.label(text="Only mapping file transforms", icon='INFO')


classes = (
    MTAR_PT_SettingsPanel,
)

def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
