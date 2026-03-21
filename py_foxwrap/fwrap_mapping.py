"""
Utilities for parsing and processing track mapping files.

Track mapping files define transformations to apply to imported animation tracks,
including renaming, rotation transformations, constraint setup, and more.
"""

from typing import Dict, Tuple, Optional

from ..py_core.core_logging import Debug

from .fwrap_mapping_types import TransformConstraintEntry, BoneParameters, TrackMappingData

from . import fwrap_metadata

# Reserved mapping target name: routes the track's keyframes to the armature
# object itself (object-level FCurves) instead of a pose bone.  Bracket syntax
# guarantees this string can never be a valid Blender bone name.
ARMATURE_TARGET_NAME: str = "[armature]"

# Reserved source name for standalone constraint directives in the mapping file.
# These lines are not track mappings; they declare extra constraints on the custom rig.
CONSTRAINT_TRANSFORM_SOURCE: str = "[constraint_transform]"


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
                result = fwrap_metadata.parse_offset_r_parameter(param_value)
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
                result = fwrap_metadata.parse_map_r_parameter(param_value)
                if result:
                    mapping_data['rotation_axis_map'] = result
                    map_str = ','.join([('-' if m['negate'] else '') + m['axis'] for m in result])
                    Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with rotation axis map ({map_str})")
            
            elif param_name == 'space_r':
                result = fwrap_metadata.parse_space_parameter(param_value)
                if result:
                    mapping_data['space_r'] = result
                    if result.get('space') == 'CUSTOM':
                        Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with world-space rotation constraint (owner custom bone: '{result.get('custom_bone')}')")
                    else:
                        Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with world-space rotation constraint")
            
            elif param_name == 'space_l':
                result = fwrap_metadata.parse_space_parameter(param_value)
                if result:
                    mapping_data['space_l'] = result
                    if result.get('space') == 'CUSTOM':
                        Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with world-space location constraint (owner custom bone: '{result.get('custom_bone')}')")
                    else:
                        Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with world-space location constraint")
            
            elif param_name == 'space_ik':
                result = fwrap_metadata.parse_space_parameter(param_value)
                if result:
                    mapping_data['space_ik'] = result
                    if result.get('space') == 'CUSTOM':
                        Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with IK constraint space (owner custom bone: '{result.get('custom_bone')}')")
                    else:
                        Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with IK constraint space (world)")
            
            elif param_name == 'as_ik_up':
                result = fwrap_metadata.parse_as_ik_up_parameter(param_value)
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
            if target_name == ARMATURE_TARGET_NAME:
                Debug.log_warning(
                    f"  Multiple rotation tracks map to '[armature]': {source_names}. "
                    f"Only the first encountered will be used."
                )
            else:
                Debug.log_error(f"  ERROR: Multiple rotation tracks map to '{target_name}': {source_names}")
                Debug.log_error("    Only one rotation track per target bone is allowed")

    # Check for location conflicts
    for target_name, source_names in target_to_location_sources.items():
        if len(source_names) > 1:
            if target_name == ARMATURE_TARGET_NAME:
                Debug.log_warning(
                    f"  Multiple location tracks map to '[armature]': {source_names}. "
                    f"Only the first encountered will be used."
                )
            else:
                Debug.log_error(f"  ERROR: Multiple location tracks map to '{target_name}': {source_names}")
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

                    # Standalone constraint directive: constraint_transform : ownerBone,targetBone
                    if source_name == CONSTRAINT_TRANSFORM_SOURCE:
                        parts = blender_bone_name.split(',', 1)
                        if len(parts) == 2:
                            entry = TransformConstraintEntry(
                                owner_bone=parts[0].strip(),
                                target_bone=parts[1].strip(),
                            )
                            mapping_data.transform_constraints.append(entry)
                            Debug.log(f"  Transform constraint: owner='{entry.owner_bone}' target='{entry.target_bone}'")
                        else:
                            Debug.log_warning(
                                f"  Warning: constraint_transform on line {line_num} expects 'ownerBone,targetBone', got '{blender_bone_name}'"
                            )
                        continue

                    # Add to mapping data object
                    mapping_data.add_bone_mapping(source_name, blender_bone_name, bone_mapping_dict)

        Debug.log(f"Loaded {len(mapping_data)} bone mapping(s) from {filepath}")

        # Validate track mappings
        validate_track_mappings(mapping_data.fox_to_blender)

        return mapping_data

    except (OSError, ValueError) as e:
        Debug.log_error(f"Error parsing track mapping file: {e}")
        return TrackMappingData()  # Return empty mapping data on error

