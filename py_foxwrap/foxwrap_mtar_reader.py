"""
Reader for MTAR (Metal Gear Solid V animation) files.
"""
import io
from typing import List, Optional, Tuple

from ..py_fox.fox_gani_types import EvpHeader, TrackMiniHeader, TrackHeader
from ..py_fox.fox_mtar_types import (
    MtarHeader,
    MtarTableList2,
    MtarTableList,
)

from .foxwrap_gani_reader import Gani2Reader
from .foxwrap_misc import TrackUnitWrapper, Tracks
from .foxwrap_misc_import import CommonInfo


class MtarReader:
    def __init__(self, filepath: str) -> None:
        self.filepath: str = filepath
        self.gani_reader: Gani2Reader = Gani2Reader()
        self.common_info: Optional[CommonInfo] = None
        self.motion_tracks = None
        self.motion_events = None

    def read_all_tracks(self) -> Tuple[List[List[TrackUnitWrapper]], List[List[TrackUnitWrapper]], List[Optional[EvpHeader]], List[TrackMiniHeader], List[Optional[Tracks]], List[MtarTableList2], List[Optional[TrackHeader]]]:
        """Read all animation tracks from the MTAR file.
        
        Returns:
            Tuple containing:
            - all_gani_tracks: List of GaniTrack objects for each file
            - all_motion_point_gani_tracks: List of motion point GaniTrack objects for each file
            - all_motion_events: List of event headers for each file
            - all_track_mini_headers: List of TrackMiniHeader objects for each file (contains segment_headers with component_bit_size for main tracks)
            - all_motion_point_layouts: List of Tracks objects for each file (contains track units with segment data for motion points)
            - all_file_headers: List of MtarTableList2 objects for each file (contains path hash)
            - all_motion_point_track_headers: List of TrackHeader objects for each file (contains motion point track header metadata)
        """
        # Read the entire file into memory and create a buffered reader
        with open(self.filepath, 'rb') as f:
            file_data = f.read()

        br = io.BytesIO(file_data)

        # Read header from the start
        br.seek(0)
        header = MtarHeader.read(br)

        # Use size constants defined on the dataclasses
        MTAR_HEADER_SIZE = MtarHeader.SIZE

        all_gani_tracks: List[List[TrackUnitWrapper]] = []
        all_motion_point_gani_tracks: List[List[TrackUnitWrapper]] = []
        all_motion_events: List[Optional[EvpHeader]] = []
        all_track_mini_headers: List[TrackMiniHeader] = []
        all_motion_point_layouts: List[Optional[Tracks]] = []
        all_file_headers: List[MtarTableList2] = []
        all_motion_point_track_headers: List[Optional[TrackHeader]] = []

        # Read CommonInfo if present
        if header.common_info_offset != 0:
            br_common = io.BytesIO(file_data)
            self.common_info = CommonInfo.read(br_common, header)

        # Get appropriate file header size based on format version
        file_header_size = (MtarTableList2.SIZE if header.flags & 0x1000 else MtarTableList.SIZE)

        # Process each file table entry
        for file_index in range(header.file_count):
            file_header_offset = MTAR_HEADER_SIZE + file_index * file_header_size
            br.seek(file_header_offset)
            file_header: MtarTableList2 = MtarTableList2.read(br) if header.flags & 0x1000 else MtarTableList.read(br)
            is_new_format: bool = header.flags & 0x1000 and isinstance(file_header, MtarTableList2)
            
            gani_tracks, motion_point_gani_tracks, motion_events, track_mini_header, motion_point_layout, motion_point_track_header = self.gani_reader.read_gani(
                file_data=file_data, 
                layout_track=self.common_info.layout_track, 
                file_header=file_header, 
                track_count=header.track_count, 
                is_new_format=is_new_format)
            
            all_gani_tracks.append(gani_tracks)
            all_motion_point_gani_tracks.append(motion_point_gani_tracks)
            all_motion_events.append(motion_events)
            all_track_mini_headers.append(track_mini_header)
            all_motion_point_layouts.append(motion_point_layout)
            all_file_headers.append(file_header)
            all_motion_point_track_headers.append(motion_point_track_header)

        return all_gani_tracks, all_motion_point_gani_tracks, all_motion_events, all_track_mini_headers, all_motion_point_layouts, all_file_headers, all_motion_point_track_headers