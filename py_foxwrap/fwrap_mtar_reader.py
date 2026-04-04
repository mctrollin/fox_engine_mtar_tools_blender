"""
Reader for MTAR (Metal Gear Solid V animation) files.
"""
import io
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..py_core.core_logging import Debug

from ..py_fox.fox_mtar_types import MtarHeader, MtarTableList2, MtarTableList, is_new_mtar_format
from ..py_fox.fox_foxdata_types import FoxDataHeader

from .fwrap_gani2_reader import Gani2Reader
from .fwrap_gani1_reader import GaniReader
from .fwrap_gani_track_types import Tracks
from .fwrap_mtar_import_types import CommonInfo, GaniImportData


@dataclass
class MtarHeaderInfo:
    """Header metadata for an MTAR file.
    
    Attributes:
        version: MTAR version number from header
        file_count: Number of files contained in the MTAR
        total_size_mb: Size of the MTAR file in megabytes (approx)
        has_common_info: Whether the file contains a CommonInfo block
        is_new_format: ``True`` if the MTAR uses the new GANI2/CommonInfo format,
            ``False`` for old FoxData GANIs.
        gani_version: Optional version of the first GANI file inside the MTAR.
            For old-format (FoxData) GANIs this reads the FoxData header version.
            For new-format (GANI2) this value is ``None`` (no explicit version stored).
    """
    version: int
    file_count: int
    total_size_mb: float
    has_common_info: bool
    is_new_format: bool = False
    gani_version: Optional[int] = None
    
    def __str__(self) -> str:
        """Human-readable summary."""
        base = (f"MTAR v{self.version}: {self.file_count} files, "
                f"{self.total_size_mb:.2f} MB")
        fmt = "new (GANI2)" if self.is_new_format else "old (FoxData)"
        base += f" [{fmt}]"
        if self.gani_version is not None:
            base += f" (GANI v{self.gani_version})"
        return base


