"""
FCurve processing utilities for decimation and cleaning operations.

This module provides functions to optimize fcurves for import/export workflows:
- Import: Decimate dense linear keyframes → sparse bezier curves
- Export: Bake and clean non-linear fcurves → optimized linear keyframes
"""
from contextlib import contextmanager
from typing import List, Set, Optional, Dict, Any

import bpy

from .utilities_logging import Debug
from .utilities_blender_animation import (
    iter_action_fcurves,
    action_has_fcurves,
    assign_action_to_datablock,
    remove_action_from_datablock,
    MTAR_ARMATURE_SLOT_NAME,
    get_fcurves_for_bones,
    is_fcurve_linear
    )

from ..py_foxwrap.foxwrap_metadata import extract_fox_bone_to_rig_unit_type_mapping



@contextmanager
def switch_context(area_type: str, obj: Optional[bpy.types.Object] = None, 
                   action: Optional[bpy.types.Action] = None):
    """Context manager to switch to a specific area type with optional object/action.
    
    Args:
        area_type: Type of area to switch to (e.g., 'GRAPH_EDITOR')
        obj: Optional object to set as active (needed for graph operators)
        action: Optional action to assign to object (needed for graph operators)
        
    Yields:
        Context with specified area type and object/action configured
    """
    
    target_area = None
    former_area_type = None
    former_mode = None
    former_active = bpy.context.view_layer.objects.active if bpy.context.view_layer else None
    former_action = None
    former_slot = None
    former_object_mode = bpy.context.mode if obj else None
    window = bpy.context.window

    # Try to find an area of the requested type (preserves existing editor if open)
    for area in window.screen.areas:
        if area.type == area_type:
            target_area = area
            break

    # If not found, override the first area
    if target_area is None:
        target_area = window.screen.areas[0]
        former_area_type = target_area.type
        target_area.type = area_type

    try:
        # Set active object and action if provided (needed for graph operators)
        if obj is not None:
            bpy.context.view_layer.objects.active = obj
            
            # Switch to POSE mode for armatures (required for pose bone FCurves)
            if obj.type == 'ARMATURE' and bpy.context.mode != 'POSE':
                bpy.ops.object.mode_set(mode='POSE')
            
            if action is not None:
                # Save current action and slot
                if obj.animation_data:
                    former_action = obj.animation_data.action
                    if hasattr(obj.animation_data, 'action_slot'):
                        former_slot = obj.animation_data.action_slot
                
                # Assign action using the proper slot-aware helper (Blender 4.4+ compatible)
                try:
                    assign_action_to_datablock(obj, action, slot_name=MTAR_ARMATURE_SLOT_NAME)
                except Exception as e:
                    # Fallback to direct assignment if slot helper fails
                    Debug.log_warning(f"Could not use slot-aware assignment: {e}")
                    if not obj.animation_data:
                        obj.animation_data_create()
                    obj.animation_data.action = action
                
                # Verify action is assigned; warn if assignment failed
                if not (obj.animation_data and obj.animation_data.action):
                    Debug.log_warning(f"No action assigned to object '{obj.name}' after attempted assignment")
        
        # Configure graph editor space
        if area_type == 'GRAPH_EDITOR':
            try:
                space = target_area.spaces.active
                # Ensure FCURVES mode (not DRIVERS)
                if hasattr(space, 'mode'):
                    former_mode = space.mode
                    space.mode = 'FCURVES'
            
            except (AttributeError, RuntimeError) as e:
                Debug.log_warning(f"Failed to configure graph editor space: {e}")
        
        with bpy.context.temp_override(window=window, area=target_area):
            yield
    finally:
        # Restore object action and slot
        if obj is not None and action is not None:
            if obj.animation_data:
                if former_action is not None:
                    obj.animation_data.action = former_action
                    # Restore slot if it was saved (Blender 4.4+)
                    if hasattr(obj.animation_data, 'action_slot') and former_slot is not None:
                        try:
                            obj.animation_data.action_slot = former_slot
                        except Exception:
                            pass
                else:
                    # No former action, clear it
                    try:
                        remove_action_from_datablock(obj)
                    except Exception:
                        obj.animation_data.action = None
        
        # Restore object mode
        if former_object_mode is not None and bpy.context.mode != former_object_mode:
            try:
                bpy.ops.object.mode_set(mode=former_object_mode)
            except RuntimeError:
                pass  # Mode switch may fail in some contexts
        
        # Restore active object
        if former_active is not None:
            bpy.context.view_layer.objects.active = former_active
        
        # Restore graph editor space state
        if former_mode is not None:
            try:
                space = target_area.spaces.active
                space.mode = former_mode
            except (AttributeError, RuntimeError):
                pass
        
        # Restore original area type if we changed it
        if former_area_type is not None:
            target_area.type = former_area_type


