"""
Data types used by foxwrap_mapping logic.
"""
from dataclasses import dataclass
from typing import Dict, Tuple, Optional, List, Union


@dataclass
class TransformConstraintEntry:
    """A standalone Transform constraint directive from the mapping file.

    Syntax in the mapping file::

        constraint_transform : ownerBone,targetBone

    where ``ownerBone`` is the bone on the custom rig that receives the
    constraint and ``targetBone`` is the bone (on the imported armature)
    that acts as the source.

    All other constraint settings are left at Blender defaults.

    Attributes:
        owner_bone:  Bone on the custom rig that gets the TRANSFORM constraint.
        target_bone: Bone on the imported armature used as the constraint target.
    """
    owner_bone: str
    target_bone: str


@dataclass
class IkUpParameters:
    """Parameters for as_ik_up directional vector IK."""
    bone_base: str
    axis: str


@dataclass
class BoneParameters:
    """Parameters for bone mapping and transformation.
    Used to convert from native fox animation data to a custom rig in blender and back.
    
    This class replaces the dictionary-based approach for bone parameters
    with a type-safe structure containing all possible bone mapping options.
    """
    fox_name: str
    rotation_offset: Optional[List[dict]] = None
    rotation_axis_map: Optional[List[Dict[str, Union[str, bool]]]] = None
    space_r: Optional[str] = None
    space_l: Optional[str] = None
    space_ik: Optional[str] = None
    as_ik_up: Optional[IkUpParameters] = None
    track_name: Optional[str] = None
    map_r: Optional[dict] = None

    @classmethod
    def from_mapping_dict(cls, fox_name: str, mapping_dict: dict) -> 'BoneParameters':
        """Create BoneParameters from mapping file parser dictionary."""
        as_ik_up_data = mapping_dict.get('as_ik_up')
        as_ik_up_obj = None
        if as_ik_up_data and isinstance(as_ik_up_data, dict):
            as_ik_up_obj = IkUpParameters(
                bone_base=as_ik_up_data.get('bone_base', ''),
                axis=as_ik_up_data.get('axis', 'x')
            )

        return cls(
            fox_name=fox_name,
            rotation_offset=mapping_dict.get('rotation_offset'),
            rotation_axis_map=mapping_dict.get('rotation_axis_map'),
            space_r=mapping_dict.get('space_r'),
            space_l=mapping_dict.get('space_l'),
            space_ik=mapping_dict.get('space_ik'),
            as_ik_up=as_ik_up_obj,
            track_name=mapping_dict.get('name'),
            map_r=mapping_dict.get('map_r')
        )


class TrackMappingData:
    """Container for all track mapping information from a mapping file."""
    def __init__(self) -> None:
        self.fox_to_blender: Dict[str, BoneParameters] = {}
        self.fox_to_blender_names: Dict[str, str] = {}
        self.blender_to_fox_names: Dict[str, str] = {}
        self.blender_property_to_fox_base: Dict[Tuple[str, str], str] = {}
        self.blender_to_fox_base_names: Dict[str, str] = {}
        self.fox_base_to_blender_names: Dict[str, List[str]] = {}
        self.transform_constraints: List[TransformConstraintEntry] = []

    @staticmethod
    def _infer_property_type_from_params(mapping_dict: dict) -> str:
        if mapping_dict.get('space_l'):
            return 'location'
        if any([
            mapping_dict.get('space_r'),
            mapping_dict.get('rotation_offset'),
            mapping_dict.get('rotation_axis_map'),
            mapping_dict.get('as_ik_up')
        ]):
            return 'rotation'
        return 'rotation'

    def add_bone_mapping(self, fox_name: str, blender_name: str, mapping_dict: dict) -> None:
        bone_params = BoneParameters.from_mapping_dict(fox_name, mapping_dict)
        self.fox_to_blender[fox_name] = bone_params
        self.fox_to_blender_names[fox_name] = blender_name
        base_fox_name, _ = fox_name.rsplit('_', 1) if '_' in fox_name else (fox_name, -1)
        existing = self.blender_to_fox_names.get(blender_name)
        if existing is None:
            self.blender_to_fox_names[blender_name] = fox_name
        else:
            existing_base, _ = existing.rsplit('_', 1) if '_' in existing else (existing, -1)
            if existing_base != base_fox_name:
                self.blender_to_fox_names[blender_name] = base_fox_name

    def get_fox_base_name_for_blender_bone(
        self,
        blender_name: str,
        fcurve_data_path: Optional[str] = None
    ) -> Optional[str]:
        if fcurve_data_path:
            property_type = self._infer_property_type_from_fcurve(fcurve_data_path)
            property_key = (blender_name, property_type)
            result = self.blender_property_to_fox_base.get(property_key)
            if result:
                return result
        return self.blender_to_fox_base_names.get(blender_name)

    def get_blender_bones_for_fox_base(self, fox_base_name: str) -> List[str]:
        return self.fox_base_to_blender_names.get(fox_base_name, [])

    @staticmethod
    def _infer_property_type_from_fcurve(data_path: str) -> str:
        if 'rotation_quaternion' in data_path or 'rotation_euler' in data_path or 'rotation_axis_angle' in data_path:
            return 'rotation'
        if 'location' in data_path:
            return 'location'
        if 'scale' in data_path:
            return 'scale'
        return 'rotation'
