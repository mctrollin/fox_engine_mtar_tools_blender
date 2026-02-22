"""
Shared utilities for parsing and working with animation metadata.

This module contains helper functions used throughout the importer and
exporter for parsing @track metadata strings stored either in mapping files
or on Blender action properties.
"""
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
import copy

import bpy

from ..py_fox.fox_gani_types import SegmentType, TrackHeader, TrackUnitFlags, TrackUnit, Gani2TrackData
from ..py_fox import fox_gani_constants as gani_const
from ..py_fox import fox_mtar_constants as mtar_const
from ..py_fox.fox_frig_types import RigUnitType
from ..py_fox.fox_misc_types import StrCode32
from ..py_foxwrap.foxwrap_misc import TrackUnitWrapper
from ..py_utilities.utilities_logging import Debug
from ..py_utilities.utilities_blender_animation import action_has_fcurves, iter_action_fcurves, build_data_path_for_bone
from ..py_utilities.utilities_hashing import unhash_rig_type


# Action property key constants -------------------------------------------------------------
# These strings are used as Blender action custom-property keys throughout the
# importer/exporter. The field names are derived from Fox Engine binary templates.
# (Constants imported from py_fox layer).

TRACK_PROP_PREFIX = "track_"  # used by make_/parse_track_property_key
EVENT_PROP_PREFIX = "event_"  # used by make_/parse_event_property_key


# Custom Property Key Utilities #############################################################

def make_track_property_key(track_idx: int, track_name: str) -> str:
    """Create a custom property key for track metadata.
    
    Format: track_<padded_idx>_<track_name>
    Uses zero-padding to ensure alphabetical sort matches numeric order.
    
    Args:
        track_idx: Index of the track (0-based)
        track_name: Name of the track
        
    Returns:
        Property key string (e.g., "track_000_SKL_000_ROOT")
    """
    return f"{TRACK_PROP_PREFIX}{track_idx:03d}_{track_name}"


def parse_track_property_key(key: str) -> Optional[Tuple[int, str]]:
    """Parse a track metadata property key.
    
    Format: track_<padded_idx>_<track_name>
    
    Args:
        key: Property key string
        
    Returns:
        Tuple of (track_idx, track_name) if valid, None otherwise
    """
    if not key.startswith(TRACK_PROP_PREFIX):
        return None
    
    parts = key.split('_', 2)
    if len(parts) == 3 and parts[1].isdigit():
        track_idx = int(parts[1])
        track_name = parts[2]
        return (track_idx, track_name)
    
    return None


def make_event_property_key(event_idx: int, category_name: str) -> str:
    """Create a custom property key for motion event metadata.
    
    Format: event_<padded_idx>_<category>
    Uses zero-padding to ensure alphabetical sort matches numeric order.
    
    Args:
        event_idx: Index of the event (0-based)
        category_name: Category name (e.g., "ag", "sd", "fx")
        
    Returns:
        Property key string (e.g., "event_000_ag")
    """
    return f"{EVENT_PROP_PREFIX}{event_idx:03d}_{category_name}"


def parse_event_property_key(key: str) -> Optional[Tuple[int, str]]:
    """Parse a motion event metadata property key.
    
    Format: event_<padded_idx>_<category>
    
    Args:
        key: Property key string
        
    Returns:
        Tuple of (event_idx, category_name) if valid, None otherwise
    """
    if not key.startswith(EVENT_PROP_PREFIX):
        return None
    
    parts = key.split('_', 2)
    if len(parts) == 3 and parts[1].isdigit():
        event_idx = int(parts[1])
        category_name = parts[2]
        return (event_idx, category_name)
    
    return None


def iter_track_properties(action: bpy.types.Action) -> List[Tuple[int, str, str]]:
    """Iterate through all track metadata properties on an action.
    
    Args:
        action: Blender action to read properties from
        
    Returns:
        List of tuples (track_idx, track_name, property_value) sorted by track_idx
    """
    results = []
    for key in action.keys():
        parsed = parse_track_property_key(key)
        if parsed:
            track_idx, track_name = parsed
            results.append((track_idx, track_name, action[key]))
    
    # Sort by track index
    results.sort(key=lambda x: x[0])
    return results


def iter_event_properties(action: bpy.types.Action) -> List[Tuple[int, str, str]]:
    """Iterate through all motion event properties on an action.
    
    Args:
        action: Blender action to read properties from
        
    Returns:
        List of tuples (event_idx, category_name, property_value) sorted by event_idx
    """
    results = []
    for key in action.keys():
        parsed = parse_event_property_key(key)
        if parsed:
            event_idx, category_name = parsed
            results.append((event_idx, category_name, action[key]))
    
    # Sort by event index
    results.sort(key=lambda x: x[0])
    return results


# Track Header Properties #############################################################

