"""Debug panel for testing map_r parameter generation and animation rotation mapping."""

import math
from math import radians

import bpy
from mathutils import Quaternion, Euler

from .py_core.core_logging import Debug

from .py_utilities import util_blender_armature


class MTAR_PT_DebugMapRPanel(bpy.types.Panel):
    """Debug panel for map_r parameter testing and animation rotation mapping."""
    bl_label = "Debug - Map R"
    bl_idname = "MTAR_PT_debug_map_r_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MTAR'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        # Input section
        box = layout.box()
        box.label(text="Input Animation Keyframe Rotation", icon='IMPORT')
        
        col = box.column(align=True)
        col.label(text="Test Animation Keyframe (Blender quaternion)")
        col.prop(scene.mtar_debug_map_r_properties, "test_keyframe_w", text="W")
        col.prop(scene.mtar_debug_map_r_properties, "test_keyframe_x", text="X")
        col.prop(scene.mtar_debug_map_r_properties, "test_keyframe_y", text="Y")
        col.prop(scene.mtar_debug_map_r_properties, "test_keyframe_z", text="Z")
        
        col.separator()
        col.label(text="As Euler angles (degrees):")
        row = col.row(align=True)
        row.enabled = False
        row.prop(scene.mtar_debug_map_r_properties, "test_keyframe_euler_x", text="X")
        row.prop(scene.mtar_debug_map_r_properties, "test_keyframe_euler_y", text="Y")
        row.prop(scene.mtar_debug_map_r_properties, "test_keyframe_euler_z", text="Z")
        
        col.separator()
        col.label(text="Example Blender quaternions:")
        col.label(text="Identity: (1, 0, 0, 0)", icon='INFO')
        col.label(text="90° X-axis: (0.707, 0.707, 0, 0)", icon='INFO')
        
        # custom rig section
        box = layout.box()
        box.label(text="custom rig Bone Selection", icon='BONE_DATA')
        
        col = box.column(align=True)
        col.prop(scene.mtar_debug_map_r_properties, "target_armature", text="Armature")
        col.prop(scene.mtar_debug_map_r_properties, "target_bone", text="Bone")
        col.operator("mtar.debug_pick_selected_bone", text="Pick Selected Armature & Bone", icon='EYEDROPPER')
        
        # Analysis button
        box = layout.box()
        col = box.column(align=True)
        col.operator("mtar.debug_analyze_map_r", text="Analyze & Calculate Map_R", icon='TRIA_RIGHT')
        col.operator("mtar.debug_apply_inverted_rest_pose", text="Apply Inverted Rest Pose (Verify)", icon='CHECKMARK')
        col.operator("mtar.debug_apply_mapped_rotation", text="Apply Mapped Rotation to Bone")
        
        # Output section
        box = layout.box()
        box.label(text="Analysis Results", icon='OUTPUT')
        
        col = box.column(align=True)
        col.label(text="Rest Pose Rotation (Euler degrees):")
        row = col.row(align=True)
        row.prop(scene.mtar_debug_map_r_properties, "output_rest_pose_euler_x", text="X")
        row.prop(scene.mtar_debug_map_r_properties, "output_rest_pose_euler_y", text="Y")
        row.prop(scene.mtar_debug_map_r_properties, "output_rest_pose_euler_z", text="Z")
        row.operator("mtar.copy_rest_pose_euler", text="", icon='COPYDOWN')
        
        col.separator()
        col.label(text="Rest Pose Rotation (Quaternion):")
        row = col.row(align=True)
        row.prop(scene.mtar_debug_map_r_properties, "output_rest_pose_quat_w", text="W")
        row.prop(scene.mtar_debug_map_r_properties, "output_rest_pose_quat_x", text="X")
        row.prop(scene.mtar_debug_map_r_properties, "output_rest_pose_quat_y", text="Y")
        row.prop(scene.mtar_debug_map_r_properties, "output_rest_pose_quat_z", text="Z")
        row.operator("mtar.copy_rest_pose_quat", text="", icon='COPYDOWN')
        
        col.separator()
        col.label(text="Calculated Map_R Parameter:")
        row = col.row(align=True)
        row.prop(scene.mtar_debug_map_r_properties, "output_map_r", text="")
        row.operator("mtar.copy_map_r_to_clipboard", text="", icon='COPYDOWN')
        
        col.separator()
        col.label(text="Mapped Animation Keyframe (after map_r):")
        row = col.row(align=True)
        row.prop(scene.mtar_debug_map_r_properties, "output_mapped_quat_w", text="W")
        row.prop(scene.mtar_debug_map_r_properties, "output_mapped_quat_x", text="X")
        row.prop(scene.mtar_debug_map_r_properties, "output_mapped_quat_y", text="Y")
        row.prop(scene.mtar_debug_map_r_properties, "output_mapped_quat_z", text="Z")
        row.operator("mtar.copy_mapped_quat", text="", icon='COPYDOWN')
        
        col.separator()
        col.label(text="As Euler angles (degrees):")
        row = col.row(align=True)
        row.enabled = False
        row.prop(scene.mtar_debug_map_r_properties, "output_mapped_euler_x", text="X")
        row.prop(scene.mtar_debug_map_r_properties, "output_mapped_euler_y", text="Y")
        row.prop(scene.mtar_debug_map_r_properties, "output_mapped_euler_z", text="Z")
        
        col.separator()
        col.label(text="Debug Info:")
        col.prop(scene.mtar_debug_map_r_properties, "debug_log", text="")


