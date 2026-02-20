from typing import Set, TYPE_CHECKING

import os
import bpy
from bpy.props import StringProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper
from .py_utilities.utilities_logging import Debug
import traceback

from .py_tools.tools_mtar_importer import import_mtar
from . import blender_panel_import
from . import blender_panel_export
from . import blender_panel_settings
from . import blender_properties

blender_debug_module = None
_debug_registered = False

if TYPE_CHECKING:
    from bpy.types import Context


bl_info = {
    "name": "Fox Engine MTAR Importer and Exporter",
    "description": "Import and Export MTAR animations from Metal Gear Solid V",
    "author": "Till - rollin - Maginot",
    "version": (1, 0),
    "blender": (3, 0, 0),
    "location" : "View3D Panel",
    "category": "Import-Export",
}


class MTAR_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__
    
    def _update_enable_debug(self, context):
        """Update callback for addon preferences - toggles debug registration."""
        global _debug_registered
        global blender_debug_module
        try:
            # If enabling debug and not already registered, try to register
            if self.enable_debug_tools and not _debug_registered:
                from . import blender_panel_debug as _bd
                blender_debug_module = _bd
                blender_debug_module.register()
                # bpy.utils.register_class(MTAR_PT_DebugPanel)
                _debug_registered = True
            # If disabling and currently registered, unregister
            elif (not self.enable_debug_tools) and _debug_registered and blender_debug_module is not None:
                blender_debug_module.unregister()
                # bpy.utils.unregister_class(MTAR_PT_DebugPanel)
                _debug_registered = False
        except Exception as e:
            # Avoid raising errors in the UI, but log exception for debugging
            _debug_registered = False
            try:
                Debug.log_error(f"Error toggling debug tools: {e}")
                Debug.log_error(traceback.format_exc())
            except Exception:
                # Fallback to printing if logging is unavailable
                print(f"[ERROR] Error toggling debug tools: {e}")

    enable_debug_tools: BoolProperty(
        name="Enable Debug Tools",
        default=False,
        description="Show extra diagnostic panels for development",
        update=_update_enable_debug
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'enable_debug_tools')



def register() -> None:
    # Register properties first
    blender_properties.register()

    # Register panels (includes their own classes)
    blender_panel_import.register()
    blender_panel_export.register()
    blender_panel_settings.register()

    # Register addon preferences so users can toggle debug tools
    try:
        bpy.utils.register_class(MTAR_AddonPreferences)
    except Exception:
        # Safe to ignore; registration may happen in a different order
        pass

    # Decide whether to register the debug module
    enable_debug = os.environ.get('MTAR_DEBUG', '0') == '1'
    # Check addon preferences safely (don't assume the add-on key exists yet)
    try:
        addon = bpy.context.preferences.addons.get(__name__)
        if addon is not None:
            prefs = addon.preferences
            enable_debug = enable_debug or bool(getattr(prefs, 'enable_debug_tools', False))
    except Exception:
        # bpy.context/preferences may not be available in some contexts (headless)
        pass

    global _debug_registered
    global blender_debug_module
    _debug_registered = False
    if enable_debug:
        try:
            from . import blender_panel_debug as _bd
            blender_debug_module = _bd
            blender_debug_module.register()
            _debug_registered = True
        except Exception:
            # Guard against errors in debug module registration
            _debug_registered = False



def unregister() -> None:

    # Unregister debug panel if it was registered
    global _debug_registered
    global blender_debug_module
    if _debug_registered and blender_debug_module is not None:
        try:
            blender_debug_module.unregister()
        except Exception:
            pass
        _debug_registered = False
        blender_debug_module = None

    # Unregister addon preferences
    try:
        bpy.utils.unregister_class(MTAR_AddonPreferences)
    except Exception:
        pass

    # Unregister panels
    blender_panel_settings.unregister()
    blender_panel_export.unregister()
    blender_panel_import.unregister()

    # Unregister properties last
    blender_properties.unregister()

if __name__ == "__main__":
    register()
