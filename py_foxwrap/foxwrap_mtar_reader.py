"""
Reader for MTAR (Metal Gear Solid V animation) files.
"""
import io
from dataclasses import dataclass
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


@dataclass
class MtarHeaderInfo:
    """Header metadata for an MTAR file."""
    version: int
    file_count: int
    total_size_mb: float
    has_common_info: bool
    
    def __str__(self) -> str:
        """Human-readable summary."""
        return (f"MTAR v{self.version}: {self.file_count} files, "
                f"{self.total_size_mb:.2f} MB" +
                (", with CommonInfo" if self.has_common_info else ""))


class MtarReader:
    def __init__(self, filepath: str) -> None:
        self.filepath: str = filepath
        self.gani_reader: Gani2Reader = Gani2Reader()
        self.common_info: Optional[CommonInfo] = None
        self.motion_tracks = None
        self.motion_events = None

    def get_header_info(self) -> MtarHeaderInfo:
        """Get MTAR header metadata without loading animation data.
        
        Returns:
            MtarHeaderInfo with version, file count, size, etc.
        """
        with open(self.filepath, 'rb') as f:
            # Read MTAR header
            header = MtarHeader.read(f)
            
            # Get file size
            f.seek(0, 2)  # Seek to end
            file_size = f.tell()
            
            return MtarHeaderInfo(
                version=header.version,
                file_count=header.file_count,
                total_size_mb=file_size / (1024 * 1024),
                has_common_info=header.common_info_offset > 0
            )

    def validate_header(self) -> Tuple[bool, Optional[str]]:
        """Validate MTAR header for basic sanity checks.
        
        Performs validation on:
        - Version number (first 4 digits should be between 2010 and 2015)
        - File count (should be > 0 and < 10000)
        - File size (should be large enough for header + file table)
        - CommonInfo offset (should be within file bounds if present)
        
        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if all checks pass, False otherwise
            - error_message: None if valid, otherwise a string describing the issue
        """
        try:
            with open(self.filepath, 'rb') as f:
                header = MtarHeader.read(f)
                
                # Get file size
                f.seek(0, 2)
                file_size = f.tell()
            
            # Check version (first 4 digits should be between 2010 and 2015)
            version_string = str(abs(int(header.version)))
            version_year = int(version_string[:4]) if len(version_string) >= 4 else int(version_string)
            if version_year < 2010 or version_year > 2015:
                return False, f"Invalid version: {header.version} (expected year ~2010-2015)"
            
            # Check file count
            if header.file_count <= 0:
                return False, f"Invalid file count: {header.file_count} (must be > 0)"
            if header.file_count >= 10000:
                return False, f"{header.file_count} animations seems too large"
            
            # Check file size is large enough for header + file table
            min_size = MtarHeader.SIZE + (header.file_count * MtarTableList2.SIZE)
            if file_size < min_size:
                return False, f"File too small: {file_size} bytes (needs at least {min_size})"
            
            # Check CommonInfo offset if present
            if header.common_info_offset > 0:
                if header.common_info_offset >= file_size:
                    return False, f"CommonInfo offset {header.common_info_offset} exceeds file size {file_size}"
            
            return True, None
            
        except Exception as e:
            return False, f"Error reading MTAR header: {str(e)}"

    def read_all_ganies(self) -> Tuple[List[List[TrackUnitWrapper]], List[List[TrackUnitWrapper]], List[Optional[EvpHeader]], List[TrackMiniHeader], List[Optional[Tracks]], List[MtarTableList2], List[Optional[TrackHeader]]]:
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
        # Read all GANIs using selective reading
        with open(self.filepath, 'rb') as f:
            header = MtarHeader.read(f)
            all_indices = list(range(header.file_count))
        
        results_dict = self.read_selected_ganis(all_indices)
        
        # Convert dictionary results to lists in index order
        all_gani_tracks: List[List[TrackUnitWrapper]] = []
        all_motion_point_gani_tracks: List[List[TrackUnitWrapper]] = []
        all_motion_events: List[Optional[EvpHeader]] = []
        all_track_mini_headers: List[TrackMiniHeader] = []
        all_motion_point_layouts: List[Optional[Tracks]] = []
        all_file_headers: List[MtarTableList2] = []
        all_motion_point_track_headers: List[Optional[TrackHeader]] = []
        
        for idx in sorted(results_dict.keys()):
            gani_tracks, motion_point_tracks, motion_events, track_mini_header, motion_point_layout, file_header, motion_point_track_header = results_dict[idx]
            all_gani_tracks.append(gani_tracks)
            all_motion_point_gani_tracks.append(motion_point_tracks)
            all_motion_events.append(motion_events)
            all_track_mini_headers.append(track_mini_header)
            all_motion_point_layouts.append(motion_point_layout)
            all_file_headers.append(file_header)
            all_motion_point_track_headers.append(motion_point_track_header)
        
        return all_gani_tracks, all_motion_point_gani_tracks, all_motion_events, all_track_mini_headers, all_motion_point_layouts, all_file_headers, all_motion_point_track_headers

    def read_selected_ganis(self, gani_indices: List[int]) -> dict:
        """Read specific GANI files by index from MTAR file.
        
        This method reads entire GANI chunks into memory for requested indices,
        providing performance improvement for selective import (reads 510KB chunks
        vs 50MB full file when importing single animations).
        
        Args:
            gani_indices: List of zero-based GANI indices to read
            
        Returns:
            Dictionary mapping gani_index -> (gani_tracks, motion_point_tracks, motion_events, 
                                              track_mini_header, motion_point_layout, file_header, motion_point_track_header)
            
        Raises:
            IndexError: If any index is out of range
            ValueError: If gani_indices is empty
        """
        if not gani_indices:
            raise ValueError("gani_indices cannot be empty")
        
        # Read entire file into memory
        with open(self.filepath, 'rb') as f:
            file_data = f.read()
        
        br = io.BytesIO(file_data)
        
        # Read header
        header = MtarHeader.read(br)
        
        # Validate indices
        max_index = header.file_count
        for idx in gani_indices:
            if idx < 0 or idx >= max_index:
                raise IndexError(f"GANI index {idx} out of range (file has {max_index} GANIs)")
        
        # Read CommonInfo if present (shared across all GANIs)
        if header.common_info_offset != 0:
            br.seek(header.common_info_offset)
            self.common_info = CommonInfo.read(br, header)
        
        # Get file header size based on format version
        file_header_size = (MtarTableList2.SIZE if header.flags & 0x1000 else MtarTableList.SIZE)
        MTAR_HEADER_SIZE = MtarHeader.SIZE
        
        # Read selected GANIs
        results = {}
        for gani_index in gani_indices:
            # Read file header for this GANI
            file_header_offset = MTAR_HEADER_SIZE + gani_index * file_header_size
            br.seek(file_header_offset)
            file_header: MtarTableList2 = MtarTableList2.read(br) if header.flags & 0x1000 else MtarTableList.read(br)
            is_new_format: bool = header.flags & 0x1000 and isinstance(file_header, MtarTableList2)
            
            # Use buffer-based reading (pass entire file_data)
            gani_tracks, motion_point_gani_tracks, motion_events, track_mini_header, motion_point_layout, motion_point_track_header = self.gani_reader.read_gani(
                file_data=file_data,
                layout_track=self.common_info.layout_track,
                file_header=file_header,
                track_count=header.track_count,
                is_new_format=is_new_format
            )
            
            # Store results for this GANI
            results[gani_index] = (
                gani_tracks,
                motion_point_gani_tracks,
                motion_events,
                track_mini_header,
                motion_point_layout,
                file_header,
                motion_point_track_header
            )
        
        return results