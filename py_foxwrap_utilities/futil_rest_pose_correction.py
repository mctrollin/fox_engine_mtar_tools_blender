"""
Rest Pose Correction related utilities.
"""

from ..py_foxwrap.fwrap_mapping_types import BoneParameters
from ..py_foxwrap.fwrap_track_types import TrackDataBlobWrapper





def _apply_rest_pose_correction_to_track_blob(track_blob: TrackDataBlobWrapper, rest_pose_dict: dict) -> list:
    euler_deg = rest_pose_dict.get('euler', [])
    if track_blob.space_r:
        if track_blob.rotation_offset is None:
            track_blob.rotation_offset = []
        track_blob.rotation_offset.append(rest_pose_dict)
    else:
        track_blob.map_r_rest_pose = rest_pose_dict
    return euler_deg


def _apply_rest_pose_correction_to_bone_parameters(bone_params: BoneParameters, rest_pose_dict: dict) -> list:
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
        return _apply_rest_pose_correction_to_track_blob(target, rest_pose_dict)
    if isinstance(target, BoneParameters):
        return _apply_rest_pose_correction_to_bone_parameters(target, rest_pose_dict)
    raise TypeError('target must be TrackDataBlobWrapper or BoneParameters')

