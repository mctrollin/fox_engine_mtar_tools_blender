"""
Bake debug operators for MTAR tools.

This module contains operator classes for debugging and testing bake flows.
"""

# pyright: reportInvalidTypeForm=false

import re

import bpy
from bpy.types import Operator, Context

from ..py_core.core_logging import Debug

from ..blender_properties import get_effective_import_bake_decimate_error

from ..py_utilities import util_blender_animation, util_fcurve_processing

from ..py_foxwrap import fwrap_metadata

from ..py_tools import tools_animation_bake


class MTAR_OT_DebugRunBake(Operator):
    """Run synchronous bake using selected debug armature and optional imported armature."""
    bl_idname = "mtar.debug_run_bake"
    bl_label = "Debug: Run Bake"
    bl_description = "Run the existing synchronous bake operation for the selected armature"

    def execute(self, context: Context) -> set:
        props = context.scene.mtar_debug_transform_properties

        if not props.debug_armature:
            Debug.report_and_log(self, 'ERROR', "No target armature selected to bake into")
            return {'CANCELLED'}

        target_armature = props.debug_armature
        source_arm = props.debug_source_armature
        idx = props.debug_bake_gani_index
        prepare_only = props.debug_prepare_only

        try:
            Debug.update_progress(75, "Baking (debug)...")

            if target_armature.animation_data and target_armature.animation_data.nla_tracks:
                # Gather strips
                strips_by_index = {}
                strips_list = []
                for track in target_armature.animation_data.nla_tracks:
                    for strip in track.strips:
                        if not strip.action:
                            continue
                        m = re.search(r"_(\d{3})\b", strip.name)
                        if m:
                            stripped_idx = int(m.group(1))
                            strips_by_index.setdefault(stripped_idx, []).append((track, strip, strip.action))
                        strips_list.append((track, strip, strip.action))

                # Determine target strips
                target_strips = []
                if idx == -1:
                    # full bake or prepare all
                    if prepare_only:
                        # Prepare all: mute source NLA tracks and leave scene for inspection
                        if source_arm and source_arm.animation_data and source_arm.animation_data.nla_tracks:
                            for t in source_arm.animation_data.nla_tracks:
                                t.mute = True
                            Debug.report_and_log(self, 'INFO', "Prepared scene: muted all source NLA tracks for inspection (no bake performed)")
                            return {'FINISHED'}
                        else:
                            Debug.report_and_log(self, 'WARNING', "No source armature NLA tracks to mute for prepare")
                            return {'CANCELLED'}
                    else:
                        # Full bake using unified utility (constraint-bake + optional fcurve decimation)
                        Debug.log("Debug: Baking NLA strips on target armature")
                        bake_result = tools_animation_bake.bake_constraints_and_decimate_fcurves(
                            rig_armature=target_armature,
                            source_armature=source_arm,
                            create_new_action=True,
                            new_action_suffix="_baked",
                            remove_constraints=True,
                            delete_import_armature=False,
                            bake_decimate_fcurve_error=0.01,
                            decimate_skip_types='',
                            layout_action=None,
                        )

                        if bake_result.get('success'):
                            Debug.report_and_log(self, 'INFO', f"Debug bake completed: {bake_result.get('message')} (post-processed: decimated={bake_result.get('fcurves_decimated', 0)})")
                        else:
                            Debug.report_and_log(self, 'WARNING', f"Debug bake failed: {bake_result.get('message')}")

                        Debug.update_progress(100, "Bake complete")
                        return {'FINISHED'}
                else:
                    # Bake only specific index
                    if idx not in strips_by_index:
                        Debug.report_and_log(self, 'ERROR', f"No strip found for GANI index {idx}")
                        return {'CANCELLED'}

                    target_strips = [t for t in strips_by_index[idx] if not t[0].mute and not t[1].mute]
                    if not target_strips:
                        Debug.report_and_log(self, 'WARNING', "No eligible strips to bake for this index")
                        return {'CANCELLED'}

                # Process selected strips
                success_count = 0
                baked_actions = []
                for (track, strip, action) in target_strips:
                    if prepare_only:
                        if source_arm and source_arm != target_armature:
                            if not source_arm.animation_data:
                                source_arm.animation_data_create()
                            if source_arm.animation_data.nla_tracks:
                                for t in source_arm.animation_data.nla_tracks:
                                    t.mute = True
                            util_blender_animation.assign_action_to_datablock(source_arm, action)
                            Debug.log(f"Prepared strip '{strip.name}': muted source NLA and assigned action '{action.name}'")
                            track.mute = True
                            util_blender_animation.assign_action_to_datablock(target_armature, action)
                            Debug.report_and_log(self, 'INFO', f"Prepared scene for strip '{strip.name}' (no bake performed)")
                        continue

                    original_source_action = None
                    original_source_nla_mute_states = None
                    try:
                        if source_arm and source_arm != target_armature:
                            if not source_arm.animation_data:
                                source_arm.animation_data_create()
                            original_source_action = source_arm.animation_data.action
                            if source_arm.animation_data.nla_tracks:
                                original_source_nla_mute_states = {t.name: t.mute for t in source_arm.animation_data.nla_tracks}
                                for t in source_arm.animation_data.nla_tracks:
                                    t.mute = True
                                Debug.log(f"  Muted {len(source_arm.animation_data.nla_tracks)} source NLA tracks for legacy bake")
                            util_blender_animation.assign_action_to_datablock(source_arm, action)

                        bake_result = tools_animation_bake.bake_armature_constraints_to_keyframes(
                            rig_armature=target_armature,
                            action=action,
                            remove_constraints=False,
                            create_new_action=True,
                            new_action_suffix="_baked",
                            nla_track=track,
                            source_armature=source_arm
                        )

                        if bake_result.get('success'):
                            strip.action = bake_result.get('action')
                            baked_actions.append(bake_result.get('action'))
                            success_count += 1

                            try:
                                decimate_err = 0.0
                                decimate_skip_types = ''
                                if hasattr(context.scene, 'mtar_properties'):
                                    ip = getattr(context.scene.mtar_properties, 'import_props', None)
                                    if ip is not None:
                                        decimate_err = get_effective_import_bake_decimate_error(ip)
                                        decimate_skip_types = getattr(ip, 'import_bake_decimate_skip_types', '')

                                if decimate_err > 0.0:
                                    layout_action = util_blender_animation.try_find_layout_track_action()
                                    blender_bone_skip_map = fwrap_metadata.build_blender_bone_decimation_skip_map(
                                        all_blender_bone_names=set(target_armature.data.bones.keys()) if target_armature and target_armature.data else set(),
                                        layout_action=layout_action,
                                        decimate_skip_types=decimate_skip_types,
                                        blender_to_fox_map=None,
                                        cache={},
                                    )
                                    dec_res = util_fcurve_processing.decimate_import_fcurves_to_bezier(
                                        armature=target_armature,
                                        bake_decimate_fcurve_error=decimate_err,
                                        decimate_skip_types=decimate_skip_types,
                                        layout_action=layout_action,
                                        blender_bone_skip_map=blender_bone_skip_map,
                                    )
                                    Debug.log(f"  Post-bake decimation: decimated={dec_res.get('fcurves_decimated', 0)}")
                            except Exception as opt_e:
                                Debug.log_warning(f"Post-bake optimize failed for '{strip.name}': {opt_e}")
                        else:
                            Debug.log_warning(f"Failed to bake strip '{strip.name}': {bake_result.get('message')}")

                    finally:
                        try:
                            if source_arm and source_arm != target_armature and source_arm.animation_data:
                                if original_source_action:
                                    util_blender_animation.assign_action_to_datablock(source_arm, original_source_action)
                                else:
                                    util_blender_animation.remove_action_from_datablock(source_arm)
                                if original_source_nla_mute_states and source_arm.animation_data.nla_tracks:
                                    for tn, was in original_source_nla_mute_states.items():
                                        if tn in source_arm.animation_data.nla_tracks:
                                            source_arm.animation_data.nla_tracks[tn].mute = was
                                    Debug.log("  Restored source NLA mute states after legacy bake")
                        except Exception as restore_err:
                            Debug.log_warning(f"Error restoring source armature state after legacy bake: {restore_err}")

                if success_count > 0:
                    baked_bones = set()
                    for act in baked_actions:
                        baked_bones.update(tools_animation_bake.get_bones_with_keyframes(act))
                    if baked_bones:
                        removed = tools_animation_bake.remove_bone_constraints(target_armature, baked_bones)
                        Debug.log(f"Removed constraints from {len(baked_bones)} bones ({removed} constraints)")

                    if tools_animation_bake.clear_armature_transforms(target_armature):
                        Debug.report_and_log(self, 'INFO', f"Baked {success_count} strip(s) and cleared transforms")
                        return {'FINISHED'}
                    else:
                        Debug.report_and_log(self, 'WARNING', f"Baked {success_count} strip(s) but failed to clear transforms")
                        return {'FINISHED'}
                else:
                    Debug.report_and_log(self, 'WARNING', "No strips were successfully baked")
                    return {'CANCELLED'}

            elif target_armature.animation_data and target_armature.animation_data.action:
                Debug.log("Debug: Baking active action on target armature")
                bake_result = tools_animation_bake.bake_constraints_and_decimate_fcurves(
                    rig_armature=target_armature,
                    source_armature=source_arm,
                    create_new_action=True,
                    new_action_suffix="_baked",
                    remove_constraints=True,
                    bake_decimate_fcurve_error=0.0,
                    decimate_skip_types='',
                    layout_action=None,
                )

                if bake_result.get('success'):
                    Debug.report_and_log(self, 'INFO', f"Debug bake completed: {bake_result.get('message')} (post-processed: decimated={bake_result.get('fcurves_decimated', 0)})")
                else:
                    Debug.report_and_log(self, 'WARNING', f"Debug bake failed: {bake_result.get('message')}")

            else:
                Debug.report_and_log(self, 'WARNING', "Target armature has no NLA tracks or active action to bake")
                return {'CANCELLED'}

        except Exception as e:
            Debug.report_and_log(self, 'ERROR', f"Debug bake failed: {e}")
            return {'CANCELLED'}

        Debug.update_progress(100, "Bake complete")
        return {'FINISHED'}