def draw_map_r_page(layout, context):
    """Draw the Map_R debug UI directly using provided layout and context.

    This duplicates the body of :meth:`MTAR_PT_DebugMapRPanel.draw` without
    instantiating a Panel object (which fails outside Blender's normal
    registration system).
    """
    scene = context.scene
    # Input section
    box = layout.box()
    box.label(text="Input Animation Keyframe Rotation", icon='IMPORT')
    
    col = box.column(align=True)
    col.label(text="Test Animation Keyframe (Blender quaternion)")
    col.prop(scene.mtar_debug_map_r_properties, "test_keyframe_w", text="W")
    col.prop(scene.mtar_debug_map_r_properties, "test_keyframe_x", text="X")
    col.prop(scene.mtar_debug_map_r_properties, "test_keyframe_y", text="Y")
    col.prop(scene.mtar_debug_map_r_properties, "test_keyframe_z", text="Z")
    
    col.separator()
    col.label(text="As Euler angles (degrees):")
    row = col.row(align=True)
    row.enabled = False
    row.prop(scene.mtar_debug_map_r_properties, "test_keyframe_euler_x", text="X")
    row.prop(scene.mtar_debug_map_r_properties, "test_keyframe_euler_y", text="Y")
    row.prop(scene.mtar_debug_map_r_properties, "test_keyframe_euler_z", text="Z")
    
    col.separator()
    col.label(text="Example Blender quaternions:")
    col.label(text="Identity: (1, 0, 0, 0)", icon='INFO')
    col.label(text="90° X-axis: (0.707, 0.707, 0, 0)", icon='INFO')
    
    # custom rig section
    box = layout.box()
    box.label(text="custom rig Bone Selection", icon='BONE_DATA')
    
    col = box.column(align=True)
    col.prop(scene.mtar_debug_map_r_properties, "target_armature", text="Armature")
    col.prop(scene.mtar_debug_map_r_properties, "target_bone", text="Bone")
    col.operator("mtar.debug_pick_selected_bone", text="Pick Selected Armature & Bone", icon='EYEDROPPER')
    
    # Analysis button
    box = layout.box()
    col = box.column(align=True)
    col.operator("mtar.debug_analyze_map_r", text="Analyze & Calculate Map_R", icon='TRIA_RIGHT')
    col.operator("mtar.debug_apply_inverted_rest_pose", text="Apply Inverted Rest Pose (Verify)", icon='CHECKMARK')
    col.operator("mtar.debug_apply_mapped_rotation", text="Apply Mapped Rotation to Bone")
    
    # Output section
    box = layout.box()
    box.label(text="Analysis Results", icon='OUTPUT')
    
    col = box.column(align=True)
    col.label(text="Rest Pose Rotation (Euler degrees):")
    row = col.row(align=True)
    row.prop(scene.mtar_debug_map_r_properties, "output_rest_pose_euler_x", text="X")
    row.prop(scene.mtar_debug_map_r_properties, "output_rest_pose_euler_y", text="Y")
    row.prop(scene.mtar_debug_map_r_properties, "output_rest_pose_euler_z", text="Z")
    row.operator("mtar.copy_rest_pose_euler", text="", icon='COPYDOWN')
    
    col.separator()
    col.label(text="Rest Pose Rotation (Quaternion):")
    row = col.row(align=True)
    row.prop(scene.mtar_debug_map_r_properties, "output_rest_pose_quat_w", text="W")
    row.prop(scene.mtar_debug_map_r_properties, "output_rest_pose_quat_x", text="X")
    row.prop(scene.mtar_debug_map_r_properties, "output_rest_pose_quat_y", text="Y")
    row.prop(scene.mtar_debug_map_r_properties, "output_rest_pose_quat_z", text="Z")
    row.operator("mtar.copy_rest_pose_quat", text="", icon='COPYDOWN')
    
    col.separator()
    col.label(text="Calculated Map_R Parameter:")
    row = col.row(align=True)
    row.prop(scene.mtar_debug_map_r_properties, "output_map_r", text="")
    row.operator("mtar.copy_map_r_to_clipboard", text="", icon='COPYDOWN')
    
    col.separator()
    col.label(text="Mapped Animation Keyframe (after map_r):")
    row = col.row(align=True)
    row.prop(scene.mtar_debug_map_r_properties, "output_mapped_quat_w", text="W")
    row.prop(scene.mtar_debug_map_r_properties, "output_mapped_quat_x", text="X")
    row.prop(scene.mtar_debug_map_r_properties, "output_mapped_quat_y", text="Y")
    row.prop(scene.mtar_debug_map_r_properties, "output_mapped_quat_z", text="Z")
    row.operator("mtar.copy_mapped_quat", text="", icon='COPYDOWN')
    
    col.separator()
    col.label(text="As Euler angles (degrees):")
    row = col.row(align=True)
    row.enabled = False
    row.prop(scene.mtar_debug_map_r_properties, "output_mapped_euler_x", text="X")
    row.prop(scene.mtar_debug_map_r_properties, "output_mapped_euler_y", text="Y")
    row.prop(scene.mtar_debug_map_r_properties, "output_mapped_euler_z", text="Z")
    
    col.separator()
    col.label(text="Debug Info:")
    col.prop(scene.mtar_debug_map_r_properties, "debug_log", text="")


