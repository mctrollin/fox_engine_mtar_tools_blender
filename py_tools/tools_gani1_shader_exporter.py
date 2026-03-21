"""Shader-node-specific Blender export utilities (old-format GANI only).

This module contains the helper functions that collect shader node animation
data from a Blender armature for export back to old-format MTAR files.

Bone naming convention (set by importer):
 - Property bones: ``"TENSION_CHEEKL"`` (the shader property name)
 - Unit bones:     ``"TENSION_CHEEKL.3462891356"`` (property.decimal_hash)

For export:
 1. :func:`collect_shader_nodes_actions` — gather NLA / active actions from
    the shader-nodes armature.
 2. :func:`build_shader_nodes_metadata_dict` — derive per-bone segment types
    and bit-size metadata for :func:`~py_tools.tools_mtar_exporter.export_gani_tracks_from_action`.
 3. :func:`group_shader_tracks_by_property` — after track export, group the
    resulting :class:`~py_foxwrap.foxwrap_misc.TrackUnitWrapper` objects by
    their parent property and strip the property prefix from unit names so the
    binary writer receives bare unit hash strings.

Dependency chain (no circularity):
    tools_mtar_exporter
        → tools_gani_shader_exporter   (this module)
            → py_foxwrap / py_fox / py_utilities
"""

from typing import List, Dict, Optional, Tuple

import bpy

from ..py_core.core_logging import Debug

from ..py_fox import fox_gani_constants as gani_const
from ..py_fox.fox_misc_types import StrCode32

from ..py_foxwrap.fwrap_metadata_types import TrackMetaData
from ..py_foxwrap.fwrap_misc_types import TrackUnitWrapper, TrackDataBlobWrapper
from ..py_foxwrap.fwrap_misc_export_types import ExportActionData
from ..py_foxwrap import fwrap_misc_export


# ---------------------------------------------------------------------------
# Action collection
# ---------------------------------------------------------------------------

def collect_shader_nodes_actions(
    shader_nodes_armature: bpy.types.Object,
    use_nla: bool,
    export_clean_threshold: float = 0.0,
) -> List[ExportActionData]:
    """Collect shader-node animation actions from *shader_nodes_armature*.

    Args:
        shader_nodes_armature:  Armature object containing shader-node bones.
        use_nla:                If ``True``, collect from NLA strips; if
                                ``False``, use the active action.
        export_clean_threshold: FCurve cleaning threshold (0 = disabled).

    Returns:
        List of :class:`ExportActionData` objects (may be empty).
    """
    return fwrap_misc_export.collect_armature_actions(
        shader_nodes_armature, use_nla,
        track_type_label="shader nodes",
        export_clean_threshold=export_clean_threshold,
    )


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def build_shader_nodes_metadata_dict(
    shader_nodes_armature: bpy.types.Object,
    action: bpy.types.Action,
) -> Dict[str, TrackMetaData]:
    """Build a per-bone metadata dictionary for shader unit tracks.

    Shader unit bones have no layout-track action, so segment types and bit
    sizes are derived from FCurves in *action* and stored metadata properties.

    Only *unit* bones (those with a parent bone) carry animation data; the
    *property* parent bones are grouping-only and are skipped.

    Args:
        shader_nodes_armature: Shader-nodes armature object.
        action:                The Blender action to inspect.

    Returns:
        ``{unit_bone_name: TrackMetaData}`` for every unit bone present in *action*.
    """

    def _shader_hash(bone_name: str, bone: bpy.types.Bone) -> int:
        parent_bone_name = bone.parent.name if bone.parent else bone_name
        prefix = parent_bone_name + "."
        unit_part = bone_name[len(prefix):] if bone_name.startswith(prefix) else bone_name
        try:
            return int(unit_part)
        except ValueError:
            return StrCode32.from_string(unit_part).to_int()

    return fwrap_misc_export.build_track_metadata_dict_from_fcurves(
        armature=shader_nodes_armature,
        action=action,
        armature_label="shader nodes",
        bone_skip_predicate=lambda b: b.parent is None,
        name_hash_extractor_fn=_shader_hash,
        warn_on_missing_metadata=False,
    )


# ---------------------------------------------------------------------------
# Post-export grouping
# ---------------------------------------------------------------------------

