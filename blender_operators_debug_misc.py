"""
Misc debug operators for MTAR tools (filter-file helpers and NLA control).
"""

# pyright: reportInvalidTypeForm=false

import re
import os
import bpy
from bpy.types import Operator, Context

from .py_core.core_logging import Debug
from .py_fox.fox_mtar_constants import TABL_PATH
from .py_utilities import util_filtering, util_hashing

# Shared regex for verbose h/d naming in strip/action names
_PATH_H_D_RE = re.compile(r"(?:^|\.)h(?P<h>\d+)_d(?P<d>\d+)(?:\.|$)")


def _get_h_d_from_strip(strip):
    """Extract (h,d) indices from strip or strip.action name."""
    name = strip.name or ''
    if not name and strip.action:
        name = strip.action.name or ''

    m = _PATH_H_D_RE.search(name)
    if not m and strip.action and strip.action.name:
        m = _PATH_H_D_RE.search(strip.action.name)

    if not m:
        return None

    return int(m.group('h')), int(m.group('d'))


def _find_matching_strips(armature, header_set, data_set):
    """Yield strips matching header/data index sets."""
    if not armature or not armature.animation_data:
        return

    for track in getattr(armature.animation_data, 'nla_tracks', []):
        for strip in track.strips:
            hd = _get_h_d_from_strip(strip)
            if not hd:
                continue
            h_idx, d_idx = hd
            if (h_idx in header_set) or (d_idx in data_set):
                yield track, strip, h_idx, d_idx


def _parse_index_set_text(text: str, index_mode: str):
    """Parse hN/dN lines from a text blob into sets."""
    header_set = set()
    data_set = set()
    invalid = []

    for token in re.split(r'[\n;,]+', text or ""):
        raw = token.strip()
        if not raw:
            continue

        if raw.lower().startswith('h') and raw[1:].isdigit():
            header_set.add(int(raw[1:]))
            continue
        if raw.lower().startswith('d') and raw[1:].isdigit():
            data_set.add(int(raw[1:]))
            continue

        if raw.isdigit():
            if index_mode == 'HEADER':
                header_set.add(int(raw))
            elif index_mode == 'DATA':
                data_set.add(int(raw))
            else:
                header_set.add(int(raw))
                data_set.add(int(raw))
            continue

        invalid.append(raw)

    if invalid:
        Debug.log(f"Ignored invalid index tokens: {invalid}")

    return header_set, data_set


def _get_nla_filter_shape(context):
    main_props = context.scene.mtar_properties
    filter_path = bpy.path.abspath((main_props.gani_filter_txt_filepath or '').strip())

    if not filter_path:
        return None
    if not os.path.exists(filter_path):
        return None

    return util_filtering.load_gani_filter_list_with_indices(filter_path)


def _strip_passes_gani_filter(
    action_path_hash,
    strip_idx,
    h_idx,
    d_idx,
    allowed_hashes,
    excluded_hashes,
    allowed_header_indices,
    excluded_header_indices,
    allowed_data_indices,
    excluded_data_indices,
    allowed_strip_indices,
    excluded_strip_indices,
):
    # Exclusion precedence
    if h_idx is not None and h_idx in excluded_header_indices:
        return False
    if d_idx is not None and d_idx in excluded_data_indices:
        return False
    if strip_idx is not None and strip_idx in excluded_strip_indices:
        return False
    if action_path_hash is not None and action_path_hash in excluded_hashes:
        return False

    has_allow_rules = bool(
        allowed_hashes
        or allowed_header_indices
        or allowed_data_indices
        or allowed_strip_indices
    )

    if not has_allow_rules:
        return True

    # Allow-by-match rules
    if action_path_hash is not None and action_path_hash in allowed_hashes:
        return True
    if h_idx is not None and h_idx in allowed_header_indices:
        return True
    if d_idx is not None and d_idx in allowed_data_indices:
        return True
    if strip_idx is not None and strip_idx in allowed_strip_indices:
        return True

    return False


def _resolve_action_path_hash(action):
    if not action:
        return None

    path_value = None
    if TABL_PATH in action.keys():
        path_value = str(action[TABL_PATH]).strip()

    if not path_value:
        return None

    if util_hashing.is_hash_string(path_value):
        try:
            return util_hashing.parse_gani_hash_str(path_value)
        except ValueError:
            return None

    normalized_path = util_filtering.normalize_gani_path(path_value)
    return util_filtering.hash_gani_path_input(normalized_path)