class MTAR_OT_DebugAnalyzeMapR(bpy.types.Operator):
    """Analyze target bone rest pose and calculate map_r parameter."""
    bl_idname = "mtar.debug_analyze_map_r"
    bl_label = "Analyze Map_R"
    bl_description = "Extract rest pose from target bone and calculate corresponding map_r parameter"

    def execute(self, context):
        scene = context.scene
        props = scene.mtar_debug_map_r_properties
        
        # Validate inputs
        if not props.target_armature:
            Debug.report_and_log(self, 'ERROR', "Please select a target armature")
            return {'FINISHED'}
        
        if not props.target_bone:
            Debug.report_and_log(self, 'ERROR', "Please select a target bone")
            return {'FINISHED'}
        
        # Get test keyframe quaternion
        test_quat_fox = [props.test_keyframe_x, props.test_keyframe_y, props.test_keyframe_z, props.test_keyframe_w]
        
        Debug.log("=" * 60)
        Debug.log("MAP_R DEBUG ANALYSIS")
        Debug.log("=" * 60)
        Debug.log(f"Input Blender quaternion [x, y, z, w]: {test_quat_fox}")
        Debug.log(f"  (or in w,x,y,z order: [{test_quat_fox[3]}, {test_quat_fox[0]}, {test_quat_fox[1]}, {test_quat_fox[2]}])")
        Debug.log("  NOTE: This is already in Blender format (after Fox->Blender import conversion)")
        
        try:
            # Extract rest pose rotation from target bone
            armature = props.target_armature
            bone_name = props.target_bone
            
            Debug.log(f"\nTarget: Armature '{armature.name}', Bone '{bone_name}'")
            
            if bone_name not in armature.pose.bones:
                Debug.report_and_log(self, 'ERROR', f"Bone '{bone_name}' not found in armature")
                return {'FINISHED'}
            
            # Get the bone from edit bones (required for extract_rest_pose_rotation)
            bone = armature.data.bones[bone_name]
            
            # Extract rest pose as Euler angles (world space)
            rest_pose_euler, _ = util_blender_armature.extract_rest_pose_rotation(
                bone=bone,
                is_world_space=True,
                known_bone_names=set()  # Empty set - not used when is_world_space=True
            )
            
            Debug.log(f"\nExtracted rest pose: {rest_pose_euler}")
            
            if rest_pose_euler is None:
                Debug.report_and_log(self, 'WARNING', "Could not extract rest pose (zero rotation)")
                props.output_rest_pose_euler_x = 0.0
                props.output_rest_pose_euler_y = 0.0
                props.output_rest_pose_euler_z = 0.0
                props.output_map_r = "# Rest pose is identity (0, 0, 0)"
                props.debug_log = "Rest pose has zero rotation"
                return {'FINISHED'}
            
            # Store rest pose rotation (convert from Euler object to degrees)
            rest_euler_x = math.degrees(rest_pose_euler.x)
            rest_euler_y = math.degrees(rest_pose_euler.y)
            rest_euler_z = math.degrees(rest_pose_euler.z)
            
            Debug.log(f"Rest pose in degrees [x, y, z]: [{rest_euler_x:.2f}, {rest_euler_y:.2f}, {rest_euler_z:.2f}]")
            
            # Convert to quaternion
            rest_pose_quat = rest_pose_euler.to_quaternion()
            Debug.log(f"Rest pose as quaternion: w={rest_pose_quat.w:.4f}, x={rest_pose_quat.x:.4f}, y={rest_pose_quat.y:.4f}, z={rest_pose_quat.z:.4f}")
            
            props.output_rest_pose_euler_x = rest_euler_x
            props.output_rest_pose_euler_y = rest_euler_y
            props.output_rest_pose_euler_z = rest_euler_z
            
            props.output_rest_pose_quat_x = rest_pose_quat.x
            props.output_rest_pose_quat_y = rest_pose_quat.y
            props.output_rest_pose_quat_z = rest_pose_quat.z
            props.output_rest_pose_quat_w = rest_pose_quat.w
            
            # Generate map_r parameter
            map_r_param = f"euler:{rest_euler_x:.2f},{rest_euler_y:.2f},{rest_euler_z:.2f},XYZ"
            props.output_map_r = map_r_param
            
            Debug.log(f"\nGenerated map_r parameter: {map_r_param}")
            
            Debug.log("\nApplying map_r to test keyframe...")
            
            # Input quaternion is already in Blender format (after import)
            # So we just need to apply the Euler rotation, not convert from Fox
            
            
            # Create Blender quaternion from input (w, x, y, z order in Blender)
            input_quat = Quaternion((test_quat_fox[3], test_quat_fox[0], test_quat_fox[1], test_quat_fox[2]))
            Debug.log(f"  Input Blender quaternion: w={input_quat.w:.4f}, x={input_quat.x:.4f}, y={input_quat.y:.4f}, z={input_quat.z:.4f}")
            
            # Convert test keyframe to Euler for display
            test_euler = input_quat.to_euler('XYZ')
            test_euler_x = math.degrees(test_euler.x)
            test_euler_y = math.degrees(test_euler.y)
            test_euler_z = math.degrees(test_euler.z)
            
            props.test_keyframe_euler_x = test_euler_x
            props.test_keyframe_euler_y = test_euler_y
            props.test_keyframe_euler_z = test_euler_z
            Debug.log(f"  Test keyframe as Euler: x={test_euler_x:.2f}°, y={test_euler_y:.2f}°, z={test_euler_z:.2f}°")
            Debug.log("  NOTE: This is the rotation from Fox import (on world-aligned bones)")
            
            # Get target bone rest pose
            euler_rot = Euler((radians(rest_euler_x), radians(rest_euler_y), radians(rest_euler_z)), 'XYZ')
            euler_quat = euler_rot.to_quaternion()
            Debug.log(f"\n  Target bone rest pose quaternion: w={euler_quat.w:.4f}, x={euler_quat.x:.4f}, y={euler_quat.y:.4f}, z={euler_quat.z:.4f}")
            
            # CONVERSION LOGIC (CORRECTED):
            # The input rotation is in PARENT SPACE (world space for root bones)
            # Target bone has a rest pose R that orients its local axes
            # To convert a parent-space rotation P to local-space rotation L:
            # 
            # The relationship is: P = R @ L @ R^(-1)
            # This is because the bone's actual rotation in parent space is the rest pose,
            # then the local rotation, then un-rotate the rest pose.
            #
            # Solving for L: L = R^(-1) @ P @ R
            # This is the conjugation formula (similarity transformation)
            
            inverted_euler_quat = euler_quat.inverted()
            Debug.log(f"  Inverted rest pose: w={inverted_euler_quat.w:.4f}, x={inverted_euler_quat.x:.4f}, y={inverted_euler_quat.y:.4f}, z={inverted_euler_quat.z:.4f}")
            
            # Calculate: local_rotation = rest_pose_inverse @ parent_rotation @ rest_pose
            mapped_quat = inverted_euler_quat @ input_quat @ euler_quat
            
            Debug.log(f"\nResult (target bone's LOCAL rotation): {mapped_quat}")
            Debug.log(f"  w={mapped_quat.w:.4f}, x={mapped_quat.x:.4f}, y={mapped_quat.y:.4f}, z={mapped_quat.z:.4f}")
            
            # Convert mapped quaternion to Euler for display
            mapped_euler = mapped_quat.to_euler('XYZ')
            mapped_euler_x = math.degrees(mapped_euler.x)
            mapped_euler_y = math.degrees(mapped_euler.y)
            mapped_euler_z = math.degrees(mapped_euler.z)
            
            props.output_mapped_euler_x = mapped_euler_x
            props.output_mapped_euler_y = mapped_euler_y
            props.output_mapped_euler_z = mapped_euler_z
            Debug.log(f"  Mapped keyframe as Euler: x={mapped_euler_x:.2f}°, y={mapped_euler_y:.2f}°, z={mapped_euler_z:.2f}°")
            Debug.log("=" * 60)
            
            # Store mapped quaternion
            props.output_mapped_quat_x = mapped_quat.x
            props.output_mapped_quat_y = mapped_quat.y
            props.output_mapped_quat_z = mapped_quat.z
            props.output_mapped_quat_w = mapped_quat.w
            
            # Update debug info
            props.debug_log = f"✓ Analyzed bone '{bone_name}' in armature '{armature.name}'"
            
            Debug.report_and_log(self, 'INFO', f"Map_R parameter: {map_r_param}")
            
        except (RuntimeError, KeyError, AttributeError) as e:
            Debug.report_and_log(self, 'ERROR', f"Debug map_r analysis error: {str(e)}")
            props.debug_log = f"✗ Error: {str(e)}"
        
        return {'FINISHED'}


