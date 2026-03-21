"""
Export-only fake types for MTAR exporter.
"""
from typing import Optional, List, Dict, Tuple, Callable

import bpy

from ..py_core.core_logging import Debug

from ..py_utilities import util_blender_animation
from ..py_utilities import util_parsing

from ..py_fox.fox_gani_types import TrackUnitFlags, SegmentType
from ..py_fox.fox_misc_types import StrCode32

from .fwrap_misc_export_types import ExportActionData, TrackSegmentBoneMapping
from .fwrap_mapping_types import BoneParameters
from . import fwrap_metadata


def collect_armature_actions(
    armature: bpy.types.Object,
    use_nla: bool,
    track_type_label: str,
    export_clean_threshold: float = 0.0,
) -> List['ExportActionData']:
    """Collect animation actions from *armature* for export.

    This is the shared implementation used by all three track types (motion
    points, shader nodes, and — via wrappers — the main animation tracks).
    The only difference between the three callers is the human-readable
    *track_type_label* used in log messages.

    Args:
        armature:               Armature object to collect actions from.
        use_nla:                If ``True``, collect from unmuted NLA strips;
                                if ``False``, use the active action.
        track_type_label:       Human-readable label for log messages
                                (e.g. ``"motion points"``, ``"shader nodes"``).
        export_clean_threshold: FCurve cleaning threshold (0 = disabled).

    Returns:
        List of :class:`ExportActionData` objects (may be empty).
    """
    if not armature:
        return []

    Debug.log(f"\nCollecting {track_type_label} actions from '{armature.name}'...")

    actions: List[ExportActionData] = []

    if (
        use_nla
        and armature.animation_data
        and armature.animation_data.nla_tracks
    ):
        Debug.log(f"  Using NLA strips for {track_type_label}")
        for track in armature.animation_data.nla_tracks:
            if track.mute:
                continue
            for strip in track.strips:
                if not util_blender_animation.is_relevant_strip(strip):
                    if strip.action:
                        Debug.log(
                            f"    Skipping {track_type_label} strip "
                            f"'{getattr(strip, 'name', '<unknown>')}' "
                            f"(not a GANI strip)"
                        )
                    continue

                action_data = ExportActionData(
                    action=strip.action,
                    frame_start=int(strip.frame_start),
                    frame_end=int(strip.frame_end),
                    source=f"NLA strip '{strip.name}' on track '{track.name}'",
                    export_clean_threshold=export_clean_threshold,
                )
                actions.append(action_data)
                Debug.log(f"    {action_data.to_string()}")

    elif armature.animation_data and armature.animation_data.action:
        Debug.log(f"  Using active action for {track_type_label}")
        action = armature.animation_data.action

        if util_blender_animation.action_has_fcurves(action):
            frame_start = int(
                min(kp.co.x for fc in util_blender_animation.iter_action_fcurves(action) for kp in fc.keyframe_points)
            )
            frame_end = int(
                max(kp.co.x for fc in util_blender_animation.iter_action_fcurves(action) for kp in fc.keyframe_points)
            )
        else:
            frame_start = 0
            frame_end = 0

        action_data = ExportActionData(
            action=action,
            frame_start=frame_start,
            frame_end=frame_end,
            source="Active action",
            export_clean_threshold=export_clean_threshold,
        )
        actions.append(action_data)
        Debug.log(f"    {action_data.to_string()}")

    else:
        Debug.log(f"  No {track_type_label} actions found")

    return actions


