"""
Naming utilities for MTAR action and NLA strip naming.

Provides consistent naming format across import/export workflows.
"""
from typing import Optional, Dict, Tuple, List

from .utilities_hashing import unhash_gani_path
from ..py_fox.fox_mtar_types import MtarTableList2
from ..py_foxwrap.foxwrap_misc import TrackUnitWrapper


def extract_gani_name_from_path(path_str: str) -> str:
    """Extract the last segment from a GANI asset path.
    
    Args:
        path_str: Full asset path (e.g., "/Assets/mgo/motion/walk_idle")
        
    Returns:
        Last path segment (e.g., "walk_idle")
    """
    return path_str.rstrip('/').rsplit('/', 1)[-1]


def resolve_gani_name_segment(file_header: MtarTableList2, gani_hash_dict: Optional[Dict[int, str]]) -> Tuple[Optional[str], Optional[str]]:
    """Resolve GANI full path and name segment from hash dictionary if available.
    
    Args:
        file_header: MtarTableList2 file header with path hash
        gani_hash_dict: Optional pre-loaded GANI hash dictionary
        
    Returns:
        Tuple of (gani_full_path, gani_name_segment) where both are None if not resolved
    """
    gani_full_path: Optional[str] = None
    gani_name_segment: Optional[str] = None
    
    if gani_hash_dict is not None and hasattr(file_header, 'path'):
        gani_full_path = unhash_gani_path(file_header.path, gani_hash_dict)
        if gani_full_path is not None:
            gani_name_segment = extract_gani_name_from_path(gani_full_path)
    
    return gani_full_path, gani_name_segment



def _format_gani_name(
    base_name: str,
    running_idx: int,
    h_idx: int,
    d_idx: int,
    verbose: bool,
    is_motion_points: bool,
    is_layout: bool,
    gani_name: Optional[str],
    ext: str,
    is_shader_nodes: bool = False,
) -> str:
    """Shared formatting logic for action and strip names.

    New naming schema: <mtar-name>.<animation-parts>.<index>.<type>.<ext>
    Where animation-parts can include resolved name and optional verbose hash.

    Args:
        ext: File extension suffix without leading dot, e.g. "gani" or "strip"
        is_shader_nodes: This is a shader nodes action/strip (old-format only)
    """
    if is_layout:
        # Layout uses index -1 and type 'track'
        return f"{base_name}.layout.-1.track.{ext}"

    # Build animation component: can include resolved name and verbose hash parts
    animation_parts = []
    if gani_name is not None:
        animation_parts.append(gani_name)
    if verbose:
        animation_parts.append(f"h{h_idx}_d{d_idx}")
    animation_str = ".".join(animation_parts) if animation_parts else str(running_idx)

    # Determine type string
    if is_motion_points:
        gani_type = "motionpoints"
    elif is_shader_nodes:
        gani_type = "shadernodes"
    else:
        gani_type = "track"

    # Format: <base>.<animation-parts>.<index>.<type>.<ext>
    return f"{base_name}.{animation_str}.{running_idx}.{gani_type}.{ext}"


def format_action_name(
    base_name: str,
    running_idx: int,
    h_idx: int,
    d_idx: int,
    verbose: bool,
    is_motion_points: bool = False,
    is_layout: bool = False,
    gani_name: Optional[str] = None,
    is_shader_nodes: bool = False,
) -> str:
    """Format action name with dot-separated convention.

    Args:
        base_name: Base name (typically MTAR filename without extension)
        running_idx: Running index (0, 1, 2, ... after filtering/sorting)
        h_idx: Header index (position in MTAR file table)
        d_idx: Data index (position sorted by file offset)
        verbose: Include h/d indices in name (suppressed when gani_name is resolved)
        is_motion_points: This is a motion points action
        is_layout: This is a layout track action
        gani_name: Resolved GANI name from hash dictionary (last path segment).
                   When provided, h/d indices are suppressed regardless of verbose.

    Returns:
        Formatted action name

    Examples (without gani_name):
        - Verbose animation: "player2.0.h340_d278.gani"
        - Simple animation: "player2.0.gani"
        - Verbose motion points: "player2.0.h340_d278.motionpoints.gani"
        - Layout track: "player2.layout.gani"
    Examples (with gani_name="walk_idle"):
        - Animation: "player2.walk_idle.0.gani"
        - Motion points: "player2.walk_idle.0.motionpoints.gani"
        - Shader nodes: "player2.walk_idle.0.shadernodes.gani"
    """
    return _format_gani_name(base_name, running_idx, h_idx, d_idx, verbose, is_motion_points, is_layout, gani_name, "gani", is_shader_nodes)


