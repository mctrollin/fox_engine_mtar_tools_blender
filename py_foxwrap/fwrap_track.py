"""
Shared GANI track naming helpers used by both GANI1 and GANI2 readers.
"""
from typing import List, Optional

import bpy
from mathutils import Quaternion, Vector

from ..py_core.core_logging import Debug

from ..py_utilities import util_blender_animation, util_fcurve_processing, util_transforms, util_hashing, util_hashing_cityhash

from ..py_fox.fox_gani_enums import SegmentType
from ..py_fox.fox_gani_types import AnimKeyframe
from ..py_fox.fox_frig_types import RigUnitType
from ..py_fox.fox_hash_types import StrCode32

from . import fwrap_metadata
from .fwrap_mapping_types import BoneParameters
from .fwrap_track_types import TrackUnitWrapper, TrackDataBlobWrapper


# Naming ####################################################

def resolve_track_name(rig_hash: StrCode32, prefix: Optional[str] = None) -> str:
    """Resolve a StrCode32 hash to a readable name."""
    bone_name = util_hashing.unhash_rig_type(rig_hash.to_int())
    if bone_name:
        return bone_name
    hex_str = str(rig_hash)
    return f"{prefix}_{hex_str}" if prefix else hex_str


def apply_track_naming(gani_tracks: List[TrackUnitWrapper], prefix: Optional[str] = None, use_decimal_only: bool = False) -> List[TrackUnitWrapper]:
    """Apply name resolution to a list of GaniTracks."""
    named_tracks: List[TrackUnitWrapper] = []

    for gani_track in gani_tracks:
        if use_decimal_only:
            resolved_name = str(gani_track.name)
        else:
            resolved_name = resolve_track_name(gani_track.name, prefix)

        named_keyframes_tracks = []
        for keyframe_track in gani_track.segments_track_data:
            named_track = TrackDataBlobWrapper(
                name=resolved_name,
                segment_index=keyframe_track.segment_index,
                data_blob=keyframe_track.data_blob
            )
            named_keyframes_tracks.append(named_track)

        named_gani_track = TrackUnitWrapper(
            name=resolved_name,
            segments_track_data=named_keyframes_tracks,
            unit_flags=gani_track.unit_flags,
            rig_unit_type=gani_track.rig_unit_type
        )
        named_tracks.append(named_gani_track)

    return named_tracks


def _apply_stringlist_names(
    tracks: List[TrackUnitWrapper],
    string_list: Optional[List[str]],
    label: str,
) -> None:
    """Apply names from a reference string list (SKL_LIST / MTP_LIST)."""
    if not string_list:
        return

    skl_lookup: dict = {}
    for entry in string_list:
        if not util_hashing.is_hash_string(entry):
            h = util_hashing_cityhash.strcode32(entry)
            skl_lookup[h] = entry

    for track in tracks:
        name = track.name
        is_hash_fallback = util_hashing.is_hash_string(name)
        track_hash = util_hashing.hash_or_parse_name(name)

        if track_hash in skl_lookup:
            skl_name = skl_lookup[track_hash]
            if not is_hash_fallback and name != skl_name:
                Debug.log_warning(
                    f"_apply_stringlist_names() [{label}]: hash 0x{track_hash:08X} — "
                    f"dict resolved ('{name}') differs from ('{skl_name}') which will be used."
                )
            track.name = skl_name
            for seg in track.segments_track_data:
                seg.name = skl_name
        elif is_hash_fallback:
            Debug.log(
                f"_apply_stringlist_names ({label}): hash 0x{track_hash:08X} ('{name}') "
                f"has no list entry — keeping ('{name}')."
            )


def apply_segment_suffixes_to_tracks(gani_tracks: List[TrackUnitWrapper]) -> List[TrackUnitWrapper]:
    """Apply _N suffix to TrackDataBlobWrapper names for multi-segment tracks."""
    for gani_track in gani_tracks:
        if len(gani_track.segments_track_data) <= 1:
            continue
        for segment_blob in gani_track.segments_track_data:
            if segment_blob.segment_index > 0:
                segment_blob.name = f"{segment_blob.name}_{segment_blob.segment_index}"
    return gani_tracks


