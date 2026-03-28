from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Union, Dict

import io

from ..py_utilities.util_hashing import unhash_rig_type

from ..py_fox.fox_gani_enums import CommonInfoNodeType
from ..py_fox.fox_gani_types import EvpHeader, TrackMiniHeader, TrackHeader
from ..py_fox.fox_mtar_types import MtarHeader, MtarMiniDataNode, MtarTableList, MtarTableList2

from .fwrap_gani_track_types import Tracks, TrackUnitWrapper
from .fwrap_gani_motionpoint_types import MotionPointWrapper


@dataclass
class Gani1ShaderTrackWrapper:
    """Container for GANI1 shader tracks tied to a property name.

    Used when importing legacy GANI1 shader data, where each property may map to
    multiple tracks.  The `property_name` is the shader property identifier.
    """
    property_name: str
    tracks: Tracks


@dataclass
class GaniImportData:
    """Aggregated data object for one imported GANI file.

    - gani_bone_tracks: imported bone animation tracks
    - gani_mtp_tracks: imported motion-point tracks (optional)
    - gani_events: optional event header (EvpHeader)
    - gani_layout_track: layout block metadata for track order and segments
    - gani_track_mini_header: mini header for this GANI
    - gani_motion_point_layout: optional motion point layout information
    - _from_gani1/from_gani2 classmethods for source-specific construction
    """
    gani_bone_tracks: List[TrackUnitWrapper]
    gani_mtp_tracks: List[TrackUnitWrapper]
    gani_events: Optional[EvpHeader]
    gani_layout_track: Tracks
    gani_track_mini_header: TrackMiniHeader
    gani_motion_point_layout: Optional[Tracks]
    gani_motion_point_track_header: Optional[TrackHeader]
    gani1_shader_tracks: List[Gani1ShaderTrackWrapper] = None
    gani_skeleton_list: Optional[List[str]] = None
    gani1_motion_point_list: Optional[List[str]] = None
    gani1_motion_point_parent_list: Optional[List[str]] = None
    gani_node_params: Dict[str, List[Tuple[int, Union[float, str, int]]]] = field(default_factory=dict)
    file_header: Optional[Union['MtarTableList', 'MtarTableList2']] = None

    def __post_init__(self):
        if self.gani1_shader_tracks is None:
            self.gani1_shader_tracks = []

    @property
    def has_events(self) -> bool:
        return self.gani_events is not None

    @classmethod
    def from_gani1(cls,
                   gani_bone_tracks: List[TrackUnitWrapper],
                   gani_mtp_tracks: List[TrackUnitWrapper],
                   gani_events: Optional[EvpHeader],
                   gani_layout_track: Tracks,
                   gani_track_mini_header: TrackMiniHeader,
                   gani_motion_point_layout: Optional[Tracks],
                   gani_motion_point_track_header: Optional[TrackHeader],
                   gani1_shader_tracks: List[Gani1ShaderTrackWrapper],
                   gani_skeleton_list: Optional[List[str]],
                   gani1_motion_point_list: Optional[List[str]],
                   gani1_motion_point_parent_list: Optional[List[str]],
                   gani_node_params: Optional[Dict[str, List[Tuple[int, Union[float, str, int]]]]] = None,
                   file_header: Optional[Union['MtarTableList', 'MtarTableList2']] = None
                   ) -> 'GaniImportData':
        if gani_node_params is None:
            gani_node_params = {}
        return cls(
            gani_bone_tracks=gani_bone_tracks,
            gani_mtp_tracks=gani_mtp_tracks,
            gani_events=gani_events,
            gani_layout_track=gani_layout_track,
            gani_track_mini_header=gani_track_mini_header,
            gani_motion_point_layout=gani_motion_point_layout,
            gani_motion_point_track_header=gani_motion_point_track_header,
            gani1_shader_tracks=gani1_shader_tracks,
            gani_node_params=gani_node_params,
            file_header=file_header,
            gani_skeleton_list=gani_skeleton_list,
            gani1_motion_point_list=gani1_motion_point_list,
            gani1_motion_point_parent_list=gani1_motion_point_parent_list,
        )

    @classmethod
    def from_gani2(cls,
                   gani_bone_tracks: List[TrackUnitWrapper],
                   gani_mtp_tracks: List[TrackUnitWrapper],
                   gani_events: Optional[EvpHeader],
                   gani_layout_track: Tracks,
                   gani_track_mini_header: TrackMiniHeader,
                   gani_motion_point_layout: Optional[Tracks],
                   gani_motion_point_track_header: Optional[TrackHeader],
                   file_header: Optional[Union['MtarTableList', 'MtarTableList2']] = None,
                   gani_node_params: Optional[Dict[str, List[Tuple[int, Union[float, str, int]]]]] = None,
                   gani_skeleton_list: Optional[List[str]] = None
                   ) -> 'GaniImportData':
        if gani_node_params is None:
            gani_node_params = {}
        return cls(
            gani_bone_tracks=gani_bone_tracks,
            gani_mtp_tracks=gani_mtp_tracks,
            gani_events=gani_events,
            gani_layout_track=gani_layout_track,
            gani_track_mini_header=gani_track_mini_header,
            gani_motion_point_layout=gani_motion_point_layout,
            gani_motion_point_track_header=gani_motion_point_track_header,
            gani1_shader_tracks=[],
            gani_node_params=gani_node_params,
            file_header=file_header,
            gani_skeleton_list=gani_skeleton_list,
            gani1_motion_point_list=None,
            gani1_motion_point_parent_list=None,
        )

    @classmethod
    def iter_tracks(cls, all_gani_data: List['GaniImportData'], include_mtp: bool = True):
        """Yield bone + optional motion point tracks from GaniImportData list."""
        for data in all_gani_data:
            for gani_track in data.gani_bone_tracks:
                yield gani_track
            if include_mtp:
                for mtp_track in data.gani_mtp_tracks:
                    yield mtp_track

    @classmethod
    def iter_bone_tracks(cls, all_gani_data: List['GaniImportData']):
        """Yield only bone tracks from a list of GaniImportData objects."""
        return cls.iter_tracks(all_gani_data, include_mtp=False)


