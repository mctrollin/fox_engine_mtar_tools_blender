"""
FoxData container types for old-format GANI files (GZ/Legacy).

FoxData is the Fox Engine binary container format used for hierarchical node trees.
Old-format GANI animation files embed animation tracks inside FoxData node structures,
unlike GANI2 (TPP) which uses flat GANI2TrackData blobs with a shared CommonInfo layout.

**Animation Node Name Hashes:**
Well-known StrCode32 hashes for animation FoxData nodes (e.g., MOTION, MTP, EVP, SHADER, 
SKL_LIST, MTP_LIST) are managed via the dictionary system (dic/mtar_dictionary.txt and related
files). Use the hash lookup utilities from py_utilities.utilities_hashing for name resolution.
"""

from dataclasses import dataclass
from typing import BinaryIO, Tuple
from enum import IntEnum
import struct


class FoxDataNodeType(IntEnum):
    """Node type flags used in FoxDataNode.flags."""
    STRINGDATA = 0  # NODE_TYPE_STRINGDATA — payload is StringData (e.g. SKL_LIST, MTP_LIST)
    TRACKS     = 1  # NODE_TYPE_TRACKS — payload is TrackHeader (e.g. MOTION, MTP, SHADER children)
    EVENTS     = 3  # NODE_TYPE_EVENTS — payload is EvpHeader (e.g. EVP)


@dataclass
class FoxDataHeader:
    """32-byte FoxData container header.
    
    Fields are stored as:
    - version (uint, 4 bytes)
    - nodes_offset (uint, 4 bytes) — offset from header start to first FoxDataNode
    - file_size (uint, 4 bytes)
    - name_hash (uint, 4 bytes) — StrCode32
    - name_string_offset (uint, 4 bytes)
    - flags (uint, 4 bytes)
    - [8 bytes padding to align(16)]
    """
    version: int
    nodes_offset: int
    file_size: int
    name_hash: int
    name_string_offset: int
    flags: int

    SIZE = 32

    @classmethod
    def read(cls, br: BinaryIO) -> Tuple['FoxDataHeader', str]:
        """Read FoxDataHeader from binary stream and detect endianness.
        
        Returns:
            Tuple of (FoxDataHeader, endian_str) where endian_str is '<' (LE) or '>' (BE)
        """
        # Peek at first 2 bytes and first uint to detect endianness
        pos = br.tell()
        
        # Read 4 bytes as little-endian uint to check magic
        raw4 = br.read(4)
        if len(raw4) < 4:
            raise EOFError('Unexpected EOF while reading FoxDataHeader for endian detection')
        
        le_version = struct.unpack('<I', raw4)[0]
        
        # Endianness detection logic from FoxData_common.bt
        # Check if reading as LE gives us 372637195 or 1144389900 (big-endian magic values)
        # or if first 2 bytes are 0x0000 (indicator of big-endian)
        is_big_endian = (le_version == 372637195 or le_version == 1144389900 or raw4[:2] == b'\x00\x00')
        endian = '>' if is_big_endian else '<'
        
        # Reset and read full header
        br.seek(pos)
        data = br.read(cls.SIZE)
        if len(data) < cls.SIZE:
            raise EOFError('Unexpected EOF while reading FoxDataHeader')
        
        # Unpack: 6 uints (24 bytes) + 8 bytes padding
        fmt = endian + 'IIIIII8x'
        version, nodes_offset, file_size, name_hash, name_string_offset, flags = struct.unpack(fmt, data)
        
        return cls(
            version=version,
            nodes_offset=nodes_offset,
            file_size=file_size,
            name_hash=name_hash,
            name_string_offset=name_string_offset,
            flags=flags
        ), endian

    def write(self, bw: BinaryIO, endian: str = '<') -> None:
        """Write FoxDataHeader to binary stream.
        
        Args:
            bw: Binary writer stream
            endian: '<' for little-endian (default), '>' for big-endian
        """
        fmt = endian + 'IIIIII8x'
        bw.write(struct.pack(fmt,
            self.version,
            self.nodes_offset,
            self.file_size,
            self.name_hash,
            self.name_string_offset,
            self.flags
        ))


@dataclass
class FoxDataNode:
    """48-byte FoxData node structure (aligned to 16 bytes).
    
    Fields are stored as:
    - name_hash (uint, 4 bytes) — StrCode32
    - name_string_offset (uint, 4 bytes)
    - flags (uint, 4 bytes)
    - data_offset (int, 4 bytes) — signed, relative to node start; 0 = no data
    - data_size (uint, 4 bytes)
    - parent_node_offset (int, 4 bytes) — relative to node start
    - child_node_offset (int, 4 bytes) — relative to node start
    - previous_node_offset (int, 4 bytes)
    - next_node_offset (int, 4 bytes) — 0 = no more siblings
    - parameters_offset (int, 4 bytes)
    - [8 bytes padding to align(16)]
    """
    name_hash: int
    name_string_offset: int
    flags: int
    data_offset: int
    data_size: int
    parent_node_offset: int
    child_node_offset: int
    previous_node_offset: int
    next_node_offset: int
    parameters_offset: int

    SIZE = 48

    @property
    def has_payload(self) -> bool:
        """Check if this node has a data payload."""
        return self.data_offset != 0 and (self.flags & 1 or self.data_size != 0)

    def payload_abs_offset(self, node_start: int) -> int:
        """Get absolute offset of payload data from file start.
        
        Args:
            node_start: Absolute offset of this node in the file
            
        Returns:
            Absolute offset to payload data
        """
        return node_start + self.data_offset

    @classmethod
    def read(cls, br: BinaryIO, endian: str = '<') -> 'FoxDataNode':
        """Read FoxDataNode from binary stream.
        
        Args:
            br: Binary reader stream
            endian: '<' for little-endian (default), '>' for big-endian
            
        Returns:
            FoxDataNode instance
        """
        data = br.read(cls.SIZE)
        if len(data) < cls.SIZE:
            raise EOFError('Unexpected EOF while reading FoxDataNode')
        
        # Unpack: 10 fields (40 bytes) + 8 bytes padding
        # fmt: 2 uint, 1 uint, 1 int, 1 uint, 5 int = IIIIIIIIII (but mixed)
        fmt = endian + 'IIIiIiiiii8x'
        name_hash, name_string_offset, flags, data_offset, data_size, \
            parent_node_offset, child_node_offset, previous_node_offset, \
            next_node_offset, parameters_offset = struct.unpack(fmt, data)
        
        return cls(
            name_hash=name_hash,
            name_string_offset=name_string_offset,
            flags=flags,
            data_offset=data_offset,
            data_size=data_size,
            parent_node_offset=parent_node_offset,
            child_node_offset=child_node_offset,
            previous_node_offset=previous_node_offset,
            next_node_offset=next_node_offset,
            parameters_offset=parameters_offset
        )

    def write(self, bw: BinaryIO, endian: str = '<') -> None:
        """Write FoxDataNode to binary stream.
        
        Args:
            bw: Binary writer stream
            endian: '<' for little-endian (default), '>' for big-endian
        """
        fmt = endian + 'IIIiIiiiii8x'
        bw.write(struct.pack(fmt,
            self.name_hash,
            self.name_string_offset,
            self.flags,
            self.data_offset,
            self.data_size,
            self.parent_node_offset,
            self.child_node_offset,
            self.previous_node_offset,
            self.next_node_offset,
            self.parameters_offset
        ))
