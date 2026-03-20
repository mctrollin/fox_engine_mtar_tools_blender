"""Blender armature utility functions."""

from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict

import bpy
from mathutils import Euler, Matrix


from ..py_core.core_logging import Debug


# Shared armature creation ####################################################

@dataclass
class BoneSpec:
    """Specification for a single bone to be created in an armature.

    Attributes:
        name:        Bone name.
        parent_name: Name of the parent bone, or ``None`` for a root bone.
    """
    name: str
    parent_name: Optional[str] = None


def create_track_armature(
    context: 'bpy.types.Context',
    armature_name: str,
    bone_specs: List[BoneSpec],
) -> bpy.types.Object:
    """Create a Blender armature from a list of :class:`BoneSpec` objects.

    All bones are created as flat stubs (head at origin, tail at +Y 0.1).
    Parent relationships are set according to :attr:`BoneSpec.parent_name`.

    This is the shared implementation used by all three track types
    (animation, motion points, shaders).  Each caller builds its own
    ``List[BoneSpec]`` describing the desired hierarchy and passes it here.

    Args:
        context:       Blender context (used to link the object and set active).
        armature_name: Name for both the Armature data-block and its Object.
        bone_specs:    Ordered list of bone specifications.

    Returns:
        The newly created armature :class:`bpy.types.Object`.
    """
    arm_data: bpy.types.Armature = bpy.data.armatures.new(name=armature_name)
    armature_obj: bpy.types.Object = bpy.data.objects.new(armature_name, arm_data)
    context.view_layer.active_layer_collection.collection.objects.link(armature_obj)

    context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode='EDIT')

    created: Dict[str, bpy.types.EditBone] = {}
    for spec in bone_specs:
        if spec.name in created:
            continue
        eb = armature_obj.data.edit_bones.new(spec.name)
        eb.head = (0.0, 0.0, 0.0)
        eb.tail = (0.0, 0.1, 0.0)
        created[spec.name] = eb

    # Set parent relationships in a second pass so all bones exist first.
    for spec in bone_specs:
        if spec.parent_name and spec.parent_name in created:
            created[spec.name].parent = created[spec.parent_name]

    bpy.ops.object.mode_set(mode='OBJECT')
    Debug.log(
        f"create_track_armature: '{armature_name}' — {len(created)} bone(s) created"
    )
    return armature_obj


# Rest Pose Utilities #############################################################

# def gather_known_bone_names_from_tracks(all_gani_tracks: List[List] | List[GaniImportData]) -> set:
#     """Gather all bone names that exist in track data.
    
#     The input may be either the legacy ``List[List[TrackUnitWrapper]]`` or a
#     list of :class:`GaniImportData` objects.  If ``GaniImportData`` objects are
#     provided, their ``bone_tracks`` lists are used internally.
    
#     Args:
#         all_gani_tracks: Either raw track lists or a list of GaniImportData objects.
        
#     Returns:
#         Set of bone names found in the track data
#     """
#     # normalize to list-of-lists
#     if all_gani_tracks and isinstance(all_gani_tracks[0], GaniImportData):
#         track_lists = [d.gani_bone_tracks for d in all_gani_tracks]
#     else:
#         track_lists = all_gani_tracks  # type: ignore

#     known_bone_names = set()
#     for gani_tracks in track_lists:
#         for track_unit in gani_tracks:
#             for track_blob in track_unit.segments_track_data:
#                 if track_blob.name:
#                     known_bone_names.add(track_blob.name)
#     return known_bone_names


def gather_known_bone_names_from_mapping(track_segment_bone_mapping) -> set:
    """Gather all bone names that exist in track segment bone mapping.
    
    This builds a set of bone names from the export mapping, used to identify which
    bones are part of the actual animation data (vs Blender utility bones).
    
    Args:
        track_segment_bone_mapping: TrackSegmentBoneMapping object containing bone mappings
        
    Returns:
        Set of bone names found in the mapping
    """
    known_bone_names = set()
    for track_idx in track_segment_bone_mapping.get_track_indices():
        for segment_idx in track_segment_bone_mapping.get_segment_indices(track_idx):
            blender_bone_name, _ = track_segment_bone_mapping.get_segment_mapping(track_idx, segment_idx)
            if blender_bone_name:
                known_bone_names.add(blender_bone_name)
    return known_bone_names


def find_known_parent_bone(bone: bpy.types.Bone, known_bone_names: set) -> Tuple[Optional[bpy.types.Bone], List[str]]:
    """Walk up parent chain to find the nearest parent bone that exists in the known bone set.
    
    This is used to skip Blender utility/helper bones (like Rigify control bones) when
    calculating local space rest poses, ensuring offsets are calculated relative to bones
    that actually exist in the animation data.
    
    Args:
        bone: Starting bone to search from
        known_bone_names: Set of bone names that exist in the track/mapping data
        
    Returns:
        Tuple of (known_parent_bone, list_of_skipped_bone_names)
        - known_parent_bone: First parent found in known_bone_names, or None if not found
        - list_of_skipped_bone_names: Names of bones skipped while searching
    """
    current_parent = bone.parent
    skipped_bones = []
    
    while current_parent:
        if current_parent.name in known_bone_names:
            return current_parent, skipped_bones
        skipped_bones.append(current_parent.name)
        current_parent = current_parent.parent
    
    return None, skipped_bones