@dataclass
class CommonInfo:
    """Common MTAR data parsed from the 'common_info' section of the gani2 format.

    Contains:
    - layout_track: global track layout metadata
    - skeleton_list: string list of skeleton joint names (may be empty)
    - motion_points: optional MotionPointWrapper for motion-point data
    """
    layout_track: Tracks
    skeleton_list: List[str]
    motion_points: Optional[MotionPointWrapper]

    @staticmethod
    def _read_layout_track(br: io.BytesIO) -> Optional[Tracks]:
        return Tracks.read(br)

    @staticmethod
    def _read_skeleton_list(br: io.BytesIO, size: int) -> Optional[List[str]]:
        data = br.read(size)
        if not data:
            return []
        try:
            text = data.decode('utf-8', errors='replace')
        except Exception:
            text = data.decode('latin-1', errors='replace')
        if '\x00' in text:
            items = [s for s in text.split('\x00') if s]
        elif '\n' in text:
            items = [s for s in text.split('\n') if s]
        else:
            items = [s for s in text.split() if s]
        return items

    @staticmethod
    def _read_motion_points(br: io.BytesIO) -> Optional[MotionPointWrapper]:
        from ..py_fox.fox_mtar_types import MotionPointList2
        mpl = MotionPointList2.read(br)
        return MotionPointWrapper.from_new_format(mpl, unhash_rig_type)

    @classmethod
    def read(cls, br: io.BytesIO, header: MtarHeader) -> 'CommonInfo':
        br.seek(header.common_info_offset)
        layout_track = None
        skeleton_list = None
        motion_points = None
        node_pos = header.common_info_offset
        while True:
            br.seek(node_pos)
            node = MtarMiniDataNode.read(br)
            if node.name == CommonInfoNodeType.LayoutTrack:
                layout_track = cls._read_layout_track(br)
            elif node.name == CommonInfoNodeType.SkeletonList:
                skeleton_list = cls._read_skeleton_list(br, node.data_size)
            elif node.name == CommonInfoNodeType.MotionPoints:
                motion_points = cls._read_motion_points(br)
            if node.next_node_offset == 0:
                break
            node_pos += node.next_node_offset
        return cls(layout_track=layout_track, skeleton_list=skeleton_list or [], motion_points=motion_points)
