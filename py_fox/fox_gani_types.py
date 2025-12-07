"""
Types for GANI2 animation data structures in Metal Gear Solid V.
"""
import io
from dataclasses import dataclass
from typing import BinaryIO, List, Optional
import struct

from ..py_utilities.binary_utilities_write import write_padding, align_length
from ..py_utilities.binary_utilities_read import (
    read_unaligned_bits,
    read_unaligned_quaternion,
    read_float,
    read_vector2,
    read_vector3,
    read_vector4,
)
from ..py_utilities.binary_utilities_write import (
    write_unaligned_bits,
    write_unaligned_quaternion,
    write_float,
    write_vector2,
    write_vector3,
    write_vector4,
    align_buffer,
    align_bytearray,
)
from ..py_utilities.logging_utilities import log_message

from .fox_misc_types import StrCode32
from .fox_gani_enums import SegmentType, TrackUnitFlags


@dataclass
class SegmentKeyframeData:
    """Container for a decoded segment keyframe value.

    This is a small, flexible holder that represents the different kinds
    of data a track segment can hold: quaternion (4 components), vector2/3/4
    or a single float. Consumers should look at `values` length and the
    associated `track_type` where available to interpret the contents.
    """
    # Raw track type (use SegmentType enum when available); optional because
    # some uses only need the values array.
    # track_type: SegmentType | None = None

    # Flat list of floats representing the value. For quaternions length==4,
    # vector3 length==3, vector2 length==2, float length==1, etc.
    # values: List[float] = None
    value: any = None


