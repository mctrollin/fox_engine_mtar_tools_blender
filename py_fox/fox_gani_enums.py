"""
Enumerations for GANI2 animation data structures in Metal Gear Solid V.
"""
from enum import IntEnum


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


