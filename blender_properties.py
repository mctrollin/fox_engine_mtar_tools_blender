"""
Blender property groups for MTAR import and export functionality.
"""
import bpy
from bpy.types import PropertyGroup
from bpy.props import StringProperty, PointerProperty, IntProperty, BoolProperty, FloatProperty, EnumProperty

# pyright: reportInvalidTypeForm=false

# Helper to add relative path support based on Blender version
def _file_path_kwargs(**kwargs):
    """Add FILE_PATH subtype and relative path support if available.
    
    PATH_SUPPORTS_BLEND_RELATIVE option is available in Blender 4.5+
    """
    kwargs['subtype'] = 'FILE_PATH'
    # Add relative path support for Blender 4.0+
    if bpy.app.version >= (4, 5, 0):
        kwargs['options'] = {'PATH_SUPPORTS_BLEND_RELATIVE'}
    return kwargs

# Helper: apply/show pose markers in all Dope Sheet / Action Editor areas
def _apply_show_pose_markers_value(value: bool) -> None:
    """Set the Action Editor's `show_pose_markers` flag across all windows/screens.

    This toggles the visual display of action pose markers in Dope Sheet / Action Editor
    areas so the user can control marker visibility from the add-on settings.
    """
    
    wm = bpy.context.window_manager

    for window in wm.windows:
        screen = window.screen
        for area in screen.areas:
            # Target the Dope Sheet / Action Editor area
            if area.type == 'DOPESHEET_EDITOR':
                try:
                    sp = area.spaces.active
                    if hasattr(sp, 'show_pose_markers'):
                        sp.show_pose_markers = bool(value)
                except Exception:
                    # Ignore areas that do not support action editor settings
                    continue

            # Also target the NLA Editor to toggle SpaceNLA.show_local_markers
            if area.type == 'NLA_EDITOR':
                try:
                    sp = area.spaces.active
                    if hasattr(sp, 'show_local_markers'):
                        sp.show_local_markers = bool(value)
                except Exception:
                    # Ignore areas that do not support NLA settings
                    continue


def _compute_action_show_pose_markers() -> bool:
    """Return True iff all visible Dope Sheet / Action Editor areas have pose markers enabled.

    If no Dope Sheet areas are present, return True (neutral behavior).
    """
    try:
        wm = bpy.context.window_manager
    except Exception:
        return True

    seen = False
    for window in wm.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type == 'DOPESHEET_EDITOR':
                seen = True
                try:
                    sp = area.spaces.active
                    if hasattr(sp, 'show_pose_markers') and not sp.show_pose_markers:
                        return False
                except Exception:
                    continue
    return True


def _compute_nla_show_local_markers() -> bool:
    """Return True iff all visible NLA Editor areas have local markers enabled.

    If no NLA areas are present, return True (neutral behavior).
    """
    try:
        wm = bpy.context.window_manager
    except Exception:
        return True

    for window in wm.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type == 'NLA_EDITOR':
                try:
                    sp = area.spaces.active
                    if hasattr(sp, 'show_local_markers') and not sp.show_local_markers:
                        return False
                except Exception:
                    continue
    return True


def _get_show_pose_markers(self) -> bool:
    """Property getter: True iff both action editor pose markers and NLA local markers are enabled."""
    return bool(_compute_action_show_pose_markers() and _compute_nla_show_local_markers())


def _set_show_pose_markers(self, value: bool) -> None:
    """Property setter: set stored preference and apply value to both editor types."""
    try:
        # Persist user intention
        self.show_pose_markers_pref = bool(value)
    except Exception:
        pass
    # Apply to editors immediately
    _apply_show_pose_markers_value(bool(value))


def _update_show_pose_markers(self, context) -> None:
    # Backwards-compatible update for the stored preference
    _apply_show_pose_markers_value(bool(self.show_pose_markers_pref))

