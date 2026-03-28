"""Motion-point-specific Blender import utilities.

This module contains the functions that build Blender animation actions and
armatures from motion point (MTP) data read by the MTAR reader.  It also
houses the shared NLA helper utilities (``get_action_length``,
``create_nla_strips_for_actions``) so that both this module and
``tools_mtar_importer`` can use them without creating a circular dependency.

Dependency chain (no circularity):
    tools_mtar_importer
        → tools_motion_points_importer   (this module)
            → tools_gani_track_importer
            → py_foxwrap / py_fox / py_utilities
"""

from typing import Optional, List, Dict, Tuple

import bpy
from ..py_foxwrap_utilities import futil_naming

from ..py_core.core_logging import Debug

from ..py_utilities.util_blender_armature_types import BoneSpec
from ..py_utilities import util_hashing, util_blender_animation, util_blender_armature

from ..py_fox import fox_mtar_constants as mtar_const
from ..py_fox.fox_gani_types import TrackHeader
from ..py_fox.fox_mtar_types import MtarTableList2

from .fwrap_gani_track_types import TrackUnitWrapper, Tracks
from .fwrap_gani_motionpoint_types import MotionPointWrapper
from . import fwrap_metadata

# TODO: don't import tools into other tools
from .fwrap_gani_track_import import import_gani_track



# ---------------------------------------------------------------------------
# Shared NLA helpers  (used by both this module and tools_mtar_importer)
# ---------------------------------------------------------------------------

def get_action_length(action: bpy.types.Action) -> int:
    """Return the frame-end value for *action*.

    Tries ``action.use_frame_range`` / ``action.frame_end`` first; falls back
    to scanning keyframe co-ordinates.

    Args:
        action: Blender action to measure.

    Returns:
        Frame-end integer, or ``0`` if the action has no keyframes.
    """
    if action.use_frame_range:
        return int(action.frame_end)

    action_frame_end: int = 0
    for fcurve in util_blender_animation.iter_action_fcurves(action):
        for keyframe in fcurve.keyframe_points:
            action_frame_end = max(action_frame_end, int(keyframe.co.x))
    return action_frame_end


def create_nla_strips_for_actions(
    nla_track: bpy.types.NlaTrack,
    actions: List[Optional[bpy.types.Action]],
    mtar_file_name: str,
    all_file_headers: List[MtarTableList2],
    path_to_indices: Dict[int, Tuple[int, int]],
    use_verbose_naming: bool,
    is_motion_points: bool = False,
    is_shader_nodes: bool = False,
    strip_padding: int = 10,
    reference_actions: Optional[List[bpy.types.Action]] = None,
) -> int:
    """Create NLA strips for *actions* on *nla_track*.

    This utility reduces duplication by handling the common pattern of placing
    actions as NLA strips with consistent naming and padding.

    Args:
        nla_track:          NLA track to add strips to.
        actions:            Ordered list of actions (``None`` entries are skipped
                            but still advance the frame offset).
        mtar_file_name:     Base name used when formatting strip names.
        all_file_headers:   File headers indexed in the same order as *actions*.
        path_to_indices:    Maps path hash → ``(h_index, d_index)`` tuple.
        use_verbose_naming: Include h/d indices in strip names when ``True``.
        is_motion_points:   Flag the strips as motion-point strips for naming.
        is_shader_nodes:    Flag the strips as shader-node strips for naming.
        strip_padding:      Gap (frames) inserted between consecutive strips.
        reference_actions:  Optional parallel list of reference actions used to
                            derive frame offsets, keeping motion-point strips
                            synchronised with the main animation strips even
                            when a motion-point GANI is absent.

    Returns:
        Total frame offset reached after all strips (useful for chaining).
    """
    current_frame_offset: int = 0

    for index, action in enumerate(actions):
        if action is None:
            Debug.log(f"  Skipped GANI {index} (no action data)")
            if reference_actions and index < len(reference_actions) and reference_actions[index]:
                ref_len = get_action_length(reference_actions[index])
                if ref_len > 0:
                    current_frame_offset += ref_len + strip_padding
            continue

        action_length = get_action_length(action)

        if reference_actions and index < len(reference_actions) and reference_actions[index]:
            reference_action_length = get_action_length(reference_actions[index])
        else:
            reference_action_length = action_length

        if action_length > 0:
            strip: bpy.types.NlaStrip = nla_track.strips.new(
                name="tmp",
                start=int(current_frame_offset),
                action=action,
            )
            file_header = all_file_headers[index]
            h_idx, d_idx = path_to_indices.get(file_header.path, (0, 0))

            gani_name_segment: Optional[str] = None
            if mtar_const.TABL_PATH in action.keys():
                gani_path_val = str(action[mtar_const.TABL_PATH])
                if not util_hashing.is_gani_path_a_hash(gani_path_val):
                    gani_name_segment = futil_naming.extract_gani_name_from_path(gani_path_val)

            strip.name = futil_naming.format_strip_name(
                mtar_file_name, index, h_idx, d_idx,
                use_verbose_naming,
                is_motion_points=is_motion_points,
                is_shader_nodes=is_shader_nodes,
                gani_name=gani_name_segment,
            )
            strip.frame_end = strip.frame_start + action_length
            strip.action_frame_start = 0
            strip.action_frame_end = action_length

            Debug.log(
                f"  Created NLA strip '{strip.name}' at frame {current_frame_offset} "
                f"(length: {action_length})"
            )
        else:
            Debug.log(f"  Skipped GANI {index} (no animation data)")

        if reference_action_length > 0:
            current_frame_offset += reference_action_length + strip_padding

    return current_frame_offset


