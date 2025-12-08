"""
Blender N-Panels for MTAR import/export functionality.
"""
from typing import TYPE_CHECKING

import bpy
from bpy.types import Panel, PropertyGroup, Context, UILayout
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
)

if TYPE_CHECKING:
    from bpy.types import Object


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
    
    export_use_evaluated: bpy.props.BoolProperty(
        name="Use Evaluated Transforms",
        description="Export transforms after applying constraints, IK, and other modifiers (evaluated). If disabled, exports raw keyframe data",
        default=False
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
        
        import_box = layout.box()

        # MTAR file picker
        mtar_box = import_box
        row = mtar_box.row(align=True)
        row.prop(props, "import_mtar_filepath", text="", icon='ANIM')
        row.operator("mtar.select_import_mtar_file", text="", icon='FILE_FOLDER')

        # FRIG file picker
        frig_box = import_box
        row = frig_box.row(align=True)
        row.prop(props, "import_frig_filepath", text="", icon='OUTLINER_OB_ARMATURE')
        row.operator("mtar.select_frig_file", text="", icon='FILE_FOLDER')
        
        # Generate mapping file button
        col = frig_box.column()
        col.enabled = bool(props.import_frig_filepath)
        col.scale_y = 1
        col.operator("mtar.generate_track_mapping_template_file", text="Generate Mapping Template", icon='TEXT')

        # Track mapping file picker
        mapping_box = import_box
        row = mapping_box.row(align=True)
        row.prop(props, "import_mapping_filepath", text="", icon='TEXT')
        row.operator("mtar.select_mapping_file", text="", icon='FILE_FOLDER')

        # GANI index selector
        box = import_box
        box.prop(props, "import_gani_index", text="Anim Index", icon='FILTER')

        # Target rig selector
        box = import_box
        box.prop(props, "import_target_rig", text="", icon='ARMATURE_DATA')

        # Import button
        col = import_box.column()
        col.scale_y = 1.5
         # Disable button if required fields are missing
        col.enabled = bool(props.import_mtar_filepath)
        col.operator("mtar.import_animation", text="Import Animation", icon='IMPORT')


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
        
        export_box = layout.box()

        # Armature selector
        box = export_box
        box.prop(props, "export_armature", text="", icon='ARMATURE_DATA')

        # Motion Points armature selector
        box = export_box
        box.prop(props, "export_motion_points_armature", text="", icon='ARMATURE_DATA')

        # Mapping file (optional)
        box = export_box
        row = box.row(align=True)
        row.prop(props, "export_mapping_filepath", text="", icon='TEXT')
        row.operator("mtar.select_export_mapping_file", text="", icon='FILE_FOLDER')

        # Export file picker
        box = export_box
        row = box.row(align=True)
        row.prop(props, "export_filepath", text="", icon='CURRENT_FILE')
        row.operator("mtar.select_export_file", text="", icon='FILE_FOLDER')

        # Export options
        box = export_box
        row = box.row()
        row.prop(props, "export_use_nla", icon='OPTIONS')
        row = box.row()
        row.prop(props, "export_use_evaluated", icon='OPTIONS')
        
        # Info
        box = export_box
        if not props.export_armature:
            box.label(text="No armature selected", icon='ERROR')
        
        if not props.export_filepath:
            box.label(text="No export path set", icon='ERROR')

        # Show info about NLA status
        if props.export_armature and props.export_armature.animation_data:
            anim_data = props.export_armature.animation_data
            if anim_data.nla_tracks:
                unmuted_strips = sum(1 for track in anim_data.nla_tracks 
                                    if not track.mute 
                                    for strip in track.strips 
                                    if not strip.mute and strip.action)
                if unmuted_strips > 0:
                    box.label(text=f"Found {unmuted_strips} NLA strip(s)", icon='CHECKMARK')
                else:
                    box.label(text="No unmuted NLA strips", icon='INFO')
            elif anim_data.action:
                box.label(text="Using active action", icon='ACTION')
            else:
                box.label(text="No animation data", icon='ERROR')


        # Export button
        col = export_box.column()
        col.scale_y = 1.5
        
        # Disable button if required fields are missing
        can_export = bool(props.export_armature and props.export_filepath)
        col.enabled = can_export
        col.operator("mtar.export_animation", text="Export Animation", icon='EXPORT')


class MTAR_PT_SettingsPanel(Panel):
    """N-Panel for MTAR plugin settings."""
    bl_label = "MTAR Settings"
    bl_idname = "MTAR_PT_settings_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MTAR'
    bl_options = {'DEFAULT_CLOSED'}
    
    def draw(self, context: Context) -> None:
        layout: UILayout = self.layout
        props = context.scene.mtar_properties
        
        box = layout.box()
        box.label(text="Logging", icon='PREFERENCES')
        box.prop(props, "log_verbosity", text="", icon='INFO')
        box.prop(props, "enable_timer_logs", toggle=True, icon='MOD_TIME')


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
    MTAR_PT_ImportPanel,
    MTAR_PT_ExportPanel,
    MTAR_PT_SettingsPanel,
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