@dataclass
class AnimKeyframe:
    """A time-stamped keyframe for a segment.

    `value` is a SegmentKeyframeData instance containing the decoded components.
    """
    frame_count: int
    data: SegmentKeyframeData

    def __init__(self, frame: int, value: any):
        self.frame_count=frame
        self.data=SegmentKeyframeData(value=value)

    @staticmethod
    def read_list_from_bytes(file_data: bytes, data_offset: int, 
                             segment_type: SegmentType, 
                             component_bit_size: int, unit_flags: int, 
                             frame_count: int) -> List['AnimKeyframe']:
        """Read keyframe animation data from binary format.
        
        This is the unified keyframe reading logic used by both GANI2 tracks and 
        TrackHeader-based tracks (layout/motion point tracks).
        
        Args:
            file_data: Complete file data buffer
            data_offset: Absolute byte offset where the blob data starts
            segment_type: Type of segment (QUAT, VECTOR3, FLOAT, etc.)
            component_bit_size: Bit size for components (12, 13, 15, 16, 32)
            unit_flags: Track unit flags (contains IS_STATIC flag)
            frame_count: Total number of frames in the animation
            
        Returns:
            List of AnimKeyframe objects containing the keyframe data
        """
        keyframes = []
        current_frame = 0
        is_static = (unit_flags & TrackUnitFlags.IS_STATIC) != 0
        
        # Rotations (Quaternions)
        if segment_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
            bit_pos = data_offset * 8  # Convert byte offset to bit offset
            quat, bit_pos = read_unaligned_quaternion(file_data, bit_pos, component_bit_size)
            keyframes.append(AnimKeyframe(frame=current_frame, value=quat))
            
            if not is_static:
                while current_frame < frame_count:
                    frame_delta, bit_pos = read_unaligned_bits(file_data, bit_pos, 8)
                    current_frame += frame_delta
                    quat, bit_pos = read_unaligned_quaternion(file_data, bit_pos, component_bit_size)
                    keyframes.append(AnimKeyframe(frame=current_frame, value=quat))
        
        # 3D Vectors (Positions, etc.)
        elif segment_type in [SegmentType.VECTOR3, SegmentType.VECTOR_DIFF]:
            offset = data_offset
            vec, offset = read_vector3(file_data, offset)
            keyframes.append(AnimKeyframe(frame=current_frame, value=vec))
            
            if not is_static:
                while current_frame < frame_count:
                    frame_delta = file_data[offset]
                    offset += 1
                    current_frame += frame_delta
                    vec, offset = read_vector3(file_data, offset)
                    keyframes.append(AnimKeyframe(frame=current_frame, value=vec))
        
        # Floats (Single values)
        elif segment_type == SegmentType.FLOAT:
            offset = data_offset
            value, offset = read_float(file_data, offset)
            keyframes.append(AnimKeyframe(frame=current_frame, value=[value]))
            
            if not is_static:
                while current_frame < frame_count:
                    frame_delta = file_data[offset]
                    offset += 1
                    current_frame += frame_delta
                    value, offset = read_float(file_data, offset)
                    keyframes.append(AnimKeyframe(frame=current_frame, value=[value]))
        
        # 2D Vectors
        elif segment_type == SegmentType.VECTOR2:
            offset = data_offset
            vec, offset = read_vector2(file_data, offset)
            keyframes.append(AnimKeyframe(frame=current_frame, value=vec))
            
            if not is_static:
                while current_frame < frame_count:
                    frame_delta = file_data[offset]
                    offset += 1
                    current_frame += frame_delta
                    vec, offset = read_vector2(file_data, offset)
                    keyframes.append(AnimKeyframe(frame=current_frame, value=vec))
        
        # 4D Vectors
        elif segment_type == SegmentType.VECTOR4:
            offset = data_offset
            vec, offset = read_vector4(file_data, offset)
            keyframes.append(AnimKeyframe(frame=current_frame, value=vec))
            
            if not is_static:
                while current_frame < frame_count:
                    frame_delta = file_data[offset]
                    offset += 1
                    current_frame += frame_delta
                    vec, offset = read_vector4(file_data, offset)
                    keyframes.append(AnimKeyframe(frame=current_frame, value=vec))
        
        else:
            raise ValueError(f"Unsupported segment type: {segment_type}")
        
        return keyframes

    @staticmethod
    def write_list_to_bytes(keyframes: List['AnimKeyframe'], track_type: SegmentType, 
                            component_bit_size: int, unit_flags: int) -> bytes:
        """Write keyframe data to binary format.
        
        Args:
            keyframes: List of AnimKeyframe objects with frame_count and data.value
            track_type: SegmentType enum value (QUAT, VECTOR3, FLOAT, etc.)
            component_bit_size: Bit size for components (used for quaternions)
            unit_flags: TrackUnitFlags to determine if track has multiple frames
            
        Returns:
            Binary keyframe data
        """
        # For static tracks (single keyframe), we only write the initial value
        if not keyframes:
            return b''
        
        has_frames = TrackUnitFlags.has_frames(unit_flags)
        
        # Handle different track types
        if track_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
            # Write quaternion keyframes
            buffer = bytearray()
            bit_pos = 0
            
            # Write initial quaternion (bit-packed)
            initial_quat = keyframes[0].data.value
            bit_pos = write_unaligned_quaternion(buffer, bit_pos, initial_quat, component_bit_size)
            
            # Write subsequent keyframes if this is not a static track
            if has_frames and len(keyframes) > 1:
                for i in range(1, len(keyframes)):
                    # Calculate frame delta
                    frame_delta = keyframes[i].frame_count - keyframes[i-1].frame_count
                    
                    # Validate: frame_delta must be at least 1
                    if frame_delta < 1:
                        log_message(f"  Warning: Invalid frame_delta {frame_delta} between keyframes {i-1} and {i}")
                        log_message(f"  Keyframe {i-1}: frame_count={keyframes[i-1].frame_count}")
                        log_message(f"  Keyframe {i}: frame_count={keyframes[i].frame_count}")
                        frame_delta = 1  # Clamp to minimum value
                    
                    # Write frame delta (8 bits)
                    bit_pos = write_unaligned_bits(buffer, bit_pos, frame_delta, 8)
                    
                    # Write quaternion
                    quat = keyframes[i].data.value
                    bit_pos = write_unaligned_quaternion(buffer, bit_pos, quat, component_bit_size)
            
            # Ensure buffer is byte-aligned first (round up bit_pos to next byte)
            byte_size = (bit_pos + 7) // 8
            if len(buffer) < byte_size:
                buffer.extend(bytes(byte_size - len(buffer)))
            
            # Align to 2-byte boundary (FAlign(2) in binary template)
            align_bytearray(buffer, 2)
            
            return bytes(buffer)
        
        elif track_type in [SegmentType.VECTOR3, SegmentType.VECTOR_DIFF]:
            # Write vector3 keyframes
            buffer = io.BytesIO()
            
            # Write initial vector3
            initial_vec = keyframes[0].data.value
            write_vector3(buffer, initial_vec)
            
            # Write subsequent keyframes if this is not a static track
            if has_frames and len(keyframes) > 1:
                for i in range(1, len(keyframes)):
                    # Calculate frame delta
                    frame_delta = keyframes[i].frame_count - keyframes[i-1].frame_count
                    
                    # Validate: frame_delta must be at least 1
                    if frame_delta < 1:
                        log_message(f"  Warning: Invalid frame_delta {frame_delta} between keyframes {i-1} and {i}")
                        log_message(f"  Keyframe {i-1}: frame_count={keyframes[i-1].frame_count}")
                        log_message(f"  Keyframe {i}: frame_count={keyframes[i].frame_count}")
                        frame_delta = 1  # Clamp to minimum value
                    
                    # Write frame delta (1 byte)
                    buffer.write(bytes([frame_delta & 0xFF]))
                    
                    # Write vector3
                    vec = keyframes[i].data.value
                    write_vector3(buffer, vec)
            
            # Align to 2-byte boundary (FAlign(2) in binary template)
            align_buffer(buffer, 2)
            
            return buffer.getvalue()
        
        elif track_type == SegmentType.FLOAT:
            # Write float keyframes
            buffer = io.BytesIO()
            
            # Write initial float
            initial_value = keyframes[0].data.value[0] if isinstance(keyframes[0].data.value, list) else keyframes[0].data.value
            write_float(buffer, initial_value)
            
            # Write subsequent keyframes if this is not a static track
            if has_frames and len(keyframes) > 1:
                for i in range(1, len(keyframes)):
                    # Calculate frame delta
                    frame_delta = keyframes[i].frame_count - keyframes[i-1].frame_count
                    
                    # Validate: frame_delta must be at least 1
                    if frame_delta < 1:
                        log_message(f"  Warning: Invalid frame_delta {frame_delta} between keyframes {i-1} and {i}")
                        log_message(f"  Keyframe {i-1}: frame_count={keyframes[i-1].frame_count}")
                        log_message(f"  Keyframe {i}: frame_count={keyframes[i].frame_count}")
                        frame_delta = 1  # Clamp to minimum value
                    
                    # Write frame delta (1 byte)
                    buffer.write(bytes([frame_delta & 0xFF]))
                    
                    # Write float
                    value = keyframes[i].data.value[0] if isinstance(keyframes[i].data.value, list) else keyframes[i].data.value
                    write_float(buffer, value)
            
            # Align to 2-byte boundary (FAlign(2) in binary template)
            align_buffer(buffer, 2)
            
            return buffer.getvalue()
        
        elif track_type == SegmentType.VECTOR2:
            # Write vector2 keyframes
            buffer = io.BytesIO()
            
            # Write initial vector2
            initial_vec = keyframes[0].data.value
            write_vector2(buffer, initial_vec)
            
            # Write subsequent keyframes if this is not a static track
            if has_frames and len(keyframes) > 1:
                for i in range(1, len(keyframes)):
                    # Calculate frame delta
                    frame_delta = keyframes[i].frame_count - keyframes[i-1].frame_count
                    
                    # Validate: frame_delta must be at least 1
                    if frame_delta < 1:
                        log_message(f"  Warning: Invalid frame_delta {frame_delta} between keyframes {i-1} and {i}")
                        log_message(f"  Keyframe {i-1}: frame_count={keyframes[i-1].frame_count}")
                        log_message(f"  Keyframe {i}: frame_count={keyframes[i].frame_count}")
                        frame_delta = 1  # Clamp to minimum value
                    
                    # Write frame delta (1 byte)
                    buffer.write(bytes([frame_delta & 0xFF]))
                    
                    # Write vector2
                    vec = keyframes[i].data.value
                    write_vector2(buffer, vec)
            
            # Align to 2-byte boundary (FAlign(2) in binary template)
            align_buffer(buffer, 2)
            
            return buffer.getvalue()
        
        elif track_type == SegmentType.VECTOR4:
            # Write vector4 keyframes
            buffer = io.BytesIO()
            
            # Write initial vector4
            initial_vec = keyframes[0].data.value
            write_vector4(buffer, initial_vec)
            
            # Write subsequent keyframes if this is not a static track
            if has_frames and len(keyframes) > 1:
                for i in range(1, len(keyframes)):
                    # Calculate frame delta
                    frame_delta = keyframes[i].frame_count - keyframes[i-1].frame_count
                    
                    # Validate: frame_delta must be at least 1
                    if frame_delta < 1:
                        log_message(f"  Warning: Invalid frame_delta {frame_delta} between keyframes {i-1} and {i}")
                        log_message(f"  Keyframe {i-1}: frame_count={keyframes[i-1].frame_count}")
                        log_message(f"  Keyframe {i}: frame_count={keyframes[i].frame_count}")
                        frame_delta = 1  # Clamp to minimum value
                    
                    # Write frame delta (1 byte)
                    buffer.write(bytes([frame_delta & 0xFF]))
                    
                    # Write vector4
                    vec = keyframes[i].data.value
                    write_vector4(buffer, vec)
            
            # Align to 2-byte boundary (FAlign(2) in binary template)  
            align_buffer(buffer, 2)
            
            return buffer.getvalue()
        
        else:
            raise ValueError(f"Unsupported track type: {track_type}")



