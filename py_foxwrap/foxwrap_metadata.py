"""
Shared utilities for parsing and working with animation metadata.

This module contains helper functions used throughout the importer and
exporter for parsing track metadata strings stored either in mapping files
or on Blender action properties.
"""
from typing import List, Optional, Dict, Set, Tuple, Union
import copy

import bpy

from ..py_core.core_logging import Debug

from ..py_utilities.utilities_blender_animation import action_has_fcurves, iter_action_fcurves, build_data_path_for_bone
from ..py_utilities.utilities_hashing import (
    unhash_param_name,
    hash_or_parse_name,
)
from ..py_utilities.utilities_parsing import format_float_for_metadata
from ..py_utilities.utilities_hashing import is_hash_string, unhash_rig_type

from ..py_fox.fox_gani_types import Gani2TrackData, SegmentType, TrackHeader, TrackUnit, TrackUnitFlags, TrackMiniHeader
from ..py_fox import fox_gani_constants as gani_const
from ..py_fox import fox_mtar_constants as mtar_const
from ..py_fox.fox_frig_types import RigUnitType

from ..py_foxwrap.foxwrap_misc_types import Tracks, TrackUnitWrapper
from ..py_foxwrap.foxwrap_metadata_types import TrackMetaData


# Action property key constants -------------------------------------------------------------
# These strings are used as Blender action custom-property keys throughout the
# importer/exporter. The field names are derived from Fox Engine binary templates.
# (Constants imported from py_fox layer).

TRACK_PROP_PREFIX = "track_"  # used by make_/parse_track_property_key
EVENT_PROP_PREFIX = "event_"  # used by make_/parse_event_property_key
PROP_PARAMS = "params"        # DEPRECATED (kept for GANI2 compatibility); replaced by PROP_NODE_PARAMS_PREFIX
PROP_NODE_PARAMS_PREFIX = "gfox_node_params_"  # used by store_/parse_node_params_on_action

# FoxData StringData list action properties (old-format GANI round-trip)
# SKL_LIST names are NOT stored here — they are applied directly to bone track names
# during import (see GaniReader._apply_stringlist_names) and re-derived from track
# names during export.
PROP_MTP_LIST        = "gfox_mtp_list"         # MTP_LIST  node — motion point name hashes
PROP_MTP_PARENT_LIST = "gfox_mtp_parent_list"  # MTP_PARENT_LIST node — motion point parent hashes
PROP_NO_SKL_LIST     = "gfox_no_skl_list"      # 1 = original GANI had no SKL_LIST node (suppress on re-export)


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


# Generic Node Parameters ####################################################################
# Unified store/parse for FoxData node parameters accessible by node path key (e.g., "MOTION",
# "SHADER/TENSION_CHEEKL"). Format: "NAME:value,NAME:value" with implicit type inference from
# value content ('.' → float, digits-only → int, else → str).

def store_node_params_on_action(
    action: bpy.types.Action,
    node_key: str,
    params: List[Tuple[int, Union[float, str, int]]],
) -> None:
    """Store FoxData node params under a generic node-path key on a Blender action.

    Params are serialized as ``"<name>:<value>,<name>:<value>"`` pairs.
    FLOAT values are formatted with :func:`format_float_for_metadata` (always
    contains ``'.'``).  STRING inline values are stored as plain strings.
    STRING hash-only values are stored as decimal integers (no ``'.'``), so the
    parser can distinguish them from floats.

    Args:
        action: Blender action to store params on.
        node_key: Node path string (e.g., ``"MOTION"``, ``"SHADER/TENSION_CHEEKL"``).
        params: List of ``(name_hash, value)`` tuples.
    """
    if not params:
        return

    key = f"{PROP_NODE_PARAMS_PREFIX}{node_key}"
    items = []
    for name, value in params:
        name_str = unhash_param_name(name) if isinstance(name, int) else str(name)
        if isinstance(value, float):
            items.append(f"{name_str}:{format_float_for_metadata(value)}")
        else:
            # int (hash-only STRING) or str (inline STRING): store as-is
            items.append(f"{name_str}:{value}")

    action[key] = ','.join(items)
    action.id_properties_ui(key).update(
        description=f"FoxData node params for '{node_key}': comma-separated name:value pairs"
    )