class MTAR_OT_DebugSetupGraphContext(Operator):
    """Setup graph editor context for manual decimation testing."""
    bl_idname = "mtar.debug_setup_graph_context"
    bl_label = "Setup Graph Context"
    bl_description = "Setup graph editor with action and armature for manual operator testing"
    
    def execute(self, context: Context) -> set:
        """Execute the setup."""
        
        props = context.scene.mtar_debug_transform_properties
        
        # Validate inputs
        if not props.debug_armature:
            Debug.report_and_log(self, 'ERROR', "No target armature selected")
            return {'FINISHED'}
        
        # Get the action from the armature
        armature = props.debug_armature
        if not armature.animation_data or not armature.animation_data.action:
            Debug.report_and_log(self, 'ERROR', f"Armature '{armature.name}' has no active action")
            return {'FINISHED'}
        
        action = armature.animation_data.action
        
        # Call the debug setup function
        util_fcurve_processing.debug_setup_graph_context_for_manual_test(armature.name, action.name)
        
        Debug.report_and_log(self, 'INFO', f"Graph context setup complete for '{armature.name}' / '{action.name}'")
        Debug.report_and_log(self, 'INFO', "Check console for diagnostics. Try: bpy.ops.graph.decimate(mode='ERROR', error=0.01)")
        
        return {'FINISHED'}