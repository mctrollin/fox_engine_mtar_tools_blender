"""
Common types and utilities for Fox Engine file formats
"""
from dataclasses import dataclass
import struct
import ctypes

# Type aliases for clarity
ubyte = ctypes.c_ubyte
ushort = ctypes.c_ushort
uint = ctypes.c_uint
ulong = ctypes.c_ulong
int64 = ctypes.c_int64

@dataclass
class SmallVector3:
    """3D vector with half-precision floats"""
    x: float
    y: float
    z: float

    @staticmethod
    def from_bytes(data: bytes) -> 'SmallVector3':
        # half-precision floats in little-endian
        x = struct.unpack('<e', data[0:2])[0]
        y = struct.unpack('<e', data[2:4])[0]
        z = struct.unpack('<e', data[4:6])[0]
        return SmallVector3(x, y, z)

@dataclass
class Quat:
    """Quaternion with full precision floats"""
    x: float
    y: float
    z: float
    w: float

    @staticmethod
    def from_bytes(data: bytes) -> 'Quat':
        x = struct.unpack('<f', data[0:4])[0]
        y = struct.unpack('<f', data[4:8])[0]
        z = struct.unpack('<f', data[8:12])[0]
        w = struct.unpack('<f', data[12:16])[0]
        return Quat(x, y, z, w)