class MTAR_OT_DebugPickSelectedBone(bpy.types.Operator):
    """Pick currently selected armature and bone."""
    bl_idname = "mtar.debug_pick_selected_bone"
    bl_label = "Pick Selected Bone"
    bl_description = "Automatically set target armature and bone from current selection"

    def execute(self, context):
        scene = context.scene
        props = scene.mtar_debug_map_r_properties
        
        # Get selected objects
        selected_objects = context.selected_objects
        active_object = context.active_object
        
        # Find armature in selection
        armature = None
        for obj in selected_objects:
            if obj.type == 'ARMATURE':
                armature = obj
                break
        
        if not armature:
            Debug.report_and_log(self, 'ERROR', "No armature selected. Please select an armature and a bone.")
            return {'FINISHED'}
        
        # Get selected bone from active bone in the armature
        if armature.mode == 'EDIT':
            # Edit mode: get selected edit bone
            selected_bones = [b.name for b in armature.data.edit_bones if b.select]
            if not selected_bones:
                Debug.report_and_log(self, 'ERROR', "No bone selected in edit mode. Please select a bone.")
                return {'FINISHED'}
            bone_name = selected_bones[0]
        elif armature.mode == 'POSE':
            # Pose mode: get selected pose bone
            # Blender 5.0+ checks selection via pose bone, older versions via data bone
            selected_bones = [b.name for b in armature.pose.bones if (getattr(b, "select", False) or getattr(b.bone, "select", False))]
            if not selected_bones:
                Debug.report_and_log(self, 'ERROR', "No bone selected in pose mode. Please select a bone.")
                return {'FINISHED'}
            bone_name = selected_bones[0]
        else:
            # Object mode: use active bone if available
            if active_object and active_object.type == 'ARMATURE':
                if active_object.data.bones.active:
                    bone_name = active_object.data.bones.active.name
                else:
                    Debug.report_and_log(self, 'ERROR', "No active bone. Please select a bone in the armature.")
                    return {'FINISHED'}
            else:
                Debug.report_and_log(self, 'ERROR', "Armature not in edit or pose mode. Please select in pose/edit mode.")
                return {'FINISHED'}
        
        # Set properties
        props.target_armature = armature
        props.target_bone = bone_name
        
        Debug.report_and_log(self, 'INFO', f"Selected armature '{armature.name}' and bone '{bone_name}'")
        
        return {'FINISHED'}


