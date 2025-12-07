"""
IO utility functions for writing binary data to Metal Gear Solid V files.
"""
import struct
import math
from typing import List, BinaryIO


def write_bits(buffer: bytearray, bit_pos: int, value: int, bit_size: int) -> int:
    """Write up to 32 bits to buffer starting at bit_pos (little-endian bit order).
    
    Returns new_bit_pos.
    
    Args:
        buffer: Byte array to write to (will be extended if needed)
        bit_pos: Starting bit position
        value: Value to write
        bit_size: Number of bits to write
        
    Returns:
        New bit position after write
    """
    if bit_size == 0:
        return bit_pos
    
    byte_pos = bit_pos // 8
    bit_offset = bit_pos % 8
    
    # Number of bits we need including offset
    total_bits = bit_offset + bit_size
    total_bytes = (total_bits + 7) // 8
    
    # Extend buffer if needed
    required_size = byte_pos + total_bytes
    if len(buffer) < required_size:
        buffer.extend(bytes(required_size - len(buffer)))
    
    # Mask the value to bit_size
    value = value & ((1 << bit_size) - 1)
    
    # Read existing bytes as little-endian integer
    raw = int.from_bytes(buffer[byte_pos:byte_pos + total_bytes], 'little')
    
    # Create mask for the bits we're writing
    mask = ((1 << bit_size) - 1) << bit_offset
    
    # Clear the bits we're writing to, then OR in the new value
    raw = (raw & ~mask) | (value << bit_offset)
    
    # Write back to buffer
    buffer[byte_pos:byte_pos + total_bytes] = raw.to_bytes(total_bytes, 'little')
    
    return bit_pos + bit_size


def write_unaligned_bits(buffer: bytearray, bit_pos: int, value: int, bit_size: int) -> int:
    """Write arbitrary bits across byte boundaries."""
    return write_bits(buffer, bit_pos, value, bit_size)


def write_unaligned_quaternion(buffer: bytearray, bit_pos: int, quat: List[float], bit_size: int) -> int:
    """Write a bit-packed quaternion to the buffer.
    
    The encoding stores:
    - halfTheta: angle component mapped to [0, Pi/2]
    - X, Y: normalized axis components in [0, 1]
    - Z: derived from X, Y via constraint (1.0 - X - Y)
    - Three sign bits for X, Y, Z
    
    Based on Fox Engine's axis-angle quaternion encoding.
    
    Args:
        buffer: Byte array to write to
        bit_pos: Starting bit position
        quat: Quaternion [x, y, z, w] in Fox Engine format
        bit_size: Bit size for each component (12, 13, or 15)
        
    Returns:
        New bit position after write
    """
    if bit_size not in (12, 13, 15):
        raise ValueError(f"Unsupported quaternion bit size: {bit_size}")
    
    qx, qy, qz, qw = quat
    
    # Convert quaternion to axis-angle representation
    # If w is close to 1, we have a very small rotation
    if abs(qw) > 0.9999:
        # Nearly identity rotation
        half_theta = 0.0
        X, Y, Z = 1.0, 0.0, 0.0
    else:
        # Ensure w is in [-1, 1] for acos
        qw_clamped = max(-1.0, min(1.0, qw))
        half_theta = math.acos(qw_clamped)
        sin_half = math.sin(half_theta)
        
        # Extract normalized axis
        X = qx / sin_half
        Y = qy / sin_half
        Z = qz / sin_half
    
    # Ensure half_theta is in [0, Pi/2]
    if half_theta > math.pi / 2.0:
        half_theta = math.pi - half_theta
        X, Y, Z = -X, -Y, -Z
    
    # Extract sign bits
    x_sign_bit = 1 if X < 0 else 0
    y_sign_bit = 1 if Y < 0 else 0
    z_sign_bit = 1 if Z < 0 else 0
    
    # Work with absolute values
    X, Y, Z = abs(X), abs(Y), abs(Z)
    
    # Normalize axis to sum constraint (X + Y + Z should equal 1)
    # We need to remap X, Y so that Z = 1 - X - Y
    # This is a simplification; the actual encoding may be more complex
    total = X + Y + Z
    if total > 0:
        X = X / total
        Y = Y / total
    
    # Map to integer ranges
    denominator = float((1 << bit_size) - 1)
    
    # Map halfTheta from [0, Pi/2] to [0, denominator]
    a = int((half_theta / (math.pi / 2.0)) * denominator)
    
    # Map X, Y from [0, 1] to [0, denominator]
    b = int(X * denominator)
    c = int(Y * denominator)
    
    # Clamp to valid range
    max_val = (1 << bit_size) - 1
    a = max(0, min(max_val, a))
    b = max(0, min(max_val, b))
    c = max(0, min(max_val, c))
    
    # Write three components and three sign bits
    bit_pos = write_bits(buffer, bit_pos, a, bit_size)
    bit_pos = write_bits(buffer, bit_pos, b, bit_size)
    bit_pos = write_bits(buffer, bit_pos, c, bit_size)
    
    bit_pos = write_bits(buffer, bit_pos, x_sign_bit, 1)
    bit_pos = write_bits(buffer, bit_pos, y_sign_bit, 1)
    bit_pos = write_bits(buffer, bit_pos, z_sign_bit, 1)
    
    return bit_pos


