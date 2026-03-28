"""
Utilities for locating Blender actions in MTAR exporter workflows.

This module provides helper routines that build lookup maps from action
names that include GANI index metadata, and resolve associated motion-point
or shader-node actions for given main GANI track names.
"""
from typing import Optional, List, Dict

from ..py_core.core_logging import Debug

from . import futil_naming
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
        result = futil_naming.extract_track_infos_from_action_label(a.action.name)
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
    result = futil_naming.extract_track_infos_from_action_label(gani_name)
    if result:
        idx, type_tag = result
        if type_tag == 'track':
            return by_gani_index.get(idx)
        else:
            Debug.log_warning(
                f"Warning: GANI '{gani_name}' has type '{type_tag}', expected 'track' - "
                f"{track_label} will be skipped for this GANI"
            )
    else:
        Debug.log_warning(
            f"Warning: No GANI index could be extracted from GANI name '{gani_name}' - "
            f"{track_label} will be skipped for this GANI"
        )
    return None

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
