from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, Union

import io
import bpy

from ..py_core.core_logging import Debug

from ..py_fox.fox_gani_types import TrackUnitFlags, EvpHeader, TrackHeader

from .fwrap_track_types import TrackUnitWrapper, Tracks
from .fwrap_gani2_writer import Gani2Writer




@dataclass
class GaniExportTracksData:
    """Bones/motion tracks for one GANI file.

    - gani_tracks: list of track wrappers (already mapped and transformed)
    - action: optional source action (for metadata context)
    - source: human-readable source string
    """
    gani_tracks: List[TrackUnitWrapper]
    action: Optional[bpy.types.Action] = None
    source: Optional[str] = None


@dataclass
class GaniExportMotionPointsData:
    """Motion-point tracks for one GANI file.

    Contains layout metadata for the motion points section used by GANI2 writer.
    """
    motion_point_tracks: List[TrackUnitWrapper]
    motion_point_track_header: Optional[TrackHeader] = None


@dataclass
class Gani1ExportShaderData:
    """Shader-node tracks for legacy GANI1 shader export.

    Used only by the old-format and reference-mode export path.
    """
    property_tracks: List[List[TrackUnitWrapper]]
    property_names: List[str]
    property_headers: Optional[List[Optional[Dict[str, int]]]] = None


@dataclass
class GaniMotionEventsData:
    """Motion event table wrapper for a GANI file.

    Encapsulates the EVP header needed by the exporter to write motion events data.
    """
    motion_events: EvpHeader


@dataclass
class GaniExportData:
    """Complete GANI export payload for one output file.

    Contains:
    - base gan name/frame info
    - core tracks (gani_tracks_data)
    - optional motion points/shader/events sub-structures
    - gamified legacy fields for GANI1 path round-trip
    """
    # Shared data -----
    gani_name: str
    gani_frame_count: int
    gani_frame_rate: int
    gani_frame_start: int
    gani_frame_end: int
    gani_tracks_data: GaniExportTracksData
    gani_motion_points_data: Optional[GaniExportMotionPointsData] = None
    gani_motion_events_data: Optional[GaniMotionEventsData] = None
    gani_node_params: Optional[Dict[str, List[Tuple[int, Union[float, str, int]]]]] = None
    gani_path_hash: Optional[int] = None
    
    # Old format only data -----
    gani1_shader_nodes_data: Optional[Gani1ExportShaderData] = None
    # Old-format file table 'unknown' field (MtarTableList.unknown, ushort).
    # Distinct from TrackHeader.unknown_b — these are different binary fields.
    gani1_table_unknown: Optional[int] = None
    # Old-format FoxData string lists for lossless GANI1 round-trip (reference mode).
    # In normal export these come from the Blender action; in reference mode they
    # come from GaniImportData since there is no action.
    gani1_skeleton_list: Optional[List[str]] = None
    gani1_motion_point_list: Optional[List] = None
    gani1_motion_point_parent_list: Optional[List] = None
    gani1_no_skl_list: bool = False

    def count_segments(self) -> int:
        if not self.gani_tracks_data or not self.gani_tracks_data.gani_tracks:
            return 0
        return sum(
            len(w.segments_track_data)
            for w in self.gani_tracks_data.gani_tracks
            if w.segments_track_data
        )

    def to_bytes(self, layout_track: 'Tracks') -> bytes:
        """Convert this GANI data to binary format."""
        writer = Gani2Writer()
        buffer = io.BytesIO()

        unit_flags_per_file = []
        segment_bit_sizes_per_file = []

        for track_idx, track_unit in enumerate(layout_track.track_units):
            default_flags = track_unit.unit_flags if track_idx < len(layout_track.track_units) else 0
            segment_count = len(track_unit.segments_data) if track_unit and track_unit.segments_data else 0

            if track_idx < len(self.gani_tracks_data.gani_tracks):
                gani_track = self.gani_tracks_data.gani_tracks[track_idx]

                if gani_track.unit_flags:
                    flags_value = TrackUnitFlags.track_unit_flags_to_int(gani_track.unit_flags)
                else:
                    Debug.log_warning(f"Warning: Track {track_idx} has no unit_flags in gani_track, using layout default ({default_flags})")
                    flags_value = default_flags

                unit_flags_per_file.append(flags_value)

                for segment_idx in range(segment_count):
                    if segment_idx < len(gani_track.segments_track_data):
                        segment_data = gani_track.segments_track_data[segment_idx]
                        component_bit_size = segment_data.data_blob.component_bit_size
                        segment_bit_sizes_per_file.append(component_bit_size)
                    else:
                        Debug.log_warning(f"Warning: Track {track_idx} segment {segment_idx} missing in gani_track, using bit size 0")
                        segment_bit_sizes_per_file.append(0)
            else:
                Debug.log_warning(f"Warning: Track {track_idx} missing in gani_tracks, using layout defaults (flags={default_flags}, bits=0)")
                unit_flags_per_file.append(default_flags)
                for _ in range(segment_count):
                    segment_bit_sizes_per_file.append(0)

        # Try get motion parameters (gani1)
        if self.gani_node_params is not None:
            motion_params = self.gani_node_params.get("MOTION", [])
        else:
            motion_params = []

        writer.write_gani_to_buffer(
            buffer, self.gani_tracks_data.gani_tracks, layout_track,
            self.gani_frame_count, self.gani_frame_rate,
            params=motion_params,
            unit_flags_per_file=unit_flags_per_file,
            segment_bit_sizes_per_file=segment_bit_sizes_per_file,
        )

        return buffer.getvalue()