# ---------------------------------------------------------------------------
# Motion-point animation actions
# ---------------------------------------------------------------------------

def create_motion_points_animation_actions(
    context: bpy.types.Context,
    mtar_file_name: str,
    all_motion_point_gani_tracks: List[List[TrackUnitWrapper]],
    all_motion_point_layouts: List[Optional[Tracks]],
    all_motion_point_track_headers: List[Optional[TrackHeader]],
    all_file_headers: List[MtarTableList2],
    path_to_indices: Dict[int, Tuple[int, int]],
    use_verbose_naming: bool,
    gani_hash_dict: Optional[Dict[int, str]] = None,
    motion_points: Optional['MotionPointWrapper'] = None,
) -> List[Optional[bpy.types.Action]]:
    """Create Blender animation actions for motion points from MTAR data.

    Creates one action per GANI that contains MTP tracks.  Actions can later
    be linked to a motion-points armature via NLA tracks.

    Args:
        context:                        Blender context.
        mtar_file_name:                 Base name for generated action names.
        all_motion_point_gani_tracks:   Per-GANI MTP track lists.
        all_motion_point_layouts:       Per-GANI MTP ``Tracks`` layout objects.
        all_motion_point_track_headers: Per-GANI MTP ``TrackHeader`` objects.
        all_file_headers:               Per-GANI file headers (path hashes).
        path_to_indices:                Maps path hash → ``(h_index, d_index)``.
        use_verbose_naming:             Include h/d indices in action names.
        gani_hash_dict:                 Optional GANI-path dictionary for name
                                        resolution.
        motion_points:                  Optional MotionPointWrapper for track name
                                        remapping to decimal hash format.

    Returns:
        List of actions (one per GANI); ``None`` entries for GANIs without MTP
        data (preserves index alignment with the main action list).
    """
    motion_point_actions: List[Optional[bpy.types.Action]] = []

    Debug.log(
        f"\nProcessing {len(all_motion_point_gani_tracks)} GANI file(s) "
        f"for motion points..."
    )
    for gani_index, motion_point_tracks in enumerate(all_motion_point_gani_tracks):
        if not motion_point_tracks:
            Debug.log(f"  GANI {gani_index + 1}: No motion point tracks")
            motion_point_actions.append(None)
            continue

        Debug.log(
            f"\n  --- Motion Points GANI {gani_index + 1}/"
            f"{len(all_motion_point_gani_tracks)} ---"
        )
        try:
            total_mp = len(all_motion_point_gani_tracks) or 1
            progress = 60 + min(4, int(((gani_index + 1) / total_mp) * 5))
            Debug.update_progress(
                progress,
                f"MotionPoints GANI {gani_index + 1}/{total_mp}: "
                f"MotionPoints_Gani_{gani_index + 1:03d}",
            )
        except Exception:
            pass

        file_header = all_file_headers[gani_index]
        h_idx, d_idx = path_to_indices.get(file_header.path, (0, 0))
        if file_header.path not in path_to_indices:
            Debug.log_warning(
                f"Missing path hash mapping for motion points GANI: "
                f"0x{file_header.path:016X}, using h0_d0"
            )

        gani_full_path, gani_name_segment = futil_naming.resolve_gani_name_segment(
            file_header, gani_hash_dict
        )

        action_name: str = futil_naming.format_action_name(
            mtar_file_name, gani_index, h_idx, d_idx,
            use_verbose_naming,
            is_motion_points=True,
            gani_name=gani_name_segment,
        )
        action: bpy.types.Action = bpy.data.actions.new(name=action_name)
        motion_point_actions.append(action)
        Debug.log(f"  Created action: {action_name}")

        # Store track metadata
        motion_point_layout = all_motion_point_layouts[gani_index]
        if motion_point_layout is not None:
            track_metadata_list = fwrap_metadata.build_track_metadata_from_layout_track_units(
                motion_point_layout.track_units,
                track_name_prefix="MotionPoint",
            )
            fwrap_metadata.store_track_metadata_on_action(
                action, track_metadata_list,
                include_segments=False,
            )

        motion_point_track_header: TrackHeader = all_motion_point_track_headers[gani_index]
        if motion_point_track_header is not None:
            fwrap_metadata.store_track_header_properties_on_action(action, motion_point_track_header)

        if hasattr(file_header, 'path'):
            if gani_full_path is not None:
                action[mtar_const.TABL_PATH] = gani_full_path
            else:
                action[mtar_const.TABL_PATH] = str(file_header.path)

        gani_frame_count: int = (
            motion_point_track_header.frame_count
            if motion_point_track_header is not None
            else 0
        )

        Debug.log(f"  Processing {len(motion_point_tracks)} motion point track(s)...")
        
        # Build track name to hash mapping from motion_points wrapper to ensure
        # FCurves are created with decimal hash bone names that match the armature
        name_to_hash: Dict[str, str] = {}
        if motion_points is not None:
            for entry in motion_points.entries:
                # Map both the entry name and its decimal hash representation
                name_to_hash[entry.name] = str(entry.hash_value)
                Debug.log(f"    Track name mapping: '{entry.name}' -> '{entry.hash_value}'")
        
        for gani_track in motion_point_tracks:
            # Remap track name to decimal hash format before importing.
            # This ensures FCurves are created with bone names that match the
            # motion points armature (which uses decimal hash format).
            # Both gani_track.name AND each segment's TrackDataBlobWrapper.name
            # must be updated since import_keyframes_track uses the segment name
            # to build the FCurve data path.
            original_name = gani_track.name
            if gani_track.name in name_to_hash:
                remapped_name = name_to_hash[gani_track.name]
                gani_track.name = remapped_name
                for segment in gani_track.segments_track_data:
                    segment.name = remapped_name
                Debug.log(f"    Remapped track name: '{original_name}' -> '{remapped_name}'")
            
            track_max_frame: int = import_gani_track(context, action, gani_track)
            if motion_point_track_header is None:
                gani_frame_count = max(gani_frame_count, track_max_frame)

        Debug.log(f"  Motion point frame range: 0 - {gani_frame_count}")
        util_blender_animation.configure_action(action, frame_start=0, frame_end=gani_frame_count)
        Debug.log(
            f"  Configured motion point action frame range: 0 - {gani_frame_count}"
        )

    return motion_point_actions