def finalize_bone_tracks(
    tracks: List[TrackUnitWrapper],
    skeleton_list: Optional[List[str]] = None,
    label: str = "GANI",
) -> List[TrackUnitWrapper]:
    """Apply track naming (unhashing) and segment suffixes to bone tracks."""
    named: List[TrackUnitWrapper] = apply_track_naming(tracks, prefix=None)
    if skeleton_list is not None:
        _apply_stringlist_names(named, skeleton_list, label=f"Read {label} SKL_LIST")
    apply_segment_suffixes_to_tracks(named)
    return named


def finalize_tracks(tracks: List[TrackUnitWrapper]) -> List[TrackUnitWrapper]:
    """Apply track naming and segment suffixes to general tracks (e.g. motion point tracks)."""
    named: List[TrackUnitWrapper] = apply_track_naming(tracks, use_decimal_only=True)
    apply_segment_suffixes_to_tracks(named)
    return named


# To Keyframes data ####################################################

def _get_rotation_transform_fn(
    bone_params: BoneParameters,
    armature: bpy.types.Object,
    blender_bone_name: str,
    space_bone: Optional[str],
    rig_unit_type: Optional[RigUnitType],
    transform_cache: Optional[util_transforms.TransformsCache] = None,
    use_object_level: bool = False
):
    """Return a callable that produces rotation quaternion for a given frame.

    This helper eliminates code duplication between object-level root motion
    tracks, as_ik_up conversion, and normal bone rotation paths.

    Args:
        bone_params: Bone parameters (contains as_ik_up data if applicable)
        armature: Armature object
        blender_bone_name: Name of the bone in Blender
        space_bone: Custom space bone name (or None for default space)
        rig_unit_type: Rig unit type (determines local vs world space for normal tracks)
        transform_cache: Optional pre-computed transform cache
        use_object_level: If True, read rotation from the armature object instead of a pose bone

    Returns:
        Callable that takes (frame: int) and returns Quaternion
    """
    if use_object_level:
        # Object-level rotation (root motion) is stored on the armature object
        # rather than a pose bone.
        def get_rotation_object_level(frame: int) -> Quaternion:
            if transform_cache:
                rot = transform_cache.get_object_rotation(frame)
                if rot is not None:
                    return rot
                Debug.log_warning(
                    f"Export rotation: TransformCache missing armature object rotation for frame {frame}; "
                    f"falling back to armature.matrix_world"
                )
            bpy.context.scene.frame_set(frame)
            return armature.matrix_world.to_quaternion()

        return get_rotation_object_level

    if bone_params.as_ik_up:
        # as_ik_up path: convert directional location to rotation
        as_ik_up_data = bone_params.as_ik_up
        axis = as_ik_up_data.axis
        base_bone_name = as_ik_up_data.bone_base
        
        def get_rotation_as_ik_up(frame: int) -> Quaternion:
            if transform_cache:
                ik_location, _ = transform_cache.get_world(blender_bone_name, frame, space_bone)
                base_location, _ = transform_cache.get_world(base_bone_name, frame, space_bone)
            else:
                ik_location, _ = util_transforms.get_world_space_transform(armature, blender_bone_name, frame, space_bone)
                base_location, _ = util_transforms.get_world_space_transform(armature, base_bone_name, frame, space_bone)
            return util_transforms.reverse_directional_location(ik_location, base_location, axis)
        
        return get_rotation_as_ik_up
    else:
        # Normal rotation path: read quaternion directly
        use_world_space = RigUnitType.is_world_space_unit_type(rig_unit_type)
        
        def get_rotation_normal(frame: int) -> Quaternion:
            if transform_cache:
                if use_world_space:
                    _, quat = transform_cache.get_world(blender_bone_name, frame, space_bone)
                else:
                    _, quat = transform_cache.get_local(blender_bone_name, frame)
            else:
                if use_world_space:
                    _, quat = util_transforms.get_world_space_transform(armature, blender_bone_name, frame, space_bone)
                else:
                    _, quat = util_transforms.get_local_space_transform(armature, blender_bone_name, frame)
            return quat
        
        return get_rotation_normal

