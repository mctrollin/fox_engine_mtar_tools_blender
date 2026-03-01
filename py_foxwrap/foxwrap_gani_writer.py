"""
Writer for old-format (FoxData) GANI animation files.

Old-format GANI files use a FoxData node tree as their container:

    ROOT (container) → MOTION (container) → {SKL_LIST?, MTP?, UNIT, MTP_PARENT_LIST?, MTP_LIST?, EVP?}

The TrackHeader/TrackUnit/TrackData binary payload format is identical between
old-format (FoxData) and new-format (GANI2) — only the container structure differs.

Canonical MOTION child order (matching observed files):
    1. SKL_LIST        (optional) — bone name hash list
    2. MTP             (optional) — motion point TrackHeader payload
    3. UNIT            (mandatory) — bone animation TrackHeader payload
    4. MTP_PARENT_LIST (optional) — motion point parent name hash list
    5. MTP_LIST        (optional) — motion point name hash list
    6. EVP             (optional) — motion events EvpHeader payload

FoxDataNode field byte offsets (within the 48-byte node body):
    offset  0: name_hash           (uint32)
    offset  4: name_string_offset  (uint32)
    offset  8: flags               (uint32)  — FoxDataNodeType
    offset 12: data_offset         (int32, signed, relative to node start)
    offset 16: data_size           (uint32)
    offset 20: parent_node_offset  (int32, signed, relative to node start)
    offset 24: child_node_offset   (int32, signed, relative to node start)
    offset 28: previous_node_offset(int32, signed, relative to node start)
    offset 32: next_node_offset    (int32, signed, relative to node start)
    offset 36: parameters_offset   (int32)
    offset 40: [8 bytes FAlign(16) padding]
    SIZE = 48

All inter-node offsets are relative to the *start* of the source node.
A negative offset means the target is *before* the source in the buffer.
"""

import io
import struct
from typing import Optional, List

from ..py_utilities.utilities_logging import Debug
from ..py_utilities.utilities_binary_write import align_buffer
from ..py_utilities.utilities_hashing_cityhash import strcode32

from ..py_fox.fox_foxdata_types import FoxDataHeader, FoxDataNode, FoxDataNodeType
from ..py_fox.fox_gani_types import TrackHeader, TrackUnit, EvpHeader, TrackData, TrackUnitFlags
from ..py_fox.fox_gani_constants import (
    FOXDATA_HASH_ROOT,
    FOXDATA_HASH_MOTION,
    FOXDATA_HASH_UNIT,
    FOXDATA_HASH_MTP,
    FOXDATA_HASH_EVP,
    FOXDATA_HASH_SKL_LIST,
    FOXDATA_HASH_MTP_LIST,
    FOXDATA_HASH_MTP_PARENT_LIST,
)

from .foxwrap_misc import TrackUnitWrapper, Tracks

# FoxDataHeader.flags bit 0: set when no SKL_LIST node is written
_GANI_HEADER_FLAGS_NO_SKEL_LIST: int = 1

# FoxDataNode field byte offsets within the 48-byte node
_NODE_OFF_DATA_SIZE           = 16
_NODE_OFF_PARENT_NODE_OFFSET  = 20
_NODE_OFF_CHILD_NODE_OFFSET   = 24
_NODE_OFF_PREV_NODE_OFFSET    = 28
_NODE_OFF_NEXT_NODE_OFFSET    = 32


