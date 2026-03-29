"""
Types for FRIG (Fox Rig) data structures in Metal Gear Solid V.
FRIG files define skeletal rig structures with units, bones, and masks.
"""
from dataclasses import dataclass
from typing import BinaryIO, List, Optional
from enum import IntEnum
import struct

from ..py_core.core_logging import Debug

from .fox_hash_types import StrCode32


class RigUnitType(IntEnum):
    """Types of rig units as defined in FRIG (see frig.bt RIG_UNIT_TYPE)."""
    ROOT = 1
    ORIENTATION = 2
    TWO_BONE = 3
    LOCAL_ORIENTATION = 4
    LOCAL_TRANSFORM = 5
    THREE_BONE_LIKE_TWO_BONE = 6
    TRANSFORM = 7
    ARM = 8
    LOCAL_TRANSFORM_SRT = 9
    ANIMAL_LEG = 10
    MULTI_LOCAL_ORIENTATION = 11
    TWO_BONE_TRANS = 12

    @staticmethod
    def parse_from_string(type_str: str) -> Optional['RigUnitType']:
        """Parse RigUnitType enum from string name (case-insensitive, underscores allowed).
        Args:
            type_str: String representation of the unit type (e.g., "ORIENTATION", "ARM", "TWO_BONE", "THREE_BONE_LIKE_TWO_BONE")
        Returns:
            RigUnitType enum if found, None otherwise
        Example:
            >>> RigUnitType.parse_from_string("ORIENTATION")
            <RigUnitType.ORIENTATION: 2>
        """
        try:
            return RigUnitType[type_str.upper()]
        except (KeyError, AttributeError):
            return None

    @staticmethod
    def is_world_space_unit_type(unit_type: Optional['RigUnitType']) -> bool:
        """Check if a rig unit type requires world space transforms.
        World space types are: ORIENTATION, TWO_BONE, ARM.
        Args:
            unit_type: RigUnitType to check
        Returns:
            True if the unit type requires world space transforms, False otherwise
        """
        if unit_type is None:
            return False
        world_space_types = {RigUnitType.ORIENTATION, RigUnitType.TWO_BONE, RigUnitType.ARM}
        return unit_type in world_space_types


@dataclass
class Vector3W:
    """3D vector with W component (typically used for normals)."""
    x: float
    y: float
    z: float
    w: float
    
    @classmethod
    def read(cls, br: BinaryIO) -> 'Vector3W':
        """Read a Vector3W (4 floats) from binary stream."""
        data = br.read(16)
        if len(data) < 16:
            Debug.raise_error('Unexpected EOF while reading Vector3W', EOFError)
        x, y, z, w = struct.unpack('<ffff', data)
        return cls(x=x, y=y, z=z, w=w)


@dataclass
class RigUnitDefRoot:
    """Root rig unit definition."""
    segment_index_a: int  # short
    segment_index_b: int  # short


@dataclass
class RigUnitDefOrientation:
    """Orientation rig unit definition."""
    skel_index: int  # short
    segment_index_a: int  # short


@dataclass
class RigUnitDefTransform:
    """Transform rig unit definition."""
    skel_index: int  # short
    segment_index_a: int  # short
    segment_index_b: int  # short


@dataclass
class RigUnitDefUnknown6:
    """Unknown type 6 rig unit definition."""
    unknown_data: bytes  # 16 bytes
    chain_plane_normal: Vector3W
    skel_index_a: int  # short
    skel_index_b: int  # short
    skel_index_c: int  # short
    segment_index_a: int  # short
    segment_index_b: int  # short


@dataclass
class RigUnitDefArm:
    """Arm rig unit definition."""
    unknown_data: bytes  # 16 bytes
    chain_plane_normal: Vector3W
    chain_index_a: int  # short
    chain_index_b: int  # short
    chain_index_c: int  # short
    segment_index_a: int  # short
    segment_index_b: int  # short
    segment_index_c: int  # short
    effector_skel_index: int  # short


@dataclass
class RigUnitDefTwoBone:
    """Two-bone IK rig unit definition."""
    unknown_data: bytes  # 16 bytes
    chain_plane_normal: Vector3W
    chain_index_a: int  # short
    chain_index_b: int  # short
    segment_index_a: int  # short
    segment_index_b: int  # short
    effector_skel_index: int  # short


@dataclass
class RigUnitDefUnknown9:
    """Unknown type 9 rig unit definition."""
    skel_index: int  # short


