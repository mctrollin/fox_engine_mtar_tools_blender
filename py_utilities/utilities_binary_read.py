"""
IO utility functions for reading binary data from Metal Gear Solid V files.
"""
import struct
import math
from typing import List, Tuple


def read_bits(buffer: bytes, bit_pos: int, bit_size: int) -> tuple[int, int]:
    """Read up to 32 bits from buffer starting at bit_pos (little-endian bit order).

    Returns (value, new_bit_pos).
    """
    if bit_size == 0:
        return 0, bit_pos

    byte_pos = bit_pos // 8
    bit_offset = bit_pos % 8

    # Number of bits we need including offset
    total_bits = bit_offset + bit_size
    total_bytes = (total_bits + 7) // 8

    # Guard bounds
    if byte_pos + total_bytes > len(buffer):
        raise IndexError("read_bits out of range")

    # Read little-endian integer from those bytes
    raw = int.from_bytes(buffer[byte_pos:byte_pos + total_bytes], 'little')

    # Shift away the low offset bits and mask the requested size
    value = (raw >> bit_offset) & ((1 << bit_size) - 1)
    return value, bit_pos + bit_size


def read_unaligned_bits(buffer: bytes, bit_pos: int, bit_size: int) -> tuple[int, int]:
    """Read arbitrary bits across byte boundaries."""
    return read_bits(buffer, bit_pos, bit_size)


def read_unaligned_quaternion(buffer: bytes, bit_pos: int, bit_size: int) -> tuple[list[float], int]:
    """Read a bit-packed quaternion from the buffer.
    
    The encoding stores:
    - halfTheta: angle component mapped to [0, Pi/2]
    - X, Y: normalized axis components in [0, 1]
    - Z: derived from X, Y via constraint (1.0 - X - Y)
    - Three sign bits for X, Y, Z
    
    Based on Fox Engine's axis-angle quaternion encoding.
    """
    if bit_size not in (12, 13, 15):
        raise ValueError(f"Unsupported quaternion bit size: {bit_size}")

    # Read three components and three sign bits
    a, bit_pos = read_bits(buffer, bit_pos, bit_size)
    b, bit_pos = read_bits(buffer, bit_pos, bit_size)
    c, bit_pos = read_bits(buffer, bit_pos, bit_size)

    x_sign_bit, bit_pos = read_bits(buffer, bit_pos, 1)
    y_sign_bit, bit_pos = read_bits(buffer, bit_pos, 1)
    z_sign_bit, bit_pos = read_bits(buffer, bit_pos, 1)

    # Normalize using correct denominator: (1 << bit_size) - 1
    denominator = float((1 << bit_size) - 1)
    
    # Map halfTheta to [0, Pi/2] range
    half_theta = (a / denominator) * (math.pi / 2.0)
    
    # Map X, Y to [0, 1] range
    X = b / denominator
    Y = c / denominator
    Z = 1.0 - X - Y
    
    # Normalize the axis vector (X, Y, Z)
    length = math.sqrt(X*X + Y*Y + Z*Z)
    X /= length
    Y /= length
    Z /= length
    
    # Apply sign bits to axis components
    if x_sign_bit > 0:
        X = -X
    if y_sign_bit > 0:
        Y = -Y
    if z_sign_bit > 0:
        Z = -Z
    
    # Convert axis-angle representation to quaternion
    # q = [sin(halfTheta) * axis, cos(halfTheta)]
    sin_half = math.sin(half_theta)
    cos_half = math.cos(half_theta)
    
    qx = sin_half * X
    qy = sin_half * Y
    qz = sin_half * Z
    qw = cos_half

    return [qx, qy, qz, qw], bit_pos


def read_float(buffer: bytes, offset: int) -> Tuple[float, int]:
    """Read a single float (32-bit little-endian) from buffer at offset.

    Returns (value, new_offset).
    """
    value = struct.unpack('<f', buffer[offset:offset+4])[0]
    return value, offset + 4


def read_vector2(buffer: bytes, offset: int) -> Tuple[List[float], int]:
    """Read a 2D vector (two floats) from buffer at offset.

    Returns ( [x, y], new_offset ).
    """
    vec = struct.unpack('<ff', buffer[offset:offset+8])
    return [vec[0], vec[1]], offset + 8


def read_vector3(buffer: bytes, offset: int) -> Tuple[List[float], int]:
    """Read a 3D vector from the buffer."""
    vec = struct.unpack('<fff', buffer[offset:offset+12])
    return list(vec), offset + 12


def read_vector4(buffer: bytes, offset: int) -> Tuple[List[float], int]:
    """Read a 4D vector (four floats) from buffer at offset.

    Returns ( [x, y, z, w], new_offset ).
    """
    vec = struct.unpack('<ffff', buffer[offset:offset+16])
    return [vec[0], vec[1], vec[2], vec[3]], offset + 16

