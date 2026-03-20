"""
Shared fake types used by both import and export.
"""
import math
from typing import List

from ..py_core.core_logging import Debug
from ..py_fox.fox_gani_enums import TrackUnitFlags

from .foxwrap_mapping_types import BoneParameters
from .foxwrap_misc_types import Tracks, TrackDataBlobWrapper, TrackUnitWrapper

_DIFF_SEGMENT_TYPES = frozenset(() )


def get_rest_pose_dict_from_bone(bone) -> dict:
    """Convert a bone's local rest rotation into Fox mapping rest-pose structure."""
    euler = bone.matrix_local.to_euler('XYZ')
    euler_deg = [
        math.degrees(euler.x),
        math.degrees(euler.y),
        math.degrees(euler.z),
    ]
    return {'euler': euler_deg, 'order': 'XYZ'}


def apply_rest_pose_correction_to_track_blob(track_blob: TrackDataBlobWrapper, rest_pose_dict: dict) -> list:
    euler_deg = rest_pose_dict.get('euler', [])
    if track_blob.space_r:
        if track_blob.rotation_offset is None:
            track_blob.rotation_offset = []
        track_blob.rotation_offset.append(rest_pose_dict)
    else:
        track_blob.map_r_rest_pose = rest_pose_dict
    return euler_deg


def apply_rest_pose_correction_to_bone_parameters(bone_params: BoneParameters, rest_pose_dict: dict) -> list:
    euler_deg = rest_pose_dict.get('euler', [])
    if bone_params.space_r:
        if bone_params.rotation_offset is None:
            bone_params.rotation_offset = []
        bone_params.rotation_offset.append(rest_pose_dict)
    else:
        bone_params.map_r = rest_pose_dict
    return euler_deg


def apply_rest_pose_correction_to_target(target, rest_pose_dict) -> list:
    if isinstance(target, TrackDataBlobWrapper):
        return apply_rest_pose_correction_to_track_blob(target, rest_pose_dict)
    if isinstance(target, BoneParameters):
        return apply_rest_pose_correction_to_bone_parameters(target, rest_pose_dict)
    raise TypeError('target must be TrackDataBlobWrapper or BoneParameters')


def build_gani_tracks_from_tracks(tracks: Tracks) -> List[TrackUnitWrapper]:
    """Convert a Tracks object to a list of TrackUnitWrapper instances."""
    if tracks is None:
        return []

    wrappers: List[TrackUnitWrapper] = []
    for track_unit in tracks.track_units:
        segments = []
        for segment_index, track_data in enumerate(track_unit.segments_data):
            data_blob = getattr(track_data, 'data_blob', None)
            if data_blob is None:
                Debug.log_warning(f"build_gani_tracks_from_tracks: Track '{track_unit.name}' segment {segment_index} has data_blob=None")
            segments.append(TrackDataBlobWrapper(
                name=track_unit.name,
                segment_index=segment_index,
                data_blob=data_blob,
            ))

        # Convert integer unit_flags to list of TrackUnitFlags if possible.
        flags = []
        if isinstance(track_unit.unit_flags, int):
            flags = TrackUnitFlags.int_to_track_unit_flags(track_unit.unit_flags)
        else:
            flags = track_unit.unit_flags

        rig_unit_type_val = getattr(track_unit, 'rig_unit_type', None)
        if rig_unit_type_val is not None:
            Debug.log_warning(f"build_gani_tracks_from_tracks: Track '{track_unit.name}' has unexpected rig_unit_type={rig_unit_type_val}")
            # NOTE: TrackUnit does not officialy define rig_unit_type; this should be investigated further.

        wrappers.append(TrackUnitWrapper(
            name=track_unit.name,
            segments_track_data=segments,
            unit_flags=flags,
            rig_unit_type=rig_unit_type_val,
        ))

    return wrappers


def is_root_motion_track(wrapper: TrackUnitWrapper) -> bool:
    if not wrapper.segments_track_data:
        return True
    return all(seg.data_blob.type in _DIFF_SEGMENT_TYPES for seg in wrapper.segments_track_data)