class MTAR_OT_CopyMapRToClipboard(bpy.types.Operator):
    """Copy the generated map_r parameter to clipboard."""
    bl_idname = "mtar.copy_map_r_to_clipboard"
    bl_label = "Copy to Clipboard"
    bl_description = "Copy the map_r parameter to system clipboard"

    def execute(self, context):
        scene = context.scene
        map_r_value = scene.mtar_debug_map_r_properties.output_map_r
        
        if not map_r_value or map_r_value.startswith('#'):
            Debug.report_and_log(self, 'WARNING', "No valid map_r parameter to copy")
            return {'FINISHED'}
        
        # Copy to clipboard
        bpy.context.window_manager.clipboard = map_r_value
        Debug.report_and_log(self, 'INFO', f"Copied to clipboard: {map_r_value}")
        
        return {'FINISHED'}


class MTAR_OT_CopyRestPoseEuler(bpy.types.Operator):
    """Copy rest pose Euler rotation to clipboard."""
    bl_idname = "mtar.copy_rest_pose_euler"
    bl_label = "Copy Euler"
    bl_description = "Copy rest pose Euler rotation (x, y, z) to clipboard"

    def execute(self, context):
        props = context.scene.mtar_debug_map_r_properties
        euler_str = f"{props.output_rest_pose_euler_x:.2f}, {props.output_rest_pose_euler_y:.2f}, {props.output_rest_pose_euler_z:.2f}"
        bpy.context.window_manager.clipboard = euler_str
        Debug.report_and_log(self, 'INFO', f"Copied Euler: {euler_str}")
        return {'FINISHED'}


