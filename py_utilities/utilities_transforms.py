"""
Transform utility functions for Metal Gear Solid V animation data.

This module contains coordinate transformation and directional calculation utilities
that work with standard Blender types (Vector, Quaternion) and Blender objects.
"""

import math
from typing import List, Optional, Tuple, Union, Dict

import bpy
from mathutils import Vector, Quaternion, Euler

from .utilities_logging import Debug

# Directional location (for IK up vector) #############################################################

def calculate_directional_location(bone_location: Vector, bone_rotation_quat: Quaternion, axis: str, distance: float) -> Vector:
    """Calculate a location by moving from a bone's location along one of its rotated axes.
    
    Args:
        bone_location: The bone's world space location (Vector)
        bone_rotation_quat: The bone's rotation as a quaternion (Quaternion)
        axis: Which local axis to follow ('X', 'Y', or 'Z')
        distance: How far to travel along the axis
        
    Returns:
        Vector: The final location in world space
    """
    # Define the local axis direction
    local_axis = {
        'X': Vector((1.0, 0.0, 0.0)),
        'Y': Vector((0.0, 1.0, 0.0)),
        'Z': Vector((0.0, 0.0, 1.0)),
    }[axis.upper()]
    
    # Rotate the local axis by the bone's rotation to get world-space direction
    world_direction = bone_rotation_quat @ local_axis
    
    # Move from bone location along the direction by the specified distance
    final_location = bone_location + (world_direction * distance)
    
    return final_location

def reverse_directional_location(location: Vector, base_location: Vector, axis: str) -> Quaternion:
    """Reverse the directional location calculation to get the original rotation.
    
    This is the inverse of calculate_directional_location.
    Given a target location and base location, calculate the quaternion that would
    produce that target when applied along the specified axis.
    
    Args:
        location: The target location (world space or custom space)
        base_location: The base bone location
        axis: Which local axis was used ('X', 'Y', or 'Z')
        
    Returns:
        Quaternion representing the rotation
    """
    # Calculate the direction vector from base to target
    direction = (location - base_location)
    
    # Normalize the direction vector
    if direction.length > 0.0001:
        direction = direction.normalized()
    else:
        # If locations are too close, use default orientation
        return Quaternion((1, 0, 0, 0))
    
    # Define the local axis that was used
    local_axis = {
        'X': Vector((1.0, 0.0, 0.0)),
        'Y': Vector((0.0, 1.0, 0.0)),
        'Z': Vector((0.0, 0.0, 1.0)),
    }[axis.upper()]
    
    # Calculate rotation from local axis to world direction
    # This is the quaternion that rotates local_axis to align with direction
    rotation_axis = local_axis.cross(direction)
    
    if rotation_axis.length < 0.0001:
        # Vectors are parallel or anti-parallel
        if local_axis.dot(direction) > 0:
            # Same direction - no rotation needed
            return Quaternion((1, 0, 0, 0))
        else:
            # Opposite direction - 180 degree rotation
            # Find a perpendicular axis for rotation
            if abs(local_axis.x) < 0.9:
                perpendicular = Vector((1, 0, 0)).cross(local_axis)
            else:
                perpendicular = Vector((0, 1, 0)).cross(local_axis)
            perpendicular.normalize()
            return Quaternion(perpendicular, math.pi)
    
    rotation_axis.normalize()
    angle = local_axis.angle(direction)
    
    return Quaternion(rotation_axis, angle)


# Rotation offset (to cover differences in the ref pose and axis usage of Kojima Productions' rig and our custom blender rig) #############################################################