def parse_node_params_from_action(
    action: bpy.types.Action,
    node_key: str,
) -> List[Tuple[int, Union[float, str, int]]]:
    """Read FoxData node params back from a Blender action custom property by node key.

    The value type is inferred from the stored string:

    - Contains ``'.'`` → ``float`` (FLOAT parameter).
    - All digits (no ``'.'``) → ``int`` (STRING hash-only parameter).
    - Otherwise → ``str`` (STRING inline parameter).

    Args:
        action: Blender action to read params from.
        node_key: Node path string (e.g., ``"MOTION"``, ``"SHADER/TENSION_CHEEKL"``).

    Returns:
        List of ``(name_hash, value)`` tuples, or empty list if the property is absent.
    """
    key = f"{PROP_NODE_PARAMS_PREFIX}{node_key}"
    value_str = action.get(key)
    if not value_str:
        return []

    result: List[Tuple[int, Union[float, str, int]]] = []
    for pair in value_str.split(','):
        pair = pair.strip()
        if not pair or ':' not in pair:
            continue
        name_str, val_str = pair.split(':', 1)
        val_str = val_str.strip()
        if '.' in val_str:
            try:
                value: Union[float, str, int] = float(val_str)
            except ValueError:
                Debug.log_warning(f"parse_node_params_from_action: skipping invalid pair '{pair}'")
                continue
        elif val_str.isdigit():
            value = int(val_str)
        else:
            value = val_str
        name_str = name_str.strip()
        name_hash = hash_or_parse_name(name_str)
        result.append((name_hash, value))
    return result


def iter_all_node_params_from_action(action: bpy.types.Action) -> Dict[str, List[Tuple[int, Union[float, str, int]]]]:
    """Scan a Blender action for all FoxData node params and return them as a dict.

    Scans all custom properties with prefix ``PROP_NODE_PARAMS_PREFIX`` and parses
    each using :func:`parse_node_params_from_action`.

    Args:
        action: Blender action to scan.

    Returns:
        Dict mapping node_key → params list. Empty dict if no params found.
    """
    result: Dict[str, List[Tuple[int, Union[float, str, int]]]] = {}
    for key in action.keys():
        if key.startswith(PROP_NODE_PARAMS_PREFIX):
            node_key = key[len(PROP_NODE_PARAMS_PREFIX):]
            params = parse_node_params_from_action(action, node_key)
            if params:
                result[node_key] = params
    return result


def merge_node_params(
    base_node_params: Dict[str, List[Tuple[int, Union[float, str, int]]]],
    shader_node_params: Optional[Dict[str, List[Tuple[int, Union[float, str, int]]]]] = None,
) -> Dict[str, List[Tuple[int, Union[float, str, int]]]]:
    """Merge non-SHADER and SHADER node params into a final node params map."""
    merged: Dict[str, List[Tuple[int, Union[float, str, int]]]] = {
        k: v for k, v in base_node_params.items() if not k.startswith("SHADER")
    }
    if shader_node_params:
        for k, v in shader_node_params.items():
            if k.startswith("SHADER"):
                merged[k] = v
    return merged


def resolve_gani_frame_info(
    gani_layout_track: Optional[Tracks],
    gani_track_mini_header: Optional[TrackMiniHeader],
    gani_motion_point_track_header: Optional[TrackHeader],
) -> Tuple[int, int]:
    """Resolve frame count and rate for exported GANI based on available sources."""
    frame_count = 0
    frame_rate = 60

    if gani_track_mini_header is not None:
        frame_count = gani_track_mini_header.frame_count
    elif gani_layout_track is not None:
        frame_count = gani_layout_track.header.frame_count

    if gani_layout_track is not None and gani_layout_track.header.frame_rate > 0:
        frame_rate = gani_layout_track.header.frame_rate
    elif gani_motion_point_track_header is not None:
        frame_rate = gani_motion_point_track_header.frame_rate

    return frame_count, frame_rate


