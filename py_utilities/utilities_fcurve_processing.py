"""
FCurve processing utilities for decimation and cleaning operations.

This module provides functions to optimize fcurves for import/export workflows:
- Import: Decimate dense linear keyframes → sparse bezier curves
- Export: Bake and clean non-linear fcurves → optimized linear keyframes
"""
from typing import List, Set, Optional, Dict, Any

import bpy  # type: ignore[import]
from mathutils import Quaternion  # type: ignore[import]

from .utilities_logging import Debug
from .utilities_blender_animation import (
    iter_action_fcurves,
    action_has_fcurves,
    assign_action_to_datablock,
    get_fcurves_for_bones,
    is_fcurve_linear,
    is_pose_bone_data_path,
    extract_bone_name_from_data_path,
    MTAR_ARMATURE_SLOT_NAME,
    )
from .utilities_blender_state import switch_context

from ..py_foxwrap.foxwrap_metadata import extract_fox_bone_to_rig_unit_type_mapping



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
        Debug.log("✓ Switched to POSE mode")
    
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
        Debug.log("✓ Created GRAPH_EDITOR in area")
    else:
        Debug.log("✓ Found existing GRAPH_EDITOR")
    
    # Configure graph editor space
    space = target_area.spaces.active
    if hasattr(space, 'mode'):
        space.mode = 'FCURVES'
        Debug.log("✓ Set graph editor mode to FCURVES")
    
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


def _sample_fcurves_to_linear(armature: bpy.types.Object, action: bpy.types.Action,
                        frame_start: int, frame_end: int) -> None:
    """Sample fcurves in an action to LINEAR interpolation at every frame.

    Uses bpy.ops.anim.channels_bake() to sample animation at every frame.
    FCurves must be selected before calling this function.

    This is distinct from constraint-baking (bpy.ops.nla.bake): this function
    samples non-linear fcurves to create linear keyframes on every frame, which
    can then be cleaned (redundant keyframes removed).

    State (active action, object mode, area type) is fully restored by
    switch_context on exit — no manual cleanup needed here.

    Args:
        armature: Armature object (must be active)
        action: Action to bake
        frame_start: First frame to bake
        frame_end: Last frame to bake
    """
    if not armature.animation_data:
        armature.animation_data_create()
    with switch_context('GRAPH_EDITOR', obj=armature, action=action):
        # Bake selected FCurves with LINEAR interpolation, frame step of 1.
        # FCurves must be selected by the caller before calling this function.
        bpy.ops.anim.channels_bake(
            range=(frame_start, frame_end),
            step=1,
            remove_outside_range=True,
            interpolation_type='LIN',
            bake_modifiers=True
        )


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


def _find_keyframe_point(fcurve: bpy.types.FCurve, frame: float, eps: float = 1e-4):
    """Find a keyframe point at (or very near) a given frame.

    Blender's keyframe_points.find() can raise internal errors on some
    versions/contexts. Use a safe fallback scan instead.
    """
    # Using a small epsilon to tolerate float rounding differences
    for kp in fcurve.keyframe_points:
        if abs(kp.co[0] - frame) < eps:
            return kp
    return None


def _make_quaternion_fcurves_compatible(action: bpy.types.Action) -> int:
    """Stabilize quaternion sign across keyframes (prevent q -> -q flips).

    This function walks all "rotation_quaternion" fcurves in the action and
    ensures the quaternion stored at each keyframe stays in the same hemisphere
    by applying mathutils.Quaternion.make_compatible() sequentially.

    The function modifies keyframe values in-place.

    Returns:
        Number of quaternion tracks modified.
    """
    if not action or not action_has_fcurves(action):
        return 0

    # Group quaternion fcurves by data_path (bone/object path)
    quat_groups: Dict[str, List[bpy.types.FCurve]] = {}
    for fc in iter_action_fcurves(action):
        if fc.data_path.endswith("rotation_quaternion"):
            quat_groups.setdefault(fc.data_path, []).append(fc)

    modified_tracks = 0

    for _, fcurves in quat_groups.items():
        if len(fcurves) < 4:
            continue
        # Ensure stable x/y/z/w ordering
        fcurves_sorted = sorted(fcurves, key=lambda fc: fc.array_index)

        # Collect all keyframe frames across the 4 channels
        frames: Set[int] = set()
        for fc in fcurves_sorted:
            frames.update(int(kp.co[0]) for kp in fc.keyframe_points)

        if not frames:
            continue

        prev_quat: Optional[Quaternion] = None
        track_modified = False

        for frame in sorted(frames):
            vals = [fc.evaluate(frame) for fc in fcurves_sorted]
            quat = Quaternion((vals[3], vals[0], vals[1], vals[2]))
            orig_quat = quat.copy()

            if prev_quat is not None:
                quat.make_compatible(prev_quat)

            # Only update keyframes if the hemisphere changed
            if quat.dot(orig_quat) < 0.999999:
                # Write back corrected values where keyframes exist
                corrected_vals = [quat.x, quat.y, quat.z, quat.w]
                for idx, fc in enumerate(fcurves_sorted):
                    kp = _find_keyframe_point(fc, frame)
                    if kp is not None:
                        kp.co[1] = corrected_vals[idx]
                track_modified = True

            prev_quat = quat

        if track_modified:
            modified_tracks += 1

    return modified_tracks


