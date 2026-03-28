from typing import List, Optional, Dict, Tuple

import bpy

from ..py_core.core_logging import Debug

from ..py_utilities import util_parsing

from .fwrap_mapping_export_types import TrackSegmentBoneMapping
from .fwrap_mapping_types import BoneParameters
from .fwrap_metadata_types import TrackMetaData
from . import fwrap_metadata


def group_bones_by_segment(bone_names: List[str]) -> List[Tuple[str, List[Tuple[int, str]]]]:
    """Group bone names by their base track, detecting segment convention.

    Segment convention:
    - Segment 0 = base bone name, no suffix  (e.g. "bone_XYZ")
    - Segment N = base bone + "_N" for N >= 1 (e.g. "bone_XYZ_1", "bone_XYZ_2")
    A suffixed bone is only treated as a segment of its base when the unsuffixed
    base name ALSO exists in the bone list — this prevents false-grouping of bones
    whose names happen to end in a digit.

    Args:
        bone_names: Ordered list of bone names from the armature.

    Returns:
        List of (base_name, [(segment_idx, bone_name), ...]) in stable input order.
        Each tuple's segment list always starts with (0, base_name) and is followed
        by consecutively numbered siblings found in bone_names.
    """
    name_set = set(bone_names)
    processed: set = set()
    groups: List[Tuple[str, List[Tuple[int, str]]]] = []

    for bone_name in bone_names:
        if bone_name in processed:
            continue

        # If this bone looks like a segment N (N>=1) of an existing base, skip it here;
        # it will be picked up when the base bone is processed.
        base, idx = util_parsing.parse_segment_suffix(bone_name)
        if idx >= 1 and base in name_set:
            continue

        # This is a base bone — collect all _N siblings (N=1, 2, …) present in the armature.
        processed.add(bone_name)
        segments: List[Tuple[int, str]] = [(0, bone_name)]
        seg_idx = 1
        while True:
            sibling = f"{bone_name}_{seg_idx}"
            if sibling in name_set and sibling not in processed:
                processed.add(sibling)
                segments.append((seg_idx, sibling))
                seg_idx += 1
            else:
                break

        groups.append((bone_name, segments))

    return groups


def create_synthetic_mapping(armature: bpy.types.Object,
                            action: bpy.types.Action,
                            layout_metadata_dict: Optional[Dict[str, TrackMetaData]]
                            ) -> Tuple[TrackSegmentBoneMapping, Dict[str, TrackMetaData]]:
    """Create synthetic track mapping from armature bones when no mapping is provided.
    
    This is used for motion points export or when exporting without a mapping file.
    Builds a TrackSegmentBoneMapping with one track per bone and derives metadata
    from either the provided layout_metadata_dict or by analyzing fcurves.
    
    Args:
        armature: Armature object (bpy.types.Object)
        action: Action to analyze for fcurves and metadata (bpy.types.Action)
        layout_metadata_dict: Optional metadata dict (for motion points)
        
    Returns:
        Tuple of (mapping, metadata_dict):
        - mapping: TrackSegmentBoneMapping with one track per bone (segment 0)
        - metadata_dict: Dictionary of bone_name -> fwrap_metadata.TrackMetaData
    """

    Debug.log("    Building synthetic mapping from armature bones...")
    
    bones_iterable = armature.pose.bones if armature.pose else armature.data.bones
    bone_names = [bone.name for bone in bones_iterable]

    temp_mapping = TrackSegmentBoneMapping()
    metadata_dict = {}
    track_idx = 0

    for base_name, segments in group_bones_by_segment(bone_names):

        # Collect metadata from the base bone; create default if none found.
        bone_metadata = None
        if layout_metadata_dict and base_name in layout_metadata_dict:
            bone_metadata = layout_metadata_dict[base_name]
        else:
            bone_metadata = fwrap_metadata.build_track_metadata_from_fcurves(bone_name=base_name, action=action)

        # Merge per-action overrides if available
        if action and bone_metadata:
            action_meta_bone = fwrap_metadata.build_track_metadata_from_action(action, base_name)
            if action_meta_bone:
                bone_metadata = fwrap_metadata.merge_track_metadata(bone_metadata, action_meta_bone)

        # Register every segment detected by group_bones_by_segment.
        for seg_idx, seg_bone_name in segments:
            temp_mapping.set_segment_mapping(track_idx, seg_idx, seg_bone_name, BoneParameters(fox_name=seg_bone_name))

        if bone_metadata:
            metadata_dict[base_name] = bone_metadata
        track_idx += 1
    
    # Finalize temp_mapping to populate missing segments (e.g., if a bone has both rotation and location)
    # This prevents "Missing mapping" warnings for segment 1, 2, etc.
    if layout_metadata_dict:
        temp_mapping.finalize_with_layout_metadata(layout_metadata_dict)
    
    Debug.log(f"    Built synthetic mapping: {track_idx} track(s)")
    
    return temp_mapping, metadata_dict
