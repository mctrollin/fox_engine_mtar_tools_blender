from dataclasses import dataclass
from typing import List, BinaryIO
from enum import IntEnum
import struct

from .fox_misc_types import PathCode64, StrCode32


class MtarFlags(IntEnum):
    UseMini = 0x1000 # MTAR_FLAGS_USE_MINI
    HasSkelList = 0x4000 # MTAR_FLAGS_HAS_SKEL_LIST


@dataclass
class MtarHeader:
    version: int # uint
    file_count: int # uint 
    track_count: int # ushort /UnitCount
    segment_count: int # ushort
    shader_node_count: int # ushort
    shader_unit_count: int # ushort / shader unit count
    motion_point_unit_count: int # ushort
    flags: MtarFlags # ushort
    common_info_offset: int # uint
    padding: int # ulong

    SIZE = 32  # computed from MtarHeader fields (4+4+2+2+2+2+2+2+4+8)

    @classmethod
    def read(cls, br: BinaryIO) -> 'MtarHeader':
        # Read fields in the same order as written in the file
        data = br.read(cls.SIZE)
        if len(data) < cls.SIZE:
            raise EOFError('Unexpected EOF while reading MtarHeader')
        version, file_count, track_count, segment_count, shader_node_count, shader_unit_count, motion_point_unit_count, flags, common_info_offset, padding = struct.unpack('<IIHHHHHHIQ', data)
        return cls(
            version=version,
            file_count=file_count,
            track_count=track_count,
            segment_count=segment_count,
            shader_node_count=shader_node_count,
            shader_unit_count=shader_unit_count,
            motion_point_unit_count=motion_point_unit_count,
            flags=flags,
            common_info_offset=common_info_offset,
            padding=padding,
        )
    
    def write(self, bw: BinaryIO) -> None:
        """Write MtarHeader to binary stream."""
        bw.write(struct.pack('<IIHHHHHHIQ',
            self.version,
            self.file_count,
            self.track_count,
            self.segment_count,
            self.shader_node_count,
            self.shader_unit_count,
            self.motion_point_unit_count,
            self.flags,
            self.common_info_offset,
            self.padding
        ))

@dataclass
class MtarTableList2:
    path: PathCode64  # PathCode64
    tracks_offset: int # uint
    tracks_data_size: int # ushort (this << 4)
    motion_point_tracks_offset: int # ushort (this << 4)
    motion_point_tracks_data_size: int # ushort (this << 4)
    shader_tracks_offset: int # ushort (this << 4)
    shader_tracks_data_size : int # ushort (this << 4)
    padding0: int # ushort
    motion_events_offset: int # uint
    padding1: int # uint

    SIZE = 32

    @classmethod
    def read(cls, br: BinaryIO) -> 'MtarTableList2':
        data = br.read(cls.SIZE)
        if len(data) < cls.SIZE:
            raise EOFError('Unexpected EOF while reading MtarTableList2')
        path, tracks_offset, tracks_data_size, motion_point_tracks_offset, motion_point_tracks_data_size, shader_tracks_offset, shader_tracks_data_size, padding0, motion_events_offset, padding1 = struct.unpack('<QIHHHHHHII', data)
        return cls(
            path=path,
            tracks_offset=tracks_offset,
            tracks_data_size=tracks_data_size * 16,
            motion_point_tracks_offset=motion_point_tracks_offset * 16,
            motion_point_tracks_data_size=motion_point_tracks_data_size * 16,
            shader_tracks_offset=shader_tracks_offset * 16,
            shader_tracks_data_size=shader_tracks_data_size * 16,
            padding0=padding0,
            motion_events_offset=motion_events_offset,
            padding1=padding1,
        )
    
    def write(self, bw: BinaryIO) -> None:
        """Write MtarTableList2 to binary stream."""
        bw.write(struct.pack('<QIHHHHHHII',
            self.path,
            self.tracks_offset,
            self.tracks_data_size // 16,
            self.motion_point_tracks_offset // 16,
            self.motion_point_tracks_data_size // 16,
            self.shader_tracks_offset // 16,
            self.shader_tracks_data_size // 16,
            self.padding0,
            self.motion_events_offset,
            self.padding1
        ))

@dataclass
class MtarTableList:
    path: PathCode64  # PathCode64
    tracks_offset: int # uint
    tracks_data_size: int # ushort (this << 4)
    unknown: int # ushort

    SIZE = 16

    @classmethod
    def read(cls, br: BinaryIO) -> 'MtarTableList':
        data = br.read(cls.SIZE)
        if len(data) < cls.SIZE:
            raise EOFError('Unexpected EOF while reading MtarTableList')
        path, tracks_offset, tracks_data_size, unknown = struct.unpack('<QIHH', data)
        return cls(
            path=path,
            tracks_offset=tracks_offset,
            tracks_data_size=tracks_data_size,  # old format: raw value = FoxData FileSize (no ×16)
            unknown=unknown,
        )
    
    def write(self, bw: BinaryIO) -> None:
        """Write MtarTableList to binary stream (old-format MTAR, 16 bytes)."""
        # old format: tracks_data_size stored as raw FoxData FileSize (no >>4 shift)
        bw.write(struct.pack('<QIHH',
            self.path,
            self.tracks_offset,
            self.tracks_data_size,
            self.unknown
        ))
    

@dataclass
class MtarMiniDataNode:
    name: StrCode32
    data_size: int
    next_node_offset: int
    padding: int

    SIZE = 16

    @classmethod
    def read(cls, br: BinaryIO) -> 'MtarMiniDataNode':
        data = br.read(cls.SIZE)
        if len(data) < cls.SIZE:
            raise EOFError('Unexpected EOF while reading MtarMiniDataNode')
        name_int, data_size, next_node_offset, padding = struct.unpack('<IIII', data)
        return cls(name=StrCode32(name_int), data_size=data_size, next_node_offset=next_node_offset, padding=padding)
    
    def write(self, bw: BinaryIO) -> None:
        """Write MtarMiniDataNode to binary stream."""
        # Write name (should be StrCode32)
        bw.write(struct.pack('<IIII',
            self.name.to_int(),
            self.data_size,
            self.next_node_offset,
            self.padding
        ))


@dataclass
class MotionPointEntry:
    name: StrCode32
    parent_name: StrCode32


@dataclass
class MotionPointList2:
    count: int
    entries: List[MotionPointEntry]

    @classmethod
    def read(cls, br: BinaryIO) -> 'MotionPointList2':
        count_raw = br.read(4)
        if len(count_raw) < 4:
            raise EOFError('Unexpected EOF while reading MotionPointList2 count')
        count = struct.unpack('<I', count_raw)[0]
        entries: List[MotionPointEntry] = []
        for _ in range(count):
            raw = br.read(8)
            if len(raw) < 8:
                raise EOFError('Unexpected EOF while reading MotionPointList2 entry')
            name_int, parent_name_int = struct.unpack('<II', raw)
            entries.append(MotionPointEntry(name=StrCode32(name_int), parent_name=StrCode32(parent_name_int)))
        return cls(count=count, entries=entries)
    
    def write(self, bw: BinaryIO) -> None:
        """Write MotionPointList2 to binary stream."""
        # Write count
        bw.write(struct.pack('<I', self.count))
        
        # Write entries (name and parent_name should be StrCode32)
        for entry in self.entries:
            bw.write(struct.pack('<II', entry.name.to_int(), entry.parent_name.to_int()))