class MTAR_OT_CopyRestPoseQuat(bpy.types.Operator):
    """Copy rest pose quaternion to clipboard."""
    bl_idname = "mtar.copy_rest_pose_quat"
    bl_label = "Copy Quaternion"
    bl_description = "Copy rest pose quaternion (w, x, y, z) to clipboard"

    def execute(self, context):
        props = context.scene.mtar_debug_map_r_properties
        quat_str = f"{props.output_rest_pose_quat_w:.4f}, {props.output_rest_pose_quat_x:.4f}, {props.output_rest_pose_quat_y:.4f}, {props.output_rest_pose_quat_z:.4f}"
        bpy.context.window_manager.clipboard = quat_str
        Debug.report_and_log(self, 'INFO', f"Copied quaternion: {quat_str}")
        return {'FINISHED'}


class MTAR_OT_CopyMappedQuat(bpy.types.Operator):
    """Copy mapped quaternion to clipboard."""
    bl_idname = "mtar.copy_mapped_quat"
    bl_label = "Copy Mapped Quaternion"
    bl_description = "Copy mapped animation quaternion (w, x, y, z) to clipboard"

    def execute(self, context):
        props = context.scene.mtar_debug_map_r_properties
        quat_str = f"{props.output_mapped_quat_w:.4f}, {props.output_mapped_quat_x:.4f}, {props.output_mapped_quat_y:.4f}, {props.output_mapped_quat_z:.4f}"
        bpy.context.window_manager.clipboard = quat_str
        Debug.report_and_log(self, 'INFO', f"Copied quaternion: {quat_str}")
        return {'FINISHED'}


class MTAR_OT_DebugApplyInvertedRestPose(bpy.types.Operator):
    """Apply inverted rest pose to bone to verify rest pose extraction."""
    bl_idname = "mtar.debug_apply_inverted_rest_pose"
    bl_label = "Apply Inverted Rest Pose"
    bl_description = "Apply the inverted rest pose rotation to the bone (verification: bone should have zero local rotation afterwards)"

    def execute(self, context):
        scene = context.scene
        props = scene.mtar_debug_map_r_properties
        
        # Validate inputs
        if not props.target_armature:
            Debug.report_and_log(self, 'ERROR', "Please select a target armature")
            return {'FINISHED'}
        
        if not props.target_bone:
            Debug.report_and_log(self, 'ERROR', "Please select a target bone")
            return {'FINISHED'}
        
        try:
            armature = props.target_armature
            bone_name = props.target_bone
            
            if bone_name not in armature.pose.bones:
                Debug.report_and_log(self, 'ERROR', f"Bone '{bone_name}' not found in armature")
                return {'FINISHED'}
            
            # Get the pose bone
            pose_bone = armature.pose.bones[bone_name]
            
            # Create inverted rest pose quaternion (inverse of extracted rest pose)
            rest_pose_quat = Quaternion((props.output_rest_pose_quat_w, props.output_rest_pose_quat_x, 
                                        props.output_rest_pose_quat_y, props.output_rest_pose_quat_z))
            
            # Invert the quaternion
            inverted_quat = rest_pose_quat.inverted()
            
            Debug.log("=" * 60)
            Debug.log("APPLYING INVERTED REST POSE (VERIFICATION)")
            Debug.log("=" * 60)
            Debug.log(f"Target: Armature '{armature.name}', Bone '{bone_name}'")
            Debug.log(f"Rest pose quaternion (world space): w={rest_pose_quat.w:.4f}, x={rest_pose_quat.x:.4f}, y={rest_pose_quat.y:.4f}, z={rest_pose_quat.z:.4f}")
            Debug.log(f"Inverted quaternion: w={inverted_quat.w:.4f}, x={inverted_quat.x:.4f}, y={inverted_quat.y:.4f}, z={inverted_quat.z:.4f}")
            
            # Apply as local rotation to the bone
            pose_bone.rotation_quaternion = inverted_quat
            
            Debug.log("Applied inverted rotation to bone's local rotation")
            Debug.log(f"After applying: bone local rotation = {pose_bone.rotation_quaternion}")
            Debug.log("Note: If rest pose extraction was correct, the bone should now have ~zero local rotation")
            Debug.log("(The combined world space = rest pose × inverted rest pose = identity)")
            Debug.log("=" * 60)
            
            Debug.report_and_log(self, 'INFO', f"Applied inverted rest pose to '{bone_name}'")
            props.debug_log = "✓ Applied inverted rest pose to verify extraction"
            
        except (RuntimeError, KeyError, AttributeError) as e:
            Debug.report_and_log(self, 'ERROR', f"Error: {str(e)}")
            props.debug_log = f"✗ Error: {str(e)}"
        
        return {'FINISHED'}