def store_track_header_properties_on_action(action: bpy.types.Action, track_header: TrackHeader) -> None:
    """Store TrackHeader fields as custom properties on an action.
    
    Note: frame_count is NOT stored as a custom property - it's stored in action.frame_end
    via the manual frame range (set by configure_action).
    
    Args:
        action: The Blender action to store properties on
        track_header: TrackHeader object containing Id, UnknownA, UnknownB, FrameCount, FrameRate
    """
    action[gani_const.TRKH_ID] = int(track_header.t_id)
    action.id_properties_ui(gani_const.TRKH_ID).update(
        description="Track header Id field"
    )
    
    action[gani_const.TRKH_UNKNOWN_A] = int(track_header.unknown_a)
    action.id_properties_ui(gani_const.TRKH_UNKNOWN_A).update(
        description="Track header UnknownA field"
    )
    
    action[gani_const.TRKH_UNKNOWN_B] = int(track_header.unknown_b)
    action.id_properties_ui(gani_const.TRKH_UNKNOWN_B).update(
        description="Track header UnknownB field"
    )
    
    action[gani_const.TRKH_FRAME_RATE] = int(track_header.frame_rate)
    action.id_properties_ui(gani_const.TRKH_FRAME_RATE).update(
        description="Track header FrameRate field"
    )


def read_track_header_properties_from_action(action: Optional[bpy.types.Action]) -> Dict[str, int]:
    """Read TrackHeader fields from action custom properties.
    
    Note: frame_count is read from action.frame_end (manual frame range) instead of 
    a custom property. This assumes the action has use_frame_range=True and frame_end
    is set to the original MTAR frame count.
    
    Args:
        action: The Blender action to read properties from (can be None)
        
    Returns:
        Dictionary with Id, UnknownA, UnknownB, FrameCount, FrameRate
    """
    result = {
        gani_const.TRKH_ID: 0,
        gani_const.TRKH_UNKNOWN_A: 0,
        gani_const.TRKH_UNKNOWN_B: 0,
        gani_const.TRKH_FRAME_COUNT: 0,
        gani_const.TRKH_FRAME_RATE: 60
    }
    
    if action:
        if gani_const.TRKH_ID in action.keys():
            result[gani_const.TRKH_ID] = int(action[gani_const.TRKH_ID])
        if gani_const.TRKH_UNKNOWN_A in action.keys():
            result[gani_const.TRKH_UNKNOWN_A] = int(action[gani_const.TRKH_UNKNOWN_A])
        if gani_const.TRKH_UNKNOWN_B in action.keys():
            result[gani_const.TRKH_UNKNOWN_B] = int(action[gani_const.TRKH_UNKNOWN_B])
        
        # Read FrameCount from manual frame range instead of custom property
        # Use frame_end - frame_start to get the duration, then take absolute value
        # to handle negative time ranges (e.g., layout track at -100 to -50)
        if action.use_frame_range:
            frame_duration = int(action.frame_end - action.frame_start)
            result[gani_const.TRKH_FRAME_COUNT] = abs(frame_duration)
        elif gani_const.TRKH_FRAME_COUNT in action.keys():
            # Fallback: read from custom property if present (for backward compatibility)
            result[gani_const.TRKH_FRAME_COUNT] = abs(int(action[gani_const.TRKH_FRAME_COUNT]))
        
        if gani_const.TRKH_FRAME_RATE in action.keys():
            result[gani_const.TRKH_FRAME_RATE] = int(action[gani_const.TRKH_FRAME_RATE])
    
    return result


# Track Metadata Parsing Helpers #############################################################

def _parse_segment_codes(segment_codes_str: str) -> List[SegmentType]:
    """Parse comma-separated segment codes into SegmentType list.
    
    Recognized codes: q (QUAT), qd (QUAT_DIFF), v (VECTOR3), vd (VECTOR_DIFF), f (FLOAT)
    
    Args:
        segment_codes_str: Comma-separated segment codes (e.g., 'q,v,q')
        
    Returns:
        List of SegmentType enums
    """
    segment_types = []
    segment_codes = [code.strip() for code in segment_codes_str.split(',') if code.strip()]
    
    for code in segment_codes:
        if code == 'q':
            segment_types.append(SegmentType.QUAT)
        elif code == 'qd':
            segment_types.append(SegmentType.QUAT_DIFF)
        elif code == 'v':
            segment_types.append(SegmentType.VECTOR3)
        elif code == 'vd':
            segment_types.append(SegmentType.VECTOR_DIFF)
        elif code == 'f':
            segment_types.append(SegmentType.FLOAT)
        else:
            Debug.log_warning(f"Unknown segment code '{code}', ignoring")
    
    return segment_types


def _parse_component_bits(bits_str: str) -> List[int]:
    """Parse comma-separated component bit sizes into int list.
    
    Args:
        bits_str: Comma-separated bit sizes (e.g., '14,14,14') or single value ('14')
        
    Returns:
        List of component bit sizes
    """
    component_bit_sizes = []
    
    if ',' in bits_str:
        # Multi-segment: parse each value
        for bs_str in bits_str.split(','):
            bs_str = bs_str.strip()
            if bs_str:
                try:
                    component_bit_sizes.append(int(bs_str))
                except ValueError:
                    component_bit_sizes.append(0)
    else:
        # Single value: parse once
        try:
            component_bit_sizes.append(int(bits_str))
        except ValueError:
            pass
    
    return component_bit_sizes