def _collect_nla_matches_from_sets(
    context,
    allowed_hashes,
    excluded_hashes,
    allowed_header_indices,
    excluded_header_indices,
    allowed_data_indices,
    excluded_data_indices,
    allowed_strip_indices,
    excluded_strip_indices,
):
    if not context.active_object or context.active_object.type != 'ARMATURE':
        return None, "Active object is not an armature"

    armature = context.active_object
    nla = getattr(armature.animation_data, 'nla_tracks', None) if armature.animation_data else None
    if not nla:
        return None, "No NLA tracks found on active armature"

    match_info = []

    for track in nla:
        # Track strip indices counted per track starting at 0; skip layout-type strips (frame start <0)
        valid_strips = [s for s in track.strips if s.frame_start >= 0]
        for strip_idx, strip in enumerate(valid_strips):
            action = strip.action
            if not action:
                continue

            action_hash = _resolve_action_path_hash(action)
            h_d = _get_h_d_from_strip(strip)
            h_idx = None
            d_idx = None
            if h_d is not None:
                h_idx, d_idx = h_d

            if not _strip_passes_gani_filter(
                action_hash,
                strip_idx,
                h_idx,
                d_idx,
                allowed_hashes,
                excluded_hashes,
                allowed_header_indices,
                excluded_header_indices,
                allowed_data_indices,
                excluded_data_indices,
                allowed_strip_indices,
                excluded_strip_indices,
            ):
                continue

            path_val = None
            if action and TABL_PATH in action.keys():
                path_val = str(action[TABL_PATH]).strip()

            match_info.append({'track': track, 'strip': strip, 'h': h_idx, 'd': d_idx, 'path': path_val})

    if not match_info:
        return [], "No matching NLA strips found"

    return match_info, None


def _collect_nla_matches(context):
    props = context.scene.mtar_debug_transform_properties
    mode = props.debug_misc_input_mode

    if mode == 'FILTER_FILE':
        filter_path = bpy.path.abspath((context.scene.mtar_properties.gani_filter_txt_filepath or '').strip())
        if not filter_path or not os.path.exists(filter_path):
            return None, "GANI filter file path invalid or missing"
        try:
            with open(filter_path, 'r', encoding='utf-8', errors='ignore') as f:
                text_blob = f.read()
        except OSError as e:
            return None, f"Failed to read GANI filter file: {e}"
    elif mode == 'CSV':
        text_blob = (props.debug_misc_csv_input or '').strip()
    else:  # CLIPBOARD
        text_blob = (context.window_manager.clipboard or '').strip()

    (
        allowed_hashes,
        excluded_hashes,
        allowed_header_indices,
        excluded_header_indices,
        allowed_data_indices,
        excluded_data_indices,
        allowed_strip_indices,
        excluded_strip_indices,
    ) = util_filtering.parse_gani_filter_text(text_blob)

    if not any([
        allowed_hashes,
        excluded_hashes,
        allowed_header_indices,
        excluded_header_indices,
        allowed_data_indices,
        excluded_data_indices,
        allowed_strip_indices,
        excluded_strip_indices,
    ]):
        return [], "No valid filter tokens in selected input source"

    return _collect_nla_matches_from_sets(
        context,
        allowed_hashes,
        excluded_hashes,
        allowed_header_indices,
        excluded_header_indices,
        allowed_data_indices,
        excluded_data_indices,
        allowed_strip_indices,
        excluded_strip_indices,
    )


class _MTAR_OT_Debug_CopyNLAByFilterFileBase(Operator):
    """Base operator for copying filter-matched NLA values to clipboard."""

    output_type = 'PATH'  # PATH, H, D

    def execute(self, context: Context):
        result, error = _collect_nla_matches(context)
        if error:
            Debug.report_and_log(self, 'WARNING', error)
            return {'FINISHED'}

        values = []
        for item in result:
            if self.output_type == 'PATH':
                if item['path']:
                    values.append(item['path'])
            elif self.output_type == 'H':
                values.append(f"h{item['h']}")
            elif self.output_type == 'D':
                values.append(f"d{item['d']}")

        unique = []
        seen = set()
        for v in values:
            if v not in seen:
                seen.add(v)
                unique.append(v)

        context.window_manager.clipboard = "\n".join(unique)

        if unique:
            Debug.report_and_log(self, 'INFO', f"Copied {len(unique)} items ({self.output_type}) to clipboard")
        else:
            Debug.report_and_log(self, 'WARNING', "No matching values to copy to clipboard")

        return {'FINISHED'}