def build_track_metadata_dict_from_fcurves(
    armature: bpy.types.Object,
    action: bpy.types.Action,
    armature_label: str,
    bone_skip_predicate: Optional[Callable[[bpy.types.Bone], bool]] = None,
    name_hash_extractor_fn: Optional[Callable[[str, bpy.types.Bone], Optional[int]]] = None,
    warn_on_missing_metadata: bool = True,
) -> Dict[str, fwrap_metadata.TrackMetaData]:
    """Build a per-bone metadata dictionary by inspecting FCurves and stored properties.

    This is the shared implementation used by motion-point and shader-node export.
    Both callers have no layout-track action, so segment types are inferred from
    FCurve existence (``rotation_quaternion`` → QUAT, ``location`` → VECTOR3 or
    FLOAT) and bit-sizes / flags are read from the action's stored metadata.

    The helper also reports bones that are animated but lack an explicit
    metadata string; this is useful for layout‑track exports but generates
    noise for motion-point/shader exports.  Set ``warn_on_missing_metadata`` to
    ``False`` to suppress the warning and emit only a debug message instead.

    Args:
        armature:              Armature object whose bones are iterated.
        action:                Blender action to inspect for FCurves and metadata.
        armature_label:        Human-readable label for warning messages
                               (e.g. ``"motion points"``).
        bone_skip_predicate:   Optional callable ``(bone) -> bool``; return
                               ``True`` to skip a bone entirely.  Used by the
                               shader caller to skip property-parent bones (those
                               with no parent of their own).
        name_hash_extractor_fn: Optional callable ``(bone_name, bone) -> int|None``;
                               returns the StrCode32 hash to store in
                               :attr:`fwrap_metadata.TrackMetaData.name_hash`.  When ``None``,
                               the hash is computed via
                               ``StrCode32.from_string(bone_name).to_int()``.
                               The shader caller passes a function that parses the
                               decimal suffix after the last ``.`` in the bone name.
        warn_on_missing_metadata: If ``True`` (the default), log a warning when
                               bones have animation but no stored metadata.
                               Set to ``False`` to downgrade the message to
                               a normal debug log.

    Returns:
        ``{bone_name: fwrap_metadata.TrackMetaData}`` for every bone present in *action*.
    """
    metadata_dict: Dict[str, fwrap_metadata.TrackMetaData] = {}

    if not armature or armature.type != 'ARMATURE':
        return metadata_dict

    if not action:
        Debug.log_warning(
            f"  Warning: No action provided to build_track_metadata_dict_from_fcurves() "
            f"for {armature_label} armature '{armature.name}', returning empty dict"
        )
        return metadata_dict

    missing_metadata_bones: List[str] = []

    for bone in armature.data.bones:
        if bone_skip_predicate is not None and bone_skip_predicate(bone):
            continue

        bone_name = bone.name

        # Determine whether we have any FCurves for this bone, used for
        # deciding if the bone is present in the action at all.  We also
        # infer segment types (and default bit sizes) from the curves when
        # needed.
        has_fcurves = util_blender_animation.action_has_fcurves(action)
        segment_types: List[SegmentType] = []
        default_bits: Optional[List[int]] = None
        if has_fcurves:
            segment_types, default_bits = fwrap_metadata.infer_segment_types_from_fcurves(action, bone_name)

        # has_rotation/has_location flags used only for the old bool-based
        # detection; they are no longer needed.

        component_bit_sizes = None
        unit_flags = 0
        found_metadata_in_action = False

        for _, track_name, metadata_str in fwrap_metadata.iter_track_properties(action):
            if track_name == bone_name:
                found_metadata_in_action = True
                if isinstance(metadata_str, str):
                    parsed = fwrap_metadata.parse_action_track_metadata(metadata_str)
                    if parsed:
                        if parsed.get('component_bit_sizes'):
                            component_bit_sizes = parsed['component_bit_sizes']
                        if parsed.get('flags'):
                            flag_enums = [
                                TrackUnitFlags[name]
                                for name in parsed['flags']
                                if name in TrackUnitFlags.__members__
                            ]
                            if flag_enums:
                                unit_flags = TrackUnitFlags.track_unit_flags_to_int(flag_enums)
                break

        bone_present_in_action = found_metadata_in_action or has_fcurves
        if bone_present_in_action and not found_metadata_in_action:
            missing_metadata_bones.append(bone_name)

        if not bone_present_in_action:
            continue

        # If we haven't already inferred segment_types above, do it now.  The
        # helper may return an empty list in pathological cases (no fcurves);
        # fall back to metadata-only FLOAT detection as before.
        if not segment_types:
            segment_types = []
        if not segment_types and found_metadata_in_action:
            segment_types.append(SegmentType.FLOAT)

        # ensure component_bit_sizes defaults are assigned when no explicit
        # metadata was found
        if component_bit_sizes is None:
            component_bit_sizes = default_bits

        # Compute name hash
        if name_hash_extractor_fn is not None:
            name_hash_int = name_hash_extractor_fn(bone_name, bone)
        else:
            name_hash_int = StrCode32.from_string(bone_name).to_int()

        metadata_dict[bone_name] = fwrap_metadata.TrackMetaData(
            track_name=bone_name,
            segment_types=segment_types,
            unit_flags=unit_flags,
            name_hash=name_hash_int,
            component_bit_sizes=component_bit_sizes,
            rig_unit_type=None,
        )

    if missing_metadata_bones:
        message = (
            f"  No stored metadata for {len(missing_metadata_bones)} "
            f"{armature_label} bone(s) in armature '{armature.name}': "
            + ", ".join(missing_metadata_bones)
        )
        if warn_on_missing_metadata:
            Debug.log_warning(message)
        else:
            Debug.log(message)

    return metadata_dict



# Helper utilities for motion-point action matching ################################

