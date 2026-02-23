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


class ParamName_StrCode32Alias(IntEnum):
    SLOPE_ANGLE = 35201703
    SLOPE_DIR = 3426329078


class EventUnitInfoName_StrCode32Alias(IntEnum):
    """Readable names for EventUnitInfo.name hashes (anim_common.bt).

    These values are used during motion event parsing and storage; having an
    enum makes it easy to convert the 32‑bit integer hash back into a human
    identifier when debugging or displaying events.
    """
    # fx
    FX_CREATE_EFFECT_WITH_SKL = 312449893

    # ag
    MTEV_AG_SYNC_L = 877721620
    MTEV_AG_SYNC_R = 3647133869
    MTEV_FOOT_STOP_R = 2051014260
    MTEV_FOOT_STOP_L = 4246579437
    MTEV_FOOT_START_R = 3689287927
    MTEV_FOOT_START_L = 3049626829

    # sd
    right_foot_ground = 2122718581
    right_foot_leave = 3453979597
    left_foot_ground = 1190238672
    left_foot_leave = 3446064903
    rattle_weapon = 2416440354
    rattle_suit = 1532442511

    # Demo
    ExecCommand = 595181585
    DemoStart = 3541589503
    DemoEnd = 3534133658
    CreateLocator = 2523762371
    CreateModel = 3313525351
    VisibleModel = 1040836537
    VisibleMesh = 1799953944
    DeleteLocator = 826863926
    DeleteModel = 1968729831
    CreateCamera = 2135789169
    DeleteCamera = 4099945744
    ClipEnd = 708232468

