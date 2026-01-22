"""
Blender property groups for MTAR import and export functionality.
"""
import bpy
from bpy.types import PropertyGroup
from bpy.props import StringProperty, PointerProperty, IntProperty, BoolProperty, FloatProperty, EnumProperty

# pyright: reportInvalidTypeForm=false

class MTAR_PG_ImportProperties(PropertyGroup):
    """Property group for MTAR import settings."""
    mtar_filepath: StringProperty(
        name="MTAR File",
        description="Path to the .mtar animation file",
        default="",
        maxlen=1024,
    )
    
    frig_filepath: StringProperty(
        name="FRIG File",
        description="Path to the .frig rig file",
        default="",
        maxlen=1024,
    )
    
    mapping_filepath: StringProperty(
        name="Track Mapping File",
        description="Path to the .txt file defining track transformations (renaming, rotation offsets, axis mapping, etc.)",
        default="",
        maxlen=1024,
    )
    
    gani_indices_str: StringProperty(
        name="GANI Selection",
        description=(
            "Select GANI indices to import. Leave empty to import all.\n"
            "Syntax:\n"
            "  • Ranges: 0-2 (indices 0,1,2)\n"
            "  • Individual: 30,40\n"
            "  • Exclusion: !300 (exclude index 300)\n"
            "  • Exclusion ranges: !400-500\n"
            "  • Combined: 0-2,30,40,!300,!400-500"
        ),
        default="",
        maxlen=256,
    )
    
    custom_rig: PointerProperty(
        name="custom rig",
        description="Optional Rigify rig to connect to imported animation (constraints will be added based on mapping file)",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE'
    )
    
    ik_up_distance: FloatProperty(
        name="IK Up Distance",
        description="Distance for directional IK up vector calculation",
        default=1.0,
        min=0.0,
        soft_max=10.0,
    )
    
    bake_after_import: BoolProperty(
        name="Bake custom rig Constraints",
        description="Bake the constraints from the rig into the animation.",
        default=True
    )
    
    delete_import_armature: BoolProperty(
        name="Delete Raw Import-Armature",
        description="Remove the temporary imported armature after baking is complete",
        default=True
    )
    
    strip_padding: IntProperty(
        name="Strip Padding (Frames)",
        description="Number of frames to insert between animation strips to prevent overlap",
        default=10,
        min=0,
    )

class MTAR_PG_ExportProperties(PropertyGroup):
    """Property group for MTAR export settings."""
    armature: PointerProperty(
        name="Export Armature",
        description="Armature to export animation from",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE'
    )
    
    filepath: StringProperty(
        name="Export File",
        description="Path for the exported .mtar animation file",
        default="",
        maxlen=1024,
    )
    
    mapping_filepath: StringProperty(
        name="Export Mapping File",
        description="Path to the bone mapping file for export transformations",
        default="",
        maxlen=1024,
    )
    
    use_nla: BoolProperty(
        name="Export NLA Strips",
        description="Export all unmuted NLA strips as separate GANI files. If disabled or no NLA tracks exist, exports only the active action",
        default=True
    )

    custom_path_hashes: BoolProperty(
        name="Export Custom Path Hashes",
        description="Also export hashes for a custom base path",
        default=False
    )

    custom_path_base: StringProperty(
        name="Hash Base Path",
        description="Base path to use for custom path hashes",
        default="/Assets/tpp/",
        maxlen=1024
    )

    info_file: BoolProperty(
        name="Export Info File",
        description="Write a '<mtar file name>.mtar.info.txt' file containing exported GANI names or hashes",
        default=True
    )

    force_highest_bit_encoding: BoolProperty(
        name="Force highest bit encoding",
        description="When enabled, export uses the highest available bit encoding for each segment (may increase file size)",
        default=False
    )

    motion_points_armature: PointerProperty(
        name="Motion Points Armature",
        description="Optional armature that contains motion point bones to export (name should match <base>_MotionPoints if auto-detected).",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE'
    )

class MTAR_PG_ExecutionProperties(PropertyGroup):
    """Property group for tracking operation progress and status."""
    progress: FloatProperty(
        name="Execution Progress",
        description="Progress of the current operation",
        default=0.0,
        min=0.0,
        max=1.0,
    )
    
    status: StringProperty(
        name="Execution Status",
        description="Current status of the operation",
        default="",
    )
    
    operation_type: EnumProperty(
        name="Execution Operation Type",
        items=[
            ('NONE', "None", ""),
            ('IMPORT', "Import", ""),
            ('EXPORT', "Export", ""),
        ],
        default='NONE'
    )

class MTAR_PG_SettingsProperties(PropertyGroup):
    """Property group for general plugin settings."""
    show_advanced_settings: BoolProperty(
        name="Show Advanced Settings",
        description="Display advanced import/export settings",
        default=False
    )
    
    log_verbosity: EnumProperty(
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
    
    enable_timer_logs: BoolProperty(
        name="Log timings",
        description="Log performance timing information for import/export operations",
        default=False
    )
    
    enable_rest_pose_correction: BoolProperty(
        name="Enable Rest Pose Correction",
        description="Automatically extract and apply rest pose corrections from custom rig/armature (map_r for local space, offset_r for world space). Disable to use only mapping file transformations",
        default=True
    )

    hash_generator_exe_path: StringProperty(
        name="Hash Generator Executable",
        description="Path to the external hash generator executable (GzsTool fork with debug output)",
        default="",
        maxlen=1024,
        subtype='FILE_PATH'
    )

class MTAR_PG_Properties(PropertyGroup):
    """Main property group containing all MTAR sub-properties."""
    import_props: PointerProperty(type=MTAR_PG_ImportProperties)
    export_props: PointerProperty(type=MTAR_PG_ExportProperties)
    execution_props: PointerProperty(type=MTAR_PG_ExecutionProperties)
    settings_props: PointerProperty(type=MTAR_PG_SettingsProperties)

def register():
    bpy.utils.register_class(MTAR_PG_ImportProperties)
    bpy.utils.register_class(MTAR_PG_ExportProperties)
    bpy.utils.register_class(MTAR_PG_ExecutionProperties)
    bpy.utils.register_class(MTAR_PG_SettingsProperties)
    bpy.utils.register_class(MTAR_PG_Properties)
    
    bpy.types.Scene.mtar_properties = PointerProperty(type=MTAR_PG_Properties)

def unregister():
    del bpy.types.Scene.mtar_properties
    
    bpy.utils.unregister_class(MTAR_PG_Properties)
    bpy.utils.unregister_class(MTAR_PG_SettingsProperties)
    bpy.utils.unregister_class(MTAR_PG_ExecutionProperties)
    bpy.utils.unregister_class(MTAR_PG_ExportProperties)
    bpy.utils.unregister_class(MTAR_PG_ImportProperties)
