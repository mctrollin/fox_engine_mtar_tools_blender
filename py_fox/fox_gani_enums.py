"""
Enumerations for GANI2 animation data structures in Metal Gear Solid V.
"""
from enum import IntEnum

from ..py_core.core_logging import Debug


class CommonInfoNodeType(IntEnum):
    """Node types found in CommonInfo section."""
    LayoutTrack = 1337830127
    SkeletonList = 2447659851
    MotionPoints = 999978884


class SegmentType(IntEnum):
    QUAT = 0
    FLOAT = 1
    VECTOR2 = 2
    VECTOR3 = 3
    VECTOR4 = 4
    QUAT_DIFF = 5
    VECTOR_DIFF = 6

DIFF_SEGMENT_TYPES = frozenset({SegmentType.QUAT_DIFF, SegmentType.VECTOR_DIFF})

def get_highest_bit_size_for_segment(segment_type: SegmentType) -> int:
    """Return the highest available bit encoding for a given segment type.
    
    Args:
        segment_type: The segment type to get bit size for
        
    Returns:
        Maximum component_bit_size for the segment type:
        - QUAT/QUAT_DIFF: 15 bits
        - VECTOR2/3/4/VECTOR_DIFF/FLOAT: 32 bits
        - Other types: 0 (no override)
    """
    if segment_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
        return 15
    elif segment_type in [SegmentType.VECTOR2, SegmentType.VECTOR3, SegmentType.VECTOR4, SegmentType.VECTOR_DIFF, SegmentType.FLOAT]:
        return 32
    return 0

def get_default_bit_size_for_segment(segment_type: SegmentType) -> int:
    """Return the safe default component bit size for a given segment type.

    Quaternion types only support 12, 13, or 15 bits. Using 16 (a common
    vector default) would cause a ValueError in write_unaligned_quaternion.
    This function returns a type-correct default so callers never accidentally
    use an invalid size.

    Returns:
        - QUAT/QUAT_DIFF: 15
        - Everything else: 16
    """
    if segment_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
        return 15
    return 16

def clamp_bit_size_for_segment(segment_type: SegmentType, component_bit_size: int) -> int:
    """Validate and clamp component_bit_size to a value supported by the writer.

    Fox Engine quaternion encoding only supports 12, 13, or 15 bits.
    If a stored metadata value (e.g. 32 from a VECTOR track mis-classified
    during import, or 16 from a legacy default) is passed for a QUAT segment
    the writer will raise a ValueError.  This function silently clamps the
    value and emits a warning.

    Args:
        segment_type: The segment type that will be written.
        component_bit_size: The requested bit size (possibly invalid).

    Returns:
        A valid component_bit_size for the given segment type.
    """
    if segment_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
        if component_bit_size not in (12, 13, 15):
            valid = 15
            Debug.log_warning(
                f"  Warning: component_bit_size {component_bit_size} is not valid for QUAT "
                f"(must be 12, 13 or 15) — clamping to {valid}"
            )
            return valid
    return component_bit_size


class TrackUnitFlags(IntEnum):
    """Track unit flags from anim_common.bt TRACK_UNIT_FLAGS enum.
    
    These are bitflags that can be combined using bitwise OR.
    
    IS_STATIC flag (0x4): When set, the track has only one keyframe (static value).
                         When NOT set, the track has multiple keyframes over time.
    """
    NONE = 0x0
    LOOP = 0x1
    HERMITE_VECTOR_INTERPOLATION = 0x2
    IS_STATIC = 0x4
    
    # Helper method to check if track has multiple frames
    @staticmethod
    def has_frames(flags: int) -> bool:
        """Return True if the track has multiple keyframes (IS_STATIC is NOT set)."""
        return (flags & TrackUnitFlags.IS_STATIC) == 0

    @staticmethod
    def track_unit_flags_to_int(flags: list['TrackUnitFlags']) -> int:
        """Convert a list of TrackUnitFlags enum values to a single integer bitfield.
        
        Args:
            flags: List of TrackUnitFlags enum values to combine
            
        Returns:
            Integer with combined bitflags
            
        Example:
            >>> track_unit_flags_to_int([TrackUnitFlags.LOOP, TrackUnitFlags.IS_STATIC])
            5  # 0x1 | 0x4
        """
        result = 0
        for flag in flags:
            result |= flag
        return result

    @staticmethod
    def int_to_track_unit_flags(value: int) -> list['TrackUnitFlags']:
        """Convert an integer bitfield to a list of TrackUnitFlags enum values.
        
        Args:
            value: Integer bitfield value
            
        Returns:
            List of TrackUnitFlags enum values that are set in the bitfield
            
        Example:
            >>> int_to_track_unit_flags(5)
            [<TrackUnitFlags.LOOP: 1>, <TrackUnitFlags.IS_STATIC: 4>]
        """
        flags = []
        if value == 0:
            return [TrackUnitFlags.NONE]
        
        # Check each defined flag
        for flag in TrackUnitFlags:
            if flag == TrackUnitFlags.NONE:
                continue
            if value & flag:
                flags.append(flag)
        
        return flags


class MotionGraphFootFitFlags(IntEnum):
    """Flags for the MotionGraphFootFitInfo cache entry in the 'ag' EvpData category.

    Source: anim_common.bt FOOT_FIT_INFO_FLAGS enum.
    """
    IS_LOOP    = 0x1  # Animation loops
    START_LEFT = 0x2  # Left foot arrives first (MTEV_AG_SYNC_L event has the lowest start frame)
