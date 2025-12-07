"""
Import-only fake types for MTAR importer.
"""
from dataclasses import dataclass
import io
from typing import Optional

from .foxwrap_misc import Tracks

from ..py_fox.fox_gani_enums import CommonInfoNodeType
from ..py_fox.fox_mtar_types import MotionPointList2, MtarHeader, MtarMiniDataNode


@dataclass
class CommonInfo:
    layout_track : Tracks
    skeleton_list : list[str]
    motion_points : MotionPointList2

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
    def read_motion_points(self, br: io.BytesIO) -> Optional[MotionPointList2]:
        """Read the motion points list."""
        return MotionPointList2.read(br)
    
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
