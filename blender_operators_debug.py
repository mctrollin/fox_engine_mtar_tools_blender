"""
Debug operators for MTAR tools - NLA control and bake utilities.

This module contains operator classes for NLA strip management (mute/unmute/select)
and bake operations.
Transform inspection operators are in blender_operators_debug_transform.py.
Hash utilities are in blender_operators_debug_hash.py.
"""

# pyright: reportInvalidTypeForm=false

from typing import Dict, Optional, Set
import os
import re

import bpy
from bpy.types import Operator, Context
from bpy.props import StringProperty

from .py_core.core_logging import Debug

from .blender_properties import get_effective_import_bake_decimate_error

from .py_utilities import util_blender_animation, util_fcurve_processing

from .py_fox.fox_mtar_constants import TABL_PATH
from .py_foxwrap import fwrap_metadata
from .blender_operators_debug_misc import _find_matching_strips

# Shared regex for verbose h/d naming in strip/action names
_PATH_H_D_RE = re.compile(r"(?:^|\.)h(?P<h>\d+)_d(?P<d>\d+)(?:\.|$)")


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


def _resolve_clipboard_index_sets(context):
    props = context.scene.mtar_debug_transform_properties
    clipboard_text = context.window_manager.clipboard or ""
    return _resolve_index_sets_from_text(clipboard_text, props.debug_clipboard_index_mode)


def _resolve_csv_index_sets(context):
    props = context.scene.mtar_debug_transform_properties
    csv_text = props.debug_misc_csv_input or ""
    if not csv_text.strip():
        return set(), set()
    normalized = re.sub(r'[;,]+', '\n', csv_text.strip())
    return _resolve_index_sets_from_text(normalized, props.debug_clipboard_index_mode)





# Import bake helpers from tools module (keep top-level to prevent import loops)
from .py_tools import tools_animation_bake


class MTAR_OT_DebugCollectNLAPathClipboard(Operator):
    """Collect action Path metadata from named verbose NLA indices in clipboard."""
    bl_idname = "mtar.debug_collect_nla_path_clipboard"
    bl_label = "Collect NLA Path from Clipboard"
    bl_description = "Read hN/dN list from clipboard, collect matching NLA action Path values, write results to clipboard"

    def execute(self, context: Context) -> set:
        props = context.scene.mtar_debug_transform_properties
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

                # find h/d in strip name first, fallback to action name
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


def _resolve_clipboard_index_sets(context):
    """Resolve header/data index sets from clipboard content."""
    props = context.scene.mtar_debug_transform_properties
    index_mode = props.debug_clipboard_index_mode

    clipboard_text = context.window_manager.clipboard or ""
    entries, invalid_lines = _parse_clipboard_index_lines(clipboard_text, index_mode)
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
        selected_path = None
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
            selected_path = strip.action[TABL_PATH] if strip.action and TABL_PATH in strip.action.keys() else None
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


# Bake Debug Panel Operators ##################################################################

