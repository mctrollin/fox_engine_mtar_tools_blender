"""
Blender N-Panel for MTAR export functionality.
"""
import bpy
from bpy.types import Panel, Context

from .blender_operators_export import (
    MTAR_OT_ExportAnimationToMTAR,
)
from .py_utilities.utilities_blender_animation import is_relevant_strip

# Import shared utilities and properties from the import panel module
# (This avoids duplication of the PropertyGroup and helper functions)
from .blender_panel_import import draw_bool_prop_checkbox_icon, draw_progress_bar

class MTAR_PT_ExportPanel(Panel):
    """N-Panel for MTAR animation export."""
    bl_label = "MTAR Animation Export"
    bl_idname = "MTAR_PT_export_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MTAR'
    
    def draw(self, context: Context) -> None:
        layout = self.layout
        props = context.scene.mtar_properties
        export_props = props.export_props
        settings_props = props.settings_props
        
        box_export = layout.box()
        # Compute count of animations/strips that will be exported (None = unknown/no armature)
        export_count = None

        # Armatures selector
        box_rig = box_export.box()
        box_rig.prop(export_props, "armature", text="", icon='ARMATURE_DATA')
        box_rig.prop(export_props, "motion_points_armature", text="", icon='ARMATURE_DATA')

        if settings_props.show_advanced_settings:
            adv_box = box_rig.box()
            adv_box.alert = True
            draw_bool_prop_checkbox_icon(adv_box, export_props, "use_nla")

        # Show info about NLA status and compute export_count
        animinfo_box = box_rig.box()
        if export_props.armature and export_props.armature.animation_data:
            anim_data = export_props.armature.animation_data
            if anim_data.nla_tracks and export_props.use_nla:
                unmuted_strips = sum(1 for track in anim_data.nla_tracks
                                    if not track.mute
                                    for strip in track.strips
                                    if is_relevant_strip(strip))
                export_count = unmuted_strips
                if unmuted_strips > 0:
                    animinfo_box.label(text=f"Found {unmuted_strips} NLA strip(s)", icon='CHECKMARK')
                else:
                    animinfo_box.label(text="No unmuted NLA strips", icon='INFO')
            elif anim_data.action:
                export_count = 1
                animinfo_box.label(text="Using active action", icon='ACTION')
            else:
                export_count = 0
                animinfo_box.label(text="No animation data", icon='ERROR')

        # Mapping file (optional)
        box = box_export
        box.prop(export_props, "mapping_filepath", text="", icon='TEXT')

        # Export file picker
        box = box_export
        box.prop(export_props, "filepath", text="", icon='CURRENT_FILE')

        if settings_props.show_advanced_settings:
            adv_box = box_export.box()
            adv_box.alert = True

            # FCurve cleaning threshold (advanced setting)
            adv_box.prop(export_props, 'export_clean_threshold', text='Clean Threshold', icon='IPO_LINEAR')

            # Force highest bit encoding option
            row2 = adv_box.row()
            draw_bool_prop_checkbox_icon(row2, export_props, "force_highest_bit_encoding")

            # Custom path hash export option
            row_path_hash = adv_box.box()
            draw_bool_prop_checkbox_icon(row_path_hash, export_props, "custom_path_hashes")
            if export_props.custom_path_hashes:
                # Show base path text field with required label
                row_path_hash.prop(export_props, "custom_path_base", text="")
                # Warn if Hash Generator executable is not configured in settings
                if not settings_props.hash_generator_exe_path:
                    warn_box = row_path_hash.box()
                    warn_box.label(text="Hash Generator not configured", icon='ERROR')
                    warn_box.label(text="Configure 'Hash Generator Executable' in MTAR Settings → Show Advanced Settings")

            # Export info file option
            draw_bool_prop_checkbox_icon(row_path_hash, export_props, "info_file")

        # Export button
        box_button = layout.box()
        col = box_button.column()
        col.scale_y = 1.5
        
        # Disable button if required fields are missing
        can_export = bool(export_props.armature and export_props.filepath)
        col.enabled = can_export
        col.operator("mtar.export_animation", text="Export Animation", icon='EXPORT')

        draw_progress_bar(box_button, props, 'EXPORT')

        # Slim warning if exporting many animations/strips (no filtering available for export)
        if export_count is not None and export_count > 100:
            warn_box = box_button.box()
            warn_box.alert = True
            warn_box.label(text=f"Exporting {export_count} animations.")
            warn_box.label(text="This may take several minutes.")
            warn_box.label(text="View console to track progress.")

        if not export_props.armature:
            box_button.label(text="No armature selected", icon='ERROR')
        
        if not export_props.filepath:
            box_button.label(text="No export path set", icon='ERROR')


classes = (
    MTAR_OT_ExportAnimationToMTAR,
    MTAR_PT_ExportPanel,
)

def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
