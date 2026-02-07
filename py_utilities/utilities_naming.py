"""
Naming utilities for MTAR action and NLA strip naming.

Provides consistent naming format across import/export workflows.
"""


def format_action_name(
    base_name: str,
    running_idx: int,
    h_idx: int,
    d_idx: int,
    verbose: bool,
    is_motion_points: bool = False,
    is_layout: bool = False
) -> str:
    """Format action name with new dot-separated convention.
    
    Args:
        base_name: Base name (typically MTAR filename without extension)
        running_idx: Running index (0, 1, 2, ... after filtering/sorting)
        h_idx: Header index (position in MTAR file table)
        d_idx: Data index (position sorted by file offset)
        verbose: Include h/d indices in name
        is_motion_points: This is a motion points action
        is_layout: This is a layout track action
    
    Returns:
        Formatted action name
        
    Examples:
        - Verbose animation: "player2.0.h340_d278.gani"
        - Simple animation: "player2.0.gani"
        - Verbose motion points: "player2.0.h340_d278.motionpoints.gani"
        - Layout track: "player2.layout.gani"
    """
    if is_layout:
        return f"{base_name}.layout.gani"
    
    # Build middle part with h/d indices if verbose
    if verbose:
        middle = f"{running_idx}.h{h_idx}_d{d_idx}"
    else:
        middle = f"{running_idx}"
    
    # Build type suffix
    if is_motion_points:
        type_suffix = ".motionpoints"
    else:
        type_suffix = ""
    
    return f"{base_name}.{middle}{type_suffix}.gani"


def format_strip_name(
    base_name: str,
    running_idx: int,
    h_idx: int,
    d_idx: int,
    verbose: bool,
    is_motion_points: bool = False,
    is_layout: bool = False
) -> str:
    """Format NLA strip name with new dot-separated convention.
    
    Args:
        base_name: Base name (typically MTAR filename without extension)
        running_idx: Running index (0, 1, 2, ... after filtering/sorting)
        h_idx: Header index (position in MTAR file table)
        d_idx: Data index (position sorted by file offset)
        verbose: Include h/d indices in name
        is_motion_points: This is a motion points strip
        is_layout: This is a layout track strip
    
    Returns:
        Formatted strip name
        
    Examples:
        - Verbose animation: "player2.0.h340_d278.strip"
        - Simple animation: "player2.0.strip"
        - Verbose motion points: "player2.0.h340_d278.motionpoints.strip"
        - Layout track: "player2.layout.strip"
    """
    if is_layout:
        return f"{base_name}.layout.strip"
    
    # Build middle part with h/d indices if verbose
    if verbose:
        middle = f"{running_idx}.h{h_idx}_d{d_idx}"
    else:
        middle = f"{running_idx}"
    
    # Build type suffix
    if is_motion_points:
        type_suffix = ".motionpoints"
    else:
        type_suffix = ""
    
    return f"{base_name}.{middle}{type_suffix}.strip"