class MTAR_OT_DebugRunBake(Operator):
    """Run synchronous bake using selected debug armature and optional imported armature."""
    bl_idname = "mtar.debug_run_bake"
    bl_label = "Debug: Run Bake"
    bl_description = "Run the existing synchronous bake operation for the selected armature"

    def execute(self, context: Context) -> set:
        props = context.scene.mtar_debug_transform_properties

        if not props.debug_armature:
            Debug.report_and_log(self, 'ERROR', "No target armature selected to bake into")
            return {'CANCELLED'}

        target_armature = props.debug_armature
        source_arm = props.debug_source_armature
        idx = props.debug_bake_gani_index
        prepare_only = props.debug_prepare_only

        try:
            Debug.update_progress(75, "Baking (debug)...")

            if target_armature.animation_data and target_armature.animation_data.nla_tracks:
                # Gather strips
                strips_by_index = {}
                strips_list = []
                for track in target_armature.animation_data.nla_tracks:
                    for strip in track.strips:
                        if not strip.action:
                            continue
                        m = re.search(r"_(\d{3})\b", strip.name)
                        if m:
                            stripped_idx = int(m.group(1))
                            strips_by_index.setdefault(stripped_idx, []).append((track, strip, strip.action))
                        strips_list.append((track, strip, strip.action))

                # Determine target strips
                target_strips = []
                if idx == -1:
                    # full bake or prepare all
                    if prepare_only:
                        # Prepare all: mute source NLA tracks and leave scene for inspection
                        if source_arm and source_arm.animation_data and source_arm.animation_data.nla_tracks:
                            for t in source_arm.animation_data.nla_tracks:
                                t.mute = True
                            Debug.report_and_log(self, 'INFO', "Prepared scene: muted all source NLA tracks for inspection (no bake performed)")
                            return {'FINISHED'}
                        else:
                            Debug.report_and_log(self, 'WARNING', "No source armature NLA tracks to mute for prepare")
                            return {'CANCELLED'}
                    else:
                        # Full bake using unified utility (constraint-bake + optional fcurve decimation)
                        Debug.log("Debug: Baking NLA strips on target armature")
                        bake_result = tools_animation_bake.bake_constraints_and_decimate_fcurves(
                            rig_armature=target_armature,
                            source_armature=source_arm,
                            create_new_action=True,
                            new_action_suffix="_baked",
                            remove_constraints=True,
                            delete_import_armature=False,
                            bake_decimate_fcurve_error=0.01,  # Debug UI does not apply fcurve decimation by default
                            decimate_skip_types='',
                            layout_action=None,
                        )

                        if bake_result.get('success'):
                            Debug.report_and_log(self, 'INFO', f"Debug bake completed: {bake_result.get('message')} (post-processed: decimated={bake_result.get('fcurves_decimated', 0)})")
                        else:
                            Debug.report_and_log(self, 'WARNING', f"Debug bake failed: {bake_result.get('message')}")

                        Debug.update_progress(100, "Bake complete")
                        return {'FINISHED'}
                else:
                    # Bake only specific index
                    if idx not in strips_by_index:
                        Debug.report_and_log(self, 'ERROR', f"No strip found for GANI index {idx}")
                        return {'CANCELLED'}

                    target_strips = [t for t in strips_by_index[idx] if not t[0].mute and not t[1].mute]
                    if not target_strips:
                        Debug.report_and_log(self, 'WARNING', "No eligible strips to bake for this index")
                        return {'CANCELLED'}

                # Process selected strips
                success_count = 0
                baked_actions = []
                for (track, strip, action) in target_strips:
                    if prepare_only:
                        # Mute source NLA tracks and assign action for inspection
                        if source_arm and source_arm != target_armature:
                            if not source_arm.animation_data:
                                source_arm.animation_data_create()
                            if source_arm.animation_data.nla_tracks:
                                for t in source_arm.animation_data.nla_tracks:
                                    t.mute = True
                            util_blender_animation.assign_action_to_datablock(source_arm, action)
                            Debug.log(f"Prepared strip '{strip.name}': muted source NLA and assigned action '{action.name}'")
                            # Mute the target track and assign action so active action previews
                            track.mute = True
                            util_blender_animation.assign_action_to_datablock(target_armature, action)
                            Debug.report_and_log(self, 'INFO', f"Prepared scene for strip '{strip.name}' (no bake performed)")
                        continue

                    # Not prepare-only: perform legacy bake per strip (mute source tracks and assign)
                    original_source_action = None
                    original_source_nla_mute_states = None
                    try:
                        if source_arm and source_arm != target_armature:
                            if not source_arm.animation_data:
                                source_arm.animation_data_create()
                            original_source_action = source_arm.animation_data.action
                            if source_arm.animation_data.nla_tracks:
                                original_source_nla_mute_states = {t.name: t.mute for t in source_arm.animation_data.nla_tracks}
                                for t in source_arm.animation_data.nla_tracks:
                                    t.mute = True
                                Debug.log(f"  Muted {len(source_arm.animation_data.nla_tracks)} source NLA tracks for legacy bake")
                            util_blender_animation.assign_action_to_datablock(source_arm, action)

                        bake_result = tools_animation_bake.bake_armature_constraints_to_keyframes(
                            rig_armature=target_armature,
                            action=action,
                            remove_constraints=False,
                            create_new_action=True,
                            new_action_suffix="_baked",
                            nla_track=track,
                            source_armature=source_arm
                        )

                        if bake_result.get('success'):
                            strip.action = bake_result.get('action')
                            baked_actions.append(bake_result.get('action'))
                            success_count += 1

                            # Run post-bake decimation for this baked action using scene settings
                            try:
                                decimate_err = 0.0
                                decimate_skip_types = ''
                                if hasattr(context.scene, 'mtar_properties'):
                                    ip = getattr(context.scene.mtar_properties, 'import_props', None)
                                    if ip is not None:
                                        decimate_err = get_effective_import_bake_decimate_error(ip)
                                        decimate_skip_types = getattr(ip, 'import_bake_decimate_skip_types', '')

                                # Decimate via decimate_import_fcurves_to_bezier (operates on armature level)
                                if decimate_err > 0.0:
                                    layout_action = util_blender_animation.try_find_layout_track_action()
                                    blender_bone_skip_map = fwrap_metadata.build_blender_bone_decimation_skip_map(
                                        all_blender_bone_names=set(target_armature.data.bones.keys()) if target_armature and target_armature.data else set(),
                                        layout_action=layout_action,
                                        decimate_skip_types=decimate_skip_types,
                                        blender_to_fox_map=None,
                                        cache={},
                                    )
                                    dec_res = util_fcurve_processing.decimate_import_fcurves_to_bezier(
                                        armature=target_armature,
                                        bake_decimate_fcurve_error=decimate_err,
                                        decimate_skip_types=decimate_skip_types,
                                        layout_action=layout_action,
                                        blender_bone_skip_map=blender_bone_skip_map,
                                    )
                                    Debug.log(f"  Post-bake decimation: decimated={dec_res.get('fcurves_decimated', 0)}")
                            except Exception as opt_e:
                                Debug.log_warning(f"Post-bake optimize failed for '{strip.name}': {opt_e}")
                        else:
                            Debug.log_warning(f"Failed to bake strip '{strip.name}': {bake_result.get('message')}")

                    finally:
                        # Restore source armature state
                        try:
                            if source_arm and source_arm != target_armature and source_arm.animation_data:
                                if original_source_action:
                                    util_blender_animation.assign_action_to_datablock(source_arm, original_source_action)
                                else:
                                    util_blender_animation.remove_action_from_datablock(source_arm)
                                if original_source_nla_mute_states and source_arm.animation_data.nla_tracks:
                                    for tn, was in original_source_nla_mute_states.items():
                                        if tn in source_arm.animation_data.nla_tracks:
                                            source_arm.animation_data.nla_tracks[tn].mute = was
                                    Debug.log("  Restored source NLA mute states after legacy bake")
                        except Exception as restore_err:
                            Debug.log_warning(f"Error restoring source armature state after legacy bake: {restore_err}")

                # Wrap up per-strip baking
                if success_count > 0:
                    baked_bones = set()
                    for act in baked_actions:
                        baked_bones.update(tools_animation_bake.get_bones_with_keyframes(act))
                    if baked_bones:
                        removed = tools_animation_bake.remove_bone_constraints(target_armature, baked_bones)
                        Debug.log(f"Removed constraints from {len(baked_bones)} bones ({removed} constraints)")

                    if tools_animation_bake.clear_armature_transforms(target_armature):
                        Debug.report_and_log(self, 'INFO', f"Baked {success_count} strip(s) and cleared transforms")
                        return {'FINISHED'}
                    else:
                        Debug.report_and_log(self, 'WARNING', f"Baked {success_count} strip(s) but failed to clear transforms")
                        return {'FINISHED'}
                else:
                    Debug.report_and_log(self, 'WARNING', "No strips were successfully baked")
                    return {'CANCELLED'}

            elif target_armature.animation_data and target_armature.animation_data.action:
                Debug.log("Debug: Baking active action on target armature")
                bake_result = tools_animation_bake.bake_constraints_and_decimate_fcurves(
                    rig_armature=target_armature,
                    source_armature=source_arm,
                    create_new_action=True,
                    new_action_suffix="_baked",
                    remove_constraints=True,
                    bake_decimate_fcurve_error=0.0,
                    decimate_skip_types='',
                    layout_action=None,
                )

                if bake_result.get('success'):
                    Debug.report_and_log(self, 'INFO', f"Debug bake completed: {bake_result.get('message')} (post-processed: decimated={bake_result.get('fcurves_decimated', 0)})")
                else:
                    Debug.report_and_log(self, 'WARNING', f"Debug bake failed: {bake_result.get('message')}")

            else:
                Debug.report_and_log(self, 'WARNING', "Target armature has no NLA tracks or active action to bake")
                return {'CANCELLED'}

        except Exception as e:
            Debug.report_and_log(self, 'ERROR', f"Debug bake failed: {e}")
            return {'CANCELLED'}

        Debug.update_progress(100, "Bake complete")
        return {'FINISHED'}

