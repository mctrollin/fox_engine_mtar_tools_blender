"""
Blender N-Panels for MTAR import/export functionality.
"""
import os
from typing import Optional

import bpy
from bpy.types import Panel, Context, UILayout

from .py_foxwrap.foxwrap_mtar_reader import MtarReader
from .py_utilities.utilities_parsing import parse_index_selection

from .blender_operators_import import (
    MTAR_OT_GenerateTrackMappingTemplateFile,
    MTAR_OT_ImportAnimationFromMTAR
)
from .blender_properties import MTAR_PG_Properties



def draw_bool_prop_checkbox_icon(layout: UILayout, props, property_name: str, text: Optional[str] = None, **prop_kwargs) -> None:
    """Draw a boolean property with checkbox-highlight icon when True.

    If the caller passes an explicit `icon` via prop_kwargs, that icon will be used.

    Args:
        layout: Blender UILayout (row/column/box)
        props: PropertyGroup instance, typically context.scene.mtar_properties
        property_name: Name of the boolean property on props
        text: Optional label text override (default: uses property name)
        prop_kwargs: Additional keyword args forwarded to layout.prop (e.g., toggle=True)
    """
    # Safely read the property value; default to False when missing
    try:
        value = getattr(props, property_name)
    except AttributeError:
        value = False

    # Pick the icon to display: when the caller passes explicit 'icon', use it; otherwise use checkbox highlight
    icon_prop = prop_kwargs.pop('icon', None)
    if icon_prop:
        icon = icon_prop
    else:
        icon = 'CHECKBOX_HLT' if value else 'CHECKBOX_DEHLT'
    label_text = text if text is not None else None
    layout.prop(props, property_name, icon=icon, text=label_text, **prop_kwargs)


def draw_progress_bar(layout: UILayout, props: 'MTAR_PG_Properties', operation_type: str) -> None:
    """Draw a progress bar if supported by the Blender version (4.0+)."""
    # UILayout.progress was introduced in Blender 4.0
    exec_props = props.execution_props
    is_active = operation_type == exec_props.operation_type
    progress: float = exec_props.progress if is_active else 0
    status_text: str = exec_props.status if is_active else ""
    
    if hasattr(layout, "progress") :
        col = layout.column()
        col.scale_y = 0.6
        col.progress(factor=progress, text=status_text)


def check_bake_during_import(import_props) -> bool:
    return import_props.custom_rig and import_props.import_bake_constraints

