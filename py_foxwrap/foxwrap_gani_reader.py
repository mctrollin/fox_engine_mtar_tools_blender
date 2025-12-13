"""
GANI2 animation data import functionality for Metal Gear Solid V files.
"""
import io
from typing import List, Optional, Tuple

from ..py_utilities.utilities_logging import Debug
from ..py_utilities.utilities_rig_hash import unhash_rig_type
from ..py_utilities.utilities_binary_write import align_length

from ..py_fox.fox_mtar_types import MtarTableList2
from ..py_fox.fox_misc_types import StrCode32
from ..py_fox.fox_gani_types import (
    SegmentType,
    TrackUnitFlags,
    TrackData,
    TrackDataBlob,
    TrackUnit,
    TrackHeader,
    Gani2TrackData,
    TrackMiniHeader,
    EvpHeader,
)

from .foxwrap_misc import Tracks, TrackDataBlobWrapper, TrackUnitWrapper


def resolve_track_name(rig_hash: StrCode32, prefix: Optional[str] = None) -> str:
    """Resolve a StrCode32 hash to a readable name.
    
    Args:
        rig_hash: StrCode32 object containing the hash
        prefix: Optional prefix to add if unhashing fails (e.g., "MotionPoint")
        
    Returns:
        Resolved name string (unhashed name, or prefixed hex, or just hex)
    """
    # Try to unhash the name
    bone_name = unhash_rig_type(rig_hash.to_int())
    
    if bone_name:
        return bone_name
    else:
        # Use prefix + hex format as fallback
        hex_str = str(rig_hash)  # StrCode32.__str__() gives "0x{value:08X}"
        if prefix:
            return f"{prefix}_{hex_str}"
        else:
            return hex_str


def apply_track_naming(gani_tracks: List[TrackUnitWrapper], prefix: Optional[str] = None) -> List[TrackUnitWrapper]:
    """Apply name resolution to a list of GaniTracks.
    
    Args:
        gani_tracks: List of GaniTrack objects with StrCode32 names
        prefix: Optional prefix for unresolved names (e.g., "MotionPoint")
        
    Returns:
        List of GaniTrack objects with resolved string names
    """
    named_tracks = []
    
    for gani_track in gani_tracks:
        # gani_track.name is a StrCode32 object
        resolved_name = resolve_track_name(gani_track.name, prefix)
        
        # Update keyframes_tracks with resolved names
        named_keyframes_tracks = []
        for keyframe_track in gani_track.segments_track_data:
            named_track = TrackDataBlobWrapper(
                name=resolved_name,
                segment_index=keyframe_track.segment_index,
                data_blob=keyframe_track.data_blob
            )
            named_keyframes_tracks.append(named_track)
        
        # Create new GaniTrack with resolved name
        named_gani_track = TrackUnitWrapper(
            name=resolved_name,
            segments_track_data=named_keyframes_tracks,
            unit_flags=gani_track.unit_flags,
            rig_unit_type=gani_track.rig_unit_type
        )
        named_tracks.append(named_gani_track)
    
    return named_tracks