class MTAR_OT_DebugCopyNLAPathByFilterFile(_MTAR_OT_Debug_CopyNLAByFilterFileBase):
    bl_idname = "mtar.debug_copy_nla_path_by_filter"
    bl_label = "Copy Paths from Filter File"
    bl_description = "Copy matched NLA Path values from current filter file to clipboard"
    output_type = 'PATH'


class MTAR_OT_DebugCopyNLADByFilterFile(_MTAR_OT_Debug_CopyNLAByFilterFileBase):
    bl_idname = "mtar.debug_copy_nla_d_by_filter"
    bl_label = "Copy dN from Filter File"
    bl_description = "Copy matched NLA data indices from current filter file to clipboard"
    output_type = 'D'


class MTAR_OT_DebugCopyNLAHByFilterFile(_MTAR_OT_Debug_CopyNLAByFilterFileBase):
    bl_idname = "mtar.debug_copy_nla_h_by_filter"
    bl_label = "Copy hN from Filter File"
    bl_description = "Copy matched NLA header indices from current filter file to clipboard"
    output_type = 'H'


def _run_filter_mute_unmute(operator, context, mute_value=None, toggle=False):
    result, error = _collect_nla_matches(context)
    if error:
        Debug.report_and_log(operator, 'WARNING', error)
        return {'FINISHED'}

    matched = 0
    for info in result:
        strip = info['strip']
        if toggle:
            strip.mute = not strip.mute
        else:
            strip.mute = mute_value
        matched += 1

    action = 'Toggled' if toggle else ('Muted' if mute_value else 'Unmuted')
    Debug.report_and_log(operator, 'INFO' if matched else 'WARNING', f"{action} {matched} matching NLA strip(s)")
    return {'FINISHED'}


class MTAR_OT_DebugToggleMuteNLAByFilterFile(Operator):
    bl_idname = "mtar.debug_toggle_mute_nla_by_filter"
    bl_label = "Toggle Mute by Filter"
    bl_description = "Toggle mute on NLA strips matching current filter file"

    def execute(self, context: Context):
        return _run_filter_mute_unmute(self, context, toggle=True)


class MTAR_OT_DebugMuteNLAByFilterFile(Operator):
    bl_idname = "mtar.debug_mute_nla_by_filter"
    bl_label = "Mute by Filter"
    bl_description = "Mute NLA strips matching current filter file"

    def execute(self, context: Context):
        return _run_filter_mute_unmute(self, context, mute_value=True)


class MTAR_OT_DebugUnmuteNLAByFilterFile(Operator):
    bl_idname = "mtar.debug_unmute_nla_by_filter"
    bl_label = "Unmute by Filter"
    bl_description = "Unmute NLA strips matching current filter file"

    def execute(self, context: Context):
        return _run_filter_mute_unmute(self, context, mute_value=False)


class MTAR_OT_DebugSelectNLAByFilterFile(Operator):
    bl_idname = "mtar.debug_select_nla_by_filter"
    bl_label = "Select by Filter"
    bl_description = "Select NLA strips matching current filter file"

    def execute(self, context: Context):
        if not context.active_object or context.active_object.type != 'ARMATURE':
            Debug.report_and_log(self, 'ERROR', 'Active object is not an armature')
            return {'FINISHED'}

        armature = context.active_object
        nla = getattr(armature.animation_data, 'nla_tracks', None) if armature.animation_data else None
        if not nla:
            Debug.report_and_log(self, 'ERROR', 'No NLA tracks found on active armature')
            return {'FINISHED'}

        result, error = _collect_nla_matches(context)
        if error:
            Debug.report_and_log(self, 'WARNING', error)
            return {'FINISHED'}
        
        if len(result) > 0:
            for track in nla:
                track.select = False
                for strip in track.strips:
                    strip.select = False

        selected_count = 0
        for info in result:
            track = info.get('track')
            strip = info['strip']
            strip.select = True
            if track is not None:
                track.select = True
            selected_count += 1

        if selected_count:
            Debug.report_and_log(self, 'INFO', f"Selected {selected_count} matching NLA strip(s)")
        else:
            Debug.report_and_log(self, 'WARNING', 'No matching NLA strips found')

        return {'FINISHED'}