def debug_setup_graph_context_for_manual_test(armature_name: str, action_name: str):
    """Debug helper: Setup graph editor context for manual operator testing.
    
    Call this from Blender's Python console, then manually run the operator.
    
    Usage:
        from py_utilities.utilities_fcurve_processing import debug_setup_graph_context_for_manual_test
        debug_setup_graph_context_for_manual_test('rig.001', 'SKL_BODY_s0000_tpp')
        # Now manually open Graph Editor and run: bpy.ops.graph.decimate(mode='ERROR', error=0.01)
    
    Args:
        armature_name: Name of the armature object
        action_name: Name of the action to assign
    """
    # Get objects
    armature = bpy.data.objects.get(armature_name)
    action = bpy.data.actions.get(action_name)
    
    if not armature:
        Debug.log(f"ERROR: Armature '{armature_name}' not found")
        return
    if not action:
        Debug.log(f"ERROR: Action '{action_name}' not found")
        return
    
    Debug.log(f"Setting up context for armature '{armature_name}' with action '{action_name}'")
    
    # Make armature active
    bpy.context.view_layer.objects.active = armature
    Debug.log(f"✓ Set active object: {armature.name}")
    
    # Switch to POSE mode
    if bpy.context.mode != 'POSE':
        bpy.ops.object.mode_set(mode='POSE')
        Debug.log(f"✓ Switched to POSE mode")
    
    # Assign action using the same helper as the real code (slot-aware for Blender 4.4+/5.0+)
    if not armature.animation_data:
        armature.animation_data_create()
    
    Debug.log(f"Assigning action '{action.name}' using assign_action_to_datablock...")
    try:
        assign_action_to_datablock(armature, action, slot_name=MTAR_ARMATURE_SLOT_NAME)
        Debug.log(f"✓ Assigned action: {action.name} (using slot-aware helper)")
    except Exception as e:
        # Fallback to direct assignment if slot helper fails
        Debug.log(f"Warning: Could not use slot-aware assignment: {e}")
        armature.animation_data.action = action
        Debug.log(f"✓ Assigned action: {action.name} (fallback direct assignment)")
    
    Debug.log(f"  FCurves in action: {len(list(iter_action_fcurves(action)))}")
    
    # Find or create graph editor
    target_area = None
    for area in bpy.context.screen.areas:
        if area.type == 'GRAPH_EDITOR':
            target_area = area
            break
    
    if not target_area:
        # Override first area to graph editor
        target_area = bpy.context.screen.areas[0]
        target_area.type = 'GRAPH_EDITOR'
        Debug.log(f"✓ Created GRAPH_EDITOR in area")
    else:
        Debug.log(f"✓ Found existing GRAPH_EDITOR")
    
    # Configure graph editor space
    space = target_area.spaces.active
    if hasattr(space, 'mode'):
        space.mode = 'FCURVES'
        Debug.log(f"✓ Set graph editor mode to FCURVES")
    
    # Select some fcurves
    selected = 0
    for i, fcurve in enumerate(iter_action_fcurves(action)):
        fcurve.select = True
        selected += 1
        if i >= 4:  # Select first 5 for testing
            break
    Debug.log(f"✓ Selected {selected} FCurves")
    
    # Test operator poll
    can_run = bpy.ops.graph.decimate.poll()
    Debug.log(f"\n{'✓' if can_run else '✗'} Operator poll: {can_run}")
    
    if can_run:
        Debug.log("\n✓ SUCCESS - Context is ready!")
        Debug.log("Now manually run: bpy.ops.graph.decimate(mode='ERROR', error=0.01)")
    else:
        Debug.log("\n✗ FAILED - Operator poll returned False")
        Debug.log("Additional diagnostics:")
        Debug.log(f"  Context area: {bpy.context.area.type if bpy.context.area else 'NONE'}")
        Debug.log(f"  Context mode: {bpy.context.mode}")
        Debug.log(f"  Active object: {bpy.context.active_object.name if bpy.context.active_object else 'NONE'}")