def _export_rotation_segment(
    armature: bpy.types.Object,
    blender_bone_name: str,
    bone_params: BoneParameters,
    export_frames: List[int],
    frame_start: int,
    is_static: bool,
    rig_unit_type: Optional[RigUnitType] = None,
    transform_cache: Optional[util_transforms.TransformsCache] = None,
    use_object_level: bool = False,
) -> List[AnimKeyframe]:
    """Export rotation segment keyframes."""
    keyframes = []
    # Debug.start_timer("export_rotation_segment")
    
    # POINT 4 OPTIMIZATION: Extract loop-invariant setup and use pluggable transform function
    # These are constant across all frames, so extract once to avoid redundant lookups
    rotation_offset = bone_params.rotation_offset
    rotation_axis_map = bone_params.rotation_axis_map
    
    if use_object_level:
        # Track maps to the armature object via [armature] mapping target.
        # Rotation FCurves are stored as bare 'rotation_quaternion' on the object.
        # Read the object rotation directly — no bone-space or rest-pose corrections.
        space_bone = None
        space_r_value = None
        map_r_dict = None
    else:
        # For as_ik_up bones, use space_ik instead of space_r for the space bone
        # This is because space_ik defines the transformation constraint space for IK targets
        if bone_params.as_ik_up:
            space_bone = fwrap_metadata.extract_space_bone_name(bone_params.space_ik)
        else:
            space_bone = fwrap_metadata.extract_space_bone_name(bone_params.space_r)
        
        # Extract rest pose correction parameters
        space_r_value = bone_params.space_r
        map_r_dict = bone_params.map_r

    # Get rotation transform function (varies by mode / space type)
    get_rotation = _get_rotation_transform_fn(
        bone_params, armature, blender_bone_name, space_bone,
        rig_unit_type, transform_cache, use_object_level=use_object_level
    )

    # Unified frame loop for both as_ik_up, normal rotation, and object-level rotation
    prev_frame = frame_start  # Track previous frame for relative delta computation
    prev_blender_quat_transformed: Quaternion = None
    for frame in export_frames:
        # Set frame explicitly for performance (if no cache present)
        if not transform_cache:
            bpy.context.scene.frame_set(frame)
        
        # Get rotation using appropriate method (as_ik_up, normal, or object-level)
        blender_quat: Quaternion = get_rotation(frame)

        # Apply reverse rest pose corrections (must happen BEFORE axis mapping and offsets)
        # World space tracks (space_r=world): reverse offset_r using simple multiplication
        # Local space tracks (default): reverse map_r using similarity transformation
        if space_r_value and isinstance(space_r_value, dict) and space_r_value.get('space') == 'WORLD':
            # World space track - reverse offset_r if present
            if rotation_offset:
                # Use first offset as the offset_r (world space offset)
                blender_quat = util_transforms.reverse_rest_pose_correction_world(blender_quat, rotation_offset[0])
        elif map_r_dict:
            # Local space track - reverse similarity transformation
            blender_quat = util_transforms.reverse_rest_pose_correction_local(blender_quat, map_r_dict)

        # Apply reverse transformations (offsets, axis mapping)
        blender_quat_transformed = util_transforms.apply_reverse_transforms(blender_quat, rotation_offset, rotation_axis_map)
        if prev_blender_quat_transformed is not None:
            blender_quat_transformed.make_compatible(prev_blender_quat_transformed)
        prev_blender_quat_transformed = blender_quat_transformed.copy()
        
        # Convert to Fox Engine coordinate system
        fox_quat_final = util_transforms.blender_to_fox_quaternion(blender_quat_transformed)

        # Create keyframe with relative frame delta from previous frame
        if is_static:
            frame_delta = 0
        elif not keyframes:
            frame_delta = 0  # First keyframe is always delta=0
        else:
            frame_delta = frame - prev_frame
            if frame_delta < 1:
                Debug.log_warning(f"Export rotation: Invalid frame_delta {frame_delta} at frame {frame} for bone '{blender_bone_name}'. Clamping to 1.")
                frame_delta = 1
            elif frame_delta > 255:
                Debug.log_error(f"Export rotation: INVALID FILE - frame_delta {frame_delta} exceeds the 255-frame binary limit at frame {frame} for bone '{blender_bone_name}'. Delta clamped to 255 but this corrupts all subsequent keyframe timings. Reduce the export clean threshold.")
                frame_delta = 255
        prev_frame = frame
        keyframe = AnimKeyframe(frame=frame_delta, value=fox_quat_final)
        keyframes.append(keyframe)
    
    # Debug.stop_timer("export_rotation_segment")
    return keyframes