def extract_gani_metadata(name: str) -> Optional[Tuple[int, str]]:
    """Extract (index, type) from action/strip name using new schema.
    
    Schema: <mtar-name>.<animation-parts>.<index>.<type>.(gani|strip)
    Handles both new and old formats with backward compatibility.
    
    Args:
        name: Action or strip name
        
    Returns:
        Tuple of (index, type) where type is 'track' or 'motionpoints'
        Returns None if name doesn't match expected schema
    """
    # Remove file extension
    if name.endswith('.gani'):
        name_no_ext = name[:-5]
    elif name.endswith('.strip'):
        name_no_ext = name[:-6]
    else:
        # Try old format detection: look for .motionpoints suffix
        if '.motionpoints.' in name:
            name_no_ext = name.replace('.gani', '').replace('.strip', '')
        else:
            return None
    
    parts = name_no_ext.split('.')
    if len(parts) < 4:  # At minimum: mtar, animation, index, type
        return None
    
    try:
        # Last two components are index and type
        gani_type = parts[-1]
        index = int(parts[-2])
        
        # Validate type
        if gani_type not in ('track', 'motionpoints', 'shadernodes'):
            # Backward compatibility: old format has no explicit type
            # Try to detect old .motionpoints suffix
            if '.motionpoints' in name:
                return (index, 'motionpoints')
            return None
        
        return (index, gani_type)
    except (ValueError, IndexError):
        pass
    
    return None


def build_action_maps_by_tag(
    actions: List[ExportActionData],
    expected_type_tag: str,
) -> Dict[int, ExportActionData]:
    """Build a lookup map for actions indexed by extracted GANI running index.

    Only actions whose embedded type-tag matches *expected_type_tag* are
    included.  Any action that cannot be parsed or has the wrong tag is logged
    as a warning and skipped.

    Args:
        actions:            List of :class:`ExportActionData` to index.
        expected_type_tag:  The type-tag string to accept (e.g.
                            ``'motionpoints'`` or ``'shadernodes'``).

    Returns:
        ``{running_index: ExportActionData}``
    """
    by_gani_index: Dict[int, ExportActionData] = {}

    for a in actions:
        result = extract_gani_metadata(a.action.name)
        if result:
            idx, gani_type = result
            if gani_type == expected_type_tag:
                if idx not in by_gani_index:
                    by_gani_index[idx] = a
            else:
                Debug.log_warning(
                    f"Warning: Action '{a.action.name}' has type '{gani_type}', "
                    f"expected '{expected_type_tag}' - this action will be skipped"
                )
        else:
            Debug.log_warning(
                f"Warning: No GANI index found in action name '{a.action.name}' - "
                f"this action will be skipped"
            )

    return by_gani_index


def find_action_for_gani(
    gani_name: str,
    by_gani_index: Dict[int, ExportActionData],
    track_label: str = "data",
) -> Optional[ExportActionData]:
    """Find the action matching a main GANI track name by running index.

    Args:
        gani_name:      Name of the GANI track action whose index should be matched.
        by_gani_index:  Lookup map built by :func:`build_action_maps_by_tag`.
        track_label:    Human-readable label for warning messages (e.g.
                        ``'motion points'`` or ``'shader nodes'``).

    Returns:
        :class:`ExportActionData` if found, else ``None``.
    """
    result = extract_gani_metadata(gani_name)
    if result:
        idx, gani_type = result
        if gani_type == 'track':
            return by_gani_index.get(idx)
        else:
            Debug.log_warning(
                f"Warning: GANI '{gani_name}' has type '{gani_type}', expected 'track' - "
                f"{track_label} will be skipped for this GANI"
            )
    else:
        Debug.log_warning(
            f"Warning: No GANI index could be extracted from GANI name '{gani_name}' - "
            f"{track_label} will be skipped for this GANI"
        )
    return None


def build_motion_point_action_maps(motion_point_actions: List[ExportActionData]) -> Dict[int, ExportActionData]:
    """Build lookup map for motion point actions indexed by extracted GANI index."""
    return build_action_maps_by_tag(motion_point_actions, expected_type_tag='motionpoints')


def find_motion_point_action_for_gani(gani_name: str, by_gani_index: Dict[int, ExportActionData]) -> Optional[ExportActionData]:
    """Find the motion point action matching a GANI using only extracted running index."""
    return find_action_for_gani(gani_name, by_gani_index, track_label="motion points")


def build_shader_action_maps(shader_actions: List[ExportActionData]) -> Dict[int, ExportActionData]:
    """Build lookup map for shader node actions indexed by extracted GANI index."""
    return build_action_maps_by_tag(shader_actions, expected_type_tag='shadernodes')


def find_shader_action_for_gani(gani_name: str, by_gani_index: Dict[int, ExportActionData]) -> Optional[ExportActionData]:
    """Find the shader nodes action matching a main GANI action name."""
    return find_action_for_gani(gani_name, by_gani_index, track_label="shader nodes")