class MTAR_OT_DebugApplyMappedRotation(bpy.types.Operator):
    """Apply the mapped rotation to the bone."""
    bl_idname = "mtar.debug_apply_mapped_rotation"
    bl_label = "Apply Mapped Rotation"
    bl_description = "Apply the mapped animation rotation to the bone's local rotation"

    def execute(self, context):
        scene = context.scene
        props = scene.mtar_debug_map_r_properties
        
        # Validate inputs
        if not props.target_armature:
            Debug.report_and_log(self, 'ERROR', "Please select a target armature")
            return {'FINISHED'}
        
        if not props.target_bone:
            Debug.report_and_log(self, 'ERROR', "Please select a target bone")
            return {'FINISHED'}
        
        try:
            armature = props.target_armature
            bone_name = props.target_bone
            
            if bone_name not in armature.pose.bones:
                Debug.report_and_log(self, 'ERROR', f"Bone '{bone_name}' not found in armature")
                return {'FINISHED'}
            
            # Get the pose bone
            pose_bone = armature.pose.bones[bone_name]
            
            # Create quaternion from mapped rotation output
            mapped_quat = Quaternion((props.output_mapped_quat_w, props.output_mapped_quat_x, 
                                     props.output_mapped_quat_y, props.output_mapped_quat_z))
            
            Debug.log("=" * 60)
            Debug.log("APPLYING MAPPED ROTATION TO BONE")
            Debug.log("=" * 60)
            Debug.log(f"Target: Armature '{armature.name}', Bone '{bone_name}'")
            Debug.log(f"Mapped quaternion: w={mapped_quat.w:.4f}, x={mapped_quat.x:.4f}, y={mapped_quat.y:.4f}, z={mapped_quat.z:.4f}")
            
            # Apply as local rotation to the bone
            pose_bone.rotation_quaternion = mapped_quat
            
            Debug.log("Applied mapped rotation to bone's local rotation")
            Debug.log(f"Bone now has rotation: {pose_bone.rotation_quaternion}")
            Debug.log("=" * 60)
            
            Debug.report_and_log(self, 'INFO', f"Applied mapped rotation to '{bone_name}'")
            props.debug_log = "✓ Applied mapped rotation to bone"
            
        except (RuntimeError, KeyError, AttributeError) as e:
            Debug.report_and_log(self, 'ERROR', f"Apply mapped rotaton error: {str(e)}")
            props.debug_log = f"✗ Error: {str(e)}"
        
        return {'FINISHED'}


