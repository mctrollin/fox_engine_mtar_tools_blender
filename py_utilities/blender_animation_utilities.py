"""Utilities for working with Blender animation data.

This module contains helper functions for manipulating Blender actions,
FCurves, keyframes, and other animation-related structures.
"""
from typing import TYPE_CHECKING

from .logging_utilities import Debug

if TYPE_CHECKING:
    import bpy


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
        Debug.log(f"Warning: Invalid frame range: '{frame_end - frame_start}'")


def add_dummy_keyframes_to_action(action: 'bpy.types.Action') -> None:
    """Add dummy location keyframes at frames -100 and -50 to the layout track action.
    
    This creates a baseline reference that prevents the action from being empty
    and establishes the frame range for the NLA strip.
    
    Args:
        action: The layout track action to add keyframe to
    """
    Debug.log(f"Adding dummy location keyframes to layout action '{action.name}'")
    
    # Create a single dummy location track at origin
    data_path = 'location'
    values = [0.0, 0.0, 0.0]
    
    # Create FCurve(s) for each component (X, Y, Z)
    for component_idx, value in enumerate(values):
        fcurve = action.fcurves.new(data_path=data_path, index=component_idx)
        # Add keyframes at frames -100 and -50
        keyframe_start = fcurve.keyframe_points.insert(frame=-100.0, value=value)
        keyframe_start.interpolation = 'LINEAR'
        keyframe_end = fcurve.keyframe_points.insert(frame=-50.0, value=value)
        keyframe_end.interpolation = 'LINEAR'
    
    Debug.log("    Added dummy location keyframes at frames -100 and -50: (0.0, 0.0, 0.0)")