@dataclass
class TrackHeader:
    unit_count: int # int
    segment_count: int # uint
    t_id: int # ushort
    unknown_a: int # byte
    unknown_b: int # byte
    frame_count: int # uint
    frame_rate: int # byte (read uint but skip 3 bytes)


    BASE_SIZE = 20  # TrackHeader size (4+4+2+1+1+4+4)

    unit_offsets: List[int]

    @classmethod
    def read(cls, br: BinaryIO) -> 'TrackHeader':

        data = br.read(cls.BASE_SIZE)
        if len(data) < cls.BASE_SIZE:
            raise EOFError('Unexpected EOF while reading TrackHeader')
        unit_count, segment_count, t_id, unknown_a, unknown_b, frame_count, frame_rate = struct.unpack('<IIHBBII', data)

        unit_offsets: List[int] = []
        for _ in range(unit_count):
            unit_offsets.append(struct.unpack('<I', br.read(4))[0])

        return cls(
            unit_count=unit_count,
            segment_count=segment_count,
            t_id=t_id,
            unknown_a=unknown_a,
            unknown_b=unknown_b,
            frame_count=frame_count,
            frame_rate=frame_rate,
            unit_offsets=unit_offsets
        )
    
    def write(self, bw: BinaryIO) -> None:
        """Write TrackHeader to binary stream."""
        # Write base fields
        bw.write(struct.pack('<IIHBBII', 
            self.unit_count,
            self.segment_count,
            self.t_id,
            self.unknown_a,
            self.unknown_b,
            self.frame_count,
            self.frame_rate
        ))
        
        # Write unit offsets
        for offset in self.unit_offsets:
            bw.write(struct.pack('<I', offset))
        
        # Write 12 bytes of padding after unit offsets (observed in binary files)
        write_padding(bw, 12)