def _parse_flags(flags_str: str) -> Tuple[List[str], Optional[int]]:
    """Parse comma-separated flags into flag list and unit_flags integer.
    
    Args:
        flags_str: Comma-separated flag names (e.g., 'IS_STATIC,UNKNOWN_0') or 'NONE'
        
    Returns:
        Tuple of (flags_list, unit_flags_int)
    """
    if flags_str == 'NONE':
        return ([], 0)
    
    flags_list = [f.strip() for f in flags_str.split(',') if f.strip()]
    
    # Convert to unit_flags integer
    flag_enums = []
    for name in flags_list:
        try:
            flag_enums.append(TrackUnitFlags[name])
        except KeyError:
            Debug.log_warning(f"Unknown flag name '{name}'")
    
    unit_flags = TrackUnitFlags.track_unit_flags_to_int(flag_enums) if flag_enums else None
    
    return (flags_list, unit_flags)


def get_segments_for_track_type(track_type: str, count: Optional[int] = None) -> List[dict]:
    """Get the standard segment structure for a given track type.

    Args:
        track_type: The rig unit type (ROOT, ARM, ORIENTATION, etc.)
        count: Number of segments for MULTI_LOCAL_ORIENTATION type

    Returns:
        List of segment dictionaries with 'type' and 'data_type' keys
    """
    type_segments = {
        'ROOT': [
            {'type': 'rotation', 'data_type': 'quatdiff'},
            {'type': 'position', 'data_type': 'vec3diff'}
        ],
        'ORIENTATION': [
            {'type': 'rotation', 'data_type': 'quat'}
        ],
        'TWO_BONE': [
            {'type': 'position', 'data_type': 'vec3'},
            {'type': 'rotation', 'data_type': 'quat'}
        ],
        'LOCAL_ORIENTATION': [
            {'type': 'rotation', 'data_type': 'quat'}
        ],
        'LOCAL_TRANSFORM': [
            {'type': 'rotation', 'data_type': 'quat'},
            {'type': 'position', 'data_type': 'vec3'}
        ],
        'TRANSFORM': [
            {'type': 'rotation', 'data_type': 'quat'},
            {'type': 'position', 'data_type': 'vec3'}
        ],
        'ARM': [
            {'type': 'rotation', 'data_type': 'quat'},
            {'type': 'position', 'data_type': 'vec3'},
            {'type': 'rotation', 'data_type': 'quat'}
        ]
    }

    if track_type == 'MULTI_LOCAL_ORIENTATION':
        if count is None:
            raise ValueError("MULTI_LOCAL_ORIENTATION requires 'count' parameter")
        return [{'type': 'rotation', 'data_type': 'quat'}] * count

    return type_segments.get(track_type, [])


def parse_track_metadata_generic(metadata_str: str) -> Optional[dict]:
    """Unified parser for @track metadata in all formats.
    
    Handles three format variants:
    1. Mapping file: @track <name> : type=<type> ; [count=<n>] ; [flags=<flags>] ; [bits=<bits>]
    2. Action: @track <name> : type=<type> ; [flags=<flags>] ; [bits=<bits>]
    3. Layout: @track <name> : segments=<codes> ; [flags=<flags>] ; [hash=<hash>] ; [type=<type>] ; [bits=<bits>] ; [count=<n>]
    
    Auto-detects which parameters are present:
    - Explicit segments= takes priority
    - Falls back to type-derived segments when segments= not present
    - Handles MULTI_LOCAL_ORIENTATION count parameter
    
    Args:
        metadata_str: Full @track directive string
        
    Returns:
        Dictionary with standardized keys:
        - track_name: str
        - segment_types: List[SegmentType]
        - component_bit_sizes: List[int] (optional)
        - flags_list: List[str] (optional)
        - unit_flags: int (optional)
        - name_hash: int (optional)
        - rig_unit_type: str (optional)
        - count: int (optional)
    """
    if not isinstance(metadata_str, str):
        return None
    
    metadata_str = metadata_str.strip()
    if not metadata_str.startswith('@track'):
        return None
    
    # Split by colon to separate track name from parameters
    rest = metadata_str[len('@track'):].strip()
    if ':' not in rest:
        return None
    
    parts = rest.split(':', 1)
    track_name = parts[0].strip()
    if not track_name:
        return None
    
    params_str = parts[1].strip()
    
    # Initialize result dict with all possible fields
    result = {
        'track_name': track_name,
        'segment_types': [],
        'component_bit_sizes': None,
        'flags_list': None,
        'unit_flags': None,
        'name_hash': None,
        'rig_unit_type': None,
        'count': None
    }
    
    # Parse all parameters
    segments_str = None
    type_str = None
    bits_str = None
    flags_str = None
    hash_str = None
    count_str = None
    
    for param in params_str.split(';'):
        param = param.strip()
        if not param or '=' not in param:
            continue
        
        key, value = param.split('=', 1)
        key = key.strip()
        value = value.strip()
        
        if key == 'segments':
            segments_str = value
        elif key == 'type':
            type_str = value
        elif key == 'bits':
            bits_str = value
        elif key == 'flags':
            flags_str = value
        elif key == 'hash':
            hash_str = value
        elif key == 'count':
            count_str = value
    
    # Parse segments (explicit or type-derived)
    if segments_str:
        # Explicit segments= parameter (layout format)
        result['segment_types'] = _parse_segment_codes(segments_str)
    elif type_str:
        # Derive segments from type (mapping file / action format)
        try:
            count_value = int(count_str) if count_str else None
            segment_defs = get_segments_for_track_type(type_str, count_value)
            
            # Convert segment definitions to SegmentType enums
            for seg_def in segment_defs:
                seg_data_type = seg_def.get('data_type', '')
                if seg_data_type == 'quat':
                    result['segment_types'].append(SegmentType.QUAT)
                elif seg_data_type == 'quatdiff':
                    result['segment_types'].append(SegmentType.QUAT_DIFF)
                elif seg_data_type == 'vec3':
                    result['segment_types'].append(SegmentType.VECTOR3)
                elif seg_data_type == 'vec3diff':
                    result['segment_types'].append(SegmentType.VECTOR_DIFF)
                elif seg_data_type == 'float':
                    result['segment_types'].append(SegmentType.FLOAT)
        except Exception as e:
            Debug.log_warning(f"Could not derive segments from type '{type_str}': {e}")
            return None
    
    # Note: segment_types may be empty for action metadata that only contains overrides (bits, flags)
    # This is valid - the caller can decide if segments are required for their use case
    
    # Parse component bits
    if bits_str:
        result['component_bit_sizes'] = _parse_component_bits(bits_str)
    
    # Parse flags
    if flags_str:
        flags_list, unit_flags = _parse_flags(flags_str)
        result['flags_list'] = flags_list if flags_list else None
        result['unit_flags'] = unit_flags
    
    # Parse hash
    if hash_str:
        try:
            result['name_hash'] = int(hash_str)
        except ValueError:
            Debug.log_warning(f"Invalid hash value '{hash_str}'")
    
    # Store type string
    if type_str:
        result['rig_unit_type'] = type_str
    
    # Store count
    if count_str:
        try:
            result['count'] = int(count_str)
        except ValueError:
            Debug.log_warning(f"Invalid count value '{count_str}'")
    
    return result


