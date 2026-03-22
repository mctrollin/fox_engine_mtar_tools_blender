"""
MTAR file writer for Metal Gear Solid V.

This module handles writing MTAR (Motion Track Archive) files.
Mirrors the structure of MtarReader for symmetry.
"""

from typing import List, Optional
import io

import bpy

from ..py_core.core_logging import Debug

from ..py_utilities import util_binary_write, util_hashing

from ..py_fox import fox_gani_constants as gani_const
from ..py_fox import fox_mtar_constants as mtar_const
from ..py_fox.fox_mtar_types import MtarHeader, MtarTableList2, MtarTableList, MtarFlags, MtarMiniDataNode, MotionPointList2
from ..py_fox.fox_gani_enums import CommonInfoNodeType, TrackUnitFlags
from ..py_fox.fox_gani_types import TrackHeader, TrackUnit, TrackData
from ..py_fox.fox_misc_types import StrCode32

from .fwrap_misc_export_types import GaniExportData
from .fwrap_misc_types import Tracks, TrackUnitWrapper
from .fwrap_gani2_writer import Gani2Writer
from .fwrap_gani1_writer import GaniWriter
from . import fwrap_metadata
from ..py_tools import tools_hash_generator


def _is_valid_asset_path(s: str) -> bool:
    """Return True if s is a recognised Fox Engine asset path (starts with '/Assets/')."""
    return s.startswith('/Assets/')


