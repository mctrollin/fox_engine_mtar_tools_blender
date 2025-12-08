"""
GANI (Game Animation) file writer for Metal Gear Solid V.

This module handles writing GANI format animation data.
"""

import io
from typing import List

from ..py_utilities.logging_utilities import Debug

from ..py_fox.fox_gani_types import (
    Gani2TrackData,
    TrackData,
    TrackMiniHeader,
)

from .foxwrap_misc import TrackUnitWrapper, Tracks, TrackDataBlobWrapper


class Gani2Writer:
    """Writer for GANI2 animation data.
    
    This class mirrors the structure of Gani2Reader and handles writing
    GANI animation files with the same hierarchical organization:
    - write_gani: Write complete GANI file (top-level)
    - write_all_tracks: Write all tracks and create TrackMiniHeader
    - write_track: Write single track with all segments
    - write_segment: Write single segment with keyframes
    - write_keyframes: Write keyframe data for a segment
    """

    def write_gani(self, filepath: str, gani_tracks: List['TrackUnitWrapper'], layout_track: 'Tracks', 
                   frame_count: int, frame_rate: int = 60, params: List[tuple] = None,
                   unit_flags_per_file: List[int] = None, segment_bit_sizes_per_file: List[int] = None) -> None:
        """Write a GANI animation file.
        
        This is the top-level write function, equivalent to Gani2Reader.read_gani().
        
        Args:
            filepath: Path where the GANI file should be written
            gani_tracks: List of GaniTrack objects containing animation data
            layout_track: Tracks object containing track structure/layout
            frame_count: Total number of frames in the animation
            frame_rate: Animation frame rate (default 60 fps)
            params: Optional list of (name, value) parameter tuples
        """
        # Write to buffer
        buffer = io.BytesIO()
        self.write_gani_to_buffer(buffer, gani_tracks, layout_track, frame_count, frame_rate, params,
                                  unit_flags_per_file=unit_flags_per_file,
                                  segment_bit_sizes_per_file=segment_bit_sizes_per_file)
        
        # Write to file
        Debug.log("    Writing to file...")
        with open(filepath, 'wb') as f:
            f.write(buffer.getvalue())
        
        Debug.log(f"    GANI write complete: {len(buffer.getvalue())} bytes")
    
    def write_gani_to_buffer(self, buffer, gani_tracks: List['TrackUnitWrapper'], layout_track: 'Tracks', 
                             frame_count: int, frame_rate: int = 60, params: List[tuple] = None,
                             unit_flags_per_file: List[int] = None, segment_bit_sizes_per_file: List[int] = None) -> None:
        """Write GANI animation data to a buffer.
        
        This is the core write function that writes to a BytesIO buffer.
        
        Args:
            buffer: BytesIO buffer to write to
            gani_tracks: List of GaniTrack objects containing animation data
            layout_track: Tracks object containing track structure/layout
            frame_count: Total number of frames in the animation
            frame_rate: Animation frame rate (default 60 fps)
            params: Optional list of (name, value) parameter tuples
        """
        Debug.log("  Writing GANI data:")
        Debug.log(f"    Track count: {len(gani_tracks)}")
        Debug.log(f"    Frame count: {frame_count}")
        Debug.log(f"    Frame rate: {frame_rate}")
        
        if not gani_tracks:
            Debug.log("    Warning: No tracks to write")
            return
        
        # Initialize params if not provided
        if params is None:
            params = []
        
        # Calculate track count and segment count from layout
        track_count = layout_track.header.unit_count
        total_segment_count = layout_track.header.segment_count
        
        Debug.log(f"    Segment count: {total_segment_count}")
        
        # Collect unit flags from tracks. Prefer per-file flags if provided, otherwise fall back to layout defaults
        unit_flags = []
        if unit_flags_per_file is not None:
            # Use provided per-file unit flags list
            unit_flags = list(unit_flags_per_file)
            # Ensure we have at least 'track_count' entries
            if len(unit_flags) < track_count:
                unit_flags.extend([0] * (track_count - len(unit_flags)))
        else:
            for i in range(track_count):
                if i < len(layout_track.track_units):
                    unit_flags.append(layout_track.track_units[i].unit_flags)
                else:
                    # Default flags if not specified
                    unit_flags.append(0)
        
        # Create placeholder segment_headers (will be populated with actual data later)
        if segment_bit_sizes_per_file is not None:
            # Use provided per-file component bit sizes, pad to total_segment_count
            placeholder_segment_headers = []
            for i in range(total_segment_count):
                bit_size = segment_bit_sizes_per_file[i] if i < len(segment_bit_sizes_per_file) else 0
                placeholder_segment_headers.append(Gani2TrackData(component_bit_size=bit_size, data_offset=0))
        else:
            placeholder_segment_headers = [Gani2TrackData(component_bit_size=0, data_offset=0) 
                                           for _ in range(total_segment_count)]
        
        # Create TrackMiniHeader
        track_mini_header = TrackMiniHeader(
            frame_count=frame_count,
            param_count=len(params),
            params=params,
            unit_flags=unit_flags,
            segment_headers=placeholder_segment_headers
        )
        
        # Write TrackMiniHeader placeholder
        header_start = buffer.tell()
        track_mini_header.write(buffer)
        
        # Write all tracks and get Gani2TrackData entries
        Debug.log("    Writing tracks...")
        all_gani2_entries, _ = self.write_all_tracks_to_buffer(
            buffer, gani_tracks, layout_track, header_start, track_mini_header, track_count
        )
        
        # Update TrackMiniHeader with segment_headers
        track_mini_header.segment_headers = all_gani2_entries
        
        # Go back and rewrite the header with complete segment_headers
        current_pos = buffer.tell()
        buffer.seek(header_start)
        track_mini_header.write(buffer)
        buffer.seek(current_pos)
    
    def write_all_tracks_to_buffer(self, buffer, gani_tracks: List['TrackUnitWrapper'], layout_track: 'Tracks',
                         header_start: int, track_mini_header: 'TrackMiniHeader', 
                         unit_count: int) -> tuple[List['Gani2TrackData'], bytes]:
        """Write all animation tracks to a GANI2 section.
        
        This mirrors Gani2Reader.read_all_tracks().
        
        Args:
            buffer: BytesIO buffer to write to
            gani_tracks: List of GaniTrack objects to write
            layout_track: Tracks object with layout information
            header_start: Position where TrackMiniHeader starts in buffer
            track_mini_header: TrackMiniHeader object (for calculating segment header positions)
            unit_count: Number of track units
            
        Returns:
            Tuple of (list of Gani2TrackData entries, keyframe data bytes)
        """
        # Calculate where segment headers start within the buffer
        segment_headers_start = header_start + track_mini_header.get_segment_headers_offset(unit_count)
        
        Debug.log("      Writing all tracks:")
        Debug.log(f"        Header start: 0x{header_start:X}")
        Debug.log(f"        Segment headers start: 0x{segment_headers_start:X}")
        
        # PASS 1: Collect all segments and write keyframe blobs to calculate sizes
        segments_data = []  # List of (component_bit_size, keyframe_blob)
        segment_idx_abs = 0
        
        for track_idx, gani_track in enumerate(gani_tracks):
            if track_idx >= len(layout_track.track_units):
                Debug.log(f"        Warning: Track {track_idx} has no corresponding layout unit")
                continue
            
            track_unit = layout_track.track_units[track_idx]
            Debug.log(f"        Processing track {track_idx}: '{gani_track.name}'")
            
            for segment_idx, keyframes_track in enumerate(gani_track.segments_track_data):
                if segment_idx >= len(track_unit.segments_data):
                    Debug.log(f"          Warning: Segment {segment_idx} ({segment_idx_abs}) has no corresponding track_data")
                    continue
                
                track_data = track_unit.segments_data[segment_idx]
                
                # Write this segment and collect its data
                # Use per-file unit flags from track_mini_header if available (per-file metadata overrides layout)
                unit_flags_for_track = track_mini_header.unit_flags[track_idx] if track_idx < len(track_mini_header.unit_flags) else track_unit.unit_flags
                # Determine expected component_bit_size from placeholder segment headers (per-file override) if available
                component_bit_size_override = None
                if segment_idx_abs < len(track_mini_header.segment_headers):
                    component_bit_size_override = track_mini_header.segment_headers[segment_idx_abs].component_bit_size

                segment_data = self.write_segment_to_bytes(
                    keyframes_track, track_data,
                    segment_idx, unit_flags_for_track, component_bit_size_override
                )
                
                segments_data.append(segment_data)
                segment_idx_abs += 1
        
        # PASS 2: Create Gani2TrackData entries with correct offsets
        segments_count = len(segments_data)
        
        # Keyframe data starts at current buffer position (after TrackMiniHeader including padding)
        keyframe_data_start = buffer.tell()
        
        segment_headers = []
        current_blob_offset = 0  # Offset from keyframe_data_start to current blob
        
        for seg_idx, (component_bit_size, segment_keyframes_blob_bytes) in enumerate(segments_data):
            # Position of this segment header in the buffer
            segment_header_position = segment_headers_start + (seg_idx * Gani2TrackData.ENTRY_SIZE)
            
            # Position of the keyframe blob for this segment
            keyframe_blob_position = keyframe_data_start + current_blob_offset
            
            # data_offset is relative to the segment header's position
            data_offset = keyframe_blob_position - segment_header_position
            
            segment_header = Gani2TrackData(
                component_bit_size=component_bit_size,
                data_offset=data_offset
            )
            segment_headers.append(segment_header)
            
            # Advance blob offset for next segment
            current_blob_offset += len(segment_keyframes_blob_bytes)
        
        # Write only keyframe data (segment headers and padding are written by TrackMiniHeader.write())
        segments_keyframes_blob_bytes = bytearray()
        for _, segment_keyframes_blob_bytes in segments_data:
            segments_keyframes_blob_bytes.extend(segment_keyframes_blob_bytes)
        buffer.write(segments_keyframes_blob_bytes)
        
        Debug.log(f"      Completed writing {len(gani_tracks)} track(s), {segments_count} segment(s)")
        return segment_headers, bytes(segments_keyframes_blob_bytes)
    
    def write_segment_to_bytes(self, keyframes_track: 'TrackDataBlobWrapper', track_data: 'TrackData', 
                     segment_idx: int, unit_flags_for_track: int,
                     component_bit_size_override: int = None) -> tuple:
        """Write a single segment and return its data.
        
        Args:
            keyframes_track: TrackDataBlobWrapper containing keyframes for this segment
            track_data: TrackData containing segment type and bit size info
            track_unit: TrackUnit containing unit flags
            segment_idx: Index of this segment within the track
            
        Returns:
            Tuple of (component_bit_size, keyframes_blob)
        """
        # Get actual component_bit_size from action if available; allow override
        component_bit_size = component_bit_size_override if component_bit_size_override is not None else track_data.component_bit_size
        
        Debug.log(f"          Segment {segment_idx}: type={track_data.td_type.name}, bits={component_bit_size}, frames={len(keyframes_track.data_blob.keyframes)}")
        
        # Write keyframes for this segment using AnimKeyframe.write_list_to_bytes
        from ..py_fox.fox_gani_types import AnimKeyframe
        keyframes_blob_bytes: bytes = AnimKeyframe.write_list_to_bytes(
            keyframes_track.data_blob.keyframes,
            track_data.td_type,
            component_bit_size,
            unit_flags_for_track
        )
        
        return (component_bit_size, keyframes_blob_bytes)