def parse_track_metadata(line: str) -> Optional[dict]:
    """Parse track metadata from @track directive (mapping file format).

    Format: @track <name> : type=<rig_type> ; [count=<n>] ; [flags=<flags>] ; [bits=<bit_sizes>]
    
    Note: 'bits' is legacy and represents a default compression level. In actual MTAR files,
    each segment has its own component_bit_size stored separately.
    
    Uses the unified parse_track_metadata_generic() parser internally.

    Returns:
        Dictionary with track metadata or None if not a track directive
    """
    # Use unified parser
    parsed = parse_track_metadata_generic(line)
    if not parsed:
        return None
    
    # Mapping files MUST define track structure (require segments)
    if not parsed['segment_types']:
        return None
    
    # Convert to expected return format
    metadata = {
        'name': parsed['track_name'],
        'segments': [],
        'flags': parsed['flags_list'] if parsed['flags_list'] else [],
        'type': parsed['rig_unit_type'],
        'bits': parsed['component_bit_sizes'][0] if parsed['component_bit_sizes'] else 16,
        'count': parsed['count']
    }
    
    # Convert SegmentType enums to segment definition dicts
    for seg_type in parsed['segment_types']:
        if seg_type == SegmentType.QUAT:
            metadata['segments'].append({'type': 'rotation', 'data_type': 'quat'})
        elif seg_type == SegmentType.QUAT_DIFF:
            metadata['segments'].append({'type': 'rotation', 'data_type': 'quatdiff'})
        elif seg_type == SegmentType.VECTOR3:
            metadata['segments'].append({'type': 'position', 'data_type': 'vec3'})
        elif seg_type == SegmentType.VECTOR_DIFF:
            metadata['segments'].append({'type': 'position', 'data_type': 'vec3diff'})
        elif seg_type == SegmentType.FLOAT:
            metadata['segments'].append({'type': 'float', 'data_type': 'float'})
    
    return metadata


def parse_track_type_from_metadata(metadata_str: str) -> Optional[RigUnitType]:
    """Extract only the RigUnitType from @track metadata string.
    
    Lightweight parser that extracts only 'type=VALUE' attribute without
    parsing segments, flags, or other metadata. Optimized for performance.
    
    Args:
        metadata_str: Metadata string in format '@track BoneName : type=ROOT ; bits=14'
        
    Returns:
        RigUnitType enum if type attribute found and valid, None otherwise
    """
    if not metadata_str or not isinstance(metadata_str, str):
        return None
    
    # Split by ':' to separate bone name from attributes
    parts = metadata_str.split(':', 1)
    if len(parts) < 2:
        return None
    
    # Parse attributes section (type=ROOT ; bits=14)
    attributes_part = parts[1]
    
    # Split by ';' to get individual attributes
    for attr in attributes_part.split(';'):
        attr = attr.strip()
        if not attr or '=' not in attr:
            continue
        
        # Split by '=' to get key=value
        attr_key, attr_value = attr.split('=', 1)
        attr_key = attr_key.strip()
        attr_value = attr_value.strip()
        
        if attr_key == 'type':
            # Parse RigUnitType from string
            return RigUnitType.parse_from_string(attr_value)
    
    return None


