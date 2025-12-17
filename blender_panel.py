"""
Blender N-Panels for MTAR import/export functionality.
"""
from typing import Optional

import bpy
from bpy.types import Panel, PropertyGroup, Context, UILayout, Object
from bpy.props import StringProperty, PointerProperty, IntProperty

from .blender_operators import (
    MTAR_OT_GenerateTrackMappingTemplateFile,
    MTAR_OT_ImportAnimationFromMTAR,
    MTAR_OT_ExportAnimationToMTAR,
    MTAR_OT_SelectImportMtarFile,
    MTAR_OT_SelectFrigFile,
    MTAR_OT_SelectMappingFile,
    MTAR_OT_SelectExportFile,
    MTAR_OT_SelectExportMappingFile,
    MTAR_OT_ValidateHashGeneratorExe
)

# pyright: reportInvalidTypeForm=false

class MTAR_PG_Properties(PropertyGroup):
    """Property group for MTAR import and export settings."""
    
    # Import properties
    import_mtar_filepath: StringProperty(
        name="MTAR File",
        description="Path to the .mtar animation file",
        default="",
        maxlen=1024,
        # subtype='FILE_PATH'
    )
    
    import_frig_filepath: StringProperty(
        name="FRIG File",
        description="Path to the .frig rig file",
        default="",
        maxlen=1024,
        # subtype='FILE_PATH'
    )
    
    import_mapping_filepath: StringProperty(
        name="Track Mapping File",
        description="Path to the .txt file defining track transformations (renaming, rotation offsets, axis mapping, etc.)",
        default="",
        maxlen=1024,
        # subtype='FILE_PATH'
    )
    
    import_gani_index: IntProperty(
        name="GANI Index",
        description="Index of the GANI file to import (-1 = import all)",
        default=-1,
        min=-1,
    )
    
    import_target_rig: PointerProperty(
        name="Target Rig",
        description="Optional Rigify rig to connect to imported animation (constraints will be added based on mapping file)",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE'
    )
    
    import_bake_after_import: bpy.props.BoolProperty(
        name="Bake Target Rig Constraints",
        description="Bake the constraints from the rig into the animation.",
        default=True
    )
    
    # Advanced import option: delete the imported armature after baking
    delete_import_armature: bpy.props.BoolProperty(
        name="Delete Raw Import-Armature",
        description="Remove the temporary imported armature after baking is complete",
        default=True
    )
    
    import_strip_padding: IntProperty(
        name="Strip Padding (Frames)",
        description="Number of frames to insert between animation strips to prevent overlap",
        default=10,
        min=0,
    )
    
    show_advanced_settings: bpy.props.BoolProperty(
        name="Show Advanced Settings",
        description="Display advanced import/export settings",
        default=False
    )
    
    # Export properties
    export_armature: PointerProperty(
        name="Export Armature",
        description="Armature to export animation from",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE'
    )
    
    export_filepath: StringProperty(
        name="Export File",
        description="Path for the exported .mtar animation file",
        default="",
        maxlen=1024,
        # subtype='FILE_PATH'
    )
    
    export_mapping_filepath: StringProperty(
        name="Export Mapping File",
        description="Path to the bone mapping file for export transformations",
        default="",
        maxlen=1024,
        # subtype='FILE_PATH'
    )
    
    export_use_nla: bpy.props.BoolProperty(
        name="Export NLA Strips",
        description="Export all unmuted NLA strips as separate GANI files. If disabled or no NLA tracks exist, exports only the active action",
        default=True
    )

    export_custom_path_hashes: bpy.props.BoolProperty(
        name="Export Custom Path Hashes",
        description="Also export hashes for a custom base path",
        default=False
    )

    export_custom_path_base: StringProperty(
        name="Hash Base Path",
        description="Base path to use for custom path hashes",
        default="/Assets/tpp/",
        maxlen=1024
    )

    export_info_file: bpy.props.BoolProperty(
        name="Export Info File",
        description="Write a '<mtar file name>.mtar.info.txt' file containing exported GANI names or hashes",
        default=True
    )

    export_motion_points_armature: PointerProperty(
        name="Motion Points Armature",
        description="Optional armature that contains motion point bones to export (name should match <base>_MotionPoints if auto-detected).",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE'
    )
    
    # Debug settings
    log_verbosity: bpy.props.EnumProperty(
        name="Log Verbosity",
        description="Minimum log level to display (ERROR and above are always shown, lower levels add more detail)",
        items=[
            ('ERROR', "Errors", "Show only error messages", 0),
            ('WARNING', "Warnings", "Show warnings and errors (default)", 1),
            ('INFO', "Infos", "Show informational messages, warnings, and errors", 2),
            ('DEBUG', "Debug", "Show all messages including debug output", 3),
        ],
        default='WARNING'
    )
    
    enable_timer_logs: bpy.props.BoolProperty(
        name="Log timings",
        description="Log performance timing information for import/export operations",
        default=False
    )

    # External hash generator executable path
    hash_generator_exe_path: StringProperty(
        name="Hash Generator Executable",
        description="Path to the external hash generator executable (GzsTool fork with debug output)",
        default="",
        maxlen=1024,
        subtype='FILE_PATH'
    )


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
        
        box_import = layout.box()

        # MTAR file picker
        mtar_box = box_import
        row = mtar_box.row(align=True)
        row.prop(props, "import_mtar_filepath", text="", icon='ANIM')
        row.operator("mtar.select_import_mtar_file", text="", icon='FILE_FOLDER')

        # FRIG file picker
        mapping_box = box_import.box()
        row = mapping_box.row(align=True)
        row.prop(props, "import_frig_filepath", text="", icon='OUTLINER_OB_ARMATURE')
        row.operator("mtar.select_frig_file", text="", icon='FILE_FOLDER')
        
        # Generate mapping file button
        if props.show_advanced_settings:
            col = mapping_box.column()
            col.enabled = bool(props.import_frig_filepath)
            col.scale_y = 1
            col.operator("mtar.generate_track_mapping_template_file", text="Generate Mapping Template", icon='TEXT')

        # Track mapping file picker
        row = mapping_box.row(align=True)
        row.prop(props, "import_mapping_filepath", text="", icon='TEXT')
        row.operator("mtar.select_mapping_file", text="", icon='FILE_FOLDER')

        # GANI index selector
        box = box_import
        box.prop(props, "import_gani_index", text="Gani File Index", icon='FILTER')
        
        # Strip padding (advanced setting)
        if props.show_advanced_settings:
            box.prop(props, "import_strip_padding", text="Strip Padding", icon='TIME')

        # Target rig selector
        box_target_rig = box_import.box()
        box_target_rig.prop(props, "import_target_rig", text="", icon='ARMATURE_DATA')
        
        # Bake after import checkbox (only shown if advanced settings enabled and target rig is specified)
        if props.show_advanced_settings and props.import_target_rig:
            draw_bool_prop_checkbox_icon(box_target_rig, props, "import_bake_after_import")

            # Delete imported armature option is an advanced, dependent setting
            if props.import_bake_after_import:
                draw_bool_prop_checkbox_icon(box_target_rig, props, "delete_import_armature")

        # Import button
        box_button = layout.box()
        col = box_button.column()
        col.scale_y = 1.5
         # Disable button if required fields are missing
        col.enabled = bool(props.import_mtar_filepath)
        col.operator("mtar.import_animation", text="Import Animation", icon='IMPORT')

        if not props.import_mtar_filepath:
            box_button.label(text="No import path set", icon='ERROR')


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
        
        box_export = layout.box()

        # Armatures selector
        box_rig = box_export.box()
        box_rig.prop(props, "export_armature", text="", icon='ARMATURE_DATA')
        box_rig.prop(props, "export_motion_points_armature", text="", icon='ARMATURE_DATA')

        draw_bool_prop_checkbox_icon(box_rig, props, "export_use_nla")

        # Show info about NLA status
        if props.export_armature and props.export_armature.animation_data:
            anim_data = props.export_armature.animation_data
            if anim_data.nla_tracks and props.export_use_nla:
                unmuted_strips = sum(1 for track in anim_data.nla_tracks 
                                    if not track.mute 
                                    for strip in track.strips 
                                    if not strip.mute and strip.action)
                if unmuted_strips > 0:
                    box_rig.label(text=f"Found {unmuted_strips} NLA strip(s)", icon='CHECKMARK')
                else:
                    box_rig.label(text="No unmuted NLA strips", icon='INFO')
            elif anim_data.action:
                box_rig.label(text="Using active action", icon='ACTION')
            else:
                box_rig.label(text="No animation data", icon='ERROR')

        # Mapping file (optional)
        box = box_export
        row = box.row(align=True)
        row.prop(props, "export_mapping_filepath", text="", icon='TEXT')
        row.operator("mtar.select_export_mapping_file", text="", icon='FILE_FOLDER')

        # Export file picker
        box = box_export
        row = box.row(align=True)
        row.prop(props, "export_filepath", text="", icon='CURRENT_FILE')
        row.operator("mtar.select_export_file", text="", icon='FILE_FOLDER')

        if props.show_advanced_settings:
            # Custom path hash export option
            row_path_hash = box_export.box()
            draw_bool_prop_checkbox_icon(row_path_hash, props, "export_custom_path_hashes")
            if props.export_custom_path_hashes:
                # Show base path text field with required label
                row_path_hash.prop(props, "export_custom_path_base", text="")
                # Warn if Hash Generator executable is not configured in settings
                scene = context.scene
                if not hasattr(scene, 'mtar_properties') or not getattr(scene.mtar_properties, 'hash_generator_exe_path', ''):
                    warn_box = row_path_hash.box()
                    warn_box.label(text="Hash Generator not configured", icon='ERROR')
                    warn_box.label(text="Configure 'Hash Generator Executable' in MTAR Settings → Show Advanced Settings")

            # Export info file option
            row = box.row()
            draw_bool_prop_checkbox_icon(row, props, "export_info_file")
        
        # Export button
        box_button = layout.box()
        col = box_button.column()
        col.scale_y = 1.5
        
        # Disable button if required fields are missing
        can_export = bool(props.export_armature and props.export_filepath)
        col.enabled = can_export
        col.operator("mtar.export_animation", text="Export Animation", icon='EXPORT')

        if not props.export_armature:
            box_button.label(text="No armature selected", icon='ERROR')
        
        if not props.export_filepath:
            box_button.label(text="No export path set", icon='ERROR')


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
        
        # Show advanced settings toggle
        box = layout.box()
        box.label(text="Pro", icon='PREFERENCES')
        col = box.column()
        draw_bool_prop_checkbox_icon(col, props, "show_advanced_settings")

        # Hash Generator executable
        conv_box = layout.box()
        conv_box.label(text="External Hash Generator", icon='FILE_SCRIPT')
        row = conv_box.row(align=True)
        row.prop(props, "hash_generator_exe_path", text="")
        row.operator("mtar.validate_hash_generator_exe", text="", icon='FORCE_HARMONIC')
        conv_box.label(text="https://mgsvmoddingwiki.github.io/GzsTool/")
        conv_box.label(text="Needed for custom hashes.")

        box = layout.box()
        box.label(text="Logging", icon='PREFERENCES')
        box.prop(props, "log_verbosity", text="", icon='INFO')
        draw_bool_prop_checkbox_icon(box, props, "enable_timer_logs", toggle=True)


# Registration
classes = (
    MTAR_PG_Properties,
    MTAR_OT_GenerateTrackMappingTemplateFile,
    MTAR_OT_ImportAnimationFromMTAR,
    MTAR_OT_ExportAnimationToMTAR,
    MTAR_OT_SelectImportMtarFile,
    MTAR_OT_SelectFrigFile,
    MTAR_OT_SelectMappingFile,
    MTAR_OT_SelectExportFile,
    MTAR_OT_SelectExportMappingFile,
    MTAR_OT_ValidateHashGeneratorExe,
    MTAR_PT_SettingsPanel,
    MTAR_PT_ImportPanel,
    MTAR_PT_ExportPanel,
)


def register() -> None:
    """Register all panel classes and properties."""
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.Scene.mtar_properties = PointerProperty(type=MTAR_PG_Properties)


def unregister() -> None:
    """Unregister all panel classes and properties."""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    
    del bpy.types.Scene.mtar_properties
