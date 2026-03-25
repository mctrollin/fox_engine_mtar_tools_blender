"""
NLA tracks related debug operators for MTAR tools (filter-file helpers and NLA control).
"""

# pyright: reportInvalidTypeForm=false

import re
import os
import bpy
from bpy.types import Operator, Context

from .py_core.core_logging import Debug

from .py_fox.fox_mtar_constants import TABL_PATH

from .py_utilities import util_filtering, util_hashing


class MTAR_PG_DebugNLAProperties(bpy.types.PropertyGroup):
    """Property group for NLA-related debug settings."""

    debug_nla_input_mode: bpy.props.EnumProperty(
        name="Input Source",
        description="Choose whether to use the system clipboard, filter file, or CSV string source for NLA debug operations",
        items=[
            ('CLIPBOARD', "Clipboard", "Use the Blender system clipboard input"),
            ('FILTER_FILE', "Filter File", "Use the configured GANI filter file"),
            ('CSV', "CSV String", "Use the custom comma-separated values in debug CSV field"),
        ],
        default='FILTER_FILE'
    )

    debug_nla_csv_input: bpy.props.StringProperty(
        name="CSV Input",
        description="Comma-separated hN/dN entries or raw indices used for NLA debug operations when CSV mode is selected",
        default="",
        maxlen=4096,
    )

    debug_clipboard_index_mode: bpy.props.EnumProperty(
        name="Clipboard Mode",
        description="Interpret clipboard indices as header, data or auto mode",
        items=[
            ('HEADER', 'Header (hN)', 'Prefer hN values'),
            ('DATA', 'Data (dN)', 'Prefer dN values'),
            ('AUTO', 'Auto', 'Use both hN and dN for pure digits'),
        ],
        default='AUTO'
    )


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


def _resolve_clipboard_index_sets(context):
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


class MTAR_OT_DebugCollectNLAPathClipboard(Operator):
    """Collect action Path metadata from named verbose NLA indices in clipboard."""
    bl_idname = "mtar.debug_collect_nla_path_clipboard"
    bl_label = "Collect NLA Path from Clipboard"
    bl_description = "Read hN/dN list from clipboard, collect matching NLA action Path values, write results to clipboard"

    def execute(self, context: Context) -> set:
        props = context.scene.mtar_debug_nla_properties
        index_mode = props.debug_clipboard_index_mode

        clipboard_text = context.window_manager.clipboard or ""
        lines = [line.strip() for line in clipboard_text.splitlines() if line.strip()]

        if not lines:
            Debug.report_and_log(self, 'INFO', "Clipboard is empty")
            context.window_manager.clipboard = ""
            return {'FINISHED'}

        requested_header_indices = set()
        requested_data_indices = set()
        invalid_lines = []

        for line in lines:
            raw = line
            mode = None
            value = None

            if raw.lower().startswith('h'):
                mode = 'HEADER'
                value = raw[1:]
            elif raw.lower().startswith('d'):
                mode = 'DATA'
                value = raw[1:]
            else:
                value = raw
                if index_mode in ('HEADER', 'DATA'):
                    mode = index_mode
                else:
                    mode = 'AUTO'

            try:
                n = int(value)
            except Exception:
                invalid_lines.append(raw)
                continue

            if mode == 'HEADER':
                requested_header_indices.add(n)
            elif mode == 'DATA':
                requested_data_indices.add(n)
            else:  # AUTO
                requested_header_indices.add(n)
                requested_data_indices.add(n)

        found_paths = []
        found_set = set()
        matched_any = False

        armature = context.active_object
        if not armature or armature.type != 'ARMATURE':
            Debug.report_and_log(self, 'ERROR', "Active object is not an armature")
            return {'FINISHED'}

        nla = getattr(armature.animation_data, 'nla_tracks', None) if armature.animation_data else None
        if not nla:
            Debug.report_and_log(self, 'ERROR', "No NLA tracks found on active armature")
            return {'FINISHED'}

        path_re = re.compile(r"(?:^|\.)h(?P<h>\d+)_d(?P<d>\d+)(?:\.|$)")

        for track in nla:
            for strip in track.strips:
                action = strip.action
                if not action:
                    continue

                src_name = strip.name or action.name
                m = path_re.search(src_name)
                if not m and action.name:
                    m = path_re.search(action.name)

                if not m:
                    continue

                h_idx = int(m.group('h'))
                d_idx = int(m.group('d'))

                matches_header = h_idx in requested_header_indices
                matches_data = d_idx in requested_data_indices

                if not (matches_header or matches_data):
                    continue

                matched_any = True
                path_val = None
                if TABL_PATH in action.keys():
                    path_val = str(action[TABL_PATH]).strip()

                if path_val:
                    if path_val not in found_set:
                        found_set.add(path_val)
                        found_paths.append(path_val)

        if invalid_lines:
            Debug.log(f"Ignored non-int lines from clipboard: {invalid_lines}")

        if not matched_any:
            Debug.log(f"No matching indices found for headers {sorted(requested_header_indices)} or data {sorted(requested_data_indices)}")

        output_text = "\n".join(found_paths)
        context.window_manager.clipboard = output_text

        Debug.report_and_log(self, 'INFO', f"Collected {len(found_paths)} unique Path values")

        return {'FINISHED'}


