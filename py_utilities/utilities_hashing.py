"""
Utilities for handling Fox Engine hash values and rig type name mappings.
"""
import os
from typing import Optional, Dict

# Mapping of hash values to rig type names
RIG_TYPE_HASH_TO_NAME = {
    3552837520: "Root",
    2832076631: "Waist",
    538406145: "Spine",
    1382944449: "Chest",
    12750096: "Neck",
    1833209204: "Head",
    4069048318: "LArm",
    1626172505: "LHand",
    2063241216: "RArm",
    4246335734: "RHand",
    1587345382: "LLeg",
    2318116707: "LFoot",
    1917416821: "RLeg",
    3730058848: "RFoot",
    657792596: "LToe",
    2688182121: "RToe",
    3930921867: "LFingers",
    2376760760: "RFingers"
}

# Reverse mapping: rig type names to hash values
RIG_TYPE_NAME_TO_HASH = {name: hash_val for hash_val, name in RIG_TYPE_HASH_TO_NAME.items()}


def unhash_rig_type(hash_value: int) -> str:
    """Convert a rig type hash to its corresponding name.
    
    A rig here means one limb (e.g. shoulder, upper arm, lower arm), 
    all fingers of the hand or just a foot.
    It does not mean the same as a bone in blender.
    
    Args:
        hash_value: The integer hash value of the rig type name
        
    Returns:
        The resolved rig type name string, or None if not found in the mapping
    """
    return RIG_TYPE_HASH_TO_NAME.get(hash_value)


def hash_rig_type(name: str) -> int:
    """Convert a rig type name to its corresponding hash value.
    
    Args:
        name: The rig type name (e.g., "Root", "LArm", etc.)
        
    Returns:
        The hash value for the rig type, or None if not found in the mapping
    """
    return RIG_TYPE_NAME_TO_HASH.get(name)


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
