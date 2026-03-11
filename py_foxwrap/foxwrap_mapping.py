"""
Utilities for parsing and processing track mapping files.

Track mapping files define transformations to apply to imported animation tracks,
including renaming, rotation transformations, constraint setup, and more.
"""

from dataclasses import dataclass
from typing import Dict, Tuple, Optional, List, Union

from ..py_utilities.utilities_logging import Debug

from .foxwrap_metadata import (
    parse_offset_r_parameter,
    parse_map_r_parameter,
    parse_space_parameter,
    parse_as_ik_up_parameter,
)


def parse_segment_suffix(fox_name: str) -> tuple[str, int]:
    """Utility for splitting Option-D track names.

    Multi-segment tracks append ``_N`` where ``N`` is a non-negative
    integer.  The base track (segment 0) usually has no suffix; in that
    case we return ``index=-1`` to disambiguate from a literal ``_0``.

    This function is used across export/import code to normalise fox
    track names before looking them up in metadata dictionaries.
    """
    if '_' in fox_name:
        parts = fox_name.rsplit('_', 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[0], int(parts[1])
    return fox_name, -1


@dataclass
class IkUpParameters:
    """Parameters for as_ik_up directional vector IK.
    
    Attributes:
        bone_base: Name of the base bone for directional calculation
        axis: Axis for directional vector ('x', 'y', or 'z')
    """
    bone_base: str
    axis: str

@dataclass
class BoneParameters:
    """Parameters for bone mapping and transformation.
    Used to convert from native fox animation data to a custom rig in blender and back.
    
    This class replaces the dictionary-based approach for bone parameters
    with a type-safe structure containing all possible bone mapping options.
    
    Attributes:
        fox_name: Fox Engine bone name (required)
        rotation_offset: Optional list of rotation offset parameters (applied in order during import)
        rotation_axis_map: Optional axis mapping parameters
        space_r: Optional rotation space specification ('world' or 'custom,<bone>')
        space_l: Optional location space specification ('world' or 'custom,<bone>')
        space_ik: Optional IK space specification for as_ik_up Transformation constraint ('world' or 'custom,<bone>')
        as_ik_up: Optional IK up vector parameters
        track_name: Optional track name from mapping file
        map_r: Optional rest pose correction parameters for LOCAL space tracks (similarity transformation)
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
        """Create BoneParameters from mapping file parser dictionary.
        
        Converts the dict format used by foxwrap_mapping.py parser to typed BoneParameters.
        
        Args:
            fox_name: Fox Engine bone name
            mapping_dict: Dictionary from parse_mapping_line() containing:
                - 'name': Blender bone name (stored as track_name)
                - 'rotation_offset': List of rotation offset dicts
                - 'rotation_axis_map': Axis mapping list
                - 'space_r': Rotation space specification
                - 'space_l': Location space specification
                - 'as_ik_up': IK up vector dict (converted to IkUpParameters)
                - 'map_r': Rest pose correction dict
                
        Returns:
            BoneParameters object with all fields properly typed
        """
        # Convert as_ik_up dict to IkUpParameters object if present
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
            track_name=mapping_dict.get('name'),  # Blender bone name from parser
            map_r=mapping_dict.get('map_r')
        )

class TrackMappingData:
    """Container for all track mapping information from a mapping file.
    
    This class holds all the different mapping dictionaries needed by both
    import and export operations in a single convenient structure.
    
    Attributes:
        fox_to_blender: Maps fox_bone_name -> BoneParameters
        fox_to_blender_names: Maps fox_bone_name -> blender_bone_name string (utility)
    """
    
    def __init__(self) -> None:
        """Initialize empty mapping data."""
        self.fox_to_blender: Dict[str, BoneParameters] = {}  # fox_name -> BoneParameters
        self.fox_to_blender_names: Dict[str, str] = {}  # fox_name -> blender_name
        self.blender_to_fox_names: Dict[str, str] = {}  # blender_name -> fox_name (reverse mapping)
        self.blender_property_to_fox_base: Dict[Tuple[str, str], str] = {}  # (blender_name, property_type) -> fox_base_name (NO COLLISIONS)
        self.blender_to_fox_base_names: Dict[str, str] = {}  # blender_name -> fox_base_name (fallback for single-property)
        self.fox_base_to_blender_names: Dict[str, List[str]] = {}  # fox_base_name -> [blender_names] (one-to-many)
    
    @staticmethod
    def _infer_property_type_from_params(mapping_dict: dict) -> str:
        """Infer property type from mapping parameters.
        
        Determines whether a mapping targets rotation, location, or scale properties
        based on which transformation parameters are present.
        
        Args:
            mapping_dict: Dictionary with transformation parameters from parser
                - rotation_offset: Rotation offset parameters
                - rotation_axis_map: Axis mapping parameters
                - space_r: Rotation space specification
                - space_l: Location space specification
                - as_ik_up: IK up vector parameters (rotation converted to location)
        
        Returns:
            Property type string: "rotation", "location", or "scale"
        
        Logic:
            - Has space_l -> "location"
            - Has space_r, offset_r, rotation_axis_map, or as_ik_up -> "rotation"
            - Default -> "rotation" (most common case)
        """
        # Location takes priority (explicit space_l parameter)
        if mapping_dict.get('space_l'):
            return "location"
        
        # Rotation indicators
        if any([
            mapping_dict.get('space_r'),
            mapping_dict.get('rotation_offset'),
            mapping_dict.get('rotation_axis_map'),
            mapping_dict.get('as_ik_up')
        ]):
            return "rotation"
        
        # Default to rotation (most common, no params means pass-through rotation)
        return "rotation"
    
    def add_bone_mapping(self, fox_name: str, blender_name: str, mapping_dict: dict) -> None:
        """Add a bone mapping entry.
        
        Args:
            fox_name: Fox Engine bone name (source)
            blender_name: Blender bone name (target)
            mapping_dict: Dictionary with transformation parameters from parser
        """
        # Create typed BoneParameters object
        bone_params = BoneParameters.from_mapping_dict(fox_name, mapping_dict)
        self.fox_to_blender[fox_name] = bone_params
        
        # Store simple name mapping
        self.fox_to_blender_names[fox_name] = blender_name
        
        # Parse base fox name (strip segment suffix if present)
        base_fox_name, _ = parse_segment_suffix(fox_name)
        
        # Build reverse mapping (Blender -> Fox) - full fox name with suffix
        # Last wins, but no warning (multi-property to same bone is expected)
        self.blender_to_fox_names[blender_name] = fox_name
        
        # Infer property type from mapping parameters
        property_type = self._infer_property_type_from_params(mapping_dict)
        
        # Build property-specific reverse mapping - NO COLLISIONS
        # Key includes property type, so rotation/location have unique keys
        property_key = (blender_name, property_type)
        if property_key in self.blender_property_to_fox_base:
            existing_base = self.blender_property_to_fox_base[property_key]
            if existing_base != base_fox_name:
                Debug.log_warning(
                    f"Multi-track collision: Blender bone '{blender_name}' property '{property_type}' "
                    f"maps to Fox tracks '{existing_base}' and '{base_fox_name}'. "
                    f"Using '{base_fox_name}'."
                )
        self.blender_property_to_fox_base[property_key] = base_fox_name
        
        # Build simple reverse mapping (fallback for backward compat)
        # Only store if not already set to avoid collisions
        if blender_name not in self.blender_to_fox_base_names:
            self.blender_to_fox_base_names[blender_name] = base_fox_name
        
        # Build forward base name mapping (Fox base -> Blender bones, one-to-many)
        if base_fox_name not in self.fox_base_to_blender_names:
            self.fox_base_to_blender_names[base_fox_name] = []
        if blender_name not in self.fox_base_to_blender_names[base_fox_name]:
            self.fox_base_to_blender_names[base_fox_name].append(blender_name)
    
    
    def get_fox_base_name_for_blender_bone(
        self,
        blender_name: str,
        fcurve_data_path: Optional[str] = None
    ) -> Optional[str]:
        """Get Fox base track name for Blender bone with property-type-aware lookup.
        
        Primary method for baking workflow. Uses F-curve data path to determine
        property type, avoiding collisions when multiple properties map to same bone.
        
        Args:
            blender_name: Blender bone name (e.g., "hand_ik.R")
            fcurve_data_path: Optional F-curve data path for property type inference
                             (e.g., 'pose.bones["hand_ik.R"].rotation_quaternion')
            
        Returns:
            Fox base track name without segment suffix (e.g., "RIG_SKL_023_RHAND"), or None if not found
        
        Example:
            >>> # Property-aware lookup (preferred for multi-property tracks)
            >>> mapping.get_fox_base_name_for_blender_bone(
            ...     "hand_ik.R", 
            ...     'pose.bones["hand_ik.R"].rotation_quaternion'
            ... )
            "RIG_SKL_023_RHAND"  # From RIG_SKL_023_RHAND_0 (rotation property)
            
            >>> # Simple lookup (fallback for single-property tracks)
            >>> mapping.get_fox_base_name_for_blender_bone("head")  # e.g. returns "RIG_SKL_004_HEAD"
            "RIG_SKL_004_HEAD"  # Works if only one property maps to this bone
        """
        if fcurve_data_path:
            # Property-specific lookup using F-curve property type
            property_type = self._infer_property_type_from_fcurve(fcurve_data_path)
            property_key = (blender_name, property_type)
            result = self.blender_property_to_fox_base.get(property_key)
            if result:
                return result
        
        # Fallback to simple lookup (for single-property tracks or backward compat)
        return self.blender_to_fox_base_names.get(blender_name)
    
    def get_blender_bones_for_fox_base(self, fox_base_name: str) -> List[str]:
        """Get all Blender bones mapped from a Fox base track.
        
        Args:
            fox_base_name: Fox base track name without segment suffix (e.g., "RIG_SKL_023_RHAND")
            
        Returns:
            List of Blender bone names mapped from this Fox track (may be empty)
        
        Example:
            >>> mapping.get_blender_bones_for_fox_base("RIG_SKL_023_RHAND")
            ["hand_ik.R"]  # Even if mapping has RHand_0, RHand_1 -> same bone
        """
        return self.fox_base_to_blender_names.get(fox_base_name, [])
    
    @staticmethod
    def _infer_property_type_from_fcurve(data_path: str) -> str:
        """Infer property type from F-curve data path.
        
        Extracts the property type (rotation, location, scale) from a Blender F-curve
        data path by checking which property name appears in the path.
        
        Args:
            data_path: F-curve data path (e.g., 'pose.bones["hand_ik.R"].rotation_quaternion')
            
        Returns:
            Property type string: "rotation", "location", or "scale"
        
        Example:
            >>> _infer_property_type_from_fcurve('pose.bones["bone"].rotation_quaternion')
            "rotation"
            >>> _infer_property_type_from_fcurve('pose.bones["bone"].location')
            "location"
            >>> _infer_property_type_from_fcurve('pose.bones["bone"].scale')
            "scale"
        """
        data_path_lower = data_path.lower()
        if "rotation" in data_path_lower:
            return "rotation"
        elif "location" in data_path_lower:
            return "location"
        elif "scale" in data_path_lower:
            return "scale"
        # Default to rotation for unknown properties
        return "rotation"
    
    def __len__(self) -> int:
        """Return number of bone mappings."""
        return len(self.fox_to_blender)


def parse_mapping_line(line: str, line_num: int) -> Optional[Tuple[str, dict]]:
    """Parse a single line from the mapping file.
    
    Args:
        line: Line to parse
        line_num: Line number for error reporting
        
    Returns:
        Tuple of (source_name, mapping_data) or None if line should be skipped
    """
    # Remove BOM if present then skip empty lines and comments
    # (some editors may insert a UTF-8 BOM on the first line)
    if line.startswith('\ufeff'):
        line = line.lstrip('\ufeff')
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    
    # Parse mapping: from_name : to_name ; param1=value1 ; param2=value2
    if ':' not in line:
        Debug.log_warning(f"  Warning: No ':' separator on line {line_num}: '{line}'")
        return None
    
    # Split by colon to get from_name and the rest
    colon_parts = line.split(':', 1)
    from_name = colon_parts[0].strip()
    
    if not from_name:
        Debug.log_warning(f"  Warning: Invalid mapping on line {line_num}: '{line}'")
        return None
    
    # Split the rest by semicolon to get to_name and parameters
    rest = colon_parts[1].strip()
    semicolon_parts = rest.split(';')
    
    # First part is the to_name
    to_name = semicolon_parts[0].strip()
    
    if not to_name:
        Debug.log_warning(f"  Warning: Invalid mapping on line {line_num}: '{line}'")
        return None
    
    # Initialize mapping data
    mapping_data = {'name': to_name}
    
    # Parse parameters (if any)
    if len(semicolon_parts) > 1:
        for param_str in semicolon_parts[1:]:
            param_str = param_str.strip()
            if not param_str:
                continue
            
            if '=' not in param_str:
                Debug.log_warning(f"  Warning: Invalid parameter format '{param_str}' on line {line_num}")
                continue
            
            param_parts = param_str.split('=', 1)
            param_name = param_parts[0].strip()
            param_value = param_parts[1].strip()
            
            # Handle different parameter types
            if param_name == 'offset_r':
                result = parse_offset_r_parameter(param_value)
                if result:
                    # Support multiple offset_r parameters - store as list
                    if 'rotation_offset' not in mapping_data:
                        mapping_data['rotation_offset'] = []
                    mapping_data['rotation_offset'].append(result)
                    euler = result['euler']
                    order = result['order']
                    offset_index = len(mapping_data['rotation_offset'])
                    Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with rotation offset #{offset_index}: ({euler[0]}, {euler[1]}, {euler[2]}) {order}")
            
            elif param_name == 'map_r':
                result = parse_map_r_parameter(param_value)
                if result:
                    mapping_data['rotation_axis_map'] = result
                    map_str = ','.join([('-' if m['negate'] else '') + m['axis'] for m in result])
                    Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with rotation axis map ({map_str})")
            
            elif param_name == 'space_r':
                result = parse_space_parameter(param_value)
                if result:
                    mapping_data['space_r'] = result
                    if result.get('space') == 'CUSTOM':
                        Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with world-space rotation constraint (owner custom bone: '{result.get('custom_bone')}')")
                    else:
                        Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with world-space rotation constraint")
            
            elif param_name == 'space_l':
                result = parse_space_parameter(param_value)
                if result:
                    mapping_data['space_l'] = result
                    if result.get('space') == 'CUSTOM':
                        Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with world-space location constraint (owner custom bone: '{result.get('custom_bone')}')")
                    else:
                        Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with world-space location constraint")
            
            elif param_name == 'space_ik':
                result = parse_space_parameter(param_value)
                if result:
                    mapping_data['space_ik'] = result
                    if result.get('space') == 'CUSTOM':
                        Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with IK constraint space (owner custom bone: '{result.get('custom_bone')}')")
                    else:
                        Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with IK constraint space (world)")
            
            elif param_name == 'as_ik_up':
                result = parse_as_ik_up_parameter(param_value)
                if result:
                    mapping_data['as_ik_up'] = result
                    Debug.log(f"  Mapping '{from_name}' -> '{to_name}' as directional vector: base='{result['bone_base']}', axis={result['axis']}")
            
            else:
                # Unknown parameter - store it anyway for future extensibility
                mapping_data[param_name] = param_value
                Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with {param_name}={param_value}")
    
    return (from_name, mapping_data)

def validate_track_mappings(track_mapping: Dict[str, BoneParameters]) -> None:
    """Validate that only one rotation track and one location track map to each target bone.
    
    Also validates that space_ik is only used with as_ik_up.
    
    This allows separate rotation and location tracks to map to the same bone,
    but prevents conflicts like multiple rotation tracks targeting the same bone.
    
    Args:
        track_mapping: Dictionary of fox_name -> BoneParameters to validate
    """
    # Track which source tracks map to each target, categorized by type
    target_to_rotation_sources = {}
    target_to_location_sources = {}
    
    for source_name, bone_params in track_mapping.items():
        target_name = bone_params.track_name
        if not target_name:
            continue
        
        # Validate space_ik is only used with as_ik_up
        if bone_params.space_ik and not bone_params.as_ik_up:
            Debug.log_warning(f"  Warning: '{source_name}' has space_ik but no as_ik_up parameter. space_ik will be ignored.")
        
        # Determine if this is a rotation or location track based on parameters
        # as_ik_up is still a rotation track (converted to location during import)
        is_rotation_track = any([
            bone_params.rotation_offset,
            bone_params.rotation_axis_map,
            bone_params.space_r,
            bone_params.as_ik_up
        ])
        is_location_track = bone_params.space_l is not None
        
        # Track rotation sources
        if is_rotation_track:
            if target_name not in target_to_rotation_sources:
                target_to_rotation_sources[target_name] = []
            target_to_rotation_sources[target_name].append(source_name)
        
        # Track location sources
        if is_location_track:
            if target_name not in target_to_location_sources:
                target_to_location_sources[target_name] = []
            target_to_location_sources[target_name].append(source_name)
    
    # Check for rotation conflicts
    for target_name, source_names in target_to_rotation_sources.items():
        if len(source_names) > 1:
            Debug.log_error(f"  ERROR: Multiple rotation tracks map to '{target_name}': {source_names}")
            Debug.log_error("    Only one rotation track per target bone is allowed")
    
    # Check for location conflicts
    for target_name, source_names in target_to_location_sources.items():
        if len(source_names) > 1:
            Debug.log_error(f"  ERROR: Multiple location tracks map to '{target_name}': {source_names}")
            Debug.log_error("    Only one location track per target bone is allowed")
            Debug.log_error("    Only one location track per target bone is allowed")

def parse_track_mapping_file(filepath: str) -> TrackMappingData:
    """Parse a track mapping file into a TrackMappingData object.
    
    The mapping file only contains bone-to-bone mapping entries and optional
    transformation parameters.  No metadata directives are required or processed.
    Blank lines or lines beginning with ``#`` are ignored.  Each non-comment
    line has the form::
        source_name : target_name ; param1=value1 ; ...
    where parameters control offsets, axis mapping, world-space constraints,
    and IK conversions.

    Args:
        filepath: Path to the .txt mapping file

    Returns:
        TrackMappingData object containing all mapping information
    """
    mapping_data = TrackMappingData()

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                # skip empty lines and comments right away (parser also checks,
                # but doing it here prevents unnecessary warnings from later code)
                stripped = line.strip()
                if not stripped or stripped.startswith('#'):
                    continue

                # parse each line as a bone mapping entry
                result = parse_mapping_line(line, line_num)
                if result:
                    source_name, bone_mapping_dict = result  # source_name is the Fox bone name
                    blender_bone_name = bone_mapping_dict['name']

                    # Add to mapping data object
                    mapping_data.add_bone_mapping(source_name, blender_bone_name, bone_mapping_dict)

        Debug.log(f"Loaded {len(mapping_data)} bone mapping(s) from {filepath}")

        # Validate track mappings
        validate_track_mappings(mapping_data.fox_to_blender)

        return mapping_data

    except (OSError, ValueError) as e:
        Debug.log_error(f"Error parsing track mapping file: {e}")
        return TrackMappingData()  # Return empty mapping data on error

