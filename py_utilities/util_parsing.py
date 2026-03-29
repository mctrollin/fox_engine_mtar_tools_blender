"""
Parsing utilities for user input.
"""
from typing import List, Set

from ..py_core.core_logging import Debug


# Precision used when serializing float values to metadata strings
# (e.g. motion event float_params, GANI params stored in Blender action custom properties).
# Python's default str(float) can produce excessive digits for single-precision floats
# (e.g. 0.30000001192092896). 6 significant digits matches IEEE 754 single-precision fidelity.
FLOAT_SERIALIZATION_PRECISION = 6


def format_float_for_metadata(value: float) -> str:
    """Format a float value for storage in a metadata string.

    Uses :data:`FLOAT_SERIALIZATION_PRECISION` significant digits to avoid the
    excessive decimal places produced by ``str()`` on single-precision floats.

    Args:
        value: Float value to format.

    Returns:
        Compact string representation, e.g. ``"0.3"`` instead of
        ``"0.30000001192092896"``.
    """
    s = f"{value:.{FLOAT_SERIALIZATION_PRECISION}g}"
    # To ensure parameter type parsing to work properly 
    # ensure the result looks like a float by including a decimal point if
    # the formatting produced an integer-like string.  Exponential notation
    # ("e"/"E") already implies a float.
    if "." not in s and "e" not in s and "E" not in s:
        s += ".0"
    return s


def parse_index_selection(selection_str: str, max_index: int) -> List[int]:
    """Parse index selection string with ranges, individual indices, and exclusions.
    
    Supports flexible syntax for selecting animation indices:
    - Ranges: "0-2" → [0, 1, 2]
    - Individual: "30,40" → [30, 40]
    - Exclusion: "!300" → exclude index 300
    - Exclusion ranges: "!400-500" → exclude indices 400-500
    - Combined: "0-2,30,40,!300,!400-500"
    
    Args:
        selection_str: User input string with comma-separated selections
        max_index: Maximum valid index (exclusive upper bound)
    
    Returns:
        Sorted list of selected indices
        
    Raises:
        ValueError: If syntax is invalid or indices out of range
    
    Examples:
        >>> parse_index_selection("0-2,5", 10)
        [0, 1, 2, 5]
        >>> parse_index_selection("0-5,!3", 10)
        [0, 1, 2, 4, 5]
        >>> parse_index_selection("!2-4", 10)
        [0, 1, 5, 6, 7, 8, 9]
        >>> parse_index_selection("", 10)
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    """
    # Empty string means select all
    if not selection_str.strip():
        return list(range(max_index))
    
    included: Set[int] = set()
    excluded: Set[int] = set()
    
    # Split by comma and process each part
    parts = [p.strip() for p in selection_str.split(',')]
    
    for part in parts:
        if not part:
            continue
        
        # Check for exclusion prefix
        is_exclusion = part.startswith('!')
        if is_exclusion:
            part = part[1:].strip()  # Remove ! prefix
        
        # Parse range or single index
        if '-' in part:
            # Range syntax (e.g., "0-5")
            try:
                start_str, end_str = part.split('-', 1)
                start = int(start_str.strip())
                end = int(end_str.strip())
                
                if start < 0 or end >= max_index:
                    Debug.raise_error(f"Range {start}-{end} out of bounds (valid range: 0-{max_index-1})", ValueError)
                if start > end:
                    Debug.raise_error(f"Invalid range: {start}-{end} (start > end)", ValueError)
                
                indices = set(range(start, end + 1))
            except ValueError as e:
                if "invalid literal" in str(e):
                    Debug.raise_error(f"Invalid range format: '{part}' (expected format: START-END)", ValueError)
                Debug.raise_error(str(e), ValueError)
        else:
            # Single index
            try:
                index = int(part.strip())
                if index < 0 or index >= max_index:
                    Debug.raise_error(f"Index {index} out of bounds (valid range: 0-{max_index-1})", ValueError)
                indices = {index}
            except ValueError as e:
                if "invalid literal" in str(e):
                    Debug.raise_error(f"Invalid index: '{part}' (expected integer)", ValueError)
                Debug.raise_error(str(e), ValueError)
        
        # Add to appropriate set
        if is_exclusion:
            excluded.update(indices)
        else:
            included.update(indices)
    
    # If only exclusions were specified, start with all indices
    if not included and excluded:
        included = set(range(max_index))
    
    # Apply exclusions
    result = included - excluded
    
    # Return empty list if nothing selected
    if not result:
        return []
    
    return sorted(result)


def parse_segment_suffix(fox_name: str) -> tuple[str, int]:
    """Split a fox bone/track name into base and segment index.

    Fox option-D multi-segment names append ``_N`` where N is non-negative.
    The main track (no explicit suffix) uses segment index -1.

    Args:
        fox_name: FOX track or bone name, e.g. "Root_0" or "Head"

    Returns:
        Tuple of (base_name, segment_index)
    """
    if '_' in fox_name:
        parts = fox_name.rsplit('_', 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[0], int(parts[1])

    return fox_name, -1