class MTAR_PG_ImportProperties(PropertyGroup):
    """Property group for MTAR import settings."""
    mtar_filepath: StringProperty(**_file_path_kwargs(
        name="MTAR File",
        description="Path to the .mtar animation file",
        default="",
        maxlen=1024
    ))
    
    frig_filepath: StringProperty(**_file_path_kwargs(
        name="FRIG File",
        description="Path to the .frig rig file",
        default="",
        maxlen=1024
    ))
    
    mapping_filepath: StringProperty(**_file_path_kwargs(
        name="Track Mapping File",
        description="Path to the .txt file defining track transformations (renaming, rotation offsets, axis mapping, etc.)",
        default="",
        maxlen=1024
    ))
    
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

    interpolation_force_linear_track_types: StringProperty(
        name="Decimation Track Type Filter",
        description=(
            "Comma-separated list of track types to EXCLUDE from decimation (keep linear).\n"
            "Empty = decimate all tracks.\n"
            "Available types: ROOT, ORIENTATION, TWO_BONE, LOCAL_ORIENTATION, LOCAL_TRANSFORM,\n"
            "THREE_BONE_LIKE_TWO_BONE, TRANSFORM, ARM, LOCAL_TRANSFORM_SRT, ANIMAL_LEG,\n"
            "MULTI_LOCAL_ORIENTATION, TWO_BONE_TRANS\n"
            "Example: ROOT,TWO_BONE,TRANSFORM"
        ),
        default="ROOT",
        maxlen=256
    )
    
    import_decimate_error: FloatProperty(
        name="Decimate Error Threshold",
        description="Error threshold for keyframe decimation (0.0 = skip decimation, higher = more aggressive)",
        default=0.01,
        min=0.0,
        max=1.0,
        precision=3
    )
    
    use_verbose_naming: BoolProperty(
        name="Verbose Naming",
        description=(
            "Include header and data indices in action/strip names.\n"
            "Verbose: player2.0.h340_d278.gani\n"
            "Simple: player2.0.gani\n"
            "h = header index (position in MTAR file table)\n"
            "d = data index (position sorted by file offset)"
        ),
        default=True
    )

class MTAR_PG_ExportProperties(PropertyGroup):
    """Property group for MTAR export settings."""
    armature: PointerProperty(
        name="Export Armature",
        description="Armature to export animation from",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE'
    )
    
    filepath: StringProperty(**_file_path_kwargs(
        name="Export File",
        description="Path for the exported .mtar animation file",
        default="",
        maxlen=1024
    ))
    
    mapping_filepath: StringProperty(**_file_path_kwargs(
        name="Export Mapping File",
        description="Path to the bone mapping file for export transformations",
        default="",
        maxlen=1024
    ))
    
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
    
    export_clean_threshold: FloatProperty(
        name="Clean Threshold",
        description="Threshold for removing redundant keyframes after baking non linear interpolated fcurves (0.0 = skip cleaning, higher = more aggressive)",
        default=0.03,
        min=0.0,
        max=1.0,
        precision=3
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

    # Toggle whether the add-on uses bpy.ops.wm.redraw_timer for UI redraws
    use_redraw_timer: BoolProperty(
        name="Use Redraw Timer",
        description="Enable use of bpy.ops.wm.redraw_timer in progress updates (disable if this causes instability)",
        default=True
    )
    
    enable_rest_pose_correction: BoolProperty(
        name="Enable Rest Pose Correction",
        description="Automatically extract and apply rest pose corrections from custom rig/armature (map_r for local space, offset_r for world space). Disable to use only mapping file transformations. You want to keep this enabled for pretty much any normal situation.",
        default=True
    )

    # Sorting GANI import/export to match file order / hash
    sort_gani: BoolProperty(
        name="Sort GANI",
        description="When enabled, import will reorder GANIs by file offset and export will sort the MTAR file table by path hash. Can be disabled for testing.",
        default=True
    )

    # Stored preference for marker visibility (persisted)
    show_pose_markers_pref: BoolProperty(
        name="Show Pose Markers (pref)",
        description="Stored preference controlling pose marker visibility for editors",
        default=True,
        update=_update_show_pose_markers
    )

    # Public property shown in UI — computed: True iff both editor types have markers shown
    show_pose_markers: BoolProperty(
        name="Show Pose Markers",
        description="Toggle display of pose markers in the Action/Dope Sheet and NLA editors (true only if both are enabled)",
        get=_get_show_pose_markers,
        set=_set_show_pose_markers
    )

    hash_generator_exe_path: StringProperty(**_file_path_kwargs(
        name="Hash Generator Executable",
        description="Path to the external hash generator executable (GzsTool fork with debug output)",
        default="",
        maxlen=1024
    ))

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

    # Ensure UI is synced with current setting on register
    try:
        _apply_show_pose_markers_value(bpy.context.scene.mtar_properties.settings_props.show_pose_markers_pref)
    except Exception:
        pass

def unregister():
    del bpy.types.Scene.mtar_properties
    
    bpy.utils.unregister_class(MTAR_PG_Properties)
    bpy.utils.unregister_class(MTAR_PG_SettingsProperties)
    bpy.utils.unregister_class(MTAR_PG_ExecutionProperties)
    bpy.utils.unregister_class(MTAR_PG_ExportProperties)
    bpy.utils.unregister_class(MTAR_PG_ImportProperties)