def prepare_rotation_offset_quats(rotation_offset: Optional[List[dict]]) -> List[Quaternion]:
    """Convert rotation offset data to list of quaternions.
    
    Args:
        rotation_offset: List of rotation offset dictionaries or single offset dict
        
    Returns:
        List of quaternions representing the offsets
    """
    rotation_offset_quats: List[Quaternion] = []
    
    if rotation_offset:
        # Handle both single offset (old format) and list of offsets (new format)
        offset_list = rotation_offset if isinstance(rotation_offset, list) else [rotation_offset]
        
        for offset_data in offset_list:
            euler_degrees = offset_data['euler']
            order = offset_data['order']
            
            # Convert degrees to radians
            euler_radians = [math.radians(euler_degrees[0]), math.radians(euler_degrees[1]), math.radians(euler_degrees[2])]
            
            # Create Euler and convert to Quaternion
            euler_offset = Euler(euler_radians, order)
            rotation_offset_quats.append(euler_offset.to_quaternion())
            Debug.log(f"    Applying rotation offset transformation: ({euler_degrees[0]}, {euler_degrees[1]}, {euler_degrees[2]}) {order}")
    
    return rotation_offset_quats

def apply_reverse_transforms(quat: Quaternion, rotation_offset: Optional[List[dict]] = None,
                            rotation_axis_map: Optional[List[dict]] = None) -> Quaternion:
    """Apply transformations in reverse order to convert from Blender to Fox Engine format.
    
    During import:
    1. Quaternion is loaded from Fox Engine
    2. Axis mapping is applied (if present)
    3. Rotation offsets are applied (if present) as offset @ quat (offset first)
    
    During export (reverse):
    1. Remove rotation offsets (apply inverse, respecting original order)
    2. Remove axis mapping (apply inverse)
    3. Result is Fox Engine quaternion
    
    Args:
        quat: Blender quaternion
        rotation_offset: List of rotation offset dicts (applied in order during import)
        rotation_axis_map: Rotation axis mapping
        
    Returns:
        Fox Engine quaternion
    """
    result_quat = quat.copy()
    
    # Reverse rotation offsets (apply inverse in reverse order)
    if rotation_offset:
        for offset_data in reversed(rotation_offset):
            euler_degrees = offset_data['euler']
            order = offset_data['order']
            
            # Convert degrees to radians
            euler_radians = [math.radians(euler_degrees[0]),
                           math.radians(euler_degrees[1]),
                           math.radians(euler_degrees[2])]
            
            # Create Euler and convert to Quaternion
            euler_offset = Euler(euler_radians, order)
            offset_quat = euler_offset.to_quaternion()
            
            # Apply inverse based on original application order (both for ik-up and general cases)
            # as_ik_up import was: offset @ quat → reverse: quat @ offset.inverted()
            # Regular import was: quat @ offset → reverse: offset.inverted() @ quat
            result_quat = result_quat @ offset_quat.inverted()
    
    # Reverse axis mapping
    if rotation_axis_map:
        # Extract components
        w, x, y, z = result_quat.w, result_quat.x, result_quat.y, result_quat.z
        
        # Create component dictionary
        components = {'x': x, 'y': y, 'z': z}
        
        # Reverse the mapping - find which original axis mapped to each current axis
        # If map was [x->y, y->-x, z->z], reverse is [y->-x, x->y, z->z]
        # We need to find the inverse permutation
        
        # Build inverse mapping
        original_x_source = None
        original_y_source = None
        original_z_source = None
        
        for i, axis_map in enumerate(rotation_axis_map):
            target_axis = ['x', 'y', 'z'][i]
            source_axis = axis_map['axis']
            negate = axis_map['negate']
            
            if target_axis == 'x':
                original_x_source = (source_axis, negate)
            elif target_axis == 'y':
                original_y_source = (source_axis, negate)
            elif target_axis == 'z':
                original_z_source = (source_axis, negate)
        
        # Apply inverse mapping
        new_components = {'x': 0, 'y': 0, 'z': 0}
        
        # Find which current component should go to original x
        for current_axis, (source, negate) in [('x', original_x_source), ('y', original_y_source), ('z', original_z_source)]:
            if source and current_axis in ['x', 'y', 'z']:
                value = components[current_axis]
                if negate:
                    value = -value
                new_components[source] = value
        
        result_quat = Quaternion((w, new_components['x'], new_components['y'], new_components['z']))
    
    return result_quat