class MtarWriter:
    """Writes MTAR format files.
    
    This class mirrors the structure of MtarReader and uses Gani2Writer
    for writing GANI sections, maintaining symmetry with the read path.
    """
    
    def __init__(self, filepath: str,
                 treat_hashes_as_names: bool = False,
                 export_custom_path_base: str = ""):
        """Initialize the MTAR writer.

        Args:
            filepath: Path where the MTAR file should be written
            treat_hashes_as_names: When True, raw hash strings in the action's mtar_const.TABL_PATH custom property are treated as path
                name components and combined with export_custom_path_base before re-hashing via
                the generator. Has no effect on valid /Assets/ paths (always hashed directly)
                or invalid non-/Assets/ paths (always combined with base regardless of this flag).
            export_custom_path_base: Base path prepended when constructing paths for unresolved
                items — raw hashes (when treat_hashes_as_names=True), invalid paths, and NLA
                source fallbacks. Example: "/Assets/tpp/"
        """
        self.filepath = filepath
        self.gani2_writer = Gani2Writer()
        self.gani_writer = GaniWriter()
        
        # Storage for GANI data to write  
        self.gani_data_list: List[GaniExportData] = []
        
        # MTAR metadata
        self.frame_rate: int = 60
        self.version: int = 201403250  # MTAR version of mgs5 tpp
        self.flags: int = MtarFlags.UseMini  # 0x1000 for new format, 0 for old format
        
        # Layout track (shared across all GANI files)
        self.layout_track: Optional['Tracks'] = None
        
        # Motion points data (shared across all GANI files)
        self.motion_points_list: Optional['MotionPointList2'] = None
        
        # Motion point header count (separate from CommonInfo count)
        # IMPORTANT: MTAR header count != CommonInfo count!
        # - MTAR header: max motion point units used across all GANIs (game engine requirement)
        # - CommonInfo: total number of motion point bone definitions
        # During import, MTAR header value is informational only; CommonInfo has the actual data.
        self.motion_point_header_count: int = 0
        
        # Custom path hashing settings
        self.treat_hashes_as_names = treat_hashes_as_names
        self.export_custom_path_base = export_custom_path_base
        
    def add_gani_data(self, gani_data: GaniExportData) -> None:
        """Add a GaniExportData object to be included in the MTAR.
        
        Args:
            gani_data: GaniExportData object containing animation data
        """
        self.gani_data_list.append(gani_data)
    
    def set_layout_track(self, layout_track: 'Tracks') -> None:
        """Set the layout track structure for the MTAR.
        
        Args:
            layout_track: Tracks object containing track structure/layout
        """
        self.layout_track = layout_track
    
    def set_motion_points_list(self, motion_points_list: Optional['MotionPointList2']) -> None:
        """Set the motion points list for the MTAR.
        
        This sets the CommonInfo motion points list (bone definitions).
        The count in this list represents the total number of motion point entries.
        
        Args:
            motion_points_list: MotionPointList2 object containing motion point definitions,
                              or None to write an empty motion points list
        """
        self.motion_points_list = motion_points_list
    
    def set_motion_point_header_count(self, count: int) -> None:
        """Set the motion point unit count for the MTAR header.
        
        This is SEPARATE from the CommonInfo motion points count!
        - Header count: max motion point units used across all GANIs (for game engine)
        - CommonInfo count: total number of motion point bone definitions (from motion_points_list)
        
        Args:
            count: Maximum motion point units used across all GANI files
        """
        self.motion_point_header_count = count
    
    def set_mtar_version(self, version: int, flags: int) -> None:
        """Set the MTAR version and format flags for the output file.
        
        Args:
            version: MTAR version (e.g., 201403250 for MGSV:TPP)
            flags: MTAR format flags (MtarFlags.UseMini for new format, 0 for old format)
        """
        self.version = version
        self.flags = flags
    
    @property
    def is_new_format(self) -> bool:
        """Return True if this MTAR uses new format (GANI2 with CommonInfo)."""
        return bool(self.flags & MtarFlags.UseMini)  # 0x1000
    
    def sort_file_table_by_hash(self, file_table_entries):
        """Sort file table entries by path hash in ascending order.
        
        The GANI data has already been written in Blender NLA strip order,
        and the offsets in these entries point to those locations.
        This sorting only reorders the directory (file table), not the data.
        
        Args:
            file_table_entries: List of MtarTableList2 entries with offsets already set
            
        Returns:
            Same list sorted by path hash (ascending)
        """
        return sorted(file_table_entries, key=lambda entry: entry.path)
    
    def _resolve_gani_path_string(self, gani_data: GaniExportData) -> str:
        """Determine the canonical path string for a GANI, mirroring compute_gani_path_hash logic.

        Returns paths with .gani extension for use in info files and path recording.

        - Raw hash + treat_hashes_as_names=True  → export_custom_path_base + hash_str + .gani
        - Raw hash + treat_hashes_as_names=False → raw hash string (unchanged, no extension needed)
        - Valid /Assets/ path                    → returned with .gani extension if not present
        - Invalid path (not hash, not /Assets/)  → export_custom_path_base + path + .gani
        - NLA source fallback                    → export_custom_path_base + track/strip + .gani
        """
        if gani_data.gani_path_hash is not None:
            # If path hash is explicitly set (e.g., imported reference MTAR), preserve it.
            return f"0x{gani_data.gani_path_hash:016X}.gani"

        action = getattr(gani_data.gani_tracks_data, 'action', None)
        source = gani_data.gani_tracks_data.source

        if action and mtar_const.TABL_PATH in action.keys():
            gani_path_val = str(action[mtar_const.TABL_PATH])
            if util_hashing.is_gani_path_a_hash(gani_path_val):
                if self.treat_hashes_as_names:
                    return f"{self.export_custom_path_base}{gani_path_val}.gani"
                return gani_path_val  # raw hash — use directly
            if _is_valid_asset_path(gani_path_val):
                # Valid /Assets/ path — ensure .gani extension
                return gani_path_val if gani_path_val.endswith('.gani') else f"{gani_path_val}.gani"
            # Invalid path — always prepend base and add .gani
            return f"{self.export_custom_path_base}{gani_path_val}.gani"

        # No gani_path — fall back to NLA source or action name
        if source and source.startswith('NLA Track'):
            parts = source.split('"')
            if len(parts) >= 4:
                track_name = parts[1]
                strip_name = parts[3]
                # NLA source is not a valid asset path — always prepend base and add .gani
                return f"{self.export_custom_path_base}{track_name}/{strip_name}.gani"

        # Fallback to action name with .gani extension
        action_name = action.name if action else gani_data.gani_name
        return action_name if action_name.endswith('.gani') else f"{action_name}.gani"

    def get_animation_name_for_gani(self, gani_data: GaniExportData) -> str:
        """Extract the animation name string from GaniExportData for the info file.

        Mirrors the path resolution logic of compute_gani_path_hash:
        - Valid asset path from gani_path → used directly.
        - Unresolved hash with custom_path_hashes → custom_path_base + hash_str.
        - NLA source (no gani_path) → [custom_path_base]track_name/strip_name.
        - Fallback → action name.

        Args:
            gani_data: GaniExportData object containing animation data

        Returns:
            Animation name string (e.g., "/Assets/mgo/motion/walk_idle" or "player2.0.gani")
        """
        return self._resolve_gani_path_string(gani_data)

    def compute_gani_path_hash_from_action(self, action: Optional[bpy.types.Action]) -> int:
        """Compute a GANI path hash directly from a Blender action object.

        This helper is used by the export pipeline when GaniExportData does not
        carry the action object. The caller should store the resulting hash in
        GaniExportData.path_hash for later MTAR file table generation.

        Args:
            action: Blender action potentially containing mtar_const.TABL_PATH

        Returns:
            64-bit GANI path hash

        Raises:
            ValueError: if action is missing mtar_const.TABL_PATH or lookup fails.
        """
        if not action or mtar_const.TABL_PATH not in action.keys():
            raise ValueError("Could not determine GANI path hash: action has no Path metadata")

        path_value = str(action[mtar_const.TABL_PATH])
        if util_hashing.is_gani_path_a_hash(path_value):
            if not self.treat_hashes_as_names:
                return util_hashing.parse_gani_hash_str(path_value)
            combined = f"{self.export_custom_path_base}{path_value}.gani"
            success, results, error = tools_hash_generator.hash_animation_name_from_blender_context(combined)
            if success and results.get('with_extension_dec'):
                try:
                    return util_hashing.parse_gani_hash_str(results['with_extension_dec'])
                except ValueError:
                    raise ValueError(f"Could not parse path hash from computed result: {results['with_extension_dec']}")
            raise ValueError(f"Could not compute hash for path '{path_value}': {error}")

        if _is_valid_asset_path(path_value):
            path_to_hash = path_value if path_value.endswith('.gani') else f"{path_value}.gani"
            success, results, error = tools_hash_generator.hash_animation_name_from_blender_context(path_to_hash)
            if success and results.get('with_extension_dec'):
                return util_hashing.parse_gani_hash_str(results['with_extension_dec'])
            raise ValueError(f"Could not compute hash for path '{path_to_hash}': {error}")

        combined = f"{self.export_custom_path_base}{path_value}.gani"
        success, results, error = tools_hash_generator.hash_animation_name_from_blender_context(combined)
        if success and results.get('with_extension_dec'):
            return util_hashing.parse_gani_hash_str(results['with_extension_dec'])
        raise ValueError(f"Could not compute hash for path '{combined}': {error}")

    def compute_gani_path_hash(self, gani_data: GaniExportData) -> int:
        """Compute the path hash for a GANI from the export data.

        Prefers explicit path_hash if set. Falls back to action-based computation
        if the legacy action data is still available.
        """
        if gani_data.gani_path_hash is not None:
            return gani_data.gani_path_hash

        Debug.log_warning("Missing gani_path_hash > needs to calculate it from action ref.")
        action = getattr(gani_data.gani_tracks_data, 'action', None)
        if action:
            return self.compute_gani_path_hash_from_action(action)

        raise ValueError("GANI path hash is not available; set gani_data.path_hash before writing")

    
    def write(self) -> None:
        """Write the MTAR file.
        
        This is the main entry point, mirroring MtarReader.read_all_tracks().
        Writes all contained GANI files and motion data to the MTAR format.
        """
        Debug.log(f"Writing MTAR file: {self.filepath}")
        
        if not self.gani_data_list:
            Debug.log_error("  Error: No GANI data to write")
            return
        
        if not self.layout_track:
            Debug.log_error("  Error: No layout track set")
            return
        
        buffer = io.BytesIO()
        
        # Get counts from layout track (shared across all GANI files)
        track_count = self.layout_track.header.unit_count
        file_count = len(self.gani_data_list)

        # For old format: MTAR header segment_count = max TrackHeader.segment_count across all GANIs.
        # segment_count per GANI = total segments across all tracks in that GANI's tracks_data.
        # For new format: the shared CommonInfo layout_track already has the canonical count.
        if not self.is_new_format:
            segment_count = max((gd.count_segments() for gd in self.gani_data_list), default=self.layout_track.header.segment_count)
        else:
            segment_count = self.layout_track.header.segment_count
        
        # Get motion point counts
        # IMPORTANT: Header count is separate from CommonInfo count!
        # - motion_point_header_count: for MTAR header (max units across GANIs)
        # - motion_points_list.count: for CommonInfo (total bone definitions)
        motion_point_header_count = self.motion_point_header_count
        motion_point_commoninfo_count = self.motion_points_list.count if self.motion_points_list else 0
        
        Debug.log(f"  File count: {file_count}")
        Debug.log(f"  Track count: {track_count} (from layout track)")
        Debug.log(f"  Segment count: {segment_count} ({'max across GANIs' if not self.is_new_format else 'from layout track'})")
        Debug.log(f"  Motion point header count: {motion_point_header_count} (max units across GANIs)")
        Debug.log(f"  Motion point CommonInfo count: {motion_point_commoninfo_count} (total bone definitions)")
        Debug.log(f"  Format: {'New (GANI2)' if self.is_new_format else 'Old (FoxData)'}")
        
        # Convert all GaniExportData to bytes using the layout track
        Debug.log(f"  Converting {len(self.gani_data_list)} GANI data object(s) to bytes...")
        gani_bytes_list = []
        
        if self.is_new_format:
            # New format: use Gani2Writer
            for gani_data in self.gani_data_list:
                gani_bytes = gani_data.to_bytes(self.layout_track)
                gani_bytes_list.append(gani_bytes)
                Debug.log(f"    {gani_data.gani_name}: {len(gani_bytes)} bytes (GANI2)")
        else:
            # Old format: use GaniWriter
            for gani_data in self.gani_data_list:
                gani_bytes = self._write_old_gani_bytes(gani_data) # TODO: move _write_old_gani_bytes to a better spot
                gani_bytes_list.append(gani_bytes)
                Debug.log(f"    {gani_data.gani_name}: {len(gani_bytes)} bytes (FoxData)")
        
        # Set flags based on format
        flags = self.flags
        
        # Determine file table entry size based on format
        file_table_entry_size = MtarTableList2.SIZE if self.is_new_format else MtarTableList.SIZE
        
        # Reserve space for header and file table
        header_size = MtarHeader.SIZE
        file_table_size = file_count * file_table_entry_size
        data_start = header_size + file_table_size
        
        # Write placeholder header (will update later)
        buffer.seek(0)
        placeholder_header = MtarHeader(
            version=self.version,
            file_count=file_count,
            track_count=track_count,
            segment_count=segment_count,
            shader_node_count=0,
            shader_unit_count=0,
            motion_point_unit_count=motion_point_header_count,
            flags=flags,
            common_info_offset=0 if self.is_new_format else 0,  # Will update for new format, 0 for old
            padding=0
        )
        placeholder_header.write(buffer)
        
        # Reserve space for file table (will write later)
        # File table size differs: MtarTableList (16 bytes) for old format, MtarTableList2 (32 bytes) for new
        file_table_offset = buffer.tell()
        for _ in range(file_count):
            util_binary_write.write_padding(buffer, file_table_entry_size)
        
        # Position at data start
        buffer.seek(data_start)
        
        # Write CommonInfo section only for new format
        common_info_offset = 0
        if self.is_new_format:
            Debug.log("  Writing CommonInfo...")
            common_info_offset = buffer.tell()
            self._write_common_info(buffer)
        else:
            Debug.log("  Skipping CommonInfo (old format)")
        
        # Write all GANI files (mirrors processing file table in read_all_tracks)
        Debug.log(f"  Writing {file_count} GANI file(s)...")
        file_table_entries = self._write_all_gani_files(buffer, gani_bytes_list)
        
        # ========== SORT FILE TABLE BY HASH (controlled by 'Sort GANI' setting) ==========
        try:
            sort_enabled = bool(bpy.context.scene.mtar_properties.settings_props.sort_gani)
        except Exception:
            sort_enabled = True

        if sort_enabled:
            file_table_entries = self.sort_file_table_by_hash(file_table_entries)
        # ========== END SORT FILE TABLE BY HASH ==========
        
        # Update header with common_info_offset
        placeholder_header.common_info_offset = common_info_offset
        buffer.seek(0)
        placeholder_header.write(buffer)
        
        # Write file table entries
        buffer.seek(file_table_offset)
        for entry in file_table_entries:
            entry.write(buffer)
        
        # Write to file
        Debug.log("  Writing to disk...")
        with open(self.filepath, 'wb') as f:
            f.write(buffer.getvalue())
        
        Debug.log(f"MTAR file written successfully: {len(buffer.getvalue())} bytes")

    def _compute_gani_tracks_data_size(self, gani_bytes: bytes, gani_tracks_offset: int, buffer_end_pos: int) -> int:
        """Compute the tracks data size based on format.
        
        For new format: use actual buffer bytes written (buffer_end_pos - gani_tracks_offset).
        For old format: use FoxDataHeader.file_size (excludes trailing alignment bytes).
        
        Args:
            gani_bytes: Raw GANI binary data
            gani_tracks_offset: Start offset in buffer
            buffer_end_pos: Current end position in buffer after writing/alignment
            
        Returns:
            Tracks data size in bytes
        """
        if self.is_new_format:
            return buffer_end_pos - gani_tracks_offset
        else:
            # Old format: FoxDataHeader.file_size excludes trailing alignment bytes
            return int.from_bytes(gani_bytes[8:12], 'little')
    
    def _write_motion_points_section(self, buffer: io.BytesIO, gani_data: GaniExportData, 
                                     gani_tracks_offset: int) -> tuple[int, int]:
        """Write motion point tracks section (new format only).
        
        For old format, motion points are already embedded in the FoxData blob as an MTP node,
        so this method returns (0, 0) without writing anything.
        
        Args:
            buffer: Buffer to write to
            gani_data: GANI export data containing motion points
            gani_tracks_offset: Start offset of the tracks section (for relative offset calc)
            
        Returns:
            Tuple of (motion_point_tracks_offset, motion_point_tracks_data_size)
            Returns (0, 0) for old format or if no motion points
        """
        if (not self.is_new_format or
                not gani_data.gani_motion_points_data or
                not gani_data.gani_motion_points_data.motion_point_tracks):
            return 0, 0
        
        Debug.log(f"    Writing Motion Points GANI: {gani_data.gani_name}")
        
        motion_point_start = buffer.tell()
        motion_point_offset_from_tracks = motion_point_start - gani_tracks_offset
        
        # Write motion point tracks as a Tracks structure
        motion_point_tracks_bytes = self._write_motion_point_tracks(
            gani_data.gani_motion_points_data.motion_point_tracks,
            gani_data.gani_frame_count,
            motion_point_track_header=gani_data.gani_motion_points_data.motion_point_track_header
        )
        buffer.write(motion_point_tracks_bytes)
        
        # Align to 16-byte boundary after motion point tracks
        util_binary_write.align_buffer(buffer, 16)
        
        motion_point_end = buffer.tell()
        motion_point_tracks_data_size = motion_point_end - motion_point_start
        
        return motion_point_offset_from_tracks, motion_point_tracks_data_size
    
    def _build_file_table_entry(self, path_hash: int,
                                gani_data: GaniExportData,
                                gani_tracks_offset: int,
                                gani_tracks_data_size: int,
                                motion_point_tracks_offset: int,
                                motion_point_tracks_data_size: int
                                ) -> 'MtarTableList | MtarTableList2':
        """Build a file table entry for a GANI file.
        
        Args:
            path_hash: Hash of the GANI path
            gani_data: GANI export data
            gani_tracks_offset: Start offset of tracks section
            gani_tracks_data_size: Size of tracks section
            motion_point_tracks_offset: Start offset of motion points (new format only, 0 for old)
            motion_point_tracks_data_size: Size of motion points (new format only, 0 for old)
            
        Returns:
            MtarTableList2 for new format, MtarTableList for old format
        """
        if self.is_new_format:
            return MtarTableList2(
                path=path_hash,
                tracks_offset=gani_tracks_offset,
                tracks_data_size=gani_tracks_data_size,
                motion_point_tracks_offset=motion_point_tracks_offset,
                motion_point_tracks_data_size=motion_point_tracks_data_size,
                shader_tracks_offset=0,
                shader_tracks_data_size=0,
                padding0=0,
                motion_events_offset=0,  # Will be set in second pass
                padding1=0
            )
        else:
            # Old format: MtarTableList.unknown is a separate ushort field (typically 7).
            # NOT the same as TrackHeader.unknown_b — these are distinct binary fields.
            # Priority: explicit table_unknown > action TABL_UNKNOWN > default 7.
            if gani_data.gani1_table_unknown is not None:
                mtar_unknown = gani_data.gani1_table_unknown
            else:
                action = gani_data.gani_tracks_data.action
                if action and mtar_const.TABL_UNKNOWN in action.keys():
                    try:
                        mtar_unknown = int(action[mtar_const.TABL_UNKNOWN])
                    except (TypeError, ValueError):
                        Debug.log_warning(
                            f"_build_file_table_entry: invalid {mtar_const.TABL_UNKNOWN} "
                            f"on action '{getattr(action, 'name', '<unknown>')}', "
                            f"using default 7"
                        )
                        mtar_unknown = 7
                else:
                    mtar_unknown = 7  # default: observed value in real old-format files
            return MtarTableList(
                path=path_hash,
                tracks_offset=gani_tracks_offset,
                tracks_data_size=gani_tracks_data_size,
                unknown=mtar_unknown
            )

    def _write_all_gani_files(self, buffer: io.BytesIO, gani_bytes_list: List[bytes]):
        """Write all GANI files and create file table entries.
        
        Layout: Track Gani 0, Motion Points Gani 0 (new format only), Track Gani 1, ...,
                Motion Events Gani 0, Motion Events Gani 1, ... (new format only)
        
        Args:
            buffer: Buffer to write GANI data to
            gani_bytes_list: List of GANI binary data
            
        Returns:
            List of file table entries for each GANI file
        """
        file_table_entries = []
        
        # First pass: Write all Track GANIs and Motion Point GANIs (motion points only in new format)
        Debug.log("  Phase 1: Writing Track and Motion Point GANIs...")
        for file_idx, gani_bytes in enumerate(gani_bytes_list):
            gani_name = self.gani_data_list[file_idx].gani_name
            gani_data: GaniExportData = self.gani_data_list[file_idx]
            
            # Write Track GANI
            Debug.log(f"    Writing Track GANI #{file_idx}: {gani_name}")
            gani_tracks_offset = buffer.tell()
            buffer.write(gani_bytes)
            
            if self.is_new_format:
                # New format: add 12 bytes of padding after tracks data + align to 16
                util_binary_write.write_padding(buffer, 12)
                util_binary_write.align_buffer(buffer, 16)
            # Old format: FoxData blob already ends on 16-byte boundary (trailing
            # align_buffer applied inside GaniWriter after capturing file_size)
            
            # Compute tracks data size based on format
            gani_end = buffer.tell()
            gani_tracks_data_size = self._compute_gani_tracks_data_size(gani_bytes, gani_tracks_offset, gani_end)
            
            # Write Motion Points section (new format only; old format has them embedded in FoxData)
            motion_point_tracks_offset, motion_point_tracks_data_size = \
                self._write_motion_points_section(buffer, gani_data, gani_tracks_offset)
            
            # Create file table entry
            path_hash = self.compute_gani_path_hash(gani_data)
            entry = self._build_file_table_entry(
                path_hash, gani_data,
                gani_tracks_offset, gani_tracks_data_size,
                motion_point_tracks_offset, motion_point_tracks_data_size
            )
            file_table_entries.append(entry)
        
        # Second pass: Write all Motion Events GANIs (new format only)
        if self.is_new_format:
            Debug.log("  Phase 2: Writing Motion Events GANIs...")
            for file_idx, gani_data in enumerate(self.gani_data_list):
                # if gani_data.gani_path_hash == 18181032995612392029:
                #     Debug.log("bla")
                if gani_data.gani_motion_events_data:
                    gani_name = gani_data.gani_name
                    Debug.log(f"    Writing Motion Events GANI #{file_idx}: {gani_name}")
                    
                    # MotionEventsOffset is an ABSOLUTE offset
                    motion_events_start = buffer.tell()
                    motion_events_offset = motion_events_start
                    
                    # Write motion events as EvpHeader
                    evp_header = gani_data.gani_motion_events_data.motion_events
                    evp_header.write(buffer)
                    
                    # Align to 16-byte boundary after motion events
                    util_binary_write.align_buffer(buffer, 16)
                    
                    motion_events_end = buffer.tell()
                    motion_events_data_size = motion_events_end - motion_events_start
                    
                    Debug.log(f"      Motion Events offset: 0x{motion_events_start:08X} (absolute)")
                    Debug.log(f"      Motion Events size: {motion_events_data_size} bytes")
                    
                    # Update the file table entry with motion events offset
                    file_table_entries[file_idx].motion_events_offset = motion_events_offset
        else:
            Debug.log("  Phase 2: Skipping Motion Events GANIs (old format)")
        
        return file_table_entries

    
    def _write_old_gani_bytes(self, gani_data: GaniExportData) -> bytes:
        """Convert GaniExportData to old-format FoxData GANI bytes.
        
        Uses GaniWriter to write animation tracks in FoxData container format
        instead of the new GANI2 format.
        
        Args:
            gani_data: Animation data to export
            
        Returns:
            Binary blob of FoxData-formatted GANI data
        """
        buffer = io.BytesIO()
        
        # Call GaniWriter to write the old-format GANI blob
        # This handles FoxData header, node structure, and track payload
        frame_rate = gani_data.gani_frame_rate or 60

        # SKL_LIST is auto-derived from gani_track names during write — names are the
        # authoritative source (set during import via _apply_stringlist_names in GaniReader).
        # If the original GANI had no SKL_LIST node, PROP_NO_SKL_LIST=1 is stored on the
        # action during import; passing skeleton_list=[] tells the writer to suppress it.
        # MTP_LIST and MTP_PARENT_LIST are still stored as action properties.
        action = gani_data.tracks_data.action if gani_data.tracks_data else None
        if action is None:
            Debug.log_warning(
                f"Old-format GANI '{gani_data.name}' has no Blender action (reference mode): "
                "no_skl_list, mtp_list, and mtp_parent_list cannot be preserved — "
                "SKL_LIST will be auto-derived from track names; MTP lists will be empty."
            )
        if action is not None:
            # Normal export path: read FoxData metadata from the Blender action
            no_skl_list = action.get(fwrap_metadata.PROP_NO_SKL_LIST, 0)
            skeleton_list = [] if no_skl_list else None  # []: suppress; None: auto-derive
            motion_point_list = fwrap_metadata.parse_foxdata_stringlist_from_action(action, fwrap_metadata.PROP_MTP_LIST)
            motion_point_parent_list = fwrap_metadata.parse_foxdata_stringlist_from_action(action, fwrap_metadata.PROP_MTP_PARENT_LIST)
            node_params = gani_data.gani_node_params if gani_data.gani_node_params is not None else (
                fwrap_metadata.iter_all_node_params_from_action(action)
            )
        else:
            # Reference export path: no Blender action — use GaniExportData fields
            skeleton_list = gani_data.gani1_skeleton_list
            if gani_data.gani1_no_skl_list:
                skeleton_list = []  # suppress SKL_LIST node
            motion_point_list = gani_data.gani1_motion_point_list
            motion_point_parent_list = gani_data.gani1_motion_point_parent_list
            node_params = gani_data.gani_node_params if gani_data.gani_node_params is not None else {}
            if skeleton_list is None and motion_point_list is None:
                Debug.log(
                    f"Old-format GANI '{gani_data.gani_name}' (reference mode): "
                    "SKL_LIST will be auto-derived from track names; MTP lists from import data."
                )
        self.gani_writer.write_gani_to_buffer(
            buffer=buffer,
            gani_tracks=gani_data.gani_tracks_data.gani_tracks,
            frame_count=gani_data.gani_frame_count,
            frame_rate=frame_rate,
            motion_point_tracks=gani_data.gani_motion_points_data.motion_point_tracks if gani_data.gani_motion_points_data else None,
            motion_events=gani_data.gani_motion_events_data.motion_events if gani_data.gani_motion_events_data else None,
            skeleton_list=skeleton_list,
            motion_point_list=motion_point_list,
            motion_point_parent_list=motion_point_parent_list,
            node_params=node_params,
            shader_tracks=(
                [
                    (name, tracks, hdr)
                    for name, tracks, hdr in zip(
                        gani_data.gani1_shader_nodes_data.property_names,
                        gani_data.gani1_shader_nodes_data.property_tracks,
                        gani_data.gani1_shader_nodes_data.property_headers
                        if gani_data.gani1_shader_nodes_data.property_headers
                        else [None] * len(gani_data.gani1_shader_nodes_data.property_names),
                    )
                ]
                if gani_data.gani1_shader_nodes_data else None
            ),
        )
        
        return buffer.getvalue()

    
    def _write_common_info(self, buffer: io.BytesIO) -> None:
        """Write the CommonInfo section.
        
        CommonInfo uses a linked-list structure with MtarMiniDataNode headers.
        Structure (in order):
        1. LayoutTrack node + data (always present)
        2. SkeletonList node + data (optional, if MTAR_FLAGS_HAS_SKEL_LIST flag is set)
        3. MotionPoints node + data (optional, only if motion points exist)
        
        Each node has:
        - name: CommonInfoNodeType enum value
        - data_size: size of data following the node
        - next_node_offset: offset from start of this node to next node (0 if last)
        - padding: always 0
        
        Args:
            buffer: Buffer to write to
        """
        # Track all node positions for calculating offsets
        node_positions = []
        
        # Determine if we have motion points to write
        has_motion_points = self.motion_points_list and self.motion_points_list.count > 0
        
        # === Write LayoutTrack node and data ===
        layout_node_pos = buffer.tell()
        node_positions.append(('layout', layout_node_pos))
        
        # Reserve space for LayoutTrack node
        util_binary_write.write_padding(buffer, MtarMiniDataNode.SIZE)
        
        # Write layout track data
        layout_data_start = buffer.tell()
        self._write_layout_track(buffer)
        layout_data_end = buffer.tell()
        layout_data_size = layout_data_end - layout_data_start
        
        # Align to 16-byte boundary after LayoutTrack data
        util_binary_write.align_buffer(buffer, 16)
        
        # === Write SkeletonList node and data (if needed) ===
        # Note: Skeleton list writing not yet implemented
        # For now, we skip this node
        
        # === Write MotionPoints node and data (if needed) ===
        motion_node_pos = None
        motion_data_size = 0
        
        if has_motion_points:
            motion_node_pos = buffer.tell()
            node_positions.append(('motion', motion_node_pos))
            
            # Reserve space for MotionPoints node
            util_binary_write.write_padding(buffer, MtarMiniDataNode.SIZE)
            
            # Write MotionPointList2 data
            motion_data_start = buffer.tell()
            Debug.log(f"    Writing MotionPointsList: {self.motion_points_list.count} point(s)")
            self.motion_points_list.write(buffer)
            motion_data_end = buffer.tell()
            motion_data_size = motion_data_end - motion_data_start
            
            # Align to 16-byte boundary after MotionPoints data
            util_binary_write.align_buffer(buffer, 16)
        else:
            Debug.log("    No motion points - skipping MotionPoints CommonInfo node")
        
        # === Update all nodes with correct offsets and sizes ===
        current_pos = buffer.tell()
        
        # Write LayoutTrack node
        buffer.seek(layout_node_pos)
        if has_motion_points:
            # LayoutTrack points to MotionPoints node
            next_offset = motion_node_pos - layout_node_pos
        else:
            # LayoutTrack is the last node
            next_offset = 0
        
        layout_node = MtarMiniDataNode(
            name=StrCode32(CommonInfoNodeType.LayoutTrack),  # Convert enum to StrCode32
            data_size=layout_data_size,
            next_node_offset=next_offset,
            padding=0
        )
        layout_node.write(buffer)
        
        # Write MotionPoints node if present (last node, next_offset = 0)
        if has_motion_points:
            buffer.seek(motion_node_pos)
            motion_node = MtarMiniDataNode(
                name=StrCode32(CommonInfoNodeType.MotionPoints),  # Convert enum to StrCode32
                data_size=motion_data_size,
                next_node_offset=0,  # Last node in chain
                padding=0
            )
            motion_node.write(buffer)
        
        # Return to end position
        buffer.seek(current_pos)
    
    def _write_layout_track(self, buffer: io.BytesIO) -> None:
        """Write the actual layout track structure from self.layout_track.
        
        This uses the complete layout track data that was either parsed from
        a layout action or built from metadata during export.
        
        Args:
            buffer: Buffer to write to
        """
        if not self.layout_track:
            Debug.log_warning("    Warning: Failed to find layout track. Will write default layout track but this will probably not create a valid file.")
            # Write empty TrackHeader if no layout track.
            empty_header = TrackHeader(
                unit_count=0,
                segment_count=0,
                t_id=0,
                unknown_a=0,
                unknown_b=0,
                frame_count=0,
                frame_rate=60,
                unit_offsets=[]
            )
            empty_header.write(buffer)
            return
        
        # Simply write the existing layout track structure
        # The layout_track already has all TrackUnits with correct data
        self.layout_track.write(buffer)
    
    def _write_motion_point_tracks(self, motion_point_tracks: List['TrackUnitWrapper'], 
                                   frame_count: int,
                                   motion_point_track_header: Optional[TrackHeader] = None) -> bytes:
        """Write motion point tracks as a TrackHeader structure.
        
        Motion point tracks use the same TrackHeader format as the layout track,
        but contain actual keyframe data (unlike the layout track which is just structure).
        
        Args:
            motion_point_tracks: List of TrackUnitWrapper objects for motion points
            frame_count: Frame count for this GANI
            motion_point_track_header: Optional TrackHeader from imported reference mode; used to preserve header fields
            
        Returns:
            Binary data containing the MotionPointTracks section
        """
        # Safety filter: discard any empty tracks (in case upstream filtering missed any)
        # Motion points have no layout track, so empty tracks have no purpose
        valid_tracks = [t for t in motion_point_tracks if t.segments_track_data]
        if len(valid_tracks) < len(motion_point_tracks):
            Debug.log_warning(
                f"    WARNING: Discarding {len(motion_point_tracks) - len(valid_tracks)} empty motion point track(s) "
                f"(kept {len(valid_tracks)})"
            )
        
        # Build TrackUnits from TrackUnitWrapper objects
        track_units = []
        total_segment_count = 0
        
        for motion_point_track in valid_tracks:
            # Get track name hash
            if isinstance(motion_point_track.name, str):
                # Convert string hash to integer
                name_hash = StrCode32(int(motion_point_track.name))
            else:
                # Already a StrCode32
                name_hash = motion_point_track.name
            
            # Build TrackData entries from keyframes_tracks
            track_data_list = []
            for segment_idx, track_data_blob_wrapper in enumerate(motion_point_track.segments_track_data):
                # Calculate absolute segment index across all tracks
                segment_idx_abs = total_segment_count + segment_idx
                
                # Determine next_entry_offset: 0 for last segment, TrackData.ENTRY_SIZE (8) for others
                is_last_segment = (segment_idx == len(motion_point_track.segments_track_data) - 1)
                next_entry_offset = 0 if is_last_segment else TrackData.ENTRY_SIZE
                
                track_data = TrackData(
                    data_offset=0,  # Will be calculated by Tracks.write()
                    ms_id=segment_idx_abs,  # Absolute segment index
                    td_type=track_data_blob_wrapper.data_blob.type,
                    next_entry_offset=next_entry_offset,
                    component_bit_size=track_data_blob_wrapper.data_blob.component_bit_size,
                    data_blob=track_data_blob_wrapper.data_blob.keyframes
                )
                track_data_list.append(track_data)
            
            # Update total segment count after processing all segments in this track
            total_segment_count += len(motion_point_track.segments_track_data)
            
            # Get unit flags
            unit_flags_int = 0
            if motion_point_track.unit_flags:
                unit_flags_int = TrackUnitFlags.track_unit_flags_to_int(motion_point_track.unit_flags)
            
            # Create TrackUnit
            track_unit = TrackUnit(
                name=name_hash,
                segment_count=len(track_data_list),
                unit_flags=unit_flags_int,
                padding=0,  # Standard padding
                segments_data=track_data_list
            )
            track_units.append(track_unit)
        
        # Determine TrackHeader source data in priority order:
        # 1. explicit motion point header from reference mode
        # 2. action custom properties (deprecated path)
        # 3. defaults
        header_obj = motion_point_track_header

        if header_obj is not None:
            header_props = {
                gani_const.TRKH_ID: header_obj.t_id,
                gani_const.TRKH_UNKNOWN_A: header_obj.unknown_a,
                gani_const.TRKH_UNKNOWN_B: header_obj.unknown_b,
                gani_const.TRKH_FRAME_RATE: header_obj.frame_rate,
            }
        else:
            # Fallback to defaults if no explicit header available
            header_props = {
                gani_const.TRKH_ID: 0,
                gani_const.TRKH_UNKNOWN_A: 0,
                gani_const.TRKH_UNKNOWN_B: 1,
                gani_const.TRKH_FRAME_RATE: 60,
            }
            Debug.log_warning("Unable to get motion points track header data. Using fallback which will probably not work.")

        # Create TrackHeader
        track_header = TrackHeader(
            unit_count=len(track_units),
            segment_count=total_segment_count,
            t_id=header_props[gani_const.TRKH_ID],
            unknown_a=header_props[gani_const.TRKH_UNKNOWN_A],
            unknown_b=header_props[gani_const.TRKH_UNKNOWN_B],
            frame_count=frame_count,
            frame_rate=header_props[gani_const.TRKH_FRAME_RATE],
            unit_offsets=[]  # Will be calculated by Tracks.write()
        )
        
        # Create Tracks object
        motion_tracks = Tracks(
            header=track_header,
            track_units=track_units
        )
        
        # Write to buffer with data blobs enabled (motion point tracks contain actual keyframe data)
        buffer = io.BytesIO()
        motion_tracks.write(buffer, write_data_blobs=True)
        
        return buffer.getvalue()
