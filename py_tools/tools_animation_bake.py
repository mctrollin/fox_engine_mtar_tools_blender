"""
Armature baking utilities for Metal Gear Solid V animation tools.

Provides functionality to bake animated bones with visual transforms,
preserving keyframe timing while applying constraints.
"""
from typing import Set, Dict, Optional, List, Any

import bpy

from ..py_core.core_logging import Debug

from ..py_utilities import util_blender_animation
from ..py_utilities import util_fcurve_processing

from ..py_foxwrap import fwrap_metadata


# Keyframe detection / frame collection ##############################################

def get_bones_with_keyframes(action: bpy.types.Action) -> Set[str]:
    """Get set of bone names that have keyframes in the given action.
    
    Args:
        action: Action to analyze
        
    Returns:
        Set of bone names with keyframes
    """
    bones_with_keyframes = set()
    
    if not action or not util_blender_animation.action_has_fcurves(action):
        return bones_with_keyframes
    
    for fcurve in util_blender_animation.iter_action_fcurves(action):
        data_path = fcurve.data_path
        
        # Check if this is a pose bone property
        if util_blender_animation.is_pose_bone_data_path(data_path):
            bone_name = util_blender_animation.extract_bone_name_from_data_path(data_path)
            if bone_name:
                bones_with_keyframes.add(bone_name)
    
    return bones_with_keyframes


def _get_keyframe_frames(action: bpy.types.Action, 
                        bone_names: Set[str]) -> Set[int]:
    """Get set of frame numbers that have keyframes for the specified bones.
    
    Args:
        action: Action to analyze
        bone_names: Set of bone names to check
        
    Returns:
        Set of frame numbers with keyframes
    """
    keyframe_frames = set()
    
    if not action or not util_blender_animation.action_has_fcurves(action):
        return keyframe_frames
    
    for fcurve in util_blender_animation.iter_action_fcurves(action):
        data_path = fcurve.data_path
        
        # Check if this fcurve belongs to one of our bones
        if util_blender_animation.is_pose_bone_data_path(data_path):
            bone_name = util_blender_animation.extract_bone_name_from_data_path(data_path)
            
            if bone_name and bone_name in bone_names:
                # Add all keyframe frames from this fcurve
                for keyframe in fcurve.keyframe_points:
                    keyframe_frames.add(int(keyframe.co[0]))
    
    return keyframe_frames


def _get_keyframe_frames_per_fcurve(action: bpy.types.Action, 
                                    bone_names: Set[str]) -> Dict[str, Set[int]]:
    """Get frame numbers with keyframes for each fcurve data path.
    
    Args:
        action: Action to analyze
        bone_names: Set of bone names to check
        
    Returns:
        Dictionary mapping fcurve data_path to set of frame numbers with keyframes
    """
    fcurve_keyframes: Dict[str, Set[int]] = {}
    
    if not action or not util_blender_animation.action_has_fcurves(action):
        return fcurve_keyframes
    
    for fcurve in util_blender_animation.iter_action_fcurves(action):
        data_path = fcurve.data_path
        
        # Check if this fcurve belongs to one of our bones
        if util_blender_animation.is_pose_bone_data_path(data_path):
            bone_name = util_blender_animation.extract_bone_name_from_data_path(data_path)
            
            if bone_name and bone_name in bone_names:
                # Create a unique key for this fcurve (data_path + array_index)
                fcurve_key = f"{data_path}[{fcurve.array_index}]"
                
                # Collect all keyframe frames for this specific fcurve
                if fcurve_key not in fcurve_keyframes:
                    fcurve_keyframes[fcurve_key] = set()
                
                for keyframe in fcurve.keyframe_points:
                    fcurve_keyframes[fcurve_key].add(int(keyframe.co[0]))
    
    return fcurve_keyframes


# Cleanup / copy action data ##############################################

