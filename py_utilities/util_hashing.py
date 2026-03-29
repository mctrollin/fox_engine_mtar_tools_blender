"""
Utilities for handling Fox Engine hash values and rig type name mappings.
"""
import os
from typing import Optional, Dict, Set, Tuple

import bpy

from . import util_hashing_cityhash

from ..py_core.core_logging import Debug


# Unified StrCode32 cache: hash value → name string
_strcode32_cache: Dict[int, str] = {}

# Set of already-loaded dictionary absolute paths (to prevent redundant re-loading)
_loaded_dict_paths: Set[str] = set()

def get_dictionary_folders() -> Tuple[str, str]:
    """Return configured dictionary folders from addon prefs with fallback defaults."""
    default_path64 = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dic", "path64")
    default_str32 = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dic", "str32")

    try:
        addon = bpy.context.preferences.addons.get("fox_engine_mtar_tools_blender")
        if addon is not None:
            prefs = addon.preferences
            path64 = getattr(prefs, 'path64_dictionary_folder', '')
            str32 = getattr(prefs, 'str32_dictionary_folder', '')
            if path64:
                default_path64 = path64
            if str32:
                default_str32 = str32
    except Exception:
        pass

    return default_path64, default_str32

def get_path64_dir() -> Tuple[str, str]:
    """Returns the path to the path64 dictionary file. """
    path64_folder, _ = get_dictionary_folders()
    dict_path = os.path.join(path64_folder, 'mtar_dictionary.txt')
    return dict_path

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
        hash_val = util_hashing_cityhash.strcode32(name)
        _strcode32_cache[hash_val] = name
        loaded_count += 1

    _loaded_dict_paths.add(abs_path)
    Debug.log(f"Loaded {loaded_count} StrCode32 entries from '{abs_path}'")


def preload_strcode32_dictionaries() -> None:
    """Load every ``*.txt`` file found in ``dic/str32/`` into the unified cache.

    Scans the ``dic/str32/`` folder next to the addon root and calls
    :func:`load_strcode32_dictionary` for every ``.txt`` file found there.
    Already-loaded files are skipped automatically (no-op on repeated calls).
    The plugin does not need to know which individual files exist or how they
    are named — all files in the folder are treated as StrCode32 dictionaries.
    """
    str32_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'dic', 'str32')
    if not os.path.isdir(str32_dir):
        Debug.log_warning(f"StrCode32 dictionary folder not found: {str32_dir}")
        return
    for filename in sorted(os.listdir(str32_dir)):
        if filename.lower().endswith('.txt'):
            load_strcode32_dictionary(os.path.join(str32_dir, filename))


def lookup_strcode32(hash_val: int) -> Optional[str]:
    """Look up a StrCode32 hash value in the unified cache.

    Calls :func:`preload_strcode32_dictionaries` on the first invocation so
    that all ``dic/str32/*.txt`` files are available without any explicit
    preload call from the caller.

    Args:
        hash_val: The 32-bit StrCode32 hash to look up.

    Returns:
        The name string if found in the cache, ``None`` otherwise.
    """
    if not _loaded_dict_paths:
        preload_strcode32_dictionaries()
    return _strcode32_cache.get(hash_val)


def unhash_rig_type(hash_value: int) -> Optional[str]:
    """Convert a rig type hash to its corresponding name.

    A rig here means one limb (e.g. shoulder, upper arm, lower arm),
    all fingers of the hand or just a foot.
    It does not mean the same as a bone in Blender.

    All ``dic/str32/*.txt`` files are loaded automatically on the first lookup.

    Args:
        hash_value: The integer hash value of the rig type name.

    Returns:
        The resolved rig type name string, or ``None`` if not found.
    """
    return lookup_strcode32(hash_value)


def unhash_event_name(hash_value: int) -> Optional[str]:
    """Convert an event name hash to its corresponding string.

    All ``dic/str32/*.txt`` files are loaded automatically on the first lookup.

    Args:
        hash_value: The 32-bit StrCode32 hash of the event name.

    Returns:
        The event name string (e.g. ``"FX_CREATE_EFFECT_WITH_SKL"``), or
        ``None`` if the hash is not found in the dictionary.
    """
    return lookup_strcode32(hash_value)


