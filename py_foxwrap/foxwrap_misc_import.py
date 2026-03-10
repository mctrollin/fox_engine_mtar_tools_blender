"""
Import-only fake types for MTAR importer.
"""
from dataclasses import dataclass, field
import io
from typing import Optional, List, Tuple, Union, Dict

# forward references for type hinting
from ..py_fox.fox_mtar_types import MtarTableList, MtarTableList2

from .foxwrap_misc import Tracks, TrackUnitWrapper
from .foxwrap_motionpoint import MotionPointWrapper

from ..py_utilities.utilities_hashing import unhash_rig_type

from ..py_fox.fox_gani_enums import CommonInfoNodeType
from ..py_fox.fox_gani_types import EvpHeader, TrackMiniHeader, TrackHeader
from ..py_fox.fox_mtar_types import MtarHeader, MtarMiniDataNode


@dataclass
class ShaderTrackWrapper:
    """Wrapper for a single SHADER child node's animation data.
    
    SHADER nodes in old-format GANI files contain facial/property animation tracks.
    Each child property node (e.g., TENSION_CHEEKL, TENSION_CHEEKR) is a separate
    animation track that can be imported as custom properties or shape key drivers.
    
    Attributes:
        property_name: The resolved property name (StrCode32 hash) or hex fallback
        tracks: Full Tracks object with TrackHeader + TrackUnits and keyframe data
    """
    property_name: str
    tracks: Tracks


@dataclass
class GaniImportData:
    """Unified wrapper for animation data returned by both old and new GANI readers.
    
    This dataclass encapsulates all the data that a single GANI file contains, whether
    it's old-format (FoxData) or new-format (GANI2). It allows the importer to work
    uniformly with both formats without needing to handle separate return tuples.
    
    Attributes:
        bone_tracks: List of main animation tracks (bones, rotation/location/scale)
        mtp_tracks: List of motion point animation tracks (optional, empty if not present)
        events: Motion event data (optional, None if not present)
        layout_track: The track layout structure defining bones/segments (mandatory).
            For GANI2: This is a reference (alias) to the shared CommonInfo layout.
            For GANI (old): This is a unique Tracks object parsed from the MOTION node.
        track_mini_header: Synthesized track header for segment info (component bit sizes, etc.)
        motion_point_layout: Motion point Tracks object structure (optional)
        motion_point_track_header: Motion point TrackHeader (optional)
        shader_tracks: List of facial/shader property animation tracks (optional, empty if not present)
        node_params: Dict mapping FoxData node keys to their parameter lists.
            Keys use the format: ``"MOTION"``, ``"MOTION/UNIT"``, ``"SHADER"``,
            ``"SHADER/{property_name}"``, etc. Values are lists of ``(name_hash, value)``
            tuples where ``value`` is ``float`` (FLOAT), ``str`` (STRING inline),
            or ``int`` (STRING hash-only).
    """
    # TODO: make it clear (at least via comment) which vars are used for gani and which for gani2.
    bone_tracks: List[TrackUnitWrapper]
    mtp_tracks: List[TrackUnitWrapper]
    events: Optional[EvpHeader]
    layout_track: Tracks
    track_mini_header: TrackMiniHeader
    motion_point_layout: Optional[Tracks]
    motion_point_track_header: Optional[TrackHeader]
    shader_tracks: List[ShaderTrackWrapper] = None
    skeleton_list: Optional[List[str]] = None
    motion_point_list: Optional[List[str]] = None
    motion_point_parent_list: Optional[List[str]] = None
    node_params: Dict[str, List[Tuple[int, Union[float, str, int]]]] = field(default_factory=dict)
    # optional header from containing MTAR file (used for path/hash, offset sorting)
    file_header: Optional[Union['MtarTableList', 'MtarTableList2']] = None
    
    def __post_init__(self):
        """Initialize shader_tracks to empty list if None."""
        if self.shader_tracks is None:
            self.shader_tracks = []
    
    @property
    def has_events(self) -> bool:
        """Check if this GANI has motion events."""
        return self.events is not None


@dataclass
class CommonInfo:
    layout_track : Tracks
    skeleton_list : list[str]
    motion_points : Optional[MotionPointWrapper]

    @classmethod
    def read_layout_track(self, br: io.BytesIO) -> Optional[Tracks]:
        """Read the layout track data."""
        return Tracks.read(br)

    @classmethod
    def read_skeleton_list(self, br: io.BytesIO, size: int) -> Optional[list[str]]:
        """Read the skeleton list data."""
        # Read the raw data for the skeleton list (size bytes)
        data = br.read(size)
        if not data:
            return []

        # Try decoding as UTF-8/ASCII and split by null terminator or newline
        try:
            text = data.decode('utf-8', errors='replace')
        except Exception:
            # Fallback to Latin-1 if decoding fails
            text = data.decode('latin-1', errors='replace')

        # Common formats: null-terminated strings, newline-separated, or just concatenated
        if '\x00' in text:
            items = [s for s in text.split('\x00') if s]
        elif '\n' in text:
            items = [s for s in text.split('\n') if s]
        else:
            # If no separators found, split by whitespace as a fallback
            items = [s for s in text.split() if s]

        return items

    @classmethod
    def read_motion_points(cls, br: io.BytesIO) -> Optional[MotionPointWrapper]:
        """Read the motion points list and convert to MotionPointWrapper."""
        from ..py_fox.fox_mtar_types import MotionPointList2
        mpl = MotionPointList2.read(br)
        return MotionPointWrapper.from_new_format(mpl, unhash_rig_type)
    
    @classmethod
    def read(cls, br: io.BytesIO, header: MtarHeader) -> 'CommonInfo':
        """Read CommonInfo section of an MTAR file."""
        br.seek(header.common_info_offset)

        layout_track = None
        skeleton_list = None
        motion_points = None

        node_pos = header.common_info_offset
        node_idx = 0
        
        while True:
            br.seek(node_pos)
            node = MtarMiniDataNode.read(br)
            
            if node.name == CommonInfoNodeType.LayoutTrack:
                # LayoutTrack should be first node
                layout_track = cls.read_layout_track(br)
            elif node.name == CommonInfoNodeType.SkeletonList:
                # SkeletonList only present if MTAR_FLAGS_HAS_SKEL_LIST is set
                skeleton_list = cls.read_skeleton_list(br, node.data_size)
            elif node.name == CommonInfoNodeType.MotionPoints:
                # MotionPoints can be node 1 (if no skeleton) or node 2 (if skeleton exists)
                motion_points = cls.read_motion_points(br)
            else:
                raise ValueError(f"Unknown CommonInfo node type: {node.name}")
            
            if node.next_node_offset == 0:
                break
                
            node_pos = node_pos + node.next_node_offset
            node_idx += 1
        
        common_info : CommonInfo = CommonInfo(layout_track=layout_track, skeleton_list=skeleton_list, motion_points=motion_points)

        return common_info