def _export_location_segment(
    armature: bpy.types.Object,
    blender_bone_name: str,
    bone_params: BoneParameters,
    export_frames: List[int],
    frame_start: int,
    is_static: bool,
    rig_unit_type: Optional[RigUnitType] = None,
    transform_cache: Optional[util_transforms.TransformsCache] = None,
    no_coordinate_transform: bool = False,
    num_components: int = 3,
    use_object_level: bool = False,
) -> List[AnimKeyframe]:
    """Export location segment keyframes.

    Args:
        no_coordinate_transform: When True (used for FLOAT and VECTOR2 segment types),
            skips the Blender↔Fox axis-swap, always uses local space, and returns only
            the first ``num_components`` raw channel values. This is correct because
            FLOAT/VECTOR2 are auxiliary data channels (e.g. blend weights, parameters)
            stored without any coordinate-system conversion during import.
        num_components: Number of output components to include in each keyframe value
            when ``no_coordinate_transform=True``. 1 for FLOAT, 2 for VECTOR2, 3 for
            VECTOR3 (default).

    Note: When space_l=custom,<custom_bone> is used, the import creates a Copy Location
    constraint with X and Y axes inverted. During export we reverse this by inverting
    X and Y again. This does NOT apply when no_coordinate_transform=True.
    """
    keyframes = []
    # Debug.start_timer("export_location_segment")

    if use_object_level:
        # Track maps to the armature object via [armature] mapping target.
        # Location FCurves are stored as bare 'location' on the object.
        # Read the object location directly — no bone-space or axis corrections.
        space_bone = None
        use_world_space = False  # not used when use_object_level=True
        invert_xy = False
    else:
        # Get custom space if specified (constant across all frames)
        # Use the same extraction logic as rotation export for consistency
        space_bone = fwrap_metadata.extract_space_bone_name(bone_params.space_l)

        # For FLOAT/VECTOR2 (no_coordinate_transform=True): raw channel, always local
        # space, no axis-swap, no custom-space or invert-XY correction.
        if no_coordinate_transform:
            use_world_space = False
            invert_xy = False
            space_bone = None
        else:
            # Check if we need to invert X and Y (when using custom space bone)
            # Import creates constraint with invert_x=True, invert_y=True when custom_bone is specified
            # So we need to reverse that during export
            invert_xy = space_bone is not None
            # is_world_space result is constant across all frames
            use_world_space = RigUnitType.is_world_space_unit_type(rig_unit_type)
    
    # For regular location: read and convert per frame
    prev_frame = frame_start  # Track previous frame for relative delta computation
    for frame in export_frames:
        # Set frame explicitly for performance (if no cache present)
        if not transform_cache:
            bpy.context.scene.frame_set(frame)

        if use_object_level:
            # Read object-level location directly from FCurves or armature transform
            if transform_cache:
                blender_location = transform_cache.get_object_location(frame)
                if blender_location is None:
                    Debug.log_warning(
                        f"Export location: TransformCache missing armature object location for frame {frame}; "
                        f"falling back to armature.matrix_world"
                    )
                    blender_location = Vector((0, 0, 0))
            else:
                blender_location = armature.matrix_world.to_translation()
        # Read location (using pre-determined space)
        elif transform_cache:
            if use_world_space:
                blender_location, _ = transform_cache.get_world(blender_bone_name, frame, space_bone)
            else:
                blender_location, _ = transform_cache.get_local(blender_bone_name, frame)
        else:
            if use_world_space:
                # Use world space transforms for ORIENTATION, TWO_BONE, ARM
                blender_location, _ = util_transforms.get_world_space_transform(armature, blender_bone_name, frame, space_bone)
            else:
                # Use local space transforms for other types (LOCAL_ORIENTATION, TRANSFORM, ROOT, etc.)
                blender_location, _ = util_transforms.get_local_space_transform(armature, blender_bone_name, frame)
        
        # Reverse X and Y inversion if custom space bone was used during import
        if invert_xy:
            blender_location = blender_location.copy()
            blender_location.x = -blender_location.x
            blender_location.y = -blender_location.y

        # Convert to Fox Engine coordinate system (or take raw channels for FLOAT/VECTOR2)
        if no_coordinate_transform:
            fox_location = list(blender_location)[:num_components]
        else:
            fox_location = util_transforms.blender_to_fox_vector(blender_location)
        
        # Create keyframe with relative frame delta from previous frame
        if is_static:
            frame_delta = 0
        elif not keyframes:
            frame_delta = 0  # First keyframe is always delta=0
        else:
            frame_delta = frame - prev_frame
            if frame_delta < 1:
                Debug.log_warning(f"Export location: Invalid frame_delta {frame_delta} at frame {frame} for bone '{blender_bone_name}'. Clamping to 1.")
                frame_delta = 1
            elif frame_delta > 255:
                Debug.log_error(f"Export location: INVALID FILE - frame_delta {frame_delta} exceeds the 255-frame binary limit at frame {frame} for bone '{blender_bone_name}'. Delta clamped to 255 but this corrupts all subsequent keyframe timings. Reduce the export clean threshold.")
                frame_delta = 255

        prev_frame = frame
        keyframe = AnimKeyframe(frame=frame_delta, value=fox_location)
        keyframes.append(keyframe)
    
    # Debug.stop_timer("export_location_segment")
    return keyframes


