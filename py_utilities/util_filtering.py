import os
from typing import Dict, Optional, Set, Tuple

from ..py_core.core_logging import Debug

from . import util_hashing


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

