


from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..py_fox.fox_frig_types import RigUnitType
from ..py_fox.fox_gani_enums import SegmentType


@dataclass
class TrackMetaData:
    """Container for metadata for a single track.

    Holds layout-level defaults as well as per-animation overrides.
    This object is intentionally decoupled from Blender types and can be
    constructed from parsing strings (layout/action properties) or converted
    to binary-writing-friendly fields by the writer code (later).
    """
    track_name: str = ''
    # Optional numeric hash for the track name (StrCode32 integer value)
    name_hash: Optional[int] = None
    # Segment definitions: list of dicts from get_segments_for_track_type()
    segment_types: List[SegmentType] = field(default_factory=list)
    # Component bit sizes (per-segment), if provided
    component_bit_sizes: Optional[List[int]] = None
    # unit_flags integer value (optional)
    unit_flags: Optional[int] = None
    flags_list: Optional[List[str]] = None
    # Rig unit type: RigUnitType enum value e.g. ROOT, ARM, ORIENTATION
    rig_unit_type: Optional[RigUnitType] = None
    # Additional optional parameters parsed from mapping or action
    rotation_offset: Optional[Dict[str, Any]] = None
    rotation_axis_map: Optional[List[Dict[str, Any]]] = None
    space_r: Optional[Dict[str, Any]] = None
    as_ik_up: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a serialization-ready dict representing the metadata."""
        return {
            'track_name': self.track_name,
            'name_hash': self.name_hash,
            'segment_types': [s.name for s in self.segment_types],
            'component_bit_sizes': self.component_bit_sizes,
            'unit_flags': self.unit_flags,
            'flags_list': self.flags_list,
            'rig_unit_type': self.rig_unit_type,
            'rotation_offset': self.rotation_offset,
            'rotation_axis_map': self.rotation_axis_map,
            'space_r': self.space_r,
            'as_ik_up': self.as_ik_up,
        }

    def __repr__(self) -> str:
        return f"TrackMetaData(track_name={self.track_name!r}, segments={len(self.segment_types)}, rig_unit_type={self.rig_unit_type!r})"