@dataclass
class TrackUnit:
    name: StrCode32
    segment_count: int # byte
    unit_flags: int
    padding: int # ushort

    BASE_SIZE = 8  # TrackUnit base (name:4 + seg_count:1 + flags:1 + padding:2)

    segments_data: List['TrackData']


    @classmethod
    def read(cls, br: BinaryIO) -> 'TrackUnit':
        # Read base fields
        base = br.read(cls.BASE_SIZE)
        if len(base) < cls.BASE_SIZE:
            raise EOFError('Unexpected EOF while reading TrackUnit base')
        name_int, segment_count, unit_flags, padding = struct.unpack('<IBBH', base)

        track_data: List[TrackData] = []
        for _ in range(segment_count):
            # Delegate reading/parsing of a TrackData entry to TrackData.read
            track_data.append(TrackData.read(br))

        return cls(
            name=StrCode32(name_int),
            segment_count=segment_count,
            unit_flags=unit_flags,
            padding=padding,
            segments_data=track_data,
        )
    
    def write(self, bw: BinaryIO) -> None:
        """Write TrackUnit to binary stream."""
        # Write base fields (name should be StrCode32)
        bw.write(struct.pack('<IBBH',
            self.name.to_int(),
            self.segment_count,
            self.unit_flags,
            self.padding
        ))
        
        # Write track data entries
        for segment_data in self.segments_data:
            segment_data.write(bw)


@dataclass
class TrackData:
    data_offset: int # int
    ms_id: int # short
    td_type: SegmentType # 4 bits
    next_entry_offset: int # 4 bits
    component_bit_size: int # byte
    
    # Optional: The actual keyframe data blob (populated when reading motion point tracks)
    # For layout tracks, this remains None even though data_offset may be non-zero - we don't yet know how it works for the layout track
    data_blob: Optional[List['AnimKeyframe']] = None

    ENTRY_SIZE = 8

    @classmethod
    def read(cls, br: BinaryIO) -> 'TrackData':
        """Read a TrackData entry (8 bytes) from the given BinaryIO and return a TrackData instance.

        The format is: data_offset (int32), motion_segment_id (int16), type_and_next (uint8), component_bit_size (uint8)
        type_and_next packs the track_type in the low 4 bits and next_entry_offset in the high 4 bits.
        
        Note: This only reads the TrackData structure itself, not the data blob it points to.
        The data blob is optionally populated later by Tracks.read() for motion point tracks.
        """
        seg_raw = br.read(cls.ENTRY_SIZE)
        if len(seg_raw) < cls.ENTRY_SIZE:
            raise EOFError('Unexpected EOF while reading TrackData entry')
        
        data_offset, ms_id, type_and_next, component_bit_size = struct.unpack('<ihBB', seg_raw)
        td_type = type_and_next & 0x0F
        next_entry_offset = (type_and_next >> 4) & 0x0F
        
        return cls(
            data_offset=data_offset,
            ms_id=ms_id,
            td_type=SegmentType(td_type),
            next_entry_offset=next_entry_offset,
            component_bit_size=component_bit_size,
            data_blob=None  # Not read yet; populated optionally by caller
        )
    
    def write(self, bw: BinaryIO) -> None:
        """Write TrackData entry to binary stream."""
        # Pack track_type and next_entry_offset into single byte
        type_and_next = (self.td_type & 0x0F) | ((self.next_entry_offset & 0x0F) << 4)
        
        bw.write(struct.pack('<ihBB',
            self.data_offset,
            self.ms_id,
            type_and_next,
            self.component_bit_size
        ))



@dataclass
class Gani2TrackData:
    """
    GANI2 track data entry pointing to actual animation data.
    Size: 4 bytes total
    """
    component_bit_size: int       # byte: Component bit size
    data_offset: int     # 3 bytes: Offset to actual data from this entry

    ENTRY_SIZE = 4

    @classmethod
    def read(cls, br: BinaryIO) -> 'Gani2TrackData':
        """Read a Gani2TrackData entry from a binary stream."""
        seg_raw = br.read(cls.ENTRY_SIZE)
        if len(seg_raw) < cls.ENTRY_SIZE:
            raise EOFError('Unexpected EOF while reading Gani2TrackData')
        # First byte is component_bit_size, next 3 bytes are data_offset (little-endian)
        component_bit_size = seg_raw[0]
        data_offset = int.from_bytes(seg_raw[1:4], 'little')
        return cls(
            component_bit_size=component_bit_size,
            data_offset=data_offset
        )
    
    def write(self, bw: BinaryIO) -> None:
        """Write Gani2TrackData entry to binary stream."""
        # First byte is component_bit_size, next 3 bytes are data_offset (little-endian)
        bw.write(bytes([self.component_bit_size]))
        bw.write(self.data_offset.to_bytes(3, 'little'))