@dataclass
class RigUnitDefUnknown10:
    """Unknown type 10 rig unit definition."""
    unknown_data: bytes  # 24 bytes
    skel_index_a: int  # short
    skel_index_b: int  # short
    skel_index_c: int  # short
    skel_index_d: int  # short


@dataclass
class RigUnitDefList:
    """List rig unit definition."""
    skel_index_start: int  # short
    segment_index_start: int  # short


@dataclass
class RigUnitDefUnknown12:
    """Unknown type 12 rig unit definition."""
    unknown_data: bytes  # 16 bytes
    skel_index_a: int  # short
    skel_index_b: int  # short


@dataclass
class RigUnitDef:
    """Rig unit definition with type-specific data."""
    unit_type: RigUnitType
    track_count: int  # short
    bone_count: int  # short
    parent_bone_index: int  # short (unused)
    parent_unit_index: int  # short
    padding: int  # uint (should be 0)

    # Type-specific data (only one will be populated based on unit_type)
    data: Optional[object] = None
    
    BASE_SIZE = 16  # Type(4) + TrackCount(2) + BoneCount(2) + ParentBoneIndex(2) + ParentUnitIndex(2) + Padding(4)
    
    @classmethod
    def read(cls, br: BinaryIO) -> 'RigUnitDef':
        """Read a RigUnitDef from binary stream."""
        # Read base fields
        base_data = br.read(cls.BASE_SIZE)
        if len(base_data) < cls.BASE_SIZE:
            Debug.raise_error('Unexpected EOF while reading RigUnitDef base', EOFError)
        unit_type_val, track_count, bone_count, parent_bone_index, parent_unit_index, padding = struct.unpack('<IhhhhI', base_data)
        unit_type = RigUnitType(unit_type_val)
        # Read type-specific data
        type_data = None

        if unit_type == RigUnitType.ROOT:
            data = br.read(4)
            segment_index_a, segment_index_b = struct.unpack('<hh', data)
            type_data = RigUnitDefRoot(segment_index_a=segment_index_a, segment_index_b=segment_index_b)

        elif unit_type in [RigUnitType.LOCAL_ORIENTATION, RigUnitType.ORIENTATION]:
            data = br.read(4)
            skel_index, segment_index_a = struct.unpack('<hh', data)
            type_data = RigUnitDefOrientation(skel_index=skel_index, segment_index_a=segment_index_a)

        elif unit_type in [RigUnitType.LOCAL_TRANSFORM, RigUnitType.TRANSFORM]:
            data = br.read(6)
            skel_index, segment_index_a, segment_index_b = struct.unpack('<hhh', data)
            type_data = RigUnitDefTransform(skel_index=skel_index, segment_index_a=segment_index_a, segment_index_b=segment_index_b)
        
        elif unit_type == RigUnitType.THREE_BONE_LIKE_TWO_BONE:
            unknown_data = br.read(16)
            chain_plane_normal = Vector3W.read(br)
            indices = br.read(10)
            skel_a, skel_b, skel_c, seg_a, seg_b = struct.unpack('<hhhhh', indices)
            type_data = RigUnitDefUnknown6(
                unknown_data=unknown_data,
                chain_plane_normal=chain_plane_normal,
                skel_index_a=skel_a,
                skel_index_b=skel_b,
                skel_index_c=skel_c,
                segment_index_a=seg_a,
                segment_index_b=seg_b
            )

        elif unit_type == RigUnitType.ARM:
            unknown_data = br.read(16)
            chain_plane_normal = Vector3W.read(br)
            indices = br.read(14)
            chain_a, chain_b, chain_c, seg_a, seg_b, seg_c, effector = struct.unpack('<hhhhhhh', indices)
            type_data = RigUnitDefArm(
                unknown_data=unknown_data,
                chain_plane_normal=chain_plane_normal,
                chain_index_a=chain_a,
                chain_index_b=chain_b,
                chain_index_c=chain_c,
                segment_index_a=seg_a,
                segment_index_b=seg_b,
                segment_index_c=seg_c,
                effector_skel_index=effector
            )

        elif unit_type == RigUnitType.TWO_BONE:
            unknown_data = br.read(16)
            chain_plane_normal = Vector3W.read(br)
            indices = br.read(10)
            chain_a, chain_b, seg_a, seg_b, effector = struct.unpack('<hhhhh', indices)
            type_data = RigUnitDefTwoBone(
                unknown_data=unknown_data,
                chain_plane_normal=chain_plane_normal,
                chain_index_a=chain_a,
                chain_index_b=chain_b,
                segment_index_a=seg_a,
                segment_index_b=seg_b,
                effector_skel_index=effector
            )
            
        elif unit_type == RigUnitType.LOCAL_TRANSFORM_SRT:
            data = br.read(2)
            skel_index = struct.unpack('<h', data)[0]
            type_data = RigUnitDefUnknown9(skel_index=skel_index)

        elif unit_type == RigUnitType.ANIMAL_LEG:
            unknown_data = br.read(24)
            indices = br.read(8)
            skel_a, skel_b, skel_c, skel_d = struct.unpack('<hhhh', indices)
            type_data = RigUnitDefUnknown10(
                unknown_data=unknown_data,
                skel_index_a=skel_a,
                skel_index_b=skel_b,
                skel_index_c=skel_c,
                skel_index_d=skel_d
            )

        elif unit_type == RigUnitType.MULTI_LOCAL_ORIENTATION:
            data = br.read(4)
            skel_start, seg_start = struct.unpack('<hh', data)
            type_data = RigUnitDefList(skel_index_start=skel_start, segment_index_start=seg_start)

        elif unit_type == RigUnitType.TWO_BONE_TRANS:
            unknown_data = br.read(16)
            indices = br.read(4)
            skel_a, skel_b = struct.unpack('<hh', indices)
            type_data = RigUnitDefUnknown12(
                unknown_data=unknown_data,
                skel_index_a=skel_a,
                skel_index_b=skel_b
            )
            
        return cls(
            unit_type=unit_type,
            track_count=track_count,
            bone_count=bone_count,
            parent_bone_index=parent_bone_index,
            parent_unit_index=parent_unit_index,
            padding=padding,
            data=type_data
        )


