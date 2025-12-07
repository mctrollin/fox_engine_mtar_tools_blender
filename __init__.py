from typing import Set, TYPE_CHECKING

import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from .mtar_importer import import_mtar
from . import blender_panel

if TYPE_CHECKING:
    from bpy.types import Context


bl_info = {
    "name": "Fox Engine MTAR Importer and Exporter",
    "description": "Import and Export MTAR animations from Metal Gear Solid V",
    "author": "Till - rollin - Maginot",
    "version": (1, 0),
    "blender": (2, 93, 0),
    "location" : "View3D Panel",
    "category": "Import-Export",
}


class ImportMTAR(bpy.types.Operator, ImportHelper):
    """Import MTAR animation into Blender (wrapper operator)."""
    bl_idname = "import_anim.mtar"
    bl_label = "Import MTAR"
    bl_options = {'UNDO'}

    filename_ext = ".mtar"
    filter_glob = StringProperty(default="*.mtar", options={'HIDDEN'})

    def execute(self, context: 'Context') -> Set[str]:
        # delegate heavy lifting to mtar_importer.import_mtar
        # No FRIG data when using menu import
        return import_mtar(context, self.filepath, None)

def menu_import(self, context: 'Context') -> None:
    self.layout.operator(ImportMTAR.bl_idname, text="MTAR Animation (.mtar)")

def register() -> None:
    # Register panel first (includes its own classes)
    blender_panel.register()
    
    # Register menu import operator
    bpy.utils.register_class(ImportMTAR)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)

def unregister() -> None:
    # Unregister in reverse order
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.utils.unregister_class(ImportMTAR)
    
    # Unregister panel last
    blender_panel.unregister()

if __name__ == "__main__":
    register()
