"""
Naming utilities for MTAR action and NLA strip naming.

Provides consistent naming format across import/export workflows.
"""
from typing import Optional, Dict, Tuple

from .utilities_hashing import unhash_gani_path
from ..py_fox.fox_mtar_types import MtarTableList2


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
    ext: str
) -> str:
    """Shared formatting logic for action and strip names.

    Args:
        ext: File extension suffix without leading dot, e.g. "gani" or "strip"
    """
    if is_layout:
        return f"{base_name}.layout.{ext}"

    index = f"{running_idx}.h{h_idx}_d{d_idx}" if verbose else f"{running_idx}"
    type_suffix = ".motionpoints" if is_motion_points else ""
    gani_name = f".{gani_name}" if gani_name is not None else ""

    return f"{base_name}.{gani_name}.{index}{type_suffix}.{ext}"


def format_action_name(
    base_name: str,
    running_idx: int,
    h_idx: int,
    d_idx: int,
    verbose: bool,
    is_motion_points: bool = False,
    is_layout: bool = False,
    gani_name: Optional[str] = None
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
    """
    return _format_gani_name(base_name, running_idx, h_idx, d_idx, verbose, is_motion_points, is_layout, gani_name, "gani")


def format_strip_name(
    base_name: str,
    running_idx: int,
    h_idx: int,
    d_idx: int,
    verbose: bool,
    is_motion_points: bool = False,
    is_layout: bool = False,
    gani_name: Optional[str] = None
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
    """
    return _format_gani_name(base_name, running_idx, h_idx, d_idx, verbose, is_motion_points, is_layout, gani_name, "strip")