@dataclass
class TrackMiniHeader:
    """Mini header describing motion point tracks (from mtar.bt).

    Fields:
    - frame_count: uint
    - params: list of (name:uint, value:float)
    - unit_flags: list of TrackUnitFlags (one byte per unit)
    - segment_headers: list of Gani2TrackData
    """
    frame_count: int # uint
    # padding byte
    param_count: int # byte
    # padding ushort

    BASE_SIZE = 8

    params: List[tuple]
    unit_flags: List[int]
    segment_headers: List[Gani2TrackData]


    @classmethod
    def read(cls, br: BinaryIO, unit_count: int, segment_count: int) -> 'TrackMiniHeader':
        # Read FrameCount (uint), Padding0 (ubyte), ParamCount (ubyte), Padding1 (ushort)
        data = br.read(8)
        if len(data) < 8:
            raise EOFError('Unexpected EOF while reading TrackMiniHeader base')
        frame_count, _pad0, param_count, _pad1 = struct.unpack('<IBBH', data)

        # Read params (Name:uint, Value:float) * param_count
        params: List[tuple] = []
        for _ in range(param_count):
            p_raw = br.read(8)
            if len(p_raw) < 8:
                raise EOFError('Unexpected EOF while reading TrackMiniHeader params')
            name, value = struct.unpack('<If', p_raw)
            params.append((name, value))

        # Read UnitFlags (one byte per unit)
        unit_flags: List[int] = []
        for _ in range(unit_count):
            b = br.read(1)
            if len(b) < 1:
                raise EOFError('Unexpected EOF while reading UnitFlags')
            unit_flags.append(b[0])

        # Align to 4 bytes
        pos = br.tell()
        aligned = align_length(pos, 4)
        if aligned != pos:
            br.seek(aligned)

        # Read SegmentHeaders (Gani2TrackData) - segment_count entries
        segment_headers: List[Gani2TrackData] = []
        for _ in range(segment_count):
            segment_headers.append(Gani2TrackData.read(br))

        # Skip 16 bytes as per FSkip(16)
        br.seek(br.tell() + 16)

        return cls(
            frame_count=frame_count,
            param_count=param_count,
            params=params,
            unit_flags=unit_flags,
            segment_headers=segment_headers,
        )
    
    def write(self, bw: BinaryIO) -> None:
        """Write TrackMiniHeader to binary stream."""
        # Write FrameCount (uint), Padding0 (ubyte), ParamCount (ubyte), Padding1 (ushort)
        bw.write(struct.pack('<IBBH', self.frame_count, 0, self.param_count, 0))
        
        # Write params
        for name, value in self.params:
            bw.write(struct.pack('<If', name, value))
        
        # Write UnitFlags (one byte per unit)
        for flag in self.unit_flags:
            bw.write(bytes([flag]))
        
        # Align to 4 bytes
        pos = bw.tell()
        aligned = align_length(pos, 4)
        if aligned != pos:
            bw.write(bytes(aligned - pos))
        
        # Write SegmentHeaders
        for seg_header in self.segment_headers:
            seg_header.write(bw)
        
        # Write 16 bytes padding
        write_padding(bw, 16)
    
    def get_size(self, unit_count: int, segment_count: int) -> int:
        """Calculate the total size of the TrackMiniHeader when written.
        
        Args:
            unit_count: Number of track units
            segment_count: Number of segments (Gani2TrackData entries)
            
        Returns:
            Total size in bytes
        """
        size = self.BASE_SIZE  # 8 bytes
        size += len(self.params) * 8  # params: 8 bytes each
        size += unit_count  # unit_flags: 1 byte each
        
        # Align to 4 bytes
        size = align_length(size, 4)
        
        size += segment_count * Gani2TrackData.ENTRY_SIZE  # segment_headers: 4 bytes each
        size += 16  # padding
        
        return size
    
    def get_segment_headers_offset(self, unit_count: int) -> int:
        """Calculate the offset from the start of TrackMiniHeader to where segment_headers begin.
        
        Args:
            unit_count: Number of track units
            
        Returns:
            Offset in bytes from the start of the header to segment_headers
        """
        offset = self.BASE_SIZE  # 8 bytes
        offset += len(self.params) * 8  # params: 8 bytes each
        offset += unit_count  # unit_flags: 1 byte each
        
        # Align to 4 bytes
        offset = align_length(offset, 4)
        
        return offset
    