def store_gani_params_on_action(action: bpy.types.Action, params: List[Tuple[int, Union[float, str, int]]]) -> None:
    """Store Gani2/MOTION params as a single custom property on a Blender action.

    **Wrapper around** :func:`store_node_params_on_action` **with node_key="MOTION".**
    Kept for backward compatibility (used by GANI2 reader and old-format MOTION node params).

    Args:
        action: Blender action to store params on.
        params: List of ``(name_hash, value)`` tuples.
    """
    store_node_params_on_action(action, "MOTION", params)


def parse_gani_params_from_action(action: bpy.types.Action) -> List[Tuple[int, Union[float, str, int]]]:
    """Read Gani2/MOTION params back from a Blender action custom property.

    **Wrapper around** :func:`parse_node_params_from_action` **with node_key="MOTION".**
    Kept for backward compatibility (used by GANI2 writer and export path).

    Args:
        action: Blender action to read params from.

    Returns:
        List of ``(name_hash, value)`` tuples, or empty list if the property is
        absent or the action carries no params.
    """
    return parse_node_params_from_action(action, "MOTION")


def store_shader_node_params_on_action(
    action: bpy.types.Action,
    prop_name: str,
    params: List[Tuple[int, Union[float, str, int]]],
) -> None:
    """Store per-property shader node params on a Blender action.

    **Wrapper around** :func:`store_node_params_on_action` **with node_key=f"SHADER/{prop_name}".**
    Kept for backward compatibility and convenience.

    Args:
        action: Blender action to store params on.
        prop_name: Property name (e.g., "TENSION_CHEEKL").
        params: List of ``(name_hash, value)`` tuples.
    """
    store_node_params_on_action(action, f"SHADER/{prop_name}", params)


def parse_shader_node_params_from_action(
    action: bpy.types.Action,
    prop_name: str,
) -> List[Tuple[int, Union[float, str, int]]]:
    """Read per-property shader node params back from a Blender action custom property.

    **Wrapper around** :func:`parse_node_params_from_action` **with node_key=f"SHADER/{prop_name}".**
    Kept for backward compatibility and convenience.

    Args:
        action: Blender action to read params from.
        prop_name: Property name (e.g., "TENSION_CHEEKL").

    Returns:
        List of ``(name_hash, value)`` tuples, or empty list if the property is
        absent or the action carries no params for this property.
    """
    return parse_node_params_from_action(action, f"SHADER/{prop_name}")


def store_foxdata_stringlist_on_action(
    action: bpy.types.Action,
    key: str,
    names: List,
) -> None:
    """Store a FoxData StringData name list on a Blender action custom property.

    Each entry in ``names`` may be a real bone/point name string or an integer
    hash (uint32).  Real string names are stored as-is so the writer can
    reproduce inline name strings in the output file.  Integer hash values and
    hash-literal strings (e.g. ``"0xF08B256E"`` from a dictionary miss) are
    stored as decimal integers.  The entries are joined with commas.

    Args:
        action: Blender action to store the list on.
        key:    Custom property key (e.g. ``PROP_SKL_LIST``).
        names:  Bone/point names (str) or hash values (int).
    """
    parts: List[str] = []
    for name in names:
        if isinstance(name, int):
            parts.append(str(name))
        else:
            s = str(name)
            # Hash literal fallback (e.g. "0xF08B256E" — no inline string in source)
            # → store as decimal integer so writer knows not to emit inline string.
            try:
                val = int(s, 0)
                parts.append(str(val))
            except ValueError:
                # Real bone name — preserve as-is for inline string round-trip.
                parts.append(s)
    action[key] = ",".join(parts)
    action.id_properties_ui(key).update(
        description=f"FoxData StringData name list for {key} (comma-separated; strings = inline names, integers = hash-only entries)"
    )