class MTAR_PT_ImportPanel(Panel):
    """N-Panel for MTAR animation import."""
    bl_label = "MTAR Animation Import"
    bl_idname = "MTAR_PT_import_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MTAR'
    
    def draw(self, context: Context) -> None:
        layout = self.layout
        props = context.scene.mtar_properties
        import_props = props.import_props
        settings_props = props.settings_props
        
        box_import = layout.box()

        # MTAR file picker
        mtar_box = box_import
        mtar_box.prop(import_props, "mtar_filepath", text="", icon='ANIM')

        # MTAR header preview (read-only display)
        info_box = mtar_box.box()
        header_info = None
        if import_props.mtar_filepath:
            mtar_filepath_abs = bpy.path.abspath(import_props.mtar_filepath)
            if os.path.exists(mtar_filepath_abs):
                try:
                    reader = MtarReader(mtar_filepath_abs)
                    header_info = reader.get_header_info()
                    
                    row = info_box.row()
                    gani_verstion_str = header_info.gani_version if hasattr(header_info, 'gani_version') and header_info.gani_version is not None else ""
                    row.label(text=f"v{header_info.version} [gani{('2' if header_info.is_new_format else '1 v')}{gani_verstion_str}]")
                    
                    # Validate MTAR header and show warning if invalid
                    is_valid, error_msg = reader.validate_header()
                    if not is_valid:
                        warn_box = info_box.box()
                        warn_box.alert = True
                        warn_box.label(text="File validation", icon='ERROR')
                        warn_box.label(text=error_msg)
                except Exception as e:
                    info_box.label(text=f"Error reading MTAR: {e}", icon='ERROR')
            else:
                info_box.label(text="MTAR path not found", icon='ERROR')

        # FRIG file picker
        mapping_box = box_import.box()
        row = mapping_box.row(align=True)
        row.prop(import_props, "frig_filepath", text="", icon='OUTLINER_OB_ARMATURE')
        if settings_props.show_advanced_settings:
            adv_box = mapping_box.box()
            adv_box.alert = True
            col = adv_box.column()
            col.enabled = bool(import_props.frig_filepath)
            col.scale_y = 1
            col.operator("mtar.generate_track_mapping_template_file", text="Generate Mapping Template", icon='TEXT')

        # Track mapping file picker
        row = mapping_box.row(align=True)
        row.prop(import_props, "mapping_filepath", text="", icon='TEXT')
        box = box_import
        box.prop(import_props, "gani_indices_str", text="", icon='FILTER')

        # Compute filtered selection once and show the selection count below the GANI filter (if header info is available)
        selected_count = None
        parse_error_msg = None
        selected_indices = None
        if header_info:
            if import_props.gani_indices_str.strip():
                try:
                    selected_indices = parse_index_selection(import_props.gani_indices_str, header_info.file_count)
                    selected_count = len(selected_indices)
                except ValueError as e:
                    parse_error_msg = str(e)
                    selected_count = 0
            else:
                selected_count = header_info.file_count

            if parse_error_msg:
                err_row = box.row()
                err_row.alert = True
                err_row.label(text=f"Invalid GANI selection: {parse_error_msg}", icon='ERROR')
            else:
                label_text = f"{selected_count} of {header_info.file_count} animation{'s' if selected_count != 1 else ''} selected"
                row = box.row()
                row.label(text=label_text, icon='ANIM')
        
        if settings_props.show_advanced_settings:
            adv_box = box_import.box()
            adv_box.alert = True

            # Verbose naming (advanced setting)
            draw_bool_prop_checkbox_icon(adv_box, import_props, "use_verbose_naming")

            # Hash dictionary for GANI name unhashing (advanced setting)
            draw_bool_prop_checkbox_icon(adv_box, import_props, "import_use_hash_dictionary")
            if import_props.import_use_hash_dictionary:
                dict_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dic', 'path64', 'mtar_dictionary.txt')
                if not os.path.exists(dict_path):
                    row = adv_box.row()
                    row.alert = True
                    row.label(text="dic/path64/mtar_dictionary.txt not found", icon='ERROR')

        # Strip padding (advanced setting)
        if settings_props.show_advanced_settings:
            adv_box = box_import.box()
            adv_box.alert = True
            adv_box.prop(import_props, "strip_padding", text="Strip Padding", icon='TIME')

        # custom rig selector
        box_custom_rig = box_import.box()
        box_custom_rig.prop(import_props, "custom_rig", text="", icon='ARMATURE_DATA')
        
        if settings_props.show_advanced_settings and import_props.custom_rig:
            # IK Up Distance (advanced setting, shown when advanced settings are enabled)
            adv_box = box_custom_rig.box()
            adv_box.alert = True
            adv_box.prop(import_props, "ik_up_distance", text="IK Up Distance", icon='DRIVER_DISTANCE')

            # Bake after import checkbox (only shown if advanced settings enabled and custom rig is specified)
            draw_bool_prop_checkbox_icon(adv_box, import_props, "import_bake_constraints")

            # Delete imported armature option is an advanced, dependent setting
            if import_props.import_bake_constraints:
                 # FCurve decimation settings (advanced setting) — per-import property
                row = adv_box.row()
                row.prop(import_props, 'import_bake_decimate_fcurve_error', text='Decimate Error', icon='IPO_BEZIER')
                
                # Decimation track type filter (only shown if decimation error > 0)
                if import_props.import_bake_decimate_fcurve_error > 0:
                    row.prop(import_props, 'import_bake_decimate_skip_types', text='', icon='FILTER')

                draw_bool_prop_checkbox_icon(adv_box, import_props, "delete_import_armature")


        # Import button
        box_button = layout.box()
        col = box_button.column()
        col.scale_y = 1.5
         # Disable button if required fields are missing
        col.enabled = bool(import_props.mtar_filepath)
        col.operator("mtar.import_animation", text="Import Animation", icon='IMPORT')

        draw_progress_bar(box_button, props, 'IMPORT')

        # Show a slim warning if the number of animations that will be processed (after applying the GANI filter)
        # exceeds the threshold; keep parse error shown only below the filter (do not duplicate it here).
        if header_info:
            if selected_count is not None and selected_count > 100 and check_bake_during_import(import_props) and not parse_error_msg:
                warn_box = box_button.box()
                warn_box.alert = True
                warn_box.label(text=f"Importing + baking {selected_count} animations.")
                warn_box.label(text="This may take several minutes.")
                warn_box.label(text="View console to track progress.")

        if not import_props.mtar_filepath:
            box_button.label(text="No import path set", icon='ERROR')


# Registration
classes = (
    MTAR_OT_GenerateTrackMappingTemplateFile,
    MTAR_OT_ImportAnimationFromMTAR,
    MTAR_PT_ImportPanel,
)


def register() -> None:
    """Register all panel classes and properties."""
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    """Unregister all panel classes and properties."""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
