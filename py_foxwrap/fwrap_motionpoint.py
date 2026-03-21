"""Format-agnostic wrapper for motion point definitions.

Provides :class:`MotionPointWrapper` and :class:`MotionPointEntryWrapper` as the
common representation of motion point data (bone names + parent hierarchy) used
throughout the import/export pipeline, replacing direct use of the binary
``MotionPointList2`` / ``MotionPointEntry`` types from ``py_fox``.

Conversion paths
----------------
* New-format (GANI2 / CommonInfo):  ``MotionPointWrapper.from_new_format()``
* Old-format (FoxData) synthesis:   ``MotionPointWrapper.from_old_format()``
* Export serialisation:             ``MotionPointWrapper.to_motion_point_list2()``

Round-trip helpers
------------------
* ``store_motion_point_stringlists_on_action()``  — import side (old-format only)
"""

from typing import List, Optional

import bpy

from ..py_core.core_logging import Debug

from . import fwrap_metadata


# ---------------------------------------------------------------------------
# Round-trip helpers (old-format, stored on the main animation action)
# ---------------------------------------------------------------------------

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
