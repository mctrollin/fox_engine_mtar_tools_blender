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

import os
from typing import Optional, List, Any

from ..py_core.core_logging import Debug

from ..py_utilities import util_hashing

from ..py_fox.fox_frig_types import FrigFile, RigUnitDef

from ..py_foxwrap import fwrap_metadata
from ..py_foxwrap.fwrap_mtar_reader import MtarReader


def _load_mapping_sources(frig_filepath: Optional[str], mtar_filepath: Optional[str]):
    """Load FRIG and MTAR source data; return (unit_defs, layout_units, track_count)."""
    frig_data: Optional[FrigFile] = None
    if frig_filepath:
        if not os.path.exists(frig_filepath):
            Debug.log_warning(f"FRIG file not found: {frig_filepath}")
        else:
            try:
                with open(frig_filepath, 'rb') as f:
                    frig_data = FrigFile.read(f)
            except (OSError, ValueError) as e:
                Debug.log_warning(f"Could not read FRIG file: {e}")
                frig_data = None

    layout_units: Optional[List[Any]] = None
    if mtar_filepath and os.path.exists(mtar_filepath):
        try:
            reader = MtarReader(mtar_filepath)
            reader.read_selected_ganis([0])
            if reader.layout_track:
                layout_units = reader.layout_track.track_units
        except (OSError, ValueError) as e:  # noqa: E722
            Debug.log_warning(f"Could not read MTAR file: {e}")

    if frig_data and frig_data.rig_def and frig_data.rig_def.unit_defs:
        unit_defs: List[RigUnitDef] = frig_data.rig_def.unit_defs
        track_count = len(unit_defs)
    elif layout_units:
        unit_defs = [None] * len(layout_units)  # type: ignore[var-assign]
        track_count = len(layout_units)
    else:
        raise ValueError("Cannot determine track count - provide either FRIG or MTAR layout information")

    return unit_defs, layout_units, track_count


def _determine_output_path(frig_filepath: Optional[str], mtar_filepath: Optional[str]) -> str:
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
    return output_path


def _resolve_track_name(track_idx: int, layout_unit: Any):
    track_name = f"Track{track_idx}"
    track_hash = None

    if layout_unit and getattr(layout_unit, 'name', None) is not None:
        track_hash = layout_unit.name
        try:
            track_hash_int = track_hash.to_int() if hasattr(track_hash, 'to_int') else int(track_hash)
            resolved = util_hashing.unhash_rig_type(track_hash_int)
            if resolved:
                track_name = resolved
        except Exception:
            pass

    return track_name, track_hash


def _map_segment_type_to_shorthand(segment_type):
    if segment_type is None:
        return '?'
    seg_name = segment_type.name if hasattr(segment_type, 'name') else str(segment_type)
    if seg_name in ('QUAT_DIFF', 'QUAT'):
        return 'q'
    if seg_name in ('VECTOR_DIFF', 'VECTOR3'):
        return 'v'
    if seg_name == 'FLOAT':
        return 'f'
    if seg_name == 'VECTOR2':
        return 'v2'
    if seg_name == 'VECTOR4':
        return 'v4'
    return '?'


def _infer_segment_info(track_type: Optional[str], actual_segment_count: Optional[int], unit_def: Any, layout_unit: Any):
    segments_shorthand: List[str] = []
    segment_count = 1

    if track_type:
        if track_type == 'MULTI_LOCAL_ORIENTATION':
            segment_count = actual_segment_count or 1
            if segment_count == 1 and unit_def:
                if getattr(unit_def, 'bone_count', None):
                    segment_count = unit_def.bone_count
                elif getattr(unit_def, 'track_count', None):
                    segment_count = unit_def.track_count
            segment_count = max(1, segment_count)
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
            if actual_segment_count:
                if len(segments_shorthand) != actual_segment_count:
                    Debug.log_warning(
                        f"  Warning: track_type {track_type} expects {len(segments_shorthand)} segments, but MTAR has {actual_segment_count}"
                    )
                if actual_segment_count > len(segments_shorthand):
                    segments_shorthand.extend(['?'] * (actual_segment_count - len(segments_shorthand)))
                else:
                    segments_shorthand = segments_shorthand[:actual_segment_count]
            segment_count = len(segments_shorthand) if segments_shorthand else 1
    else:
        segment_count = actual_segment_count or 1
        if layout_unit:
            unit_segments = getattr(layout_unit, 'track_data', None)
            if unit_segments is None:
                unit_segments = getattr(layout_unit, 'segments_data', None)
            if unit_segments is not None:
                segments_shorthand = []
                for seg in unit_segments:
                    seg_type = None
                    if hasattr(seg, 'td_type'):
                        seg_type = seg.td_type
                    elif getattr(seg, 'data_blob', None) is not None:
                        seg_type = seg.data_blob.type
                    segments_shorthand.append(_map_segment_type_to_shorthand(seg_type))

        if not segments_shorthand:
            segments_shorthand = ['?'] * segment_count
        segment_count = len(segments_shorthand)

    if not segments_shorthand:
        segments_shorthand = ['?'] * segment_count

    if segment_count > 1 and all(s == segments_shorthand[0] for s in segments_shorthand):
        segment_str = f"{segments_shorthand[0]} * {segment_count}"
    else:
        segment_str = ', '.join(segments_shorthand) if segments_shorthand else '?'

    return segment_count, segments_shorthand, segment_str


def _emit_track_mapping_lines(track_name: str, track_type: Optional[str], track_hash: Any, segment_count: int, segment_str: str) -> List[str]:
    lines: List[str] = []
    if track_hash:
        lines.append(f"# Track {track_name} ({segment_str}) - Hash: {track_hash} (0x{track_hash:X})")
    else:
        lines.append(f"# Track {track_name} ({segment_str})")
    if track_type:
        lines.append(f"# type={track_type}")

    lines.append(f"{track_name} : {track_name}")
    for seg_idx in range(1, segment_count):
        lines.append(f"{track_name}_{seg_idx} : {track_name}_{seg_idx}")
    lines.append("")
    return lines


def generate_mapping_template(frig_filepath: Optional[str], mtar_filepath: Optional[str]) -> str:
    """Create a track mapping template file."""
    unit_defs, layout_track_units, track_count = _load_mapping_sources(frig_filepath, mtar_filepath)
    output_path = _determine_output_path(frig_filepath, mtar_filepath)

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

    if layout_track_units:
        Debug.log(f"Using MTAR layout track with {len(layout_track_units)} units")

    for track_idx in range(track_count):
        unit_def = unit_defs[track_idx] if unit_defs else None
        layout_unit = layout_track_units[track_idx] if layout_track_units and track_idx < len(layout_track_units) else None

        track_name, track_hash = _resolve_track_name(track_idx, layout_unit)

        track_type = None
        if unit_def and getattr(unit_def, 'unit_type', None) is not None:
            try:
                track_type = unit_def.unit_type.name
            except Exception:
                track_type = f"UNKNOWN_{unit_def.unit_type}"

        actual_segment_count: Optional[int] = None
        if layout_unit is not None:
            unit_segments = getattr(layout_unit, 'track_data', None)
            if unit_segments is None:
                unit_segments = getattr(layout_unit, 'segments_data', None)
            if unit_segments is not None:
                actual_segment_count = len(unit_segments)
                Debug.log(f"Track {track_idx}: MTAR reports {actual_segment_count} segments")

        segment_count, segments_shorthand, segment_str = _infer_segment_info(track_type, actual_segment_count, unit_def, layout_unit)

        lines.extend(_emit_track_mapping_lines(track_name, track_type, track_hash, segment_count, segment_str))

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return output_path
