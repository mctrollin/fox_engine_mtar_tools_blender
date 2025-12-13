"""
Utilities for parsing and processing track mapping files.

Track mapping files define transformations to apply to imported animation tracks,
including renaming, rotation transformations, constraint setup, and more.
"""

from typing import Dict, Tuple, Optional

from ..py_utilities.utilities_logging import Debug

from .foxwrap_metadata import (
    parse_track_metadata,
    parse_offset_r_parameter,
    parse_map_r_parameter,
    parse_space_parameter,
    parse_as_ik_up_parameter,
)


class TrackMappingData:
    """Container for all track mapping information from a mapping file.
    
    This class holds all the different mapping dictionaries needed by both
    import and export operations in a single convenient structure.
    
    Attributes:
        fox_to_blender: Maps fox_bone_name -> mapping_data dict (for import)
        track_metadata: Maps track_name -> metadata dict (for import)
        blender_to_fox: Maps blender_bone_name -> {fox_name, params} dict (for export)
        fox_to_blender_names: Maps fox_bone_name -> blender_bone_name string (utility)
    """
    
    def __init__(self) -> None:
        """Initialize empty mapping data."""
        self.fox_to_blender: Dict[str, dict] = {}  # For importer: fox_name -> mapping_data
        self.track_metadata: Dict[str, dict] = {}  # For importer: track_name -> metadata
        self.blender_to_fox: Dict[str, dict] = {}  # For exporter: blender_name -> {fox_name, params}
        self.fox_to_blender_names: Dict[str, str] = {}  # Utility: fox_name -> blender_name
    
    def add_bone_mapping(self, fox_name: str, blender_name: str, mapping_data: dict) -> None:
        """Add a bone mapping entry.
        
        Args:
            fox_name: Fox Engine bone name (source)
            blender_name: Blender bone name (target)
            mapping_data: Dictionary with transformation parameters
        """
        # Store for importer (fox -> blender with params)
        self.fox_to_blender[fox_name] = mapping_data
        
        # Store for exporter (blender -> fox with params)
        self.blender_to_fox[blender_name] = {
            'fox_name': fox_name,
            **mapping_data
        }
        
        # Store simple name mapping
        self.fox_to_blender_names[fox_name] = blender_name
    
    def add_track_metadata(self, track_name: str, metadata: dict) -> None:
        """Add track metadata entry.
        
        Args:
            track_name: Name of the track
            metadata: Metadata dictionary with segments, flags, type, etc.
        """
        self.track_metadata[track_name] = metadata
    
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
    # Skip empty lines and comments
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    
    # Parse mapping: from_name : to_name ; param1=value1 ; param2=value2
    if ':' not in line:
        Debug.log(f"  Warning: No ':' separator on line {line_num}: '{line}'")
        return None
    
    # Split by colon to get from_name and the rest
    colon_parts = line.split(':', 1)
    from_name = colon_parts[0].strip()
    
    if not from_name:
        Debug.log(f"  Warning: Invalid mapping on line {line_num}: '{line}'")
        return None
    
    # Split the rest by semicolon to get to_name and parameters
    rest = colon_parts[1].strip()
    semicolon_parts = rest.split(';')
    
    # First part is the to_name
    to_name = semicolon_parts[0].strip()
    
    if not to_name:
        Debug.log(f"  Warning: Invalid mapping on line {line_num}: '{line}'")
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
                Debug.log(f"  Warning: Invalid parameter format '{param_str}' on line {line_num}")
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
                    custom_bone = result.get('custom_bone')
                    if custom_bone:
                        Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with world space rotation constraint (custom space: '{custom_bone}')")
                    else:
                        Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with world space rotation constraint")
            
            elif param_name == 'space_l':
                result = parse_space_parameter(param_value)
                if result:
                    mapping_data['space_l'] = result
                    Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with world space location constraint")
            
            elif param_name == 'as_ik_up':
                result = parse_as_ik_up_parameter(param_value)
                if result:
                    mapping_data['as_ik_up'] = result
                    Debug.log(f"  Mapping '{from_name}' -> '{to_name}' as directional vector: base='{result['bone_base']}', axis={result['axis']}, distance={result['distance']}")
            
            else:
                # Unknown parameter - store it anyway for future extensibility
                mapping_data[param_name] = param_value
                Debug.log(f"  Mapping '{from_name}' -> '{to_name}' with {param_name}={param_value}")
    
    return (from_name, mapping_data)