# Transform getter (to adhere to the mtar / gani format) #############################################################

def get_local_space_transform(obj: bpy.types.Object, bone_name: str, frame: int) -> Tuple[Vector, Quaternion]:
    """Get local bone space transform (relative to parent) for a bone at a specific frame.
    
    Args:
        obj: Armature object
        bone_name: Name of the bone
        frame: Frame number
        
    Returns:
        Tuple of (location, rotation_quaternion) in local bone space
    """
    bpy.context.scene.frame_set(frame)
    
    # Get raw keyframe/matrix_basis transform (before constraints, IK, etc.)
    pose_bone = obj.pose.bones[bone_name]
    
    # Use matrix_basis for local transform relative to parent bone
    # This gives the transform in the bone's local space
    location = pose_bone.matrix_basis.to_translation()
    rotation = pose_bone.matrix_basis.to_quaternion()
    
    return location, rotation

def get_world_space_transform(obj: bpy.types.Object, bone_name: str, frame: int,
                               space_bone: Optional[str] = None) -> Tuple[Vector, Quaternion]:
    """Get world space or custom space transform for a bone at a specific frame.
    
    Args:
        obj: Armature object
        bone_name: Name of the bone
        frame: Frame number
        space_bone: Optional bone name to use as custom space (instead of world space)
        
    Returns:
        Tuple of (location, rotation_quaternion) in the specified space
    """
    bpy.context.scene.frame_set(frame)
    
    # Get raw keyframe/matrix_basis transform (before constraints, IK, etc.)
    pose_bone = obj.pose.bones[bone_name]
    
    if space_bone:
        # Get transform in custom bone space (non-evaluated)
        space_pose_bone = obj.pose.bones[space_bone]
        # True world matrix for both bones
        bone_world_matrix = obj.matrix_world @ pose_bone.matrix
        space_world_matrix = obj.matrix_world @ space_pose_bone.matrix
        # Transform relative to custom space
        local_matrix = space_world_matrix.inverted() @ bone_world_matrix
        location = local_matrix.to_translation()
        rotation = local_matrix.to_quaternion()
    else:
        # True world space (non-evaluated) - accounts for armature's transform in scene
        world_matrix = obj.matrix_world @ pose_bone.matrix
        location = world_matrix.to_translation()
        rotation = world_matrix.to_quaternion()
    
    return location, rotation


# Coordinate space transformations (blender <-> fox engine) #############################################################

def apply_rotation_transforms(fox_quat: List[float], 
                             rotation_axis_map: Optional[List[Dict[str, Union[str, bool]]]] = None,
                             rotation_offset_quats: List[Quaternion] = None,
                             offset_first: bool = False) -> Quaternion:
    """Apply all rotation transformations to convert Fox quaternion to final Blender quaternion.
    
    Args:
        fox_quat: Fox Engine quaternion [x, y, z, w]
        rotation_axis_map: Optional axis remapping configuration
        rotation_offset_quats: List of rotation offset quaternions to apply
        offset_first: If True, apply offsets before the base rotation (for as_ik_up)
                     If False, apply offsets after the base rotation (for regular rotation)
        
    Returns:
        Final transformed Blender quaternion
    """
    # Convert quaternion from Fox Engine coordinate system to Blender
    quat: Quaternion = fox_to_blender_quaternion(fox_quat)
    
    # Apply rotation axis mapping if present (remaps xyz components)
    if rotation_axis_map:
        # Extract components (w, x, y, z)
        w, x, y, z = quat.w, quat.x, quat.y, quat.z
        
        # Create component dictionary for easy access
        components = {'x': x, 'y': y, 'z': z}
        
        # Remap components according to axis mapping
        new_x = components[rotation_axis_map[0]['axis']]
        if rotation_axis_map[0]['negate']:
            new_x = -new_x
        
        new_y = components[rotation_axis_map[1]['axis']]
        if rotation_axis_map[1]['negate']:
            new_y = -new_y
        
        new_z = components[rotation_axis_map[2]['axis']]
        if rotation_axis_map[2]['negate']:
            new_z = -new_z
        
        # Reconstruct quaternion with remapped axes
        quat = Quaternion((w, new_x, new_y, new_z))
    
    # Apply rotation offsets if present
    if rotation_offset_quats:
        for rotation_offset_quat in rotation_offset_quats:
            if offset_first:
                # For as_ik_up: apply offset before base rotation
                quat = rotation_offset_quat @ quat
            else:
                # For regular rotation: apply offset after base rotation
                quat = quat @ rotation_offset_quat
    
    return quat


