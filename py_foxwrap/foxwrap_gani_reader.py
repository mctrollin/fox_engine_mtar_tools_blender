"""
Reader for old-format (FoxData) GANI animation files.

Old-format GANI files (GZ/Legacy, pre-GANI2) use FoxData node trees instead of
flat GANI2TrackData blobs. This module provides GaniReader to parse these files,
mirroring the interface of Gani2Reader but extracting data from FoxData structures.
"""

import io
import struct
from typing import Optional, List, BinaryIO, Tuple

from ..py_utilities.utilities_logging import Debug
from ..py_utilities.utilities_hashing import unhash_gani_node, unhash_shader_prop, is_hash_string, parse_hash_string
from ..py_utilities.utilities_hashing_cityhash import strcode32

from ..py_fox.fox_foxdata_types import FoxDataHeader, FoxDataNode
from ..py_fox.fox_gani_types import TrackMiniHeader, EvpHeader, Gani2TrackData
from ..py_fox.fox_gani_constants import (
    FOXDATA_HASH_ROOT, FOXDATA_HASH_MOTION, FOXDATA_HASH_UNIT,
    FOXDATA_HASH_MTP, FOXDATA_HASH_EVP, FOXDATA_HASH_SHADER,
    FOXDATA_HASH_SKL_LIST, FOXDATA_HASH_MTP_LIST, FOXDATA_HASH_MTP_PARENT_LIST,
)

from .foxwrap_misc import Tracks, TrackUnitWrapper
from .foxwrap_misc_import import GaniImportData, ShaderTrackWrapper

from .foxwrap_gani2_reader import apply_track_naming
from ..py_utilities.utilities_naming import apply_segment_suffixes


def _apply_stringlist_names(
    tracks: List[TrackUnitWrapper],
    string_list: Optional[List[str]],
    label: str,
) -> None:
    """Apply names from a FoxData StringData list (SKL_LIST / MTP_LIST) to *tracks*.

    Builds a ``{hash: name}`` lookup from the real-string entries in *string_list*
    (decimal-integer entries are hash-only fallbacks and contribute no real name).
    Iterates *tracks* and for each:

    - If the track already has a real name (dict-resolved) **and** the SKL entry
      resolves to the same hash but a different string → log a warning, SKL wins.
    - If the track has only a hash fallback name and SKL has a real string → apply it.
    - If the track has only a hash fallback name and SKL has no entry → log a warning.

    Size mismatches are warned but do not abort (lookup is hash-based, not positional).
    """
    if not string_list:
        return

    # Build hash → name from real-string entries only.
    skl_lookup: dict = {}
    for entry in string_list:
        if not is_hash_string(entry):
            h = strcode32(entry)
            skl_lookup[h] = entry

    for track in tracks:
        name = track.name
        is_hash_fallback = is_hash_string(name)

        # Compute the hash of this track's current name.
        if is_hash_fallback:
            track_hash = parse_hash_string(name)
        else:
            track_hash = strcode32(name)

        if track_hash in skl_lookup:
            skl_name = skl_lookup[track_hash]
            if not is_hash_fallback and name != skl_name:
                Debug.log_warning(
                    f"_apply_stringlist_names() [{label}]: hash 0x{track_hash:08X} — "
                    f"dict resolved ('{name}') differs from ('{skl_name}') which will be used."
                )
            # Apply SKL name (silently for hash fallbacks, or after warning above).
            track.name = skl_name
            for seg in track.segments_track_data:
                seg.name = skl_name
        elif is_hash_fallback:
            Debug.log(
                f"_apply_stringlist_names ({label}): hash 0x{track_hash:08X} ('{name}') "
                f"has no list entry — keeping ('{name}')."
            )


