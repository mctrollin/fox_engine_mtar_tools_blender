"""
Blender N-Panel for MTAR export functionality.
"""
import bpy
from bpy.types import Panel, Context

from .blender_operators_export import (
    MTAR_OT_ExportAnimationToMTAR,
)
from .py_utilities.utilities_blender_animation import is_relevant_strip, try_find_layout_track_action
from .py_foxwrap.foxwrap_metadata import read_mtar_properties_from_action
from .py_fox import fox_mtar_constants as mtar_const

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
        if export_props.armature:
            box_rig.prop(export_props, "motion_points_armature", text="", icon='ARMATURE_DATA')
            # Resolve the layout action once; reused for both the shader picker
            # visibility check and the format info box below.
            _layout_action = try_find_layout_track_action()
            _fmt_flags = 0x1000  # default: new format
            if _layout_action:
                _fmt_mtar_props = read_mtar_properties_from_action(_layout_action)
                _fmt_flags = _fmt_mtar_props.get(mtar_const.MTAR_FLAGS, 0x1000)
            # Show shader nodes armature picker only for old-format (FoxData / GZ) MTARs
            if _layout_action and not bool(_fmt_flags & 0x1000):  # old format (no UseMini flag)
                box_rig.prop(export_props, "shader_nodes_armature", text="", icon='SHADING_RENDERED')

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

        # Show format info (detected from layout action properties)
        # Only show if armature is selected (user is actively configuring an export)
        if export_props.armature:
            format_info_box = box_rig.box()
            layout_action = _layout_action  # already resolved above
            if layout_action:
                # Read MTAR properties from the layout action
                flags = _fmt_flags
                is_new_format = bool(flags & 0x1000)  # UseMini flag
                
                # Check if properties are explicitly set (not defaults)
                has_stored_props = (mtar_const.MTAR_VERSION in layout_action.keys() and 
                                   mtar_const.MTAR_FLAGS in layout_action.keys())
                
                if is_new_format:
                    format_info_box.label(text="Format: GANI2 (TPP)", icon='CHECKMARK')
                else:
                    format_info_box.label(text="Format: GANI (GZ/old)", icon='INFO')
                
                if not has_stored_props:
                    # Warn if using defaults (layout action found but no stored props)
                    warn_box = format_info_box.box()
                    warn_box.alert = True
                    warn_box.label(text="No MTAR version on layout action", icon='ERROR')
                    warn_box.label(text="Defaulting to GANI2 (new format)")
                    warn_box.label(text="Re-import an MTAR to preserve format")
            else:
                # No layout action found
                format_info_box.label(text="No layout track action found", icon='INFO')


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
            adv_box.prop(export_props, 'export_fcurve_clean_threshold', text='Clean Threshold', icon='IPO_LINEAR')

            # Force highest bit encoding option
            row2 = adv_box.row()
            draw_bool_prop_checkbox_icon(row2, export_props, "force_highest_bit_encoding")

            # Custom path hash export option
            row_path_hash = adv_box.box()
            draw_bool_prop_checkbox_icon(row_path_hash, export_props, "treat_hashes_as_names")
            # Always show base path — it applies to invalid paths and NLA fallbacks regardless of the flag above
            row_path_hash.prop(export_props, "custom_path_base", text="")

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