class MTAR_OT_DebugSelectNLAByClipboardIndex(Operator):
    """Select first matching NLA strip by first parsed index from clipboard."""
    bl_idname = "mtar.debug_select_nla_by_clipboard_index"
    bl_label = "Select NLA Strip by Clipboard Index"
    bl_description = "Read first index from clipboard and select matching verbose hN_dN NLA strip"

    def execute(self, context: Context) -> set:
        header_set, data_set = _resolve_clipboard_index_sets(context)

        if not header_set and not data_set:
            Debug.report_and_log(self, 'WARNING', "No valid index found in clipboard")
            return {'FINISHED'}

        armature = context.active_object
        if not armature or armature.type != 'ARMATURE':
            Debug.report_and_log(self, 'ERROR', "Active object is not an armature")
            return {'FINISHED'}

        if not armature.animation_data or not getattr(armature.animation_data, 'nla_tracks', None):
            Debug.report_and_log(self, 'ERROR', "Active armature has no NLA tracks")
            return {'FINISHED'}

        for track in armature.animation_data.nla_tracks:
            track.select = False
            for strip in track.strips:
                strip.select = False

        selected = False
        selected_name = ""
        selected_h = None
        selected_d = None

        for track, strip, h_idx, d_idx in _find_matching_strips(armature, header_set, data_set):
            track.select = True
            strip.select = True
            selected = True
            selected_name = strip.name
            selected_h = h_idx
            selected_d = d_idx
            break

        if selected:
            Debug.report_and_log(self, 'INFO', f"Selected strip '{selected_name}' (h{selected_h}_d{selected_d})")
            return {'FINISHED'}

        Debug.report_and_log(self, 'WARNING', f"No NLA strip found for first index entry: {next(iter(header_set or data_set), None)}")
        return {'FINISHED'}


class _MTAR_OT_Debug_MuteUnmuteBase(Operator):
    """Base for clipboard-based mute/unmute/toggle debug operator."""

    def _run(self, context: Context, mute_value: bool = False, toggle: bool = False):
        header_set, data_set = _resolve_clipboard_index_sets(context)

        if not header_set and not data_set:
            Debug.report_and_log(self, 'WARNING', "No valid index found in clipboard")
            return {'FINISHED'}

        armature = context.active_object
        if not armature or armature.type != 'ARMATURE':
            Debug.report_and_log(self, 'ERROR', "Active object is not an armature")
            return {'FINISHED'}

        if not armature.animation_data or not getattr(armature.animation_data, 'nla_tracks', None):
            Debug.report_and_log(self, 'ERROR', "Active armature has no NLA tracks")
            return {'FINISHED'}

        matched = 0
        for _, strip, _, _ in _find_matching_strips(armature, header_set, data_set):
            strip.mute = not strip.mute if toggle else mute_value
            matched += 1

        if matched:
            action = 'Toggled' if toggle else ('Muted' if mute_value else 'Unmuted')
            Debug.report_and_log(self, 'INFO', f"{action} {matched} matched NLA strip(s)")
        else:
            Debug.report_and_log(self, 'WARNING', "No matching NLA strips found based on clipboard indices")

        return {'FINISHED'}


