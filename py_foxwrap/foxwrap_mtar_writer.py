"""
MTAR file writer for Metal Gear Solid V.

This module handles writing MTAR (Motion Track Archive) files.
Mirrors the structure of MtarReader for symmetry.
"""

from typing import List, Optional
import io

import bpy

from ..py_utilities.utilities_logging import Debug
from ..py_utilities.utilities_binary_write import align_buffer, write_padding
from ..py_utilities.utilities_hashing import is_gani_path_a_hash, parse_gani_hash_str

from ..py_fox.fox_mtar_types import MtarHeader, MtarTableList2, MtarFlags, MtarMiniDataNode, MotionPointList2
from ..py_fox.fox_gani_enums import CommonInfoNodeType, TrackUnitFlags
from ..py_fox.fox_gani_types import TrackHeader, TrackUnit, TrackData
from ..py_fox.fox_misc_types import StrCode32

from .foxwrap_gani_writer import Gani2Writer
from .foxwrap_misc import Tracks, TrackUnitWrapper
from .foxwrap_misc_export import GaniData
from .foxwrap_metadata import read_track_header_properties_from_action
from ..py_fox import fox_mtar_constants as mtar_const

from ..py_tools.tools_hash_generator import hash_animation_name_from_blender_context


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
        self.gani_writer = Gani2Writer()
        
        # Storage for GANI data to write  
        self.gani_data_list: List['GaniData'] = []
        
        # MTAR metadata
        self.frame_rate: int = 60
        self.version: int = 201403250  # MTAR version of mgs5 tpp
        
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
        
    def add_gani_data(self, gani_data: 'GaniData') -> None:
        """Add a GaniData object to be included in the MTAR.
        
        Args:
            gani_data: GaniData object containing animation data
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
    
    def sort_file_table_by_hash(self, file_table_entries: List['MtarTableList2']) -> List['MtarTableList2']:
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
    
    def _resolve_gani_path_string(self, gani_data: 'GaniData') -> str:
        """Determine the canonical path string for a GANI, mirroring _compute_gani_path_hash logic.

        Returns paths with .gani extension for use in info files and path recording.

        - Raw hash + treat_hashes_as_names=True  → export_custom_path_base + hash_str + .gani
        - Raw hash + treat_hashes_as_names=False → raw hash string (unchanged, no extension needed)
        - Valid /Assets/ path                    → returned with .gani extension if not present
        - Invalid path (not hash, not /Assets/)  → export_custom_path_base + path + .gani
        - NLA source fallback                    → export_custom_path_base + track/strip + .gani
        """
        action = gani_data.tracks_data.action
        source = gani_data.tracks_data.source

        if action and mtar_const.TABL_PATH in action.keys():
            gani_path_val = str(action[mtar_const.TABL_PATH])
            if is_gani_path_a_hash(gani_path_val):
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
        action_name = action.name if action else gani_data.name
        return action_name if action_name.endswith('.gani') else f"{action_name}.gani"

    def get_animation_name_for_gani(self, gani_data: 'GaniData') -> str:
        """Extract the animation name string from GaniData for the info file.

        Mirrors the path resolution logic of _compute_gani_path_hash:
        - Valid asset path from gani_path → used directly.
        - Unresolved hash with custom_path_hashes → custom_path_base + hash_str.
        - NLA source (no gani_path) → [custom_path_base]track_name/strip_name.
        - Fallback → action name.

        Args:
            gani_data: GaniData object containing animation data

        Returns:
            Animation name string (e.g., "/Assets/mgo/motion/walk_idle" or "player2.0.gani")
        """
        return self._resolve_gani_path_string(gani_data)

    def _compute_gani_path_hash(self, gani_data: 'GaniData') -> int:
        """Compute the path hash for a GANI file.

        Reads the 'gani_path' custom property from the action:
        - Raw hash + treat_hashes_as_names=False → use hash directly.
        - Raw hash + treat_hashes_as_names=True  → combine with export_custom_path_base,
          hash via generator only (no dict fallback for constructed paths).
        - Valid /Assets/ path                    → hash via generator first, then dict lookup.
        - Invalid path (not hash, not /Assets/)  → always combine with base, hash via generator only.
        - Raises ValueError if the hash cannot be determined or the property is missing.

        Args:
            gani_data: GaniData object containing animation data

        Returns:
            Path hash value (64-bit integer)

        Raises:
            ValueError: If the hash cannot be determined.
        """
        action = gani_data.tracks_data.action

        # TABL_PATH is required — abort if missing
        if not action or mtar_const.TABL_PATH not in action.keys():
            action_name = action.name if action else "None"
            raise ValueError(
                f"Action '{action_name}' is missing the '{mtar_const.TABL_PATH}' custom property. "
                "Re-import the MTAR with the current version to populate this property or add it yourself."
            )

        gani_path_val: str = str(action[mtar_const.TABL_PATH])

        if is_gani_path_a_hash(gani_path_val):
            if not self.treat_hashes_as_names:
                # Raw hash — use directly
                path_hash = parse_gani_hash_str(gani_path_val)
                Debug.log(f"      Using stored hash from gani_path: 0x{path_hash:016X}")
                return path_hash
            # treat_hashes_as_names: treat hash as a path component, combine with base
            combined = f"{self.export_custom_path_base}{gani_path_val}.gani"
            Debug.log(f"      Treating hash as name component, combined path: '{combined}'")
            success, results, error = hash_animation_name_from_blender_context(combined)
            if success and results.get('with_extension_dec'):
                hash_str = results['with_extension_dec']
                try:
                    path_hash = parse_gani_hash_str(hash_str)
                    Debug.log(f"      Hash result: 0x{path_hash:016X}")
                    return path_hash
                except ValueError:
                    Debug.log_warning(f"      Failed to parse hash result: '{hash_str}'")
            raise ValueError(
                f"Could not compute hash for action '{action.name}' with combined path '{combined}'. "
                f"Hash computation failed: {error}."
            )

        if _is_valid_asset_path(gani_path_val):
            # Valid /Assets/ path — hash directly via Python
            # Ensure .gani extension is present before hashing
            path_to_hash = gani_path_val if gani_path_val.endswith('.gani') else f"{gani_path_val}.gani"
            Debug.log(f"      Hashing valid asset path: '{path_to_hash}'")
            success, results, error = hash_animation_name_from_blender_context(path_to_hash)
            if success and results.get('with_extension_dec'):
                hash_str = results['with_extension_dec']
                try:
                    path_hash = parse_gani_hash_str(hash_str)
                    Debug.log(f"      Hash result: 0x{path_hash:016X}")
                    return path_hash
                except ValueError:
                    Debug.log_warning(f"      Failed to parse hash result: '{hash_str}'")
            raise ValueError(
                f"Could not compute hash for action '{action.name}' with gani_path='{gani_path_val}'. "
                f"Hash computation failed: {error}."
            )

        # Invalid path (not a hash, not /Assets/) — always combine with base, hash via Python
        combined = f"{self.export_custom_path_base}{gani_path_val}.gani"
        Debug.log(f"      Invalid path, combining with base: '{combined}'")
        success, results, error = hash_animation_name_from_blender_context(combined)
        if success and results.get('with_extension_dec'):
            hash_str = results['with_extension_dec']
            try:
                path_hash = parse_gani_hash_str(hash_str)
                Debug.log(f"      Hash result: 0x{path_hash:016X}")
                return path_hash
            except ValueError:
                Debug.log_warning(f"      Failed to parse hash result: '{hash_str}'")
        raise ValueError(
            f"Could not compute hash for action '{action.name}' with combined path '{combined}'. "
            f"Hash computation failed: {error}."
        )

    
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
        segment_count = self.layout_track.header.segment_count
        file_count = len(self.gani_data_list)
        
        # Get motion point counts
        # IMPORTANT: Header count is separate from CommonInfo count!
        # - motion_point_header_count: for MTAR header (max units across GANIs)
        # - motion_points_list.count: for CommonInfo (total bone definitions)
        motion_point_header_count = self.motion_point_header_count
        motion_point_commoninfo_count = self.motion_points_list.count if self.motion_points_list else 0
        
        Debug.log(f"  File count: {file_count}")
        Debug.log(f"  Track count: {track_count} (from layout track)")
        Debug.log(f"  Segment count: {segment_count} (from layout track)")
        Debug.log(f"  Motion point header count: {motion_point_header_count} (max units across GANIs)")
        Debug.log(f"  Motion point CommonInfo count: {motion_point_commoninfo_count} (total bone definitions)")
        
        # Convert all GaniData to bytes using the layout track
        Debug.log(f"  Converting {len(self.gani_data_list)} GANI data object(s) to bytes...")
        gani_bytes_list = []
        for gani_data in self.gani_data_list:
            gani_bytes = gani_data.to_bytes(self.layout_track)
            gani_bytes_list.append(gani_bytes)
            Debug.log(f"    {gani_data.name}: {len(gani_bytes)} bytes")
        
        # Determine flags (TODO: find out how to retrieve the flag value)
        flags = MtarFlags.UseMini
        
        # Reserve space for header and file table
        header_size = MtarHeader.SIZE
        file_table_size = file_count * MtarTableList2.SIZE
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
            common_info_offset=0,  # Will update
            padding=0
        )
        placeholder_header.write(buffer)
        
        # Reserve space for file table (will write later)
        file_table_offset = buffer.tell()
        for _ in range(file_count):
            write_padding(buffer, MtarTableList2.SIZE)
        
        # Position at data start
        buffer.seek(data_start)
        
        # Write CommonInfo section (mirrors reading CommonInfo in read_all_tracks)
        Debug.log("  Writing CommonInfo...")
        common_info_offset = buffer.tell()
        self._write_common_info(buffer)
        
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

    def _write_all_gani_files(self, buffer: io.BytesIO, gani_bytes_list: List[bytes]) -> List['MtarTableList2']:
        """Write all GANI files and create file table entries.
        
        Layout: Track Gani 0, Motion Points Gani 0, Track Gani 1, Motion Points Gani 1, ...,
                Motion Events Gani 0, Motion Events Gani 1, ..., Motion Events Gani N
        
        Args:
            buffer: Buffer to write GANI data to
            gani_bytes_list: List of GANI binary data
            
        Returns:
            List of file table entries for each GANI file
        """
        file_table_entries = []
        
        # First pass: Write all Track GANIs and Motion Point GANIs in interleaved order
        Debug.log("  Phase 1: Writing Track and Motion Point GANIs (interleaved)...")
        for file_idx, gani_bytes in enumerate(gani_bytes_list):
            gani_name = self.gani_data_list[file_idx].name
            gani_data: GaniData = self.gani_data_list[file_idx]
            
            # =============================
            # Write Track GANI

            Debug.log(f"    Writing Track GANI #{file_idx}: {gani_name}")
            
            # Record start offset
            gani_tracks_offset = buffer.tell()
            
            # Write GANI data
            buffer.write(gani_bytes)
            
            # Add 12 bytes of padding after tracks data (before alignment)
            write_padding(buffer, 12)
            
            # Align to 16-byte boundary after main tracks and padding
            align_buffer(buffer, 16)
            
            # Record end offset after main tracks
            gani_end = buffer.tell()
            gani_tracks_data_size = gani_end - gani_tracks_offset
            
            Debug.log(f"      Track offset: 0x{gani_tracks_offset:08X}, Size: {gani_tracks_data_size} bytes")
            
            # =============================
            # Write MotionPointTracks GANI immediately after (if present)

            motion_point_tracks_offset = 0
            motion_point_tracks_data_size = 0
            
            if gani_data.motion_points_data:
                Debug.log(f"    Writing Motion Points GANI #{file_idx}: {gani_name}")
                
                # Calculate offset relative to tracks_offset (in 16-byte units)
                motion_point_start = buffer.tell()
                motion_point_offset_from_tracks = motion_point_start - gani_tracks_offset
                motion_point_tracks_offset = motion_point_offset_from_tracks
                
                # Write motion point tracks as a Tracks structure
                motion_point_tracks_bytes = self._write_motion_point_tracks(
                    gani_data.motion_points_data.motion_point_tracks, 
                    gani_data.frame_count,
                    gani_data.motion_points_data.action  # Pass action to read TrackHeader custom properties
                )
                buffer.write(motion_point_tracks_bytes)
                
                # Align to 16-byte boundary after motion point tracks
                align_buffer(buffer, 16)
                
                motion_point_end = buffer.tell()
                motion_point_tracks_data_size = motion_point_end - motion_point_start
                
                Debug.log(f"      Motion Points offset: 0x{motion_point_start:08X} (relative: 0x{motion_point_offset_from_tracks:08X})")
                Debug.log(f"      Motion Points size: {motion_point_tracks_data_size} bytes")
            
            # Compute path hash for this GANI file
            path_hash = self._compute_gani_path_hash(gani_data)
            
            # Create file table entry (motion_events_offset will be set in second pass)
            entry = MtarTableList2(
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
            file_table_entries.append(entry)
        
        # Second pass: Write all Motion Events GANIs
        Debug.log("  Phase 2: Writing Motion Events GANIs...")
        for file_idx, gani_data in enumerate(self.gani_data_list):
            if gani_data.motion_events_data:
                gani_name = gani_data.name
                Debug.log(f"    Writing Motion Events GANI #{file_idx}: {gani_name}")
                
                # MotionEventsOffset is an ABSOLUTE offset
                motion_events_start = buffer.tell()
                motion_events_offset = motion_events_start
                
                # Write motion events as EvpHeader
                evp_header = gani_data.motion_events_data.motion_events
                evp_header.write(buffer)
                
                # Align to 16-byte boundary after motion events
                align_buffer(buffer, 16)
                
                motion_events_end = buffer.tell()
                motion_events_data_size = motion_events_end - motion_events_start
                
                Debug.log(f"      Motion Events offset: 0x{motion_events_start:08X} (absolute)")
                Debug.log(f"      Motion Events size: {motion_events_data_size} bytes")
                
                # Update the file table entry with motion events offset
                file_table_entries[file_idx].motion_events_offset = motion_events_offset
        
        return file_table_entries

    
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
        write_padding(buffer, MtarMiniDataNode.SIZE)
        
        # Write layout track data
        layout_data_start = buffer.tell()
        self._write_layout_track(buffer)
        layout_data_end = buffer.tell()
        layout_data_size = layout_data_end - layout_data_start
        
        # Align to 16-byte boundary after LayoutTrack data
        align_buffer(buffer, 16)
        
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
            write_padding(buffer, MtarMiniDataNode.SIZE)
            
            # Write MotionPointList2 data
            motion_data_start = buffer.tell()
            Debug.log(f"    Writing MotionPointsList: {self.motion_points_list.count} point(s)")
            self.motion_points_list.write(buffer)
            motion_data_end = buffer.tell()
            motion_data_size = motion_data_end - motion_data_start
            
            # Align to 16-byte boundary after MotionPoints data
            align_buffer(buffer, 16)
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
                                   frame_count: int, action: Optional[bpy.types.Action] = None) -> bytes:
        """Write motion point tracks as a TrackHeader structure.
        
        Motion point tracks use the same TrackHeader format as the layout track,
        but contain actual keyframe data (unlike the layout track which is just structure).
        
        Args:
            motion_point_tracks: List of TrackUnitWrapper objects for motion points
            frame_count: Frame count for this GANI
            action: Optional action containing custom properties for TrackHeader fields (including frame_rate)
            
        Returns:
            Binary data containing the MotionPointTracks section
        """
        # Build TrackUnits from TrackUnitWrapper objects
        track_units = []
        total_segment_count = 0
        
        for motion_point_track in motion_point_tracks:
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
        
        # Read TrackHeader fields from action custom properties if available
        header_props = read_track_header_properties_from_action(action)
        
        # Create TrackHeader
        # Use header_props['frame_rate'] from action metadata instead of passed frame_rate parameter
        track_header = TrackHeader(
            unit_count=len(track_units),
            segment_count=total_segment_count,
            t_id=header_props['t_id'],
            unknown_a=header_props['unknown_a'],
            unknown_b=header_props['unknown_b'],
            frame_count=frame_count,
            frame_rate=header_props['frame_rate'],  # Use frame_rate from action metadata
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
