"""
Export-only fake types for MTAR exporter.
"""
from dataclasses import dataclass
import io
import copy
from typing import Optional, List, Dict, Tuple
import re

import bpy

from ..py_utilities.utilities_logging import Debug

from ..py_fox.fox_gani_types import TrackUnitFlags, EvpHeader

from .foxwrap_metadata import TrackMetaData, merge_track_metadata, parse_gani_params_from_action
from .foxwrap_misc import Tracks, TrackUnitWrapper
from .foxwrap_gani2_writer import Gani2Writer
from .foxwrap_mapping import BoneParameters


@dataclass
class ExportActionData:
    """Container for action data to be exported to MTAR.
    
    This class holds the information needed to export a single Blender action
    as part of an MTAR file.
    
    Attributes:
        action: Blender action containing animation data
        frame_start: First frame to export
        frame_end: Last frame to export (inclusive)
        name: Display name for the exported animation
        source: Description of where this action came from (NLA strip, active action, etc.)
        export_clean_threshold: Threshold for FCurve cleaning after baking non-linear fcurves to linear (0 = disabled)
    """
    action: bpy.types.Action
    frame_start: int
    frame_end: int
    source: str
    export_clean_threshold: float = 0.0
    
    def to_string(self) -> str:
        """Get a formatted string representation of this export action."""
        frame_count = self.frame_end - self.frame_start + 1
        return f"'Action '{self.action.name}' (frames {self.frame_start}-{self.frame_end}, {frame_count} frames) - {self.source}"


@dataclass
class GaniExportTracksData:
    """Container for main animation track data in a GANI file.
    
    Attributes:
        gani_tracks: List of GaniTrack objects containing animation data
        action: Blender action containing animation-specific metadata (unit flags, component bit sizes)
        source: Description of where this animation came from (NLA track/strip, active action, etc.)
    """
    gani_tracks: List[TrackUnitWrapper]
    action: Optional[bpy.types.Action] = None
    source: Optional[str] = None


@dataclass
class GaniExportMotionPointsData:
    """Container for motion point track data in a GANI file.
    
    Attributes:
        motion_point_tracks: List of TrackUnitWrapper objects for motion points
        action: Blender action containing motion point-specific metadata
    """
    motion_point_tracks: List[TrackUnitWrapper]
    action: Optional[bpy.types.Action] = None


@dataclass
class GaniMotionEventsData:
    """Container for motion event data in a GANI file.
    
    Attributes:
        motion_events: EvpHeader containing all motion event data
        action: Optional reference to the action (for reading markers)
    """
    motion_events: 'EvpHeader'
    action: Optional[bpy.types.Action] = None


