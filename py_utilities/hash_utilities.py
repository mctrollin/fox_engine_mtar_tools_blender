"""
Utilities for handling Fox Engine hash values and rig type name mappings.
"""

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