# ---------------------------------------------------------------------------
# Motion-point armature
# ---------------------------------------------------------------------------

def create_and_setup_motion_points_armature(
    context: bpy.types.Context,
    mtar_file_name: str,
    motion_points: Optional[MotionPointWrapper],
    motion_point_actions: List[Optional[bpy.types.Action]],
    all_file_headers: List[MtarTableList2],
    path_to_indices: Dict[int, Tuple[int, int]],
    use_verbose_naming: bool,
    strip_padding: int = 10,
    reference_actions: Optional[List[bpy.types.Action]] = None,
) -> Optional[bpy.types.Object]:
    """Create and set up the motion-points armature.

    Builds the armature skeleton from a :class:`MotionPointWrapper` and links
    pre-created animation actions to it via NLA tracks.

    Args:
        context:               Blender context.
        mtar_file_name:        Base name for the armature and NLA track.
        motion_points:         :class:`MotionPointWrapper` providing bone
                               definitions; if ``None`` or empty the function
                               returns ``None`` immediately.
        motion_point_actions:  Pre-created motion-point animation actions (from
                               :func:`create_motion_points_animation_actions`).
        all_file_headers:      Per-GANI file headers for NLA strip naming.
        path_to_indices:       Maps path hash → ``(h_index, d_index)``.
        use_verbose_naming:    Include h/d indices in NLA strip names.
        strip_padding:         Gap (frames) between NLA strips.
        reference_actions:     Optional list of main-armature actions used to
                               synchronise frame offsets when a motion-point
                               GANI has no data.

    Returns:
        The created armature :class:`bpy.types.Object`, or ``None``.
    """
    if not motion_points or motion_points.count == 0:
        return None

    Debug.log("\nCreating motion points armature...")
    armature_name = f"{mtar_file_name}_MotionPoints"
    Debug.log(f"  Creating motion points armature: {armature_name}")

    # Build BoneSpec list from motion point entries.
    # All bone names are decimal hash strings. We create stub bones for parents
    # that don't appear as their own entry (non-MTP skeleton parents).
    motion_point_bones: Dict[int, Tuple[int, Optional[str]]] = {}
    for entry in motion_points.entries:
        motion_point_bones[entry.hash_value] = (
            entry.parent_hash, entry.parent_name
        )

    # Collect all hashes that need to exist as bones (entries + missing parents)
    all_hashes: Dict[int, Optional[int]] = {}  # hash -> parent_hash or None
    for hash_value, (parent_hash, _) in motion_point_bones.items():
        all_hashes[hash_value] = parent_hash if parent_hash != 0 else None
        if parent_hash != 0 and parent_hash not in motion_point_bones:
            # Stub parent bone
            all_hashes.setdefault(parent_hash, None)

    bone_specs = [
        BoneSpec(
            name=str(h),
            parent_name=str(p) if p is not None else None,
        )
        for h, p in all_hashes.items()
    ]
    armature: bpy.types.Object = util_blender_armature.create_track_armature(context, armature_name, bone_specs)
    Debug.log(
        f"Motion points armature created with {motion_points.count} point(s)"
    )

    if motion_point_actions:
        Debug.log("\n=== Processing Motion Point Animations ===")
        Debug.log(
            f"Importing animations to motion points armature: {armature.name}"
        )

        if not armature.animation_data:
            armature.animation_data_create()

        nla_track: bpy.types.NlaTrack = armature.animation_data.nla_tracks.new()
        nla_track.name = f"{mtar_file_name}_MotionPoints_Animations"
        Debug.log(f"Created NLA track: {nla_track.name}")

        motion_point_final_offset = create_nla_strips_for_actions(
            nla_track,
            motion_point_actions,
            mtar_file_name,
            all_file_headers,
            path_to_indices,
            use_verbose_naming,
            is_motion_points=True,
            strip_padding=strip_padding,
            reference_actions=reference_actions,
        )

        if motion_point_final_offset > context.scene.frame_end:
            context.scene.frame_end = int(motion_point_final_offset)
            Debug.log(
                f"Extended scene frame range to {motion_point_final_offset} "
                f"for motion points"
            )

        Debug.log("Motion point animations import complete")

    return armature





