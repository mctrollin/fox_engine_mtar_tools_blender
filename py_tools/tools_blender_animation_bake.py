"""
Armature baking utilities for Metal Gear Solid V animation tools.

Provides functionality to bake animated bones with visual transforms,
preserving keyframe timing while applying constraints.
"""
from typing import Set, Dict, Optional

import bpy

from ..py_utilities.utilities_logging import Debug, start_timer, stop_timer, update_progress_status
from ..py_utilities.utilities_blender_animation import assign_action_to_datablock, remove_action_from_datablock, action_has_fcurves, iter_action_fcurves, ensure_action_fcurve, remove_action_fcurve


def get_bones_with_keyframes(action: bpy.types.Action) -> Set[str]:
    """Get set of bone names that have keyframes in the given action.
    
    Args:
        action: Action to analyze
        
    Returns:
        Set of bone names with keyframes
    """
    bones_with_keyframes = set()
    
    if not action or not action_has_fcurves(action):
        return bones_with_keyframes
    
    for fcurve in iter_action_fcurves(action):
        data_path = fcurve.data_path
        
        # Check if this is a pose bone property
        if data_path.startswith('pose.bones["') or data_path.startswith("pose.bones['"):
            # Extract bone name from data_path
            # Format: pose.bones["BoneName"].property or pose.bones['BoneName'].property
            quote_char = '"' if '["' in data_path else "'"
            start = data_path.index('[' + quote_char) + 2
            end = data_path.index(quote_char + ']', start)
            bone_name = data_path[start:end]
            bones_with_keyframes.add(bone_name)
    
    return bones_with_keyframes


def get_keyframe_frames(action: bpy.types.Action, 
                        bone_names: Set[str]) -> Set[int]:
    """Get set of frame numbers that have keyframes for the specified bones.
    
    Args:
        action: Action to analyze
        bone_names: Set of bone names to check
        
    Returns:
        Set of frame numbers with keyframes
    """
    keyframe_frames = set()
    
    if not action or not action_has_fcurves(action):
        return keyframe_frames
    
    for fcurve in iter_action_fcurves(action):
        data_path = fcurve.data_path
        
        # Check if this fcurve belongs to one of our bones
        if data_path.startswith('pose.bones["') or data_path.startswith("pose.bones['"):
            quote_char = '"' if '["' in data_path else "'"
            start = data_path.index('[' + quote_char) + 2
            end = data_path.index(quote_char + ']', start)
            bone_name = data_path[start:end]
            
            if bone_name in bone_names:
                # Add all keyframe frames from this fcurve
                for keyframe in fcurve.keyframe_points:
                    keyframe_frames.add(int(keyframe.co[0]))
    
    return keyframe_frames


def get_keyframe_frames_per_fcurve(action: bpy.types.Action, 
                                    bone_names: Set[str]) -> Dict[str, Set[int]]:
    """Get frame numbers with keyframes for each fcurve data path.
    
    Args:
        action: Action to analyze
        bone_names: Set of bone names to check
        
    Returns:
        Dictionary mapping fcurve data_path to set of frame numbers with keyframes
    """
    fcurve_keyframes: Dict[str, Set[int]] = {}
    
    if not action or not action_has_fcurves(action):
        return fcurve_keyframes
    
    for fcurve in iter_action_fcurves(action):
        data_path = fcurve.data_path
        
        # Check if this fcurve belongs to one of our bones
        if data_path.startswith('pose.bones["') or data_path.startswith("pose.bones['"):
            quote_char = '"' if '["' in data_path else "'"
            start = data_path.index('[' + quote_char) + 2
            end = data_path.index(quote_char + ']', start)
            bone_name = data_path[start:end]
            
            if bone_name in bone_names:
                # Create a unique key for this fcurve (data_path + array_index)
                fcurve_key = f"{data_path}[{fcurve.array_index}]"
                
                # Collect all keyframe frames for this specific fcurve
                if fcurve_key not in fcurve_keyframes:
                    fcurve_keyframes[fcurve_key] = set()
                
                for keyframe in fcurve.keyframe_points:
                    fcurve_keyframes[fcurve_key].add(int(keyframe.co[0]))
    
    return fcurve_keyframes


