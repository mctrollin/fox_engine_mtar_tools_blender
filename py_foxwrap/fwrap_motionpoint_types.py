"""Motion point wrapper types isolated for import/export module separation."""
from dataclasses import dataclass, field
from typing import List, Optional

from ..py_core.core_logging import Debug

from ..py_utilities import util_hashing_cityhash

from ..py_fox.fox_mtar_types import MotionPointList2, MotionPointEntry
from ..py_fox.fox_misc_types import StrCode32


@dataclass
class MotionPointEntryWrapper:
    hash_value: int
    name: str
    parent_hash: int
    parent_name: Optional[str] = None


@dataclass
class MotionPointWrapper:
    entries: List[MotionPointEntryWrapper] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.entries)

    @classmethod
    def from_new_format(
        cls,
        mpl: 'MotionPointList2',
        unhash_fn,
    ) -> 'MotionPointWrapper':
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
                    continue

                parent_name: Optional[str] = (
                    parent_list[track_idx]
                    if parent_list and track_idx < len(parent_list)
                    else None
                )
                hash_val = int(name_str) if name_str.isdigit() else util_hashing_cityhash.strcode32(name_str)
                seen[name_str] = (hash_val, parent_name)

        if not seen:
            return None

        entries = []
        for name_str, (hash_val, parent_name) in seen.items():
            parent_hash = (
                (int(parent_name) if (parent_name and parent_name.isdigit()) else util_hashing_cityhash.strcode32(parent_name))
                if parent_name
                else 0
            )
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

    def to_motion_point_list2(self) -> 'MotionPointList2':
        fox_entries = [
            MotionPointEntry(
                name=StrCode32(e.hash_value),
                parent_name=StrCode32(e.parent_hash),
            )
            for e in self.entries
        ]
        return MotionPointList2(count=len(fox_entries), entries=fox_entries)