@dataclass
class TrackDataBlob:
    """Represents the in-file track data blob containing keyframe animation data.
    
    This class handles reading keyframe data for all segment types (QUAT, VECTOR3, FLOAT, etc.)
    based on the track metadata (segment type, component bit size, unit flags).
    """
    type: SegmentType
    component_bit_size: int
    is_static: bool
    keyframes: List[AnimKeyframe]
    # raw: bytes | None = None
    
    @classmethod
    def from_keyframes(cls, segment_type: SegmentType, component_bit_size: int, 
                       is_static: bool, keyframes: List[AnimKeyframe]) -> 'TrackDataBlob':
        """Create a TrackDataBlob from keyframe data.
        
        Args:
            segment_type: Type of segment (QUAT, VECTOR3, FLOAT, etc.)
            component_bit_size: Bit size for components
            is_static: Whether the track is static
            keyframes: List of keyframes
            
        Returns:
            TrackDataBlob instance
        """
        return cls(
            type=segment_type,
            component_bit_size=component_bit_size,
            is_static=is_static,
            keyframes=keyframes
        )

    @staticmethod
    def read(file_data: bytes, data_offset: int, segment_type: SegmentType, 
             component_bit_size: int, unit_flags: int, frame_count: int) -> List[AnimKeyframe]:
        """Read keyframe animation data from a TrackDataBlob.
        
        This delegates to AnimKeyframe.read_list_from_bytes().
        
        Args:
            file_data: Complete file data buffer
            data_offset: Absolute byte offset where the blob data starts
            segment_type: Type of segment (QUAT, VECTOR3, FLOAT, etc.)
            component_bit_size: Bit size for components (12, 13, 15, 16, 32)
            unit_flags: Track unit flags (contains IS_STATIC flag)
            frame_count: Total number of frames in the animation
            
        Returns:
            List of AnimKeyframe objects containing the keyframe data
        """
        return AnimKeyframe.read_list_from_bytes(
            file_data=file_data,
            data_offset=data_offset,
            segment_type=segment_type,
            component_bit_size=component_bit_size,
            unit_flags=unit_flags,
            frame_count=frame_count
        )


@dataclass
class TimeSection:
    """Time section for an event unit (frame range)."""
    start_frame: int
    end_frame: int


