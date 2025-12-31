"""
Parsing utilities for user input.
"""
from typing import List, Set


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
                    raise ValueError(f"Range {start}-{end} out of bounds (valid range: 0-{max_index-1})")
                if start > end:
                    raise ValueError(f"Invalid range: {start}-{end} (start > end)")
                
                indices = set(range(start, end + 1))
            except ValueError as e:
                if "invalid literal" in str(e):
                    raise ValueError(f"Invalid range format: '{part}' (expected format: START-END)")
                raise
        else:
            # Single index
            try:
                index = int(part.strip())
                if index < 0 or index >= max_index:
                    raise ValueError(f"Index {index} out of bounds (valid range: 0-{max_index-1})")
                indices = {index}
            except ValueError as e:
                if "invalid literal" in str(e):
                    raise ValueError(f"Invalid index: '{part}' (expected integer)")
                raise
        
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
