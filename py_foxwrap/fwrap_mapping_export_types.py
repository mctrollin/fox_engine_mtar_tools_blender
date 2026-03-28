import copy
from dataclasses import dataclass
from typing import Optional, List, Dict, Set, Tuple

from ..py_core.core_logging import Debug

from ..py_utilities import util_parsing

from .fwrap_mapping_types import BoneParameters
from .fwrap_metadata_types import TrackMetaData


@dataclass
class TrackSegmentBoneMapping:
    """Unified mapping for track segments to Blender bones.
    
    This class provides a consistent interface for mapping track segments to bones,
    regardless of whether a track has single or multiple segments.
    
    Key format: Always (track_idx, segment_idx) for consistency
    - Single-segment tracks: (track_idx, 0)
    - Multi-segment tracks: (track_idx, 0), (track_idx, 1), etc.
    
    This eliminates the need for separate integer and tuple keys in the mapping dictionary.
    """

    def __init__(self):
        # Internal storage: (track_idx, segment_idx) -> (bone_name, bone_parameters)
        self._mappings: Dict[Tuple[int, int], Tuple[str, BoneParameters]] = {}

    def __len__(self) -> int:
        """Get total number of segment mappings."""
        return len(self._mappings)

    def __contains__(self, key: Tuple[int, int]) -> bool:
        """Check if a (track_idx, segment_idx) key exists."""
        return key in self._mappings

    def __iter__(self):
        """Iterate over all (track_idx, segment_idx) keys."""
        return iter(self._mappings.keys())


    def set_segment_mapping(self, track_idx: int, segment_idx: int, bone_name: str, bone_parameters: BoneParameters) -> None:
        """Set mapping for a specific track segment.
        
        Args:
            track_idx: Track index
            segment_idx: Segment index within track (0-based)
            bone_name: Name of Blender bone
            bone_parameters: Bone parameters
        """
        key = (track_idx, segment_idx)
        self._mappings[key] = (bone_name, bone_parameters)

    def get_segment_mapping(self, track_idx: int, segment_idx: int) -> Optional[Tuple[str, BoneParameters]]:
        """Get mapping for a specific track segment.
        
        Args:
            track_idx: Track index
            segment_idx: Segment index within track (0-based)
            
        Returns:
            Tuple of (bone_name, bone_parameters) if found, None otherwise
        """
        key = (track_idx, segment_idx)
        return self._mappings.get(key)

    def get_base_mapping(self, track_idx: int) -> Optional[Tuple[str, BoneParameters]]:
        """Get base mapping for a track (segment 0).
        
        Args:
            track_idx: Track index
            
        Returns:
            Tuple of (bone_name, bone_parameters) for segment 0 if found, None otherwise
        """
        return self.get_segment_mapping(track_idx, 0)

    def get_track_indices(self) -> List[int]:
        """Get all unique track indices in sorted order.
        
        Returns:
            List of track indices
        """
        track_indices = set()
        for track_idx, _ in self._mappings.keys():
            track_indices.add(track_idx)
        return sorted(track_indices)

    def get_segment_count(self, track_idx: int) -> int:
        """Get number of segments for a track.
        
        Args:
            track_idx: Track index
            
        Returns:
            Number of segments for this track
        """
        segment_indices = set()
        for (t_idx, s_idx) in self._mappings.keys():
            if t_idx == track_idx:
                segment_indices.add(s_idx)
        return len(segment_indices)

    def has_track(self, track_idx: int) -> bool:
        """Check if track exists in mapping.
        
        Args:
            track_idx: Track index
            
        Returns:
            True if track has any segments mapped
        """
        for (t_idx, _s_idx) in self._mappings.keys():
            if t_idx == track_idx:
                return True
        return False

    def is_multi_segment_track(self, track_idx: int) -> bool:
        """Check if track has multiple segments.
        
        Args:
            track_idx: Track index
            
        Returns:
            True if track has more than one segment
        """
        return self.get_segment_count(track_idx) > 1

    def get_track_segments(self, track_idx: int) -> List[Tuple[int, str, BoneParameters]]:
        """Get all segments for a track in sorted order.
        
        Args:
            track_idx: Track index
            
        Returns:
            List of (segment_idx, bone_name, bone_parameters) tuples
        """
        segments = []
        for (t_idx, s_idx), (bone_name, bone_parameters) in self._mappings.items():
            if t_idx == track_idx:
                segments.append((s_idx, bone_name, bone_parameters))
        
        # Sort by segment index
        segments.sort(key=lambda x: x[0])
        return segments

    def get_total_track_count(self) -> int:
        """Get total number of unique tracks.
        
        Returns:
            Number of unique tracks
        """
        return len(self.get_track_indices())

    def get_all_mappings(self) -> Dict[Tuple[int, int], Tuple[str, BoneParameters]]:
        """Get all mappings as a dictionary.
        
        Returns:
            Dictionary of (track_idx, segment_idx) -> (bone_name, bone_parameters)
        """
        return self._mappings.copy()

    def get_all_bone_names(self) -> Set[str]:
        """Get all unique Blender bone names referenced in this mapping.

        Returns:
            Set of bone names (may include special names such as the armature target).
        """
        return {bone_name for (bone_name, _) in self._mappings.values()}

    def populate_missing_segments(self, track_idx: int, expected_segment_count: int) -> None:
        """Populate missing segments for a track using the base mapping.
        
        For tracks where the mapping file only specifies one bone (segment 0) but the track
        has multiple segments according to the layout metadata, this method will replicate
        the base mapping to all segments.
        
        Args:
            track_idx: Track index
            expected_segment_count: Expected number of segments from layout metadata
        """
        # Get the base mapping (segment 0)
        base_mapping = self.get_base_mapping(track_idx)
        if not base_mapping:
            return  # No base mapping to replicate
        
        base_bone_name, base_bone_parameters = base_mapping
        
        # Check if we already have all expected segments
        current_segment_count = self.get_segment_count(track_idx)
        if current_segment_count >= expected_segment_count:
            return  # Already have enough segments
        
        # Replicate base mapping to missing segments
        populated_segments = []
        for segment_idx in range(expected_segment_count):
            if not self.get_segment_mapping(track_idx, segment_idx):
                # Missing segment - replicate base mapping
                # Create a copy of the bone parameters for this segment
               
                new_bone_parameters = copy.deepcopy(base_bone_parameters)
                self.set_segment_mapping(track_idx, segment_idx, base_bone_name, new_bone_parameters)
                populated_segments.append(segment_idx)
        
        if populated_segments:
            Debug.log(f"    Track {track_idx}: Populated segments {populated_segments} with base mapping '{base_bone_name}'")

    def finalize_with_layout_metadata(self, metadata_dict: Dict[str, TrackMetaData]) -> None:
        """Finalize mappings using layout metadata to populate missing segments.
        
        This method should be called after all explicit mappings are loaded from the
        mapping file. It uses the layout metadata to ensure all tracks have the
        correct number of segments, replicating base mappings where needed.
        
        Args:
            metadata_dict: Dictionary of fox_track_name -> TrackMetaData from layout action
        """
        # Get all track indices that we have mappings for
        track_indices = self.get_track_indices()
        
        for track_idx in track_indices:
            # Get base mapping to determine fox track name
            base_mapping = self.get_base_mapping(track_idx)
            if not base_mapping:
                continue
                
            _, bone_parameters = base_mapping
            fox_track_name = bone_parameters.fox_name
            
            # Strip multi-segment suffix if present
            base_fox_track_name, _ = util_parsing.parse_segment_suffix(fox_track_name)
            
            # Look up expected segment count from layout metadata
            if base_fox_track_name in metadata_dict:
                metadata = metadata_dict[base_fox_track_name]
                expected_segment_count = len(metadata.segment_types)
                
                # Populate missing segments
                self.populate_missing_segments(track_idx, expected_segment_count)
                
    # def gather_known_bone_names_from_mapping(self) -> Set[str]:
    #     """Gather all bone names that exist in track segment bone mapping.
        
    #     This builds a set of bone names from the export mapping, used to identify which
    #     bones are part of the actual animation data (vs Blender utility bones).
        
    #     Args:
    #         track_segment_bone_mapping: containing bone mappings
            
    #     Returns:
    #         Set of bone names found in the mapping
    #     """
    #     known_bone_names = set()
    #     for track_idx in self.get_track_indices():
    #         for segment_idx in self.get_segment_indices(track_idx):
    #             blender_bone_name, _ = self.get_segment_mapping(track_idx, segment_idx)
    #             if blender_bone_name:
    #                 known_bone_names.add(blender_bone_name)
    #     return known_bone_names