class GaniReader:
    """Reader for old-format (FoxData) GANI animation files.
    
    Parses FoxData node trees embedded in legacy GANI files and extracts:
    - MOTION node: main animation tracks (bones, transforms)
    - MTP node: motion point tracks (optional)
    - EVP node: motion events (optional)
    - SKL_LIST/MTP_LIST nodes: bone/point hierarchies
    
    The output is wrapped in GaniImportData for uniform handling with GANI2 format.
    """
    
    def __init__(self):
        pass
    
    def read_gani(
        self,
        file_data: bytes,
        gani_start: int,
    ) -> GaniImportData:
        """Read an old-format GANI file from memory buffer.
        
        Traverses the FoxData node tree: ROOT → MOTION → {UNIT, MTP, SKL_LIST,
        MTP_LIST, MTP_PARENT_LIST, EVP}.
        
        Args:
            file_data: Complete MTAR file contents as bytes
            gani_start: Absolute offset to FoxDataHeader in file_data
            
        Returns:
            GaniImportData containing all parsed animation data
            
        Raises:
            NotImplementedError: If big-endian format is detected (not yet supported)
            ValueError: If required ROOT or MOTION nodes are missing
        """
        br = io.BytesIO(file_data)
        br.seek(gani_start)
        
        # Read header and detect endianness
        foxdata_header, endian = FoxDataHeader.read(br)
        
        if endian == '>':
            raise NotImplementedError("Big-endian GANI format not yet supported")
        
        Debug.log(f"Reading old-format GANI at offset 0x{gani_start:X}: version={foxdata_header.version}, "
                  f"nodes_offset=0x{foxdata_header.nodes_offset:X}")
        
        nodes_start = gani_start + foxdata_header.nodes_offset
        
        # --- Find ROOT (always first node, but use search for safety) ---
        root_result = self._find_node_by_hash(file_data, nodes_start, FOXDATA_HASH_ROOT, endian)
        if root_result is None:
            raise ValueError("GANI has no ROOT node")
        root_node, root_node_pos = root_result
        
        # --- Enumerate ROOT's children ---
        unit_tracks: Tracks = Tracks(header=None, track_units=[])
        mtp_raw_tracks: Optional[Tracks] = None
        events: Optional[EvpHeader] = None
        skeleton_list: Optional[List[str]] = None
        motion_point_list: Optional[List[str]] = None
        motion_point_parent_list: Optional[List[str]] = None
        shader_tracks: List = []
        
        motion_node: Optional[FoxDataNode] = None
        motion_node_pos: int = 0
        
        if root_node.child_node_offset != 0:
            child_pos = root_node_pos + root_node.child_node_offset
            while True:
                br.seek(child_pos)
                child_node = FoxDataNode.read(br, endian)
                child_hash = child_node.name_hash
                
                if child_hash == FOXDATA_HASH_MOTION:
                    motion_node = child_node
                    motion_node_pos = child_pos
                elif child_hash == FOXDATA_HASH_SHADER:
                    # Debug.log_warning("SHADER node found — facial animation import not yet implemented")
                    shader_tracks = self._read_shader_tracks(br, file_data, child_node, child_pos, endian)
                else:
                    resolved = unhash_gani_node(child_hash) or f"0x{child_hash:08X}"
                    Debug.log_warning(f"Unrecognized ROOT child node '{resolved}' — skipping (no Blender import)")
                
                if child_node.next_node_offset == 0:
                    break
                child_pos += child_node.next_node_offset
        
        if motion_node is None:
            raise ValueError("GANI ROOT has no MOTION child — no animation data")
        
        # --- Enumerate MOTION's children ---
        if motion_node.child_node_offset == 0:
            Debug.log_warning("MOTION node has no children — no animation data will be imported")
        else:
            child_pos = motion_node_pos + motion_node.child_node_offset
            while True:
                br.seek(child_pos)
                child_node = FoxDataNode.read(br, endian)
                child_hash = child_node.name_hash
                
                if child_hash == FOXDATA_HASH_UNIT:
                    unit_tracks = self._read_node_payload_as_tracks(br, file_data, child_node, child_pos, endian)
                elif child_hash == FOXDATA_HASH_MTP:
                    mtp_raw_tracks = self._read_node_payload_as_tracks(br, file_data, child_node, child_pos, endian)
                elif child_hash == FOXDATA_HASH_EVP:
                    br.seek(child_node.payload_abs_offset(child_pos))
                    events = EvpHeader.read(br, endian)
                elif child_hash == FOXDATA_HASH_SKL_LIST:
                    skeleton_list = self._read_stringdata_payload(file_data, child_node, child_pos, endian)
                elif child_hash == FOXDATA_HASH_MTP_LIST:
                    motion_point_list = self._read_stringdata_payload(file_data, child_node, child_pos, endian)
                elif child_hash == FOXDATA_HASH_MTP_PARENT_LIST:
                    motion_point_parent_list = self._read_stringdata_payload(file_data, child_node, child_pos, endian)
                else:
                    resolved = unhash_gani_node(child_hash) or f"0x{child_hash:08X}"
                    Debug.log_warning(f"Unimplemented MOTION child node '{resolved}' — skipping (no Blender import)")
                
                if child_node.next_node_offset == 0:
                    break
                child_pos += child_node.next_node_offset
        
        # --- Convert UNIT tracks to TrackUnitWrapper list ---
        bone_tracks = apply_track_naming(Tracks.convert_to_gani_tracks(unit_tracks), prefix=None)
        _apply_stringlist_names(bone_tracks, skeleton_list, label=f"Read gani @ (0x{gani_start}) SKL_LIST")
        apply_segment_suffixes(bone_tracks)

        # --- Convert MTP tracks if present ---
        mtp_tracks: List[TrackUnitWrapper] = []
        motion_point_layout: Optional[Tracks] = None
        motion_point_track_header = None
        if mtp_raw_tracks is not None:
            # Always use decimal hash strings for motion point track names (no prefix, no unhashing).
            # This ensures FCurve bone paths match the decimal hash bone names in the motion points armature.
            # _apply_stringlist_names is intentionally skipped here: applying readable names from MTP_LIST
            # would break the name consistency with the armature bones.
            mtp_tracks = apply_track_naming(Tracks.convert_to_gani_tracks(mtp_raw_tracks), use_decimal_only=True)
            apply_segment_suffixes(mtp_tracks)
            motion_point_layout = mtp_raw_tracks
            motion_point_track_header = mtp_raw_tracks.header
        
        # Synthesize TrackMiniHeader from UNIT TrackHeader
        track_mini_header = self._synthesize_mini_header(unit_tracks)
        
        Debug.log(f"Loaded old-format GANI: {len(bone_tracks)} bone track(s), "
                  f"{len(mtp_tracks)} motion point track(s), {len(shader_tracks)} shader track(s), "
                  f"events={'yes' if events else 'no'}, "
                  f"skeleton_list={len(skeleton_list) if skeleton_list else 0} entries")
        
        return GaniImportData(
            bone_tracks=bone_tracks,
            mtp_tracks=mtp_tracks,
            events=events,
            layout_track=unit_tracks,
            track_mini_header=track_mini_header,
            motion_point_layout=motion_point_layout,
            motion_point_track_header=motion_point_track_header,
            shader_tracks=shader_tracks,
            skeleton_list=skeleton_list,
            motion_point_list=motion_point_list,
            motion_point_parent_list=motion_point_parent_list,
        )
    
    def _find_node_by_hash(
        self,
        file_data: bytes,
        nodes_start_pos: int,
        target_hash: int,
        endian: str = '<'
    ) -> Optional[Tuple[FoxDataNode, int]]:
        """Find a FoxDataNode by its name hash using depth-first traversal.
        
        Recursively traverses the node tree via child and sibling links to locate
        the target node by its name hash. Supports hierarchical structures where
        animation nodes are nested under container nodes (e.g., MOTION under ROOT).
        
        Args:
            file_data: Complete file contents as bytes
            nodes_start_pos: Absolute offset of first node in file
            target_hash: StrCode32 hash to search for
            endian: Endianness marker
            
        Returns:
            Tuple of (FoxDataNode, absolute_position) if found, None otherwise.
            The position is the absolute offset of the node in file_data.
        """
        return self._find_node_recursive(file_data, nodes_start_pos, target_hash, endian, max_depth=64)
    
    def _find_node_recursive(
        self,
        file_data: bytes,
        node_pos: int,
        target_hash: int,
        endian: str = '<',
        max_depth: int = 64
    ) -> Optional[Tuple[FoxDataNode, int]]:
        """Recursively search for a node by hash using depth-first traversal.
        
        Traversal order: current node → first child (DFS) → next sibling.
        
        Args:
            file_data: Complete file contents as bytes
            node_pos: Absolute offset of current node to check
            target_hash: StrCode32 hash to search for
            endian: Endianness marker
            max_depth: Maximum recursion depth to prevent stack overflow from circular pointers
            
        Returns:
            Tuple of (FoxDataNode, absolute_position) if found, None otherwise
        """
        # FIX C2: Prevent infinite recursion from circular node pointers
        if max_depth <= 0:
            Debug.log_warning(f"Node search exceeded max recursion depth while looking for hash 0x{target_hash:08X}")
            return None
        
        # Read node at current position
        try:
            br = io.BytesIO(file_data)
            br.seek(node_pos)
            node = FoxDataNode.read(br, endian)
        except (EOFError, struct.error):
            return None
        
        # Check if this node matches target
        if node.name_hash == target_hash:
            return (node, node_pos)
        
        # Recursively search in children (child_node_offset is relative to this node)
        if node.child_node_offset != 0:
            child_pos = node_pos + node.child_node_offset
            result = self._find_node_recursive(file_data, child_pos, target_hash, endian, max_depth - 1)
            if result is not None:
                return result
        
        # Recursively search in next sibling (next_node_offset is relative to this node)
        if node.next_node_offset != 0:
            next_pos = node_pos + node.next_node_offset
            result = self._find_node_recursive(file_data, next_pos, target_hash, endian, max_depth - 1)
            if result is not None:
                return result
        
        return None
    
    def _read_node_payload_as_tracks(
        self,
        br: BinaryIO,
        file_data: bytes,
        node: FoxDataNode,
        node_pos: int,
        endian: str = '<'
    ) -> Tracks:
        """Read a FoxDataNode's payload as a Tracks object.
        
        Args:
            br: Binary stream
            file_data: Complete file contents as bytes (needed for keyframe blob reading)
            node: The FoxDataNode to read from
            node_pos: Absolute offset of node in file (for correct payload position calculation)
            endian: Endianness marker
            
        Returns:
            Tracks object with header and track units
        """
        if not node.has_payload:
            return Tracks(header=None, track_units=[])
        
        payload_pos = node.payload_abs_offset(node_pos)
        br.seek(payload_pos)
        
        # The payload is a TrackHeader + TrackUnit[] + TrackData[] + data blobs
        # Use the existing Tracks.read method to handle this
        # Pass file_data so data blobs (keyframe bytes) are read correctly
        tracks = Tracks.read(br, file_data=file_data, read_data_blobs=True, endian=endian)
        
        return tracks
    
    def _read_stringdata_payload(
        self,
        file_data: bytes,
        node: FoxDataNode,
        node_pos: int,
        endian: str = '<'
    ) -> List[str]:
        """Read a StringData payload from a FoxDataNode.
        
        Parses the StringData structure (from anim_common.bt):
            uint EntryCount
            EntryCount x FoxDataName { StrCode32 hash; uint StringOffset; }
        
        If StringOffset != 0, the name string is stored inline at
        entry_start + StringOffset (null-terminated UTF-8).
        Otherwise the name is resolved via dictionary lookup or hex fallback.
        
        Used for SKL_LIST, MTP_LIST, MTP_PARENT_LIST nodes.
        
        Args:
            file_data: Complete file contents as bytes
            node: The FoxDataNode with StringData payload (flags == NODE_TYPE_STRINGDATA)
            node_pos: Absolute offset of node in file
            endian: Endianness marker
            
        Returns:
            List of resolved name strings, one per entry.
        """
        if not node.has_payload:
            return []
        
        payload_pos = node.payload_abs_offset(node_pos)
        entry_count = struct.unpack_from(endian + 'I', file_data, payload_pos)[0]
        entries_start = payload_pos + 4
        
        results: List[str] = []
        for i in range(entry_count):
            entry_start = entries_start + i * 8  # FoxDataName = 4 (hash) + 4 (StringOffset)
            hash_val = struct.unpack_from(endian + 'I', file_data, entry_start)[0]
            string_offset = struct.unpack_from(endian + 'I', file_data, entry_start + 4)[0]
            
            if string_offset != 0:
                # Inline null-terminated string at entry_start + string_offset
                str_pos = entry_start + string_offset
                end = str_pos
                while end < len(file_data) and file_data[end] != 0:
                    end += 1
                name = file_data[str_pos:end].decode('utf-8', errors='replace')
            else:
                # No inline string: resolve from dictionary or fall back to hex
                name = unhash_gani_node(hash_val)
                if name is None:
                    name = f"0x{hash_val:08X}"
            
            results.append(name)
        
        return results
    
    def _read_shader_tracks(self, br: BinaryIO, file_data: bytes, shader_node: FoxDataNode, shader_node_pos: int, endian: str = '<') -> List:
        """Read SHADER node children and their animation data.
        
        The SHADER node is a container with no payload; its children are individual
        facial property animations (e.g., TENSION_CHEEKL, TENSION_CHEEKR).
        
        Args:
            br: Binary stream
            file_data: Complete file contents as bytes (needed for keyframe blob reading)
            shader_node: The SHADER container node
            shader_node_pos: Absolute offset of SHADER node in file
            endian: Endianness marker
            
        Returns:
            List of ShaderTrackWrapper objects, one per child property
        """
        
        shader_tracks = []
        
        # Traverse SHADER node's children (stored via child_node_offset)
        if shader_node.child_node_offset == 0:
            Debug.log("SHADER node has no children")
            return shader_tracks
        
        # Read first child node (child_node_offset is relative to shader node position)
        child_pos = shader_node_pos + shader_node.child_node_offset
        br.seek(child_pos)
        child_node = FoxDataNode.read(br, endian)
        
        # Traverse sibling list of property nodes
        while True:
            # Each child is a property track; get its name hash and payload (TrackHeader)
            if child_node.has_payload:
                try:
                    payload_tracks = self._read_node_payload_as_tracks(br, file_data, child_node, child_pos, endian)
                    
                    # Resolve property name from dictionary or use decimal hash fallback
                    property_name = unhash_shader_prop(child_node.name_hash)
                    if not property_name:
                        # Use '.' as separator so the decimal hash is never mistaken
                        # for a multi-segment index by parse_segment_suffix (which uses '_').
                        property_name = f"shader_prop.{child_node.name_hash}"
                    
                    # TODO(shader-export): Store property name mapping for future export path
                    Debug.log(f"  Shader property: {property_name} - {len(payload_tracks.track_units)} track unit(s)")
                    
                    shader_track = ShaderTrackWrapper(
                        property_name=property_name,
                        tracks=payload_tracks
                    )
                    shader_tracks.append(shader_track)
                except Exception as e:
                    Debug.log_warning(f"Failed to read SHADER property 0x{child_node.name_hash:08X}: {e}")
            
            # Move to next sibling (next_node_offset is relative to child node position)
            if child_node.next_node_offset == 0:
                break
            
            child_pos += child_node.next_node_offset
            br.seek(child_pos)
            child_node = FoxDataNode.read(br, endian)
        
        return shader_tracks
    
    def _synthesize_mini_header(self, tracks: Tracks) -> TrackMiniHeader:
        """Synthesize a TrackMiniHeader from a Tracks object.
        
        For old-format GANI, there is no separate TrackMiniHeader in the file,
        so we construct one from the TrackHeader and TrackUnit metadata.
        
        Args:
            tracks: Tracks object from MOTION node
            
        Returns:
            TrackMiniHeader with frame count, unit flags, and segment headers
        """
        if not tracks or not tracks.header:
            return TrackMiniHeader(
                frame_count=0,
                param_count=0,
                params=[],
                unit_flags=[],
                segment_headers=[]
            )
        
        header = tracks.header
        
        # Extract unit flags from each TrackUnit
        unit_flags = []
        for track_unit in tracks.track_units:
            unit_flags.append(track_unit.unit_flags if track_unit.unit_flags else 0)
        
        # Synthesize segment headers (one per segment of each track unit)
        segment_headers = []
        for track_unit in tracks.track_units:
            if track_unit.segments_data:
                for segment_data in track_unit.segments_data:
                    # Create Gani2TrackData objects for segment headers
                    # (data is already loaded inline in old format, so data_offset=0)
                    segment_headers.append(Gani2TrackData(
                        component_bit_size=segment_data.component_bit_size if segment_data else 0,
                        data_offset=0
                    ))
        
        return TrackMiniHeader(
            frame_count=header.frame_count,
            param_count=0,
            params=[],
            unit_flags=unit_flags,
            segment_headers=segment_headers
        )
