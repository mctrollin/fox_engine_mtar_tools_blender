"""Utilities for working with Blender animation data.

This module contains helper functions for manipulating Blender actions,
FCurves, keyframes, and other animation-related structures.
"""
from typing import TYPE_CHECKING, Optional, Dict, List

import bpy

from .utilities_logging import Debug


# FCurve Cache Utilities #########################################################

def extract_bone_name_from_fcurve_path(data_path: str) -> Optional[str]:
    """Extract bone name from an fcurve data_path.
    
    Handles paths like:
    - pose.bones["BoneName"].rotation_quaternion
    - pose.bones["BoneName"].location
    
    Args:
        data_path: The fcurve's data_path attribute
        
    Returns:
        Bone name if path matches expected format, None otherwise
    """
    if not data_path or not data_path.startswith('pose.bones["'):
        return None
    
    # Extract bone name between pose.bones[" and "]
    try:
        start_idx = data_path.index('pose.bones["') + len('pose.bones["')
        end_idx = data_path.index('"]', start_idx)
        return data_path[start_idx:end_idx]
    except (ValueError, IndexError):
        return None


def extract_property_from_fcurve_path(data_path: str) -> Optional[str]:
    """Extract property name from an fcurve data_path.
    
    Handles paths like:
    - pose.bones["BoneName"].rotation_quaternion → "rotation_quaternion"
    - pose.bones["BoneName"].location → "location"
    
    Args:
        data_path: The fcurve's data_path attribute
        
    Returns:
        Property name if path matches expected format, None otherwise
    """
    if not data_path or '"].' not in data_path:
        return None
    
    # Extract property after "].
    try:
        property_start = data_path.rindex('"].') + 3
        return data_path[property_start:]
    except (ValueError, IndexError):
        return None


class FCurveCache:
    """Cache of FCurves indexed by bone name and property name.
    
    This eliminates the need to scan action.fcurves repeatedly for every bone.
    With many fcurves and many bones, this provides 20-100× speedup.
    
    Example usage:
        cache = FCurveCache.build(action)
        fcurves_for_rotation = cache.get_fcurves_for_bone(bone_name, 'rotation_quaternion')
    """
    
    def __init__(self, cache_dict: Optional[Dict[str, Dict[str, List['bpy.types.FCurve']]]] = None):
        """Initialize the FCurve cache.
        
        Args:
            cache_dict: Pre-built cache dictionary, or None for empty cache
        """
        self._cache = cache_dict if cache_dict else {}
    
    @classmethod
    def build(cls, action: 'bpy.types.Action') -> 'FCurveCache':
        """Build a cache of fcurves indexed by bone name and property name.
        
        Args:
            action: Blender action containing fcurves
            
        Returns:
            FCurveCache instance with all fcurves indexed
        """
        cache_dict: Dict[str, Dict[str, List['bpy.types.FCurve']]] = {}
        
        if not action or not action.fcurves:
            return cls(cache_dict)
        
        for fcurve in action.fcurves:
            bone_name = extract_bone_name_from_fcurve_path(fcurve.data_path)
            if not bone_name:
                continue
                
            property_name = extract_property_from_fcurve_path(fcurve.data_path)
            if not property_name:
                continue
            
            # Build nested dict structure
            if bone_name not in cache_dict:
                cache_dict[bone_name] = {}
            if property_name not in cache_dict[bone_name]:
                cache_dict[bone_name][property_name] = []
            
            cache_dict[bone_name][property_name].append(fcurve)
        
        return cls(cache_dict)
    
    def get_fcurves_for_bone(self, bone_name: str, property_name: str) -> List['bpy.types.FCurve']:
        """Get all fcurves for a specific bone and property.
        
        Args:
            bone_name: Name of the bone
            property_name: Name of the property (e.g., 'rotation_quaternion', 'location')
            
        Returns:
            List of matching fcurves (empty list if none found)
        """
        if bone_name not in self._cache:
            return []
        if property_name not in self._cache[bone_name]:
            return []
        return self._cache[bone_name][property_name]
    
    def has_bone(self, bone_name: str) -> bool:
        """Check if cache has fcurves for a bone.
        
        Args:
            bone_name: Name of the bone to check
            
        Returns:
            True if cache has entries for this bone
        """
        return bone_name in self._cache
    
    def get_bones(self) -> List[str]:
        """Get list of all bones in the cache.
        
        Returns:
            List of bone names
        """
        return list(self._cache.keys())
    
    def is_empty(self) -> bool:
        """Check if cache is empty.
        
        Returns:
            True if no bones are cached
        """
        return len(self._cache) == 0
    
    def to_dict(self) -> Dict[str, Dict[str, List['bpy.types.FCurve']]]:
        """Get the underlying cache dictionary.
        
        Useful for passing to functions that expect the raw dict format.
        
        Returns:
            The internal cache dictionary
        """
        return self._cache



def configure_action(action: 'bpy.types.Action',
                     frame_start: int = 0,
                     frame_end: int = 0,
                     use_fake_user: bool = True,
                     use_frame_range: bool = True) -> None:
    """Configure a Blender action with standard settings.
    
    Sets up the action's frame range, fake user flag, and other common properties.
    
    Args:
        action: The Blender action to configure
        frame_start: Start frame for the action's manual frame range
        frame_end: End frame for the action's manual frame range
        use_fake_user: Whether to enable fake user (prevents deletion when unused)
        use_frame_range: Whether to enable manual frame range
    """
    action.use_fake_user = use_fake_user
    
    if use_frame_range:
        action.frame_start = frame_start
        action.frame_end = frame_end
        action.use_frame_range = True

    if frame_end - frame_start <= 0:
        Debug.log_warning(f"Warning: Invalid frame range: '{frame_end - frame_start}'")


def add_dummy_keyframes_to_action(action: 'bpy.types.Action') -> None:
    """Add dummy location keyframes at frames -100 and -50 to the layout track action.
    
    This creates a baseline reference that prevents the action from being empty
    and establishes the frame range for the NLA strip. The dummy keyframes are
    added to a virtual bone called "dummy" (as pose.bones["dummy"].location)
    so the action is suitable to be applied on armature objects via NLA strips.
    
    Args:
        action: The layout track action to add keyframe to
    """
    Debug.log(f"Adding dummy location keyframes to layout action '{action.name}'")
    
    # Create a single dummy location track on a virtual bone named "dummy"
    data_path = 'pose.bones["dummy"].location'
    values = [0.0, 0.0, 0.0]

    # Ensure a group exists for the dummy bone so curves are organized
    group_name = "dummy"
    if group_name not in action.groups:
        action.groups.new(name=group_name)
    group = action.groups[group_name]
    
    # Create FCurve(s) for each component (X, Y, Z)
    for component_idx, value in enumerate(values):
        fcurve = action.fcurves.new(data_path=data_path, index=component_idx)
        fcurve.group = group
        # Add keyframes at frames -100 and -50
        keyframe_start = fcurve.keyframe_points.insert(frame=-100.0, value=value)
        keyframe_start.interpolation = 'LINEAR'
        keyframe_end = fcurve.keyframe_points.insert(frame=-50.0, value=value)
        keyframe_end.interpolation = 'LINEAR'
    
    Debug.log("    Added dummy location keyframes at frames -100 and -50: (0.0, 0.0, 0.0)")