def _cleanup_baked_keyframes(action: bpy.types.Action,
                            fcurve_keyframes: Dict[str, Set[int]]) -> int:
    """Remove keyframes on non-original frames after baking.
    
    After baking with NLA, keyframes are created on every frame in the range.
    This function removes excess keyframes, keeping only those on original keyframe frames.
    Also removes empty fcurves after cleanup.
    
    Args:
        action: Action to clean up
        fcurve_keyframes: Dictionary mapping fcurve keys to original keyframe frames
        
    Returns:
        Number of keyframes removed
    """
    if not action or not util_blender_animation.action_has_fcurves(action):
        return 0
    
    fcurves_to_remove = []
    keyframes_removed_count = 0
    
    for fcurve in util_blender_animation.iter_action_fcurves(action):
        # Check if this fcurve belongs to a baked bone
        data_path = fcurve.data_path
        if util_blender_animation.is_pose_bone_data_path(data_path):
            # Build fcurve key to look up original keyframes
            fcurve_key = f"{data_path}[{fcurve.array_index}]"
            
            # Get the original keyframe frames for this specific fcurve
            original_frames = fcurve_keyframes.get(fcurve_key, set())
            
            # If no original keyframes, mark fcurve for removal
            if not original_frames:
                fcurves_to_remove.append(fcurve)
                continue
            
            # Collect keyframe points to remove (frames not in this fcurve's original keyframes)
            keyframe_points_to_remove = []
            for kf_point in fcurve.keyframe_points:
                frame = int(kf_point.co[0])
                if frame not in original_frames:
                    keyframe_points_to_remove.append(kf_point)
            
            # Remove keyframes on non-original frames
            # Remove in reverse order to avoid index shifting issues
            for kf_point in reversed(keyframe_points_to_remove):
                try:
                    fcurve.keyframe_points.remove(kf_point, fast=True)
                    keyframes_removed_count += 1
                except (RuntimeError, IndexError):
                    # Keyframe might not exist, skip
                    pass
            
            # If all keyframes were removed, mark fcurve for removal
            if len(fcurve.keyframe_points) == 0:
                fcurves_to_remove.append(fcurve)
    
    # Remove empty fcurves
    if fcurves_to_remove:
        Debug.log(f"  Removing {len(fcurves_to_remove)} empty fcurves")
    for fcurve in fcurves_to_remove:
        try:
            util_blender_animation.remove_action_fcurve(action, fcurve)
        except Exception:
            # FCurve might have been already removed, skip
            pass
    
    return keyframes_removed_count

# TODO: this function is not really necessary if we say: baking animations always removes the original import armature
def _copy_action_animation_data(source_action: bpy.types.Action, 
                               target_action: bpy.types.Action,
                               datablock: Optional[bpy.types.ID] = None) -> int:
    """Copy all animation data (fcurves and keyframes) from source to target action.
    
    Copies all fcurves with their keyframe points, interpolation modes, and handle types.
    This is necessary as we can not let the blender bake operator generate a new action.
    Also transfers all custom properties from source to target action.
    
    Args:
        source_action: Action to copy from
        target_action: Action to copy to
        datablock: Optional datablock (armature/object) that owns the action (required for Blender 5)
        
    Returns:
        Number of fcurves copied
    """
    fcurves_copied = 0
    
    # Copy all fcurves from source action to target action
    for fcurve in util_blender_animation.iter_action_fcurves(source_action):
        try:
            new_fcurve = util_blender_animation.ensure_action_fcurve(
                target_action,
                data_path=fcurve.data_path,
                index=fcurve.array_index,
                datablock=datablock,
                action_group_name=(fcurve.group.name if fcurve.group else None),
                slot_name=util_blender_animation.MTAR_ARMATURE_SLOT_NAME
            )
        except Exception as e:
            Debug.log_warning(f"Could not create target fcurve '{fcurve.data_path}[{fcurve.array_index}]' on action '{getattr(target_action, 'name', '<unknown>')}': {e}")
            continue

        # Check if fcurve was successfully created (can be None in Blender 5)
        if new_fcurve is None:
            Debug.log_warning(f"ensure_action_fcurve returned None for '{fcurve.data_path}[{fcurve.array_index}]' on action '{getattr(target_action, 'name', '<unknown>')}'")
            continue

        # Copy keyframe points
        for keyframe in fcurve.keyframe_points:
            new_keyframe = new_fcurve.keyframe_points.insert(
                frame=keyframe.co[0],
                value=keyframe.co[1]
            )
            # Copy keyframe interpolation mode
            new_keyframe.interpolation = keyframe.interpolation
            # Copy handle types if available
            if hasattr(keyframe, 'handle_left') and hasattr(new_keyframe, 'handle_left'):
                new_keyframe.handle_left_type = keyframe.handle_left_type
                new_keyframe.handle_right_type = keyframe.handle_right_type
        
        fcurves_copied += 1
    
    # Transfer all custom properties from source to target action
    custom_props_transferred = 0
    if source_action.keys():
        for key in source_action.keys():
            # Skip internal Blender properties (start with '_')
            if not key.startswith('_'):
                try:
                    target_action[key] = source_action[key]
                    custom_props_transferred += 1
                except (TypeError, AttributeError):
                    # Some properties might not be transferable, skip them
                    pass
    
    if custom_props_transferred > 0:
        Debug.log(f"  Transferred {custom_props_transferred} custom properties")
    
    return fcurves_copied


# Constraint + Transform reset ##############################################

def remove_bone_constraints(armature: bpy.types.Object, bone_names: Set[str]) -> int:
    """Remove all constraints from specified bones.
    
    Args:
        armature: Armature object
        bone_names: Set of bone names to remove constraints from
        
    Returns:
        Number of constraints removed
    """
    if not armature or armature.type != 'ARMATURE':
        return 0
    
    constraint_count = 0
    
    for bone_name in bone_names:
        if bone_name in armature.pose.bones:
            pose_bone = armature.pose.bones[bone_name]
            
            # Remove all constraints
            while pose_bone.constraints:
                pose_bone.constraints.remove(pose_bone.constraints[0])
                constraint_count += 1
    
    return constraint_count


