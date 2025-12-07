"""
Shared utilities for parsing and working with animation metadata.

This module contains helper functions used throughout the importer and
exporter for parsing @track metadata strings stored either in mapping files
or on Blender action properties.
"""
from typing import List, Optional, Dict, Any, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field
import copy

from ..py_fox.fox_gani_types import SegmentType, TrackHeader
from ..py_fox.fox_gani_types import TrackUnitFlags
from ..py_fox.fox_misc_types import StrCode32

if TYPE_CHECKING:
    import bpy


# ========== Custom Property Key Utilities ==========

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


def iter_track_properties(action: 'bpy.types.Action') -> List[Tuple[int, str, str]]:
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


def iter_event_properties(action: 'bpy.types.Action') -> List[Tuple[int, str, str]]:
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


# ========== Track Header Properties ==========

def store_track_header_properties_on_action(action: 'bpy.types.Action', track_header: TrackHeader) -> None:
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


def read_track_header_properties_from_action(action: Optional['bpy.types.Action']) -> Dict[str, int]:
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

    @classmethod
    def from_layout_metadata(cls, layout_meta: dict) -> Optional['TrackMetaData']:
        """Build a TrackMetadata instance from layout metadata dictionary.

        The input `layout_meta` is expected to be in the format returned by
        `parse_track_metadata()` (keys: name, type, count, bits, segments, flags).
        """
        if not layout_meta:
            return None
        track_name = layout_meta.get('name', '')
        tm = cls(track_name=track_name)
        tm.rig_unit_type = layout_meta.get('type')
        tm.name_hash = None
        # Build segments if present
        # Convert the layout segments to a list of SegmentType enums if possible
        segments = layout_meta.get('segments', [])
        tm.segment_types = []
        if segments:
            for seg in segments:
                # segment may be a dict with 'data_type' or a SegmentType enum already
                if isinstance(seg, dict):
                    dtype = seg.get('data_type', '').lower()
                    if dtype in ('quat', 'quaternion'):
                        tm.segment_types.append(SegmentType.QUAT)
                    elif dtype in ('quatdiff', 'qd'):
                        tm.segment_types.append(SegmentType.QUAT_DIFF)
                    elif dtype in ('vec3', 'vector3', 'v'):
                        tm.segment_types.append(SegmentType.VECTOR3)
                    elif dtype in ('vec3diff', 'vxd', 'vd', 'vectordiff'):
                        tm.segment_types.append(SegmentType.VECTOR_DIFF)
                    elif dtype in ('float', 'f'):
                        tm.segment_types.append(SegmentType.FLOAT)
                    else:
                        # Unknown - skip
                        continue
                elif isinstance(seg, SegmentType):
                    tm.segment_types.append(seg)
        # If flags exist, they will be processed by caller into unit_flags
        tm.component_bit_sizes = None
        return tm

    def apply_action_override(self, action_meta_str: str) -> None:
        """Apply per-action overrides to this TrackMetadata instance.

        The action_meta_str is expected to be the full '@track' metadata string
        (e.g. "@track Root : bits=24 ; flags=IS_STATIC ; type=ROOT").
        Only the parsed fields will be used to override values in-place.
        """
        parsed = parse_action_track_metadata(action_meta_str)
        if not parsed:
            return
        # Override component bit sizes if provided
        cbs = parsed.get('component_bit_sizes')
        if cbs:
            self.component_bit_sizes = cbs
        # Override flags: we store as list here; conversion to int occurs elsewhere
        flags = parsed.get('flags')
        if flags is not None and flags != []:
            # Keep flag names list for later conversion
            self.flags_list = flags
            # Convert to unit_flags int using TrackUnitFlags if possible
            try:
                flag_enums = [TrackUnitFlags[name] for name in flags if name in TrackUnitFlags.__members__]
                if flag_enums:
                    # Convert list of enums to int bitfield
                    bit_value = 0
                    for fe in flag_enums:
                        bit_value |= int(fe)
                    self.unit_flags = bit_value
            except KeyError:
                # If conversion fails, leave unit_flags as 0 or None
                self.unit_flags = 0
        # Override rig type
        rtype = parsed.get('type')
        if rtype:
            self.rig_unit_type = rtype

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