def group_shader_tracks_by_property(
    shader_tracks: List[TrackUnitWrapper],
    shader_nodes_armature: bpy.types.Object,
) -> Tuple[List[str], List[List[TrackUnitWrapper]]]:
    """Group exported unit tracks by their parent property bone.

    After :func:`~py_tools.tools_mtar_exporter.export_gani_tracks_from_action`
    returns a flat list of tracks (one per unit bone), this function:

    1. Groups them by their parent property bone.
    2. Strips the ``"{property_name}."`` prefix from each track's ``name`` so
       the binary writer receives the bare unit decimal hash (e.g. "3462891356")
       instead of the full Blender bone name.

    Args:
        shader_tracks:          Flat list of exported :class:`TrackUnitWrapper`
                                objects (one per unit bone).
        shader_nodes_armature:  Source armature used for parent-bone lookups.

    Returns:
        Parallel ``(property_names, property_tracks)`` lists.  Each element of
        ``property_tracks`` is the list of unit :class:`TrackUnitWrapper` objects
        belonging to the corresponding property.
    """
    if not shader_tracks:
        return [], []

    property_map: Dict[str, List[TrackUnitWrapper]] = {}
    property_order: List[str] = []

    for track in shader_tracks:
        unit_bone_name = track.name  # e.g. "TENSION_CHEEKL.3462891356"

        # Determine property name from armature bone hierarchy
        if (
            shader_nodes_armature
            and shader_nodes_armature.type == 'ARMATURE'
            and unit_bone_name in shader_nodes_armature.data.bones
        ):
            bone = shader_nodes_armature.data.bones[unit_bone_name]
            prop_name = bone.parent.name if bone.parent else unit_bone_name
        else:
            # Fallback: split at the last dot separator
            if '.' in unit_bone_name:
                prop_name = unit_bone_name.rsplit('.', 1)[0]
            else:
                prop_name = unit_bone_name
                Debug.log_warning(
                    f"  group_shader_tracks_by_property: bone '{unit_bone_name}' "
                    f"not found in armature and has no '.' separator — "
                    f"treating itself as its own property"
                )

        # Strip property prefix from the track name so the writer sees just
        # the unit decimal hash (e.g. "3462891356").
        prefix = prop_name + "."
        stripped_name = (
            unit_bone_name[len(prefix):]
            if unit_bone_name.startswith(prefix)
            else unit_bone_name
        )

        # Rebuild TrackUnitWrapper with the stripped name.
        # Each segment blob also carries a prefixed name (e.g.
        # "TENSION_CHEEKL.3462891356_1" for segment index 1); strip the
        # same prefix so the writer receives the bare hash ("3462891356_1").
        stripped_segs = [
            TrackDataBlobWrapper(
                name=seg.name[len(prefix):] if seg.name.startswith(prefix) else seg.name,
                segment_index=seg.segment_index,
                data_blob=seg.data_blob,
            )
            for seg in track.segments_track_data
        ]
        stripped_track = TrackUnitWrapper(
            name=stripped_name,
            segments_track_data=stripped_segs,
            unit_flags=track.unit_flags,
            rig_unit_type=track.rig_unit_type,
        )

        if prop_name not in property_map:
            property_map[prop_name] = []
            property_order.append(prop_name)
        property_map[prop_name].append(stripped_track)

    property_names = property_order
    property_tracks = [property_map[p] for p in property_order]

    Debug.log(
        f"  Grouped {len(shader_tracks)} shader unit track(s) into "
        f"{len(property_names)} property group(s): "
        + ", ".join(
            f"{p}({len(property_tracks[i])})"
            for i, p in enumerate(property_names)
        )
    )

    return property_names, property_tracks


# ---------------------------------------------------------------------------
# Per-property TrackHeader reading
# ---------------------------------------------------------------------------

def collect_shader_property_headers(
    action: bpy.types.Action,
    property_names: List[str],
) -> List[Optional[Dict[str, int]]]:
    """Read stored per-property shader ``TrackHeader`` fields from *action*.

    During import :func:`~py_tools.tools_gani_shader_importer._store_shader_property_header_on_action`
    stored each property's header as::

        action["shader_hdr_{prop_name}"] = "t_id=X ; unknown_a=Y ; unknown_b=Z ; frame_count=N ; frame_rate=W"

    This function parses those properties back and returns a parallel list
    of dicts (one per entry in *property_names*) for use in
    :class:`~py_foxwrap.foxwrap_misc_export.GaniExportShaderData`.

    Args:
        action:         Blender action carrying the per-property custom properties.
        property_names: Ordered property names as returned by
                        :func:`group_shader_tracks_by_property`.

    Returns:
        Parallel list; entries are ``None`` when no stored header is found for
        that property (the writer will fall back to GANI-level defaults).
    """
    if not action:
        return [None] * len(property_names)

    headers: List[Optional[Dict[str, int]]] = []
    for prop_name in property_names:
        key = f"{gani_const.SHADER_HDR_PREFIX}{prop_name}"
        raw = action.get(key)
        if raw is None:
            headers.append(None)
            continue

        hdr: Dict[str, int] = {}
        for part in str(raw).split(';'):
            part = part.strip()
            if '=' not in part:
                continue
            k, v = part.split('=', 1)
            try:
                hdr[k.strip()] = int(v.strip())
            except ValueError:
                pass
        headers.append(hdr if hdr else None)

    return headers