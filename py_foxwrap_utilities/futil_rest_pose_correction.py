"""
Rest Pose Correction related utilities.
"""

from typing import List, Optional

import bpy

from ..py_core.core_logging import Debug

from ..py_utilities import util_blender_armature

from ..py_fox.fox_gani_enums import SegmentType

from ..py_foxwrap.fwrap_mapping_export_types import TrackSegmentBoneMapping
from ..py_foxwrap.fwrap_mtar_import_types import GaniImportData
from ..py_foxwrap.fwrap_mapping_types import BoneParameters
from ..py_foxwrap.fwrap_gani_track_types import TrackDataBlobWrapper


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


def _apply_rest_pose_correction_to_target(target, rest_pose_dict) -> list:
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


def extract_rest_pose_from_custom_rig(all_gani_data: List[GaniImportData], custom_rig: Optional[bpy.types.Object]) -> None:
    """Extract rest pose rotations from custom rig and merge with existing transformations.
    
    For each rotation track, extracts the bone's rest pose from the custom rig and:
    - For LOCAL space tracks: Merges with existing map_r_rest_pose (or creates if missing)
    - For WORLD space tracks: Adds to rotation_offset list
    
    This allows combining mapping file transformations with custom rig rest pose.
    
    Args:
        all_gani_data: List of imported GaniImportData objects
        custom_rig: Optional target armature to extract rest pose from
    """
    if not custom_rig or custom_rig.type != 'ARMATURE':
        return
    
    Debug.log("\n=== Extracting Rest Pose from custom rig ===")
    rest_pose_count = 0
    
    for gani_track in GaniImportData.iter_bone_tracks(all_gani_data):
        for track_blob in gani_track.segments_track_data:
            # Only apply to rotation segments
            if track_blob.data_blob.type not in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
                continue

            # Skip as_ik_up bones - they should not be affected by rest pose corrections
            if track_blob.as_ik_up:
                continue

            # Check if bone exists in custom rig
            if track_blob.name not in custom_rig.data.bones:
                continue

            # Extract rest pose rotation from custom rig
            bone = custom_rig.data.bones[track_blob.name]
            rest_pose_dict = util_blender_armature.get_rest_pose_dict_from_bone(bone)

            had_map_r_rest_pose = track_blob.map_r_rest_pose is not None
            existing_euler = track_blob.map_r_rest_pose['euler'] if had_map_r_rest_pose else None

            # Apply helper updates for both import/export semantics
            euler_deg = _apply_rest_pose_correction_to_target(track_blob, rest_pose_dict)

            if track_blob.space_r:
                Debug.log(f"  {track_blob.name} [WORLD]: Added rest pose to offset_r: ({euler_deg[0]:.1f}, {euler_deg[1]:.1f}, {euler_deg[2]:.1f})")
            else:
                if not had_map_r_rest_pose:
                    Debug.log(f"  {track_blob.name} [LS]: Set rest pose from rig: ({euler_deg[0]:.1f}, {euler_deg[1]:.1f}, {euler_deg[2]:.1f})")
                else:
                    Debug.log(f"  {track_blob.name} [LS]: Mapping file has map_r=({existing_euler[0]:.1f}, {existing_euler[1]:.1f}, {existing_euler[2]:.1f}), using custom rig instead")

            rest_pose_count += 1
    
    Debug.log(f"Extracted rest pose for {rest_pose_count} track(s) from custom rig")


def extract_rest_pose_correction_mapping_from_armature(track_segment_bone_mapping: TrackSegmentBoneMapping, armature: bpy.types.Object) -> None:
    """Extract rest pose rotations from armature and merge with existing transformations.
    
    For each bone in the mapping, extracts its rest pose from the armature and:
    - For LOCAL space tracks: Merges with existing map_r (or creates if missing)
    - For WORLD space tracks: Adds to rotation_offset list
    
    This allows combining mapping file transformations with armature rest pose.
    
    Args:
        track_segment_bone_mapping: Mapping structure to update
        armature: Source armature
    """
    if not armature or armature.type != 'ARMATURE':
        return
    
    Debug.log("\n=== Extracting Rest Pose from Armature ===")
    rest_pose_count = 0
    
    # Iterate through all mapped bones
    for track_idx in track_segment_bone_mapping.get_track_indices():
        segments = track_segment_bone_mapping.get_track_segments(track_idx)
        for _seg_idx, blender_bone_name, bone_params in segments:
            # Skip as_ik_up bones - they should not be affected by rest pose corrections
            if bone_params.as_ik_up:
                continue
            
            # Check if bone exists in armature
            if blender_bone_name not in armature.data.bones:
                continue
            
            # Extract rest pose rotation from armature
            bone = armature.data.bones[blender_bone_name]
            rest_pose_dict = util_blender_armature.get_rest_pose_dict_from_bone(bone)

            had_map_r = bone_params.map_r is not None
            existing_euler = bone_params.map_r['euler'] if had_map_r else None

            # Apply helper updates for both import/export semantics
            euler_deg = _apply_rest_pose_correction_to_target(bone_params, rest_pose_dict)

            if bone_params.space_r:
                Debug.log(f"  {blender_bone_name} [WORLD]: Added rest pose to offset_r: ({euler_deg[0]:.1f}, {euler_deg[1]:.1f}, {euler_deg[2]:.1f})")
            else:
                if not had_map_r:
                    Debug.log(f"  {blender_bone_name} [LS]: Set rest pose from armature: ({euler_deg[0]:.1f}, {euler_deg[1]:.1f}, {euler_deg[2]:.1f})")
                else:
                    Debug.log(f"  {blender_bone_name} [LS]: Mapping file has map_r=({existing_euler[0]:.1f}, {existing_euler[1]:.1f}, {existing_euler[2]:.1f}), using armature instead")

            rest_pose_count += 1            
            rest_pose_count += 1
    
    Debug.log(f"Extracted rest pose for {rest_pose_count} bone(s) from armature")