def apply_rest_pose_correction_local(quat: Quaternion, map_r_dict: dict) -> Quaternion:
    """Apply local space rest pose correction using similarity transformation.
    
    For LOCAL space tracks, the rest pose correction transforms the rotation from
    a world-aligned coordinate frame into the bone's local rest pose frame using
    similarity transformation: R^(-1) @ P @ R
    
    This is mathematically equivalent to:
    - Rotating by rest_pose to align world to bone local frame
    - Applying the parent rotation
    - Rotating back by inverse rest_pose
    
    Args:
        quat: Input Blender quaternion (already converted from Fox format)
        map_r_dict: Dictionary with 'euler' [x, y, z] in degrees and 'order' string
        
    Returns:
        Transformed quaternion in bone's local rest pose frame
    """
    # Extract rest pose Euler angles from map_r
    euler_degrees = map_r_dict['euler']
    euler_order = map_r_dict['order'].upper()
    
    # Convert to radians and create Euler
    euler_radians = [math.radians(deg) for deg in euler_degrees]
    rest_pose_euler = Euler(euler_radians, euler_order)
    rest_pose_quat = rest_pose_euler.to_quaternion()
    
    # Apply similarity transformation: R^(-1) @ P @ R
    rest_pose_inv = rest_pose_quat.inverted()
    result = rest_pose_inv @ quat @ rest_pose_quat
    
    return result

def apply_rest_pose_correction_world(quat: Quaternion, offset_r_dict: dict) -> Quaternion:
    """Apply world space rest pose correction using simple quaternion multiplication.
    
    For WORLD space tracks (indicated by space_r=world), the offset_r parameter
    applies a simple rotation offset via quaternion multiplication.
    
    Args:
        quat: Input Blender quaternion (already converted from Fox format)
        offset_r_dict: Dictionary with 'euler' [x, y, z] in degrees and 'order' string
        
    Returns:
        Transformed quaternion with offset applied
    """
    # Extract offset Euler angles from offset_r
    euler_degrees = offset_r_dict['euler']
    euler_order = offset_r_dict['order'].upper()
    
    # Convert to radians and create Euler
    euler_radians = [math.radians(deg) for deg in euler_degrees]
    offset_euler = Euler(euler_radians, euler_order)
    offset_quat = offset_euler.to_quaternion()
    
    # Apply offset via simple post-multiplication
    result = quat @ offset_quat
    
    return result

def reverse_rest_pose_correction_local(quat: Quaternion, map_r_dict: dict) -> Quaternion:
    """Reverse local space rest pose correction for export.
    
    Inverts the similarity transformation applied during import:
    Original: R^(-1) @ P @ R
    Reverse: R @ P @ R^(-1)
    
    Args:
        quat: Blender quaternion in bone's local rest pose frame
        map_r_dict: Dictionary with 'euler' [x, y, z] in degrees and 'order' string
        
    Returns:
        Transformed quaternion in world-aligned frame
    """
    # Extract rest pose Euler angles from map_r
    euler_degrees = map_r_dict['euler']
    euler_order = map_r_dict['order'].upper()
    
    # Convert to radians and create Euler
    euler_radians = [math.radians(deg) for deg in euler_degrees]
    rest_pose_euler = Euler(euler_radians, euler_order)
    rest_pose_quat = rest_pose_euler.to_quaternion()
    
    # Apply reverse similarity transformation: R @ P @ R^(-1)
    rest_pose_inv = rest_pose_quat.inverted()
    result = rest_pose_quat @ quat @ rest_pose_inv
    
    return result

