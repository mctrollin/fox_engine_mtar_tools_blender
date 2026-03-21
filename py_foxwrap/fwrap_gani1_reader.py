"""
Reader for old-format (FoxData) GANI animation files.

Old-format GANI files (GZ/Legacy, pre-GANI2) use FoxData node trees instead of
flat GANI2TrackData blobs. This module provides GaniReader to parse these files,
mirroring the interface of Gani2Reader but extracting data from FoxData structures.
"""

import io
import struct
from typing import Optional, List, BinaryIO, Tuple, Union, Dict

from ..py_core.core_logging import Debug

from ..py_utilities import util_hashing

from ..py_fox import fox_gani_constants as gani_const
from ..py_fox.fox_foxdata_types import FoxDataHeader, FoxDataNode, FoxDataParamType
from ..py_fox.fox_gani_types import TrackMiniHeader, EvpHeader, Gani2TrackData

from .fwrap_misc_types import Tracks, TrackUnitWrapper
from .fwrap_misc_import_types import GaniImportData, ShaderTrackWrapper
from . import fwrap_misc, fwrap_gani_helpers


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
        
        # Unified dict to collect all FoxData node parameters
        node_params: Dict[str, List[Tuple[int, Union[float, str, int]]]] = {}
        
        # --- Find ROOT (always first node, but use search for safety) ---
        root_result = self._find_node_by_hash(file_data, nodes_start, gani_const.FOXDATA_HASH_ROOT, endian)
        if root_result is None:
            raise ValueError("GANI has no ROOT node")
        root_node, root_node_pos = root_result
        
        # Check ROOT node itself for unexpected parameters
        if root_node.parameters_offset != 0:
            root_params = self._read_node_parameters(file_data, root_node_pos, root_node.parameters_offset, endian)
            if root_params:
                node_params["ROOT"] = root_params
        
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
                
                if child_hash == gani_const.FOXDATA_HASH_MOTION:
                    motion_node = child_node
                    motion_node_pos = child_pos
                elif child_hash == gani_const.FOXDATA_HASH_SHADER:
                    # Read SHADER container parameters (if present)
                    if child_node.parameters_offset != 0:
                        shader_params = self._read_node_parameters(file_data, child_pos, child_node.parameters_offset, endian)
                        if shader_params:
                            node_params["SHADER"] = shader_params
                    # Read SHADER children and their parameters
                    shader_tracks, shader_child_params = self._read_shader_tracks(br, file_data, child_node, child_pos, endian)
                    node_params.update(shader_child_params)
                else:
                    resolved = util_hashing.unhash_gani_node(child_hash) or str(child_hash)
                    # Store parameters from unhandled ROOT children
                    if child_node.parameters_offset != 0:
                        unhandled_params = self._read_node_parameters(file_data, child_pos, child_node.parameters_offset, endian)
                        if unhandled_params:
                            node_params[f"ROOT/{resolved}"] = unhandled_params
                    else:
                        Debug.log(f"Unrecognized ROOT child node '{resolved}' — no parameters, skipping")
                
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
                
                if child_hash == gani_const.FOXDATA_HASH_UNIT:
                    unit_tracks = self._read_node_payload_as_tracks(br, file_data, child_node, child_pos, endian)
                    # Store UNIT node parameters
                    if child_node.parameters_offset != 0:
                        unit_params = self._read_node_parameters(file_data, child_pos, child_node.parameters_offset, endian)
                        if unit_params:
                            node_params["MOTION/UNIT"] = unit_params
                elif child_hash == gani_const.FOXDATA_HASH_MTP:
                    mtp_raw_tracks = self._read_node_payload_as_tracks(br, file_data, child_node, child_pos, endian)
                    # Store MTP node parameters
                    if child_node.parameters_offset != 0:
                        mtp_params = self._read_node_parameters(file_data, child_pos, child_node.parameters_offset, endian)
                        if mtp_params:
                            node_params["MOTION/MTP"] = mtp_params
                elif child_hash == gani_const.FOXDATA_HASH_EVP:
                    events = fwrap_gani_helpers.read_evp_header(file_data, child_node.payload_abs_offset(child_pos), endian)
                    # Store EVP node parameters
                    if child_node.parameters_offset != 0:
                        evp_params = self._read_node_parameters(file_data, child_pos, child_node.parameters_offset, endian)
                        if evp_params:
                            node_params["MOTION/EVP"] = evp_params
                elif child_hash == gani_const.FOXDATA_HASH_SKL_LIST:
                    skeleton_list = self._read_stringdata_payload(file_data, child_node, child_pos, endian)
                    # Store SKL_LIST node parameters
                    if child_node.parameters_offset != 0:
                        skl_params = self._read_node_parameters(file_data, child_pos, child_node.parameters_offset, endian)
                        if skl_params:
                            node_params["MOTION/SKL_LIST"] = skl_params
                elif child_hash == gani_const.FOXDATA_HASH_MTP_LIST:
                    motion_point_list = self._read_stringdata_payload(file_data, child_node, child_pos, endian)
                    # Store MTP_LIST node parameters
                    if child_node.parameters_offset != 0:
                        mtp_list_params = self._read_node_parameters(file_data, child_pos, child_node.parameters_offset, endian)
                        if mtp_list_params:
                            node_params["MOTION/MTP_LIST"] = mtp_list_params
                elif child_hash == gani_const.FOXDATA_HASH_MTP_PARENT_LIST:
                    motion_point_parent_list = self._read_stringdata_payload(file_data, child_node, child_pos, endian)
                    # Store MTP_PARENT_LIST node parameters
                    if child_node.parameters_offset != 0:
                        mtp_parent_params = self._read_node_parameters(file_data, child_pos, child_node.parameters_offset, endian)
                        if mtp_parent_params:
                            node_params["MOTION/MTP_PARENT_LIST"] = mtp_parent_params
                else:
                    resolved = util_hashing.unhash_gani_node(child_hash) or str(child_hash)
                    # Store parameters from unhandled MOTION children
                    if child_node.parameters_offset != 0:
                        unhandled_motion_params = self._read_node_parameters(file_data, child_pos, child_node.parameters_offset, endian)
                        if unhandled_motion_params:
                            node_params[f"MOTION/{resolved}"] = unhandled_motion_params
                    else:
                        Debug.log(f"Unimplemented MOTION child node '{resolved}' — no parameters, skipping")
                
                if child_node.next_node_offset == 0:
                    break
                child_pos += child_node.next_node_offset
        
        # --- Convert UNIT tracks to TrackUnitWrapper list ---
        bone_tracks = fwrap_gani_helpers.finalize_bone_tracks(
            fwrap_misc.build_gani_tracks_from_tracks(unit_tracks),
            skeleton_list=skeleton_list,
            label=f"Read gani @ (0x{gani_start:X})"
        )

        # --- Convert MTP tracks if present ---
        mtp_tracks: List[TrackUnitWrapper] = []
        motion_point_layout: Optional[Tracks] = None
        motion_point_track_header = None
        if mtp_raw_tracks is not None:
            # Always use decimal hash strings for motion point track names (no prefix, no unhashing).
            # This ensures FCurve bone paths match the decimal hash bone names in the motion points armature.
            # SKL_LIST/MTP_LIST name overrides are intentionally skipped here.
            mtp_tracks = fwrap_gani_helpers.finalize_motion_point_tracks(fwrap_misc.build_gani_tracks_from_tracks(mtp_raw_tracks))
            motion_point_layout = mtp_raw_tracks
            motion_point_track_header = mtp_raw_tracks.header
        
        # Store MOTION node parameters
        if motion_node.parameters_offset != 0:
            motion_params = self._read_node_parameters(
                file_data, motion_node_pos, motion_node.parameters_offset, endian
            )
            if motion_params:
                node_params["MOTION"] = motion_params
        
        # Synthesize TrackMiniHeader from UNIT TrackHeader (no MOTION params for old format)
        track_mini_header = self._synthesize_mini_header(unit_tracks)
        
        Debug.log(f"Loaded old-format GANI: {len(bone_tracks)} bone track(s), "
                  f"{len(mtp_tracks)} motion point track(s), {len(shader_tracks)} shader track(s), "
                  f"events={'yes' if events else 'no'}, "
                  f"skeleton_list={len(skeleton_list) if skeleton_list else 0} entries")
        
        return GaniImportData.from_gani1(
            gani_bone_tracks=bone_tracks,
            gani_mtp_tracks=mtp_tracks,
            gani_events=events,
            gani_layout_track=unit_tracks,
            gani_track_mini_header=track_mini_header,
            gani_motion_point_layout=motion_point_layout,
            gani_motion_point_track_header=motion_point_track_header,
            gani1_shader_tracks=shader_tracks,
            gani_skeleton_list=skeleton_list,
            gani1_motion_point_list=motion_point_list,
            gani1_motion_point_parent_list=motion_point_parent_list,
            gani_node_params=node_params,
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
                name = util_hashing.unhash_gani_node(hash_val)
                if name is None:
                    name = f"0x{hash_val:08X}"
            
            results.append(name)
        
        return results
    
    def _read_shader_tracks(self, br: BinaryIO, file_data: bytes, shader_node: FoxDataNode, shader_node_pos: int, endian: str = '<') -> Tuple[List[ShaderTrackWrapper], Dict[str, List[Tuple[int, Union[float, str, int]]]]]:
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
            Tuple of:
                - List of ShaderTrackWrapper objects, one per child property
                - Dict mapping SHADER/{property_name} keys to their parameter lists
        """
        
        shader_tracks = []
        shader_child_params: Dict[str, List[Tuple[int, Union[float, str, int]]]] = {}
        
        # Traverse SHADER node's children (stored via child_node_offset)
        if shader_node.child_node_offset == 0:
            Debug.log("SHADER node has no children")
            return shader_tracks, shader_child_params
        
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
                    
                    # Read and store child node's parameters
                    child_params = self._read_node_parameters(
                        file_data, child_pos, child_node.parameters_offset, endian
                    )
                    
                    # Resolve property name from dictionary or use decimal hash fallback
                    property_name = util_hashing.unhash_shader_prop(child_node.name_hash)
                    if not property_name:
                        # Use '.' as separator so the decimal hash is never mistaken
                        # for a multi-segment index by parse_segment_suffix (which uses '_').
                        property_name = f"shader_prop.{child_node.name_hash}"
                    
                    # Store child parameters in node_params dict
                    if child_params:
                        shader_child_params[f"SHADER/{property_name}"] = child_params
                    
                    # TODO(shader-export): Store property name mapping for future export path
                    Debug.log(f"  Shader property: {property_name} - {len(payload_tracks.track_units)} track unit(s)")
                    
                    shader_track = ShaderTrackWrapper(
                        property_name=property_name,
                        tracks=payload_tracks,
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
        
        return shader_tracks, shader_child_params
    
    def _read_node_parameters(
        self,
        file_data: bytes,
        node_start: int,
        parameters_offset: int,
        endian: str = '<',
    ) -> List[Tuple[int, Union[float, str, int]]]:
        """Read the FoxDataNodeParameter chain attached to a node.

        Entry sizes depend on type (from bt template):

            - FLOAT  (type=2): 16 bytes — ``uint32 name_hash, uint32 name_str_off, float value``
            - UINT   (type=0): 16 bytes — ``uint32 name_hash, uint32 name_str_off, uint32 value``
            - STRING (type=1): 20 bytes — ``uint32 name_hash, uint32 name_str_off,
              uint32 value_hash, uint32 value_str_off``.  When ``value_str_off != 0``
              the null-terminated string is at ``param_pos + 12 + value_str_off``.

        Traversal follows the ``NextParameterOffset`` chain:
        ``param_pos += param.NextParameterOffset`` until ``NextParameterOffset == 0``.

        Args:
            file_data:         Complete file contents as bytes.
            node_start:        Absolute offset of the owning FoxDataNode in *file_data*.
            parameters_offset: Value of ``FoxDataNode.parameters_offset`` (relative to
                               *node_start*).  If 0, returns an empty list immediately.
            endian:            Endianness marker (``'<'`` little, ``'>'`` big).

        Returns:
            List of ``(name_hash, value)`` tuples:

            - FLOAT  parameters: ``value`` is ``float``.
            - STRING with inline string (``value_str_off != 0``): ``value`` is ``str``.
            - STRING hash-only (``value_str_off == 0``): ``value`` is ``int``
              (the raw ``value_hash`` stored as a decimal integer in metadata).
            - UINT and unknown types: skipped with a warning.
        """
        if parameters_offset == 0:
            return []

        results: List[Tuple[int, Union[float, str, int]]] = []
        param_pos = node_start + parameters_offset
        _PARAM_MIN_SIZE    = 16  # minimum entry size (FLOAT / UINT)
        _PARAM_STRING_SIZE = 20  # STRING entry size (Value is a FoxDataName = 8 bytes)

        while True:
            if param_pos + _PARAM_MIN_SIZE > len(file_data):
                Debug.log_warning(
                    f"_read_node_parameters: parameter at 0x{param_pos:X} exceeds "
                    f"file bounds (file size 0x{len(file_data):X}) — stopping early"
                )
                break

            # Read common header: type (H=ushort), next_off (h=signed short),
            # name_hash (I), name_str_off (I)  — 12 bytes total
            type_code, next_off, name_hash, _name_str_off = struct.unpack_from(
                endian + 'HhII', file_data, param_pos
            )

            if type_code == FoxDataParamType.FLOAT:
                value = struct.unpack_from(endian + 'f', file_data, param_pos + 12)[0]
                results.append((name_hash, value))

            elif type_code == FoxDataParamType.STRING:
                if param_pos + _PARAM_STRING_SIZE > len(file_data):
                    resolved = util_hashing.unhash_param_name(name_hash) or f"0x{name_hash:08X}"
                    Debug.log_warning(
                        f"_read_node_parameters: STRING parameter '{resolved}' at "
                        f"0x{param_pos:X} exceeds file bounds — stopping early"
                    )
                    break
                value_hash, value_str_off = struct.unpack_from(
                    endian + 'II', file_data, param_pos + 12
                )
                if value_str_off != 0:
                    # Inline string: null-terminated at param_pos + 12 + value_str_off
                    str_pos = param_pos + 12 + value_str_off
                    end = str_pos
                    while end < len(file_data) and file_data[end] != 0:
                        end += 1
                    str_value = file_data[str_pos:end].decode('utf-8', errors='replace')
                    results.append((name_hash, str_value))
                else:
                    # Hash-only: no inline string.  Store the raw value_hash as int so
                    # the writer can reproduce a hash-only STRING entry (no '.' in
                    # the serialised form keeps it distinct from FLOAT).
                    results.append((name_hash, value_hash))

            elif type_code == FoxDataParamType.UINT:
                resolved = util_hashing.unhash_param_name(name_hash) or f"0x{name_hash:08X}"
                Debug.log_warning(
                    f"_read_node_parameters: parameter '{resolved}' has unsupported type "
                    f"UINT (0) — skipping"
                )
            else:
                resolved = util_hashing.unhash_param_name(name_hash) or f"0x{name_hash:08X}"
                Debug.log_warning(
                    f"_read_node_parameters: parameter '{resolved}' has unknown type "
                    f"{type_code} — skipping"
                )

            if next_off == 0:
                break
            param_pos += next_off

        return results

    def _synthesize_mini_header(self, tracks: Tracks, params: Optional[List[Tuple[int, Union[float, str, int]]]] = None) -> TrackMiniHeader:
        """Synthesize a TrackMiniHeader from a Tracks object.
        
        For old-format GANI, there is no separate TrackMiniHeader in the file,
        so we construct one from the TrackHeader and TrackUnit metadata.
        
        Args:
            tracks: Tracks object from MOTION node.
            params: Optional parameter list (GANI2 only; for old-format, MOTION and all
                other node params are collected into ``GaniImportData.node_params`` instead).
            
        Returns:
            TrackMiniHeader with frame count, unit flags, segment headers, and params.
        """
        effective_params = params if params is not None else []

        if not tracks or not tracks.header:
            return TrackMiniHeader(
                frame_count=0,
                param_count=len(effective_params),
                params=effective_params,
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
            param_count=len(effective_params),
            params=effective_params,
            unit_flags=unit_flags,
            segment_headers=segment_headers
        )