def validate_track_mappings(track_mapping: Dict[str, dict]) -> None:
    """Validate that only one rotation track and one location track map to each target bone.
    
    This allows separate rotation and location tracks to map to the same bone,
    but prevents conflicts like multiple rotation tracks targeting the same bone.
    
    Args:
        track_mapping: Dictionary to validate (not modified)
    """
    # Track which source tracks map to each target, categorized by type
    target_to_rotation_sources = {}
    target_to_location_sources = {}
    
    for source_name, mapping_data in track_mapping.items():
        target_name = mapping_data.get('name')
        if not target_name:
            continue
        
        # Determine if this is a rotation or location track based on parameters
        # as_ik_up is still a rotation track (converted to location during import)
        is_rotation_track = any(key in mapping_data for key in ['rotation_offset', 'rotation_axis_map', 'space_r', 'as_ik_up'])
        is_location_track = 'space_l' in mapping_data
        
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
            Debug.log(f"  ERROR: Multiple rotation tracks map to '{target_name}': {source_names}")
            Debug.log("    Only one rotation track per target bone is allowed")
    
    # Check for location conflicts
    for target_name, source_names in target_to_location_sources.items():
        if len(source_names) > 1:
            Debug.log(f"  ERROR: Multiple location tracks map to '{target_name}': {source_names}")
            Debug.log("    Only one location track per target bone is allowed")

def parse_track_mapping_file(filepath: str) -> TrackMappingData:
    """Parse a track mapping file into a TrackMappingData object.
    
    The mapping file defines transformations to apply to imported tracks,
    including renaming, rotation transformations, and adding data from other tracks.
    
    File format:
    @track <name> : segments=<segment_list> ; [flags=<flags>] ; [type=<rig_type>] ; [bits=<compression>]
    from_name : to_name
    or with transformation parameters:
    from_name : to_name ; param1=value1 ; param2=value2
    
    Track metadata (@track directives):
    - segments: Pipe-separated segment types (e.g., "q|v|q" or "rotation:quat|position:vec3")
      Shorthand: q=rotation:quat, qd=rotation:quatdiff, v=position:vec3, vd=position:vec3diff, f=scale:float
    - flags: Comma-separated flags (e.g., "static", "animated", "compressed")
    - type: Rig unit type hint (e.g., ROOT, ARM, ORIENTATION) - for documentation
    - bits: Quaternion compression bits (12, 14, or 16, default: 16)
    
    Supported transformation parameters:
    - offset_r: Rotation offset as euler_x,euler_y,euler_z,order (e.g., offset_r=90,0,0,xyz)
                Can be specified multiple times; offsets are applied in order of appearance
    - map_r: Rotation axis mapping (e.g., map_r=x,y,z or map_r=y,-x,z)
    - space_r: Rotation constraint space (e.g., space_r=ws or space_r=ws,bone_name)
                Format: ws or ws,custom_bone_name
                Creates Copy Rotation constraint. Optional second parameter sets owner space to custom bone.
    - space_l: Location constraint space (space_l=ws for world space Copy Location constraint)
    - as_ik_up: Convert rotation track to directional location IK (e.g., as_ik_up=base_bone,Z,1.0)
                Format: bone_base,axis,distance
                Creates constraints: Copy Location from base + Transformation (Add) from imported offset
                If space_r is also specified with custom bone, applies to Transformation constraint too.
    
    Only one rotation track and one location track can map to the same target bone.
    
    Examples:
    @track Root : segments=qd|vd ; type=ROOT
    Root : torso_root ; offset_r=90,0,0,xyz ; map_r=y,x,z
    
    @track LArm : segments=q|v|q ; type=ARM ; bits=14
    LArm_0 : shoulder.L ; space_r=ws,torso_root
    LArm_1 : hand_ik.L ; space_l=ws
    LArm_2 : upper_arm_ik_target.L ; as_ik_up=upper_arm_ik.L,x,1
    
    Args:
        filepath: Path to the .txt mapping file
        
    Returns:
        TrackMappingData object containing all mapping information
    """
    mapping_data = TrackMappingData()
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                # Try to parse as track metadata first
                metadata = parse_track_metadata(line)
                if metadata:
                    track_name = metadata['name']
                    mapping_data.add_track_metadata(track_name, metadata)
                    continue
                
                # Otherwise parse as bone mapping
                result = parse_mapping_line(line, line_num)
                if result:
                    source_name, bone_mapping_dict = result  # source_name is the Fox bone name
                    blender_bone_name = bone_mapping_dict['name']
                    
                    # Add to mapping data object
                    mapping_data.add_bone_mapping(source_name, blender_bone_name, bone_mapping_dict)
        
        Debug.log(f"Loaded {len(mapping_data)} bone mapping(s) and {len(mapping_data.track_metadata)} track metadata entries from {filepath}")
        
        # Validate track mappings
        validate_track_mappings(mapping_data.fox_to_blender)
        
        return mapping_data
        
    except (OSError, ValueError) as e:
        Debug.log(f"Error parsing track mapping file: {e}")
        return TrackMappingData()  # Return empty mapping data on error