def _reset_baked_bone_transforms(armature: bpy.types.Object, bone_names: Set[str]) -> int:
    """Reset transforms on baked bones to rest pose and clear library overrides.
    
    Clears location, rotation, and scale on specified bones. For library-linked
    armatures with overrides, also removes transform property overrides.
    
    Args:
        armature: Armature object
        bone_names: Set of bone names to reset
        
    Returns:
        Number of bones reset
    """
    if not armature or armature.type != 'ARMATURE':
        return 0
    
    if not bone_names:
        return 0
    
    bones_reset = 0
    
    # Clear library overrides on transform properties if armature is overridden
    if hasattr(armature, 'override_library') and armature.override_library:
        Debug.log(f"  Clearing library overrides on transform properties for {len(bone_names)} bones")
        for bone_name in bone_names:
            if bone_name in armature.pose.bones:
                pose_bone = armature.pose.bones[bone_name]
                # Clear overrides on transform properties
                for prop in ['location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale']:
                    try:
                        pose_bone.property_unset(prop)
                    except (TypeError, AttributeError):
                        # Property might not be overridden or doesn't exist
                        pass
    
    # Ensure proper context: armature must be selected and active
    bpy.ops.object.select_all(action='DESELECT')
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    
    # Switch to POSE mode
    bpy.ops.object.mode_set(mode='POSE')
    
    # Deselect all bones first
    bpy.ops.pose.select_all(action='DESELECT')
    
    # Select bones to reset
    for bone_name in bone_names:
        if bone_name in armature.pose.bones:
            pose_bone = armature.pose.bones[bone_name]
            # Blender 5.0+ selects via pose bone, older versions via data bone
            if hasattr(pose_bone, "select"):
                pose_bone.select = True
            else:
                pose_bone.bone.select = True
            bones_reset += 1
    
    if bones_reset > 0:
        # Use Blender operators to clear transforms (handles rotation modes automatically)
        try:
            bpy.ops.pose.loc_clear()
            bpy.ops.pose.rot_clear()
            bpy.ops.pose.scale_clear()
            Debug.log(f"  Reset transforms on {bones_reset} baked bones")
        except Exception as e:
            Debug.log_warning(f"  Failed to reset transforms: {e}")
            return 0
    
    # Switch back to OBJECT mode
    bpy.ops.object.mode_set(mode='OBJECT')
    
    return bones_reset


def clear_armature_transforms(armature: bpy.types.Object) -> bool:
    """Clear all pose transforms from an armature (utility moved here).

    Copied from former location in blender_operators_import.py so that
    bake/cleanup helpers live in the same module and avoid circular imports.
    """
    try:
        # Make sure the armature is selected and in the scene
        for obj in bpy.context.scene.objects:
            obj.select_set(False)
        armature.select_set(True)
        bpy.context.view_layer.objects.active = armature
        
        # Enter pose mode
        bpy.ops.object.mode_set(mode='POSE')
        
        # Select all bones
        bpy.ops.pose.select_all(action='SELECT')
        
        # Clear all transforms
        bpy.ops.pose.transforms_clear()
        
        # Return to object mode
        bpy.ops.object.mode_set(mode='OBJECT')
        
        return True
    except Exception as e:  # noqa: E722
        Debug.log_warning(f"Failed to clear transforms from armature: {e}")
        return False


# Baking workflows ##############################################