def decimate_fcurves(action: bpy.types.Action, error_threshold: float,
                     bone_filter: Optional[Set[str]] = None,
                     obj: Optional[bpy.types.Object] = None) -> int:
    """Apply decimation to reduce keyframe density.
    
    Uses bpy.ops.graph.decimate(mode='ERROR') to remove redundant keyframes
    while staying within error threshold.
    
    Args:
        action: Action containing fcurves to decimate
        error_threshold: Maximum allowed error (0.0 to skip)
        bone_filter: Optional set of bone names to limit decimation to
        obj: Optional object with animation data (required for operator poll)
        
    Returns:
        Number of fcurves processed
    """
    if error_threshold <= 0.0 or not action or not action_has_fcurves(action):
        return 0

    # Get all fcurves once (version-safe)
    all_fcurves: List[bpy.types.FCurve] = list(iter_action_fcurves(action))

    # Decide which fcurves to process (filtered by bone if requested)
    if bone_filter:
        fcurves_to_process = get_fcurves_for_bones(action, bone_filter)
    else:
        fcurves_to_process = all_fcurves

    # Fallback if nothing to do
    if not fcurves_to_process:
        return 0

    process_keys: Set[tuple] = {(fc.data_path, fc.array_index) for fc in fcurves_to_process}

    try:
        with switch_context('GRAPH_EDITOR', obj=obj, action=action):
            # Select only fcurves to process using stable (data_path, array_index) key
            for fcurve in iter_action_fcurves(action):
                fcurve.select = (fcurve.data_path, fcurve.array_index) in process_keys

            selected_count = len([fcurve for fcurve in iter_action_fcurves(action) if fcurve.select])
            Debug.log(f"Decimating {selected_count} fcurves")

            # Apply decimation
            bpy.ops.graph.decimate(mode='ERROR', remove_error_margin=error_threshold)

            # Deselect all fcurves for clean UI state
            for fcurve in iter_action_fcurves(action):
                fcurve.select = False

            return len(fcurves_to_process)

    except (RuntimeError, AttributeError) as e:
        Debug.log_warning(f"FCurve decimation failed: {e}")
        return 0


def bake_action_fcurves(armature: bpy.types.Object, action: bpy.types.Action, 
                        frame_start: int, frame_end: int) -> None:
    """Bake fcurves in an action using LINEAR interpolation.
    
    Uses bpy.ops.anim.channels_bake() to sample animation at every frame.
    FCurves must be selected before calling this function.
    
    Args:
        armature: Armature object (must be active)
        action: Action to bake
        frame_start: First frame to bake
        frame_end: Last frame to bake
    """
    # Store original context state
    original_action: Optional[bpy.types.Action] = armature.animation_data.action if armature.animation_data else None
    original_mode: str = bpy.context.mode
    
    try:
        # Ensure armature has animation_data and run bake inside the same graph/action context
        if not armature.animation_data:
            armature.animation_data_create()
        with switch_context('GRAPH_EDITOR', obj=armature, action=action):
            # Bake selected FCurves with LINEAR interpolation, frame step of 1
            # FCurves must be selected by the caller before calling this function
            bpy.ops.anim.channels_bake(
                range=(frame_start, frame_end),
                step=1,
                remove_outside_range=True,
                interpolation_type='LIN',
                bake_modifiers=True
            )
    
    finally:
        # Restore original state
        if armature.animation_data and original_action:
            armature.animation_data.action = original_action
        
        if bpy.context.mode != original_mode:
            try:
                bpy.ops.object.mode_set(mode=original_mode)
            except RuntimeError:
                pass  # Mode switch may fail in some contexts