def export_keyframes_track(
    armature: bpy.types.Object,
    blender_bone_name: str,
    bone_params: BoneParameters,
    segment_type: SegmentType,
    frame_start: int,
    frame_end: int,
    is_static: bool,
    action: bpy.types.Action = None,
    rig_unit_type: Optional[RigUnitType] = None,
    fcurve_cache: Optional[util_blender_animation.FCurveCache] = None,
    transform_cache: Optional[util_transforms.TransformsCache] = None,
    use_object_level: bool = False
) -> List[AnimKeyframe]:
    """Export a single track data segment (one segment of a bone's animation).
    
    This is the export counterpart to import_keyframes_track().
    
    Args:
        armature: Armature object
        blender_bone_name: Name of the bone in Blender
        bone_params: BoneParameters from mapping file (rotation_offset, axis_map, space_r, space_l, as_ik_up)
        segment_type: Type of this segment (from layout track metadata)
        frame_start: First frame to export
        frame_end: Last frame to export
        is_static: Whether this is a static track (single frame)
        action: Blender action to get actual keyframe frames from
        rig_unit_type: Type of rig unit (determines if world space transforms are needed)
        fcurve_cache: Optional pre-built util_blender_animation.FCurveCache for fast lookups
        transform_cache: Optional pre-computed transform cache for all bones/frames
        
    Returns:
        List of AnimKeyframe objects
    """
    # Determine frame range
    if is_static:
        export_frames = [frame_start]
    elif use_object_level and action:
        # Root motion is on the armature object: get frame list from object FCurves
        export_frames = util_blender_animation.get_object_keyframe_numbers(action, segment_type, frame_start, frame_end)
    elif action:
        # Get actual keyframe frames from Blender fcurves
        export_frames = util_blender_animation.get_bone_keyframe_numbers_from_action(
                action,
                blender_bone_name,
                segment_type,
                frame_start,
                frame_end,
                bone_params and bone_params.as_ik_up,
                fcurve_cache
            )
    else:
        # Fallback: export all frames
        export_frames = list(range(frame_start, frame_end + 1))
    
    # ── Non-static track validation ──────────────────────────────────────────
    # The GANI binary format reads animated keyframes in a loop:
    #   do { read AnimKeyframe; frameIndex += FrameCount; } while (frameIndex < FrameCount);
    # so the accumulated frame deltas MUST reach at least FrameCount
    # (= frame_end - frame_start). If after FCurve cleaning only 1 keyframe
    # remains (frame_start), the loop has no data to read and parses garbage.
    if not is_static:
        # 1. Ensure frame_end is always present so deltas sum to FrameCount
        if len(export_frames) < 2 or export_frames[-1] < frame_end:
            if frame_end not in export_frames:
                Debug.log(
                    f"Non-static track '{blender_bone_name}' ({segment_type.name}): "
                    f"only {len(export_frames)} keyframe(s) found after FCurve cleaning, "
                    f"missing frame_end ({frame_end}). Adding it to prevent invalid binary output."
                )
                export_frames.append(frame_end)
                export_frames = sorted(set(export_frames))

        # 2. Fill gaps > 255 with intermediate frames (8-bit delta limit)
        export_frames, inserted = util_fcurve_processing.insert_intermediate_frames(export_frames)
        if inserted:
            Debug.log(
                f"Non-static track '{blender_bone_name}' ({segment_type.name}): "
                f"inserted {inserted} intermediate frame(s) to keep frame deltas within the 255-frame binary limit."
            )
    # ────────────────────────────────────────────────────────────────────────

    Debug.log(f"    Collected keyed frames {len(export_frames)}")

    if segment_type in [SegmentType.QUAT, SegmentType.QUAT_DIFF]:
        # Rotation segment
        return _export_rotation_segment(
            armature, 
            blender_bone_name, 
            bone_params,
            export_frames, 
            frame_start, 
            is_static, 
            rig_unit_type,
            transform_cache,
            use_object_level=use_object_level,
        )
    
    elif segment_type in [SegmentType.VECTOR3, SegmentType.VECTOR_DIFF]:
        # Location segment
        return _export_location_segment(
            armature, 
            blender_bone_name, 
            bone_params,
            export_frames, 
            frame_start, 
            is_static, 
            rig_unit_type,
            transform_cache,
            use_object_level=use_object_level,
        )

    elif segment_type == SegmentType.FLOAT:
        # Raw scalar channel stored as location[0] — no axis-swap, local space only.
        return _export_location_segment(
            armature, blender_bone_name, bone_params,
            export_frames, frame_start, is_static,
            rig_unit_type, transform_cache,
            no_coordinate_transform=True, num_components=1
        )

    elif segment_type == SegmentType.VECTOR2:
        # Raw [x, y] channel stored as location[0,1] — no axis-swap, local space only.
        return _export_location_segment(
            armature, blender_bone_name, bone_params,
            export_frames, frame_start, is_static,
            rig_unit_type, transform_cache,
            no_coordinate_transform=True, num_components=2
        )

    else:
        # Unsupported segment type
        if segment_type == SegmentType.VECTOR4:
            # VECTOR4 has no FCurve representation; export produces a zeroed segment.
            # Round-trip fidelity requires the layout action to preserve the VECTOR4
            # segment type so the exporter knows to include it.
            Debug.log_warning(
                f"    Segment type VECTOR4 on bone '{blender_bone_name}' is not supported "
                f"as Blender FCurves. Exporting zeroed segment. Round-trip fidelity "
                f"requires the layout action to contain this track's VECTOR4 segment type."
            )
        else:
            Debug.log_warning(f"    Warning: Unsupported segment type {segment_type}")
        return []
