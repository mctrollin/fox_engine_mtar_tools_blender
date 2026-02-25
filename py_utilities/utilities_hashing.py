"""
Utilities for handling Fox Engine hash values and rig type name mappings.
"""
import os
from typing import Optional, Dict, Set

from .utilities_hashing_cityhash import strcode32
from .utilities_logging import Debug

# Unified StrCode32 cache: hash value → name string
_strcode32_cache: Dict[int, str] = {}

# Set of already-loaded dictionary absolute paths (to prevent redundant re-loading)
_loaded_dict_paths: Set[str] = set()


def load_strcode32_dictionary(dict_path: str) -> None:
    """Load a StrCode32 name dictionary into the unified cache.

    Reads plain name strings from a .txt file (one name per line, blank lines
    and lines starting with '#' are skipped), hashes each with strcode32(), and
    merges the results into the shared in-memory cache.  Calling this function
    more than once with the same path is a no-op.

    Args:
        dict_path: Absolute path to the dictionary text file.
    """
    abs_path = os.path.abspath(dict_path)
    if abs_path in _loaded_dict_paths:
        return

    if not os.path.exists(abs_path):
        Debug.log_warning(f"StrCode32 dictionary not found: {abs_path}")
        return

    try:
        with open(abs_path, encoding='utf-8') as f:
            names = [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith('#')
            ]
    except OSError as e:
        Debug.log_warning(f"Failed to read StrCode32 dictionary '{abs_path}': {e}")
        return

    loaded_count = 0
    for name in names:
        hash_val = strcode32(name, remove_extension=False)
        _strcode32_cache[hash_val] = name
        loaded_count += 1

    _loaded_dict_paths.add(abs_path)
    Debug.log(f"Loaded {loaded_count} StrCode32 entries from '{abs_path}'")


def lookup_strcode32(hash_val: int) -> Optional[str]:
    """Look up a StrCode32 hash value in the unified cache.

    The caller is responsible for loading the relevant dictionaries first via
    :func:`load_strcode32_dictionary`.

    Args:
        hash_val: The 32-bit StrCode32 hash to look up.

    Returns:
        The name string if found in the cache, ``None`` otherwise.
    """
    return _strcode32_cache.get(hash_val)


def _get_rig_dict_path() -> str:
    """Return the absolute path to dic/rig_dictionary.txt."""
    addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(addon_dir, 'dic', 'rig_dictionary.txt')


def unhash_rig_type(hash_value: int) -> Optional[str]:
    """Convert a rig type hash to its corresponding name.

    A rig here means one limb (e.g. shoulder, upper arm, lower arm),
    all fingers of the hand or just a foot.
    It does not mean the same as a bone in Blender.

    Lazily loads ``dic/rig_dictionary.txt`` on first call.

    Args:
        hash_value: The integer hash value of the rig type name.

    Returns:
        The resolved rig type name string, or ``None`` if not found.
    """
    load_strcode32_dictionary(_get_rig_dict_path())
    return lookup_strcode32(hash_value)


def _get_events_dict_path() -> str:
    """Return the absolute path to dic/events_dictionary.txt."""
    addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(addon_dir, 'dic', 'events_dictionary.txt')


def unhash_event_name(hash_value: int) -> Optional[str]:
    """Convert an event name hash to its corresponding string.

    Lazily loads ``dic/events_dictionary.txt`` on first call.

    Args:
        hash_value: The 32-bit StrCode32 hash of the event name.

    Returns:
        The event name string (e.g. ``"FX_CREATE_EFFECT_WITH_SKL"``), or
        ``None`` if the hash is not found in the dictionary.
    """
    load_strcode32_dictionary(_get_events_dict_path())
    return lookup_strcode32(hash_value)


def hash_rig_type(name: str) -> int:
    """Convert a rig type name to its corresponding hash value.

    Args:
        name: The rig type name (e.g., "Root", "RIG_SKL_010_LSHLD", etc.)

    Returns:
        The StrCode32 hash value for the name.
    """
    return strcode32(name, remove_extension=False)


def load_gani_hash_dictionary(dict_path: str) -> Dict[int, str]:
    """Load the GANI path hash dictionary from a text file.

    The file format is: <64-bit decimal hash>:<full asset path> — one entry per line.
    Lines that do not match this format are silently skipped.

    Args:
        dict_path: Absolute path to the mtar_hash_dictionary.txt file

    Returns:
        Dict mapping 64-bit hash integer to full path string (e.g. "/Assets/mgo/...")
    """
    result: Dict[int, str] = {}
    if not os.path.exists(dict_path):
        return result
    try:
        with open(dict_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or ':' not in line:
                    continue
                colon_idx = line.index(':')
                hash_str = line[:colon_idx]
                path_str = line[colon_idx + 1:]
                try:
                    result[int(hash_str)] = path_str
                except ValueError:
                    continue
    except OSError:
        pass
    return result


def unhash_gani_path(hash_int: int, loaded_dict: Dict[int, str]) -> Optional[str]:
    """Look up a 64-bit GANI path hash in the loaded dictionary.

    Args:
        hash_int: The 64-bit PathCode64 hash value
        loaded_dict: Dictionary loaded via load_gani_hash_dictionary()

    Returns:
        Full asset path string (e.g. "/Assets/mgo/...") or None if not found
    """
    return loaded_dict.get(hash_int)


def hash_gani_path_from_dict(path_str: str, loaded_dict: Dict[int, str]) -> Optional[int]:
    """Reverse-lookup a GANI path string to its 64-bit hash using the loaded dictionary.

    Accepts paths with or without the "/Assets/" prefix.

    Args:
        path_str: The asset path string to look up
        loaded_dict: Dictionary loaded via load_gani_hash_dictionary()

    Returns:
        The 64-bit hash integer, or None if not found
    """
    for hash_int, path in loaded_dict.items():
        if path == path_str:
            return hash_int
        # Also try matching without leading /Assets/ prefix
        if not path_str.startswith('/Assets/') and path == '/Assets/' + path_str:
            return hash_int
    return None


def parse_gani_hash_str(s: str) -> int:
    """Parse a hash string (decimal or hex with 0x prefix) to an integer.

    Args:
        s: Hash string to parse (plain decimal or 0x-prefixed hex)

    Returns:
        The parsed 64-bit hash integer

    Raises:
        ValueError: If the string is not a valid hash representation
    """
    s = s.strip()
    try:
        # int(s, 0) auto-detects base: 0x prefix → hex, otherwise → decimal
        return int(s, 0)
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Invalid hash string: '{s}'") from e


def is_gani_path_a_hash(s: str) -> bool:
    """Check whether a string represents a raw hash value (decimal or 0x-prefixed hex).

    Args:
        s: String to check

    Returns:
        True if the string is a plain decimal or hex integer, False otherwise
    """
    try:
        parse_gani_hash_str(s)
        return True
    except ValueError:
        return False
