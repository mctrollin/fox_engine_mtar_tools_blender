"""
Blender N-Panels for MTAR import/export functionality.
"""
import os
from typing import Optional

import bpy
from bpy.types import Panel, PropertyGroup, Context, UILayout, Object
from bpy.props import StringProperty, PointerProperty, IntProperty

from .py_foxwrap.foxwrap_mtar_reader import MtarReader

from .blender_operators_import import (
    MTAR_OT_GenerateTrackMappingTemplateFile,
    MTAR_OT_ImportAnimationFromMTAR,
    MTAR_OT_ValidateHashGeneratorExe
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
        if import_props.mtar_filepath:
            mtar_filepath_abs = bpy.path.abspath(import_props.mtar_filepath)
            if os.path.exists(mtar_filepath_abs):
                try:
                    reader = MtarReader(mtar_filepath_abs)
                    header_info = reader.get_header_info()
                    
                    row = info_box.row()
                    row.label(text=f"v: {header_info.version}")
                    row.label(text=f"Files: {header_info.file_count}")
                except Exception as e:
                    info_box.label(text=f"Error reading MTAR: {e}", icon='ERROR')

        # FRIG file picker
        mapping_box = box_import.box()
        row = mapping_box.row(align=True)
        row.prop(import_props, "frig_filepath", text="", icon='OUTLINER_OB_ARMATURE')
        if settings_props.show_advanced_settings:
            col = mapping_box.column()
            col.enabled = bool(import_props.frig_filepath)
            col.scale_y = 1
            col.operator("mtar.generate_track_mapping_template_file", text="Generate Mapping Template", icon='TEXT')

        # Track mapping file picker
        row = mapping_box.row(align=True)
        row.prop(import_props, "mapping_filepath", text="", icon='TEXT')
        box = box_import
        box.prop(import_props, "gani_indices_str", text="", icon='FILTER')
        
        # Strip padding (advanced setting)
        if settings_props.show_advanced_settings:
            box.prop(import_props, "strip_padding", text="Strip Padding", icon='TIME')

        # custom rig selector
        box_custom_rig = box_import.box()
        box_custom_rig.prop(import_props, "custom_rig", text="", icon='ARMATURE_DATA')
        
        # IK Up Distance (advanced setting, shown when advanced settings are enabled)
        if settings_props.show_advanced_settings:
            box_custom_rig.prop(import_props, "ik_up_distance", text="IK Up Distance", icon='DRIVER_DISTANCE')
        
            # Interpolation mode (advanced setting) — per-import property
            box_custom_rig.prop(import_props, 'interpolation_mode', text='', icon="IPO_BEZIER")

        # Bake after import checkbox (only shown if advanced settings enabled and custom rig is specified)
        if settings_props.show_advanced_settings and import_props.custom_rig:

            draw_bool_prop_checkbox_icon(box_custom_rig, import_props, "bake_after_import")

            # Delete imported armature option is an advanced, dependent setting
            if import_props.bake_after_import:
                draw_bool_prop_checkbox_icon(box_custom_rig, import_props, "delete_import_armature")

        # Import button
        box_button = layout.box()
        col = box_button.column()
        col.scale_y = 1.5
         # Disable button if required fields are missing
        col.enabled = bool(import_props.mtar_filepath)
        col.operator("mtar.import_animation", text="Import Animation", icon='IMPORT')

        draw_progress_bar(box_button, props, 'IMPORT')

        if not import_props.mtar_filepath:
            box_button.label(text="No import path set", icon='ERROR')


# Registration
classes = (
    MTAR_OT_GenerateTrackMappingTemplateFile,
    MTAR_OT_ImportAnimationFromMTAR,
    MTAR_OT_ValidateHashGeneratorExe,
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
