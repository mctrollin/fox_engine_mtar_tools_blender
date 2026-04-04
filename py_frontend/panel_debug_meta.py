"""Meta debug panel for MTAR tools."""

import os

import bpy
from bpy.types import UILayout, Context


def draw_meta_page(layout: UILayout, context: Context) -> None:
    """Draw the shared metadata debug page."""
    props = context.scene.mtar_properties

    box = layout.box()
    box.label(text="Metadata Tools", icon='ARMATURE_DATA')

    # Source MTAR file — reuse the import panel's shared mtar_filepath property
    src_box = box.box()
    src_box.label(text="Source MTAR", icon='FILE_BLANK')
    src_box.prop(props.import_props, "mtar_filepath", text="")

    mtar_path = bpy.path.abspath(props.import_props.mtar_filepath or '')
    has_armature = bool(context.active_object and context.active_object.type == 'ARMATURE')
    has_mtar = bool(mtar_path and os.path.exists(mtar_path))

    # Re-sync operator
    upgrade_box = box.box()

    if not has_armature:
        upgrade_box.label(text="Select an armature in the viewport", icon='ERROR')
    if not has_mtar:
        upgrade_box.label(text="Set a valid MTAR file path above", icon='ERROR')

    col = upgrade_box.column()
    col.enabled = has_armature and has_mtar
    col.operator("mtar.upgrade_events_from_mtar", text="Re-sync Events from MTAR", icon='FILE_REFRESH')

    rename_box = box.box()
    if not has_armature:
        rename_box.label(text="Select an armature in the viewport", icon='ERROR')
    col = rename_box.column()
    col.enabled = has_armature
    col.operator("mtar.rename_track_property_keys", text="Rename Track Property Keys", icon='SORTALPHA')
