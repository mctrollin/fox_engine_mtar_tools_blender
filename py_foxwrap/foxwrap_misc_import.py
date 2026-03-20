"""
Import-only fake types for MTAR importer.
"""
from typing import List

from .foxwrap_misc_import_types import ShaderTrackWrapper, GaniImportData, CommonInfo

__all__ = [
    "ShaderTrackWrapper",
    "GaniImportData",
    "CommonInfo",
    "iter_gani_tracks",
    "iter_gani_bone_tracks",
]


def iter_gani_tracks(all_gani_data: List[GaniImportData], include_mtp: bool = True):
    """Yield bone + optional motion point tracks from GaniImportData list."""
    for data in all_gani_data:
        for gani_track in data.gani_bone_tracks:
            yield gani_track
        if include_mtp:
            for mtp_track in data.gani_mtp_tracks:
                yield mtp_track


def iter_gani_bone_tracks(all_gani_data: List[GaniImportData]):
    """Yield only bone tracks from a list of GaniImportData objects."""
    return iter_gani_tracks(all_gani_data, include_mtp=False)



