from dataclasses import dataclass
from typing import BinaryIO, List, Optional

from ..py_utilities.utilities_binary_write import write_padding, align_length

from ..py_fox.fox_gani_types import TrackHeader, TrackUnit, TrackUnitFlags, TrackData, TrackDataBlob, AnimKeyframe
from ..py_fox.fox_frig_types import RigUnitType


@dataclass
class Tracks:
    """Track layout structure containing header and track units."""
    header: TrackHeader
    track_units: List[TrackUnit]

    @classmethod
    def read(cls, br: BinaryIO, file_data: Optional[bytes] = None, read_data_blobs: bool = False, endian: str = '<') -> 'Tracks':
        """Read Tracks structure (TrackHeader + TrackUnits)."""
        read_start: int = br.tell()
        header = TrackHeader.read(br, endian)

        track_units: List[TrackUnit] = []
        for unit_index in range(header.unit_count):
            br.seek(read_start + header.unit_offsets[unit_index])
            track_unit = TrackUnit.read(br, endian)

            if read_data_blobs and file_data is not None:
                cls._read_track_data_blobs(
                    track_unit,
                    file_data,
                    read_start,
                    header.unit_offsets[unit_index],
                    header.frame_count,
                )

            track_units.append(track_unit)

        return cls(header=header, track_units=track_units)

    @staticmethod
    def _read_track_data_blobs(track_unit: TrackUnit, file_data: bytes, base_offset: int, unit_offset: int, frame_count: int) -> None:
        """Read TrackDataBlob keyframes for each segment in a TrackUnit."""
        track_unit_offset = base_offset + unit_offset
        track_data_start = track_unit_offset + 8

        for segment_index, track_data in enumerate(track_unit.segments_data):
            if track_data.data_offset == 0:
                continue

            track_data_entry_offset = track_data_start + (segment_index * TrackData.ENTRY_SIZE)
            absolute_data_offset = track_data_entry_offset + track_data.data_offset

            keyframes = TrackDataBlob.read(
                file_data=file_data,
                data_offset=absolute_data_offset,
                segment_type=track_data.td_type,
                component_bit_size=track_data.component_bit_size,
                unit_flags=track_unit.unit_flags,
                frame_count=frame_count,
            )
            track_data.data_blob = keyframes

    def write(self, bw: BinaryIO, write_data_blobs: bool = False) -> None:
        """Write Tracks structure and optional data blobs."""
        write_start = bw.tell()

        blob_sizes = []
        blob_data = []
        if write_data_blobs:
            for track_unit in self.track_units:
                for track_data in track_unit.segments_data:
                    if track_data.data_blob is not None and len(track_data.data_blob) > 0:
                        keyframes_bytes = AnimKeyframe.write_list_to_bytes(
                            keyframes=track_data.data_blob,
                            track_type=track_data.td_type,
                            component_bit_size=track_data.component_bit_size,
                            unit_flags=track_unit.unit_flags,
                        )
                        blob_sizes.append(len(keyframes_bytes))
                        blob_data.append(keyframes_bytes)
                    else:
                        blob_sizes.append(0)
                        blob_data.append(b'')

        header_and_offsets = TrackHeader.BASE_SIZE + (self.header.unit_count * 4)
        after_padding = header_and_offsets + 12
        header_size = align_length(after_padding, 8)

        if not self.header.unit_offsets or len(self.header.unit_offsets) != len(self.track_units):
            current_offset = header_size
            unit_offsets = []
            for track_unit in self.track_units:
                unit_offsets.append(current_offset)
                unit_size = TrackUnit.BASE_SIZE + (track_unit.segment_count * 8)
                current_offset += unit_size
            self.header.unit_offsets = unit_offsets

        if write_data_blobs:
            blobs_start_offset = header_size
            for track_unit in self.track_units:
                blobs_start_offset += TrackUnit.BASE_SIZE + (track_unit.segment_count * TrackData.ENTRY_SIZE)

            blob_idx = 0
            current_blob_offset = blobs_start_offset
            for track_unit in self.track_units:
                for track_data in track_unit.segments_data:
                    if blob_sizes[blob_idx] > 0:
                        track_unit_idx = self.track_units.index(track_unit)
                        track_data_idx = track_unit.segments_data.index(track_data)
                        track_data_entry_offset = self.header.unit_offsets[track_unit_idx] + TrackUnit.BASE_SIZE + (track_data_idx * TrackData.ENTRY_SIZE)
                        track_data.data_offset = current_blob_offset - track_data_entry_offset
                        current_blob_offset += blob_sizes[blob_idx]
                    else:
                        track_data.data_offset = 0
                    blob_idx += 1

        self.header.write(bw)

        for i, track_unit in enumerate(self.track_units):
            expected_offset = write_start + self.header.unit_offsets[i]
            current_pos = bw.tell()
            if current_pos < expected_offset:
                write_padding(bw, expected_offset - current_pos)
            track_unit.write(bw)

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