class GaniWriter:
    """Writer for old-format (FoxData) GANI animation data.

    Emits:
        FoxDataHeader (32 bytes)
        ROOT node     (48 bytes, container — no payload)
          MOTION node (48 bytes, container — no payload)
            [SKL_LIST node + StringData payload]  (optional)
            [MTP node     + TrackHeader payload]   (optional)
            UNIT node     + TrackHeader payload    (mandatory)
            [MTP_PARENT_LIST node + StringData]    (optional)
            [MTP_LIST node + StringData payload]   (optional)
            [EVP node     + EvpHeader payload]     (optional)
    """

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def write_gani_to_buffer(
        self,
        buffer: io.BytesIO,
        gani_tracks: List[TrackUnitWrapper],
        frame_count: int,
        frame_rate: int = 30,
        motion_point_tracks: Optional[List[TrackUnitWrapper]] = None,
        motion_events: Optional[EvpHeader] = None,
        foxdata_version: int = 201304220,
        skeleton_list: Optional[List] = None,
        motion_point_list: Optional[List] = None,
        motion_point_parent_list: Optional[List] = None,
    ) -> None:
        """Write a FoxData GANI blob to a seekable BytesIO buffer.

        If ``skeleton_list`` is ``None``, the bone track names are used to derive
        a SKL_LIST automatically (FoxDataHeader.flags = 0, meaning SKL_LIST *is*
        present).  Pass an explicit empty list ``[]`` to suppress SKL_LIST
        entirely (flags = 1 = GANI_HEADER_FLAGS_NO_SKEL_LIST).

        Args:
            buffer:                   BytesIO buffer to write to.
            gani_tracks:              Bone animation tracks (mandatory, non-empty).
            frame_count:              Total frame count.
            frame_rate:               Frame rate (default 30).
            motion_point_tracks:      Optional motion point tracks (MTP node).
            motion_events:            Optional motion events (EVP node).
            foxdata_version:          FoxData version field (default 201304220).
            skeleton_list:            Names / hashes for SKL_LIST node.
                                      ``None`` -> derive from gani_tracks.
                                      ``[]``   -> omit SKL_LIST (header flags = 1).
            motion_point_list:        Names / hashes for MTP_LIST node.
                                      ``None`` -> derive from motion_point_tracks (if present).
                                      ``[]``   -> omit MTP_LIST.
            motion_point_parent_list: Names / hashes for MTP_PARENT_LIST node.
                                      ``None`` -> omit MTP_PARENT_LIST.

        Raises:
            ValueError: If ``gani_tracks`` is empty.
        """
        if not gani_tracks:
            raise ValueError("gani_tracks cannot be empty")

        # Build UNIT (bone) and optional MTP Tracks structures
        unit_tracks = self._build_tracks_from_wrappers(gani_tracks, frame_count, frame_rate)
        mtp_tracks = (
            self._build_tracks_from_wrappers(motion_point_tracks, frame_count, frame_rate)
            if motion_point_tracks
            else None
        )

        # Resolve effective string lists ──────────────────────────────────────
        # SKL_LIST: None -> derive from gani_tracks; [] -> suppress (None means omit)
        if skeleton_list is None:
            effective_skl_list: Optional[List] = [w.name for w in gani_tracks]
        elif len(skeleton_list) == 0:
            effective_skl_list = None  # explicitly suppress
        else:
            effective_skl_list = skeleton_list

        # MTP_LIST: None -> derive from motion_point_tracks if present; [] -> suppress
        if motion_point_list is None:
            effective_mtp_list: Optional[List] = (
                [w.name for w in motion_point_tracks] if motion_point_tracks else None
            )
        elif len(motion_point_list) == 0:
            effective_mtp_list = None
        else:
            effective_mtp_list = motion_point_list

        # MTP_PARENT_LIST: always explicit, no auto-derive
        effective_mtp_parent_list: Optional[List] = (
            motion_point_parent_list if motion_point_parent_list else None
        )

        self._write_foxdata_gani(
            buffer,
            unit_tracks,
            mtp_tracks,
            motion_events,
            foxdata_version,
            skeleton_list=effective_skl_list,
            motion_point_list=effective_mtp_list,
            motion_point_parent_list=effective_mtp_parent_list,
        )

        Debug.log(
            f"Wrote old-format GANI: {len(gani_tracks)} bone track(s), "
            f"{len(motion_point_tracks) if motion_point_tracks else 0} MTP track(s), "
            f"frame_count={frame_count}, foxdata_version={foxdata_version}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Internal — FoxData tree writer
    # ─────────────────────────────────────────────────────────────────────────

    def _write_foxdata_gani(
        self,
        buffer: io.BytesIO,
        unit_tracks: Tracks,
        mtp_tracks: Optional[Tracks],
        motion_events: Optional[EvpHeader],
        foxdata_version: int,
        skeleton_list: Optional[List] = None,
        motion_point_list: Optional[List] = None,
        motion_point_parent_list: Optional[List] = None,
    ) -> None:
        """Write the complete FoxData GANI structure.

        Tree shape::

            FoxDataHeader
            ROOT  node  (container)
              MOTION node (container)
                [SKL_LIST  node + StringData]   optional
                [MTP       node + TrackHeader]  optional
                UNIT       node + TrackHeader   mandatory
                [MTP_PARENT_LIST + StringData]  optional
                [MTP_LIST  node + StringData]   optional
                [EVP       node + EvpHeader]    optional
        """
        start_pos = buffer.tell()

        # ── FoxDataHeader placeholder (back-filled at the end) ──────────────
        header_pos = start_pos
        buffer.seek(header_pos + FoxDataHeader.SIZE)

        # ── ROOT node (container, no payload) ───────────────────────────────
        root_node_pos = buffer.tell()
        self._write_placeholder_node(buffer, FOXDATA_HASH_ROOT, FoxDataNodeType.TRACKS)

        # ── MOTION node (container, no payload) ─────────────────────────────
        motion_node_pos = buffer.tell()
        self._write_placeholder_node(buffer, FOXDATA_HASH_MOTION, FoxDataNodeType.TRACKS)

        # ── MOTION children in canonical order ──────────────────────────────
        # Each entry: (debug_name: str, node_pos: int, payload_end: int)
        children: List[tuple] = []

        # 1. SKL_LIST (optional, before MTP and UNIT)
        if skeleton_list is not None:
            pos, payload_end = self._write_stringdata_node(buffer, FOXDATA_HASH_SKL_LIST, skeleton_list)
            children.append(("SKL_LIST", pos, payload_end))

        # 2. MTP (optional, before UNIT)
        if mtp_tracks is not None:
            pos, _, payload_end = self._write_tracks_node(buffer, FOXDATA_HASH_MTP, mtp_tracks)
            children.append(("MTP", pos, payload_end))

        # 3. UNIT (mandatory)
        unit_pos, _, unit_payload_end = self._write_tracks_node(buffer, FOXDATA_HASH_UNIT, unit_tracks)
        children.append(("UNIT", unit_pos, unit_payload_end))

        # 4. MTP_PARENT_LIST (optional, after UNIT)
        if motion_point_parent_list is not None:
            pos, payload_end = self._write_stringdata_node(
                buffer, FOXDATA_HASH_MTP_PARENT_LIST, motion_point_parent_list
            )
            children.append(("MTP_PARENT_LIST", pos, payload_end))

        # 5. MTP_LIST (optional, after UNIT)
        if motion_point_list is not None:
            pos, payload_end = self._write_stringdata_node(
                buffer, FOXDATA_HASH_MTP_LIST, motion_point_list
            )
            children.append(("MTP_LIST", pos, payload_end))

        # 6. EVP (optional, always last)
        if motion_events is not None:
            pos, _, payload_end = self._write_evp_node(buffer, motion_events)
            children.append(("EVP", pos, payload_end))

        # ── Compute final file size ──────────────────────────────────────────
        file_end_pos = buffer.tell()
        file_size = file_end_pos - start_pos

        # ── M7: Back-fill FoxDataHeader ──────────────────────────────────────
        # GANI_HEADER_FLAGS_NO_SKEL_LIST (bit 0) is set when no SKL_LIST is written
        header_flags = 0 if skeleton_list is not None else _GANI_HEADER_FLAGS_NO_SKEL_LIST
        buffer.seek(header_pos)
        FoxDataHeader(
            version=foxdata_version,
            nodes_offset=FoxDataHeader.SIZE,
            file_size=file_size,
            name_hash=0,
            name_string_offset=0,
            flags=header_flags,
        ).write(buffer)

        # ── Back-fill ROOT node ──────────────────────────────────────────────
        # child_node_offset: ROOT -> MOTION  (positive, relative to ROOT start)
        self._backfill_int(
            buffer,
            root_node_pos + _NODE_OFF_CHILD_NODE_OFFSET,
            motion_node_pos - root_node_pos,
        )

        # ── Back-fill MOTION node ────────────────────────────────────────────
        # parent_node_offset: MOTION -> ROOT  (negative)
        self._backfill_int(
            buffer,
            motion_node_pos + _NODE_OFF_PARENT_NODE_OFFSET,
            root_node_pos - motion_node_pos,
        )
        # child_node_offset: MOTION -> first child
        if children:
            first_child_pos = children[0][1]
            self._backfill_int(
                buffer,
                motion_node_pos + _NODE_OFF_CHILD_NODE_OFFSET,
                first_child_pos - motion_node_pos,
            )

        # ── M6: Back-fill each MOTION child ──────────────────────────────────
        for i, (_, child_pos, payload_end) in enumerate(children):
            # parent_node_offset -> MOTION (negative)
            self._backfill_int(
                buffer,
                child_pos + _NODE_OFF_PARENT_NODE_OFFSET,
                motion_node_pos - child_pos,
            )

            # previous_node_offset -> previous sibling (negative), 0 if first
            if i > 0:
                prev_pos = children[i - 1][1]
                self._backfill_int(
                    buffer,
                    child_pos + _NODE_OFF_PREV_NODE_OFFSET,
                    prev_pos - child_pos,
                )
            # else: placeholder already 0

            # next_node_offset -> next sibling (positive), 0 if last
            if i < len(children) - 1:
                next_pos = children[i + 1][1]
                self._backfill_int(
                    buffer,
                    child_pos + _NODE_OFF_NEXT_NODE_OFFSET,
                    next_pos - child_pos,
                )
            # else: placeholder already 0

            # data_size = payload bytes written after the node header
            data_size = payload_end - (child_pos + FoxDataNode.SIZE)
            self._backfill_uint(buffer, child_pos + _NODE_OFF_DATA_SIZE, data_size)

        buffer.seek(file_end_pos)

    # ─────────────────────────────────────────────────────────────────────────
    # Node writing helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _write_placeholder_node(
        self,
        buffer: io.BytesIO,
        name_hash: int,
        flags: int = FoxDataNodeType.STRINGDATA,
    ) -> int:
        """Write a FoxDataNode with all offsets/sizes zeroed and return its position.

        Used for container nodes (ROOT, MOTION) that carry no payload.  All
        back-fill fields (child/parent/prev/next offsets, data_size) are written
        as zero; the caller fills them in via ``_backfill_*``.
        """
        node_pos = buffer.tell()
        FoxDataNode(
            name_hash=name_hash,
            name_string_offset=0,
            flags=flags,
            data_offset=0,   # container: no payload
            data_size=0,
            parent_node_offset=0,
            child_node_offset=0,
            previous_node_offset=0,
            next_node_offset=0,
            parameters_offset=0,
        ).write(buffer)
        return node_pos

    def _write_tracks_node(
        self,
        buffer: io.BytesIO,
        name_hash: int,
        tracks: Tracks,
    ) -> tuple:
        """Write a FoxDataNode with a TrackHeader payload (UNIT or MTP).

        Returns:
            ``(node_pos, payload_start, payload_end)``
        """
        node_pos = buffer.tell()
        payload_start = node_pos + FoxDataNode.SIZE

        FoxDataNode(
            name_hash=name_hash,
            name_string_offset=0,
            flags=FoxDataNodeType.TRACKS,
            data_offset=FoxDataNode.SIZE,
            data_size=0,       # back-filled by caller
            parent_node_offset=0,
            child_node_offset=0,
            previous_node_offset=0,
            next_node_offset=0,
            parameters_offset=0,
        ).write(buffer)

        tracks.write(buffer, write_data_blobs=True)
        payload_end = buffer.tell()
        align_buffer(buffer, 16)

        return (node_pos, payload_start, payload_end)

    def _write_stringdata_node(
        self,
        buffer: io.BytesIO,
        name_hash: int,
        names: List,
    ) -> tuple:
        """Write a FoxDataNode with a StringData payload (SKL_LIST, MTP_LIST, etc.).

        StringData payload layout (from ``anim_common.bt``)::

            uint32 EntryCount
            EntryCount x { uint32 hash; uint32 StringOffset (= 0); }

        Each name in ``names`` may be a string or an integer hash.  Strings are
        converted via ``strcode32``; integers are used directly.

        Returns:
            ``(node_pos, payload_end)``
        """
        node_pos = buffer.tell()

        FoxDataNode(
            name_hash=name_hash,
            name_string_offset=0,
            flags=FoxDataNodeType.STRINGDATA,
            data_offset=FoxDataNode.SIZE,
            data_size=0,       # back-filled by caller
            parent_node_offset=0,
            child_node_offset=0,
            previous_node_offset=0,
            next_node_offset=0,
            parameters_offset=0,
        ).write(buffer)

        # Write StringData payload
        buffer.write(struct.pack('<I', len(names)))
        for name in names:
            if isinstance(name, int):
                hash_val = name
            else:
                s = str(name)
                try:
                    # Handles "0x..." hex literals stored by the reader as fallbacks
                    hash_val = int(s, 0)
                except ValueError:
                    hash_val = strcode32(s, remove_extension=False)
            # FoxDataName: uint32 hash + uint32 StringOffset (0 = no inline string)
            buffer.write(struct.pack('<II', hash_val, 0))

        payload_end = buffer.tell()
        align_buffer(buffer, 16)

        return (node_pos, payload_end)

    def _write_evp_node(
        self,
        buffer: io.BytesIO,
        motion_events: EvpHeader,
    ) -> tuple:
        """Write a FoxDataNode with an EvpHeader payload (EVP).

        Returns:
            ``(node_pos, payload_start, payload_end)``
        """
        node_pos = buffer.tell()
        payload_start = node_pos + FoxDataNode.SIZE

        FoxDataNode(
            name_hash=FOXDATA_HASH_EVP,
            name_string_offset=0,
            flags=FoxDataNodeType.EVENTS,
            data_offset=FoxDataNode.SIZE,
            data_size=0,       # back-filled by caller
            parent_node_offset=0,
            child_node_offset=0,
            previous_node_offset=0,
            next_node_offset=0,
            parameters_offset=0,
        ).write(buffer)

        motion_events.write(buffer)
        payload_end = buffer.tell()
        align_buffer(buffer, 16)

        return (node_pos, payload_start, payload_end)

    # ─────────────────────────────────────────────────────────────────────────
    # Track structure builder
    # ─────────────────────────────────────────────────────────────────────────

    def _build_tracks_from_wrappers(
        self,
        track_wrappers: List[TrackUnitWrapper],
        frame_count: int,
        frame_rate: int,
    ) -> Tracks:
        """Convert a list of ``TrackUnitWrapper`` objects to a ``Tracks`` structure.

        Converts ``TrackDataBlobWrapper`` segments to ``TrackData`` instances with
        keyframe blobs.  The resulting ``Tracks`` object is ready to be serialised
        by ``Tracks.write(write_data_blobs=True)``.
        """
        track_units: List[TrackUnit] = []
        for wrapper in track_wrappers:
            track_data_list: List[TrackData] = []
            if wrapper.segments_track_data:
                for blob_wrapper in wrapper.segments_track_data:
                    track_data_list.append(
                        TrackData(
                            data_offset=0,       # calculated by Tracks.write()
                            ms_id=0,
                            td_type=blob_wrapper.data_blob.type,
                            next_entry_offset=0,
                            component_bit_size=blob_wrapper.data_blob.component_bit_size,
                            data_blob=blob_wrapper.data_blob.keyframes,
                        )
                    )

            unit_flags_int = (
                TrackUnitFlags.track_unit_flags_to_int(wrapper.unit_flags)
                if wrapper.unit_flags
                else 0
            )
            track_units.append(
                TrackUnit(
                    name=wrapper.name,
                    segment_count=len(track_data_list),
                    unit_flags=unit_flags_int,
                    padding=0,
                    segments_data=track_data_list,
                )
            )

        header = TrackHeader(
            unit_count=len(track_units),
            segment_count=sum(u.segment_count for u in track_units),
            t_id=0,
            unknown_a=0,
            unknown_b=1,  # Must be 1 per binary template assertion
            frame_count=frame_count,
            frame_rate=frame_rate,
            unit_offsets=[],  # Calculated by Tracks.write()
        )
        return Tracks(header=header, track_units=track_units)

    # ─────────────────────────────────────────────────────────────────────────
    # Back-fill helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _backfill_int(self, buffer: io.BytesIO, abs_offset: int, value: int) -> None:
        """Write a signed int32 at ``abs_offset`` without disturbing the stream position."""
        saved = buffer.tell()
        buffer.seek(abs_offset)
        buffer.write(struct.pack('<i', value))
        buffer.seek(saved)

    def _backfill_uint(self, buffer: io.BytesIO, abs_offset: int, value: int) -> None:
        """Write an unsigned int32 at ``abs_offset`` without disturbing the stream position."""
        saved = buffer.tell()
        buffer.seek(abs_offset)
        buffer.write(struct.pack('<I', value))
        buffer.seek(saved)