def cleanup_baked_keyframes(action: bpy.types.Action,
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
    if not action or not action_has_fcurves(action):
        return 0
    
    fcurves_to_remove = []
    keyframes_removed_count = 0
    
    for fcurve in iter_action_fcurves(action):
        # Check if this fcurve belongs to a baked bone
        data_path = fcurve.data_path
        if data_path.startswith('pose.bones["') or data_path.startswith("pose.bones['"):
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
            remove_action_fcurve(action, fcurve)
        except Exception:
            # FCurve might have been already removed, skip
            pass
    
    return keyframes_removed_count

# TODO: this function is not really necessary if we say: baking animations always removes the original import armature
def copy_action_animation_data(source_action: bpy.types.Action, 
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
    for fcurve in iter_action_fcurves(source_action):
        try:
            new_fcurve = ensure_action_fcurve(
                target_action,
                data_path=fcurve.data_path,
                index=fcurve.array_index,
                datablock=datablock,
                action_group_name=(fcurve.group.name if fcurve.group else None),
                slot_name='mtar_import_armature'
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


def bake_armature_action(rig_armature: bpy.types.Object, 
                        action: Optional[bpy.types.Action] = None,
                        remove_constraints: bool = True,
                        create_new_action: bool = False,
                        new_action_suffix: str = "_baked",
                        nla_track: Optional[bpy.types.NlaTrack] = None,
                        source_armature: Optional[bpy.types.Object] = None,
                        interpolation_mode: str = 'BEZIER') -> Dict[str, any]:
    """Bake animated bones in an armature action with visual transforms.
    
    This function bakes only bones that have keyframes, only on frames where
    keyframes exist, using visual transforms (post-constraint evaluation).
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
        interpolation_mode: Interpolation mode to apply to all baked keyframes (BEZIER, LINEAR, CONSTANT)
        
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
        raise ValueError("Invalid armature object")
    
    # Get action to bake
    if action is None:
        if not rig_armature.animation_data or not rig_armature.animation_data.action:
            raise ValueError("Armature has no active action")
        action = rig_armature.animation_data.action
    
    # Ensure action is assigned to armature
    if not rig_armature.animation_data:
        rig_armature.animation_data_create()
    
    # Update status text without changing progress percentage
    update_progress_status(f"Baking: {action.name}")
        
    Debug.log(f"Baking action '{action.name}' for armature '{rig_armature.name}'")
    
    target_action = action
    
    # Create new action if requested
    if create_new_action:
        new_action_name = f"{action.name}{new_action_suffix}"
        target_action = bpy.data.actions.new(name=new_action_name)
        Debug.log(f"  Created new action '{new_action_name}'")
        
        # Copy animation data from original action to new action
        fcurves_copied = copy_action_animation_data(action, target_action, datablock=rig_armature)
        if fcurves_copied > 0:
            Debug.log(f"  Copied {fcurves_copied} fcurves from original action")
        
    assign_action_to_datablock(rig_armature, target_action)
    
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
    keyframe_frames = get_keyframe_frames(action, bones_with_keyframes)
    
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
    fcurve_keyframes = get_keyframe_frames_per_fcurve(action, bones_with_keyframes)
    
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
        assign_action_to_datablock(source_armature, action)
        Debug.log(f"  Assigned action '{action.name}' to source armature '{source_armature.name}' for constraint binding")

    
    # Store original NLA track mute state if provided
    original_track_mute_state = None
    if nla_track:
        original_track_mute_state = nla_track.mute
        nla_track.mute = True
        Debug.log(f"  Disabled NLA track '{nla_track.name}' during baking")
    
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
    
    # Select bones to bake
    bpy.ops.object.mode_set(mode='POSE')
    bpy.ops.pose.select_all(action='DESELECT')
    
    for bone_name in bones_with_keyframes:
        if bone_name in rig_armature.pose.bones:
            pose_bone = rig_armature.pose.bones[bone_name]
            # Blender 5.0+ selects via pose bone, older versions via data bone
            if hasattr(pose_bone, "select"):
                pose_bone.select = True
            else:
                pose_bone.bone.select = True
            
            # Log status using whichever property is available
            select_status = getattr(pose_bone, "select", getattr(pose_bone.bone, "select", False))
            Debug.log(f"  Selecting bone for baking: {bone_name} : {select_status}")
    
    try:
        Debug.log("  Starting bake operation...")

        start_timer("bpy.ops.nla.bake()")
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
        stop_timer("bpy.ops.nla.bake()")
        
        Debug.log("  Bake operation completed")
        
        # Clean up: Remove keyframes on non-keyframe frames
        # After baking, NLA creates keyframes on every frame in the range
        # We only want keyframes on original keyframe frames per fcurve
        Debug.log("  Cleaning up keyframes...")
        keyframes_removed_count = cleanup_baked_keyframes(target_action, fcurve_keyframes)
        
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

        remove_action_from_datablock(rig_armature)
        
        # Restore source armature's action if it was changed
        if source_armature and source_armature != rig_armature:
            if original_source_action:
                assign_action_to_datablock(source_armature, original_source_action)
            else:
                remove_action_from_datablock(source_armature)
            Debug.log(f"  Restored source armature '{source_armature.name}' action state")
        
        # Restore NLA track mute state
        if nla_track and original_track_mute_state is not None:
            nla_track.mute = original_track_mute_state
            Debug.log(f"  Re-enabled NLA track '{nla_track.name}'")
        
        Debug.log(f"Successfully baked action '{action.name}' -> '{target_action.name}'")
        
        # Set interpolation mode on all baked keyframes (after context restoration)
        Debug.log(f"  Setting interpolation mode to {interpolation_mode}...")
        interpolation_count = 0
        for fcurve in iter_action_fcurves(target_action):
            data_path = fcurve.data_path
            fcurve_modified = False
            # Only process pose bone fcurves
            if data_path.startswith('pose.bones["') or data_path.startswith("pose.bones['"):
                for keyframe in fcurve.keyframe_points:
                    if keyframe.interpolation != interpolation_mode:
                        keyframe.interpolation = interpolation_mode
                        # Set handle types to AUTO for bezier interpolation
                        if interpolation_mode == 'BEZIER':
                            keyframe.handle_left_type = 'AUTO'
                            keyframe.handle_right_type = 'AUTO'
                        fcurve_modified = True
                        interpolation_count += 1
            if fcurve_modified:
                fcurve.update()
        if interpolation_count > 0:
            Debug.log(f"  Set interpolation on {interpolation_count} keyframes")
        
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
            remove_action_from_datablock(rig_armature)
            # Restore source armature's action if it was changed
            if source_armature and source_armature != rig_armature:
                if original_source_action:
                    assign_action_to_datablock(source_armature, original_source_action)
                else:
                    remove_action_from_datablock(source_armature)
            # Restore NLA track mute state
            if nla_track and original_track_mute_state is not None:
                nla_track.mute = original_track_mute_state
            # Clean up new action if creation failed
            if create_new_action and target_action and target_action.users == 0:
                bpy.data.actions.remove(target_action)
        except Exception:
            # If restoration fails, swallow the error (main error is more important)
            pass
        
        raise RuntimeError(f"Failed to bake armature action: {str(e)}") from e


def bake_armature_nla_strips(rig_armature: bpy.types.Object,
                             remove_constraints: bool = True,
                             new_action_suffix: str = "_baked",
                             only_unmuted: bool = True,
                             source_armature: Optional[bpy.types.Object] = None,
                             create_new_action: bool = False,
                             interpolation_mode: str = 'BEZIER') -> Dict[str, any]:
    """Bake all NLA strips in an armature, creating new actions for each.
    
    This function iterates through all NLA strips and bakes each one into
    a new action with the specified suffix. Only processes unmuted strips by default.
    
    Args:
        armature: Armature object to bake
        remove_constraints: Whether to remove bone constraints after baking all strips
        new_action_suffix: Suffix to add to new action names
        only_unmuted: If True, only bake unmuted strips
        source_armature: Armature with animation data to bind constraints to (if different from armature being baked)
        create_new_action: If True, creates new actions instead of overriding existing ones
        interpolation_mode: Interpolation mode to apply to all baked keyframes (BEZIER, LINEAR, CONSTANT)
        
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
        raise ValueError("Invalid rig armature object")
    
    if not rig_armature.animation_data or not rig_armature.animation_data.nla_tracks:
        raise ValueError("Rig armature has no NLA tracks")
    
    Debug.log(f"Baking NLA strips for rig armature '{rig_armature.name}'")
    
    # Store original state
    original_action = rig_armature.animation_data.action if rig_armature.animation_data else None
    
    # Collect strips to bake
    strips_to_bake = []
    for track in rig_armature.animation_data.nla_tracks:
        if track.mute and only_unmuted:
            continue
        for strip in track.strips:
            if strip.mute and only_unmuted:
                continue
            if strip.action:
                strips_to_bake.append((track, strip, strip.action))
    
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
        
        # Calculate secondary progress within this strip batch (0.0 at start of first strip, approaching 1.0 at end of last)
        secondary = (idx - 1) / len(strips_to_bake) if len(strips_to_bake) > 1 else 0.0
        update_progress_status(f"Baking {idx}/{len(strips_to_bake)}: {strip.name}", secondary_progress=secondary)
            
        try:
            # Bake the action
            bake_result = bake_armature_action(
                rig_armature,
                action,
                remove_constraints=False,  # We'll handle this once at the end
                create_new_action=create_new_action,
                new_action_suffix=new_action_suffix,
                nla_track=track,
                source_armature=source_armature,
                interpolation_mode=interpolation_mode
            )
            
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
        assign_action_to_datablock(rig_armature, original_action)
    else:
        remove_action_from_datablock(rig_armature)
    
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