@dataclass
class GaniExportData:
    """Container for GANI file data to be written to MTAR.
    
    This class holds all the necessary information for a single GANI file
    without requiring temporary file storage.
    
    Attributes:
        name: Name/identifier for this GANI file
        frame_count: Total number of frames in the animation
        frame_rate: Animation frame rate (default 60 fps)
        frame_start: Starting frame of the animation
        frame_end: Ending frame of the animation
        tracks_data: Main animation track data (GaniTracksData)
        motion_points_data: Optional motion point track data (GaniMotionPointsData)
        motion_events_data: Optional motion event data (GaniMotionEventsData)
    """
    name: str
    frame_count: int
    frame_rate: int
    frame_start: int
    frame_end: int
    
    tracks_data: GaniExportTracksData
    motion_points_data: Optional[GaniExportMotionPointsData] = None
    motion_events_data: Optional[GaniMotionEventsData] = None

    def count_segments(self) -> int:
        """Count the total number of segments across all tracks in this GANI file.
        
        Used during export to determine per-GANI segment count for MTAR header.
        For old format: MTAR header segment_count = max across all GANIs.
        
        Returns:
            Total number of segments (sum of segment counts across all gani_tracks)
        """
        if not self.tracks_data or not self.tracks_data.gani_tracks:
            return 0
        return sum(
            len(w.segments_track_data)
            for w in self.tracks_data.gani_tracks
            if w.segments_track_data
        )
    
    def to_bytes(self, layout_track: 'Tracks') -> bytes:
        """Convert this GANI data to binary format.
        
        Args:
            layout_track: Tracks object containing track structure/layout
            
        Returns:
            Binary GANI data ready to be written to MTAR
        """
        
        # Create writer and write to memory buffer
        writer = Gani2Writer()
        buffer = io.BytesIO()
        
        # Extract per-file per-track metadata (unit flags and per-segment component bit sizes) 
        # from the already-merged TrackUnitWrapper objects (merged via merge_track_metadata())
        unit_flags_per_file = []
        segment_bit_sizes_per_file = []

        for track_idx, track_unit in enumerate(layout_track.track_units):
            # Default fallback values from layout track
            default_flags = track_unit.unit_flags if track_idx < len(layout_track.track_units) else 0
            segment_count = len(track_unit.segments_data) if track_unit and track_unit.segments_data else 0
            
            # Try to get merged metadata from gani_tracks (populated by export_gani_track_from_action)
            if track_idx < len(self.tracks_data.gani_tracks):
                gani_track = self.tracks_data.gani_tracks[track_idx]
                
                # Extract unit_flags (already merged via merge_track_metadata in export_gani_track_from_action)
                if gani_track.unit_flags:
                    flags_value = TrackUnitFlags.track_unit_flags_to_int(gani_track.unit_flags)
                else:
                    Debug.log_warning(f"Warning: Track {track_idx} has no unit_flags in gani_track, using layout default ({default_flags})")
                    flags_value = default_flags
                
                unit_flags_per_file.append(flags_value)
                
                # Extract component_bit_sizes from segment data blobs (already set during export)
                for segment_idx in range(segment_count):
                    if segment_idx < len(gani_track.segments_track_data):
                        segment_data = gani_track.segments_track_data[segment_idx]
                        component_bit_size = segment_data.data_blob.component_bit_size
                        segment_bit_sizes_per_file.append(component_bit_size)
                    else:
                        # Missing segment - use 0 as fallback
                        Debug.log_warning(f"Warning: Track {track_idx} segment {segment_idx} missing in gani_track, using bit size 0")
                        segment_bit_sizes_per_file.append(0)
            else:
                # Missing track - use layout defaults and log warning
                Debug.log_warning(f"Warning: Track {track_idx} missing in gani_tracks, using layout defaults (flags={default_flags}, bits=0)")
                unit_flags_per_file.append(default_flags)
                for _ in range(segment_count):
                    segment_bit_sizes_per_file.append(0)

        # Write GANI data to buffer
        # Pass the action-derived unit_flags and segment bit sizes if present
        writer.write_gani_to_buffer(
            buffer, self.tracks_data.gani_tracks, layout_track,
            self.frame_count, self.frame_rate,
            params=parse_gani_params_from_action(self.tracks_data.action),
            unit_flags_per_file=unit_flags_per_file,
            segment_bit_sizes_per_file=segment_bit_sizes_per_file
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
    
    def finalize_with_layout_metadata(self, metadata_dict: Dict[str, 'TrackMetaData']) -> None:
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
            
            # Strip segment suffix if present (e.g., "RIG_SKL_010_LSHLD_0" -> "RIG_SKL_010_LSHLD")
            base_fox_track_name = fox_track_name
            if '_' in fox_track_name:
                parts = fox_track_name.rsplit('_', 1)
                if len(parts) == 2 and parts[1].isdigit():
                    base_fox_track_name = parts[0]
            
            # Look up expected segment count from layout metadata
            if base_fox_track_name in metadata_dict:
                metadata = metadata_dict[base_fox_track_name]
                expected_segment_count = len(metadata.segment_types)
                
                # Populate missing segments
                self.populate_missing_segments(track_idx, expected_segment_count)


# Helper utilities for motion-point action matching ################################

def extract_gani_metadata(name: str) -> Optional[Tuple[int, str]]:
    """Extract (index, type) from action/strip name using new schema.
    
    Schema: <mtar-name>.<animation-parts>.<index>.<type>.(gani|strip)
    Handles both new and old formats with backward compatibility.
    
    Args:
        name: Action or strip name
        
    Returns:
        Tuple of (index, type) where type is 'track' or 'motionpoints'
        Returns None if name doesn't match expected schema
    """
    # Remove file extension
    if name.endswith('.gani'):
        name_no_ext = name[:-5]
    elif name.endswith('.strip'):
        name_no_ext = name[:-6]
    else:
        # Try old format detection: look for .motionpoints suffix
        if '.motionpoints.' in name:
            name_no_ext = name.replace('.gani', '').replace('.strip', '')
        else:
            return None
    
    parts = name_no_ext.split('.')
    if len(parts) < 4:  # At minimum: mtar, animation, index, type
        return None
    
    try:
        # Last two components are index and type
        gani_type = parts[-1]
        index = int(parts[-2])
        
        # Validate type
        if gani_type not in ('track', 'motionpoints'):
            # Backward compatibility: old format has no explicit type
            # Try to detect old .motionpoints suffix
            if '.motionpoints' in name:
                return (index, 'motionpoints')
            return None
        
        return (index, gani_type)
    except (ValueError, IndexError):
        pass
    
    return None


def build_motion_point_action_maps(motion_point_actions: List[ExportActionData]) -> Dict[int, ExportActionData]:
    """Build lookup map for motion point actions indexed by extracted GANI index.

    Returns:
    - by_gani_index: Dict[int, ExportActionData] mapping extracted running index -> action
    """
    by_gani_index: Dict[int, ExportActionData] = {}

    for a in motion_point_actions:
        # Extract index and type using robust parser
        result = extract_gani_metadata(a.action.name)
        if result:
            idx, gani_type = result
            if gani_type == 'motionpoints':
                if idx not in by_gani_index:
                    by_gani_index[idx] = a
            else:
                Debug.log_warning(f"Warning: Motion point action '{a.action.name}' has type '{gani_type}', expected 'motionpoints' - this action will be skipped")
        else:
            Debug.log_warning(f"Warning: No GANI index found in motion point action name '{a.action.name}' - this action will be skipped")

    return by_gani_index


def find_motion_point_action_for_gani(gani_name: str, by_gani_index: Dict[int, ExportActionData]) -> Optional[ExportActionData]:
    """Find the motion point action matching a GANI using only extracted running index.

    Returns the ExportActionData if a motion-point action exists for the given index, else None.
    """
    # Extract index and type using robust parser
    result = extract_gani_metadata(gani_name)
    if result:
        idx, gani_type = result
        if gani_type == 'track':
            return by_gani_index.get(idx)
        else:
            Debug.log_warning(f"Warning: GANI '{gani_name}' has type '{gani_type}', expected 'track' - motion points will be skipped for this GANI")
    else:
        Debug.log_warning(f"Warning: No GANI index could be extracted from GANI name '{gani_name}' - motion points will be skipped for this GANI")
    return None


def create_synthetic_mapping(armature: 'bpy.types.Object', 
                            action: bpy.types.Action,
                            layout_metadata_dict: Optional[Dict[str, TrackMetaData]]) -> Tuple[TrackSegmentBoneMapping, Dict[str, TrackMetaData]]:
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
        - metadata_dict: Dictionary of bone_name -> TrackMetaData
    """

    Debug.log("    Building synthetic mapping from armature bones...")
    
    bones_iterable = armature.pose.bones if armature.pose else armature.data.bones
    temp_mapping = TrackSegmentBoneMapping()
    metadata_dict = {}
    
    track_idx = 0
    for bone in bones_iterable:
        bone_name = bone.name
        
        # Get metadata for this bone (from layout_metadata_dict or by analyzing fcurves)
        bone_metadata = None
        if layout_metadata_dict and bone_name in layout_metadata_dict:
            # Use provided metadata
            bone_metadata = layout_metadata_dict[bone_name]
        else:
            # Build minimal metadata by analyzing fcurves (legacy fallback)
            bone_metadata = TrackMetaData.from_fcurves(bone_name=bone_name, action=action)
        
        # Skip bones with no metadata (no fcurves and not in metadata_dict)
        if not bone_metadata:
            continue
        
        # Merge per-action overrides if available
        if action:
            action_meta_bone = TrackMetaData.from_action(action, bone_name)
            if action_meta_bone:
                bone_metadata = merge_track_metadata(bone_metadata, action_meta_bone)
        
        # Create single-segment mapping for this bone
        # Each bone becomes a single track with one segment (segment 0)
        temp_mapping.set_segment_mapping(
            track_idx, 0, bone_name,
            BoneParameters(fox_name=bone_name)
        )
        
        metadata_dict[bone_name] = bone_metadata
        track_idx += 1
    
    # Finalize temp_mapping to populate missing segments (e.g., if a bone has both rotation and location)
    # This prevents "Missing mapping" warnings for segment 1, 2, etc.
    if layout_metadata_dict:
        temp_mapping.finalize_with_layout_metadata(layout_metadata_dict)
    
    Debug.log(f"    Built synthetic mapping: {track_idx} track(s)")
    
    return temp_mapping, metadata_dict