@dataclass
class EventUnitInfo:
    """Event unit information containing timing and parameters."""
    name: StrCode32  # (EventUnitInfoName)
    time_section_count: int  # 6 bits
    format: int  # TIME_SECTION_FORMAT (2 bits)
    int_param_count: int  # byte
    float_param_count: int  # byte
    string_param_count: int  # byte
    time_sections: List[TimeSection]
    int_params: List[int]
    float_params: List[float]
    string_params: List[int]  # List of StrCode64 (stored as uint64)

    @classmethod
    def read(cls, br: BinaryIO) -> 'EventUnitInfo':
        """Read a single EventUnitInfo structure."""
        # Read name (4 bytes)
        name_data = br.read(4)
        if len(name_data) < 4:
            raise EOFError('Unexpected EOF while reading EventUnitInfo name')
        name_int = struct.unpack('<I', name_data)[0]

        # Read counts (4 bytes: 1 byte packed + 3 count bytes)
        counts_data = br.read(4)
        if len(counts_data) < 4:
            raise EOFError('Unexpected EOF while reading EventUnitInfo counts')
        
        packed_byte = counts_data[0]
        time_section_count = packed_byte & 0x3F  # Lower 6 bits
        format_type = (packed_byte >> 6) & 0x03  # Upper 2 bits
        int_param_count = counts_data[1]
        float_param_count = counts_data[2]
        string_param_count = counts_data[3]

        # Read time sections based on format
        time_sections = []
        for _ in range(time_section_count):
            if format_type == 0:  # TIME_SECTION_FORMAT_INT
                sec_data = br.read(8)
                start, end = struct.unpack('<ii', sec_data)
                # Handle negative flag masking
                if start >= 0:
                    start = start & 0xBFFFFFFF
                if end >= 0:
                    end = end & 0xBFFFFFFF
            elif format_type == 1:  # TIME_SECTION_FORMAT_SHORT
                sec_data = br.read(4)
                start, end = struct.unpack('<hh', sec_data)
            elif format_type == 2:  # TIME_SECTION_FORMAT_BYTE
                sec_data = br.read(2)
                start, end = struct.unpack('<bb', sec_data)
            elif format_type == 3:  # TIME_SECTION_FORMAT_INFINITE
                start, end = -1, -1
            time_sections.append(TimeSection(start, end))

        # Align to 4 bytes
        pos = br.tell()
        aligned = align_length(pos, 4)
        if aligned != pos:
            br.seek(aligned)

        # Read int params
        int_params = []
        if int_param_count > 0:
            int_data = br.read(int_param_count * 4)
            int_params = list(struct.unpack(f'<{int_param_count}I', int_data))

        # Read float params
        float_params = []
        if float_param_count > 0:
            float_data = br.read(float_param_count * 4)
            float_params = list(struct.unpack(f'<{float_param_count}f', float_data))

        # Read string params (stored as uint64 StrCode)
        string_params = []
        if string_param_count > 0:
            for _ in range(string_param_count):
                str_data = br.read(8)
                if len(str_data) < 8:
                    raise EOFError('Unexpected EOF while reading string param')
                # Read as two uints forming a uint64
                param_a, param_b = struct.unpack('<II', str_data)
                param_hash = param_a | (param_b << 32)
                string_params.append(param_hash)

        return cls(
            name=StrCode32(name_int),
            time_section_count=time_section_count,
            format=format_type,
            int_param_count=int_param_count,
            float_param_count=float_param_count,
            string_param_count=string_param_count,
            time_sections=time_sections,
            int_params=int_params,
            float_params=float_params,
            string_params=string_params
        )
    
    def write(self, bw: BinaryIO) -> None:
        """Write EventUnitInfo to binary stream."""
        # Write name (should be StrCode32)
        bw.write(struct.pack('<I', self.name.to_int()))
        
        # Pack counts into 4 bytes
        packed_byte = (self.time_section_count & 0x3F) | ((self.format & 0x03) << 6)
        bw.write(bytes([packed_byte, self.int_param_count, self.float_param_count, self.string_param_count]))
        
        # Write time sections based on format
        for ts in self.time_sections:
            if self.format == 0:  # TIME_SECTION_FORMAT_INT
                bw.write(struct.pack('<ii', ts.start_frame, ts.end_frame))
            elif self.format == 1:  # TIME_SECTION_FORMAT_SHORT
                bw.write(struct.pack('<hh', ts.start_frame, ts.end_frame))
            elif self.format == 2:  # TIME_SECTION_FORMAT_BYTE
                bw.write(struct.pack('<bb', ts.start_frame, ts.end_frame))
            # format == 3 (INFINITE) writes nothing
        
        # Align to 4 bytes
        pos = bw.tell()
        aligned = align_length(pos, 4)
        if aligned != pos:
            bw.write(bytes(aligned - pos))
        
        # Write int params
        if self.int_param_count > 0:
            bw.write(struct.pack(f'<{self.int_param_count}I', *self.int_params))
        
        # Write float params
        if self.float_param_count > 0:
            bw.write(struct.pack(f'<{self.float_param_count}f', *self.float_params))
        
        # Write string params (as uint64)
        for param_hash in self.string_params:
            param_a = param_hash & 0xFFFFFFFF
            param_b = (param_hash >> 32) & 0xFFFFFFFF
            bw.write(struct.pack('<II', param_a, param_b))
    
    def _get_size(self) -> int:
        """Calculate total size of this EventUnitInfo in bytes."""
        size = 0
        
        # Name (4 bytes)
        size += 4
        
        # Counts (4 bytes: 1 packed + 3 count bytes)
        size += 4
        
        # Time sections
        for _ in self.time_sections:
            if self.format == 0:  # TIME_SECTION_FORMAT_INT
                size += 8  # 2 ints
            elif self.format == 1:  # TIME_SECTION_FORMAT_SHORT
                size += 4  # 2 shorts
            elif self.format == 2:  # TIME_SECTION_FORMAT_BYTE
                size += 2  # 2 bytes
            # format == 3 (INFINITE) adds nothing
        
        # Align to 4 bytes after time sections
        size = align_length(size, 4)
        
        # Int params (4 bytes each)
        size += self.int_param_count * 4
        
        # Float params (4 bytes each)
        size += self.float_param_count * 4
        
        # String params (8 bytes each)
        size += self.string_param_count * 8
        
        return size


