"""
Shared drawing utilities for the MTAR panels.

Functions here are originally from ``blender_panel_import`` but are used by
both import and export pages (and the debug UI).  Moving them to a dedicated
module breaks remaining interdependencies and gives a single home for
common helpers.
"""

import os
from typing import Optional
import math

import bpy
from bpy.types import UILayout

from .py_utilities import util_filtering


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


def draw_gani_selection_filter(
    layout: UILayout,
    main_props,
    indices_props,
    index_prop_name: str,
    total_count: int | None,
) -> int | None:
    """Draw either a GANI index selector or a file-based filter selector.

    This helper unifies the logic for import and export GANI selection.

    Args:
        layout: UI layout to draw into.
        main_props: Top-level property group containing use_gani_filter_file and gani_filter_txt_filepath.
        indices_props: Import/export property group containing gani_indices_str.
        index_prop_name: Name of the index selection string property (e.g. "gani_indices_str").
        total_count: Total number of available GANIs, used for validating selection.
            If None, only the input field is shown but no count/error message is displayed.

    Returns:
        Tuple of (selected_count, selected_indices, error_message).
    """

    box = layout.box()
    row = box.row(align=True)

    if main_props.use_gani_filter_file:
        row.prop(main_props, "gani_filter_txt_filepath", text="", icon='FILE_TEXT')
    else:
        row.prop(indices_props, index_prop_name, text="", icon='FILTER')

    row.prop(main_props, "use_gani_filter_file", text="", icon='FILE_TEXT')

    # Filter by path selection file -------------------------------
    if main_props.use_gani_filter_file:
        row = box.row()
        filter_file_abs = bpy.path.abspath(main_props.gani_filter_txt_filepath.strip())
        if not filter_file_abs:
            row.label(text="Filter file not set", icon='ERROR')
            return None

        if os.path.exists(filter_file_abs):
            row = box.row()
            valid_filter_lines = util_filtering.count_filter_file_valid_entries(filter_file_abs)
            row.label(text=f"Filter entries: {valid_filter_lines}", icon='CHECKMARK')
            # Max possible is bounded by available GANI count
            if total_count is not None:
                return min(valid_filter_lines, total_count)
            return valid_filter_lines
        else:
            row.label(text="Filter file missing", icon='ERROR')
            return None

    if total_count is None:
        return None

    # Filter by index selection string -------------------------------
    selection_str = getattr(indices_props, index_prop_name, "").strip()

    if selection_str:
        header_indices, data_indices = util_filtering.prepare_gani_selection_indices(selection_str, total_count, 'AUTO')
        selected_count = len(set(header_indices + data_indices))
        if selected_count == 0:
            parse_error_msg = "No valid indices in selection"
        else:
            parse_error_msg = None
    else:
        selected_count = total_count
        parse_error_msg = None

    if parse_error_msg:
        err_row = box.row()
        err_row.alert = True
        err_row.label(text=f"{parse_error_msg}", icon='ERROR')
    else:
        label_text = f"{selected_count} of {total_count} animation{'s' if selected_count != 1 else ''}"
        row = box.row()
        row.label(text=label_text, icon='CHECKMARK')

    return selected_count