def reverse_rest_pose_correction_world(quat: Quaternion, offset_r_dict: dict) -> Quaternion:
    """Reverse world space rest pose correction for export.
    
    Inverts the simple offset multiplication applied during import:
    Original: P @ O
    Reverse: P @ O^(-1)
    
    Args:
        quat: Blender quaternion with offset applied
        offset_r_dict: Dictionary with 'euler' [x, y, z] in degrees and 'order' string
        
    Returns:
        Transformed quaternion with offset removed
    """
    # Extract offset Euler angles from offset_r
    euler_degrees = offset_r_dict['euler']
    euler_order = offset_r_dict['order'].upper()
    
    # Convert to radians and create Euler
    euler_radians = [math.radians(deg) for deg in euler_degrees]
    offset_euler = Euler(euler_radians, euler_order)
    offset_quat = offset_euler.to_quaternion()
    
    # Apply inverse offset
    offset_inv = offset_quat.inverted()
    result = quat @ offset_inv
    
    return result

def fox_to_blender_vector(fox_vec: List[float]) -> List[float]:
    """Convert a 3D vector from Fox Engine coordinate system to Blender.
    
    Fox Engine uses a left-handed Y-up system (Unity with inverted X axis).
    Blender uses a right-handed Z-up system.
    
    Conversion: Fox (-X, Y, Z) -> Blender (X, Z, Y)
    
    Args:
        fox_vec: Vector in Fox Engine coordinate system [x, y, z]
        
    Returns:
        Vector in Blender coordinate system [x, z, y]
    """
    return [-fox_vec[0], fox_vec[2], fox_vec[1]]

def blender_to_fox_vector(blender_vec: List[float]) -> List[float]:
    """Convert a 3D vector from Blender coordinate system to Fox Engine.
    
    Blender uses a right-handed Z-up system.
    Fox Engine uses a left-handed Y-up system (Unity with inverted X axis).
    
    Conversion: Blender (X, Z, Y) -> Fox (-X, Y, Z)
    
    Args:
        blender_vec: Vector in Blender coordinate system [x, z, y]
        
    Returns:
        Vector in Fox Engine coordinate system [x, y, z]
    """
    return [-blender_vec[0], blender_vec[2], blender_vec[1]]


def fox_to_blender_quaternion(fox_quat: List[float]) -> Quaternion:
    """
    Converts a quaternion from a Unity-like coordinate system with inverted X axis
    (referred to as 'fox') to Blender's coordinate system using quaternion operations only.

    Parameters:
        fox_quat (List[float]): Quaternion [x, y, z, w] from the 'fox' coordinate system.

    Returns:
        Quaternion: Converted quaternion for Blender.
    """
    # Convert input to Blender's Quaternion (w, x, y, z)
    q = Quaternion((fox_quat[3], fox_quat[0], fox_quat[1], fox_quat[2]))

    # Convert from Y-up (Fox) to Z-up (Blender) using -90° rotation around X
    q_convert = Quaternion((0.7071068, 0.7071068, 0, 0))  # -90° around X
    q_blender = q_convert @ q @ q_convert.conjugated()

    return q_blender

def blender_to_fox_quaternion(blender_quat: Quaternion) -> List[float]:
    """Convert a quaternion from Blender coordinate system to Fox Engine.
    
    Inverse of fox_to_blender_quaternion.
    
    Args:
        blender_quat: Quaternion in Blender coordinate system
        
    Returns:
        Quaternion [x, y, z, w] in Fox Engine coordinate system
    """
    # Step 1: Convert from Z-up (Blender) to Y-up (Fox) using +90° rotation around X
    q_convert = Quaternion((0.7071068, -0.7071068, 0, 0))  # +90° around X (inverse of -90°)
    q = q_convert @ blender_quat @ q_convert.conjugated()
    
    # Convert from Blender's Quaternion (w, x, y, z) to list [x, y, z, w]
    return [q.x, q.y, q.z, q.w]