"""
Types for GANI2 animation data structures in Metal Gear Solid V.
"""
import io
from dataclasses import dataclass
from typing import BinaryIO, List, Optional
import struct

from ..py_core.core_logging import Debug

from ..py_utilities import util_binary_write
from ..py_utilities import util_binary_read
from ..py_utilities import util_transforms

from .fox_hash_types import StrCode32
from .fox_gani_enums import SegmentType, TrackUnitFlags, MotionGraphFootFitFlags
from . import fox_gani_constants as gani_const



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

    ``frame_count`` stores the **relative frame delta** from the previous keyframe,
    matching the Fox Engine binary format (8-bit unsigned, range 0-255).
    First keyframe always has frame_count=0 (implicit start).
    
    Consumers that need absolute frame numbers must accumulate deltas:
    ``current_frame += keyframe.frame_count``

    ``data`` is a SegmentKeyframeData instance containing the decoded components.
    """
    frame_count: int
    '''Relative frame delta from the previous keyframe (0 for the first keyframe).'''

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
        accumulated_frame = 0  # Tracks absolute position for loop termination only
        is_static = (unit_flags & TrackUnitFlags.IS_STATIC) != 0
        
        # Rotations (Quaternions)
        if segment_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
            bit_pos = data_offset * 8  # Convert byte offset to bit offset
            quat, bit_pos = util_binary_read.read_unaligned_quaternion(file_data, bit_pos, component_bit_size)
            keyframes.append(AnimKeyframe(frame=0, value=quat))  # First keyframe: delta=0
            
            if not is_static:
                while accumulated_frame < frame_count:
                    frame_delta, bit_pos = util_binary_read.read_unaligned_bits(file_data, bit_pos, 8)
                    accumulated_frame += frame_delta
                    if frame_delta < 1:
                        Debug.log_warning(f"Import: Invalid frame_delta {frame_delta} at accumulated frame {accumulated_frame} (segment={segment_type})")
                    elif frame_delta > 255:
                        Debug.log_warning(f"Import: frame_delta {frame_delta} exceeds 8-bit range at accumulated frame {accumulated_frame} (segment={segment_type})")
                    quat, bit_pos = util_binary_read.read_unaligned_quaternion(file_data, bit_pos, component_bit_size)
                    
                    # Ensure quaternion stays in same hemisphere as previous keyframe
                    prev_quat = keyframes[-1].data.value if keyframes else None
                    quat = util_transforms.make_quaternion_list_compatible(quat, prev_quat)
                    
                    keyframes.append(AnimKeyframe(frame=frame_delta, value=quat))  # Store delta directly
        
        # 3D Vectors (Positions, etc.)
        elif segment_type in [SegmentType.VECTOR3, SegmentType.VECTOR_DIFF]:
            offset = data_offset
            vec, offset = util_binary_read.read_vector3(file_data, offset, component_bit_size)
            keyframes.append(AnimKeyframe(frame=0, value=vec))  # First keyframe: delta=0
            
            if not is_static:
                while accumulated_frame < frame_count:
                    frame_delta = file_data[offset]
                    offset += 1
                    accumulated_frame += frame_delta
                    if frame_delta < 1:
                        Debug.log_warning(f"Import: Invalid frame_delta {frame_delta} at accumulated frame {accumulated_frame} (segment={segment_type})")
                    vec, offset = util_binary_read.read_vector3(file_data, offset, component_bit_size)
                    if abs(vec[0]) > 100 or abs(vec[1]) > 100 or abs(vec[2]) > 100:
                        Debug.log_error(f"Vector segment ({segment_type}) too big ({vec})")
                    keyframes.append(AnimKeyframe(frame=frame_delta, value=vec))  # Store delta directly
        
        # Floats (Single values)
        elif segment_type == SegmentType.FLOAT:
            offset = data_offset
            if component_bit_size == 16:
                value, offset = util_binary_read.read_anim_half(file_data, offset)
            else:  # component_bit_size == 32
                value, offset = util_binary_read.read_float(file_data, offset)
            keyframes.append(AnimKeyframe(frame=0, value=[value]))  # First keyframe: delta=0
            
            if not is_static:
                while accumulated_frame < frame_count:
                    frame_delta = file_data[offset]
                    offset += 1
                    accumulated_frame += frame_delta
                    if frame_delta < 1:
                        Debug.log_warning(f"Import: Invalid frame_delta {frame_delta} at accumulated frame {accumulated_frame} (segment={segment_type})")
                    if component_bit_size == 16:
                        value, offset = util_binary_read.read_anim_half(file_data, offset)
                    else:  # component_bit_size == 32
                        value, offset = util_binary_read.read_float(file_data, offset)
                    keyframes.append(AnimKeyframe(frame=frame_delta, value=[value]))  # Store delta directly
        
        # 2D Vectors
        elif segment_type == SegmentType.VECTOR2:
            offset = data_offset
            vec, offset = util_binary_read.read_vector2(file_data, offset, component_bit_size)
            keyframes.append(AnimKeyframe(frame=0, value=vec))  # First keyframe: delta=0
            
            if not is_static:
                while accumulated_frame < frame_count:
                    frame_delta = file_data[offset]
                    offset += 1
                    accumulated_frame += frame_delta
                    if frame_delta < 1:
                        Debug.log_warning(f"Import: Invalid frame_delta {frame_delta} at accumulated frame {accumulated_frame} (segment={segment_type})")
                    vec, offset = util_binary_read.read_vector2(file_data, offset, component_bit_size)
                    keyframes.append(AnimKeyframe(frame=frame_delta, value=vec))  # Store delta directly
        
        # 4D Vectors
        elif segment_type == SegmentType.VECTOR4:
            offset = data_offset
            vec, offset = util_binary_read.read_vector4(file_data, offset, component_bit_size)
            keyframes.append(AnimKeyframe(frame=0, value=vec))  # First keyframe: delta=0
            
            if not is_static:
                while accumulated_frame < frame_count:
                    frame_delta = file_data[offset]
                    offset += 1
                    accumulated_frame += frame_delta
                    if frame_delta < 1:
                        Debug.log_warning(f"Import: Invalid frame_delta {frame_delta} at accumulated frame {accumulated_frame} (segment={segment_type})")
                    vec, offset = util_binary_read.read_vector4(file_data, offset, component_bit_size)
                    keyframes.append(AnimKeyframe(frame=frame_delta, value=vec))  # Store delta directly
        
        else:
            Debug.raise_error(f"Unsupported segment type: {segment_type}", ValueError)
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
        
        # Validate: non-static tracks MUST have at least 2 keyframes.
        # The GANI binary format reads a do-while loop after the initial value
        # when IS_STATIC is not set. With only 1 keyframe the loop has no data
        # to read and the reader will parse garbage, corrupting the file.
        if has_frames and len(keyframes) <= 1:
            Debug.log_error(
                f"Export: INVALID FILE - Non-static track (type={track_type}) has only "
                f"{len(keyframes)} keyframe(s). The binary format requires animated "
                f"keyframe data (frame deltas summing to FrameCount) when IS_STATIC is "
                f"not set. This produces a corrupt MTAR. Check FCurve cleaning settings."
            )
        
        # Handle different track types
        if track_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
            # Write quaternion keyframes
            buffer = bytearray()
            bit_pos = 0

            # Write initial quaternion (bit-packed)
            initial_quat = keyframes[0].data.value
            bit_pos, prev_axis = util_binary_write.write_unaligned_quaternion(buffer, bit_pos, initial_quat, component_bit_size)
            
            # Write subsequent keyframes if this is not a static track
            if has_frames and len(keyframes) > 1:
                for i in range(1, len(keyframes)):
                    # frame_count is already the relative delta from previous keyframe
                    frame_delta = keyframes[i].frame_count
                    
                    # Validate: delta must be 1-255 (8-bit unsigned, non-zero)
                    if frame_delta < 1:
                        Debug.log_warning(f"Export: Invalid frame_delta {frame_delta} at keyframe {i} (type={track_type}). Clamping to 1.")
                        frame_delta = 1
                    elif frame_delta > 255:
                        Debug.log_error(f"Export: INVALID FILE - frame_delta {frame_delta} exceeds the 255-frame binary limit at keyframe {i} (type={track_type}). Delta clamped to 255 but this corrupts all subsequent keyframe timings. Reduce the export clean threshold.")
                        frame_delta = 255
                    
                    # Write frame delta (8 bits)
                    bit_pos = util_binary_write.write_unaligned_bits(buffer, bit_pos, frame_delta, 8)
                    
                    # Write quaternion, passing prev_axis for stable hemisphere selection
                    quat = keyframes[i].data.value
                    bit_pos, prev_axis = util_binary_write.write_unaligned_quaternion(buffer, bit_pos, quat, component_bit_size, prev_axis)
            
            # Ensure buffer is byte-aligned first (round up bit_pos to next byte)
            byte_size = (bit_pos + 7) // 8
            if len(buffer) < byte_size:
                buffer.extend(bytes(byte_size - len(buffer)))
            
            # Align to 2-byte boundary (FAlign(2) in binary template)
            util_binary_write.align_bytearray(buffer, 2)
            
            return bytes(buffer)
        
        elif track_type in [SegmentType.VECTOR3, SegmentType.VECTOR_DIFF]:
            # Write vector3 keyframes
            buffer = io.BytesIO()
            
            # Write initial vector3
            initial_vec = keyframes[0].data.value
            util_binary_write.write_vector3(buffer, initial_vec, component_bit_size)
            
            # Write subsequent keyframes if this is not a static track
            if has_frames and len(keyframes) > 1:
                for i in range(1, len(keyframes)):
                    # frame_count is already the relative delta from previous keyframe
                    frame_delta = keyframes[i].frame_count
                    
                    # Validate: delta must be 1-255 (8-bit unsigned, non-zero)
                    if frame_delta < 1:
                        Debug.log_warning(f"Export: Invalid frame_delta {frame_delta} at keyframe {i} (type={track_type}). Clamping to 1.")
                        frame_delta = 1
                    elif frame_delta > 255:
                        Debug.log_error(f"Export: INVALID FILE - frame_delta {frame_delta} exceeds the 255-frame binary limit at keyframe {i} (type={track_type}). Delta clamped to 255 but this corrupts all subsequent keyframe timings. Reduce the export clean threshold.")
                        frame_delta = 255
                    
                    # Write frame delta (1 byte)
                    buffer.write(bytes([frame_delta & 0xFF]))
                    
                    # Write vector3
                    vec = keyframes[i].data.value
                    util_binary_write.write_vector3(buffer, vec, component_bit_size)
            
            # Align to 2-byte boundary (FAlign(2) in binary template)
            util_binary_write.align_buffer(buffer, 2)
            
            return buffer.getvalue()
        
        elif track_type == SegmentType.FLOAT:
            # Write float keyframes
            buffer = io.BytesIO()
            
            # Write initial float
            initial_value = keyframes[0].data.value[0] if isinstance(keyframes[0].data.value, list) else keyframes[0].data.value
            if component_bit_size == 16:
                util_binary_write.write_anim_half(buffer, initial_value)
            else:  # component_bit_size == 32
                util_binary_write.write_float(buffer, initial_value)
            
            # Write subsequent keyframes if this is not a static track
            if has_frames and len(keyframes) > 1:
                for i in range(1, len(keyframes)):
                    # frame_count is already the relative delta from previous keyframe
                    frame_delta = keyframes[i].frame_count
                    
                    # Validate: delta must be 1-255 (8-bit unsigned, non-zero)
                    if frame_delta < 1:
                        Debug.log_warning(f"Export: Invalid frame_delta {frame_delta} at keyframe {i} (type={track_type}). Clamping to 1.")
                        frame_delta = 1
                    elif frame_delta > 255:
                        Debug.log_error(f"Export: INVALID FILE - frame_delta {frame_delta} exceeds the 255-frame binary limit at keyframe {i} (type={track_type}). Delta clamped to 255 but this corrupts all subsequent keyframe timings. Reduce the export clean threshold.")
                        frame_delta = 255
                    
                    # Write frame delta (1 byte)
                    buffer.write(bytes([frame_delta & 0xFF]))
                    
                    # Write float
                    value = keyframes[i].data.value[0] if isinstance(keyframes[i].data.value, list) else keyframes[i].data.value
                    if component_bit_size == 16:
                        util_binary_write.write_anim_half(buffer, value)
                    else:  # component_bit_size == 32
                        util_binary_write.write_float(buffer, value)
            
            # Align to 2-byte boundary (FAlign(2) in binary template)
            util_binary_write.align_buffer(buffer, 2)
            
            return buffer.getvalue()
        
        elif track_type == SegmentType.VECTOR2:
            # Write vector2 keyframes
            buffer = io.BytesIO()
            
            # Write initial vector2
            initial_vec = keyframes[0].data.value
            util_binary_write.write_vector2(buffer, initial_vec, component_bit_size)
            
            # Write subsequent keyframes if this is not a static track
            if has_frames and len(keyframes) > 1:
                for i in range(1, len(keyframes)):
                    # frame_count is already the relative delta from previous keyframe
                    frame_delta = keyframes[i].frame_count
                    
                    # Validate: delta must be 1-255 (8-bit unsigned, non-zero)
                    if frame_delta < 1:
                        Debug.log_warning(f"Export: Invalid frame_delta {frame_delta} at keyframe {i} (type={track_type}). Clamping to 1.")
                        frame_delta = 1
                    elif frame_delta > 255:
                        Debug.log_error(f"Export: INVALID FILE - frame_delta {frame_delta} exceeds the 255-frame binary limit at keyframe {i} (type={track_type}). Delta clamped to 255 but this corrupts all subsequent keyframe timings. Reduce the export clean threshold.")
                        frame_delta = 255
                    
                    # Write frame delta (1 byte)
                    buffer.write(bytes([frame_delta & 0xFF]))
                    
                    # Write vector2
                    vec = keyframes[i].data.value
                    util_binary_write.write_vector2(buffer, vec, component_bit_size)
            
            # Align to 2-byte boundary (FAlign(2) in binary template)
            util_binary_write.align_buffer(buffer, 2)
            
            return buffer.getvalue()
        
        elif track_type == SegmentType.VECTOR4:
            # Write vector4 keyframes
            buffer = io.BytesIO()
            
            # Write initial vector4
            initial_vec = keyframes[0].data.value
            util_binary_write.write_vector4(buffer, initial_vec, component_bit_size)
            
            # Write subsequent keyframes if this is not a static track
            if has_frames and len(keyframes) > 1:
                for i in range(1, len(keyframes)):
                    # frame_count is already the relative delta from previous keyframe
                    frame_delta = keyframes[i].frame_count
                    
                    # Validate: delta must be 1-255 (8-bit unsigned, non-zero)
                    if frame_delta < 1:
                        Debug.log_warning(f"Export: Invalid frame_delta {frame_delta} at keyframe {i} (type={track_type}). Clamping to 1.")
                        frame_delta = 1
                    elif frame_delta > 255:
                        Debug.log_error(f"Export: INVALID FILE - frame_delta {frame_delta} exceeds the 255-frame binary limit at keyframe {i} (type={track_type}). Delta clamped to 255 but this corrupts all subsequent keyframe timings. Reduce the export clean threshold.")
                        frame_delta = 255
                    
                    # Write frame delta (1 byte)
                    buffer.write(bytes([frame_delta & 0xFF]))
                    
                    # Write vector4
                    vec = keyframes[i].data.value
                    util_binary_write.write_vector4(buffer, vec, component_bit_size)
            
            # Align to 2-byte boundary (FAlign(2) in binary template)  
            util_binary_write.align_buffer(buffer, 2)
            
            return buffer.getvalue()
        
        else:
            Debug.raise_error(f"Unsupported track type: {track_type}", ValueError)


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
    def read(cls, br: BinaryIO, endian: str = '<') -> 'TrackHeader':

        data = br.read(cls.BASE_SIZE)
        if len(data) < cls.BASE_SIZE:
            Debug.raise_error('Unexpected EOF while reading TrackHeader', EOFError)
        unit_count, segment_count, t_id, unknown_a, unknown_b, frame_count, frame_rate = struct.unpack(endian + 'IIHBBII', data)

        unit_offsets: List[int] = []
        for _ in range(unit_count):
            unit_offsets.append(struct.unpack(endian + 'I', br.read(4))[0])

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
        if self.frame_count <= 0:
            Debug.log_warning(f"TrackHeader.write: frame_count is {self.frame_count} (expected > 0).")
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
        util_binary_write.write_padding(bw, 12)


@dataclass
class TrackUnit:
    name: StrCode32
    segment_count: int # byte
    unit_flags: int
    padding: int # ushort

    BASE_SIZE = 8  # TrackUnit base (name:4 + seg_count:1 + flags:1 + padding:2)

    segments_data: List['TrackData']


    @classmethod
    def read(cls, br: BinaryIO, endian: str = '<') -> 'TrackUnit':
        # Read base fields
        base = br.read(cls.BASE_SIZE)
        if len(base) < cls.BASE_SIZE:
            Debug.raise_error('Unexpected EOF while reading TrackUnit base', EOFError)
        name_int, segment_count, unit_flags, padding = struct.unpack(endian + 'IBBH', base)

        track_data: List[TrackData] = []
        for _ in range(segment_count):
            # Delegate reading/parsing of a TrackData entry to TrackData.read
            track_data.append(TrackData.read(br, endian))

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
    def read(cls, br: BinaryIO, endian: str = '<') -> 'TrackData':
        """Read a TrackData entry (8 bytes) from the given BinaryIO and return a TrackData instance.

        The format is: data_offset (int32), motion_segment_id (int16), type_and_next (uint8), component_bit_size (uint8)
        type_and_next packs the track_type in the low 4 bits and next_entry_offset in the high 4 bits.
        
        Note: This only reads the TrackData structure itself, not the data blob it points to.
        The data blob is optionally populated later by Tracks.read() for motion point tracks.
        """
        seg_raw = br.read(cls.ENTRY_SIZE)
        if len(seg_raw) < cls.ENTRY_SIZE:
            Debug.raise_error('Unexpected EOF while reading TrackData entry', EOFError)
        
        data_offset, ms_id, type_and_next, component_bit_size = struct.unpack(endian + 'ihBB', seg_raw)
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
            Debug.raise_error('Unexpected EOF while reading Gani2TrackData', EOFError)
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
            Debug.raise_error('Unexpected EOF while reading TrackMiniHeader base', EOFError)
        frame_count, _pad0, param_count, _pad1 = struct.unpack('<IBBH', data)

        # Read params (Name:uint, Value:float) * param_count
        params: List[tuple] = []
        for _ in range(param_count):
            p_raw = br.read(8)
            if len(p_raw) < 8:
                Debug.raise_error('Unexpected EOF while reading TrackMiniHeader params', EOFError)
            name, value = struct.unpack('<If', p_raw)
            params.append((name, value))

        # Read UnitFlags (one byte per unit)
        unit_flags: List[int] = []
        for _ in range(unit_count):
            b = br.read(1)
            if len(b) < 1:
                Debug.raise_error('Unexpected EOF while reading UnitFlags', EOFError)
            unit_flags.append(b[0])

        # Align to 4 bytes
        pos = br.tell()
        aligned = util_binary_write.align_length(pos, 4)
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
        if self.frame_count <= 0:
            Debug.log_warning(f"TrackMiniHeader.write: frame_count is {self.frame_count} (expected > 0).")
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
        aligned = util_binary_write.align_length(pos, 4)
        if aligned != pos:
            bw.write(bytes(aligned - pos))
        
        # Write SegmentHeaders
        for seg_header in self.segment_headers:
            seg_header.write(bw)
        
        # Write 16 bytes padding
        util_binary_write.write_padding(bw, 16)
    
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
        size = util_binary_write.align_length(size, 4)
        
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
        offset = util_binary_write.align_length(offset, 4)
        
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
    def from_keyframes(cls, 
                       segment_type: SegmentType,
                       component_bit_size: int,
                       is_static: bool,
                       keyframes: List[AnimKeyframe]
                       ) -> 'TrackDataBlob':
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
    def read_keyframes(file_data: bytes, 
             data_offset: int, 
             segment_type: SegmentType, 
             component_bit_size: int, 
             unit_flags: int, 
             frame_count: int
             ) -> List[AnimKeyframe]:
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
            Debug.raise_error('Unexpected EOF while reading EventUnitInfo name', EOFError)
        name_int = struct.unpack('<I', name_data)[0]

        # Read counts (4 bytes: 1 byte packed + 3 count bytes)
        counts_data = br.read(4)
        if len(counts_data) < 4:
            Debug.raise_error('Unexpected EOF while reading EventUnitInfo counts', EOFError)
        
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
        aligned = util_binary_write.align_length(pos, 4)
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
                    Debug.raise_error('Unexpected EOF while reading string param', EOFError)
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
        aligned = util_binary_write.align_length(pos, 4)
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
    
    def get_size(self) -> int:
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
        size = util_binary_write.align_length(size, 4)
        
        # Int params (4 bytes each)
        size += self.int_param_count * 4
        
        # Float params (4 bytes each)
        size += self.float_param_count * 4
        
        # String params (8 bytes each)
        size += self.string_param_count * 8
        
        return size


def _build_ag_cache(events: List['EventUnitInfo'], is_loop: bool, total_frame_count: int) -> bytes:
    """Build the binary cache blob for the 'ag' (AnimGraph) EvpData category.

    The cache is fully derived from event data and the is_loop flag — it is never
    stored persistently. Call this whenever the cache bytes are needed.

    Binary layout (offsets relative to cache start):
      +0   FramesOffset (uint32) — offset to Frames[] relative to this field (cache_start+0)
      +4   FrameCount   (uint32)
      +8   StartFrame   (int32)  — -(first sync frame) if loop, else 0
      +12  Flags        (uint32) — IS_LOOP=0x1, START_LEFT=0x2
      +16  TagsOffset   (uint32) — offset to Tags[] relative to this field (cache_start+16)
      +20  TagCount     (uint32)
      +24  Frames[FrameCount]         (uint32 each, absolute frame numbers)
      +24+FrameCount*4  Tags[TagCount] (uint64 each)

    Source: anim_common.bt AnimGraphEventCache / MotionGraphFootFitEventCacheData.

    Args:
        events:  EventUnitInfo list from the 'ag' EvpData category.
        is_loop: Whether the animation loops (action.use_cyclic or TrackUnitFlags.LOOP).
        total_frame_count: Total GANI frame length; when looped, the final frame boundary is taken from this value if greater than event boundaries.

    Returns:
        Binary cache blob, or empty bytes if no MTEV_AG_SYNC_L/R events are present.
    """
    # 1 -----------------------------------------------------
    # Collect sync frame boundaries from SYNC_L/R sections

    # We need all distinct switches, e.g. [0-12,12-36,36-62] -> [0,12,36,62]
    sync_boundaries_l: List[tuple] = []  # (frame, is_left_foot)
    sync_boundaries_r: List[tuple] = []  # (frame, is_left_foot)
    tag_hashes: List[int] = []

    for event in events:
        event_hash = event.name.to_int()
        # Left Foot sync
        if event_hash == gani_const.MTEV_AG_SYNC_L_HASH:
            for section in event.time_sections:
                sync_boundaries_l.append((section.start_frame, True))
                if section.end_frame >= 0:
                    sync_boundaries_l.append((section.end_frame, True))
        # Right Foot sync
        elif event_hash == gani_const.MTEV_AG_SYNC_R_HASH:
            for section in event.time_sections:
                sync_boundaries_r.append((section.start_frame, False))
                if section.end_frame >= 0:
                    sync_boundaries_r.append((section.end_frame, False))
        # Tags
        elif event_hash == gani_const.MTEV_AG_TAG_CONTROL_HASH:
            tag_hashes.extend(event.string_params)

    # No SYNC events → no cache needed
    if not sync_boundaries_l and not sync_boundaries_r:
        return b''

    # Sort boundaries then remove duplicates by frame number (preserve first foot side for boundary frame)
    sync_boundaries = sync_boundaries_l + sync_boundaries_r
    sync_boundaries.sort(key=lambda x: x[0])
    frame_to_is_left = {}
    for frame, is_left in sync_boundaries:
        if frame not in frame_to_is_left:
            frame_to_is_left[frame] = is_left

    frame_list = sorted(frame_to_is_left.keys())
    start_frame = frame_list[0]

    # Determine whether first boundary belongs to left sync to set START_LEFT
    first_is_left = sync_boundaries_l[0] < sync_boundaries_r[0] if sync_boundaries_l and sync_boundaries_r else True

    # 2 -----------------------------------------------------
    # Post-process loop case to normalize the first transition frame.

    if is_loop:
        if len(frame_list) < 3:
            frame_list.extend([0] * (3 - len(frame_list)))
        
        # Get stride time length
        sync_len = frame_list[1] - frame_list[0]
        start_frame = -frame_list[0] if first_is_left else total_frame_count - frame_list[1]
        frame_list[0] = 0
        frame_list[1] = total_frame_count - sync_len
        frame_list[2] = total_frame_count

    # Ensure loop endpoint includes explicit GANI length when present.
    # Sections can exceed total_frame_count; do not clamp in that direction.
    if not is_loop:
        final_sync_frame = frame_list[-1]
        if total_frame_count > final_sync_frame:
            frame_list.append(total_frame_count)

    frame_count = len(frame_list)

    # 3 -----------------------------------------------------
    # Set Flags

    flags = 0
    if is_loop:
        flags |= int(MotionGraphFootFitFlags.IS_LOOP)
    if is_loop or first_is_left:
        flags |= int(MotionGraphFootFitFlags.START_LEFT)


    # 4 -----------------------------------------------------
    # Prepare Offsets

    # Self-relative pointer: FramesOffset field is at cache_start+0; frame data starts at cache_start+24
    frames_offset = 24

    # Self-relative pointer: TagsOffset field is at cache_start+16; tag data starts after frames
    tag_count = len(tag_hashes)
    tags_data_abs_from_cache_start = 24 + frame_count * 4
    tags_offset = (tags_data_abs_from_cache_start - 16) if tag_count > 0 else 0

    # 5 -----------------------------------------------------
    # Write

    buf = io.BytesIO()
    buf.write(struct.pack('<IIiI', frames_offset, frame_count, start_frame, flags))
    buf.write(struct.pack('<II', tags_offset, tag_count))
    for f in frame_list:
        # uint in binary template; mask handles negative frames
        buf.write(struct.pack('<I', f & 0xFFFFFFFF))
    for t in tag_hashes:
        buf.write(struct.pack('<Q', t & 0xFFFFFFFFFFFFFFFF))

    return buf.getvalue()

@dataclass
class EvpData:
    """Event packet data for a specific category."""
    category_name: StrCode32  # (EvfCategoryName)
    unit_count: int  # ushort
    cache_offset: int  # ushort
    unit_offsets: List[int]
    events: List[EventUnitInfo]

    @classmethod
    def read(cls, br: BinaryIO, endian: str = '<') -> 'EvpData':
        """Read a single EvpData structure."""
        evp_start = br.tell()
        
        # Read EvpData header
        data = br.read(8)
        if len(data) < 8:
            Debug.raise_error('Unexpected EOF while reading EvpData', EOFError)
        category_name, unit_count, cache_offset = struct.unpack(endian + 'IHH', data)

        # Read unit offsets
        unit_offsets = []
        if unit_count > 0:
            offsets_data = br.read(unit_count * 4)
            if len(offsets_data) < unit_count * 4:
                Debug.raise_error('Unexpected EOF while reading EvpData unit offsets', EOFError)
            unit_offsets = list(struct.unpack(f'{endian}{unit_count}I', offsets_data))

        # Read each EventUnitInfo
        events = []
        for offset in unit_offsets:
            br.seek(evp_start + offset)
            event = EventUnitInfo.read(br)
            events.append(event)

        # category_name is stored as uint32 in MTAR and must be converted to StrCode32.
        category_name_code = StrCode32(category_name)

        return cls(
            category_name=category_name_code,
            unit_count=unit_count,
            cache_offset=cache_offset,
            unit_offsets=unit_offsets,
            events=events
        )
    
    def write(self, bw: BinaryIO, is_loop: bool, total_frame_count: int) -> None:
        """Write EvpData to binary stream.

        For the 'ag' category the AnimGraph cache blob is always re-derived from
        the event list and is_loop flag, so no cache state needs to be stored.
        For all other categories, cache_offset is forced to 0 (unsupported).

        Note: unit_offsets should be calculated by EvpHeader._calculate_offsets() before calling this.
        """
        category_name_int = self.category_name.to_int()

        # Derive cache for 'ag' category; always recomputed to avoid stale data
        cache_bytes = b''
        computed_cache_offset = 0
        if category_name_int == gani_const.EVPDATA_CATEGORY_AG_HASH:
            cache_bytes = _build_ag_cache(self.events, is_loop, total_frame_count)
            if cache_bytes:
                events_total = sum(event.get_size() for event in self.events)
                computed_cache_offset = 8 + self.unit_count * 4 + events_total
        elif self.cache_offset != 0:
            Debug.log_warning(
                f"EvpData.write: non-ag category (hash={category_name_int}) has "
                f"cache_offset={self.cache_offset}; cache not supported for this "
                f"category, writing cache_offset=0"
            )

        # Write header: category_name must be StrCode32 (strict invariant)
        bw.write(struct.pack('<IHH', category_name_int, self.unit_count, computed_cache_offset))

        # Write unit offsets
        if self.unit_count > 0:
            bw.write(struct.pack(f'<{self.unit_count}I', *self.unit_offsets))

        # Write events
        for event in self.events:
            event.write(bw)

        # Write cache blob (ag category only)
        if cache_bytes:
            bw.write(cache_bytes)
    
    def calculate_unit_offsets(self) -> None:
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
            event_size = event.get_size()
            
            # Advance offset for next event
            current_offset += event_size
    
    def get_size(self, is_loop: bool, total_frame_count: int) -> int:
        """Calculate total size of this EvpData entry in bytes."""
        # Header size
        size = 8  # category_name (4) + unit_count (2) + cache_offset (2)

        # Unit offsets array
        size += self.unit_count * 4

        # All events
        for event in self.events:
            size += event.get_size()

        # Cache blob (ag category only; always re-derived)
        if self.category_name.to_int() == gani_const.EVPDATA_CATEGORY_AG_HASH:
            size += len(_build_ag_cache(self.events, is_loop, total_frame_count))

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
    def read(cls, br: BinaryIO, endian: str = '<') -> 'EvpHeader':
        """Read EvpHeader from binary stream."""
        # Read header (version, count, padding)
        header_data = br.read(8)
        if len(header_data) < 8:
            Debug.raise_error('Unexpected EOF while reading EvpHeader', EOFError)
        version, count, padding = struct.unpack(endian + 'IhH', header_data)  # I=uint, h=short, H=ushort

        # Read entry offsets
        entry_offsets = []
        offsets_data = br.read(count * 4)
        if len(offsets_data) < count * 4:
            Debug.raise_error('Unexpected EOF while reading EvpHeader entry offsets', EOFError)
        entry_offsets = list(struct.unpack(f'{endian}{count}I', offsets_data))

        # Read each EvpData entry
        evp_data_list = []
        header_start = br.tell() - 8 - count * 4  # Calculate start position
        
        for offset in entry_offsets:
            br.seek(header_start + offset)
            evp_data = EvpData.read(br, endian)
            evp_data_list.append(evp_data)

        return cls(
            version=version,
            count=count,
            padding=padding,
            entry_offsets=entry_offsets,
            data=evp_data_list
        )

    @classmethod
    def try_read_at(cls, file_data: bytes, offset: int, endian: str = '<') -> Optional['EvpHeader']:
        """Read optional EVP data if an offset is present."""
        if not offset:
            return None

        br = io.BytesIO(file_data)
        br.seek(offset)
        return EvpHeader.read(br, endian)

    def write(self, bw: BinaryIO, is_loop: bool, total_frame_count: int) -> None:
        """Write EvpHeader to binary stream.
        
        Calculates all offsets internally before writing.
        """
        # Calculate all offsets before writing
        self._calculate_offsets(is_loop, total_frame_count)
        
        # Write header (I=uint, h=short, H=ushort to match Binary Template)
        bw.write(struct.pack('<IhH', self.version, self.count, self.padding))
        
        # Write entry offsets
        bw.write(struct.pack(f'<{self.count}I', *self.entry_offsets))
        
        # Write EvpData entries
        for evp_data in self.data:
            evp_data.write(bw, is_loop, total_frame_count)

    def _calculate_offsets(self, is_loop: bool, total_frame_count: int) -> None:
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
            evp_data.calculate_unit_offsets()
            
            # Calculate size of this EvpData entry
            evp_data_size = evp_data.get_size(is_loop, total_frame_count)
            
            # Advance offset for next entry
            current_offset += evp_data_size
