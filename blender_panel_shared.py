"""
Shared drawing utilities for the MTAR panels.

Functions here are originally from ``blender_panel_import`` but are used by
both import and export pages (and the debug UI).  Moving them to a dedicated
module breaks remaining interdependencies and gives a single home for
common helpers.
"""

from typing import Optional

import bpy
from bpy.types import UILayout


def draw_bool_prop_checkbox_icon(layout: UILayout, props, property_name: str,
                                 text: Optional[str] = None, **prop_kwargs) -> None:
    """Draw a boolean property with checkbox-highlight icon when True.

    If the caller passes an explicit ``icon`` via ``prop_kwargs``, that icon
    will be used instead of the checkbox style.

    Args:
        layout: Blender UILayout (row/column/box)
        props: PropertyGroup instance, typically ``context.scene.mtar_properties``
        property_name: Name of the boolean property on ``props``
        text: Optional label text override (default: uses property name)
        prop_kwargs: Additional keyword args forwarded to ``layout.prop``
                     (e.g. ``toggle=True``).
    """
    try:
        value = getattr(props, property_name)
    except AttributeError:
        value = False

    icon_prop = prop_kwargs.pop('icon', None)
    if icon_prop:
        icon = icon_prop
    else:
        icon = 'CHECKBOX_HLT' if value else 'CHECKBOX_DEHLT'
    label_text = text if text is not None else None
    layout.prop(props, property_name, icon=icon, text=label_text, **prop_kwargs)



def draw_progress_bar(layout: UILayout, props, operation_type: str) -> None:
    """Draw a progress bar if supported by the Blender version (4.0+)."""
    exec_props = props.execution_props
    is_active = operation_type == exec_props.operation_type
    progress: float = exec_props.progress if is_active else 0
    status_text: str = exec_props.status if is_active else ""

    if hasattr(layout, "progress"):
        col = layout.column()
        col.scale_y = 0.6
        col.progress(factor=progress, text=status_text)