def bake_armature_constraints_to_keyframes(rig_armature: bpy.types.Object, 
                        action: Optional[bpy.types.Action] = None,
                        remove_constraints: bool = True,
                        create_new_action: bool = False,
                        new_action_suffix: str = "_baked",
                        nla_track: Optional[bpy.types.NlaTrack] = None,
                        source_armature: Optional[bpy.types.Object] = None) -> Dict[str, any]:
    """Bake constraint-evaluated visual transforms from an armature action into keyframes.

    Uses bpy.ops.nla.bake with visual_keying=True to capture post-constraint transforms.
    Bakes only bones that have keyframes, only on frames where keyframes exist.
    The existing action is overridden with baked keyframes, or a new action
    is created if create_new_action is True.

    Args:
        rig_armature: Armature object to bake
        action: Action to bake (if None, uses active action)
        remove_constraints: Whether to remove bone constraints after baking
        create_new_action: If True, creates a new action instead of overriding
        new_action_suffix: Suffix to add to new action name
        nla_track: NLA track to disable during baking (if provided)
        source_armature: Armature with animation data to bind constraints to (if different from armature being baked)
        
    Returns:
        Dictionary with results:
        - 'success': bool
        - 'bones_baked': Set[str] - bone names that were baked
        - 'frames_baked': Set[int] - frame numbers that were baked
        - 'constraints_removed': int - number of constraints removed
        - 'message': str - result message
        - 'action': bpy.types.Action - the baked action (new or existing)
        
    Raises:
        ValueError: If armature is invalid or has no action
    """
    # Validate input
    if not rig_armature or rig_armature.type != 'ARMATURE':
        Debug.raise_error("Invalid armature object", ValueError)
    
    # Get action to bake
    if action is None:
        if not rig_armature.animation_data or not rig_armature.animation_data.action:
            Debug.raise_error("Armature has no active action", ValueError)
        action = rig_armature.animation_data.action
    
    # Ensure action is assigned to armature
    if not rig_armature.animation_data:
        rig_armature.animation_data_create()
    
    Debug.log(f"Baking action '{action.name}' for armature '{rig_armature.name}'")
    
    target_action = action

    # Create new action if requested
    if create_new_action:
        new_action_name = f"{action.name}{new_action_suffix}"
        target_action = bpy.data.actions.new(name=new_action_name)
        Debug.log(f"  Created new action '{new_action_name}'")
        
        # Copy animation data from original action to new action
        fcurves_copied = _copy_action_animation_data(action, target_action, datablock=rig_armature)
        if fcurves_copied > 0:
            Debug.log(f"  Copied {fcurves_copied} fcurves from original action")
        
    util_blender_animation.assign_action_to_datablock(rig_armature, target_action)
    
    # Get bones with keyframes
    bones_with_keyframes = get_bones_with_keyframes(action)
    
    if not bones_with_keyframes:
        Debug.log_warning(f"  No bones with keyframes found in action '{action.name}'")
        return {
            'success': False,
            'bones_baked': set(),
            'frames_baked': set(),
            'constraints_removed': 0,
            'message': 'No bones with keyframes found in action'
        }
    
    # Get frames with keyframes (global set for frame range)
    keyframe_frames = _get_keyframe_frames(action, bones_with_keyframes)
    
    if not keyframe_frames:
        Debug.log_warning(f"  No keyframes found in action '{action.name}'")
        return {
            'success': False,
            'bones_baked': set(),
            'frames_baked': set(),
            'constraints_removed': 0,
            'message': 'No keyframes found in action'
        }
    
    Debug.log(f"  Found {len(bones_with_keyframes)} bones with keyframes")
    
    # Get keyframes per fcurve for accurate cleanup
    fcurve_keyframes = _get_keyframe_frames_per_fcurve(action, bones_with_keyframes)
    
    # Store current context
    current_scene = bpy.context.scene
    current_frame = current_scene.frame_current
    
    # Store original state of source armature if provided (for constraint binding)
    original_source_action = None
    if source_armature and source_armature != rig_armature:
        # Ensure source armature has animation data
        if not source_armature.animation_data:
            source_armature.animation_data_create()
        original_source_action = source_armature.animation_data.action
        # Assign the same action to source armature for constraint evaluation (select Legacy Slot if available)
        util_blender_animation.assign_action_to_datablock(source_armature, action)
        Debug.log(f"  Assigned action '{action.name}' to source armature '{source_armature.name}' for constraint binding")

    
    # Select only the armature
    bpy.ops.object.select_all(action='DESELECT')
    rig_armature.select_set(True)
    bpy.context.view_layer.objects.active = rig_armature
    
    # Determine frame range from action's manual frame range if available
    if action.use_frame_range:
        frame_start = int(action.frame_start)
        frame_end = int(action.frame_end)
        Debug.log(f"  Using manual frame range: {frame_start} - {frame_end}")
    else:
        # Fall back to keyframe-based detection
        frame_start = min(keyframe_frames)
        frame_end = max(keyframe_frames)
        Debug.log(f"  Using keyframe-detected frame range: {frame_start} - {frame_end}")


    # If the whole action is in negative time (layout track etc.), skip baking it
    if frame_end <= 0:
        Debug.log(f"  Action '{action.name}' is in negative time range {frame_start} to {frame_end} (skipping)")
        try:
            if source_armature and source_armature != rig_armature and original_source_action is not None:
                util_blender_animation.assign_action_to_datablock(source_armature, original_source_action)
        except Exception:
            pass
        return {
            'success': False,
            'bones_baked': set(),
            'frames_baked': set(),
            'constraints_removed': 0,
            'message': 'Action in negative time range (skipped)'
        }
    
    # Select bones to bake
    bpy.ops.object.mode_set(mode='POSE')
    bpy.ops.pose.select_all(action='DESELECT')
    
    for blender_bone_name in bones_with_keyframes:
        if blender_bone_name in rig_armature.pose.bones:
            pose_bone = rig_armature.pose.bones[blender_bone_name]
            # Blender 5.0+ selects via pose bone, older versions via data bone
            if hasattr(pose_bone, "select"):
                pose_bone.select = True
            else:
                pose_bone.bone.select = True
            
            # Log status using whichever property is available
            select_status = getattr(pose_bone, "select", getattr(pose_bone.bone, "select", False))
            Debug.log(f"  Selecting bone for baking: {blender_bone_name} : {select_status}")
    
    # Ensure other armatures do not contribute NLA evaluation while baking.
    keep_armatures = [rig_armature]
    if source_armature and source_armature != rig_armature:
        keep_armatures.append(source_armature)

    with util_blender_animation.set_nla_solo(rig_armature, keep_track=nla_track, keep_armatures=keep_armatures):
        try:
            Debug.log("  Starting bake operation...")

            Debug.start_timer("bpy.ops.nla.bake()")
            # Bake the action
            # Note: bpy.ops.nla.bake requires specific parameters
            bpy.ops.nla.bake(
                frame_start=frame_start,
                frame_end=frame_end,
                step=1,  # We'll clean up non-keyframe frames afterward
                only_selected=True,  # Only bake selected bones
                visual_keying=True,  # Use visual transforms (post-constraint)
                clear_constraints=False,  # We'll handle constraint removal manually
                clear_parents=False,
                use_current_action=True,  # Override existing action
                clean_curves=False,  # We'll handle cleanup manually
                bake_types={'POSE'},  # Only bake pose (not object transforms)
                channel_types={'LOCATION', 'ROTATION'}
            )
            Debug.stop_timer("bpy.ops.nla.bake()")

            # Clean up: Remove keyframes on non-keyframe frames
            # After baking, NLA creates keyframes on every frame in the range
            # We only want keyframes on original keyframe frames per fcurve
            Debug.log("  Cleaning up keyframes...")
            keyframes_removed_count = _cleanup_baked_keyframes(target_action, fcurve_keyframes)

            if keyframes_removed_count > 0:
                Debug.log(f"  Removed {keyframes_removed_count} non-original keyframes")

            # Remove constraints if requested
            constraints_removed = 0
            if remove_constraints:
                constraints_removed = remove_bone_constraints(rig_armature, bones_with_keyframes)
                if constraints_removed > 0:
                    Debug.log(f"  Removed {constraints_removed} constraints")

            # Set manual frame range on target action
            target_action.use_frame_range = True
            target_action.frame_start = frame_start
            target_action.frame_end = frame_end
            Debug.log(f"  Set manual frame range on baked action: {frame_start} - {frame_end}")

            # Restore context
            bpy.ops.object.mode_set(mode='OBJECT')
            current_scene.frame_set(current_frame)

            util_blender_animation.remove_action_from_datablock(rig_armature)

            # Restore source armature's action if it was changed
            if source_armature and source_armature != rig_armature:
                if original_source_action:
                    util_blender_animation.assign_action_to_datablock(source_armature, original_source_action)
                else:
                    util_blender_animation.remove_action_from_datablock(source_armature)
                Debug.log(f"  Restored source armature '{source_armature.name}' action state")

            Debug.log(f"Successfully baked action '{action.name}' -> '{target_action.name}'")

            # Set all baked keyframes to LINEAR interpolation
            # (Decimation will convert to bezier later if enabled in import settings)
            Debug.log("  Setting interpolation mode to LINEAR...")

            interpolation_count = 0
            for fcurve in util_blender_animation.iter_action_fcurves(target_action):
                fcurve_modified = False

                # Only process pose bone fcurves
                if util_blender_animation.is_pose_bone_data_path(fcurve.data_path):
                    for keyframe in fcurve.keyframe_points:
                        if keyframe.interpolation != 'LINEAR':
                            keyframe.interpolation = 'LINEAR'
                            fcurve_modified = True
                            interpolation_count += 1

                if fcurve_modified:
                    fcurve.update()

            if interpolation_count > 0:
                Debug.log(f"  Set interpolation on {interpolation_count} keyframes")

            # Reset transforms on baked bones to prevent accumulation issues in subsequent bakes
            Debug.log("  Resetting transforms on baked bones...")
            bones_reset = _reset_baked_bone_transforms(rig_armature, bones_with_keyframes)
            if bones_reset > 0:
                Debug.log(f"  Reset complete for {bones_reset} bones")

            return {
                'success': True,
                'bones_baked': bones_with_keyframes,
                'frames_baked': keyframe_frames,
                'constraints_removed': constraints_removed,
                'action': target_action,
                'message': f'Successfully baked {len(bones_with_keyframes)} bone(s) on {len(keyframe_frames)} frame(s)'
            }

        except Exception as e:
            Debug.log_error(f"Failed to bake action '{action.name}'")
            # Restore context on error
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
                current_scene.frame_set(current_frame)
                util_blender_animation.remove_action_from_datablock(rig_armature)
                # Restore source armature's action if it was changed
                if source_armature and source_armature != rig_armature:
                    if original_source_action:
                        util_blender_animation.assign_action_to_datablock(source_armature, original_source_action)
                    else:
                        util_blender_animation.remove_action_from_datablock(source_armature)
                # Clean up new action if creation failed
                if create_new_action and target_action and target_action.users == 0:
                    bpy.data.actions.remove(target_action)
            except Exception:
                # If restoration fails, swallow the error (main error is more important)
                pass

            Debug.raise_error(f"Failed to bake armature action: {str(e)}", RuntimeError)