@dataclass
class EvpData:
    """Event packet data for a specific category."""
    category_name: StrCode32  # (EvfCategoryName)
    unit_count: int  # ushort
    cache_offset: int  # ushort
    unit_offsets: List[int]
    events: List[EventUnitInfo]
    # cache: bytes | None = None  # Cache data (not fully implemented)

    @classmethod
    def read(cls, br: BinaryIO) -> 'EvpData':
        """Read a single EvpData structure."""
        evp_start = br.tell()
        
        # Read EvpData header
        data = br.read(8)
        if len(data) < 8:
            raise EOFError('Unexpected EOF while reading EvpData')
        category_name, unit_count, cache_offset = struct.unpack('<IHH', data)

        # Read unit offsets
        unit_offsets = []
        if unit_count > 0:
            offsets_data = br.read(unit_count * 4)
            if len(offsets_data) < unit_count * 4:
                raise EOFError('Unexpected EOF while reading EvpData unit offsets')
            unit_offsets = list(struct.unpack(f'<{unit_count}I', offsets_data))

        # Read each EventUnitInfo
        events = []
        for offset in unit_offsets:
            br.seek(evp_start + offset)
            event = EventUnitInfo.read(br)
            events.append(event)

        return cls(
            category_name=category_name,
            unit_count=unit_count,
            cache_offset=cache_offset,
            unit_offsets=unit_offsets,
            events=events
        )
    
    def write(self, bw: BinaryIO) -> None:
        """Write EvpData to binary stream.
        
        Note: unit_offsets should be calculated by EvpHeader._calculate_offsets() before calling this.
        """
        # Write header (convert StrCode32 to int)
        bw.write(struct.pack('<IHH', self.category_name.to_int(), self.unit_count, self.cache_offset))
        
        # Write unit offsets
        if self.unit_count > 0:
            bw.write(struct.pack(f'<{self.unit_count}I', *self.unit_offsets))
        
        # Write events
        for event in self.events:
            event.write(bw)
    
    def _calculate_unit_offsets(self) -> None:
        """Calculate offsets for each EventUnitInfo within this EvpData.
        
        UnitOffsets are relative to the start of this EvpData entry.
        The structure is:
        - EvpData header (8 bytes: category_name, unit_count, cache_offset)
        - UnitOffsets array (unit_count * 4 bytes)
        - EventUnitInfo entries (variable size each)
        """
        # Size of EvpData header (category_name + unit_count + cache_offset)
        header_size = 8  # 4 (uint) + 2 (ushort) + 2 (ushort)
        
        # Size of unit_offsets array
        unit_offsets_size = self.unit_count * 4  # Each offset is uint (4 bytes)
        
        # First EventUnitInfo starts after header + all unit offsets
        current_offset = header_size + unit_offsets_size
        
        # Calculate offset for each EventUnitInfo
        self.unit_offsets = []
        for event in self.events:
            # Store offset to this EventUnitInfo
            self.unit_offsets.append(current_offset)
            
            # Calculate size of this EventUnitInfo
            event_size = event._get_size()
            
            # Advance offset for next event
            current_offset += event_size
    
    def _get_size(self) -> int:
        """Calculate total size of this EvpData entry in bytes."""
        # Header size
        size = 8  # category_name (4) + unit_count (2) + cache_offset (2)
        
        # Unit offsets array
        size += self.unit_count * 4
        
        # All events
        for event in self.events:
            size += event._get_size()
        
        return size


@dataclass
class EvpHeader:
    """Event header structure from anim_common.bt.
    
    Contains versioned event data organized into categories (ag, sd, fx, etc.).
    Each category has event units with timing sections and parameters.
    """
    version: int  # uint
    count: int  # short (number of EvpData entries)
    padding: int  # ushort
    entry_offsets: List[int]  # uint[count]
    data: List[EvpData]  # EvpData[count]

    @classmethod
    def read(cls, br: BinaryIO) -> 'EvpHeader':
        """Read EvpHeader from binary stream."""
        # Read header (version, count, padding)
        header_data = br.read(8)
        if len(header_data) < 8:
            raise EOFError('Unexpected EOF while reading EvpHeader')
        version, count, padding = struct.unpack('<IhH', header_data)  # I=uint, h=short, H=ushort

        # Read entry offsets
        entry_offsets = []
        offsets_data = br.read(count * 4)
        if len(offsets_data) < count * 4:
            raise EOFError('Unexpected EOF while reading EvpHeader entry offsets')
        entry_offsets = list(struct.unpack(f'<{count}I', offsets_data))

        # Read each EvpData entry
        evp_data_list = []
        header_start = br.tell() - 8 - count * 4  # Calculate start position
        
        for offset in entry_offsets:
            br.seek(header_start + offset)
            evp_data = EvpData.read(br)
            evp_data_list.append(evp_data)

        return cls(
            version=version,
            count=count,
            padding=padding,
            entry_offsets=entry_offsets,
            data=evp_data_list
        )
    
    def write(self, bw: BinaryIO) -> None:
        """Write EvpHeader to binary stream.
        
        Calculates all offsets internally before writing.
        """
        # Calculate all offsets before writing
        self._calculate_offsets()
        
        # Write header (I=uint, h=short, H=ushort to match Binary Template)
        bw.write(struct.pack('<IhH', self.version, self.count, self.padding))
        
        # Write entry offsets
        bw.write(struct.pack(f'<{self.count}I', *self.entry_offsets))
        
        # Write EvpData entries
        for evp_data in self.data:
            evp_data.write(bw)
    
    def _calculate_offsets(self) -> None:
        """Calculate all offsets for EvpHeader and nested EvpData entries.
        
        EntryOffsets are relative to the start of EvpHeader.
        The structure is:
        - EvpHeader base (8 bytes: version, count, padding)
        - EntryOffsets array (count * 4 bytes)
        - EvpData entries (variable size each)
        """
        # Size of EvpHeader base (version + count + padding)
        header_base_size = 8  # 4 (uint) + 2 (short) + 2 (ushort)
        
        # Size of entry_offsets array
        entry_offsets_size = self.count * 4  # Each offset is uint (4 bytes)
        
        # First EvpData starts after header base + all entry offsets
        current_offset = header_base_size + entry_offsets_size
        
        # Calculate offset for each EvpData entry
        self.entry_offsets = []
        for evp_data in self.data:
            # Store offset to this EvpData entry
            self.entry_offsets.append(current_offset)
            
            # Calculate unit offsets for this EvpData
            evp_data._calculate_unit_offsets()
            
            # Calculate size of this EvpData entry
            evp_data_size = evp_data._get_size()
            
            # Advance offset for next entry
            current_offset += evp_data_size
