"""
Hash code helper types for Fox Engine values.
"""

from ..py_utilities import util_hashing_cityhash


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
            return cls(int(text))
        except ValueError:
            return cls(util_hashing_cityhash.strcode32(text))

    def to_int(self) -> int:
        """Get the integer value."""
        return self.value

    def __str__(self) -> str:
        return f"{self.value}"

    def __repr__(self) -> str:
        return f"StrCode32({self.value})"

    def __eq__(self, other) -> bool:
        if isinstance(other, StrCode32):
            return self.value == other.value
        elif isinstance(other, int):
            return self.value == other
        return False

    def __hash__(self) -> int:
        return hash(self.value)

    def __format__(self, format_spec: str) -> str:
        return format(self.value, format_spec)


class PathCode64:
    """Fox Engine path string hashing algorithm (64-bit)."""

    def __init__(self, value: int):
        self.value = value

    @staticmethod
    def hash(text: str) -> int:
        result = 0
        for c in text:
            result = (result * 33 + ord(c)) & 0xFFFFFFFFFFFFFFFF
        return result
