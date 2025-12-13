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
from ..py_fox.fox_frig_types import RigUnitType
from ..py_fox.fox_misc_types import StrCode32
from ..py_foxwrap.foxwrap_misc import TrackUnitWrapper
from ..py_utilities.logging_utilities import Debug


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
    return f"track_{track_idx:03d}_{track_name}"


def parse_track_property_key(key: str) -> Optional[Tuple[int, str]]:
    """Parse a track metadata property key.
    
    Format: track_<padded_idx>_<track_name>
    
    Args:
        key: Property key string
        
    Returns:
        Tuple of (track_idx, track_name) if valid, None otherwise
    """
    if not key.startswith('track_'):
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
    return f"event_{event_idx:03d}_{category_name}"


def parse_event_property_key(key: str) -> Optional[Tuple[int, str]]:
    """Parse a motion event metadata property key.
    
    Format: event_<padded_idx>_<category>
    
    Args:
        key: Property key string
        
    Returns:
        Tuple of (event_idx, category_name) if valid, None otherwise
    """
    if not key.startswith('event_'):
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
        track_header: TrackHeader object containing t_id, unknown_a, unknown_b, frame_count, frame_rate
    """
    action["t_id"] = int(track_header.t_id)
    action.id_properties_ui("t_id").update(
        description="Track header t_id field"
    )
    
    action["unknown_a"] = int(track_header.unknown_a)
    action.id_properties_ui("unknown_a").update(
        description="Track header unknown_a field"
    )
    
    action["unknown_b"] = int(track_header.unknown_b)
    action.id_properties_ui("unknown_b").update(
        description="Track header unknown_b field"
    )
    
    action["frame_rate"] = int(track_header.frame_rate)
    action.id_properties_ui("frame_rate").update(
        description="Track header frame_rate field"
    )


def read_track_header_properties_from_action(action: Optional[bpy.types.Action]) -> Dict[str, int]:
    """Read TrackHeader fields from action custom properties.
    
    Note: frame_count is read from action.frame_end (manual frame range) instead of 
    a custom property. This assumes the action has use_frame_range=True and frame_end
    is set to the original MTAR frame count.
    
    Args:
        action: The Blender action to read properties from (can be None)
        
    Returns:
        Dictionary with t_id, unknown_a, unknown_b, frame_count, frame_rate
    """
    result = {
        't_id': 0,
        'unknown_a': 0,
        'unknown_b': 0,
        'frame_count': 0,
        'frame_rate': 60
    }
    
    if action:
        if "t_id" in action.keys():
            result['t_id'] = int(action["t_id"])
        if "unknown_a" in action.keys():
            result['unknown_a'] = int(action["unknown_a"])
        if "unknown_b" in action.keys():
            result['unknown_b'] = int(action["unknown_b"])
        
        # Read frame_count from manual frame range instead of custom property
        if action.use_frame_range:
            result['frame_count'] = int(action.frame_end)
        elif "frame_count" in action.keys():
            # Fallback: read from custom property if present (for backward compatibility)
            result['frame_count'] = int(action["frame_count"])
        
        if "frame_rate" in action.keys():
            result['frame_rate'] = int(action["frame_rate"])
    
    return result


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


def parse_track_metadata(line: str) -> Optional[dict]:
    """Parse track metadata from @track directive.

    Format: @track <name> : type=<rig_type> ; [count=<n>] ; [flags=<flags>] ; [bits=<bit_sizes>]
    
    Note: 'bits' is legacy and represents a default compression level. In actual MTAR files,
    each segment has its own component_bit_size stored separately.

    Returns:
        Dictionary with track metadata or None if not a track directive
    """
    line = line.strip()
    if not line.startswith('@track'):
        return None

    rest = line[6:].strip()
    if ':' not in rest:
        return None

    colon_parts = rest.split(':', 1)
    track_name = colon_parts[0].strip()
    if not track_name:
        return None

    metadata = {
        'name': track_name,
        'segments': [],
        'flags': [],
        'type': None,
        'bits': 16,
        'count': None
    }

    params_str = colon_parts[1].strip()
    for param in params_str.split(';'):
        param = param.strip()
        if not param or '=' not in param:
            continue
        key, value = param.split('=', 1)
        key = key.strip()
        value = value.strip()

        if key == 'type':
            metadata['type'] = value
        elif key == 'count':
            try:
                metadata['count'] = int(value)
            except ValueError:
                metadata['count'] = None
        elif key == 'flags':
            metadata['flags'] = [f.strip() for f in value.split(',')]
        elif key == 'bits':
            try:
                bits = int(value)
                if bits not in [12, 14, 16, 18, 20, 22, 24]:
                    bits = 16
                metadata['bits'] = bits
            except ValueError:
                metadata['bits'] = 16

    if metadata['type']:
        try:
            metadata['segments'] = get_segments_for_track_type(metadata['type'], metadata['count'])
        except ValueError:
            return None

    if not metadata['segments']:
        return None

    return metadata


def parse_action_track_metadata(metadata_value: str) -> Optional[dict]:
    """Parse @track metadata stored on actions (GANI file properties).

    Supports both scalar & comma-separated `bits=`.
    """
    if not isinstance(metadata_value, str):
        return None
    s = metadata_value.strip()
    if not s.startswith('@track'):
        return None
    rest = s[len('@track'):].strip()
    if ':' not in rest:
        return None
    parts = rest.split(':', 1)
    track_name = parts[0].strip()
    params_str = parts[1].strip()

    component_bit_sizes: List[int] = []
    flags_list: List[str] = []
    rig_type = None
    for param in params_str.split(';'):
        param = param.strip()
        if not param or '=' not in param:
            continue
        key, value = param.split('=', 1)
        key = key.strip()
        value = value.strip()
        if key == 'bits' and value:
            if ',' in value:
                for bs in [b.strip() for b in value.split(',') if b.strip()]:
                    try:
                        component_bit_sizes.append(int(bs))
                    except ValueError:
                        component_bit_sizes.append(0)
            else:
                try:
                    component_bit_sizes = [int(value)]
                except ValueError:
                    component_bit_sizes = []
        elif key == 'flags' and value:
            if value == 'NONE':
                flags_list = []
            else:
                flags_list = [f.strip() for f in value.split(',') if f.strip()]
        elif key == 'type' and value:
            rig_type = value
    return {
        'track_name': track_name,
        'component_bit_sizes': component_bit_sizes,
        'flags': flags_list,
        'type': rig_type,
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
    parts = param_value.split(',')
    space_value = parts[0].strip().lower()
    if space_value != 'ws':
        return None
    result = {'space': 'WORLD'}
    if len(parts) > 1:
        custom_bone = parts[1].strip()
        if custom_bone:
            result['custom_bone'] = custom_bone
    return result


def parse_as_ik_up_parameter(param_value: str) -> Optional[dict]:
    try:
        parts = param_value.split(',')
        if len(parts) != 3:
            return None

        bone_base = parts[0].strip()
        axis = parts[1].strip().upper()
        distance = float(parts[2].strip())
        if axis not in ['X', 'Y', 'Z']:
            return None
        if not bone_base:
            return None
        return {'bone_base': bone_base, 'axis': axis, 'distance': distance}
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
        Property value format: @track <name> : segments=<segments> ; flags=<flags>
        
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
                track_idx, track_name = parsed
                if track_name == fox_track_name:
                    property_key = key
                    metadata_str = layout_action[key]
                    break
        
        if metadata_str is None:
            return None
        
        # Parse @track format: @track <name> : segments=<segments> ; flags=<flags>
        if not isinstance(metadata_str, str) or not metadata_str.startswith('@track'):
            Debug.log_warning(f"      Warning: Custom property '{property_key}' is not in @track format")
            return None
        
        # Split by colon to separate track name from parameters
        parts = metadata_str.split(':', 1)
        if len(parts) < 2:
            return None
        
        # Extract track name from @track directive
        track_name_from_metadata = parts[0].replace('@track', '').strip()
        params_str = parts[1].strip()
        
        # Parse parameters (segments, bits, flags, hash, type)
        segment_types = []
        component_bit_sizes = []
        flags_value = None
        name_hash = 0
        rig_unit_type = None
        
        for param in params_str.split(';'):
            param = param.strip()
            if '=' in param:
                key, value = param.split('=', 1)
                key = key.strip()
                value = value.strip()
                
                if key == 'segments' and value:
                    # Parse segment type codes: q, qd, v, vd, f
                    segment_codes = [code.strip() for code in value.split(',') if code.strip()]
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
                            Debug.log_warning(f"      Warning: Unknown segment code '{code}' in track '{fox_track_name}'")
        
                elif key == 'bits' and value:
                    # Parse component bit sizes
                    bit_size_strs = [bs.strip() for bs in value.split(',') if bs.strip()]
                    for bs_str in bit_size_strs:
                        try:
                            component_bit_sizes.append(int(bs_str))
                        except ValueError:
                            Debug.log_warning(f"      Warning: Invalid bit size '{bs_str}' in track '{fox_track_name}'")
                            component_bit_sizes.append(0)
        
                elif key == 'flags' and value:
                    # Convert flag names to enum values, then to integer
                    flag_names = [name.strip() for name in value.split(',') if name.strip()]
                    flag_enums = []
                    for name in flag_names:
                        try:
                            flag_enums.append(TrackUnitFlags[name])
                        except KeyError:
                            Debug.log_warning(f"      Warning: Unknown flag name '{name}' in track '{fox_track_name}'")
                    
                    if flag_enums:
                        flags_value = TrackUnitFlags.track_unit_flags_to_int(flag_enums)
        
                elif key == 'hash' and value:
                    # Parse track name hash (StrCode32)
                    try:
                        name_hash = int(value)
                    except ValueError:
                        Debug.log_warning(f"      Warning: Invalid hash value '{value}' in track '{fox_track_name}'")
        
                elif key == 'type' and value:
                    # Parse rig unit type
                    rig_unit_type = RigUnitType.parse_from_string(value)
                    if rig_unit_type is None:
                        Debug.log_warning(f"      Warning: Unknown rig unit type '{value}' in track '{fox_track_name}'")
        
        # Return TrackMetaData object if we have all required data
        if segment_types and flags_value is not None:
            metadata = TrackMetaData(
                track_name=track_name_from_metadata,
                segment_types=segment_types,
                unit_flags=flags_value,
                name_hash=name_hash,
                component_bit_sizes=component_bit_sizes if component_bit_sizes else None,
                rig_unit_type=rig_unit_type
            )
            Debug.log(f"      Retrieved layout metadata for '{fox_track_name}': {len(segment_types)} segments, flags={flags_value}, hash={name_hash}, bits={component_bit_sizes}")
            return metadata
        
        # If layout-style parsing failed, try action-style parsing as a fallback
        # This handles the case where actions store per-track overrides with '@track' format
        try:
            params_parsed_action = parse_action_track_metadata(metadata_str)
        except Exception:
            params_parsed_action = None

        if params_parsed_action:
            track_name_from_action = params_parsed_action.get('track_name', fox_track_name)
            component_bit_sizes_action = params_parsed_action.get('component_bit_sizes')
            flags_list_action = params_parsed_action.get('flags') or []
            rig_type_action = params_parsed_action.get('type')

            unit_flags_action = None
            if flags_list_action:
                flag_enums_action = []
                for name in flags_list_action:
                    try:
                        flag_enums_action.append(TrackUnitFlags[name])
                    except KeyError:
                        Debug.log_warning(f"      Warning: Unknown flag name '{name}' in track '{fox_track_name}'")
                if flag_enums_action:
                    unit_flags_action = TrackUnitFlags.track_unit_flags_to_int(flag_enums_action)

            tm = TrackMetaData(
                track_name=track_name_from_action,
                name_hash=StrCode32.from_string(track_name_from_action).to_int() if track_name_from_action else None,
                segment_types=[],
                component_bit_sizes=component_bit_sizes_action if component_bit_sizes_action else None,
                unit_flags=unit_flags_action,
                flags_list=flags_list_action if flags_list_action else None,
                rig_unit_type=rig_type_action
            )
            return tm

        return None
    
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
        if not action or not action.fcurves:
            return None
        
        # Check which fcurve types exist for this bone
        has_rotation = any(
            fc.data_path in [f'pose.bones["{bone_name}"].rotation_quaternion', 
                            f'pose.bones["{bone_name}"].rotation_euler']
            for fc in action.fcurves
        )
        has_location = any(
            fc.data_path == f'pose.bones["{bone_name}"].location'
            for fc in action.fcurves
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
        from ..py_utilities.hash_utilities import unhash_rig_type
        
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
                name_hash=None,  # Not stored in GANI tracks
                segment_types=segment_types,
                component_bit_sizes=bit_sizes,
                unit_flags=unit_flags,
                flags_list=None,
                rig_unit_type=None  # Not directly available
            )
            
            track_metadata_list.append(track_meta)
            segment_idx_abs += segment_count
        
        return track_metadata_list

    @staticmethod
    def extract_space_bone(space_param: Optional[str]) -> Optional[str]:
        """Extract the space bone name from a space parameter.
        
        Space parameters can be:
        - None (use default world/local space)
        - "ws" or "ws,bone_name" (world space, optionally with custom bone)
        - "bone_name" (custom space bone)
        
        Args:
            space_param: Space parameter string (typically from bone_params.space_r or space_l)
            
        Returns:
            The custom space bone name if specified, None otherwise
        """
        if space_param:
            # Handle string-format space parameters
            if isinstance(space_param, str):
                if not space_param.startswith('ws'):
                    # It's a custom bone name
                    return space_param
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
    if action_meta.unit_flags is not None:
        result.unit_flags = action_meta.unit_flags
    elif action_meta.flags_list:
        # Convert flag list to integer bitfield
        flags_enums = []
        for name in action_meta.flags_list:
            if name in TrackUnitFlags.__members__:
                flags_enums.append(TrackUnitFlags[name])
        if flags_enums:
            result.unit_flags = TrackUnitFlags.track_unit_flags_to_int(flags_enums)
        else:
            result.unit_flags = layout_meta.unit_flags

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
