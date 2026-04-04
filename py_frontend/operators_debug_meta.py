"""Metadata debug operators for MTAR tools."""

# pyright: reportInvalidTypeForm=false

import os
import re

import bpy
from bpy.types import Operator, Context

from ..py_core.core_logging import Debug

from ..py_foxwrap.fwrap_mtar_import_types import GaniImportData
from ..py_foxwrap.fwrap_mtar_reader import MtarReader
from ..py_foxwrap.fwrap_gani_motionevent import store_motion_events_on_action, clear_motion_events_from_action
from ..py_foxwrap import fwrap_metadata


# Regex to extract h/d indices from NLA strip / action names.
# Same pattern as py_tools/tools_nla._PATH_H_D_RE.
_STRIP_HD_RE = re.compile(r"(?:^|\.)h(?P<h>\d+)_d(?P<d>\d+)(?:\.|$)")


def _get_h_idx_from_strip(strip: bpy.types.NlaStrip):
    """Extract the GANI header index (h) from a strip or its action name.

    This is the zero-based MTAR file-table position required by
    ``read_selected_ganis()``.  Do not confuse with the d (data/path) index.

    Returns:
        int header index, or None if not found
    """
    name = strip.name or ''
    if not name and strip.action:
        name = strip.action.name or ''
    m = _STRIP_HD_RE.search(name)
    if not m and strip.action and strip.action.name:
        m = _STRIP_HD_RE.search(strip.action.name)
    if m:
        return int(m.group('h'))
    return None


def _parse_track_property_key(key: str) -> int | None:
    if not key.startswith(fwrap_metadata.TRACK_PROP_PREFIX):
        return None
    suffix = key[len(fwrap_metadata.TRACK_PROP_PREFIX):]
    idx_str = suffix.split('_', 1)[0]
    if idx_str.isdigit():
        return int(idx_str)
    return None


class MTAR_OT_UpgradeEventsFromMtar(Operator):
    """Re-read motion events from the source MTAR file and rewrite event markers and
    custom properties on all NLA strip actions of the selected armature using the
    current naming scheme.  Each strip's GANI data index (dN in the strip name) is
    used to locate the matching GANI inside the MTAR."""
    bl_idname = "mtar.upgrade_events_from_mtar"
    bl_label = "Re-sync Events from MTAR"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context: Context):
        armature = context.active_object
        if not armature or armature.type != 'ARMATURE':
            self.report({'ERROR'}, "Active object must be an armature")
            return {'CANCELLED'}

        props = context.scene.mtar_properties
        mtar_filepath = bpy.path.abspath(props.import_props.mtar_filepath or '')
        if not mtar_filepath or not os.path.exists(mtar_filepath):
            self.report({'ERROR'}, "No valid MTAR file path set in the Import panel")
            return {'CANCELLED'}

        anim_data = armature.animation_data
        if not anim_data or not getattr(anim_data, 'nla_tracks', None):
            self.report({'ERROR'}, "Armature has no NLA tracks")
            return {'CANCELLED'}

        # Collect (h_idx, action) pairs — h is the zero-based MTAR file-table index
        strip_pairs = []
        for track in anim_data.nla_tracks:
            for strip in track.strips:
                if not strip.action:
                    continue
                h_idx = _get_h_idx_from_strip(strip)
                if h_idx is None:
                    Debug.log_warning(
                        f"Events upgrade: strip '{strip.name}' has no recognizable GANI header index (hN) — skipped"
                    )
                    continue
                strip_pairs.append((h_idx, strip.action))

        if not strip_pairs:
            self.report({'WARNING'}, "No MTAR-linked NLA strips found on armature (need hN_dN in strip/action name)")
            return {'CANCELLED'}

        # Read all required GANI indices from the MTAR in a single pass
        gani_indices = sorted({h for h, _ in strip_pairs})
        Debug.log(f"Events upgrade: reading {len(gani_indices)} GANI(s) from '{os.path.basename(mtar_filepath)}'")
        try:
            reader = MtarReader(mtar_filepath)
            gani_data_map = reader.read_selected_ganis(gani_indices)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to read MTAR: {e}")
            return {'CANCELLED'}

        updated = 0
        skipped = 0
        for h_idx, action in strip_pairs:
            gani_data: GaniImportData = gani_data_map.get(h_idx)
            if gani_data is None:
                Debug.log_warning(
                    f"Events upgrade: no GANI data for h_idx={h_idx} in '{os.path.basename(mtar_filepath)}'"
                )
                skipped += 1
                continue
            clear_motion_events_from_action(action)
            if gani_data.gani_events is not None:
                store_motion_events_on_action(action, gani_data.gani_events)
            updated += 1

        msg = f"Re-synced events: {updated} action(s) updated"
        if skipped:
            msg += f", {skipped} skipped (no matching GANI in file)"
        self.report({'INFO'}, msg)
        Debug.log(msg)
        return {'FINISHED'}


class MTAR_OT_RenameTrackPropertyKeys(Operator):
    """Rename legacy track metadata property keys on the selected armature."""
    bl_idname = "mtar.rename_track_property_keys"
    bl_label = "Rename Track Property Keys"
    bl_description = "Rename legacy track_NNN_<name> metadata keys to canonical track_NNN keys on the selected armature"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context: Context):
        armature = context.active_object
        if not armature or armature.type != 'ARMATURE':
            self.report({'ERROR'}, "Active object must be an armature")
            return {'CANCELLED'}

        anim_data = armature.animation_data
        if not anim_data:
            self.report({'WARNING'}, "Selected armature has no animation data")
            return {'CANCELLED'}

        actions = []
        seen_names = set()

        if getattr(anim_data, 'action', None):
            action = anim_data.action
            if action and action.name not in seen_names:
                seen_names.add(action.name)
                actions.append(action)

        for track in getattr(anim_data, 'nla_tracks', ()):
            for strip in getattr(track, 'strips', ()):
                action = getattr(strip, 'action', None)
                if action and action.name not in seen_names:
                    seen_names.add(action.name)
                    actions.append(action)

        renamed = 0
        skipped = 0
        for action in actions:
            original_keys = [key for key in action.keys() if key.startswith(fwrap_metadata.TRACK_PROP_PREFIX)]
            for key in original_keys:
                track_idx = _parse_track_property_key(key)
                if track_idx is None:
                    continue
                new_key = f"{fwrap_metadata.TRACK_PROP_PREFIX}{track_idx:03d}"
                if new_key == key:
                    continue
                if new_key in action.keys():
                    skipped += 1
                    continue
                action[new_key] = action[key]
                del action[key]
                renamed += 1

        if renamed == 0 and skipped == 0:
            self.report({'INFO'}, "No legacy track metadata keys found to rename")
        else:
            msg = f"Renamed {renamed} track metadata key(s)"
            if skipped:
                msg += f", skipped {skipped} keys because canonical key already exists"
            self.report({'INFO'}, msg)
        return {'FINISHED'}