def extract_rest_pose_rotation(bone: bpy.types.Bone, is_world_space: bool, known_bone_names: set) -> Tuple[Euler, str]:
    """Extract rest pose rotation from a bone in either world space or local space.
    
    For world space bones (ORIENTATION, TWO_BONE, ARM), returns the bone's rotation
    in armature space. For local space bones, returns rotation relative to the nearest
    parent bone that exists in the known bone set (skipping utility bones).
    
    Args:
        bone: Bone to extract rest pose from
        is_world_space: True for world space (armature space), False for local space (parent-relative)
        known_bone_names: Set of bone names that exist in the track/mapping data
        
    Returns:
        Tuple of (euler_rotation, space_label_string)
        - euler_rotation: Rest pose as Euler angles (XYZ order)
        - space_label_string: Human-readable label describing the space and parent (for logging)
    """
    if is_world_space:
        # World space: use matrix_local (armature space)
        euler = bone.matrix_local.to_euler('XYZ')
        space_label = "world"
    else:
        # Local space: calculate rotation relative to closest known parent
        known_parent, skipped_bones = find_known_parent_bone(bone, known_bone_names)
        
        if known_parent:
            # Local rotation = known_parent_inverse @ bone_local
            parent_matrix_inv = known_parent.matrix_local.inverted()
            local_matrix = parent_matrix_inv @ bone.matrix_local
            euler = local_matrix.to_euler('XYZ')
            if skipped_bones:
                space_label = f"local (parent={known_parent.name}, skipped {len(skipped_bones)} utility bone(s))"
            else:
                space_label = f"local (parent={known_parent.name})"
        else:
            # No known parent found - use world space rotation
            euler = bone.matrix_local.to_euler('XYZ')
            if skipped_bones:
                space_label = f"world (no known parent, skipped {len(skipped_bones)} utility bone(s))"
            else:
                space_label = "world (no parent)"
    
    return euler, space_label


# Auxiliary Armature Detection Helpers #############################################################

def auto_detect_motion_points_armature(main_armature: bpy.types.Object) -> Optional[bpy.types.Object]:
    """Return a motion-points armature associated with *main_armature*.

    Detection order:
    1. A direct child parented to *main_armature* whose name ends with
       ``"_MotionPoints"``.
    2. Any other armature in the file whose name contains
       ``"_MotionPoints"``.

    This relies on the import code parenting the auxiliary armature to the
    main rig (see :mod:`tools_mtar_importer`).
    """
    if not main_armature:
        return None

    # children first (parenting is the most reliable marker)
    for child in main_armature.children:
        if child.type == 'ARMATURE' and child.name.endswith("_MotionPoints"):
            return child

    # fallback: global name search
    for obj in bpy.data.objects:
        if obj is main_armature or obj.type != 'ARMATURE':
            continue
        if "_MotionPoints" in obj.name:
            return obj

    return None


def auto_detect_shader_nodes_armature(main_armature: bpy.types.Object) -> Optional[bpy.types.Object]:
    """Return a shader-nodes armature associated with *main_armature*.

    The same rules as :func:`auto_detect_motion_points_armature` apply but with
    ``"_ShaderNodes"`` in the name.  Shader armatures are only relevant for
    old-format (GZ) MTAR exports, but we implement the detector unconditionally
    so callers can use it without needing extra format knowledge.
    """
    if not main_armature:
        return None

    for child in main_armature.children:
        if child.type == 'ARMATURE' and child.name.endswith("_ShaderNodes"):
            return child

    for obj in bpy.data.objects:
        if obj is main_armature or obj.type != 'ARMATURE':
            continue
        if "_ShaderNodes" in obj.name:
            return obj

    return None


def auto_detect_aux_armatures(main_armature: bpy.types.Object) -> tuple[Optional[bpy.types.Object], Optional[bpy.types.Object]]:
    """Shortcut that returns ``(motion_points, shader_nodes)`` armatures.

    This is mostly for callers that need to look up both objects at once.
    """
    return (
        auto_detect_motion_points_armature(main_armature),
        auto_detect_shader_nodes_armature(main_armature),
    )


# Export helpers ##############################################################

# NOTE: The helpers below are only used for auxiliary armatures (motion points /
# shader nodes / etc.) to get them back to a clean world space setup no matter what
# the parent armature does with root motion. 
# They are attached for auto-detection purposes only.
# The main export armature is never detached and should not be attached to anything.

def detach_armature_for_export(armature: bpy.types.Object) -> Optional[bpy.types.Object]:
    """Detach an armature from its parent and clear its transforms.

    This ensures auxiliary armatures export with an identity world transform regardless
    of any pre-existing offsets.

    Returns:
        The original parent object (or None if there was no parent).
    """
    parent = armature.parent

    armature.parent = None
    armature.parent_type = 'OBJECT'
    armature.parent_bone = ""
    armature.matrix_parent_inverse = Matrix.Identity(4)
    armature.matrix_world = Matrix.Identity(4)

    return parent


def restore_armature_after_export(armature: bpy.types.Object, parent: Optional[bpy.types.Object]) -> None:
    """Restore an armature's parent and clear transforms.

    The armature will remain at identity transform after restore (as intended for
    these auxiliary rigs)."""
    armature.parent = parent
    armature.parent_type = 'OBJECT'
    armature.parent_bone = ""
    armature.matrix_parent_inverse = Matrix.Identity(4)
    armature.matrix_world = Matrix.Identity(4)


# Pose Bone Utilities #########################################################

def clear_rest_pose_from_bone(pose_bone: bpy.types.PoseBone) -> None:
    """Place the given pose bone at the armature origin by setting its matrix to identity.

    This makes the bone's world transform match its parent armature's world transform.
    Does NOT keyframe any values — intended for live pose manipulation.
    """
    pose_bone.matrix = Matrix.Identity(4)
