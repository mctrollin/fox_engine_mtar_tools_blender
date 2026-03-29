"""
IO utility functions for reading binary data from Metal Gear Solid V files.
"""
import struct
import math
from typing import List, Tuple

from ..py_core.core_logging import Debug


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
        Debug.raise_error("read_bits out of range", IndexError)

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
        Debug.raise_error(f"Unsupported quaternion bit size: {bit_size}", ValueError)

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


def read_anim_half(buffer: bytes, offset: int) -> Tuple[float, int]:
    """Read an AnimHalf (Fox Engine 16-bit half-precision float) from buffer.
    
    AnimHalf is a custom 16-bit float format used by Fox Engine:
    - Bits 0-9 (10 bits): Mantissa
    - Bits 10-14 (5 bits): Exponent  
    - Bit 15 (1 bit): Sign
    
    Conversion algorithm from anim_common.bt:
        num1 = (value & 0x7C00)
        if num1 > 0:
            num1 = (num1 + 0x1dc00) << 13
        num1 |= ((value & 0x8000) << 16) | ((value & 0x3FF) << 13)
        result = interpret num1 as float32
    
    Returns (float_value, new_offset).
    """
    value = struct.unpack('<H', buffer[offset:offset+2])[0]
    
    num1 = value & 0x7C00  # Extract exponent bits
    if num1 > 0:
        num1 = (num1 + 0x1dc00) << 13
    num1 |= ((value & 0x8000) << 16) | ((value & 0x3FF) << 13)
    
    # Convert uint32 to float32
    float_value = struct.unpack('<f', struct.pack('<I', num1))[0]
    
    return float_value, offset + 2


def read_float(buffer: bytes, offset: int) -> Tuple[float, int]:
    """Read a single float (32-bit little-endian) from buffer at offset.

    Returns (value, new_offset).
    """
    value = struct.unpack('<f', buffer[offset:offset+4])[0]
    return value, offset + 4


def read_vector2(buffer: bytes, offset: int, component_bit_size: int = 32) -> Tuple[List[float], int]:
    """Read a 2D vector from buffer at offset.
    
    Args:
        buffer: Binary data buffer
        offset: Starting offset in bytes
        component_bit_size: Bit size per component (16 for AnimHalf, 32 for float)
    
    Returns ( [x, y], new_offset ).
    """
    if component_bit_size == 16:
        # Read as AnimHalf (2 bytes per component)
        x, offset = read_anim_half(buffer, offset)
        y, offset = read_anim_half(buffer, offset)
        return [x, y], offset
    else:  # component_bit_size == 32
        # Read as float (4 bytes per component)
        vec = struct.unpack('<ff', buffer[offset:offset+8])
        return [vec[0], vec[1]], offset + 8


def read_vector3(buffer: bytes, offset: int, component_bit_size: int = 32) -> Tuple[List[float], int]:
    """Read a 3D vector from buffer at offset.
    
    Args:
        buffer: Binary data buffer
        offset: Starting offset in bytes
        component_bit_size: Bit size per component (0 for empty, 16 for AnimHalf, 32 for float)
    
    Returns ( [x, y, z], new_offset ).
    """
    if component_bit_size == 0:
        # Empty vector (no data)
        return [0.0, 0.0, 0.0], offset
    elif component_bit_size == 16:
        # Read as AnimHalf (2 bytes per component)
        x, offset = read_anim_half(buffer, offset)
        y, offset = read_anim_half(buffer, offset)
        z, offset = read_anim_half(buffer, offset)
        return [x, y, z], offset
    else:  # component_bit_size == 32
        # Read as float (4 bytes per component)
        vec = struct.unpack('<fff', buffer[offset:offset+12])
        return list(vec), offset + 12


def read_vector4(buffer: bytes, offset: int, component_bit_size: int = 32) -> Tuple[List[float], int]:
    """Read a 4D vector from buffer at offset.
    
    Args:
        buffer: Binary data buffer
        offset: Starting offset in bytes
        component_bit_size: Bit size per component (16 for AnimHalf, 32 for float)
    
    Returns ( [x, y, z, w], new_offset ).
    """
    if component_bit_size == 16:
        # Read as AnimHalf (2 bytes per component)
        x, offset = read_anim_half(buffer, offset)
        y, offset = read_anim_half(buffer, offset)
        z, offset = read_anim_half(buffer, offset)
        w, offset = read_anim_half(buffer, offset)
        return [x, y, z, w], offset
    else:  # component_bit_size == 32
        # Read as float (4 bytes per component)
        vec = struct.unpack('<ffff', buffer[offset:offset+16])
        return [vec[0], vec[1], vec[2], vec[3]], offset + 16