class MtarReader:
    def __init__(self, filepath: str) -> None:
        self.filepath: str = filepath
        self.gani2_reader: Gani2Reader = Gani2Reader()
        self.gani_reader: GaniReader = GaniReader()
        self.common_info: Optional[CommonInfo] = None
        self.layout_track: Optional[Tracks] = None  # Set for both old and new formats
        self.all_gani_layout_tracks: List[Tracks] = []  # Preserves ALL per-GANI layout_track objects before winner selection
        self.is_new_format: bool = False  # Determined after reading header
        self.mtar_version: int = 0  # Version from MTAR header
        self.mtar_flags: int = 0    # Flags from MTAR header
        self.motion_tracks = None
        self.motion_events = None

    def get_header_info(self) -> MtarHeaderInfo:
        """Get MTAR header metadata without loading animation data.
        
        This reads only the top-level MTAR header and, if possible, peeks into
        the first GANI file to obtain its version number.  Reading the GANI
        version requires accessing the first file table entry and then parsing
        either a FoxDataHeader (old format) or simply noting the lack of a
        version for new-format GANI2 files.
        
        Returns:
            MtarHeaderInfo with version, file count, size, etc.
        """
        with open(self.filepath, 'rb') as f:
            # Read MTAR header
            header = MtarHeader.read(f)
            
            # Get file size
            f.seek(0, 2)  # Seek to end
            file_size = f.tell()

            gani_version: Optional[int] = None
            if header.file_count > 0:
                # seek to first file table entry
                f.seek(MtarHeader.SIZE)
                try:
                    if is_new_mtar_format(header.flags):
                        # new-format: table entries are 32 bytes
                        table = MtarTableList2.read(f)
                        # GANI2 files do not store a version number we can easily
                        # expose here; leave as None to indicate "unknown".
                    else:
                        table = MtarTableList.read(f)
                        # old-format: read FoxDataHeader to obtain GANI version
                        try:
                            f.seek(table.tracks_offset)
                            fox_header, _ = FoxDataHeader.read(f)
                            gani_version = fox_header.version
                        except Exception:
                            # ignore any errors - version remains None
                            gani_version = None
                except Exception:
                    # if anything goes wrong reading the table entry, just skip
                    gani_version = None

            return MtarHeaderInfo(
                version=header.version,
                file_count=header.file_count,
                total_size_mb=file_size / (1024 * 1024),
                has_common_info=header.common_info_offset > 0,
                is_new_format=is_new_mtar_format(header.flags),
                gani_version=gani_version,
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
            # Calculate based on format version
            entry_size = MtarTableList2.SIZE if is_new_mtar_format(header.flags) else MtarTableList.SIZE
            min_size = MtarHeader.SIZE + (header.file_count * entry_size)
            if file_size < min_size:
                return False, f"File too small: {file_size} bytes (needs at least {min_size})"
            
            # Check CommonInfo offset if present
            if header.common_info_offset > 0:
                if header.common_info_offset >= file_size:
                    return False, f"CommonInfo offset {header.common_info_offset} exceeds file size {file_size}"
            
            return True, None
            
        except Exception as e:
            return False, f"Error reading MTAR header: {str(e)}"

    def read_all_ganies(self) -> List[GaniImportData]:
        """Read all animation tracks from the MTAR file.
        
        The result is simply a list of :class:`GaniImportData` objects, one per
        animation (GANI) contained in the MTAR.  This mirrors the behaviour of
        :meth:`read_selected_ganis` but avoids the intermediate tuple unpacking.
        """
        # Read all GANIs using selective reading
        with open(self.filepath, 'rb') as f:
            header = MtarHeader.read(f)
            all_indices = list(range(header.file_count))
        
        results_dict = self.read_selected_ganis(all_indices)
        
        # Simply return the list of GaniImportData objects sorted by index
        return [results_dict[idx] for idx in sorted(results_dict.keys())
               ]

    def read_selected_ganis(self, gani_header_indices: List[int]) -> dict[int, GaniImportData]:
        """Read specific GANI files by header index from MTAR file.

        Uses the **h (header) index** — the zero-based position of the GANI entry
        in the MTAR file table (``MtarTableList`` / ``MtarTableList2`` array).
        This corresponds to the ``hN`` component in NLA strip/action names.

        .. warning::
            Do NOT pass the **d (data) index** here.  The ``dN`` value in strip
            names is a path-hash lookup slot and is unrelated to the file-table
            position used by this method.

        This method reads entire GANI chunks into memory for requested indices,
        providing performance improvement for selective import (reads 510KB chunks
        vs 50MB full file when importing single animations).

        Args:
            gani_header_indices: List of zero-based MTAR file-table (h) indices to read.

        Returns:
            Dictionary mapping each h index to a ``GaniImportData`` object containing
            all the parsed animation data along with an optional ``file_header``
            pointing back to the enclosing MTAR table entry.

        Raises:
            IndexError: If any index is out of range
            ValueError: If gani_header_indices is empty
        """
        if not gani_header_indices:
            raise ValueError("gani_header_indices cannot be empty")
        
        # Read entire file into memory
        with open(self.filepath, 'rb') as f:
            file_data = f.read()
        
        br = io.BytesIO(file_data)
        
        # Read header
        header = MtarHeader.read(br)
        self.is_new_format = is_new_mtar_format(header.flags)
        self.mtar_version = header.version
        self.mtar_flags = header.flags
        
        # Validate indices
        max_index = header.file_count
        for idx in gani_header_indices:
            if idx < 0 or idx >= max_index:
                raise IndexError(f"GANI header index {idx} out of range (file has {max_index} GANIs)")
        
        # Read CommonInfo if present (new format only; old format embeds layout in each GANI)
        if header.common_info_offset != 0:
            br.seek(header.common_info_offset)
            self.common_info = CommonInfo.read(br, header)
            if self.is_new_format and self.common_info and self.common_info.layout_track:
                self.layout_track = self.common_info.layout_track
        
        # Guard: new-format MTAR must have CommonInfo
        if self.is_new_format and self.common_info is None:
            raise ValueError(
                f"New-format MTAR (flags=0x{header.flags:04X}) has no CommonInfo — "
                "cannot read without shared layout track"
            )
        
        # Get file header size based on format version
        file_header_size = (MtarTableList2.SIZE if self.is_new_format else MtarTableList.SIZE)
        
        # Read selected GANIs
        results = {}
        for gani_index in gani_header_indices:
            # Read file header for this GANI
            file_header_offset = MtarHeader.SIZE + gani_index * file_header_size
            br.seek(file_header_offset)
            file_header = MtarTableList2.read(br) if self.is_new_format else MtarTableList.read(br)
            
            # Dispatch to appropriate reader
            if self.is_new_format:
                # New format: use Gani2Reader with shared CommonInfo layout
                gani_tracks, motion_point_gani_tracks, motion_events, track_mini_header, motion_point_layout, motion_point_track_header = self.gani2_reader.read_gani(
                    file_data=file_data,
                    layout_track=self.common_info.layout_track,
                    file_header=file_header,
                    track_count=header.track_count,
                    is_new_format=True,
                    skeleton_list=self.common_info.skeleton_list if self.common_info else None,
                )
                import_data = GaniImportData.from_gani2(
                    gani_bone_tracks=gani_tracks,
                    gani_mtp_tracks=motion_point_gani_tracks,
                    gani_events=motion_events,
                    gani_layout_track=self.common_info.layout_track,
                    gani_track_mini_header=track_mini_header,
                    gani_motion_point_layout=motion_point_layout,
                    gani_motion_point_track_header=motion_point_track_header,
                    file_header=file_header,
                    gani_skeleton_list=self.common_info.skeleton_list if self.common_info else None,
                )
            else:
                # Old format: use GaniReader with embedded FoxData layout
                import_data = self.gani_reader.read_gani(
                    file_data=file_data,
                    gani_start=file_header.tracks_offset
                )
                # attach header after-the-fact
                import_data.file_header = file_header

                # Cache the layout track with the most segments across all old-format GANIs.
                # Different GANIs may have different segment counts for the same track
                # (e.g. GANI[0] may have 23 total segments while GANI[1-6] have 24).
                # Using the max-segment layout ensures the layout action captures all
                # possible segment types; the FCurve-presence check in the exporter
                # will then correctly filter out segments absent from individual GANIs.
                # ALWAYS append (even None) to maintain 1:1 index correspondence with GANI indices
                candidate = import_data.gani_layout_track
                self.all_gani_layout_tracks.append(candidate)  # Preserve all per-GANI layouts (including None)
                if candidate is not None:
                    if (self.layout_track is None or
                            candidate.header.segment_count > self.layout_track.header.segment_count):
                        self.layout_track = candidate
                        Debug.log(
                            f"  [old-format] Updated layout track from GANI index {gani_index}: "
                            f"segment_count={candidate.header.segment_count}"
                        )
            
            # store the GaniImportData object directly
            results[gani_index] = import_data

        return results