"""Helper routines for track mapping files.

This module contains utilities that are useful for both the importer operators and
potential CLI tools.  Historically the mapping-template generator lived inside the
Blender operator, which made it difficult to re-use in tests or other contexts.

The public API is intentionally small: ``generate_mapping_template`` returns the
path to the file that was created, or raises a ``ValueError``/``OSError`` on
failure.

The code reuses logic originally written in ``blender_operators_import.py`` but
is now independent of Blender context or UI.
"""

from __future__ import annotations
import os
from typing import Optional, List, Any

from ..py_core.core_logging import Debug

from ..py_utilities import util_hashing

from ..py_fox.fox_frig_types import FrigFile, RigUnitDef

from ..py_foxwrap import fwrap_metadata
from ..py_foxwrap.fwrap_mtar_reader import MtarReader


def generate_mapping_template(frig_filepath: Optional[str], mtar_filepath: Optional[str]) -> str:
    """Create a track mapping template file.

    The output file will be placed next to the FRIG file if one is provided;
    otherwise it falls back to the directory of the MTAR file (or the current
    working directory when neither path is available).  The filename is formed
    by taking the base name of the input file and appending ``_track_mapping.txt``.

    Args:
        frig_filepath: Absolute path to a FRIG file, or ``None`` if not used.
        mtar_filepath: Absolute path to an MTAR file, or ``None`` if not used.

    Returns:
        Path to the generated mapping file.

    Raises:
        ValueError: If neither FRIG nor MTAR data can supply a track count.
        OSError: If the output file already exists or cannot be written.
    """

    # load FRIG data if available
    frig_data: Optional[FrigFile] = None
    if frig_filepath:
        if not os.path.exists(frig_filepath):
            Debug.log_warning(f"FRIG file not found: {frig_filepath}")
        else:
            try:
                with open(frig_filepath, 'rb') as f:
                    frig_data = FrigFile.read(f)
            except (OSError, ValueError) as e:
                # if the file exists but can't be parsed, issue a warning and continue
                Debug.log_warning(f"Could not read FRIG file: {e}")
                frig_data = None

    # load MTAR layout info if available (new or old format)
    layout_units: Optional[List[Any]] = None
    if mtar_filepath and os.path.exists(mtar_filepath):
        try:
            reader = MtarReader(mtar_filepath)
            # reading just the first GANI is enough to populate layout_track
            reader.read_selected_ganis([0])
            if reader.layout_track:
                layout_units = reader.layout_track.track_units
        except (OSError, ValueError) as e:  # noqa: E722
            Debug.log_warning(f"Could not read MTAR file: {e}")

    # determine track count and unit definitions
    if frig_data and frig_data.rig_def and frig_data.rig_def.unit_defs:
        unit_defs: List[RigUnitDef] = frig_data.rig_def.unit_defs
        track_count = len(unit_defs)
    elif layout_units:
        track_count = len(layout_units)
        unit_defs = [None] * track_count  # type: ignore[var-assign]
    else:
        raise ValueError(
            "Cannot determine track count – provide either FRIG or MTAR layout information"
        )

    # choose output path
    if frig_filepath:
        base_dir = os.path.dirname(frig_filepath)
        base_name = os.path.splitext(os.path.basename(frig_filepath))[0]
    elif mtar_filepath:
        base_dir = os.path.dirname(mtar_filepath)
        base_name = os.path.splitext(os.path.basename(mtar_filepath))[0]
    else:
        base_dir = os.getcwd()
        base_name = "mapping"

    output_path = os.path.join(base_dir, f"{base_name}_track_mapping.txt")
    if os.path.exists(output_path):
        raise OSError(f"Mapping file already exists: {output_path}")

    # header comments
    lines: List[str] = []
    lines.append("# Track Mapping File")
    if frig_filepath:
        lines.append(f"# Generated from: {os.path.basename(frig_filepath)}")
    if mtar_filepath:
        lines.append(f"# MTAR reference: {os.path.basename(mtar_filepath)}")
    lines.append("#")
    lines.append("# Edit this file to customize bone mappings and transformations")
    lines.append("# See example_track_mapping.txt for detailed documentation")
    lines.append("")

    # gather layout units if available
    layout_track_units: Optional[List[Any]] = None
    if layout_units:
        layout_track_units = layout_units
        Debug.log(f"Using MTAR layout track with {len(layout_track_units)} units")

    # iterate tracks
    for track_idx in range(track_count):
        unit_def = unit_defs[track_idx] if unit_defs else None
        track_name: str = f"Track{track_idx}"
        track_hash: Optional[int] = None

        if layout_track_units and track_idx < len(layout_track_units):
            layout_unit = layout_track_units[track_idx]
            if layout_unit.name:
                track_hash = layout_unit.name
                track_hash_int = track_hash.to_int() if hasattr(track_hash, 'to_int') else int(track_hash)
                resolved = util_hashing.unhash_rig_type(track_hash_int)
                if resolved:
                    track_name = resolved

        track_type: Optional[str] = None
        if unit_def and unit_def.unit_type is not None:
            try:
                track_type = unit_def.unit_type.name
            except Exception:  # noqa: E722
                track_type = f"UNKNOWN_{unit_def.unit_type}"

        segments_shorthand: List[str] = []
        actual_segment_count: Optional[int] = None
        if layout_track_units and track_idx < len(layout_track_units):
            layout_unit = layout_track_units[track_idx]
            # layout_unit may be TrackUnitWrapper (new format) or TrackUnit (old format)
            unit_segments = getattr(layout_unit, 'track_data', None)
            if unit_segments is None:
                unit_segments = getattr(layout_unit, 'segments_data', None)
            if unit_segments is not None:
                actual_segment_count = len(unit_segments)
                Debug.log(f"Track {track_idx}: MTAR reports {actual_segment_count} segments")

        if track_type:
            if track_type == 'MULTI_LOCAL_ORIENTATION':
                segment_count = actual_segment_count if actual_segment_count else 1
                if not actual_segment_count and unit_def:
                    if unit_def.bone_count:
                        segment_count = unit_def.bone_count
                    elif unit_def.track_count:
                        segment_count = unit_def.track_count
                segments_shorthand = ['q'] * segment_count
            else:
                segments = fwrap_metadata.get_segments_for_track_type(track_type)
                for seg in segments:
                    dtype = seg.get('data_type', '')
                    if dtype == 'quatdiff':
                        segments_shorthand.append('qd')
                    elif dtype == 'quat':
                        segments_shorthand.append('q')
                    elif dtype == 'vec3diff':
                        segments_shorthand.append('vd')
                    elif dtype == 'vec3':
                        segments_shorthand.append('v')
                    elif dtype == 'float':
                        segments_shorthand.append('f')
                    else:
                        segments_shorthand.append('?')
                if actual_segment_count and len(segments_shorthand) != actual_segment_count:
                    Debug.log_warning(
                        f"  Warning: Track {track_idx} type {track_type} expects {len(segments_shorthand)} segments, but MTAR has {actual_segment_count}"
                    )
                    if actual_segment_count > len(segments_shorthand):
                        segments_shorthand.extend(['?'] * (actual_segment_count - len(segments_shorthand)))
                    else:
                        segments_shorthand = segments_shorthand[:actual_segment_count]

        segment_str = ', '.join(segments_shorthand) if segments_shorthand else '?'
        if track_type == 'MULTI_LOCAL_ORIENTATION' and len(segments_shorthand) > 3 and all(s == 'q' for s in segments_shorthand):
            segment_str = f"q * {len(segments_shorthand)}"

        if track_hash:
            lines.append(f"# Track {track_idx} ({segment_str}) - Hash: {track_hash} (0x{track_hash:X})")
        else:
            lines.append(f"# Track {track_idx} ({segment_str})")
        if track_type:
            lines.append(f"# type={track_type}")

        if len(segments_shorthand) > 1:
            for seg_idx in range(len(segments_shorthand)):
                lines.append(f"{track_name}_{seg_idx} : {track_name}_{seg_idx}")
        else:
            lines.append(f"{track_name} : {track_name}")
        lines.append("")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return output_path
