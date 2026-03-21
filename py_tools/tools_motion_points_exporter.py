"""Motion-point-specific Blender export utilities.

This module contains the three functions that read motion point data back from
a Blender armature for export:

* :func:`build_motion_points_list_from_armature` — bone hierarchy →
  :class:`~py_foxwrap.foxwrap_motionpoint.MotionPointWrapper`
* :func:`build_motion_point_metadata_dict` — per-bone segment/bit-size metadata
* :func:`collect_motion_point_actions` — gather NLA / active actions

These functions previously lived in ``tools_mtar_exporter`` but have been
extracted here to reduce that module's size and to centralise all
motion-point logic.
"""

from typing import List, Dict

import bpy

from ..py_core.core_logging import Debug

from ..py_utilities import util_blender_animation

from ..py_fox.fox_misc_types import StrCode32

from ..py_foxwrap.fwrap_metadata_types import TrackMetaData
from ..py_foxwrap.fwrap_misc_export_types import ExportActionData
from ..py_foxwrap.fwrap_motionpoint_types import MotionPointWrapper, MotionPointEntryWrapper
from ..py_foxwrap import fwrap_misc_export


def build_motion_points_list_from_armature(
    motion_points_armature: bpy.types.Object,
) -> MotionPointWrapper:
    """Build a :class:`MotionPointWrapper` from a motion-points armature.

    Extracts bone names and parent relationships from the armature to produce
    the motion-point definition list that is ultimately written to the MTAR
    CommonInfo section.

    Only bones that have animation data are exported as motion points.
    Parent-only bones (bones whose sole purpose is to anchor hierarchy) are
    excluded from the exported list but their hashes are still recorded as
    ``parent_hash`` values in child entries.

    .. note::
        Bone names in the armature are expected to be decimal-hash strings
        (e.g. ``"4036034414"``) because the importer stores them that way
        when the hash could not be resolved.  FC1 (human-readable bone names)
        is a deferred improvement; this function preserves the existing
        convention for now.

    Args:
        motion_points_armature: Armature object containing motion-point bones.

    Returns:
        :class:`MotionPointWrapper` (may be empty if the armature is invalid).
    """
    if not motion_points_armature or motion_points_armature.type != 'ARMATURE':
        return MotionPointWrapper()

    Debug.log(
        f"\nBuilding MotionPointWrapper from armature "
        f"'{motion_points_armature.name}'..."
    )

    # Identify which bones have animation data across all relevant NLA actions
    bones_with_animation: set = set()

    if motion_points_armature.animation_data:
        for nla_track in motion_points_armature.animation_data.nla_tracks:
            for strip in nla_track.strips:
                if strip.action and util_blender_animation.is_relevant_strip(strip):
                    for fcurve in util_blender_animation.iter_action_fcurves(strip.action):
                        bone_name = util_blender_animation.extract_bone_name_from_data_path(fcurve.data_path)
                        if bone_name:
                            bones_with_animation.add(bone_name)
                else:
                    if strip.action:
                        Debug.log(
                            f"  Skipping motion point strip "
                            f"'{getattr(strip, 'name', '<unknown>')}' "
                            f"(not a GANI strip)"
                        )

    if not bones_with_animation:
        Debug.log_warning(
            "  Warning: No bones with animation data found in motion points "
            "armature.  All bones in the armature will be exported."
        )
        bones_with_animation = {
            bone.name for bone in motion_points_armature.data.bones
        }

    entries: List[MotionPointEntryWrapper] = []
    parent_only_bones: List[str] = []

    for bone in motion_points_armature.data.bones:
        if bone.name not in bones_with_animation:
            parent_only_bones.append(bone.name)
            continue

        # Bone names are decimal hash strings (set by importer for GANI2 consistency)
        hash_value = int(bone.name)
        parent_hash = int(bone.parent.name) if bone.parent else 0
        parent_name = bone.parent.name if bone.parent else None

        if not bone.parent:
            Debug.log_warning(
                f"  Warning: Motion point bone '{bone.name}' has no parent; "
                f"writing empty parent hash"
            )

        entries.append(MotionPointEntryWrapper(
            hash_value=hash_value,
            name=bone.name,
            parent_hash=parent_hash,
            parent_name=parent_name,
        ))

        parent_str = f"→ {bone.parent.name}" if bone.parent else "(no parent)"
        Debug.log(
            f"  {bone.name} {parent_str} "
            f"(hash: {hash_value}, parent_hash: {parent_hash})"
        )

    if parent_only_bones:
        Debug.log(
            f"  Skipped {len(parent_only_bones)} parent-only bone(s): "
            + ", ".join(parent_only_bones)
        )

    wrapper = MotionPointWrapper(entries=entries)
    Debug.log(f"MotionPointWrapper built: {wrapper.count} point(s)")
    return wrapper


def build_motion_point_metadata_dict(
    motion_points_armature: bpy.types.Object,
    action: bpy.types.Action,
) -> Dict[str, TrackMetaData]:
    """Build a per-bone metadata dictionary for motion point tracks.

    Motion points have no layout-track action, so segment types and bit sizes
    are derived by inspecting FCurves in *action* and reading stored metadata
    properties.

    Args:
        motion_points_armature: Motion points armature object.
        action:                 The Blender action to inspect (required).

    Returns:
        ``{bone_name: TrackMetaData}`` for every bone present in *action*.
    """
    # Motion-point bone names are decimal hash strings; compute hash by parsing.
    def _mtp_hash(bone_name: str, bone: bpy.types.Bone) -> int:
        try:
            return int(bone_name)
        except ValueError:
            return StrCode32.from_string(bone_name).to_int()

    return fwrap_misc_export.build_track_metadata_dict_from_fcurves(
        armature=motion_points_armature,
        action=action,
        armature_label="motion points",
        bone_skip_predicate=None,
        name_hash_extractor_fn=_mtp_hash,
        warn_on_missing_metadata=False,
    )


def collect_motion_point_actions(
    motion_points_armature: bpy.types.Object,
    use_nla: bool,
    export_clean_threshold: float = 0.0,
) -> List[ExportActionData]:
    """Collect motion-point animation actions from *motion_points_armature*.

    Args:
        motion_points_armature: Motion points armature object.
        use_nla:                 If ``True``, collect from NLA strips; if
                                 ``False``, use the active action.
        export_clean_threshold:  FCurve cleaning threshold (0 = disabled).

    Returns:
        List of :class:`ExportActionData` objects.
    """
    return fwrap_misc_export.collect_armature_actions(
        motion_points_armature, use_nla,
        track_type_label="motion points",
        export_clean_threshold=export_clean_threshold,
    )
