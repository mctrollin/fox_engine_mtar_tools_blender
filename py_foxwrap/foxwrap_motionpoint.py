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

from dataclasses import dataclass, field
from typing import List, Optional, TYPE_CHECKING

import bpy

from ..py_utilities.utilities_logging import Debug
from ..py_utilities.utilities_hashing_cityhash import strcode32

from .foxwrap_metadata import (
    PROP_MTP_LIST,
    PROP_MTP_PARENT_LIST,
    store_foxdata_stringlist_on_action,
)

if TYPE_CHECKING:
    from ..py_fox.fox_mtar_types import MotionPointList2


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MotionPointEntryWrapper:
    """Format-agnostic representation of a single motion point definition.

    Attributes:
        hash_value:   StrCode32 integer hash of the bone name.
        name:         Resolved human-readable bone name, or decimal-hash string
                      as fallback when unhashing fails.
        parent_hash:  StrCode32 integer hash of the parent bone (0 = no parent).
        parent_name:  Resolved parent name string if available, else ``None``.
    """
    hash_value: int
    name: str
    parent_hash: int
    parent_name: Optional[str] = None


@dataclass
class MotionPointWrapper:
    """Format-agnostic wrapper for the complete motion point definition list.

    This type replaces direct usage of the binary ``MotionPointList2`` type in
    the importer/exporter, keeping fox-layer binary structs out of the
    Blender-facing pipeline code.
    """
    entries: List[MotionPointEntryWrapper] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Number of motion point entries."""
        return len(self.entries)

    # ------------------------------------------------------------------
    # Factory constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_new_format(
        cls,
        mpl: 'MotionPointList2',
        unhash_fn,
    ) -> 'MotionPointWrapper':
        """Create from a new-format ``MotionPointList2`` (GANI2 CommonInfo node).

        Args:
            mpl:        ``MotionPointList2`` read from the MTAR CommonInfo section.
            unhash_fn:  Callable ``(hash_int: int) -> Optional[str]`` used to
                        resolve hashes to readable names.
        """
        entries = []
        for e in mpl.entries:
            hash_val = e.name.to_int()
            name = unhash_fn(hash_val) or str(e.name)
            parent_hash = e.parent_name.to_int()
            parent_name = unhash_fn(parent_hash) if parent_hash != 0 else None
            entries.append(MotionPointEntryWrapper(
                hash_value=hash_val,
                name=name,
                parent_hash=parent_hash,
                parent_name=parent_name,
            ))
        Debug.log(f"MotionPointWrapper.from_new_format: {len(entries)} entry/entries")
        return cls(entries=entries)

    @classmethod
    def from_old_format(
        cls,
        all_motion_point_gani_tracks: list,
        all_mtp_parent_lists: Optional[List[Optional[List[str]]]],
    ) -> 'Optional[MotionPointWrapper]':
        """Synthesise a ``MotionPointWrapper`` from old-format (FoxData) data.

        Old-format MTAR files have no CommonInfo section.  Motion point
        definitions are recovered by:

        1. Collecting unique track names from all GANI MTP track lists
           (``all_motion_point_gani_tracks``).  Names are already resolved
           strings after ``apply_track_naming`` + ``_apply_stringlist_names``
           in the reader.
        2. Using ``MTP_PARENT_LIST`` strings (``all_mtp_parent_lists``), which
           are *positionally aligned* to the MTP tracks within each GANI, to
           reconstruct the parent hierarchy.

        Returns:
            A new ``MotionPointWrapper``, or ``None`` if no MTP tracks were
            found across all GANIs.
        """
        # name_str -> (hash_int, parent_name_str | None)
        seen: dict = {}

        for gani_idx, mtp_tracks in enumerate(all_motion_point_gani_tracks):
            if not mtp_tracks:
                continue

            parent_list: Optional[List[str]] = (
                all_mtp_parent_lists[gani_idx]
                if all_mtp_parent_lists and gani_idx < len(all_mtp_parent_lists)
                else None
            )

            for track_idx, track in enumerate(mtp_tracks):
                name_str: str = track.name
                if name_str in seen:
                    continue  # Already registered from an earlier GANI

                parent_name: Optional[str] = (
                    parent_list[track_idx]
                    if parent_list and track_idx < len(parent_list)
                    else None
                )
                # If name_str is a pure decimal number it IS the original StrCode32 integer
                # (decimal representation of the hash). Parse it directly to avoid
                # strcode32("2427674602") != int("2427674602") discrepancy.
                hash_val = int(name_str) if name_str.isdigit() else strcode32(name_str)
                seen[name_str] = (hash_val, parent_name)

        if not seen:
            return None

        entries = []
        for name_str, (hash_val, parent_name) in seen.items():
            # Same decimal-aware parsing for parent names
            parent_hash = (int(parent_name) if (parent_name and parent_name.isdigit()) else strcode32(parent_name)) if parent_name else 0
            entries.append(MotionPointEntryWrapper(
                hash_value=hash_val,
                name=name_str,
                parent_hash=parent_hash,
                parent_name=parent_name,
            ))

        Debug.log(
            f"MotionPointWrapper.from_old_format: synthesised {len(entries)} entry/entries"
        )
        return cls(entries=entries)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_motion_point_list2(self) -> 'MotionPointList2':
        """Serialise back to a ``MotionPointList2`` for writing to CommonInfo."""
        from ..py_fox.fox_mtar_types import MotionPointList2, MotionPointEntry
        from ..py_fox.fox_misc_types import StrCode32

        fox_entries = [
            MotionPointEntry(
                name=StrCode32(e.hash_value),
                parent_name=StrCode32(e.parent_hash),
            )
            for e in self.entries
        ]
        return MotionPointList2(count=len(fox_entries), entries=fox_entries)


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
        store_foxdata_stringlist_on_action(action, PROP_MTP_LIST, mtp_list)
        Debug.log(f"  Stored {PROP_MTP_LIST}: {len(mtp_list)} entries")
    if mtp_parent_list is not None:
        store_foxdata_stringlist_on_action(action, PROP_MTP_PARENT_LIST, mtp_parent_list)
        Debug.log(f"  Stored {PROP_MTP_PARENT_LIST}: {len(mtp_parent_list)} entries")
