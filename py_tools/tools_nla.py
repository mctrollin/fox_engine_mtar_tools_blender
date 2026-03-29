"""NLA debug helper functions used by frontend operator wrappers."""
import os
import re

import bpy

from ..py_core.core_logging import Debug
from ..py_fox.fox_mtar_constants import TABL_PATH
from ..py_utilities import util_filtering, util_hashing

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


def _parse_clipboard_index_lines(clipboard_text: str, index_mode: str):
    """Parse clipboard lines into index entries with header/data mode."""
    parsed = []
    invalid = []

    for line in (clipboard_text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue

        if raw.lower().startswith('h') and raw[1:].strip().isdigit():
            parsed.append(('HEADER', int(raw[1:].strip())))
            continue
        if raw.lower().startswith('d') and raw[1:].strip().isdigit():
            parsed.append(('DATA', int(raw[1:].strip())))
            continue

        if raw.isdigit():
            if index_mode == 'HEADER':
                parsed.append(('HEADER', int(raw)))
            elif index_mode == 'DATA':
                parsed.append(('DATA', int(raw)))
            else:
                parsed.append(('AUTO', int(raw)))
            continue

        invalid.append(raw)

    return parsed, invalid


def resolve_clipboard_index_sets(context):
    props = context.scene.mtar_debug_nla_properties
    clipboard_text = context.window_manager.clipboard or ""
    entries, invalid_lines = _parse_clipboard_index_lines(clipboard_text, props.debug_clipboard_index_mode)
    if invalid_lines:
        Debug.log(f"Ignored invalid clipboard lines: {invalid_lines}")

    header_set = set()
    data_set = set()
    for mode, value in entries:
        if mode == 'HEADER':
            header_set.add(value)
        elif mode == 'DATA':
            data_set.add(value)
        else:
            header_set.add(value)
            data_set.add(value)

    return header_set, data_set


def resolve_csv_index_sets(context):
    props = context.scene.mtar_debug_nla_properties
    csv_text = props.debug_nla_csv_input or ""
    if not csv_text.strip():
        return set(), set()
    normalized = re.sub(r'[;,]+', '\n', csv_text.strip())
    return _resolve_index_sets_from_text(normalized, props.debug_clipboard_index_mode)


def _resolve_index_sets_from_text(text: str, index_mode: str):
    header_set = set()
    data_set = set()

    entries, invalid_lines = _parse_clipboard_index_lines(text, index_mode)
    if invalid_lines:
        Debug.log(f"Ignored invalid index tokens: {invalid_lines}")

    for mode, value in entries:
        if mode == 'HEADER':
            header_set.add(value)
        elif mode == 'DATA':
            data_set.add(value)
        else:
            header_set.add(value)
            data_set.add(value)

    return header_set, data_set


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
    props = context.scene.mtar_debug_nla_properties
    mode = props.debug_nla_input_mode

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
        text_blob = (props.debug_nla_csv_input or '').strip()
    else:
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


def collect_nla_path_clipboard(context):
    header_set, data_set = resolve_clipboard_index_sets(context)
    if not header_set and not data_set:
        Debug.report_and_log(None, 'WARNING', "No valid index found in clipboard")
        return {'FINISHED'}

    armature = context.active_object
    if not armature or armature.type != 'ARMATURE':
        Debug.report_and_log(None, 'ERROR', "Active object is not an armature")
        return {'FINISHED'}

    nla = getattr(armature.animation_data, 'nla_tracks', None) if armature.animation_data else None
    if not nla:
        Debug.report_and_log(None, 'ERROR', "No NLA tracks found on active armature")
        return {'FINISHED'}

    clipboard_text = context.window_manager.clipboard or ""
    lines = [line.strip() for line in clipboard_text.splitlines() if line.strip()]
    if not lines:
        Debug.report_and_log(None, 'INFO', "Clipboard is empty")
        context.window_manager.clipboard = ""
        return {'FINISHED'}

    found_paths = []
    found_set = set()
    matched_any = False

    for track in nla:
        for strip in track.strips:
            action = strip.action
            if not action:
                continue

            src_name = strip.name or action.name
            m = _PATH_H_D_RE.search(src_name)
            if not m and action.name:
                m = _PATH_H_D_RE.search(action.name)
            if not m:
                continue

            h_idx = int(m.group('h'))
            d_idx = int(m.group('d'))
            matches_header = h_idx in header_set
            matches_data = d_idx in data_set
            if not (matches_header or matches_data):
                continue

            matched_any = True
            path_val = None
            if TABL_PATH in action.keys():
                path_val = str(action[TABL_PATH]).strip()

            if path_val and path_val not in found_set:
                found_set.add(path_val)
                found_paths.append(path_val)

    if not matched_any:
        Debug.log(f"No matching indices found for headers {sorted(header_set)} or data {sorted(data_set)}")

    context.window_manager.clipboard = "\n".join(found_paths)
    Debug.report_and_log(None, 'INFO', f"Collected {len(found_paths)} unique Path values")
    return {'FINISHED'}


def select_nla_by_filter(context):
    header_set, data_set = resolve_clipboard_index_sets(context)
    if not header_set and not data_set:
        Debug.report_and_log(None, 'WARNING', "No valid index found in clipboard")
        return {'FINISHED'}

    armature = context.active_object
    if not armature or armature.type != 'ARMATURE':
        Debug.report_and_log(None, 'ERROR', "Active object is not an armature")
        return {'FINISHED'}

    if not armature.animation_data or not getattr(armature.animation_data, 'nla_tracks', None):
        Debug.report_and_log(None, 'ERROR', "Active armature has no NLA tracks")
        return {'FINISHED'}

    # Deselect all strips/tracks first
    for track in armature.animation_data.nla_tracks:
        track.select = False
        for strip in track.strips:
            strip.select = False

    matched = 0
    for track, strip, h_idx, d_idx in _find_matching_strips(armature, header_set, data_set):
        track.select = True
        strip.select = True
        matched += 1

    if matched:
        Debug.report_and_log(None, 'INFO', f"Selected {matched} matching NLA strip(s)")
    else:
        first_idx = next(iter(header_set or data_set), None)
        Debug.report_and_log(None, 'WARNING', f"No NLA strip found for first index entry: {first_idx}")

    return {'FINISHED'}


def run_mute_unmute_nla_by_clipboard(context, mute_value=None, toggle=False):
    header_set, data_set = resolve_clipboard_index_sets(context)
    if not header_set and not data_set:
        Debug.report_and_log(None, 'WARNING', "No valid index found in clipboard")
        return {'FINISHED'}

    armature = context.active_object
    if not armature or armature.type != 'ARMATURE':
        Debug.report_and_log(None, 'ERROR', "Active object is not an armature")
        return {'FINISHED'}

    if not armature.animation_data or not getattr(armature.animation_data, 'nla_tracks', None):
        Debug.report_and_log(None, 'ERROR', "Active armature has no NLA tracks")
        return {'FINISHED'}

    matched = 0
    for _, strip, _, _ in _find_matching_strips(armature, header_set, data_set):
        if toggle:
            strip.mute = not strip.mute
        elif mute_value is not None:
            strip.mute = mute_value
        matched += 1

    action_name = 'Toggled' if toggle else ('Muted' if mute_value else 'Unmuted')
    level = 'INFO' if matched else 'WARNING'
    Debug.report_and_log(None, level, f"{action_name} {matched} matching NLA strip(s)")
    return {'FINISHED'}


def collect_nla_by_filter(context, output_type='PATH'):
    result, error = _collect_nla_matches(context)
    if error:
        Debug.report_and_log(None, 'WARNING', error)
        return {'FINISHED'}

    values = []
    for item in result:
        if output_type == 'PATH' and item.get('path'):
            values.append(item['path'])
        elif output_type == 'H' and item.get('h') is not None:
            values.append(f"h{item['h']}")
        elif output_type == 'D' and item.get('d') is not None:
            values.append(f"d{item['d']}")

    seen = set()
    unique = []
    for v in values:
        if v not in seen:
            seen.add(v)
            unique.append(v)

    context.window_manager.clipboard = "\n".join(unique)
    if unique:
        Debug.report_and_log(None, 'INFO', f"Copied {len(unique)} items ({output_type}) to clipboard")
    else:
        Debug.report_and_log(None, 'WARNING', "No matching values to copy to clipboard")

    return {'FINISHED'}


def set_mute_by_filter(context, mute_value=None):
    result, error = _collect_nla_matches(context)
    if error:
        Debug.report_and_log(None, 'WARNING', error)
        return {'FINISHED'}

    matched = 0
    for info in result:
        strip = info.get('strip')
        if not strip:
            continue

        if mute_value is not None:
            strip.mute = mute_value
        matched += 1

    action_name = 'Muted' if mute_value else 'Unmuted'
    level = 'INFO' if matched else 'WARNING'
    Debug.report_and_log(None, level, f"{action_name} {matched} matching NLA strip(s)")
    return {'FINISHED'}


def set_all_nla_mute(context, mute_value):
    armature = context.active_object
    if not armature or armature.type != 'ARMATURE':
        Debug.report_and_log(None, 'ERROR', "Active object is not an armature")
        return {'FINISHED'}

    if not armature.animation_data or not getattr(armature.animation_data, 'nla_tracks', None):
        Debug.report_and_log(None, 'ERROR', "Active armature has no NLA tracks")
        return {'FINISHED'}

    count = 0
    for track in armature.animation_data.nla_tracks:
        for strip in track.strips:
            strip.mute = mute_value
            count += 1

    Debug.report_and_log(None, 'INFO', f"{'Muted' if mute_value else 'Unmuted'} {count} NLA strip(s)")
    return {'FINISHED'}