def parse_foxdata_stringlist_from_action(
    action: bpy.types.Action,
    key: str,
) -> Optional[List]:
    """Read a FoxData StringData name list from a Blender action custom property.

    Args:
        action: Blender action to read from.
        key:    Custom property key (e.g. ``PROP_SKL_LIST``).

    Returns:
        List of entries, or ``None`` if the property is absent.
        Each entry is either a ``str`` (real bone name, will produce an inline
        string in the output file) or an ``int`` (hash-only entry, no inline string).
        An empty list is returned for a property with an empty string value.
    """
    value_str = action.get(key)
    if value_str is None:
        return None
    if not value_str:
        return []
    result: List = []
    for part in value_str.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError:
            result.append(part)  # real bone name string
    return result


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


# Track Metadata Storage #############################################################

def store_track_metadata_on_action(
    action: bpy.types.Action,
    track_metadata_list: List['TrackMetaData'],
    include_segments: bool = True,
) -> None:
    """Store track metadata from :class:`TrackMetaData` objects as action custom properties.

    Stores metadata in unified ``key=value`` format.

    Layout track format::

        name=<name> ; segments=<segs> ; bits=<bit_sizes> ; flags=<flags>

    GANI track format::

        name=<name> ; bits=<bit_sizes> ; flags=<flag_names>

    Args:
        action:             The Blender action to store metadata on.
        track_metadata_list: List of :class:`TrackMetaData` objects to serialise.
        include_segments:   Include segment-type abbreviations (``True`` for
                            layout tracks, ``False`` for GANI tracks).
    """
    track_type = "layout" if include_segments else "GANI"
    Debug.log(
        f"Storing {track_type} track metadata for "
        f"{len(track_metadata_list)} track(s) on action '{action.name}'"
    )

    for track_idx, track_meta in enumerate(track_metadata_list):
        track_name = track_meta.track_name
        metadata_parts = []

        if include_segments:
            abbrev_map = {
                SegmentType.QUAT: 'q',
                SegmentType.QUAT_DIFF: 'qd',
                SegmentType.VECTOR3: 'v',
                SegmentType.VECTOR_DIFF: 'vd',
                SegmentType.FLOAT: 'f',
                SegmentType.VECTOR2: 'v2',
                SegmentType.VECTOR4: 'v4',
            }
            segment_str = ','.join(
                abbrev_map.get(seg_type, '?')
                for seg_type in track_meta.segment_types
            )
            metadata_parts.append(f"segments={segment_str}")

        bit_sizes_str = ''
        if track_meta.component_bit_sizes:
            bit_sizes_str = ','.join(str(b) for b in track_meta.component_bit_sizes)
        if bit_sizes_str:
            metadata_parts.append(f"bits={bit_sizes_str}")

        if track_meta.unit_flags is not None:
            flags_list = TrackUnitFlags.int_to_track_unit_flags(track_meta.unit_flags)
            flag_names = [flag.name for flag in flags_list]
            flags_str = ','.join(flag_names) if flag_names else (
                'NONE' if not include_segments else ''
            )
        else:
            flags_str = 'NONE' if not include_segments else ''
        if flags_str:
            metadata_parts.append(f"flags={flags_str}")


        if track_meta.rig_unit_type is not None:
            metadata_parts.append(f"type={track_meta.rig_unit_type.name}")

        metadata_value = f"name={track_name} ; {' ; '.join(metadata_parts)}"
        property_key = make_track_property_key(track_idx, track_name)
        action[property_key] = metadata_value
        action.id_properties_ui(property_key).update(
            description=f"Track metadata for {track_name}"
        )

        if include_segments:
            Debug.log(f"  Stored: {property_key} = {metadata_value}")
        else:
            Debug.log(
                f"  Track {track_idx} ({track_name}): "
                f"bits=[{bit_sizes_str}], flags={flags_str}"
            )


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