def format_strip_name(
    base_name: str,
    running_idx: int,
    h_idx: int,
    d_idx: int,
    verbose: bool,
    is_motion_points: bool = False,
    is_layout: bool = False,
    gani_name: Optional[str] = None,
    is_shader_nodes: bool = False,
) -> str:
    """Format NLA strip name with dot-separated convention.

    Args:
        base_name: Base name (typically MTAR filename without extension)
        running_idx: Running index (0, 1, 2, ... after filtering/sorting)
        h_idx: Header index (position in MTAR file table)
        d_idx: Data index (position sorted by file offset)
        verbose: Include h/d indices in name (suppressed when gani_name is resolved)
        is_motion_points: This is a motion points strip
        is_layout: This is a layout track strip
        gani_name: Resolved GANI name from hash dictionary (last path segment).
                   When provided, h/d indices are suppressed regardless of verbose.
        is_shader_nodes: This is a shader nodes strip (old-format only)

    Returns:
        Formatted strip name

    Examples (without gani_name):
        - Verbose animation: "player2.0.h340_d278.strip"
        - Simple animation: "player2.0.strip"
        - Verbose motion points: "player2.0.h340_d278.motionpoints.strip"
        - Layout track: "player2.layout.strip"
    Examples (with gani_name="walk_idle"):
        - Animation: "player2.walk_idle.0.strip"
        - Motion points: "player2.walk_idle.0.motionpoints.strip"
        - Shader nodes: "player2.walk_idle.0.shadernodes.strip"
    """
    return _format_gani_name(base_name, running_idx, h_idx, d_idx, verbose, is_motion_points, is_layout, gani_name, "strip", is_shader_nodes)


def apply_segment_suffixes(gani_tracks: 'List[TrackUnitWrapper]') -> 'List[TrackUnitWrapper]':
    """Apply _N suffix to TrackDataBlobWrapper names for multi-segment tracks.

    For any TrackUnitWrapper that contains more than one segment, appends
    '_{segment_index}' to each segment's TrackDataBlobWrapper.name for segment_index > 0.
    The first segment (index 0) is left unchanged, and single-segment tracks are also unchanged.

    This convention ensures cross-GANI consistency: a track is always `bone_XYZ` for segment 0,
    `bone_XYZ_1` for segment 1, etc., regardless of whether a GANI defines the track with 1
    or multiple segments.

    Must be called AFTER apply_track_naming() and _apply_stringlist_names() so
    that base names are fully resolved strings before the suffix is appended.

    This enables correct multi-segment track naming without requiring a FRIG file
    or rig unit type information, making segment names correct at read time.

    Args:
        gani_tracks: List of TrackUnitWrapper objects with fully resolved names

    Returns:
        The same list (mutated in-place)
    """
    for gani_track in gani_tracks:
        if len(gani_track.segments_track_data) <= 1:
            continue  # Single-segment or empty: no suffix needed
        for segment_blob in gani_track.segments_track_data:
            # Only apply suffix for segment index > 0; segment 0 is the base name
            if segment_blob.segment_index > 0:
                segment_blob.name = f"{segment_blob.name}_{segment_blob.segment_index}"
    return gani_tracks