def group_bones_by_segment(bone_names: List[str]) -> List[Tuple[str, List[Tuple[int, str]]]]:
    """Group bone names by their base track, detecting Option D segment convention.

    Segment convention (Option D):
    - Segment 0 = base bone name, no suffix  (e.g. "bone_XYZ")
    - Segment N = base bone + "_N" for N >= 1 (e.g. "bone_XYZ_1", "bone_XYZ_2")
    A suffixed bone is only treated as a segment of its base when the unsuffixed
    base name ALSO exists in the bone list — this prevents false-grouping of bones
    whose names happen to end in a digit.

    Args:
        bone_names: Ordered list of bone names from the armature.

    Returns:
        List of (base_name, [(segment_idx, bone_name), ...]) in stable input order.
        Each tuple's segment list always starts with (0, base_name) and is followed
        by consecutively numbered siblings found in bone_names.
    """
    name_set = set(bone_names)
    processed: set = set()
    groups: List[Tuple[str, List[Tuple[int, str]]]] = []

    for bone_name in bone_names:
        if bone_name in processed:
            continue

        # If this bone looks like a segment N (N>=1) of an existing base, skip it here;
        # it will be picked up when the base bone is processed.
        base, idx = util_parsing.parse_segment_suffix(bone_name)
        if idx >= 1 and base in name_set:
            continue

        # This is a base bone — collect all _N siblings (N=1, 2, …) present in the armature.
        processed.add(bone_name)
        segments: List[Tuple[int, str]] = [(0, bone_name)]
        seg_idx = 1
        while True:
            sibling = f"{bone_name}_{seg_idx}"
            if sibling in name_set and sibling not in processed:
                processed.add(sibling)
                segments.append((seg_idx, sibling))
                seg_idx += 1
            else:
                break

        groups.append((bone_name, segments))

    return groups


def create_synthetic_mapping(armature: bpy.types.Object, 
                            action: bpy.types.Action,
                            layout_metadata_dict: Optional[Dict[str, fwrap_metadata.TrackMetaData]]) -> Tuple[TrackSegmentBoneMapping, Dict[str, fwrap_metadata.TrackMetaData]]:
    """Create synthetic track mapping from armature bones when no mapping is provided.
    
    This is used for motion points export or when exporting without a mapping file.
    Builds a TrackSegmentBoneMapping with one track per bone and derives metadata
    from either the provided layout_metadata_dict or by analyzing fcurves.
    
    Args:
        armature: Armature object (bpy.types.Object)
        action: Action to analyze for fcurves and metadata (bpy.types.Action)
        layout_metadata_dict: Optional metadata dict (for motion points)
        
    Returns:
        Tuple of (mapping, metadata_dict):
        - mapping: TrackSegmentBoneMapping with one track per bone (segment 0)
        - metadata_dict: Dictionary of bone_name -> fwrap_metadata.TrackMetaData
    """

    Debug.log("    Building synthetic mapping from armature bones...")
    
    bones_iterable = armature.pose.bones if armature.pose else armature.data.bones
    bone_names = [bone.name for bone in bones_iterable]
    
    temp_mapping = TrackSegmentBoneMapping()
    metadata_dict = {}
    
    track_idx = 0
    for base_name, segments in group_bones_by_segment(bone_names):
        # Collect metadata from the base bone; create default if none found.
        bone_metadata = None
        if layout_metadata_dict and base_name in layout_metadata_dict:
            bone_metadata = layout_metadata_dict[base_name]
        else:
            bone_metadata = fwrap_metadata.build_track_metadata_from_fcurves(bone_name=base_name, action=action)

        # Merge per-action overrides if available
        if action and bone_metadata:
            action_meta_bone = fwrap_metadata.build_track_metadata_from_action(action, base_name)
            if action_meta_bone:
                bone_metadata = fwrap_metadata.merge_track_metadata(bone_metadata, action_meta_bone)

        # Register every segment detected by group_bones_by_segment.
        for seg_idx, seg_bone_name in segments:
            temp_mapping.set_segment_mapping(
                track_idx, seg_idx, seg_bone_name,
                BoneParameters(fox_name=seg_bone_name)
            )

        if bone_metadata:
            metadata_dict[base_name] = bone_metadata
        track_idx += 1
    
    # Finalize temp_mapping to populate missing segments (e.g., if a bone has both rotation and location)
    # This prevents "Missing mapping" warnings for segment 1, 2, etc.
    if layout_metadata_dict:
        temp_mapping.finalize_with_layout_metadata(layout_metadata_dict)
    
    Debug.log(f"    Built synthetic mapping: {track_idx} track(s)")
    
    return temp_mapping, metadata_dict
