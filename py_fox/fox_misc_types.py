"""
Common types and utilities for Fox Engine file formats
"""
from dataclasses import dataclass
import struct
import ctypes

from ..py_utilities.utilities_hashing_cityhash import strcode32

# Type aliases for clarity
ubyte = ctypes.c_ubyte
ushort = ctypes.c_ushort
uint = ctypes.c_uint
ulong = ctypes.c_ulong
int64 = ctypes.c_int64

class StrCode32:
    """Simple converter between integers and integer strings.
    
    Used to convert:
    - File data (integers) -> Blender storage (integer strings)
    - Blender storage (integer strings) -> File data (integers)
    
    Example: 1234 <-> '1234'
    """

    def __init__(self, value: int):
        """Create StrCode32 from an integer value."""
        self.value = value
    
    @classmethod
    def from_string(cls, text: str) -> 'StrCode32':
        """Create StrCode32 from an integer string or named string.
        
        Args:
            text: String representation of an integer (e.g., '1234') or a named string (e.g., 'Root')
        
        Returns:
            StrCode32 instance with the integer value
        """
        try:
            # Try to parse as integer string first
            return cls(int(text))
        except ValueError:
            # Hash any string using the StrCode32 algorithm
            return cls(strcode32(text))
    
    def to_int(self) -> int:
        """Get the integer value."""
        return self.value
    
    def __str__(self) -> str:
        """Convert to string representation of the integer."""
        return f"{self.value}"
    
    def __repr__(self) -> str:
        """Get representation for debugging."""
        return f"StrCode32({self.value})"
    
    def __eq__(self, other) -> bool:
        """Check equality with another StrCode32 or int."""
        if isinstance(other, StrCode32):
            return self.value == other.value
        elif isinstance(other, int):
            return self.value == other
        return False
    
    def __hash__(self) -> int:
        """Make StrCode32 hashable for use in dicts/sets."""
        return hash(self.value)
    
    def __format__(self, format_spec: str) -> str:
        """Format the StrCode32 value using the underlying integer.
        
        Allows using format specifiers like :08X directly on StrCode32 objects.
        Example: f"{strcode32:08X}" will format as hex.
        """
        return format(self.value, format_spec)

class PathCode64:
    """Fox Engine's path string hashing algorithm (64-bit)"""
    def __init__(self, value: int):
        self.value = value

    @staticmethod
    def hash(text: str) -> int:
        result = 0
        for c in text:
            result = (result * 33 + ord(c)) & 0xFFFFFFFFFFFFFFFF
        return result

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