def store_mtar_properties_on_action(action: bpy.types.Action, version: int, flags: int) -> None:
    """Store MTAR-level version and flags as custom properties on an action.
    
    These properties preserve the MTAR file format metadata, allowing export to
    recreate files in the same format (old FoxData vs. new GANI2).
    
    Args:
        action: The Blender action to store properties on
        version: MTAR version number (e.g., 201304220 for old, 201403250 for new)
        flags: MTAR flags (e.g., 0x1000 for new format, 0x0 for old)
    """
    action[mtar_const.MTAR_VERSION] = version
    action[mtar_const.MTAR_FLAGS] = flags
    Debug.log(f"Stored MTAR properties on action: version={version}, flags=0x{flags:04X}")


def read_mtar_properties_from_action(action: Optional[bpy.types.Action]) -> Dict[str, int]:
    """Read MTAR version and flags from action custom properties.
    
    Returns new-format defaults if properties are not present (for backward compatibility
    with animations created before MTAR properties were stored).
    
    Args:
        action: The Blender action to read properties from (can be None)
        
    Returns:
        Dictionary with MTAR_VERSION and MTAR_FLAGS keys
    """
    result = {
        mtar_const.MTAR_VERSION: 201403250,  # Default: new format (TPP)
        mtar_const.MTAR_FLAGS: 0x1000         # Default: UseMini flag (new format)
    }
    
    if action:
        if mtar_const.MTAR_VERSION in action.keys():
            result[mtar_const.MTAR_VERSION] = int(action[mtar_const.MTAR_VERSION])
        if mtar_const.MTAR_FLAGS in action.keys():
            result[mtar_const.MTAR_FLAGS] = int(action[mtar_const.MTAR_FLAGS])
    
    return result

def read_mtar_properties_from_any_action(
        layout_action: Optional[bpy.types.Action],
        fallback_actions: Optional[List[bpy.types.Action]] = None,
        ) -> Dict[str, any]:
    """Reads MTAR_VERSION and MTAR_FLAGS from layout_action or per-GANI fallback.
    
    For new-format GANI2, reads from the dedicated layout action.
    For old-format GANI1, reads from the first per-GANI action when no layout exists.
    
    Args:
        layout_action: Optional layout track action
        fallback_actions: Optional list of per-GANI actions to try if layout_action is None
        
    Returns:
        Dictionary with MTAR version and flags (may be empty if neither source is available)
    """
    
    if layout_action is not None:
        return read_mtar_properties_from_action(layout_action)
    if fallback_actions:
        for action in fallback_actions:
            if action is not None:
                props = read_mtar_properties_from_action(action)
                if props:
                    return props
    return {}

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
        elif code == 'v2':
            segment_types.append(SegmentType.VECTOR2)
        elif code == 'v4':
            segment_types.append(SegmentType.VECTOR4)
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
    """Unified parser for track metadata in key=value format.

    Accepts semicolon-separated key=value entries produced by actions or
    internal layout tracks. The 'name' key is required, and optional
    attributes include segments, type, flags, bits, and count.

    Auto-detects which parameters are present:
    - Explicit segments= takes priority
    - Falls back to type-derived segments when segments= not present
    - Handles MULTI_LOCAL_ORIENTATION count parameter

    Args:
        metadata_str: Semicolon-separated key=value string. The 'name' key is required.
            No '@' prefix expected - callers must strip prefixes before calling this function.

    Returns:
        Dictionary with standardized keys:
        - track_name: str
        - segment_types: List[SegmentType]
        - component_bit_sizes: List[int] (optional)
        - flags_list: List[str] (optional)
        - unit_flags: int (optional)
        - rig_unit_type: str (optional)
        - count: int (optional)
    """
    if not isinstance(metadata_str, str):
        return None

    metadata_str = metadata_str.strip()
    if not metadata_str:
        return None

    # Collect all key=value pairs (including 'name')
    track_name = None
    segments_str = None
    type_str = None
    bits_str = None
    flags_str = None
    count_str = None

    for param in metadata_str.split(';'):
        param = param.strip()
        if not param or '=' not in param:
            continue

        key, value = param.split('=', 1)
        key = key.strip()
        value = value.strip()

        if key == 'name':
            track_name = value
        elif key == 'segments':
            segments_str = value
        elif key == 'type':
            type_str = value
        elif key == 'bits':
            bits_str = value
        elif key == 'flags':
            flags_str = value
        elif key == 'count':
            count_str = value

    if not track_name:
        return None

    # Initialize result dict with all possible fields
    result = {
        'track_name': track_name,
        'segment_types': [],
        'component_bit_sizes': None,
        'flags_list': None,
        'unit_flags': None,
        'rig_unit_type': None,
        'count': None
    }
    
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