@dataclass
class MaskUnitDef:
    """Mask unit definition for layered animation."""
    hash: StrCode32
    name: str  # char[12]
    weights: List[float]  # Array of weights (size = RigUnitCount)
    
    @classmethod
    def read(cls, br: BinaryIO, rig_unit_count: int) -> 'MaskUnitDef':
        """Read a MaskUnitDef from binary stream."""
        # Read hash and name
        data = br.read(16)  # 4 bytes hash + 12 bytes name
        if len(data) < 16:
            Debug.raise_error('Unexpected EOF while reading MaskUnitDef', EOFError)
        
        hash_val = struct.unpack('<I', data[0:4])[0]
        name_bytes = data[4:16]
        # Decode name, strip null bytes
        name = name_bytes.decode('ascii', errors='ignore').rstrip('\x00')
        
        # Read weights
        weights_data = br.read(rig_unit_count * 4)
        if len(weights_data) < rig_unit_count * 4:
            Debug.raise_error('Unexpected EOF while reading MaskUnitDef weights', EOFError)
        
        weights = list(struct.unpack(f'<{rig_unit_count}f', weights_data))
        
        return cls(hash=StrCode32(hash_val), name=name, weights=weights)


@dataclass
class Bone:
    """Bone definition linking a rig index to a name."""
    rig_index: int  # uint
    name: StrCode32
    
    SIZE = 8
    
    @classmethod
    def read(cls, br: BinaryIO) -> 'Bone':
        """Read a Bone from binary stream."""
        data = br.read(cls.SIZE)
        if len(data) < cls.SIZE:
            Debug.raise_error('Unexpected EOF while reading Bone', EOFError)
        
        rig_index, name_hash = struct.unpack('<II', data)
        return cls(rig_index=rig_index, name=StrCode32(name_hash))


@dataclass
class RigFileHeader:
    """FRIG file header."""
    name: int  # FoxDataName (StrCode64 stored as uint64)
    version: int  # uint (should be 102)
    rig_unit_count: int  # uint
    segment_count: int  # uint
    file_size: int  # uint
    bone_list_offset: int  # uint
    mask_def_offset: int  # uint
    
    SIZE = 32  # name(8) + version(4) + rig_unit_count(4) + segment_count(4) + file_size(4) + bone_list_offset(4) + mask_def_offset(4)
    
    @classmethod
    def read(cls, br: BinaryIO) -> 'RigFileHeader':
        """Read a RigFileHeader from binary stream."""
        data = br.read(cls.SIZE)
        if len(data) < cls.SIZE:
            Debug.raise_error('Unexpected EOF while reading RigFileHeader', EOFError)
        
        # Read FoxDataName as uint64 (two uints)
        name_a, name_b, version, rig_unit_count, segment_count, file_size, bone_list_offset, mask_def_offset = struct.unpack('<IIIIIIII', data)
        name = name_a | (name_b << 32)
        
        return cls(
            name=name,
            version=version,
            rig_unit_count=rig_unit_count,
            segment_count=segment_count,
            file_size=file_size,
            bone_list_offset=bone_list_offset,
            mask_def_offset=mask_def_offset
        )