def clean_fcurves(action: bpy.types.Action, threshold: float,
                  bone_filter: Optional[Set[str]] = None,
                  fcurve_filter: Optional[Set[tuple]] = None,
                  obj: Optional[bpy.types.Object] = None) -> int:
    """Remove redundant keyframes using clean operation.
    
    Uses bpy.ops.graph.clean() to remove keyframes that don't contribute
    to the animation within the threshold.
    
    Args:
        action: Action containing fcurves to clean
        threshold: Clean threshold (0.0 to skip)
        bone_filter: Optional set of bone names to limit cleaning to (deprecated, use fcurve_filter)
        fcurve_filter: Optional set of (data_path, array_index) tuples to limit cleaning to
        obj: Optional object with animation data (required for operator poll)
        
    Returns:
        Number of fcurves processed
    """
    if threshold <= 0.0 or not action or not action_has_fcurves(action):
        return 0
    
    # Get all fcurves once (version-safe) for reuse in selection
    all_fcurves: List[bpy.types.FCurve] = list(iter_action_fcurves(action))
    
    # Filter fcurves: prefer fcurve_filter (per-FCurve), fall back to bone_filter (per-bone)
    if fcurve_filter:
        # Direct FCurve filtering by (data_path, array_index) tuples
        fcurves_to_process = [fc for fc in all_fcurves if (fc.data_path, fc.array_index) in fcurve_filter]
        fcurve_count: int = len(fcurves_to_process)
    elif bone_filter:
        # Legacy per-bone filtering
        fcurves_to_process = get_fcurves_for_bones(action, bone_filter)
        fcurve_count: int = len(fcurves_to_process)
    else:
        fcurves_to_process = all_fcurves
        fcurve_count: int = len(all_fcurves)
    
    if not fcurves_to_process:
        return 0
    
    # Build stable set of fcurve identifiers for selection
    process_keys: Set[tuple] = {(fc.data_path, fc.array_index) for fc in fcurves_to_process}
    
    try:
        with switch_context('GRAPH_EDITOR', obj=obj, action=action):
            # Select only fcurves to process using stable (data_path, array_index) key
            for fcurve in all_fcurves:
                fcurve.select = (fcurve.data_path, fcurve.array_index) in process_keys
            
            # Apply clean operation
            bpy.ops.graph.clean(threshold=threshold)
            
            # Deselect all fcurves for clean UI state
            for fcurve in all_fcurves:
                fcurve.select = False
            
            return fcurve_count
    
    except (RuntimeError, AttributeError) as e:
        Debug.log(f"Warning: FCurve clean failed: {e}")
        return 0


def process_import_fcurves(armature: bpy.types.Object,
                           decimate_error: float,
                           force_linear_types: str = '',
                           layout_action: Optional[bpy.types.Action] = None) -> Dict[str, int]:
    """Process imported armature fcurves with decimation.
    
    Workflow:
    1. All keyframes are already LINEAR from import
    2. Apply decimation to reduce keyframe density (filtered by track type)
    3. Result: Bezier curves with fewer keyframes
    
    Args:
        armature: Armature object with animation data
        decimate_error: Error threshold for decimation (0.0 = skip)
        force_linear_types: Comma-separated rig unit types to keep LINEAR (skip decimation)
        layout_action: Layout action to extract bone-to-rig-unit-type mapping for filtering
        
    Returns:
        Dictionary with results:
        - 'actions_processed': Number of actions processed
        - 'fcurves_decimated': Number of fcurves decimated
        - 'fcurves_skipped': Number of fcurves skipped (filtered by type)
    """
    if decimate_error <= 0.0:
        return {'actions_processed': 0, 'fcurves_decimated': 0, 'fcurves_skipped': 0}
    
    # Parse force_linear_types filter
    force_linear_set: Set[str] = set()
    if force_linear_types:
        for type_str in force_linear_types.split(','):
            type_str = type_str.strip().upper()
            if type_str:
                force_linear_set.add(type_str)
    
    # Build bone-to-rig-unit-type mapping from layout action if provided
    bone_to_type: Dict[str, Any] = {}
    if layout_action and force_linear_set:
        bone_to_type = extract_fox_bone_to_rig_unit_type_mapping(layout_action, {})
    
    # Process all actions on armature (NLA strips + active action)
    actions_to_process: List[bpy.types.Action] = []
    fcurves_decimated: int = 0
    fcurves_skipped: int = 0
    
    # Collect actions from NLA tracks (skip layout action — it has only metadata, no animation)
    if armature.animation_data and armature.animation_data.nla_tracks:
        for track in armature.animation_data.nla_tracks:
            for strip in track.strips:
                if strip.action and strip.action not in actions_to_process:
                    if layout_action and strip.action == layout_action:
                        continue
                    actions_to_process.append(strip.action)
    
    # Add active action (skip layout action)
    if armature.animation_data and armature.animation_data.action:
        active = armature.animation_data.action
        if active not in actions_to_process and active != layout_action:
            actions_to_process.append(active)
    
    # Process each action
    for action in actions_to_process:
        # Get all fcurves once (version-safe)
        action_fcurves: List[bpy.types.FCurve] = list(iter_action_fcurves(action))
        
        # Get all bone names with fcurves in this action
        all_bone_names: Set[str] = set()
        for fcurve in action_fcurves:
            # Extract bone name from data path like 'pose.bones["BoneName"].location'
            if 'pose.bones[' in fcurve.data_path:
                start = fcurve.data_path.find('["') + 2
                end = fcurve.data_path.find('"]', start)
                if start > 1 and end > start:
                    bone_name = fcurve.data_path[start:end]
                    all_bone_names.add(bone_name)
        
        Debug.log(f"Found {len(all_bone_names)} bones in action '{action.name}'")
        
        # Filter bones based on rig unit types
        bones_to_decimate = set(all_bone_names)
        bones_to_skip: Set[str] = set()
        
        if force_linear_set and bone_to_type:
            for bone_name in list(bones_to_decimate):
                rig_type = bone_to_type.get(bone_name)
                if rig_type and rig_type.name in force_linear_set:
                    bones_to_skip.add(bone_name)
                    bones_to_decimate.discard(bone_name)
        
        if bones_to_skip:
            Debug.log(f"Skipping {len(bones_to_skip)} bone(s) due to force-linear filter")
        
        # Apply decimation to allowed bones
        decimated = decimate_fcurves(action, decimate_error, bones_to_decimate, obj=armature)
        fcurves_decimated += decimated
        
        # Count skipped fcurves
        for bone_name in bones_to_skip:
            for fcurve in action_fcurves:
                if f'pose.bones["{bone_name}"]' in fcurve.data_path:
                    fcurves_skipped += 1
    
    return {
        'actions_processed': len(actions_to_process),
        'fcurves_decimated': fcurves_decimated,
        'fcurves_skipped': fcurves_skipped
    }


