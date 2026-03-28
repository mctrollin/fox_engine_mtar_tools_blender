"""
Rest Pose Correction related utilities.
"""

from ..py_foxwrap.fwrap_mapping_types import BoneParameters
from ..py_foxwrap.fwrap_track_types import TrackDataBlobWrapper


def _apply_rest_pose_correction_to_track_blob(track_blob: TrackDataBlobWrapper, rest_pose_dict: dict) -> list:
    """Apply rest-pose correction data to a track blob wrapper.

    If the track uses local rotation space, append the rest-pose correction to
    :attr:`TrackDataBlobWrapper.rotation_offset`. Otherwise store it in
    :attr:`TrackDataBlobWrapper.map_r_rest_pose`.

    Args:
        track_blob: Track data blob wrapper being corrected.
        rest_pose_dict: Rest pose dictionary (usually from mapping file) with
            keys like ``'euler'``.

    Returns:
        Euler rotation values in degrees from ``rest_pose_dict.get('euler', [])``.
    """
    if track_blob.space_r:
        if track_blob.rotation_offset is None:
            track_blob.rotation_offset = []
        track_blob.rotation_offset.append(rest_pose_dict)
    else:
        track_blob.map_r_rest_pose = rest_pose_dict
    return rest_pose_dict.get('euler', [])


def _apply_rest_pose_correction_to_bone_parameters(bone_params: BoneParameters, rest_pose_dict: dict) -> list:
    """Apply rest-pose correction data to bone parameters.

    If the bone uses local rotation space, append the rest-pose correction to
    :attr:`BoneParameters.rotation_offset`. Otherwise store it in
    :attr:`BoneParameters.map_r`.

    Args:
        bone_params: Bone parameters being corrected.
        rest_pose_dict: Rest pose dictionary (usually from mapping file) with
            keys like ``'euler'``.

    Returns:
        Euler rotation values in degrees from ``rest_pose_dict.get('euler', [])``.
    """
    if bone_params.space_r:
        if bone_params.rotation_offset is None:
            bone_params.rotation_offset = []
        bone_params.rotation_offset.append(rest_pose_dict)
    else:
        bone_params.map_r = rest_pose_dict
    return rest_pose_dict.get('euler', [])


def apply_rest_pose_correction_to_target(target, rest_pose_dict) -> list:
    """Route rest-pose correction to the correct target type.

    Args:
        target: Instance of :class:`TrackDataBlobWrapper` or :class:`BoneParameters`.
        rest_pose_dict: Rest pose dictionary containing correction data.

    Returns:
        Euler rotation values in degrees after applying correction.

    Raises:
        TypeError: If target is not a supported type.
    """
    if isinstance(target, TrackDataBlobWrapper):
        return _apply_rest_pose_correction_to_track_blob(target, rest_pose_dict)
    if isinstance(target, BoneParameters):
        return _apply_rest_pose_correction_to_bone_parameters(target, rest_pose_dict)
    raise TypeError('target must be TrackDataBlobWrapper or BoneParameters')