class MTAR_OT_DebugSetupGraphContext(Operator):
    """Setup graph editor context for manual decimation testing."""
    bl_idname = "mtar.debug_setup_graph_context"
    bl_label = "Setup Graph Context"
    bl_description = "Setup graph editor with action and armature for manual operator testing"
    
    def execute(self, context: Context) -> set:
        """Execute the setup."""
        
        props = context.scene.mtar_debug_transform_properties
        
        # Validate inputs
        if not props.debug_armature:
            Debug.report_and_log(self, 'ERROR', "No target armature selected")
            return {'FINISHED'}
        
        # Get the action from the armature
        armature = props.debug_armature
        if not armature.animation_data or not armature.animation_data.action:
            Debug.report_and_log(self, 'ERROR', f"Armature '{armature.name}' has no active action")
            return {'FINISHED'}
        
        action = armature.animation_data.action
        
        # Call the debug setup function
        util_fcurve_processing.debug_setup_graph_context_for_manual_test(armature.name, action.name)
        
        Debug.report_and_log(self, 'INFO', f"Graph context setup complete for '{armature.name}' / '{action.name}'")
        Debug.report_and_log(self, 'INFO', "Check console for diagnostics. Try: bpy.ops.graph.decimate(mode='ERROR', error=0.01)")
        
        return {'FINISHED'}