class MTAR_PG_DebugMapRProperties(bpy.types.PropertyGroup):
    """Properties for map_r debug panel."""
    
    # Input: Test animation keyframe rotation (Blender quaternion after import)
    test_keyframe_x: bpy.props.FloatProperty(
        name="Test Keyframe X",
        description="X component of test Blender quaternion (after import)",
        default=0.0,
        min=-1.0,
        max=1.0
    )
    test_keyframe_y: bpy.props.FloatProperty(
        name="Test Keyframe Y",
        description="Y component of test Blender quaternion (after import)",
        default=0.0,
        min=-1.0,
        max=1.0
    )
    test_keyframe_z: bpy.props.FloatProperty(
        name="Test Keyframe Z",
        description="Z component of test Blender quaternion (after import)",
        default=0.0,
        min=-1.0,
        max=1.0
    )
    test_keyframe_w: bpy.props.FloatProperty(
        name="Test Keyframe W",
        description="W component of test Blender quaternion (scalar, after import)",
        default=1.0,
        min=-1.0,
        max=1.0
    )
    
    # Display: Test keyframe as Euler (read-only)
    test_keyframe_euler_x: bpy.props.FloatProperty(
        name="Test Euler X",
        description="Test keyframe converted to Euler X (degrees, read-only)",
        default=0.0,
        step=1,
        precision=2
    )
    test_keyframe_euler_y: bpy.props.FloatProperty(
        name="Test Euler Y",
        description="Test keyframe converted to Euler Y (degrees, read-only)",
        default=0.0,
        step=1,
        precision=2
    )
    test_keyframe_euler_z: bpy.props.FloatProperty(
        name="Test Euler Z",
        description="Test keyframe converted to Euler Z (degrees, read-only)",
        default=0.0,
        step=1,
        precision=2
    )
    
    # Input: custom rig bone selection
    target_armature: bpy.props.PointerProperty(
        name="Target Armature",
        description="Armature to extract rest pose from",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE'
    )
    target_bone: bpy.props.StringProperty(
        name="Target Bone",
        description="Name of bone to extract rest pose from",
        default=""
    )
    
    # Output: Rest pose rotation (Euler)
    output_rest_pose_euler_x: bpy.props.FloatProperty(
        name="Rest Pose X",
        description="Rest pose rotation X (degrees)",
        default=0.0,
        step=1,
        precision=2
    )
    output_rest_pose_euler_y: bpy.props.FloatProperty(
        name="Rest Pose Y",
        description="Rest pose rotation Y (degrees)",
        default=0.0,
        step=1,
        precision=2
    )
    output_rest_pose_euler_z: bpy.props.FloatProperty(
        name="Rest Pose Z",
        description="Rest pose rotation Z (degrees)",
        default=0.0,
        step=1,
        precision=2
    )
    
    # Output: Rest pose rotation (Quaternion)
    output_rest_pose_quat_x: bpy.props.FloatProperty(
        name="Rest Pose Quat X",
        description="Rest pose rotation quaternion X component",
        default=0.0,
        step=0.001,
        precision=4
    )
    output_rest_pose_quat_y: bpy.props.FloatProperty(
        name="Rest Pose Quat Y",
        description="Rest pose rotation quaternion Y component",
        default=0.0,
        step=0.001,
        precision=4
    )
    output_rest_pose_quat_z: bpy.props.FloatProperty(
        name="Rest Pose Quat Z",
        description="Rest pose rotation quaternion Z component",
        default=0.0,
        step=0.001,
        precision=4
    )
    output_rest_pose_quat_w: bpy.props.FloatProperty(
        name="Rest Pose Quat W",
        description="Rest pose rotation quaternion W component",
        default=1.0,
        step=0.001,
        precision=4
    )
    
    # Output: Generated map_r parameter
    output_map_r: bpy.props.StringProperty(
        name="Map_R Parameter",
        description="Generated map_r parameter for mapping file",
        default="",
        maxlen=256
    )
    
    # Output: Mapped animation keyframe
    output_mapped_quat_w: bpy.props.FloatProperty(
        name="Mapped W",
        description="W component of mapped quaternion",
        default=1.0,
        step=0.001,
        precision=4
    )
    output_mapped_quat_x: bpy.props.FloatProperty(
        name="Mapped X",
        description="X component of mapped quaternion",
        default=0.0,
        step=0.001,
        precision=4
    )
    output_mapped_quat_y: bpy.props.FloatProperty(
        name="Mapped Y",
        description="Y component of mapped quaternion",
        default=0.0,
        step=0.001,
        precision=4
    )
    output_mapped_quat_z: bpy.props.FloatProperty(
        name="Mapped Z",
        description="Z component of mapped quaternion",
        default=0.0,
        step=0.001,
        precision=4
    )
    
    # Display: Mapped keyframe as Euler (read-only)
    output_mapped_euler_x: bpy.props.FloatProperty(
        name="Mapped Euler X",
        description="Mapped keyframe converted to Euler X (degrees, read-only)",
        default=0.0,
        step=1,
        precision=2
    )
    output_mapped_euler_y: bpy.props.FloatProperty(
        name="Mapped Euler Y",
        description="Mapped keyframe converted to Euler Y (degrees, read-only)",
        default=0.0,
        step=1,
        precision=2
    )
    output_mapped_euler_z: bpy.props.FloatProperty(
        name="Mapped Euler Z",
        description="Mapped keyframe converted to Euler Z (degrees, read-only)",
        default=0.0,
        step=1,
        precision=2
    )
    
    # Debug info
    debug_log: bpy.props.StringProperty(
        name="Debug Info",
        description="Debug information from last analysis",
        default=""
    )


# Registration

classes = (
    MTAR_PG_DebugMapRProperties,
    MTAR_OT_DebugAnalyzeMapR,
    MTAR_OT_DebugPickSelectedBone,
    MTAR_OT_CopyMapRToClipboard,
    MTAR_OT_CopyRestPoseEuler,
    MTAR_OT_CopyRestPoseQuat,
    MTAR_OT_CopyMappedQuat,
    MTAR_OT_DebugApplyInvertedRestPose,
    MTAR_OT_DebugApplyMappedRotation,
)


def register():
    """Register map_r debug classes."""
    for cls in classes:
        bpy.utils.register_class(cls)
    
    # Add properties to scene
    bpy.types.Scene.mtar_debug_map_r_properties = bpy.props.PointerProperty(type=MTAR_PG_DebugMapRProperties)


def unregister():
    """Unregister map_r debug classes."""
    # Remove properties from scene
    if hasattr(bpy.types.Scene, 'mtar_debug_map_r_properties'):
        del bpy.types.Scene.mtar_debug_map_r_properties
    
    # Unregister classes
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