@dataclass
class RigDef:
    """Rig definition containing all rig units."""
    rig_unit_def_offsets: List[int]
    unit_defs: List[RigUnitDef]
    
    @classmethod
    def read(cls, br: BinaryIO, header: RigFileHeader) -> 'RigDef':
        """Read RigDef from binary stream."""
        # Read all offsets
        offsets_data = br.read(header.rig_unit_count * 4)
        if len(offsets_data) < header.rig_unit_count * 4:
            Debug.raise_error('Unexpected EOF while reading RigDef offsets', EOFError)
        
        offsets = list(struct.unpack(f'<{header.rig_unit_count}i', offsets_data))
        
        # Read each RigUnitDef
        unit_defs = []
        for offset in offsets:
            br.seek(offset)
            unit_defs.append(RigUnitDef.read(br))
        
        return cls(rig_unit_def_offsets=offsets, unit_defs=unit_defs)


@dataclass
class MaskDef:
    """Mask definition containing layered animation masks."""
    rig_unit_count: int  # uint
    layer_count: int  # uint
    unit_def_offsets: List[int]
    unit_defs: List[MaskUnitDef]
    
    @classmethod
    def read(cls, br: BinaryIO, header: RigFileHeader) -> 'MaskDef':
        """Read MaskDef from binary stream."""
        mask_def_start = header.mask_def_offset
        br.seek(mask_def_start)
        
        # Read counts
        counts_data = br.read(8)
        if len(counts_data) < 8:
            Debug.raise_error('Unexpected EOF while reading MaskDef counts', EOFError)
        
        rig_unit_count, layer_count = struct.unpack('<II', counts_data)
        
        # Read offsets
        offsets_data = br.read(layer_count * 4)
        if len(offsets_data) < layer_count * 4:
            Debug.raise_error('Unexpected EOF while reading MaskDef offsets', EOFError)
        
        offsets = list(struct.unpack(f'<{layer_count}i', offsets_data))
        
        # Read each MaskUnitDef
        unit_defs = []
        for offset in offsets:
            br.seek(mask_def_start + offset)
            unit_defs.append(MaskUnitDef.read(br, rig_unit_count))
        
        return cls(
            rig_unit_count=rig_unit_count,
            layer_count=layer_count,
            unit_def_offsets=offsets,
            unit_defs=unit_defs
        )


@dataclass
class BoneList:
    """List of bones in the rig."""
    bone_count: int  # int
    bones: List[Bone]
    
    @classmethod
    def read(cls, br: BinaryIO, header: RigFileHeader) -> 'BoneList':
        """Read BoneList from binary stream."""
        br.seek(header.bone_list_offset)
        
        # Read bone count
        count_data = br.read(4)
        if len(count_data) < 4:
            Debug.raise_error('Unexpected EOF while reading BoneList count', EOFError)
        
        bone_count = struct.unpack('<i', count_data)[0]
        
        if bone_count < 0:
            Debug.raise_error('Unexpected bone count while reading BoneList', ValueError)

        # Read all bones
        bones = []
        for _ in range(bone_count):
            bones.append(Bone.read(br))
        
        return cls(bone_count=bone_count, bones=bones)


@dataclass
class FrigFile:
    """Complete FRIG (Fox Rig) file structure."""
    header: RigFileHeader
    rig_def: RigDef
    mask_def: MaskDef
    bone_list: BoneList
    
    @classmethod
    def read(cls, br: BinaryIO) -> 'FrigFile':
        """Read a complete FRIG file from binary stream."""
        # Read header
        br.seek(0)
        header = RigFileHeader.read(br)
        
        # Validate version
        if header.version != 102:
            Debug.raise_error(f"Unsupported FRIG version: {header.version} (expected 102)", ValueError)
        
        # Read rig definition
        rig_def = RigDef.read(br, header)
        
        # Read mask definition
        mask_def = MaskDef.read(br, header)
        
        # Read bone list
        bone_list = BoneList.read(br, header)
        
        return cls(
            header=header,
            rig_def=rig_def,
            mask_def=mask_def,
            bone_list=bone_list
        )
