# Rest Pose Utilities #############################################################

def gather_known_bone_names_from_tracks(all_gani_tracks: List[List]) -> set:
    """Gather all bone names that exist in track data.
    
    This builds a set of bone names from track wrappers, used to identify which
    bones are part of the actual animation data (vs Blender utility bones).
    
    Args:
        all_gani_tracks: List of track lists (each inner list contains TrackUnitWrapper objects)
        
    Returns:
        Set of bone names found in the track data
    """
    known_bone_names = set()
    for gani_tracks in all_gani_tracks:
        for track_unit in gani_tracks:
            for track_blob in track_unit.segments_track_data:
                if track_blob.name:
                    known_bone_names.add(track_blob.name)
    return known_bone_names


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


def find_known_parent_bone(bone: 'bpy.types.Bone', known_bone_names: set) -> Tuple[Optional['bpy.types.Bone'], List[str]]:
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


def extract_rest_pose_rotation(bone: 'bpy.types.Bone', 
                               is_world_space: bool,
                               known_bone_names: set) -> Tuple[Euler, str]:
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
