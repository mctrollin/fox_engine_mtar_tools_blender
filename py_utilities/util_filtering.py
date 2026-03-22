import os
from typing import Dict, Optional, Set, Tuple

from ..py_core.core_logging import Debug

from . import util_hashing
from . import util_parsing


def count_filter_file_valid_entries(filter_filepath: str) -> int:
    """Count non-empty, non-comment entries in a GANI filter file."""
    if not filter_filepath or not os.path.exists(filter_filepath):
        return 0

    count = 0
    try:
        with open(filter_filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                entry = line.strip()
                if not entry or entry.startswith('#'):
                    continue
                count += 1
    except Exception:
        count = 0
    return count


def prepare_gani_selection_indices(selection_str: str, max_count: int, index_mode: str):
    """Return interpreted index list from selection string.

    This uses parse_index_selection() from util_parsing and matches the old index-filter
    behavior (ranges, single indices, inclusions, exclusions).

    index_mode is kept for compatibility but not used.
    """
    selection_str = (selection_str or "").strip()
    if not selection_str:
        return []

    # parse_index_selection can raise ValueError for invalid syntax/range out of bounds
    return util_parsing.parse_index_selection(selection_str, max_count)


def normalize_gani_path(path_str: str) -> str:
    """Normalize a GANI path string for filter comparison.

    Normalization includes:
    - Replace backslashes with slashes
    - Strip whitespace
    - Remove trailing ".gani" extension
    - Remove trailing slash
    """
    if not path_str:
        return ''
    normalized = path_str.strip().replace('\\', '/').strip()
    if normalized.endswith('.gani'):
        normalized = normalized[:-5]
    normalized = normalized.rstrip('/')
    return normalized


def _parse_filter_index_line(line: str):
    """Parse a hN/dN index entry from a filter line, or return None."""
    if not line:
        return None

    stripped = line.strip()
    if stripped.lower().startswith('h') and stripped[1:].isdigit():
        return 'HEADER', int(stripped[1:])
    if stripped.lower().startswith('d') and stripped[1:].isdigit():
        return 'DATA', int(stripped[1:])
    return None


def load_gani_filter_list_with_indices(
    filter_path: str,
    gani_hash_dict: Optional[Dict[int, str]] = None,
) -> Tuple[Set[int], Set[int], Set[str], Set[str], Set[int], Set[int], Set[int], Set[int]]:
    """Load a GANI filter file containing hashes, paths or header/data indices."""
    allowed_hashes: Set[int] = set()
    excluded_hashes: Set[int] = set()
    allowed_paths: Set[str] = set()
    excluded_paths: Set[str] = set()
    allowed_header_indices: Set[int] = set()
    excluded_header_indices: Set[int] = set()
    allowed_data_indices: Set[int] = set()
    excluded_data_indices: Set[int] = set()

    if not filter_path:
        return (
            allowed_hashes,
            excluded_hashes,
            allowed_paths,
            excluded_paths,
            allowed_header_indices,
            excluded_header_indices,
            allowed_data_indices,
            excluded_data_indices,
        )

    if not os.path.exists(filter_path):
        Debug.log_warning(f"GANI filter file not found: {filter_path}")
        return (
            allowed_hashes,
            excluded_hashes,
            allowed_paths,
            excluded_paths,
            allowed_header_indices,
            excluded_header_indices,
            allowed_data_indices,
            excluded_data_indices,
        )

    reverse_hash_dict: Dict[str, int] = {}
    if gani_hash_dict:
        for hash_val, path_val in gani_hash_dict.items():
            reverse_hash_dict[normalize_gani_path(path_val)] = hash_val

    try:
        with open(filter_path, encoding='utf-8') as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith('#'):
                    continue

                exclude = False
                if line.startswith('!'):
                    exclude = True
                    line = line[1:].strip()
                    if not line:
                        continue

                parsed_index = _parse_filter_index_line(line)
                if parsed_index is not None:
                    mode, value = parsed_index
                    if mode == 'HEADER':
                        if exclude:
                            excluded_header_indices.add(value)
                        else:
                            allowed_header_indices.add(value)
                    else:
                        if exclude:
                            excluded_data_indices.add(value)
                        else:
                            allowed_data_indices.add(value)
                    continue

                target_hash = None
                target_path = None

                if util_hashing.is_gani_path_a_hash(line):
                    try:
                        target_hash = util_hashing.parse_gani_hash_str(line)
                    except ValueError:
                        continue
                else:
                    target_path = normalize_gani_path(line)

                if target_hash is not None:
                    if exclude:
                        excluded_hashes.add(target_hash)
                    else:
                        allowed_hashes.add(target_hash)
                    if gani_hash_dict and target_hash in gani_hash_dict:
                        resolved_path = normalize_gani_path(gani_hash_dict[target_hash])
                        if exclude:
                            excluded_paths.add(resolved_path)
                        else:
                            allowed_paths.add(resolved_path)
                    continue

                if target_path:
                    if exclude:
                        excluded_paths.add(target_path)
                    else:
                        allowed_paths.add(target_path)

                    if reverse_hash_dict and target_path in reverse_hash_dict:
                        mapped_hash = reverse_hash_dict[target_path]
                        if exclude:
                            excluded_hashes.add(mapped_hash)
                        else:
                            allowed_hashes.add(mapped_hash)

                    if reverse_hash_dict and target_path.endswith('.gani'):
                        alt_no_ext = normalize_gani_path(target_path)
                        if alt_no_ext in reverse_hash_dict:
                            mapped_hash = reverse_hash_dict[alt_no_ext]
                            if exclude:
                                excluded_hashes.add(mapped_hash)
                            else:
                                allowed_hashes.add(mapped_hash)

                    if reverse_hash_dict and not target_path.startswith('/Assets/'):
                        alt = '/Assets/' + target_path
                        if alt in reverse_hash_dict:
                            mapped_hash = reverse_hash_dict[alt]
                            if exclude:
                                excluded_hashes.add(mapped_hash)
                            else:
                                allowed_hashes.add(mapped_hash)

    except OSError as e:
        Debug.log_warning(f"Failed to read GANI filter file '{filter_path}': {e}")

    return (
        allowed_hashes,
        excluded_hashes,
        allowed_paths,
        excluded_paths,
        allowed_header_indices,
        excluded_header_indices,
        allowed_data_indices,
        excluded_data_indices,
    )


def load_gani_filter_list(filter_path: str, gani_hash_dict: Optional[Dict[int, str]] = None) -> Tuple[Set[int], Set[int], Set[str], Set[str]]:
    """Load a GANI filter file containing hashes or unhashed paths (one per line).

    Lines in the file can be:
    - decimal hash (e.g. 123456789)
    - hex hash (e.g. 0x1A2B3C4D)
    - GANI path (e.g. /Assets/mgo/motion/walk_idle or /Assets/mgo/motion/walk_idle.gani)

    Prefix a line with '!' to exclude that hash/path.

    If a hash dictionary (hash -> path) is provided, this function will try to
    resolve hashes into paths and paths into hashes to maximize matching ability.

    Args:
        filter_path: Path to the filter text file
        gani_hash_dict: Optional dictionary mapping 64-bit hashes to unhashed paths

    Returns:
        A tuple (allowed_hashes, excluded_hashes, allowed_paths, excluded_paths).
    """
    allowed_hashes: Set[int] = set()
    excluded_hashes: Set[int] = set()
    allowed_paths: Set[str] = set()
    excluded_paths: Set[str] = set()

    if not filter_path:
        return allowed_hashes, excluded_hashes, allowed_paths, excluded_paths

    if not os.path.exists(filter_path):
        Debug.log_warning(f"GANI filter file not found: {filter_path}")
        return allowed_hashes, excluded_hashes, allowed_paths, excluded_paths

    reverse_hash_dict: Dict[str, int] = {}
    if gani_hash_dict:
        for hash_val, path_val in gani_hash_dict.items():
            reverse_hash_dict[normalize_gani_path(path_val)] = hash_val

    try:
        with open(filter_path, encoding='utf-8') as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith('#'):
                    continue

                exclude = False
                if line.startswith('!'):
                    exclude = True
                    line = line[1:].strip()
                    if not line:
                        continue

                target_hash = None
                target_path = None

                if util_hashing.is_gani_path_a_hash(line):
                    try:
                        target_hash = util_hashing.parse_gani_hash_str(line)
                    except ValueError:
                        continue
                else:
                    target_path = normalize_gani_path(line)

                if target_hash is not None:
                    if exclude:
                        excluded_hashes.add(target_hash)
                    else:
                        allowed_hashes.add(target_hash)
                    if gani_hash_dict and target_hash in gani_hash_dict:
                        resolved_path = normalize_gani_path(gani_hash_dict[target_hash])
                        if exclude:
                            excluded_paths.add(resolved_path)
                        else:
                            allowed_paths.add(resolved_path)
                    continue

                if target_path:
                    if exclude:
                        excluded_paths.add(target_path)
                    else:
                        allowed_paths.add(target_path)

                    if reverse_hash_dict and target_path in reverse_hash_dict:
                        mapped_hash = reverse_hash_dict[target_path]
                        if exclude:
                            excluded_hashes.add(mapped_hash)
                        else:
                            allowed_hashes.add(mapped_hash)

                    if reverse_hash_dict and target_path.endswith('.gani'):
                        alt_no_ext = normalize_gani_path(target_path)
                        if alt_no_ext in reverse_hash_dict:
                            mapped_hash = reverse_hash_dict[alt_no_ext]
                            if exclude:
                                excluded_hashes.add(mapped_hash)
                            else:
                                allowed_hashes.add(mapped_hash)

                    if reverse_hash_dict and not target_path.startswith('/Assets/'):
                        alt = '/Assets/' + target_path
                        if alt in reverse_hash_dict:
                            mapped_hash = reverse_hash_dict[alt]
                            if exclude:
                                excluded_hashes.add(mapped_hash)
                            else:
                                allowed_hashes.add(mapped_hash)

    except OSError as e:
        Debug.log_warning(f"Failed to read GANI filter file '{filter_path}': {e}")

    return allowed_hashes, excluded_hashes, allowed_paths, excluded_paths


def is_gani_path_allowed(
    path_hash: Optional[int],
    path_str: Optional[str],
    allowed_hashes: set[int],
    excluded_hashes: set[int],
    allowed_paths: set[str],
    excluded_paths: set[str],
    gani_hash_dict: Optional[dict[int, str]] = None,
) -> bool:
    """Check whether a GANI path/hash entry is allowed by include/exclude rules."""
    if not allowed_hashes and not excluded_hashes and not allowed_paths and not excluded_paths:
        return True

    normalized_path = None
    if path_str:
        normalized_path = normalize_gani_path(path_str)

    resolved_path = None
    if path_hash is not None and gani_hash_dict:
        unhashed = util_hashing.unhash_gani_path(path_hash, gani_hash_dict)
        if unhashed is not None:
            resolved_path = normalize_gani_path(unhashed)

    if path_hash is not None and path_hash in excluded_hashes:
        return False

    if normalized_path and normalized_path in excluded_paths:
        return False

    if resolved_path and resolved_path in excluded_paths:
        return False

    if allowed_hashes or allowed_paths:
        if path_hash is not None and path_hash in allowed_hashes:
            return True
        if normalized_path and normalized_path in allowed_paths:
            return True
        if resolved_path and resolved_path in allowed_paths:
            return True
        return False

    return True
