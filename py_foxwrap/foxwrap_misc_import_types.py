from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Union, Dict

import io

from ..py_utilities.utilities_hashing import unhash_rig_type
from ..py_fox.fox_gani_enums import CommonInfoNodeType
from ..py_fox.fox_gani_types import EvpHeader, TrackMiniHeader, TrackHeader
from ..py_fox.fox_mtar_types import MtarHeader, MtarMiniDataNode, MtarTableList, MtarTableList2

from .foxwrap_misc import Tracks, TrackUnitWrapper
from .foxwrap_motionpoint_types import MotionPointWrapper


@dataclass
class ShaderTrackWrapper:
    property_name: str
    tracks: Tracks


@dataclass
class GaniImportData:
    gani_bone_tracks: List[TrackUnitWrapper]
    gani_mtp_tracks: List[TrackUnitWrapper]
    gani_events: Optional[EvpHeader]
    gani_layout_track: Tracks
    gani_track_mini_header: TrackMiniHeader
    gani_motion_point_layout: Optional[Tracks]
    gani_motion_point_track_header: Optional[TrackHeader]
    gani1_shader_tracks: List[ShaderTrackWrapper] = None
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
    def from_gani1(cls, gani_bone_tracks: List[TrackUnitWrapper], gani_mtp_tracks: List[TrackUnitWrapper], gani_events: Optional[EvpHeader], gani_layout_track: Tracks, gani_track_mini_header: TrackMiniHeader, gani_motion_point_layout: Optional[Tracks], gani_motion_point_track_header: Optional[TrackHeader], gani1_shader_tracks: List[ShaderTrackWrapper], gani_skeleton_list: Optional[List[str]], gani1_motion_point_list: Optional[List[str]], gani1_motion_point_parent_list: Optional[List[str]], gani_node_params: Optional[Dict[str, List[Tuple[int, Union[float, str, int]]]]] = None, file_header: Optional[Union['MtarTableList', 'MtarTableList2']] = None) -> 'GaniImportData':
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
    def from_gani2(cls, gani_bone_tracks: List[TrackUnitWrapper], gani_mtp_tracks: List[TrackUnitWrapper], gani_events: Optional[EvpHeader], gani_layout_track: Tracks, gani_track_mini_header: TrackMiniHeader, gani_motion_point_layout: Optional[Tracks], gani_motion_point_track_header: Optional[TrackHeader], file_header: Optional[Union['MtarTableList', 'MtarTableList2']] = None, gani_node_params: Optional[Dict[str, List[Tuple[int, Union[float, str, int]]]]] = None, gani_skeleton_list: Optional[List[str]] = None) -> 'GaniImportData':
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


@dataclass
class CommonInfo:
    layout_track: Tracks
    skeleton_list: List[str]
    motion_points: Optional[MotionPointWrapper]

    @staticmethod
    def read_layout_track(br: io.BytesIO) -> Optional[Tracks]:
        return Tracks.read(br)

    @staticmethod
    def read_skeleton_list(br: io.BytesIO, size: int) -> Optional[List[str]]:
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
    def read_motion_points(br: io.BytesIO) -> Optional[MotionPointWrapper]:
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
                layout_track = cls.read_layout_track(br)
            elif node.name == CommonInfoNodeType.SkeletonList:
                skeleton_list = cls.read_skeleton_list(br, node.data_size)
            elif node.name == CommonInfoNodeType.MotionPoints:
                motion_points = cls.read_motion_points(br)
            if node.next_node_offset == 0:
                break
            node_pos += node.next_node_offset
        return cls(layout_track=layout_track, skeleton_list=skeleton_list or [], motion_points=motion_points)