def decimate_import_fcurves_to_bezier(armature: bpy.types.Object,
                           bake_decimate_fcurve_error: float,
                           decimate_skip_types: str = '',
                           layout_action: Optional[bpy.types.Action] = None,
                           blender_to_fox_map: Optional[Dict[str, str]] = None) -> Dict[str, int]:
    """Decimate imported fcurves by converting linear keyframes to Bezier curves.
    
    Import workflow:
    1. All keyframes are already LINEAR from constraint-baking
    2. Apply decimation to reduce keyframe density (filtered by track type)
    3. Result: Bezier curves with fewer keyframes for better editability
    
    Args:
        armature: Armature object with animation data
        bake_decimate_fcurve_error: Error threshold for decimation (0.0 = skip)
        decimate_skip_types: Comma-separated rig unit types to keep LINEAR (skip decimation)
        layout_action: Layout action to extract bone-to-rig-unit-type mapping for filtering
        blender_to_fox_map: Optional mapping from Blender bone names to Fox bone names.
            This is required when Blender bone names differ from Fox names (e.g. due to mapping files)
            so the correct rig unit types can be looked up.
    
    Returns:
        Dictionary with results:
        - 'actions_processed': Number of actions processed
        - 'fcurves_decimated': Number of fcurves decimated (converted to Bezier)
        - 'fcurves_skipped': Number of fcurves skipped (filtered by type, kept LINEAR)
    """
    if bake_decimate_fcurve_error <= 0.0:
        return {'actions_processed': 0, 'fcurves_decimated': 0, 'fcurves_skipped': 0}
    
    # Parse decimate_skip_types filter
    decimate_skip_set: Set[str] = set()
    if decimate_skip_types:
        for type_str in decimate_skip_types.split(','):
            type_str = type_str.strip().upper()
            if type_str:
                decimate_skip_set.add(type_str)
    
    # Build bone-to-rig-unit-type mapping from layout action if provided
    fox_bone_to_type: Dict[str, Any] = {}
    if layout_action and decimate_skip_set:
        fox_bone_to_type = extract_fox_bone_to_rig_unit_type_mapping(layout_action, {})
    
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
    total_actions = len(actions_to_process)
    for idx, action in enumerate(actions_to_process, start=1):
        # update secondary progress to reflect position in the action list
        Debug.update_progress_status(f"Decimating {idx}/{total_actions}: {action.name}", secondary_progress=(idx-1)/total_actions)
        # Get all fcurves once (version-safe)
        action_fcurves: List[bpy.types.FCurve] = list(iter_action_fcurves(action))
        
        # Get all bone names with fcurves in this action
        all_blender_bone_names: Set[str] = set()
        for fcurve in action_fcurves:
            # Extract bone name from data path like 'pose.bones["BoneName"].location'
            if is_pose_bone_data_path(fcurve.data_path):
                blender_bone_name = extract_bone_name_from_data_path(fcurve.data_path)
                if blender_bone_name:
                    all_blender_bone_names.add(blender_bone_name)
        
        Debug.log(f"Found {len(all_blender_bone_names)} bones in action '{action.name}'")
        
        # Filter bones based on rig unit types
        blender_bones_to_decimate = set(all_blender_bone_names)
        blender_bones_to_skip: Set[str] = set()
        
        if decimate_skip_set and fox_bone_to_type:
            for blender_bone_name in list(blender_bones_to_decimate):
                fox_bone_name = (
                    blender_to_fox_map.get(blender_bone_name, blender_bone_name)
                    if blender_to_fox_map
                    else blender_bone_name
                )
                rig_type = fox_bone_to_type.get(fox_bone_name)
                if rig_type and rig_type.name in decimate_skip_set:
                    blender_bones_to_skip.add(blender_bone_name)
                    blender_bones_to_decimate.discard(blender_bone_name)
        
        if blender_bones_to_skip:
            Debug.log(f"Skipping {len(blender_bones_to_skip)} bone(s) due to decimation skip filter")
        
        # Apply decimation to allowed bones
        decimated = decimate_fcurves(action, bake_decimate_fcurve_error, blender_bones_to_decimate, obj=armature)
        fcurves_decimated += decimated
        
        # Count skipped fcurves
        for blender_bone_name in blender_bones_to_skip:
            for fcurve in action_fcurves:
                if is_pose_bone_data_path(fcurve.data_path):
                    extracted_bone = extract_bone_name_from_data_path(fcurve.data_path)
                    if extracted_bone == blender_bone_name:
                        fcurves_skipped += 1
    
    return {
        'actions_processed': len(actions_to_process),
        'fcurves_decimated': fcurves_decimated,
        'fcurves_skipped': fcurves_skipped
    }