def parse_track_type_from_metadata(metadata_str: str) -> Optional[RigUnitType]:
    """Extract only the RigUnitType from track metadata string.

    Lightweight parser that extracts only the 'type=VALUE' key without
    parsing segments, flags, or other metadata. Optimized for performance.

    Args:
        metadata_str: Metadata string in key=value format, e.g. 'name=Root ; type=ROOT ; bits=14'

    Returns:
        RigUnitType enum if type attribute found and valid, None otherwise
    """
    if not metadata_str or not isinstance(metadata_str, str):
        return None

    for attr in metadata_str.split(';'):
        attr = attr.strip()
        if not attr or '=' not in attr:
            continue

        attr_key, attr_value = attr.split('=', 1)
        if attr_key.strip() == 'type':
            return RigUnitType.parse_from_string(attr_value.strip())

    return None


def extract_fox_bone_to_rig_unit_type_mapping(layout_action: bpy.types.Action, 
                                          cache: Optional[Dict[str, Dict[str, RigUnitType]]] = None
                                          ) -> Dict[str, RigUnitType]:
    """Extract fox bone name to RigUnitType mapping from layout action metadata.
    
    Parses track metadata stored in layout action custom properties (format: 'name=FoxBoneName ; type=ROOT ; bits=14')
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
            if not isinstance(metadata_str, str):
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
    """Parse track metadata stored on actions (GANI file properties).

    Format: name=<name> ; type=<type> ; [flags=<flags>] ; [bits=<bits>]

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


# FCurve Helper #############################################################

def infer_segment_types_from_fcurves(
    action: bpy.types.Action,
    bone_name: str,
) -> Tuple[List[SegmentType], List[int]]:
    """Infer track segment types and default component bit sizes for a bone.

    This helper examines *action* for F‑curves belonging to *bone_name* and
    returns a pair ``(segment_types, default_bit_sizes)``.  The bit sizes are
    **defaults only**; if explicit metadata is stored on the action it
    overrides these values during export.

    The algorithm mirrors the logic previously embedded in
    :meth:`TrackMetaData.from_fcurves` and
    ``build_track_metadata_dict_from_fcurves``:

    * ``rotation_quaternion`` or ``rotation_euler`` curves → ``QUAT`` added.
    * ``location`` curves are inspected by channel index:
        - ``{0}`` → ``FLOAT``
        - ``{0,1}`` → ``VECTOR2``
        - any non-empty set containing 0,1,2 → ``VECTOR3``
    * Both a rotation and a location type may be returned.

    Unexpected situations (e.g. deriving ``VECTOR3`` with only one location
    curve) generate a ``Debug.log_warning`` message so users can investigate.

    Args:
        action:      Blender action containing F‑curves.
        bone_name:   Name of the bone whose curves to inspect.

    Returns:
        Tuple of (segment_types:list of SegmentType, default_bit_sizes:list of int).
        May return ``([], [])`` if no relevant F‑curves are found.
    """
    

    if not action or not action_has_fcurves(action):
        return [], []

    rotation_quat_path = build_data_path_for_bone(bone_name, 'rotation_quaternion')
    rotation_euler_path = build_data_path_for_bone(bone_name, 'rotation_euler')
    location_path = build_data_path_for_bone(bone_name, 'location')

    has_rotation = False
    location_indices: Set[int] = set()

    for fc in iter_action_fcurves(action):
        if fc.data_path in (rotation_quat_path, rotation_euler_path):
            has_rotation = True
        elif fc.data_path == location_path:
            location_indices.add(fc.array_index)

    segment_types: List[SegmentType] = []
    if has_rotation:
        segment_types.append(SegmentType.QUAT)

    if location_indices == {0}:
        segment_types.append(SegmentType.FLOAT)
    elif location_indices == {0, 1}:
        segment_types.append(SegmentType.VECTOR2)
    elif location_indices:
        segment_types.append(SegmentType.VECTOR3)

    # warn about odd combinations
    if location_indices and segment_types and segment_types[-1] == SegmentType.VECTOR3 and len(location_indices) < 3:
        Debug.log_warning(
            f"infer_segment_types_from_fcurves: bone '{bone_name}' has "
            f"location indices {location_indices} but inferred VECTOR3"
        )

    # compute default bit sizes in parallel
    default_bits: List[int] = []
    for st in segment_types:
        default_bits.append(15 if st in (SegmentType.QUAT, SegmentType.QUAT_DIFF) else 16)

    return segment_types, default_bits


