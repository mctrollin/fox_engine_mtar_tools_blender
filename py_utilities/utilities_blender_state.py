"""
Blender state save/restore context managers.

This module provides context managers that temporarily mutate Blender state
and guarantee restoration on exit — whether by normal return or exception.

Context managers here deal with two orthogonal layers of state:

  nla_tweak_guard  — AnimData.use_tweak_mode (data layer)
                     AnimData.action is read-only while tweak mode is active.
                     Use at operator level to cover the full operation.

  switch_context   — UI area type, active object, active action, object mode
                     (UI / operator-poll layer).
                     Use inside fcurve processing to satisfy operator poll
                     requirements for bpy.ops.graph.* / bpy.ops.anim.*.
"""
from contextlib import contextmanager
from typing import Optional

import bpy

from .utilities_logging import Debug
from .utilities_blender_animation import (
    assign_action_to_datablock,
    remove_action_from_datablock,
    MTAR_ARMATURE_SLOT_NAME,
)


@contextmanager
def nla_tweak_guard(*armatures: Optional[bpy.types.Object]):
    """Context manager: disable NLA tweak mode for each armature, restore on exit.

    ``AnimData.action`` is read-only while ``use_tweak_mode`` is ``True``.
    Wrap the outermost operation that needs to assign actions (import/export
    operators) with this guard so that every nested action assignment is safe
    without each callee having to repeat the check.

    ``None`` entries in *armatures* are silently skipped, so callers can
    pass optional armature properties directly without pre-checking.

    Args:
        *armatures: Zero or more ``bpy.types.Object`` instances (or ``None``)
                    whose ``animation_data.use_tweak_mode`` should be disabled
                    for the duration of the block.

    Example::

        with nla_tweak_guard(main_armature, motion_points_armature):
            export_mtar(...)
    """
    saved: list[tuple] = []  # list of (anim_data, was_tweak)

    for obj in armatures:
        if obj is None:
            continue
        if obj.type != 'ARMATURE':
            continue
        anim = obj.animation_data
        if anim is None:
            continue
        was = bool(anim.use_tweak_mode)
        saved.append((anim, was))
        if was:
            Debug.log(f"  NLA tweak mode active on '{obj.name}', disabling temporarily")
            anim.use_tweak_mode = False

    try:
        yield
    finally:
        for anim, was in saved:
            if was:
                try:
                    anim.use_tweak_mode = True
                except (ReferenceError, RuntimeError):
                    pass  # Object may have been removed during the operation


@contextmanager
def switch_context(area_type: str, obj: Optional[bpy.types.Object] = None,
                   action: Optional[bpy.types.Action] = None):
    """Context manager: switch to a specific area type with optional object/action.

    Sets up the Blender UI context required by operators such as
    ``bpy.ops.graph.decimate``, ``bpy.ops.graph.clean``, and
    ``bpy.ops.anim.channels_bake``.  All state is restored on exit.

    ``nla_tweak_guard`` is applied internally around the action-assignment
    step so that assigning ``obj.animation_data.action`` is always safe,
    even when called directly from debug helpers outside of an operator.

    Args:
        area_type: Blender area type to activate (e.g. ``'GRAPH_EDITOR'``).
        obj: Optional object to set as active.  Required for most graph
             operators.  Armatures are automatically switched to POSE mode.
        action: Optional action to assign to *obj* for the duration of the
                block.  Restored (or cleared) on exit.

    Yields:
        Nothing — callers use ``with switch_context(...):`` and run operators
        inside the block.
    """
    target_area = None
    former_area_type = None
    former_mode = None
    former_active = bpy.context.view_layer.objects.active if bpy.context.view_layer else None
    former_action = None
    former_slot = None
    former_object_mode = bpy.context.mode if obj else None
    window = bpy.context.window

    # Try to find an existing area of the requested type
    for area in window.screen.areas:
        if area.type == area_type:
            target_area = area
            break

    # If not found, repurpose the first area temporarily
    if target_area is None:
        target_area = window.screen.areas[0]
        former_area_type = target_area.type
        target_area.type = area_type

    try:
        if obj is not None:
            bpy.context.view_layer.objects.active = obj

            # POSE mode is required for pose-bone FCurve operators
            if obj.type == 'ARMATURE' and bpy.context.mode != 'POSE':
                bpy.ops.object.mode_set(mode='POSE')

            if action is not None:
                # Save current action/slot before overwriting
                if obj.animation_data:
                    former_action = obj.animation_data.action
                    if hasattr(obj.animation_data, 'action_slot'):
                        former_slot = obj.animation_data.action_slot

                # Use nla_tweak_guard so action assignment never fails due to
                # tweak mode — this matters for debug helpers that run outside
                # the operator-level guard.
                with nla_tweak_guard(obj):
                    try:
                        assign_action_to_datablock(obj, action, slot_name=MTAR_ARMATURE_SLOT_NAME)
                    except Exception as e:
                        Debug.log_warning(f"Could not use slot-aware assignment: {e}")
                        if not obj.animation_data:
                            obj.animation_data_create()
                        obj.animation_data.action = action

                if not (obj.animation_data and obj.animation_data.action):
                    Debug.log_warning(
                        f"No action assigned to object '{obj.name}' after attempted assignment"
                    )

        # Configure graph editor space
        if area_type == 'GRAPH_EDITOR':
            try:
                space = target_area.spaces.active
                if hasattr(space, 'mode'):
                    former_mode = space.mode
                    space.mode = 'FCURVES'
            except (AttributeError, RuntimeError) as e:
                Debug.log_warning(f"Failed to configure graph editor space: {e}")

        with bpy.context.temp_override(window=window, area=target_area):
            yield

    finally:
        # Restore action / slot
        if obj is not None and action is not None:
            if obj.animation_data:
                with nla_tweak_guard(obj):
                    if former_action is not None:
                        obj.animation_data.action = former_action
                        if hasattr(obj.animation_data, 'action_slot') and former_slot is not None:
                            try:
                                obj.animation_data.action_slot = former_slot
                            except Exception:
                                pass
                    else:
                        try:
                            remove_action_from_datablock(obj)
                        except Exception:
                            obj.animation_data.action = None

        # Restore object mode
        if former_object_mode is not None and bpy.context.mode != former_object_mode:
            try:
                bpy.ops.object.mode_set(mode=former_object_mode)
            except RuntimeError:
                pass

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

        # Restore area type if we changed it
        if former_area_type is not None:
            target_area.type = former_area_type