def store_motion_point_stringlists_on_action(
    action: bpy.types.Action,
    mtp_list: Optional[List[str]],
    mtp_parent_list: Optional[List[str]],
) -> None:
    """Store MTP_LIST and MTP_PARENT_LIST string lists on a Blender action.

    These lists are only present in old-format (FoxData) GANI files and are
    stored on the **main animation action** (not the motion-points action) so
    that the old-format writer can reconstruct the FoxData string-list nodes
    on re-export.

    Args:
        action:          Main animation Blender action to attach properties to.
        mtp_list:        Motion point name strings from ``MTP_LIST`` node, or
                         ``None`` if the node was absent.
        mtp_parent_list: Motion point parent name strings from
                         ``MTP_PARENT_LIST`` node, or ``None`` if absent.
    """
    if mtp_list is not None:
        fwrap_metadata.store_foxdata_stringlist_on_action(action, fwrap_metadata.PROP_MTP_LIST, mtp_list)
        Debug.log(f"  Stored {fwrap_metadata.PROP_MTP_LIST}: {len(mtp_list)} entries")
    if mtp_parent_list is not None:
        fwrap_metadata.store_foxdata_stringlist_on_action(action, fwrap_metadata.PROP_MTP_PARENT_LIST, mtp_parent_list)
        Debug.log(f"  Stored {fwrap_metadata.PROP_MTP_PARENT_LIST}: {len(mtp_parent_list)} entries")

