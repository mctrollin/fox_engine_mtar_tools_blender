"""
Debug operators for MTAR tools - NLA control and bake utilities.

This module contains operator classes for NLA strip management (mute/unmute/select)
and bake operations.
Transform inspection operators are in blender_operators_debug_transform.py.
Hash utilities are in blender_operators_debug_hash.py.
"""

# pyright: reportInvalidTypeForm=false

import bpy


class MTAR_PG_DebugPanelProperties(bpy.types.PropertyGroup):
    """Property group for debug panel UI state."""

    debug_active_tab: bpy.props.EnumProperty(
        name="Page",
        items=[
            ('HASH', "Hash", "External hash generator"),
            ('NLA', "NLA", "NLA track debug tools"),
            ('BAKE', "Bake", "Animation bake tools"),
            ('TRANSFORM', "Transform", "Transform inspector"),
            ('MAP_R', "Map R", "Map_R parameter debug"),
            ('META', "Meta", "Shared metadata debug tools"),
        ],
        default='TRANSFORM'
    )