class Gani2Reader:
    """Reader for GANI2 animation data."""



    def read_gani(self, file_data: bytes, layout_track: Tracks, file_header: MtarTableList2, track_count: int, is_new_format: bool) -> Tuple[List[TrackUnitWrapper], List[TrackUnitWrapper], Optional[EvpHeader], TrackMiniHeader, Optional[Tracks], Optional[TrackHeader]]:
        Debug.log("  Reading GANI data:")
        Debug.log(f"    Track count: {track_count}")
        Debug.log(f"    Tracks offset: 0x{file_header.tracks_offset:X}")
        Debug.log(f"    New format: {is_new_format}")

        # Tracks: Let gani_reader handle the track data reading
        gani_tracks, track_mini_header = self.read_all_tracks(
            file_data,
            file_header.tracks_offset,
            track_count,
            layout_track
        )
        Debug.log(f"    Read {len(gani_tracks)} track(s)")

        # Initialize motion point tracks as empty list
        motion_point_gani_tracks: List[TrackUnitWrapper] = []
        motion_point_layout: Optional[Tracks] = None
        motion_point_track_header: Optional[TrackHeader] = None
        
        # Apply naming resolution to bone tracks (no prefix - will use hex fallback)
        named_gani_tracks = apply_track_naming(gani_tracks, prefix=None)

        motion_events: Optional[EvpHeader] = None
        if is_new_format:
            Debug.log("    Processing new format sections...")

            # MotionPointTracks: Handle motion point tracks if present (new format only)
            if file_header.motion_point_tracks_offset != 0:
                Debug.log(f"      Motion point tracks offset: 0x{file_header.motion_point_tracks_offset:X}")
                motion_ptr = file_header.tracks_offset + file_header.motion_point_tracks_offset
                br = io.BytesIO(file_data)
                br.seek(motion_ptr)
                
                # Read motion point tracks - they use Tracks structure (like layout tracks)
                # Unlike layout tracks, motion point tracks have actual data blobs
                motion_point_layout = Tracks.read(br, file_data=file_data, read_data_blobs=True)
                Debug.log(f"      Motion point layout: {motion_point_layout.header.unit_count} unit(s), {motion_point_layout.header.segment_count} segment(s)")
                Debug.log(f"        Frame count: {motion_point_layout.header.frame_count}, Frame rate: {motion_point_layout.header.frame_rate}")
                
                # Store the TrackHeader for later use
                motion_point_track_header = motion_point_layout.header
                
                # Convert TrackUnits with data_blobs to GaniTrack format
                motion_point_gani_tracks_raw = self.convert_tracks_to_gani_tracks(motion_point_layout)
                Debug.log(f"      Read {len(motion_point_gani_tracks_raw)} motion point track(s)")
                
                # Apply naming resolution to motion point tracks
                motion_point_gani_tracks = apply_track_naming(motion_point_gani_tracks_raw, prefix="")

            # MotionEvents: Handle motion events if present
            br = io.BytesIO(file_data)
            if file_header.motion_events_offset != 0:
                Debug.log(f"      Motion events offset: 0x{file_header.motion_events_offset:X}")
                br.seek(file_header.motion_events_offset)
                motion_events = EvpHeader.read(br)
                Debug.log(f"      Read motion events: {motion_events.count} event(s)")

        Debug.log(f"    GANI read complete: {len(named_gani_tracks)} named track(s)")
        return named_gani_tracks, motion_point_gani_tracks, motion_events, track_mini_header, motion_point_layout, motion_point_track_header

    # read gani2 tracks
    def read_all_tracks(self, file_data: bytes, track_header_ptr: int, track_count: int, layout_track: Tracks) -> Tuple[List[TrackUnitWrapper], TrackMiniHeader]:
        """Read all animation tracks from a GANI2 section.

        Returns a tuple of (gani_tracks_list, mini_header_object).
        """
        Debug.log("      Reading all tracks:")
        Debug.log(f"        Track header ptr: 0x{track_header_ptr:X}")
        Debug.log(f"        Track count: {track_count}")
        Debug.log(f"        Segment count: {layout_track.header.segment_count}")
        
        br = io.BytesIO(file_data)

        # Position at the start of the tracks block
        br.seek(track_header_ptr)

        # Now read the TrackMiniHeader which follows the GANI2 header
        # Includes UnitFlags and SegmentHeaders
        track_mini_header = TrackMiniHeader.read(br, unit_count=track_count, segment_count=layout_track.header.segment_count)
        Debug.log(f"        Read TrackMiniHeader: frame_count={track_mini_header.frame_count}, param_count={track_mini_header.param_count}")

        # Calculate GANI2 animation_track data array start location
        param_end_ptr = track_header_ptr + TrackMiniHeader.BASE_SIZE + track_mini_header.param_count * 8 + track_count * 1
        # align up to 4 bytes
        gani2_trackdata_base = align_length(param_end_ptr, 4)
        Debug.log(f"        Track data base: 0x{gani2_trackdata_base:X}")

        # Process each animation_track unit

        keyframes_track_index_abs = 0
        gani_tracks: List[TrackUnitWrapper] = []

        for track_index in range(track_count):
            gani_track, keyframes_track_index_abs = self.read_track(
                br,
                track_index,
                gani2_trackdata_base,
                keyframes_track_index_abs,
                file_data,
                track_mini_header,
                layout_track
            )
            # Add the GaniTrack to the list
            gani_tracks.append(gani_track)

        Debug.log(f"      Completed reading {len(gani_tracks)} track(s)")
        return gani_tracks, track_mini_header

    # read gani2 track
    def read_track(self, br: io.BytesIO, track_index: int, gani2_trackdata_base: int, keyframes_track_index_abs: int, file_data: bytes, track_mini_header: TrackMiniHeader, layout_track: Tracks) -> Tuple[TrackUnitWrapper, int]:
        """Read a single TrackUnit (at current_offset) and return (GaniTrack, new_abs_index)."""

        keyframes_tracks: List[TrackDataBlobWrapper] = []
        track_unit: TrackUnit = layout_track.track_units[track_index]
        
        # Extract unit flags for this track
        unit_flags_int = track_mini_header.unit_flags[track_index]
        unit_flags_list = TrackUnitFlags.int_to_track_unit_flags(unit_flags_int)
        
        Debug.log(f"        Reading track {track_index}: '{track_unit.name}'")
        Debug.log(f"          Segment count: {track_unit.segment_count}")
        Debug.log(f"          Unit flags: {unit_flags_int} ({unit_flags_list})")

        # Read each segment in this animation_track unit
        for segment_index in range(track_unit.segment_count):

            # Get header
            segment_track_data: TrackData = track_unit.segments_data[segment_index]
            
            Debug.log(f"          Segment {segment_index}: type={segment_track_data.td_type.name}, component_bits={segment_track_data.component_bit_size}")
            
            # Read segment into keyframes
            br.seek(segment_track_data.data_offset)
            keyframes_track, keyframes_track_index_abs = self.read_segment(
                br,
                segment_track_data,
                unit_flags_int,
                gani2_trackdata_base,
                keyframes_track_index_abs,
                    file_data,
                    track_mini_header.frame_count,
                )
            # Keep the StrCode32 object for now, will be resolved to bone name later
            keyframes_track.name = track_unit.name
            keyframes_track.segment_index = segment_index
            keyframes_tracks.append(keyframes_track)

        # Create GaniTrack containing all segments for this track
        gani_track = TrackUnitWrapper(
            name=track_unit.name,
            segments_track_data=keyframes_tracks,
            unit_flags=unit_flags_list,
            rig_unit_type=None  # Will be filled in later when FRIG data is available
        )

        Debug.log(f"          Track complete: {len(keyframes_tracks)} segment(s)")
        return gani_track, keyframes_track_index_abs

    # read gani2 track segment
    def read_segment(
        self,
        br: io.BytesIO,
        segment_track_data: TrackData,
        unit_flags: int,
        gani2_trackdata_base: int,
        keyframes_track_index_abs: int,
        file_data: bytes,
        frame_count: int,
    ) -> Tuple[TrackDataBlobWrapper, int]:
        """Read a single animation_track segment and return (animation_track, new_abs_track_data_index)."""

        # Read segment header: the corresponding Gani2TrackData for this absolute index
        br.seek(gani2_trackdata_base + keyframes_track_index_abs * Gani2TrackData.ENTRY_SIZE)
        segment_header = Gani2TrackData.read(br)

        # Data blob begins at entry's offset
        data_blob_offset = gani2_trackdata_base + keyframes_track_index_abs * Gani2TrackData.ENTRY_SIZE + segment_header.data_offset

        # Read keyframes using TrackDataBlob
        keyframes = TrackDataBlob.read(
            file_data=file_data,
            data_offset=data_blob_offset,
            segment_type=segment_track_data.td_type,
            component_bit_size=segment_track_data.component_bit_size,
            unit_flags=unit_flags,
            frame_count=frame_count
        )

        # Create TrackDataBlob with the keyframes
        is_static = (unit_flags & TrackUnitFlags.IS_STATIC) != 0
        data_blob = TrackDataBlob.from_keyframes(
            segment_type=SegmentType(segment_track_data.td_type),
            component_bit_size=segment_track_data.component_bit_size,
            is_static=is_static,
            keyframes=keyframes
        )

        # Create a Keyframes Track object to bundle all relevant data for the importer
        keyframes_track = TrackDataBlobWrapper(
            name='', # will be added later
            segment_index=0, # will be added later
            data_blob=data_blob
        )

        return keyframes_track, keyframes_track_index_abs + 1

    def convert_tracks_to_gani_tracks(self, tracks: Tracks) -> List[TrackUnitWrapper]:
        """Convert a Tracks structure (with populated data_blobs) to TrackUnitWrapper format.
        
        This extracts the keyframe data from TrackData.data_blob and creates
        the TrackUnitWrapper/TrackDataBlobWrapper structure expected by the importer.
        
        Args:
            tracks: Tracks object with TrackData.data_blob populated
            
        Returns:
            List of TrackUnitWrapper objects
        """
        gani_tracks: List[TrackUnitWrapper] = []
        
        for track_unit in tracks.track_units:
            keyframes_tracks: List[TrackDataBlobWrapper] = []
            
            # Extract unit flags for this track
            unit_flags_int = track_unit.unit_flags
            unit_flags_list = TrackUnitFlags.int_to_track_unit_flags(unit_flags_int)
            
            # Convert each segment's data_blob to a TrackDataBlobWrapper
            for segment_index, track_data in enumerate(track_unit.segments_data):
                # Get keyframes from data_blob (may be None or empty for layout tracks)
                keyframes = track_data.data_blob if track_data.data_blob is not None else []
                
                # Create TrackDataBlob
                is_static = (unit_flags_int & TrackUnitFlags.IS_STATIC) != 0
                data_blob = TrackDataBlob.from_keyframes(
                    segment_type=SegmentType(track_data.td_type),
                    component_bit_size=track_data.component_bit_size,
                    is_static=is_static,
                    keyframes=keyframes
                )
                
                # Create TrackDataBlobWrapper for this segment
                keyframes_track = TrackDataBlobWrapper(
                    name=track_unit.name,  # Will be resolved to string later
                    segment_index=segment_index,
                    data_blob=data_blob
                )
                keyframes_tracks.append(keyframes_track)
            
            # Create GaniTrack containing all segments for this track
            gani_track = TrackUnitWrapper(
                name=track_unit.name,
                segments_track_data=keyframes_tracks,
                unit_flags=unit_flags_list,
                rig_unit_type=None  # Will be filled in later for bone tracks
            )
            
            gani_tracks.append(gani_track)
        
        return gani_tracks

    def read_all_motion_tracks(self, file_data: bytes, motion_tracks_ptr: int) -> List[TrackUnit]:
        """Read motion point tracks block and return a list of TrackUnit objects.

        The motion tracks block begins with a TrackHeader followed by TrackUnit entries.
        motion_tracks_ptr is the absolute byte offset where the TrackHeader starts.
        """
        br = io.BytesIO(file_data)
        br.seek(motion_tracks_ptr)

        # Read the TrackHeader
        track_header = TrackHeader.read(br)

        units: List[TrackUnit] = []
        for _ in range(track_header.unit_count):
            units.append(TrackUnit.read(br))

        return units


