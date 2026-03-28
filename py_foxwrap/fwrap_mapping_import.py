"""
Import-time low-level track mapping transformations.

This module contains helpers for mapping imported GANI track blobs to Blender
bones using parsed mapping data (BoneParameters). It validates collision cases,
applies per-track transformations, and keeps per-file mapping logic separated
from exporters.
"""

from typing import Optional, List, Dict, Tuple

from ..py_core.core_logging import Debug

from ..py_fox.fox_gani_types import SegmentType

from .fwrap_gani_track_types import TrackDataBlobWrapper
from .fwrap_mtar_import_types import GaniImportData
from ..py_foxwrap.fwrap_mapping_types import BoneParameters


def _apply_track_mapping_transformation(track_blob: TrackDataBlobWrapper, mapping_data: BoneParameters, old_name: str) -> None:
    """Apply transformation parameters from track mapping to a TrackDataBlobWrapper.
    
    This helper function extracts and applies all transformation parameters
    (name change, rotation offset, axis mapping, rotation addition) to a track.
    
    Args:
        track_blob: The TrackDataBlobWrapper to transform
        mapping_data: BoneParameters containing transformation parameters
        old_name: Original track name (for logging)
    """
    # Use track_name from BoneParameters (defaults to fox_name if not set)
    new_name: str = mapping_data.track_name if mapping_data.track_name else mapping_data.fox_name
    
    track_blob.name = new_name
    Debug.log(f"  '{old_name}' -> '{new_name}'")
    
    # Store rotation offset transformation if present (will be applied during import)
    if mapping_data.rotation_offset:
        track_blob.rotation_offset = mapping_data.rotation_offset
        # rotation_offset is now a list of offsets
        offset_list = mapping_data.rotation_offset
        for i, offset in enumerate(offset_list, 1):
            Debug.log(f"    Rotation offset #{i}: ({offset['euler'][0]}, {offset['euler'][1]}, {offset['euler'][2]}) {offset['order']}")
    
    # Store rotation axis mapping transformation if present (will be applied during import)
    if mapping_data.rotation_axis_map:
        track_blob.rotation_axis_map = mapping_data.rotation_axis_map
        axis_str = ','.join([('-' if m['negate'] else '') + m['axis'] for m in mapping_data.rotation_axis_map])
        Debug.log(f"    Rotation axis mapping: {axis_str}")
    
    # Store directional vector IK transformation if present (will be applied during import)
    if mapping_data.as_ik_up:
        track_blob.as_ik_up = mapping_data.as_ik_up
        ik_data = mapping_data.as_ik_up
        Debug.log(f"    Directional vector IK: base='{ik_data.bone_base}', axis={ik_data.axis}")
    
    # Store map_r rest pose transformation if present (for LOCAL space tracks - similarity transformation)
    if mapping_data.map_r:
        track_blob.map_r_rest_pose = mapping_data.map_r
        euler = mapping_data.map_r['euler']
        Debug.log(f"    Rest pose (map_r): ({euler[0]}, {euler[1]}, {euler[2]}) {mapping_data.map_r['order']}")
    
    # Store space_r indicator if present (for WORLD space tracks - simple multiplication)
    if mapping_data.space_r:
        track_blob.space_r = mapping_data.space_r
        Debug.log(f"    Track space: {mapping_data.space_r['space']}")


def _validate_track_mapping_collisions(
    all_gani_data: List[GaniImportData],
    track_mapping: Dict[str, BoneParameters],
) -> None:
    """Warn if the mapping would assign multiple segments of the same type to one bone.

    The previous implementation used raw track lists and could report collisions
    across multiple GANIs.  We now accept `GaniImportData` objects and perform
    the check separately for each animation file, avoiding false positives.

    Args:
        all_gani_data: List of GaniImportData objects, one per GANI file.
        track_mapping: Mapping dictionary keyed by source track name.
    """
    if not track_mapping:
        return

    Debug.log("Validating track mapping collisions...")
    # iterate each GANI independently to avoid cross-file warnings
    for index, data in enumerate(all_gani_data):
        collision_map: Dict[Tuple[str, SegmentType], List[str]] = {}
        for gani_track in GaniImportData.iter_bone_tracks([data]):
            for track_blob in gani_track.segments_track_data:
                name = track_blob.name
                if name not in track_mapping:
                    continue
                mapping_data: BoneParameters = track_mapping[name]
                target_name: str = (
                    mapping_data.track_name if mapping_data.track_name else mapping_data.fox_name
                )
                key = (target_name, track_blob.data_blob.type)
                collision_map.setdefault(key, []).append(name)

        for (target_name, seg_type), sources in collision_map.items():
            if len(sources) > 1:
                Debug.log_warning(
                    f"GANI#{index}: mapping would apply multiple {seg_type.name} segments {sources} to bone '{target_name}'"
                )


def apply_track_transformations(all_gani_data: List[GaniImportData], track_mapping: Optional[Dict[str, BoneParameters]] = None) -> None:
    """Apply track mapping transformations to all tracks.
    
    Applies user-defined track mapping transformations if provided.
    
    Args:
        all_gani_data: List of GaniImportData objects (one per file).
        track_mapping: Optional dictionary mapping source track name to BoneParameters
    """
    # Apply track mapping transformations if provided
    if track_mapping:
        Debug.log("Applying track mapping transformations...")
        _validate_track_mapping_collisions(all_gani_data, track_mapping)

        for gani_track in GaniImportData.iter_tracks(all_gani_data, include_mtp=True):
            for track_blob in gani_track.segments_track_data:
                if track_blob.name in track_mapping:
                    old_name: str = track_blob.name
                    mapping_data: BoneParameters = track_mapping[old_name]
                    _apply_track_mapping_transformation(track_blob, mapping_data, old_name)
