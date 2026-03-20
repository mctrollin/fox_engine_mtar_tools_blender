import copy
from dataclasses import dataclass
from typing import Optional, List, Dict, Set, Tuple, Union

import io
import bpy

from ..py_core.core_logging import Debug

from ..py_utilities.utilities_parsing import parse_segment_suffix

from ..py_fox.fox_gani_types import TrackUnitFlags, EvpHeader

from .foxwrap_mapping_types import BoneParameters
from .foxwrap_metadata_types import TrackMetaData
from .foxwrap_metadata import parse_gani_params_from_action
from .foxwrap_misc import TrackUnitWrapper, Tracks
from .foxwrap_gani2_writer import Gani2Writer


@dataclass
class ExportActionData:
    action: bpy.types.Action
    frame_start: int
    frame_end: int
    source: str
    export_clean_threshold: float = 0.0

    def to_string(self) -> str:
        frame_count = self.frame_end - self.frame_start + 1
        return f"'Action '{self.action.name}' (frames {self.frame_start}-{self.frame_end}, {frame_count} frames) - {self.source}"


@dataclass
class GaniExportTracksData:
    gani_tracks: List[TrackUnitWrapper]
    action: Optional[bpy.types.Action] = None
    source: Optional[str] = None


@dataclass
class GaniExportMotionPointsData:
    motion_point_tracks: List[TrackUnitWrapper]
    action: Optional[bpy.types.Action] = None


@dataclass
class GaniExportShaderData:
    property_tracks: List[List[TrackUnitWrapper]]
    property_names: List[str]
    action: Optional[bpy.types.Action] = None
    property_headers: Optional[List[Optional[Dict[str, int]]]] = None


@dataclass
class GaniMotionEventsData:
    motion_events: EvpHeader
    action: Optional[bpy.types.Action] = None


@dataclass
class GaniExportData:
    name: str
    frame_count: int
    frame_rate: int
    frame_start: int
    frame_end: int
    tracks_data: GaniExportTracksData
    motion_points_data: Optional[GaniExportMotionPointsData] = None
    motion_events_data: Optional[GaniMotionEventsData] = None
    shader_nodes_data: Optional[GaniExportShaderData] = None
    node_params: Optional[Dict[str, List[Tuple[int, Union[float, str, int]]]]] = None
    path_hash: Optional[int] = None

    def count_segments(self) -> int:
        if not self.tracks_data or not self.tracks_data.gani_tracks:
            return 0
        return sum(
            len(w.segments_track_data)
            for w in self.tracks_data.gani_tracks
            if w.segments_track_data
        )

    def to_bytes(self, layout_track: 'Tracks') -> bytes:
        """Convert this GANI data to binary format."""
        writer = Gani2Writer()
        buffer = io.BytesIO()

        unit_flags_per_file = []
        segment_bit_sizes_per_file = []

        for track_idx, track_unit in enumerate(layout_track.track_units):
            default_flags = track_unit.unit_flags if track_idx < len(layout_track.track_units) else 0
            segment_count = len(track_unit.segments_data) if track_unit and track_unit.segments_data else 0

            if track_idx < len(self.tracks_data.gani_tracks):
                gani_track = self.tracks_data.gani_tracks[track_idx]

                if gani_track.unit_flags:
                    flags_value = TrackUnitFlags.track_unit_flags_to_int(gani_track.unit_flags)
                else:
                    Debug.log_warning(f"Warning: Track {track_idx} has no unit_flags in gani_track, using layout default ({default_flags})")
                    flags_value = default_flags

                unit_flags_per_file.append(flags_value)

                for segment_idx in range(segment_count):
                    if segment_idx < len(gani_track.segments_track_data):
                        segment_data = gani_track.segments_track_data[segment_idx]
                        component_bit_size = segment_data.data_blob.component_bit_size
                        segment_bit_sizes_per_file.append(component_bit_size)
                    else:
                        Debug.log_warning(f"Warning: Track {track_idx} segment {segment_idx} missing in gani_track, using bit size 0")
                        segment_bit_sizes_per_file.append(0)
            else:
                Debug.log_warning(f"Warning: Track {track_idx} missing in gani_tracks, using layout defaults (flags={default_flags}, bits=0)")
                unit_flags_per_file.append(default_flags)
                for _ in range(segment_count):
                    segment_bit_sizes_per_file.append(0)

        if self.tracks_data.action is not None:
            motion_params = parse_gani_params_from_action(self.tracks_data.action)
        elif self.node_params is not None:
            motion_params = self.node_params.get("MOTION", [])
        else:
            motion_params = []

        writer.write_gani_to_buffer(
            buffer, self.tracks_data.gani_tracks, layout_track,
            self.frame_count, self.frame_rate,
            params=motion_params,
            unit_flags_per_file=unit_flags_per_file,
            segment_bit_sizes_per_file=segment_bit_sizes_per_file,
        )

        return buffer.getvalue()


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
    
    def __len__(self) -> int:
        """Get total number of segment mappings."""
        return len(self._mappings)
    
    def __contains__(self, key: Tuple[int, int]) -> bool:
        """Check if a (track_idx, segment_idx) key exists."""
        return key in self._mappings
    
    def __iter__(self):
        """Iterate over all (track_idx, segment_idx) keys."""
        return iter(self._mappings.keys())

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
            base_fox_track_name, _ = parse_segment_suffix(fox_track_name)
            
            # Look up expected segment count from layout metadata
            if base_fox_track_name in metadata_dict:
                metadata = metadata_dict[base_fox_track_name]
                expected_segment_count = len(metadata.segment_types)
                
                # Populate missing segments
                self.populate_missing_segments(track_idx, expected_segment_count)