def extract_fox_bone_to_rig_unit_type_mapping(layout_action: bpy.types.Action, 
                                          cache: Optional[Dict[str, Dict[str, RigUnitType]]] = None
                                          ) -> Dict[str, RigUnitType]:
    """Extract fox bone name to RigUnitType mapping from layout action metadata.
    
    Parses track metadata stored in layout action custom properties (format: '@track FoxBoneName : type=ROOT ; bits=14')
    to build a mapping of box bone names to their rig unit types. Layout action is the authoritative source for
    rig unit types in the MTAR structure.
    
    Args:
        layout_action: Layout track action containing track structure metadata (required)
        cache: Optional cache dict to avoid re-parsing. Keyed by action name. Managed at operator level.
        
    Returns:
        Dictionary mapping fox bone name to RigUnitType (excludes bones with unparseable types)
    """
    # Check cache first if provided
    if cache is not None and layout_action.name in cache:
        return cache[layout_action.name]
    
    bone_to_type: Dict[str, RigUnitType] = {}
    
    if not layout_action or not layout_action.keys():
        # Store empty result in cache if provided
        if cache is not None:
            cache[layout_action.name] = bone_to_type
        return bone_to_type
    
    # Parse all track metadata properties (auto-filters by 'track_' prefix)
    for _, fox_track_name, metadata_str in iter_track_properties(layout_action):
        try:
            if not isinstance(metadata_str, str) or not metadata_str.startswith('@track'):
                continue
            
            # Parse rig unit type using lightweight parser
            rig_unit_type = parse_track_type_from_metadata(metadata_str)
            
            if rig_unit_type is None:
                Debug.log_warning(f"Could not parse rig unit type from metadata: {metadata_str}")
                continue  # Skip unparseable types - do not add to dict
            
            # Store mapping (only if type was successfully parsed)
            bone_to_type[fox_track_name] = rig_unit_type
            
        except Exception as e:
            Debug.log_warning(f"Failed to parse track metadata for '{fox_track_name}': {e}")
            continue
    
    # Store result in cache if provided
    if cache is not None:
        cache[layout_action.name] = bone_to_type
    
    return bone_to_type


def parse_action_track_metadata(metadata_value: str) -> Optional[dict]:
    """Parse @track metadata stored on actions (GANI file properties).

    Format: @track <name> : type=<type> ; [flags=<flags>] ; [bits=<bits>]
    
    Uses the unified parse_track_metadata_generic() parser internally.
    
    Returns:
        Dictionary with 'track_name', 'component_bit_sizes', 'flags', 'type' keys
    """
    # Use unified parser
    parsed = parse_track_metadata_generic(metadata_value)
    if not parsed:
        return None
    
    # Convert to expected return format
    return {
        'track_name': parsed['track_name'],
        'component_bit_sizes': parsed['component_bit_sizes'] if parsed['component_bit_sizes'] else [],
        'flags': parsed['flags_list'] if parsed['flags_list'] else [],
        'type': parsed['rig_unit_type'],
    }


def parse_offset_r_parameter(param_value: str) -> Optional[dict]:
    try:
        offset_parts = param_value.split(',')
        if len(offset_parts) >= 3:
            euler_x = float(offset_parts[0].strip())
            euler_y = float(offset_parts[1].strip())
            euler_z = float(offset_parts[2].strip())
            order = offset_parts[3].strip().upper() if len(offset_parts) >= 4 else 'XYZ'
            valid_orders = ['XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX']
            if order not in valid_orders:
                order = 'XYZ'
            return {
                'euler': [euler_x, euler_y, euler_z],
                'order': order
            }
    except ValueError:
        return None
    return None


def parse_map_r_parameter(param_value: str) -> Optional[List[dict]]:
    try:
        map_parts = param_value.split(',')
        if len(map_parts) == 3:
            axis_mapping = []
            for axis_str in map_parts:
                axis_str = axis_str.strip().lower()
                negate = False
                if axis_str.startswith('-'):
                    negate = True
                    axis_str = axis_str[1:]
                if axis_str not in ['x', 'y', 'z']:
                    raise ValueError
                axis_mapping.append({'axis': axis_str, 'negate': negate})
            return axis_mapping
    except ValueError:
        return None
    return None


def parse_space_parameter(param_value: str) -> Optional[dict]:
    """
    Parse the space parameter from mapping files.

    Recognized formats (case-insensitive):
      - "world" -> {'space': 'WORLD'}
      - "custom,<bone>" -> {'space': 'CUSTOM', 'custom_bone': '<bone>'}

    Returns None for invalid values. Emits warnings for invalid usages (e.g., world with a bone, or custom without a bone).
    """
    parts = param_value.split(',', 1)
    space_value = parts[0].strip().lower()

    # World-space: ignore any trailing bone and warn the user
    if space_value == 'world':
        if len(parts) > 1 and parts[1].strip():
            Debug.log_warning("Warning: 'space=world,<bone>' specified a custom bone which is invalid for 'world'; the custom bone will be ignored. Use 'space=custom,<bone>' to set a custom owner bone.")
        return {'space': 'WORLD'}

    # Custom-space: requires a bone name
    if space_value == 'custom':
        if len(parts) > 1:
            custom_bone = parts[1].strip()
            if custom_bone:
                return {'space': 'CUSTOM', 'custom_bone': custom_bone}
        Debug.log_warning("Warning: 'space=custom' requires a bone name (e.g. 'space=custom,torso_root'); parameter will be ignored.")
        return None

    # Unknown token
    Debug.log_warning(f"Warning: 'space={space_value}' is unspecified. Use either 'space=world' or 'space=custom,<bone>'.")
    return None


