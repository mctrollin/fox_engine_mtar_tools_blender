"""
Shared drawing utilities for the MTAR panels.

Functions here are originally from ``blender_panel_import`` but are used by
both import and export pages (and the debug UI).  Moving them to a dedicated
module breaks remaining interdependencies and gives a single home for
common helpers.
"""

from typing import Optional

import math
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



def draw_progress_bar(layout: UILayout, props) -> None:
    """Draw a shared progress bar if supported by Blender (4.0+).

    The tabbed UI ensures only one page is visible, so we no longer need an
    ``operation_type`` filter.  The bar simply reflects whatever state is
    stored in ``props.execution_props``.
    """
    exec_props = props.execution_props
    progress: float = exec_props.progress
    status_text: str = exec_props.status

    if hasattr(layout, "progress"):
        row = layout.row(align=True)
        row.scale_y = 0.8
        col = row.column()
        if props.settings_props.use_redraw_timer:
            col.progress(factor=progress, text=status_text)

        if props.settings_props.show_advanced_settings:
            draw_bool_prop_checkbox_icon(row, props.settings_props, "use_redraw_timer", text=("" if props.settings_props.use_redraw_timer else " "), toggle=True, icon="RECOVER_LAST")
            draw_bool_prop_checkbox_icon(row, props.settings_props, "enable_timer_logs", text="", toggle=True, icon="MOD_TIME")


def draw_estimated_operation_time(
    layout: UILayout,
    count: int | None,
    seconds_per_item: float,
    warn_threshold_seconds: int = 60,
) -> None:
    """Draw an estimated operation duration and optional warning.

    Args:
        layout: UI layout to draw into.
        count: Number of items being processed (e.g. GANIs). If None or 0, no UI is drawn.
        seconds_per_item: Estimated seconds per item.
        warn_threshold_seconds: When the total estimated duration exceeds this,
            mark the label as alerting and draw a "view console" warning.
    """
    if not count or count <= 0:
        return

    total_seconds = count * seconds_per_item
    if total_seconds >= 60:
        minutes = math.ceil(total_seconds / 60)
        est_text = f"Time: ~ {minutes}m"
    else:
        secs = int(total_seconds)
        est_text = f"Time: ~ {secs}s"

    row = layout.row()
    if total_seconds >= warn_threshold_seconds:
        row.alert = True
    row.label(text=est_text, icon='TIME')

    if total_seconds >= warn_threshold_seconds:
        warn_row = layout.row(align=True)
        warn_row.alert = True
        warn_row.label(text="View console to track progress.", icon='INFO')
        # Quick toggle for Blender's system console (Windows) / terminal (macOS/Linux)
        warn_row.operator("wm.console_toggle", text="", icon="CONSOLE")