class MTAR_OT_DebugToggleMuteNLAByClipboardIndex(_MTAR_OT_Debug_MuteUnmuteBase):
    bl_idname = "mtar.debug_toggle_mute_nla_by_clipboard_index"
    bl_label = "Toggle Mute by Clipboard Index"
    bl_description = "Toggle mute on NLA strips matching clipboard header/data indices"

    def execute(self, context: Context):
        return self._run(context, toggle=True)


class MTAR_OT_DebugMuteNLAByClipboardIndex(_MTAR_OT_Debug_MuteUnmuteBase):
    bl_idname = "mtar.debug_mute_nla_by_clipboard_index"
    bl_label = "Mute by Clipboard Index"
    bl_description = "Mute NLA strips matching clipboard header/data indices"

    def execute(self, context: Context):
        return self._run(context, mute_value=True)


class MTAR_OT_DebugUnmuteNLAByClipboardIndex(_MTAR_OT_Debug_MuteUnmuteBase):
    bl_idname = "mtar.debug_unmute_nla_by_clipboard_index"
    bl_label = "Unmute by Clipboard Index"
    bl_description = "Unmute NLA strips matching clipboard header/data indices"

    def execute(self, context: Context):
        return self._run(context, mute_value=False)



def _resolve_csv_index_sets(context):
    props = context.scene.mtar_debug_nla_properties
    csv_text = props.debug_nla_csv_input or ""
    if not csv_text.strip():
        return set(), set()
    normalized = re.sub(r'[;,]+', '\n', csv_text.strip())
    return _resolve_index_sets_from_text(normalized, props.debug_clipboard_index_mode)


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


class MTAR_OT_DebugMuteAllNLA(Operator):
    bl_idname = "mtar.debug_mute_all_nla"
    bl_label = "Mute All NLA"
    bl_description = "Mute all NLA strips in the active armature"

    def execute(self, context: Context):
        armature = context.active_object
        if not armature or armature.type != 'ARMATURE':
            Debug.report_and_log(self, 'ERROR', "Active object is not an armature")
            return {'FINISHED'}

        if not armature.animation_data or not getattr(armature.animation_data, 'nla_tracks', None):
            Debug.report_and_log(self, 'ERROR', "Active armature has no NLA tracks")
            return {'FINISHED'}

        count = 0
        for track in armature.animation_data.nla_tracks:
            for strip in track.strips:
                strip.mute = True
                count += 1

        Debug.report_and_log(self, 'INFO', f"Muted {count} NLA strip(s)")
        return {'FINISHED'}


class MTAR_OT_DebugUnmuteAllNLA(Operator):
    bl_idname = "mtar.debug_unmute_all_nla"
    bl_label = "Unmute All NLA"
    bl_description = "Unmute all NLA strips in the active armature"

    def execute(self, context: Context):
        armature = context.active_object
        if not armature or armature.type != 'ARMATURE':
            Debug.report_and_log(self, 'ERROR', "Active object is not an armature")
            return {'FINISHED'}

        if not armature.animation_data or not getattr(armature.animation_data, 'nla_tracks', None):
            Debug.report_and_log(self, 'ERROR', "Active armature has no NLA tracks")
            return {'FINISHED'}

        count = 0
        for track in armature.animation_data.nla_tracks:
            for strip in track.strips:
                strip.mute = False
                count += 1

        Debug.report_and_log(self, 'INFO', f"Unmuted {count} NLA strip(s)")
        return {'FINISHED'}