def parse_as_ik_up_parameter(param_value: str) -> Optional[dict]:
    """Parse as_ik_up parameter from mapping file.
    
    Format: "bone_base,axis"
    
    Args:
        param_value: "bone_base,axis" (e.g., "Root,Y")
        
    Returns:
        Dict with 'bone_base' and 'axis' keys, or None if invalid
    """
    try:
        parts = param_value.split(',')
        if len(parts) != 2:
            return None

        bone_base = parts[0].strip()
        axis = parts[1].strip().upper()
        
        if axis not in ['X', 'Y', 'Z']:
            return None
        if not bone_base:
            return None
        return {'bone_base': bone_base, 'axis': axis}
    except ValueError:
        return None


# Track MetaData wrapper #############################################################

@dataclass
class TrackMetaData:
    """Container for metadata for a single track.

    Holds layout-level defaults as well as per-animation overrides.
    This object is intentionally decoupled from Blender types and can be
    constructed from parsing strings (layout/action properties) or converted
    to binary-writing-friendly fields by the writer code (later).
    """
    track_name: str = ''
    # Optional numeric hash for the track name (StrCode32 integer value)
    name_hash: Optional[int] = None
    # Segment definitions: list of dicts from get_segments_for_track_type()
    segment_types: List[SegmentType] = field(default_factory=list)
    # Component bit sizes (per-segment), if provided
    component_bit_sizes: Optional[List[int]] = None
    # unit_flags integer value (optional)
    unit_flags: Optional[int] = None
    flags_list: Optional[List[str]] = None
    # Rig unit type string e.g. 'ARM', 'ROOT', 'ORIENTATION'
    rig_unit_type: Optional[str] = None
    # Additional optional parameters parsed from mapping or action
    rotation_offset: Optional[Dict[str, Any]] = None
    rotation_axis_map: Optional[List[Dict[str, Any]]] = None
    space_r: Optional[Dict[str, Any]] = None
    as_ik_up: Optional[Dict[str, Any]] = None

    @staticmethod
    def from_action(layout_action: bpy.types.Action, fox_track_name: str) -> Optional['TrackMetaData']:
        """Retrieve track structure metadata from layout action custom properties.
        
        The layout action stores the track structure (segments, track names) that is shared
        across all animations in the MTAR file.
        
        Property key format: track_<padded_idx>_<fox_track_name>
        Property value format: @track <name> : segments=<segments> ; flags=<flags> ; hash=<hash> ; type=<type> ; bits=<bits> ; count=<n>

        This function now uses the unified parse_track_metadata_generic() parser.
        
        Args:
            layout_action: The layout action containing track structure metadata
            fox_track_name: Fox track name to get metadata for (e.g., "LArm", "Root", "SKL_002_NECK1")
            
        Returns:
            TrackMetaData object if found, None otherwise
        """
        # Search for custom property matching the track name
        metadata_str = None
        property_key = None
        
        for key in layout_action.keys():
            parsed = parse_track_property_key(key)
            if parsed:
                _, track_name = parsed
                if track_name == fox_track_name:
                    property_key = key
                    metadata_str = layout_action[key]
                    break
        
        if metadata_str is None:
            return None
        
        # Validate format
        if not isinstance(metadata_str, str) or not metadata_str.startswith('@track'):
            Debug.log_warning(f"      Warning: Custom property '{property_key}' is not in @track format")
            return None
        
        # Use unified parser
        parsed = parse_track_metadata_generic(metadata_str)
        if not parsed:
            Debug.log_warning(f"      Warning: Failed to parse metadata for track '{fox_track_name}'")
            return None
        
        # Parse RigUnitType enum if type was provided
        rig_unit_type = None
        if parsed['rig_unit_type']:
            rig_unit_type = RigUnitType.parse_from_string(parsed['rig_unit_type'])
            if rig_unit_type is None:
                Debug.log_warning(f"      Warning: Unknown rig unit type '{parsed['rig_unit_type']}' in track '{fox_track_name}'")
        
        # Create TrackMetaData from parsed values
        name_hash = parsed['name_hash']
        if name_hash is None:
            # Generate hash from track name if not provided
            name_hash = StrCode32.from_string(parsed['track_name']).to_int()
        
        return TrackMetaData(
            track_name=parsed['track_name'],
            name_hash=name_hash,
            segment_types=parsed['segment_types'],
            component_bit_sizes=parsed['component_bit_sizes'],
            unit_flags=parsed['unit_flags'],
            flags_list=parsed['flags_list'],
            rig_unit_type=rig_unit_type
        )

    @staticmethod
    def from_fcurves(bone_name: str, action: bpy.types.Action) -> Optional['TrackMetaData']:
        """Build minimal TrackMetaData by analyzing fcurves when no metadata is available.
        
        This is a helper function for the fallback export path when no layout metadata exists.
        It determines segment types by checking which fcurve data paths exist for the bone.
        
        Args:
            bone_name: Name of the bone to analyze
            action: Action containing fcurves to analyze
            
        Returns:
            TrackMetaData with segment_types inferred from fcurves, or None if no fcurves found
        """
        if not action or not action_has_fcurves(action):
            return None
        
        # Check which fcurve types exist for this bone
        rotation_quat_path = build_data_path_for_bone(bone_name, 'rotation_quaternion')
        rotation_euler_path = build_data_path_for_bone(bone_name, 'rotation_euler')
        location_path = build_data_path_for_bone(bone_name, 'location')
        
        has_rotation = any(
            fc.data_path in [rotation_quat_path, rotation_euler_path]
            for fc in iter_action_fcurves(action)
        )
        has_location = any(
            fc.data_path == location_path
            for fc in iter_action_fcurves(action)
        )
        
        # Build segment types list
        segment_types = []
        if has_rotation:
            segment_types.append(SegmentType.QUAT)
        if has_location:
            segment_types.append(SegmentType.VECTOR3)
        
        # If no segments found, return None
        if not segment_types:
            return None
        
        # Create minimal metadata
        return TrackMetaData(
            track_name=bone_name,
            segment_types=segment_types,
            unit_flags=0,  # No special flags
            name_hash=StrCode32.from_string(bone_name).to_int(),
            component_bit_sizes=None,  # Use defaults
            rig_unit_type=None
        )

    @staticmethod
    def from_layout_track_units(track_units: List[TrackUnit], track_name_prefix: str = "Track", gani_tracks: Optional[List[TrackUnitWrapper]] = None) -> List['TrackMetaData']:
        """Convert layout track units to TrackMetaData objects.
        
        Args:
            track_units: List of TrackUnit objects from layout track
            track_name_prefix: Prefix for generating track names (default: "Track")
            gani_tracks: Optional list of GaniTracks with rig_unit_type populated from FRIG (for preserving rig type info)
            
        Returns:
            List of TrackMetaData objects
        """
        
        track_metadata_list: List['TrackMetaData'] = []
        
        for track_idx, track_unit in enumerate(track_units):
            # Resolve track name from hash
            track_name: str = f"{track_name_prefix}{track_idx}"
            name_hash: int = 0
            if track_unit.name:
                name_hash = track_unit.name.to_int() if hasattr(track_unit.name, 'to_int') else int(track_unit.name)
                resolved_name: Optional[str] = unhash_rig_type(name_hash)
                if resolved_name:
                    track_name = resolved_name
                else:
                    # If unhashing fails, use string representation of hash (matches bone creation)
                    track_name = str(track_unit.name)
            
            # Build segment types list
            segment_types: List[SegmentType] = []
            component_bit_sizes: List[int] = []
            for track_data in track_unit.segments_data:
                segment_types.append(track_data.td_type)
                component_bit_sizes.append(track_data.component_bit_size)
            
            # Get rig_unit_type from corresponding GaniTrack if available (populated from FRIG)
            rig_unit_type: Optional[RigUnitType] = None
            if gani_tracks and track_idx < len(gani_tracks):
                rig_unit_type = gani_tracks[track_idx].rig_unit_type
            
            # Create TrackMetaData
            track_meta: TrackMetaData = TrackMetaData(
                track_name=track_name,
                name_hash=name_hash if name_hash != 0 else None,
                segment_types=segment_types,
                component_bit_sizes=component_bit_sizes,
                unit_flags=track_unit.unit_flags,
                flags_list=None,  # Will be derived from unit_flags
                rig_unit_type=rig_unit_type  # Preserved from FRIG if available
            )
            
            track_metadata_list.append(track_meta)
        
        return track_metadata_list

    @staticmethod
    def from_gani_tracks(gani_tracks: List[TrackUnitWrapper], segment_headers: List[Gani2TrackData]) -> List['TrackMetaData']:
        """Convert GANI tracks and segment headers to TrackMetaData objects.
        
        Args:
            gani_tracks: List of GaniTrack objects containing animation data
            segment_headers: List of segment header objects with component_bit_size
            
        Returns:
            List of TrackMetaData objects
        """
        track_metadata_list: List['TrackMetaData'] = []
        segment_idx_abs: int = 0
        
        for track_idx, gani_track in enumerate(gani_tracks):
            track_name: str = gani_track.name
            
            # Extract unit flags
            unit_flags: Optional[int] = None
            if gani_track.unit_flags:
                unit_flags = TrackUnitFlags.track_unit_flags_to_int(gani_track.unit_flags)
            
            # Collect component bit sizes and segment types
            segment_count: int = len(gani_track.segments_track_data)
            bit_sizes: List[int] = []
            segment_types: List[SegmentType] = []
            
            for seg_idx in range(segment_count):
                abs_idx: int = segment_idx_abs + seg_idx
                if abs_idx < len(segment_headers):
                    bit_sizes.append(segment_headers[abs_idx].component_bit_size)
                else:
                    bit_sizes.append(0)
                
                # Get segment type from the track data blob
                if seg_idx < len(gani_track.segments_track_data):
                    segment_types.append(gani_track.segments_track_data[seg_idx].data_blob.type)
            
            # Create TrackMetaData
            track_meta: TrackMetaData = TrackMetaData(
                track_name=track_name,
                name_hash=None, # Not stored in GANI tracks but layout track
                segment_types=segment_types,
                component_bit_sizes=bit_sizes,
                unit_flags=unit_flags,
                flags_list=None, # Not stored in GANI tracks but layout track
                rig_unit_type=None # Not stored in GANI tracks but layout track
            )
            
            track_metadata_list.append(track_meta)
            segment_idx_abs += segment_count
        
        return track_metadata_list

    @staticmethod
    def extract_space_bone(space_param) -> Optional[str]:
        """Extract the space bone name from a space parameter.
        
        Space parameters can be:
        - None (use default local/world behavior)
        - Dict format: {'space': 'WORLD'} or {'space': 'CUSTOM', 'custom_bone': 'bone_name'} (from parse_space_parameter)
        
        Args:
            space_param: Space parameter (dict, typically from bone_params.space_r or space_l)
            
        Returns:
            The custom space bone name if specified, None otherwise
        """
        if space_param:
            if isinstance(space_param, dict):
                return space_param.get('custom_bone')
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Return a serialization-ready dict representing the metadata."""
        return {
            'track_name': self.track_name,
            'name_hash': self.name_hash,
            'segment_types': [s.name for s in self.segment_types],
            'component_bit_sizes': self.component_bit_sizes,
            'unit_flags': self.unit_flags,
            'flags_list': self.flags_list,
            'rig_unit_type': self.rig_unit_type,
            'rotation_offset': self.rotation_offset,
            'rotation_axis_map': self.rotation_axis_map,
            'space_r': self.space_r,
            'as_ik_up': self.as_ik_up,
        }

    def __repr__(self) -> str:
        return f"TrackMetaData(track_name={self.track_name!r}, segments={len(self.segment_types)}, rig_unit_type={self.rig_unit_type!r})"


def merge_track_metadata(layout_meta: TrackMetaData, action_meta: Optional[TrackMetaData]) -> TrackMetaData:
    """Merge action-level TrackMetaData overrides into a layout-level TrackMetaData.

    The function returns a new TrackMetaData instance with action-level fields overriding
    layout-level defaults where present. The layout metadata defines segment_types and
    defaults, while action metadata contains per-animation overrides like component bit
    sizes and flags. This function is non-destructive: it makes a shallow copy of the
    layout metadata and applies overrides.
    """
    if not layout_meta and not action_meta:
        return TrackMetaData()
    if not layout_meta:
        # If no layout metadata, return a copy of action metadata (best-effort)
        return copy.deepcopy(action_meta) if action_meta else TrackMetaData()

    result = copy.deepcopy(layout_meta)
    if not action_meta:
        return result

    # Override track name / hash
    if action_meta.track_name:
        result.track_name = action_meta.track_name
        if action_meta.name_hash is not None:
            result.name_hash = action_meta.name_hash
        else:
            try:
                result.name_hash = StrCode32.from_string(action_meta.track_name).to_int()
            except (ValueError, AttributeError):
                # If conversion fails, leave as-is
                pass

    # Override component bit sizes if provided in action
    if action_meta.component_bit_sizes is not None:
        result.component_bit_sizes = action_meta.component_bit_sizes

    # Override flags: prefer explicit integer if available, otherwise flags list
    # Edit: unit flags are a special case. The action always (!) overrides the layout
    result.unit_flags = action_meta.unit_flags

    # Override rig unit type if provided
    if action_meta.rig_unit_type:
        result.rig_unit_type = action_meta.rig_unit_type

    # Other optional overrides (rotation_offset, etc.)
    if action_meta.rotation_offset is not None:
        result.rotation_offset = action_meta.rotation_offset
    if action_meta.rotation_axis_map is not None:
        result.rotation_axis_map = action_meta.rotation_axis_map
    if action_meta.space_r is not None:
        result.space_r = action_meta.space_r
    if action_meta.as_ik_up is not None:
        result.as_ik_up = action_meta.as_ik_up

    return result

def get_all_track_metadata_from_action(action: bpy.types.Action) -> Dict[str, TrackMetaData]:
    """Parse all track structure metadata from layout track action.
    
    The layout track defines the shared structure for all animations:
    - Track names and order
    - Segment types per track
    - Default unit flags
    
    Args:
        layout_action: The layout track action containing structure metadata
        
    Returns:
        Dictionary mapping fox_track_name -> TrackMetaData
    """
    metadata_dict = {}
    
    # Iterate through track properties using utility function
    for track_idx, fox_track_name, metadata_str in iter_track_properties(action):
        # We already have the metadata_str, but get_track_metadata_from_action does the parsing
        # Call it to parse the metadata string
        metadata = TrackMetaData.from_action(action, fox_track_name)
        if metadata:
            metadata_dict[fox_track_name] = metadata
            Debug.log(f"    Parsed track {track_idx}: {fox_track_name} ({len(metadata.segment_types)} segments)")
    
    Debug.log(f"  Parsed {len(metadata_dict)} track(s) from layout action")
    return metadata_dict
