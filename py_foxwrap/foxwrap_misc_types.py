from dataclasses import dataclass
from typing import BinaryIO, List, Optional

from ..py_utilities.utilities_binary_write import write_padding, align_length

from ..py_fox.fox_gani_types import TrackHeader, TrackUnit, TrackUnitFlags, TrackData, TrackDataBlob, AnimKeyframe
from ..py_fox.fox_frig_types import RigUnitType


@dataclass
class Tracks:
    """Track layout structure containing header and track units."""
    header : TrackHeader
    track_units : list[TrackUnit]

    @classmethod
    def read(cls, br: BinaryIO, file_data: Optional[bytes] = None, read_data_blobs: bool = False, endian: str = '<') -> "Tracks":
        """Read Tracks structure (TrackHeader + TrackUnits).
        
        Args:
            br: Binary stream positioned at the start of the TrackHeader
            file_data: Optional full file buffer (required if read_data_blobs=True)
            read_data_blobs: If True, read TrackDataBlob keyframes into TrackData.data_blob
                            If False, TrackData.data_blob remains None (for layout tracks)
            endian: Endianness marker ('<' for LE, '>' for BE)
        
        Returns:
            Tracks object with header and track units
        """
        read_start: int = br.tell()
        header = TrackHeader.read(br, endian)

        track_units: List[TrackUnit] = []
        for unit_index in range(header.unit_count):
            br.seek(read_start + header.unit_offsets[unit_index])
            track_unit = TrackUnit.read(br, endian)
            
            # Optionally read data blobs for this track unit
            if read_data_blobs and file_data is not None:
                cls._read_track_data_blobs(
                    track_unit,
                    file_data,
                    read_start,
                    header.unit_offsets[unit_index],
                    header.frame_count
                )
            
            track_units.append(track_unit)

        return cls(
            header=header,
            track_units=track_units,
        )
    
    @staticmethod
    def _read_track_data_blobs(track_unit: TrackUnit,
                               file_data: bytes,
                               base_offset: int,
                               unit_offset: int,
                               frame_count: int
                               ) -> None:
        """Read TrackDataBlob keyframes for each segment in a TrackUnit.
        
        This method populates the data_blob field in each TrackData entry.
        The data_offset in TrackData is relative to the TrackData entry's position in the file.
        
        Args:
            track_unit: TrackUnit whose TrackData entries need blob data
            file_data: Full file buffer
            base_offset: Absolute position where TrackHeader starts
            unit_offset: Offset from base_offset to this TrackUnit
            frame_count: Number of frames (from TrackHeader)
        """
        
        
        # Calculate where this TrackUnit starts
        track_unit_offset = base_offset + unit_offset
        
        # TrackUnit base: name(4) + segment_count(1) + unit_flags(1) + padding(2) = 8 bytes
        track_data_start = track_unit_offset + 8
        
        # Read data blob for each segment
        for segment_index, track_data in enumerate(track_unit.segments_data):
            if track_data.data_offset == 0:
                continue  # No data for this segment
            
            # Calculate absolute position of this TrackData entry
            track_data_entry_offset = track_data_start + (segment_index * TrackData.ENTRY_SIZE)
            
            # Calculate absolute offset to the blob: TrackData position + relative offset
            absolute_data_offset = track_data_entry_offset + track_data.data_offset
            
            # Use TrackDataBlob.read_keyframes() to get keyframes list
            keyframes = TrackDataBlob.read_keyframes(
                file_data=file_data,
                data_offset=absolute_data_offset,
                segment_type=track_data.td_type,
                component_bit_size=track_data.component_bit_size,
                unit_flags=track_unit.unit_flags,
                frame_count=frame_count
            )
            
            # Convert to TrackDataBlob instance and store in TrackData
            track_data.data_blob = TrackDataBlob.from_keyframes(
                segment_type=track_data.td_type,
                component_bit_size=track_data.component_bit_size,
                is_static=(track_unit.unit_flags & TrackUnitFlags.IS_STATIC) != 0,
                keyframes=keyframes,
            )
    
    def write(self, bw: BinaryIO, write_data_blobs: bool = False) -> None:
        """Write Tracks structure to binary stream.
        
        This writes the complete track structure including header and all track units.
        Optionally writes the actual keyframe data blobs if write_data_blobs=True.
        
        Args:
            bw: Binary stream to write to
            write_data_blobs: If True, write keyframe data blobs and calculate data_offset values.
                            If False (default), only write structure (for layout tracks).
        """

        write_start = bw.tell()
        
        # PASS 1: Collect all data blobs and calculate their sizes (if writing blobs)
        blob_sizes = []
        blob_data = []
        if write_data_blobs:
            for track_unit in self.track_units:
                for track_data in track_unit.segments_data:
                    if track_data.data_blob is not None and len(track_data.data_blob) > 0:
                        # Write keyframes to bytes using AnimKeyframe.write_list_to_bytes
                        keyframes_bytes = AnimKeyframe.write_list_to_bytes(
                            keyframes=track_data.data_blob,
                            track_type=track_data.td_type,
                            component_bit_size=track_data.component_bit_size,
                            unit_flags=track_unit.unit_flags
                        )
                        blob_sizes.append(len(keyframes_bytes))
                        blob_data.append(keyframes_bytes)
                    else:
                        blob_sizes.append(0)
                        blob_data.append(b'')
        
        # Calculate header size (needed for offset calculations)
        # Header: BASE_SIZE + unit_offsets array + 12 bytes padding + align to 8
        header_and_offsets = TrackHeader.BASE_SIZE + (self.header.unit_count * 4)
        after_padding = header_and_offsets + 12
        # Align to 8 bytes: round up to next multiple of 8
        header_size = align_length(after_padding, 8)
        
        # Calculate unit offsets if not already set
        if not self.header.unit_offsets or len(self.header.unit_offsets) != len(self.track_units):
            # Calculate offsets for each track unit
            current_offset = header_size
            unit_offsets = []
            
            for track_unit in self.track_units:
                unit_offsets.append(current_offset)
                # Each TrackUnit: base (8 bytes) + track_data entries (8 bytes each)
                unit_size = TrackUnit.BASE_SIZE + (track_unit.segment_count * 8)
                current_offset += unit_size
            
            self.header.unit_offsets = unit_offsets
        
        # PASS 2: Calculate data_offset values for each TrackData entry (if writing blobs)
        if write_data_blobs:
            # Calculate where data blobs will start (after all structure)
            blobs_start_offset = header_size
            for track_unit in self.track_units:
                blobs_start_offset += TrackUnit.BASE_SIZE + (track_unit.segment_count * TrackData.ENTRY_SIZE)
            
            # Update data_offset in each TrackData entry
            blob_idx = 0
            current_blob_offset = blobs_start_offset
            for track_unit in self.track_units:
                for track_data in track_unit.segments_data:
                    if blob_sizes[blob_idx] > 0:
                        # Calculate offset relative to this TrackData entry's position
                        # Find this TrackData entry's absolute offset
                        track_unit_idx = self.track_units.index(track_unit)
                        track_data_idx = track_unit.segments_data.index(track_data)
                        track_data_entry_offset = self.header.unit_offsets[track_unit_idx] + TrackUnit.BASE_SIZE + (track_data_idx * TrackData.ENTRY_SIZE)
                        
                        # data_offset is relative to the TrackData entry
                        track_data.data_offset = current_blob_offset - track_data_entry_offset
                        current_blob_offset += blob_sizes[blob_idx]
                    else:
                        track_data.data_offset = 0
                    blob_idx += 1
        
        # Write header
        self.header.write(bw)
        
        # Write each track unit at its designated offset
        for i, track_unit in enumerate(self.track_units):
            expected_offset = write_start + self.header.unit_offsets[i]
            current_pos = bw.tell()
            
            # Pad to expected offset if needed
            if current_pos < expected_offset:
                write_padding(bw, expected_offset - current_pos)
            
            track_unit.write(bw)
        
        # PASS 3: Write data blobs (if enabled)
        if write_data_blobs:
            for blob_bytes in blob_data:
                if blob_bytes:
                    bw.write(blob_bytes)


@dataclass
class TrackDataBlobWrapper:
    data_blob: TrackDataBlob
    name: str
    segment_index: int
    rotation_offset: Optional[List[dict]] = None
    rotation_axis_map: Optional[list] = None
    map_r_rest_pose: Optional[dict] = None
    space_r: Optional[dict] = None
    as_ik_up: Optional[dict] = None


@dataclass
class TrackUnitWrapper:
    name: str
    segments_track_data: List[TrackDataBlobWrapper]
    unit_flags: List[TrackUnitFlags]
    rig_unit_type: Optional[RigUnitType] = None