def write_float(f: BinaryIO, value: float) -> None:
    """Write a single float (32-bit little-endian) to file."""
    f.write(struct.pack('<f', value))


def write_vector2(f: BinaryIO, vec: List[float]) -> None:
    """Write a 2D vector (two floats) to file.
    
    Args:
        f: File object to write to
        vec: Vector [x, y]
    """
    f.write(struct.pack('<ff', vec[0], vec[1]))


def write_vector3(f: BinaryIO, vec: List[float]) -> None:
    """Write a 3D vector to file.
    
    Args:
        f: File object to write to
        vec: Vector [x, y, z]
    """
    f.write(struct.pack('<fff', vec[0], vec[1], vec[2]))


def write_vector4(f: BinaryIO, vec: List[float]) -> None:
    """Write a 4D vector (four floats) to file.
    
    Args:
        f: File object to write to
        vec: Vector [x, y, z, w]
    """
    f.write(struct.pack('<ffff', vec[0], vec[1], vec[2], vec[3]))



def align_length(length: int, alignment: int) -> int:
    """Align the provided length to the specified byte boundary.
    
    Args:
        length: The base length
        alignment: Alignment boundary in bytes (2, 4, 8, 16, etc.)
    """
    return (length + alignment - 1) & ~(alignment - 1)

def align_buffer(buffer: BinaryIO, alignment: int) -> None:
    """Align a binary buffer to the specified byte boundary.
    
    Args:
        buffer: Binary buffer to align (BytesIO, file handle, etc.)
        alignment: Alignment boundary in bytes (2, 4, 8, 16, etc.)
    """
    current_pos = buffer.tell()
    aligned_pos = align_length(length=current_pos, alignment=alignment)# (current_pos + alignment - 1) & ~(alignment - 1)
    if aligned_pos > current_pos:
        buffer.write(bytes(aligned_pos - current_pos))




def align_bytearray(buffer: bytearray, alignment: int) -> None:
    """Align a bytearray to the specified byte boundary.
    
    Args:
        buffer: Bytearray to align (modified in place)
        alignment: Alignment boundary in bytes (2, 4, 8, 16, etc.)
    """
    aligned_size = align_length(length=len(buffer), alignment=alignment)#(len(buffer) + alignment - 1) & ~(alignment - 1)
    if aligned_size > len(buffer):
        buffer.extend(bytes(aligned_size - len(buffer)))


def write_padding(buffer: BinaryIO, num_bytes: int) -> None:
    """Write padding bytes (zeros) to a binary buffer.
    
    Args:
        buffer: Binary buffer to write to (BytesIO, file handle, etc.)
        num_bytes: Number of padding bytes to write
    """
    if num_bytes > 0:
        buffer.write(b'\x00' * num_bytes)
