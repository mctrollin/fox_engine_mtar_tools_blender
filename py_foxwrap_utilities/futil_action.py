"""
Utilities for locating Blender actions in MTAR exporter workflows.

This module provides helper routines that build lookup maps from action
names that include GANI index metadata, and resolve associated motion-point
or shader-node actions for given main GANI track names.
"""
from typing import Optional, List, Dict
import re

import bpy

from ..py_core.core_logging import Debug

from ..py_utilities import util_blender_animation, util_naming

from .futil_action_types import ExportActionData



def _build_action_maps_by_tag(
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
        result = util_naming.extract_track_infos_from_action_label(a.action.name)
        if result:
            idx, type_tag = result
            if type_tag == expected_type_tag:
                if idx not in by_gani_index:
                    by_gani_index[idx] = a
            else:
                Debug.log_warning(
                    f"Warning: Action '{a.action.name}' has type '{type_tag}', "
                    f"expected '{expected_type_tag}' - this action will be skipped"
                )
        else:
            Debug.log_warning(
                f"Warning: No GANI index found in action name '{a.action.name}' - "
                f"this action will be skipped"
            )

    return by_gani_index

def build_motion_point_action_maps(motion_point_actions: List[ExportActionData]) -> Dict[int, ExportActionData]:
    """Build a map of motion point actions keyed by GANI running index.

    The action label is parsed via :func:`futil_naming.extract_track_infos_from_action_label`.
    Only actions with type tag ``'motionpoints'`` are included; unsupported labels
    are logged and ignored.

    Args:
        motion_point_actions: List of candidate motion-point actions.

    Returns:
        Mapping from GANI running index to :class:`ExportActionData`.
    """
    return _build_action_maps_by_tag(motion_point_actions, expected_type_tag='motionpoints')

def build_shader_action_maps(shader_actions: List[ExportActionData]) -> Dict[int, ExportActionData]:
    """Build a map of shader-node actions keyed by GANI running index.

    The action label is parsed via :func:`futil_naming.extract_track_infos_from_action_label`.
    Only actions with type tag ``'shadernodes'`` are included; unsupported labels
    are logged and ignored.

    Args:
        shader_actions: List of candidate shader-node actions.

    Returns:
        Mapping from GANI running index to :class:`ExportActionData`.
    """
    return _build_action_maps_by_tag(shader_actions, expected_type_tag='shadernodes')


def _find_action_for_gani(
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
    result = util_naming.extract_track_infos_from_action_label(gani_name)
    if result:
        idx, type_tag = result
        if type_tag == 'track':
            return by_gani_index.get(idx)
        else:
            Debug.log_warning(
                f"Warning: GANI '{gani_name}' has type '{type_tag}', expected 'track' - "
                f"{track_label} will be skipped for this GANI"
            )
            return None

    if re.search(r"\.\d+$", gani_name):
        raise ValueError(
            f"Invalid GANI name '{gani_name}': trailing numeric suffix detected (e.g. '.001'). "
            "Rename the Blender action/strip to remove Blender auto-suffix, "
            "so the name matches '<mtar-name>.<animation-parts>.<index>.<type>.(gani|strip)'."
        )

    raise ValueError(
        f"Invalid GANI name '{gani_name}': expected format '<mtar-name>.<animation-parts>.<index>.<type>.(gani|strip)'. "
        "Got unrecognized name; ensure track names follow the exporter naming schema."
    )

def find_motion_point_action_for_gani(gani_name: str, by_gani_index: Dict[int, ExportActionData]) -> Optional[ExportActionData]:
    """Resolve motion-point action for a GANI track.

    This helper wraps :func:`_find_action_for_gani` using the motion point
    track label for warnings.

    Args:
        gani_name: Name of the GANI track action to match index for.
        by_gani_index: Action map from :func:`build_motion_point_action_maps`.

    Returns:
        Corresponding :class:`ExportActionData` or ``None``.
    """
    return _find_action_for_gani(gani_name, by_gani_index, track_label="motion points")

def find_shader_action_for_gani(gani_name: str, by_gani_index: Dict[int, ExportActionData]) -> Optional[ExportActionData]:
    """Resolve shader-node action for a GANI track.

    This helper wraps :func:`_find_action_for_gani` using the shader node
    track label for warnings.

    Args:
        gani_name: Name of the GANI track action to match index for.
        by_gani_index: Action map from :func:`build_shader_action_maps`.

    Returns:
        Corresponding :class:`ExportActionData` or ``None``.
    """
    return _find_action_for_gani(gani_name, by_gani_index, track_label="shader nodes")


def collect_actions_for_export_from_armature(
        armature: bpy.types.Object,
        use_nla: bool = True,
        export_clean_threshold: float = 0.0
    ) -> List[ExportActionData]:
    """Collect actions to export based on NLA tracks or active action.
    
    Args:
        armature: Armature object
        use_nla: If True, check NLA tracks first; if False, use only active action
        export_clean_threshold: Threshold for FCurve cleaning (0 = disabled)
        
    Returns:
        List of ExportActionData objects containing action export information
    """
    actions_to_export = []
    
    if not armature.animation_data:
        Debug.log_warning("  Warning: No animation data on armature")
        return actions_to_export
    
    # Try to get actions from NLA tracks
    if use_nla and armature.animation_data.nla_tracks:
        Debug.log("\nCollecting actions from NLA tracks:")
        
        for track_idx, track in enumerate(armature.animation_data.nla_tracks):
            if track.mute:
                Debug.log(f"  Track {track_idx} '{track.name}': Muted (skipping)")
                continue
            
            Debug.log(f"  Track {track_idx} '{track.name}':")
            
            for strip_idx, strip in enumerate(track.strips):
                # Skip non-GANI strips (includes muted, layout, or negative-time strips)
                if not util_blender_animation.is_relevant_strip(strip):
                    Debug.log(f"    Strip {strip_idx} '{getattr(strip, 'name', '<unknown>')}': Skipping (not a GANI strip)")
                    continue

                # Calculate frame range (use strip's frame range)
                frame_start = int(strip.frame_start)
                frame_end = int(strip.frame_end)

                
                # Use strip name if available, otherwise action name
                source = f'NLA Track "{track.name}" Strip "{strip.name}"'
                
                # Create export action data
                export_action = ExportActionData(
                    action=strip.action,
                    frame_start=frame_start,
                    frame_end=frame_end,
                    source=source,
                    export_clean_threshold=export_clean_threshold
                )
                
                actions_to_export.append(export_action)
                Debug.log(f"    Strip {strip_idx}: {export_action.to_string()}")
        
        if actions_to_export:
            Debug.log(f"\nFound {len(actions_to_export)} action(s) in NLA tracks")
            return actions_to_export
        else:
            Debug.log("\nNo unmuted NLA strips found, falling back to active action")
    
    # Fallback to active action
    if armature.animation_data.action:
        action = armature.animation_data.action
        
        # Skip layout track action (metadata only, not animation data)
        if '.layout.' in action.name.lower():
            Debug.log(f"\nActive action '{action.name}' is a layout track (skipping - metadata only)")
        else:
            frame_start = int(action.frame_range[0])
            frame_end = int(action.frame_range[1])
            
            # Skip animations in negative time range
            if frame_end <= 0:
                Debug.log(f"\nActive action '{action.name}' is in negative time range {frame_start} to {frame_end} (skipping)")
            else:
                # Create export action data
                export_action = ExportActionData(
                    action=action,
                    frame_start=frame_start,
                    frame_end=frame_end,
                    source='Active Action',
                    export_clean_threshold=export_clean_threshold
                )
                
                actions_to_export.append(export_action)
                Debug.log(f"\nUsing active action: {export_action.to_string()}")
    else:
        Debug.log_warning("\n  Warning: No active action and no NLA strips found")
    
    return actions_to_export
