"""
Blender N-Panel for MTAR export functionality.
"""
import bpy
from bpy.types import Context, UILayout

from .blender_operators_export import MTAR_OT_ExportAnimationToMTAR
from .py_utilities.utilities_blender_animation import is_relevant_strip, try_find_layout_track_action
from .py_foxwrap.foxwrap_metadata import read_mtar_properties_from_action, read_mtar_properties_from_any_action
from .py_fox import fox_mtar_constants as mtar_const

from .blender_panel_shared import draw_bool_prop_checkbox_icon, draw_estimated_operation_time, draw_progress_bar


def draw_export_page(layout: UILayout, context: Context) -> None:
    """Draw the export page inside the unified MTAR panel."""
    props = context.scene.mtar_properties
    export_props = props.export_props
    settings_props = props.settings_props

    box_export = layout.box()
    # Compute count of animations/strips that will be exported (None = unknown/no armature)
    export_count = None

    # Armatures selector
    box_rig = box_export.box()
    box_rig.prop(export_props, "armature", text="", icon='OUTLINER_OB_ARMATURE')
    if export_props.armature:
        # Resolve the layout action once; reused for format/info logic below.
        _layout_action = try_find_layout_track_action()
        _fmt_flags = 0x1000  # default: new format
        # For old-format MTARs (no layout action), collect fallback NLA actions
        _fallback_nla_actions = []
        if _layout_action:
            _fmt_mtar_props = read_mtar_properties_from_action(_layout_action)
            _fmt_flags = _fmt_mtar_props.get(mtar_const.MTAR_FLAGS, 0x1000)
        elif export_props.armature and export_props.armature.animation_data:
            # Old-format: collect NLA actions for fallback MTAR property reading
            anim_data = export_props.armature.animation_data
            for track in anim_data.nla_tracks:
                if not track.mute:
                    for strip in track.strips:
                        if is_relevant_strip(strip) and strip.action:
                            _fallback_nla_actions.append(strip.action)
            if _fallback_nla_actions:
                _fmt_mtar_props = read_mtar_properties_from_any_action(_layout_action, _fallback_nla_actions)
                _fmt_flags = _fmt_mtar_props.get(mtar_const.MTAR_FLAGS, 0x1000)
        # old-format info still needs to know _fmt_flags

    if settings_props.show_advanced_settings:
        adv_box = box_rig.box()
        adv_box.alert = True
        draw_bool_prop_checkbox_icon(adv_box, export_props, "use_nla")

    # Show info about NLA status and compute export_count
    animinfo_box = box_rig.box()
    info_icon = 'CHECKMARK'
    info_nla = ''
    # gani_text will be set later when format is determined
    gani_text = ''
    if export_props.armature and export_props.armature.animation_data:
        anim_data = export_props.armature.animation_data
        if anim_data.nla_tracks and export_props.use_nla:
            unmuted_strips = sum(1 for track in anim_data.nla_tracks
                                if not track.mute
                                for strip in track.strips
                                if is_relevant_strip(strip))
            export_count = unmuted_strips
            if unmuted_strips > 0:
                info_nla = f"{unmuted_strips} NLA strip(s)"
                info_icon = 'CHECKMARK'
            else:
                info_nla = "No unmuted NLA strips"
                info_icon = 'INFO'
        elif anim_data.action:
            export_count = 1
            info_nla = "Using active action"
            info_icon = 'ACTION'
        else:
            export_count = 0
            info_nla = "No animation data"
            info_icon = 'ERROR'

    # Show format info (detected from layout action or per-GANI fallback)
    # Only show if armature is selected (user is actively configuring an export)
    if export_props.armature:
        is_new_format = bool(_fmt_flags & 0x1000)  # UseMini flag
        
        if _layout_action:
            # Layout action exists (GANI2 / new-format)
            has_stored_props = (mtar_const.MTAR_VERSION in _layout_action.keys() and 
                               mtar_const.MTAR_FLAGS in _layout_action.keys())
            gani_text = "GANI2" if is_new_format else "GANI1"
            
            if not has_stored_props:
                warn_box = animinfo_box.box()
                warn_box.alert = True
                warn_box.label(text="No MTAR version on layout action", icon='ERROR')
                warn_box.label(text="Defaulting to GANI2 (new format)")
                warn_box.label(text="Re-import an MTAR to preserve format")
        else:
            # No layout action found — old-format or no NLA data
            if _fallback_nla_actions or (export_props.armature and export_props.armature.animation_data):
                # Old-format with NLA data: decide text
                gani_text = "GANI2" if is_new_format else "GANI1"
            else:
                gani_text = "(no format data)"
        # assign gani_text into overall info
        # it was initialized earlier


        # after evaluating NLA and format, display combined info
        combined = info_nla
        if gani_text:
            combined = f"{combined} | {gani_text}" if combined else gani_text
        animinfo_box.label(text=combined, icon=info_icon)

        # Mapping file (optional - shared with import)
        box = box_export.box()
        box.prop(props, "mapping_filepath", text="", icon='TEXT')

        if settings_props.show_advanced_settings:
            adv_box = box_export.box()
            adv_box.alert = True

            # FCurve cleaning controls (advanced setting)
            row = adv_box.row(align=True)
            draw_bool_prop_checkbox_icon(row, export_props, 'export_clean_fcurves')

            sub = row.row(align=True)
            sub.enabled = export_props.export_clean_fcurves and export_props.export_decimate_fcurves
            sub.prop(export_props, 'export_fcurve_clean_threshold', text='Clean Threshold', icon='IPO_LINEAR')

            # Force highest bit encoding option
            row2 = adv_box.row()
            draw_bool_prop_checkbox_icon(row2, export_props, "force_highest_bit_encoding")

        if settings_props.show_advanced_settings:
            adv_box = box_export.box()
            adv_box.alert = True

            # Custom path hash export option
            draw_bool_prop_checkbox_icon(adv_box, export_props, "treat_hashes_as_names")
            # Always show base path — it applies to invalid paths and NLA fallbacks regardless of the flag above
            adv_box.prop(export_props, "custom_path_base", text="")

            # Export info file option
            draw_bool_prop_checkbox_icon(adv_box, export_props, "info_file")

        # Export file picker
        box = layout.box()
        box.prop(export_props, "filepath", text="", icon='CURRENT_FILE')

        # Export button
        box_button = layout.box()
        col = box_button.column()
        col.scale_y = 1.5
        
        # Disable button if required fields are missing
        can_export = bool(export_props.armature and export_props.filepath)
        col.enabled = can_export
        col.operator("mtar.export_animation", text="Export Animation", icon='EXPORT')

        draw_progress_bar(box_button, props)

        # Show an estimated export duration based on GANI count
        if export_count is not None and export_count > 0:
            warn_box = box_button.box()
            warn_box.alert = True
            warn_box.label(text=f"Exporting {export_count} animations.")
            draw_estimated_operation_time(warn_box, export_count, 1.5)

        if not export_props.armature:
            box_button.label(text="No armature selected", icon='ERROR')
        
        if not export_props.filepath:
            box_button.label(text="No export path set", icon='ERROR')


classes = (
    MTAR_OT_ExportAnimationToMTAR,
)

def register() -> None:
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            # Already registered (e.g. on reload)
            pass

def unregister() -> None:
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except ValueError:
            # Not registered or already unregistered
            pass