def _bake_armature_nla_strips_to_keyframes(rig_armature: bpy.types.Object,
                             create_new_action: bool = False,
                             new_action_suffix: str = "_baked",
                             source_armature: Optional[bpy.types.Object] = None,
                             remove_constraints: bool = True
                             ) -> Dict[str, any]:
    """Bake constraint-evaluated visual transforms from all NLA strips into keyframes.
    
    This function iterates through all NLA strips and bakes each one into
    a new action with the specified suffix. Only processes unmuted strips by default.
    Uses bpy.ops.nla.bake with visual_keying=True to capture post-constraint transforms.
    
    Args:
        armature: Armature object to bake
        remove_constraints: Whether to remove bone constraints after baking all strips
        new_action_suffix: Suffix to add to new action names
        source_armature: Armature with animation data to bind constraints to (if different from armature being baked)
        create_new_action: If True, creates new actions instead of overriding existing ones
        
    Returns:
        Dictionary with results:
        - 'success': bool
        - 'strips_baked': int - number of strips successfully baked
        - 'actions_created': List[bpy.types.Action] - list of new baked actions
        - 'failed_strips': List[str] - names of strips that failed to bake
        - 'constraints_removed': int - total constraints removed (if remove_constraints=True)
        - 'message': str - result message
        
    Raises:
        ValueError: If armature is invalid or has no NLA data
    """
    # Validate input
    if not rig_armature or rig_armature.type != 'ARMATURE':
        Debug.raise_error("Invalid rig armature object", ValueError)
    
    if not rig_armature.animation_data or not rig_armature.animation_data.nla_tracks:
        Debug.raise_error("Rig armature has no NLA tracks", ValueError)
    
    Debug.log(f"Baking NLA strips for rig armature '{rig_armature.name}'")
    
    # Store original state
    original_action = rig_armature.animation_data.action if rig_armature.animation_data else None
    
    # Collect strips to bake (only include actual GANI strips)
    strips_to_bake = []
    for track in rig_armature.animation_data.nla_tracks:
        # Skip muted tracks
        if getattr(track, 'mute', False):
            continue
        for strip in track.strips:
            if strip.action and util_blender_animation.is_relevant_strip(strip):
                strips_to_bake.append((track, strip, strip.action))
            else:
                Debug.log(f"  Skipping strip '{getattr(strip, 'name', '<unknown>')}' (not a GANI strip)")
    
    Debug.log(f"  Found {len(strips_to_bake)} strips to bake")
    
    if not strips_to_bake:
        return {
            'success': False,
            'strips_baked': 0,
            'actions_created': [],
            'failed_strips': [],
            'constraints_removed': 0,
            'message': 'No unmuted NLA strips found to bake'
        }
    
    # Bake each strip
    actions_created = []
    failed_strips = []
    all_baked_bones = set()
    strip_action_map = []  # List of (strip, new_action) tuples
    
    Debug.log(f"  Starting to bake {len(strips_to_bake)} strips...")
    
    for idx, (track, strip, action) in enumerate(strips_to_bake, 1):
        Debug.log(f"  Baking strip {idx}/{len(strips_to_bake)}: '{strip.name}' (action: '{action.name}')")

        # Calculate secondary progress within this strip batch (0.0 at start of first strip,
        # approaching 1.0 at end of last).  The main progress is driven externally, so we
        # only update status here.
        secondary = (idx - 1) / len(strips_to_bake) if len(strips_to_bake) > 1 else 0.0
        Debug.update_progress_status(f"Baking {idx}/{len(strips_to_bake)}: {strip.name}", secondary_progress=secondary)
            
        try:
            # Bake the action
            bake_result = bake_armature_constraints_to_keyframes(
                rig_armature=rig_armature,
                action=action,
                create_new_action=create_new_action,
                new_action_suffix=new_action_suffix,
                nla_track=track,
                source_armature=source_armature,
                remove_constraints=False  # We'll handle this once at the end
            )
            # record success/failure inside try so we can still run finally
            if bake_result['success']:
                actions_created.append(bake_result['action'])
                all_baked_bones.update(bake_result['bones_baked'])
                strip_action_map.append((strip, bake_result['action']))
            else:
                failed_strips.append(f"{track.name}/{strip.name}")
                Debug.log_warning(f"    Failed to bake strip '{strip.name}'")
        except Exception as e:
            failed_strips.append(f"{track.name}/{strip.name}: {str(e)}")
            Debug.log_error(f"    Exception while baking strip '{strip.name}': {str(e)}")
        finally:
            # no main progress updates here; the import/export operator sets the
            # overall progress based on strip count
            pass
    
    # done baking strips - move main progress to the end of the bake band so the
    # post‑processing phase can begin
    Debug.update_progress(76.0, "Bake complete, performing post-processing...")

    # Replace actions in NLA strips with baked versions
    Debug.log(f"  Replacing {len(strip_action_map)} strip actions with baked versions")
    for strip, baked_action in strip_action_map:
        strip.action = baked_action
    
    # Remove constraints once at the end if requested
    constraints_removed = 0
    if remove_constraints and all_baked_bones:
        constraints_removed = remove_bone_constraints(rig_armature, all_baked_bones)
        if constraints_removed > 0:
            Debug.log(f"  Removed {constraints_removed} constraints from {len(all_baked_bones)} bones")
    
    # Restore original action
    if original_action:
        util_blender_animation.assign_action_to_datablock(rig_armature, original_action)
    else:
        util_blender_animation.remove_action_from_datablock(rig_armature)
    
    success = len(actions_created) > 0
    message = f"Baked {len(actions_created)}/{len(strips_to_bake)} NLA strip(s)"
    if failed_strips:
        message += f", {len(failed_strips)} failed"
    
    if success:
        Debug.log(f"Successfully baked {len(actions_created)} NLA strips")
    else:
        Debug.log_warning("Failed to bake any NLA strips")
    
    if failed_strips:
        Debug.log_warning(f"  Failed strips: {', '.join(failed_strips)}")
    
    return {
        'success': success,
        'strips_baked': len(actions_created),
        'actions_created': actions_created,
        'failed_strips': failed_strips,
        'constraints_removed': constraints_removed,
        'message': message
    }


