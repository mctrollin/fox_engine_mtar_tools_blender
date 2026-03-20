"""
Shared GANI track naming helpers used by both GANI1 and GANI2 readers.
"""
from typing import List, Optional

from ..py_utilities.utilities_hashing import hash_or_parse_name, is_hash_string, unhash_rig_type
from ..py_utilities.utilities_hashing_cityhash import strcode32
from ..py_utilities.utilities_naming import apply_segment_suffixes
from ..py_utilities.utilities_logging import Debug

from .foxwrap_misc import TrackUnitWrapper, TrackDataBlobWrapper
from ..py_fox.fox_misc_types import StrCode32


def resolve_track_name(rig_hash: StrCode32, prefix: Optional[str] = None) -> str:
    """Resolve a StrCode32 hash to a readable name."""
    bone_name = unhash_rig_type(rig_hash.to_int())
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
        if not is_hash_string(entry):
            h = strcode32(entry)
            skl_lookup[h] = entry

    for track in tracks:
        name = track.name
        is_hash_fallback = is_hash_string(name)
        track_hash = hash_or_parse_name(name)

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


def finalize_bone_tracks(
    tracks: List[TrackUnitWrapper],
    skeleton_list: Optional[List[str]] = None,
    label: str = "GANI",
) -> List[TrackUnitWrapper]:
    """Apply track naming (unhashing) and segment suffixes to bone tracks."""
    named = apply_track_naming(tracks, prefix=None)
    if skeleton_list is not None:
        _apply_stringlist_names(named, skeleton_list, label=f"Read {label} SKL_LIST")
    apply_segment_suffixes(named)
    return named


def finalize_motion_point_tracks(tracks: List[TrackUnitWrapper]) -> List[TrackUnitWrapper]:
    """Apply track naming and segment suffixes to motion point tracks."""
    named = apply_track_naming(tracks, use_decimal_only=True)
    apply_segment_suffixes(named)
    return named