# Track MetaData helper functions (migrated from TrackMetaData static methods) #######

def build_track_metadata_from_action(layout_action: bpy.types.Action, fox_track_name: str) -> Optional[TrackMetaData]:
    """Retrieve track metadata for one track from a layout action."""
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

    if not isinstance(metadata_str, str):
        Debug.log_warning(f"      Warning: Custom property '{property_key}' is not valid metadata")
        return None

    parsed = parse_track_metadata_generic(metadata_str)
    if not parsed:
        Debug.log_warning(f"      Warning: Failed to parse metadata for track '{fox_track_name}'")
        return None

    rig_unit_type = None
    if parsed['rig_unit_type']:
        rig_unit_type = RigUnitType.parse_from_string(parsed['rig_unit_type'])
        if rig_unit_type is None:
            Debug.log_warning(f"      Warning: Unknown rig unit type '{parsed['rig_unit_type']}' in track '{fox_track_name}'")

    track_name_val = parsed['track_name']
    name_hash = hash_or_parse_name(track_name_val)

    return TrackMetaData(
        track_name=track_name_val,
        name_hash=name_hash,
        segment_types=parsed['segment_types'],
        component_bit_sizes=parsed['component_bit_sizes'],
        unit_flags=parsed['unit_flags'],
        flags_list=parsed['flags_list'],
        rig_unit_type=rig_unit_type
    )


def build_track_metadata_from_fcurves(bone_name: str, action: bpy.types.Action) -> Optional[TrackMetaData]:
    """Infer minimal TrackMetaData from action fcurves."""
    if not action or not action_has_fcurves(action):
        return None

    segment_types, default_bits = infer_segment_types_from_fcurves(action, bone_name)
    if not segment_types:
        return None

    from ..py_fox.fox_misc_types import StrCode32

    return TrackMetaData(
        track_name=bone_name,
        segment_types=segment_types,
        unit_flags=0,
        name_hash=StrCode32.from_string(bone_name).to_int(),
        component_bit_sizes=default_bits,
        rig_unit_type=None
    )