def unhash_param_name(hash_value: int) -> Optional[str]:
    """Convert a Gani2 param name hash to its corresponding string.

    All ``dic/str32/*.txt`` files are loaded automatically on the first lookup.

    Args:
        hash_value: The 32-bit StrCode32 hash of the param name.

    Returns:
        The param name string (e.g. ``"SLOPE_ANGLE"``), or
        ``None`` if the hash is not found in the dictionary.
    """
    return lookup_strcode32(hash_value)


def unhash_gani_node(hash_value: int) -> Optional[str]:
    """Convert a FoxData node name hash to its corresponding string.

    Resolves hashes for old-format GANI node names such as ``ROOT``, ``MOTION``,
    ``UNIT``, ``SKL_LIST``, ``EVP``, etc.

    All ``dic/str32/*.txt`` files are loaded automatically on the first lookup.

    Args:
        hash_value: The 32-bit StrCode32 hash of the node name.

    Returns:
        The node name string (e.g. ``"UNIT"``), or ``None`` if not found.
    """
    return lookup_strcode32(hash_value)


def unhash_shader_prop(hash_value: int) -> Optional[str]:
    """Convert a SHADER child property hash to its corresponding name string.

    Resolves hashes for facial animation property nodes that are children of the
    SHADER node in old-format GANI files (e.g. ``TENSION_CHEEKL``).

    All ``dic/str32/*.txt`` files are loaded automatically on the first lookup.

    Args:
        hash_value: The 32-bit StrCode32 hash of the shader property name.

    Returns:
        The property name string (e.g. ``"TENSION_CHEEKL"``), or ``None`` if
        not found.
    """
    return lookup_strcode32(hash_value)


def hash_rig_type(name: str) -> int:
    """Convert a rig type name to its corresponding hash value.

    Args:
        name: The rig type name (e.g., "Root", "RIG_SKL_010_LSHLD", etc.)

    Returns:
        The StrCode32 hash value for the name.
    """
    return util_hashing_cityhash.strcode32(name)


def hash_or_parse_name(name: str) -> int:
    """Return an integer hash for *name*.

    If *name* is a plain decimal or ``0x``-prefixed hex string (as detected by
    :func:`is_hash_string`) the string is parsed as an integer and returned.
    Otherwise ``StrCode32(name)`` is computed.  This covers the common pattern
    of accepting either an unhashed bone name or a literal hash string when
    reading metadata.

    Args:
        name: Input string to convert.

    Returns:
        32-bit integer hash value.
    """
    s = name.strip()
    if is_hash_string(s):
        try:
            return parse_hash_string(s)
        except ValueError:
            # fall back to computing the hash normally
            return util_hashing_cityhash.strcode32(s)
    return util_hashing_cityhash.strcode32(s)


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


def parse_hash_string(s: str) -> int:
    """Parse a decimal or 0x-prefixed hex string to an integer hash value.

    Generic counterpart to ``parse_gani_hash_str`` for use outside the GANI
    path context (e.g. bone name hashes, StringData entries).

    Args:
        s: String to parse (plain decimal or 0x-prefixed hex)

    Returns:
        The parsed integer value

    Raises:
        ValueError: If the string is not a valid integer representation
    """
    s = s.strip()
    try:
        return int(s, 0)
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Invalid hash string: '{s}'") from e


def is_hash_string(s: str) -> bool:
    """Return True if *s* is a plain decimal or 0x-prefixed hex integer string.

    Used to distinguish raw hash fallback names (e.g. ``"4036034414"`` or
    ``"0xF08B256E"``) from real bone/node name strings.

    Args:
        s: String to check

    Returns:
        True if the string parses as an integer, False otherwise
    """
    try:
        parse_hash_string(s)
        return True
    except ValueError:
        return False


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