def _check_fcurves_for_large_gaps(action: bpy.types.Action,
                                   fcurve_filter: Set[tuple],
                                   max_gap: int = 255) -> List[tuple]:
    """Check fcurves for keyframe gaps larger than max_gap frames.

    The GANI binary format stores inter-keyframe frame deltas as 8-bit unsigned
    integers (range 1-255). A gap larger than 255 frames between consecutive
    keyframes cannot be encoded correctly and will produce an invalid binary file.

    Args:
        action: Action to check.
        fcurve_filter: Set of (data_path, array_index) tuples to restrict the
            check to.  Pass an empty set to check all fcurves.
        max_gap: Maximum allowed gap in frames (default 255 matches the 8-bit
            binary limit).

    Returns:
        List of (data_path, array_index, max_gap_found) tuples for every
        fcurve whose largest inter-keyframe gap exceeds *max_gap*.
    """
    violations: List[tuple] = []
    for fcurve in iter_action_fcurves(action):
        key = (fcurve.data_path, fcurve.array_index)
        if fcurve_filter and key not in fcurve_filter:
            continue
        if len(fcurve.keyframe_points) < 2:
            continue
        frames = sorted(int(kp.co[0]) for kp in fcurve.keyframe_points)
        max_gap_found = max(frames[i] - frames[i - 1] for i in range(1, len(frames)))
        if max_gap_found > max_gap:
            violations.append((fcurve.data_path, fcurve.array_index, max_gap_found))
    return violations


def bake_and_clean_export_fcurves(armature: bpy.types.Object,
                           fcurve_clean_threshold: float) -> Dict[str, Any]:
    """Bake non-linear Bezier fcurves to linear and optionally clean redundant keyframes.
    
    Export workflow:
    1. Identify fcurves that are non-linear (Bezier, etc.)
    2. Bake non-linear fcurves to LINEAR (sample every frame)
    3. Apply clean to remove redundant keyframes within threshold
    4. Validate that keyframe gaps don't exceed 255 frames (Fox binary format limit)
    
    Args:
        armature: Armature object with active action
        fcurve_clean_threshold: Threshold for clean operation (0.0 = skip cleaning)
        
    Returns:
        Dictionary with results:
        - 'action': Processed action (may be a copy if non-linear fcurves found)
        - 'fcurves_baked': Number of fcurves baked from non-linear to linear
        - 'fcurves_cleaned': Number of fcurves cleaned (redundant keyframes removed)
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
            
            _sample_fcurves_to_linear(armature, processed_action, frame_start, frame_end)
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
    if nonlinear_fcurves and fcurve_clean_threshold > 0.0:
        fcurves_cleaned = clean_fcurves(processed_action, fcurve_clean_threshold, fcurve_filter=baked_fcurve_keys, obj=armature)

    # Validate: warn if cleaned fcurves have keyframe gaps > 255 frames.
    # The GANI binary format stores inter-keyframe deltas as ubyte (1-255).
    # A gap larger than 255 cannot be encoded and the exporter clamps it to
    # 255, which shifts all subsequent keyframe timings and corrupts the track.
    if nonlinear_fcurves and fcurve_clean_threshold > 0.0 and fcurves_cleaned > 0:
        gap_violations = _check_fcurves_for_large_gaps(processed_action, baked_fcurve_keys)
        if gap_violations:
            Debug.log_error(
                f"Export: {len(gap_violations)} FCurve(s) have keyframe gaps exceeding the "
                f"255-frame binary format limit after baking and cleaning "
                f"(fcurve_clean_threshold={fcurve_clean_threshold}). The exported MTAR will be INVALID. "
                f"Reduce the export clean threshold to fix this. "
                f"First affected: '{gap_violations[0][0]}[{gap_violations[0][1]}]', "
                f"gap={gap_violations[0][2]} frames."
            )

    # Ensure quaternion keyframes do not flip sign (q -> -q) due to clean/decimate.
    # This post-process stabilizes the quaternion hemisphere across frames.
    if processed_action:
        # Operate on a temporary copy to avoid mutating the user's original action.
        if processed_action is action:
            processed_action = action.copy()
            processed_action.name = f"{action.name}_export_temp"
        _make_quaternion_fcurves_compatible(processed_action)

    return {
        'action': processed_action,
        'fcurves_baked': fcurves_baked,
        'fcurves_cleaned': fcurves_cleaned,
        'fcurves_already_linear': linear_count
    }