def process_export_fcurves(armature: bpy.types.Object,
                           clean_threshold: float) -> Dict[str, Any]:
    """Process active action fcurves for export.
    
    Workflow:
    1. Skip fcurves that are already LINEAR
    2. Bake non-linear fcurves to LINEAR (sample every frame)
    3. Apply clean to remove redundant keyframes
    
    Args:
        armature: Armature object with active action
        clean_threshold: Threshold for clean operation (0.0 = skip)
        
    Returns:
        Dictionary with results:
        - 'action': Processed action (may be a copy if non-linear fcurves found)
        - 'fcurves_baked': Number of fcurves baked from non-linear
        - 'fcurves_cleaned': Number of fcurves cleaned
        - 'fcurves_already_linear': Number of fcurves that were already linear
    """
    action = armature.animation_data.action if armature.animation_data else None
    
    if not action or not action_has_fcurves(action):
        return {
            'action': action,
            'fcurves_baked': 0,
            'fcurves_cleaned': 0,
            'fcurves_already_linear': 0
        }
    
    # Check if any fcurves need processing
    linear_count: int = 0
    nonlinear_fcurves: List[bpy.types.FCurve] = []
    baked_fcurve_keys: Set[tuple] = set()  # Track which FCurves were non-linear: (data_path, array_index)
    
    for fcurve in iter_action_fcurves(action):
        if is_fcurve_linear(fcurve):
            linear_count += 1
        else:
            nonlinear_fcurves.append(fcurve)
            # Track this FCurve's key for filtering
            baked_fcurve_keys.add((fcurve.data_path, fcurve.array_index))
    
    fcurves_baked: int = 0
    fcurves_cleaned: int = 0
    processed_action: bpy.types.Action = action
    
    # If we have non-linear fcurves, create a copy and bake them
    if nonlinear_fcurves:
        # Create a copy to avoid modifying original
        processed_action = action.copy()
        processed_action.name = f"{action.name}_export_temp"
        
        # Determine frame range from action
        frame_start = int(action.frame_range[0])
        frame_end = int(action.frame_range[1])
        
        # Assign copy to armature temporarily for baking
        armature.animation_data.action = processed_action
        
        try:
            # Select FCurves that need baking (non-linear ones)
            for fcurve in iter_action_fcurves(processed_action):
                fcurve.select = (fcurve.data_path, fcurve.array_index) in baked_fcurve_keys
            
            bake_action_fcurves(armature, processed_action, frame_start, frame_end)
            fcurves_baked = len(nonlinear_fcurves)
            
            # Deselect all FCurves
            for fcurve in iter_action_fcurves(processed_action):
                fcurve.select = False
        
        except Exception as e:
            bpy.data.actions.remove(processed_action)
            armature.animation_data.action = action
            raise e
        
        finally:
            # Always restore original action on armature
            armature.animation_data.action = action
    
    # Clean redundant keyframes only if we actually baked something
    if nonlinear_fcurves and clean_threshold > 0.0:
        fcurves_cleaned = clean_fcurves(processed_action, clean_threshold, fcurve_filter=baked_fcurve_keys, obj=armature)
    
    return {
        'action': processed_action,
        'fcurves_baked': fcurves_baked,
        'fcurves_cleaned': fcurves_cleaned,
        'fcurves_already_linear': linear_count
    }