def bake_constraints_and_decimate_fcurves(
    rig_armature: bpy.types.Object,
    source_armature: Optional[bpy.types.Object] = None,
    create_new_action: bool = False,
    new_action_suffix: str = "_baked",
    remove_constraints: bool = True,
    delete_import_armature: bool = False,
    bake_decimate_fcurve_error: float = 0.0,
    decimate_skip_types: str = '',
    layout_action: Optional[bpy.types.Action] = None,
    blender_to_fox_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """High-level helper: bake constraint-evaluated transforms, then optionally decimate fcurves.

    Bakes the rig armature (NLA or active action) using visual-keying (post-constraint transforms),
    then optionally decimates the resulting linear keyframes by converting to Bezier fcurves 
    for better editability. Returns a bake_result-like dict.

    This consolidates the constraint-baking + decimation behavior so callers (import, debug)
    use the same implementation and avoid duplication.
    
    Args:
        rig_armature: Armature to bake constraint transforms from
        source_armature: Optional source armature for constraint binding (if different from rig_armature)
        create_new_action: If True, creates new actions instead of overriding
        new_action_suffix: Suffix for new action names
        remove_constraints: Whether to remove constraints after baking
        delete_import_armature: Whether to delete the temporary imported armature
        bake_decimate_fcurve_error: Decimation error threshold (0.0 = skip decimation)
        decimate_skip_types: Track types to skip during decimation (keep as linear)
        layout_action: Optional layout action to preserve
        
    Returns:
        Dictionary with results including 'success', 'message', 'actions_created', 
        'fcurves_decimated', and 'failed_strips'
    """
    result: Dict[str, Any] = {
        'success': False,
        'message': '',
        'actions_created': [],
        'fcurves_decimated': 0,
        'failed_strips': []
    }

    # Bake stage ------------------------
    try:
        # Prefer NLA strips when present
        if rig_armature.animation_data and rig_armature.animation_data.nla_tracks:
            Debug.start_timer("Bake (NLA strips)")
            try:
                bake_res = _bake_armature_nla_strips_to_keyframes(
                    rig_armature=rig_armature,
                    create_new_action=create_new_action,
                    new_action_suffix=new_action_suffix,
                    source_armature=source_armature,
                    remove_constraints=remove_constraints
                )
            finally:
                Debug.stop_timer("Bake (NLA strips)")

            result.update({
                'success': bake_res.get('success', False),
                'message': bake_res.get('message', ''),
                'actions_created': bake_res.get('actions_created', []),
                'failed_strips': bake_res.get('failed_strips', [])
            })
        elif rig_armature.animation_data and rig_armature.animation_data.action:
            Debug.start_timer("Bake (single action)")
            try:
                bake_res = bake_armature_constraints_to_keyframes(
                    rig_armature=rig_armature,
                    action=rig_armature.animation_data.action,
                    remove_constraints=remove_constraints,
                    create_new_action=create_new_action,
                    new_action_suffix=new_action_suffix,
                    source_armature=source_armature
                )
            finally:
                Debug.stop_timer("Bake (single action)")

            result.update({
                'success': bake_res.get('success', False),
                'message': bake_res.get('message', ''),
                'actions_created': [bake_res.get('action')] if bake_res.get('action') else []
            })
            # single-action bake counts as the bake step; bump main progress to wake UI
            Debug.update_progress(76.0, "Bake complete, performing post-processing...")
        else:
            result['success'] = False
            result['message'] = 'No NLA tracks or active action to bake'
            return result
    except Exception as e:
        Debug.log_warning(f"Bake failed: {e}")
        result['success'] = False
        result['message'] = f"Bake failed: {e}"
        return result

    # Decimation stage ------------------------
    if bake_decimate_fcurve_error > 0.0:
        Debug.start_timer("Decimation")
        Debug.update_progress_status("Decimating fcurves", secondary_progress=0.1)
        try:
            # Build explicit skip map from layout_action metadata and skip types.
            blender_bone_skip_map = fwrap_metadata.build_blender_bone_decimation_skip_map(
                all_blender_bone_names=set(rig_armature.data.bones.keys()) if rig_armature and rig_armature.data else set(),
                layout_action=layout_action,
                decimate_skip_types=decimate_skip_types,
                blender_to_fox_map=blender_to_fox_map,
                cache={},
            )
            dec_res = util_fcurve_processing.decimate_import_fcurves_to_bezier(
                armature=rig_armature,
                bake_decimate_fcurve_error=bake_decimate_fcurve_error,
                decimate_skip_types=decimate_skip_types,
                layout_action=layout_action,
                blender_to_fox_map=blender_to_fox_map,
                blender_bone_skip_map=blender_bone_skip_map,
            )
            result['fcurves_decimated'] = dec_res.get('fcurves_decimated', 0)
        except Exception as e:
            Debug.log_warning(f"Decimation failed: {e}")
        finally:
            Debug.stop_timer("Decimation")
            Debug.update_progress_status("Decimation complete", secondary_progress=0.3)
    else:
        result['fcurves_decimated'] = 0

    # Call internal handler to perform post-bake cleanup/reporting
    _handle_bake_result(result, rig_armature, source_armature, delete_import_armature, None)

    return result


# Post-bake cleanup/housekeeping ##############################################

def delete_imported_armature(imported_armature: Optional[bpy.types.Object], 
                            custom_rig: Optional[bpy.types.Object] = None) -> bool:
    """Delete an imported armature after bake if requested (utility moved here).

    Copied from former location in blender_operators_import.py to keep bake helpers
    together and avoid import cycles.
    """
    if not imported_armature or imported_armature == custom_rig:
        return True
    
    try:
        Debug.log(f"Deleting imported armature: {imported_armature.name}")
        for col in list(imported_armature.users_collection):
            col.objects.unlink(imported_armature)
        bpy.data.objects.remove(imported_armature, do_unlink=True)
        return True
    except Exception as e:  # noqa: E722
        Debug.log_warning(f"Failed to delete imported armature: {e}")
        return False


def _handle_bake_result(bake_result: dict,
                        custom_rig: bpy.types.Object,
                        imported_armature: Optional[bpy.types.Object],
                        delete_import_armature: bool = False,
                        operator: Optional[object] = None) -> None:
    """Internal helper to report and clean up after a bake operation.

    This replaces the previous `handle_bake_result` and lives inside the bake
    tool module so import operators can delegate cleanup without circular deps.
    """
    failed_strips: Optional[List[str]] = bake_result.get('failed_strips') if isinstance(bake_result, dict) else None

    # Prefer to report using the passed operator if available
    reporter = operator if operator is not None else None

    if bake_result.get('success'):
        Debug.report_and_log(reporter, 'INFO', f"Bake completed: {bake_result.get('message')}")
        if failed_strips:
            Debug.report_and_log(reporter, 'WARNING', f"{len(failed_strips)} strip(s) failed to bake: {', '.join(failed_strips)}")

        # clear rig transforms
        Debug.update_progress_status("Clearing rig transforms", secondary_progress=0.5)
        if clear_armature_transforms(custom_rig):
            Debug.report_and_log(reporter, 'INFO', "Cleared transforms from custom rig")
        else:
            Debug.report_and_log(reporter, 'WARNING', "Could not clear transforms from custom rig")

        # delete imported armature if requested
        Debug.update_progress_status("Deleting imported armature", secondary_progress=0.7)
        if delete_import_armature:
            if delete_imported_armature(imported_armature, custom_rig):
                Debug.report_and_log(reporter, 'INFO', "Deleted imported armature after bake")
            else:
                Debug.report_and_log(reporter, 'WARNING', "Could not delete imported armature")

        Debug.update_progress(100.0, "Post-processing complete")
    else:
        Debug.report_and_log(reporter, 'WARNING', f"Bake failed: {bake_result.get('message')}")
        Debug.update_progress(100.0, "Bake failed")