def build_track_metadata_from_layout_track_units(
    track_units: List[TrackUnit],
    track_name_prefix: str = "Track",
    gani_tracks: Optional[List['TrackUnitWrapper']] = None,
) -> List[TrackMetaData]:
    """Convert layout track units to TrackMetaData objects."""
    track_metadata_list: List[TrackMetaData] = []

    for track_idx, track_unit in enumerate(track_units):
        track_name: str = f"{track_name_prefix}{track_idx}"
        name_hash: int = 0
        if track_unit.name:
            name_hash = track_unit.name.to_int() if hasattr(track_unit.name, 'to_int') else int(track_unit.name)
            resolved_name: Optional[str] = unhash_rig_type(name_hash)
            if resolved_name:
                track_name = resolved_name
            elif gani_tracks and track_idx < len(gani_tracks):
                gani_name = gani_tracks[track_idx].name
                track_name = gani_name if not is_hash_string(gani_name) else str(track_unit.name)
            else:
                track_name = str(track_unit.name)

        segment_types: List[SegmentType] = []
        component_bit_sizes: List[int] = []
        for track_data in track_unit.segments_data:
            segment_types.append(track_data.td_type)
            component_bit_sizes.append(track_data.component_bit_size)

        rig_unit_type: Optional[RigUnitType] = None
        if gani_tracks and track_idx < len(gani_tracks):
            rig_unit_type = gani_tracks[track_idx].rig_unit_type

        track_metadata_list.append(TrackMetaData(
            track_name=track_name,
            name_hash=name_hash if name_hash != 0 else None,
            segment_types=segment_types,
            component_bit_sizes=component_bit_sizes,
            unit_flags=track_unit.unit_flags,
            flags_list=None,
            rig_unit_type=rig_unit_type
        ))

    return track_metadata_list


def build_track_metadata_from_gani_tracks(
    gani_tracks: List['TrackUnitWrapper'],
    segment_headers: List[Gani2TrackData],
) -> List[TrackMetaData]:
    """Convert GANI tracks and segment headers to TrackMetaData objects."""
    track_metadata_list: List[TrackMetaData] = []
    segment_idx_abs: int = 0

    for _, gani_track in enumerate(gani_tracks):
        track_name: str = gani_track.name
        unit_flags: Optional[int] = None
        if gani_track.unit_flags:
            unit_flags = TrackUnitFlags.track_unit_flags_to_int(gani_track.unit_flags)

        segment_count: int = len(gani_track.segments_track_data)
        bit_sizes: List[int] = []
        segment_types: List[SegmentType] = []

        for seg_idx in range(segment_count):
            abs_idx: int = segment_idx_abs + seg_idx
            if abs_idx < len(segment_headers):
                bit_sizes.append(segment_headers[abs_idx].component_bit_size)
            else:
                bit_sizes.append(0)

            if seg_idx < len(gani_track.segments_track_data):
                segment_types.append(gani_track.segments_track_data[seg_idx].data_blob.type)

        track_metadata_list.append(TrackMetaData(
            track_name=track_name,
            name_hash=None,
            segment_types=segment_types,
            component_bit_sizes=bit_sizes,
            unit_flags=unit_flags,
            flags_list=None,
            rig_unit_type=None
        ))

        segment_idx_abs += segment_count

    return track_metadata_list


def extract_space_bone_name(space_param) -> Optional[str]:
    """Extract custom space bone name from a space parameter dict."""
    if space_param and isinstance(space_param, dict):
        return space_param.get('custom_bone')
    return None


# Track MetaData wrapper #############################################################

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
            # Derive hash from the track name string, handling numeric literals
            # automatically via helper.
            try:
                result.name_hash = hash_or_parse_name(action_meta.track_name)
            except Exception:
                # if helper somehow fails (shouldn't), leave existing hash
                pass
    # Override flags: prefer explicit integer if available, otherwise flags list
    # Edit: unit flags are a special case. The action always (!) overrides the layout
    result.unit_flags = action_meta.unit_flags

    # Override segment types if explicitly specified in action metadata.
    # Users can add segments=q,f (or any segment code) to a GANI action's track
    # custom property to force specific segment types, overriding the layout and
    # FCurve-presence inference. This is the escape hatch for ambiguous cases
    # (e.g. user-created FLOAT animation that only keys location[0]).
    if action_meta.segment_types:
        result.segment_types = action_meta.segment_types

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
        metadata = build_track_metadata_from_action(action, fox_track_name)
        if metadata:
            metadata_dict[fox_track_name] = metadata
            Debug.log(f"    Parsed track {track_idx}: {fox_track_name} ({len(metadata.segment_types)} segments)")
    
    Debug.log(f"  Parsed {len(metadata_dict)} track(s) from layout action")
    return metadata_dict
