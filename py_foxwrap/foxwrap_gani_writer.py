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
from typing import Optional, List, Tuple, Union, Dict

from ..py_utilities.utilities_logging import Debug
from ..py_utilities.utilities_binary_write import align_buffer
from ..py_utilities.utilities_hashing import is_hash_string, parse_hash_string
from ..py_utilities.utilities_hashing_cityhash import strcode32

from ..py_fox.fox_foxdata_types import FoxDataHeader, FoxDataNode, FoxDataNodeType, FoxDataParamType
from ..py_fox.fox_gani_types import TrackHeader, TrackUnit, EvpHeader, TrackData, TrackUnitFlags
from ..py_fox.fox_misc_types import StrCode32
from ..py_fox.fox_gani_constants import (
    FOXDATA_HASH_ROOT,
    FOXDATA_HASH_MOTION,
    FOXDATA_HASH_UNIT,
    FOXDATA_HASH_MTP,
    FOXDATA_HASH_EVP,
    FOXDATA_HASH_SKL_LIST,
    FOXDATA_HASH_MTP_LIST,
    FOXDATA_HASH_MTP_PARENT_LIST,
    FOXDATA_HASH_SHADER,
    FOXDATA_HASH_PARAM_SLOPE_ANGLE,
    FOXDATA_HASH_PARAM_SLOPE_DIR,
)

from .foxwrap_misc import TrackUnitWrapper, Tracks, is_root_motion_track

# FoxDataHeader.flags bit 0: set when no SKL_LIST node is written
_GANI_HEADER_FLAGS_NO_SKEL_LIST: int = 1

# FoxDataNode field byte offsets within the 48-byte node
_NODE_OFF_DATA_SIZE           = 16
_NODE_OFF_PARENT_NODE_OFFSET  = 20
_NODE_OFF_CHILD_NODE_OFFSET   = 24
_NODE_OFF_PREV_NODE_OFFSET    = 28
_NODE_OFF_NEXT_NODE_OFFSET    = 32
_NODE_OFF_PARAMETERS_OFFSET   = 36

# Inline name string area appended after ROOT and MOTION node bodies (16 bytes, align-16)
_CONTAINER_NAME_STRING_SIZE = 16  # null-terminated name + zero padding to 16-byte boundary

# FoxDataNodeParameter entry sizes (from bt template)
# FLOAT (type=2) and UINT (type=0): 16 bytes each.
# STRING (type=1): 20-byte base (Value is a FoxDataName = 8 bytes); inline strings
# append null-terminated UTF-8 bytes followed by padding to a 16-byte boundary.
_FLOAT_PARAM_ENTRY_SIZE  = 16
_STRING_PARAM_ENTRY_SIZE = 20  # base size; inline entries are larger
_MOTION_PARAM_COUNT      = 2   # SLOPE_ANGLE then SLOPE_DIR (default MOTION params)


class GaniWriter:
    """Writer for old-format (FoxData) GANI animation data.

    Emits:
        FoxDataHeader (32 bytes)
        ROOT node     (48 bytes body + 16 bytes inline name string = 64 bytes total)
          MOTION node (48 bytes body + 16 bytes inline name string + 2×16 bytes params = 80 bytes total)
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
        foxdata_version: int = 201106130,  # canonical old-format version (matches real MGS5 files)
        skeleton_list: Optional[List] = None,
        motion_point_list: Optional[List] = None,
        motion_point_parent_list: Optional[List] = None,
        shader_tracks: Optional[List[tuple]] = None,
        node_params: Optional[Dict[str, List[Tuple[int, Union[float, str, int]]]]] = None,
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
            shader_tracks:            Optional shader node property data.  Each element
                                      is a ``(property_name: str, tracks: List[TrackUnitWrapper])``
                                      tuple representing one SHADER child property.
                                      ``None`` -> no SHADER node written.
            node_params:              Unified node params dict keyed by node path (e.g.
                                      ``"MOTION"``, ``"SHADER/TENSION_CHEEKL"``).
                                      ``None`` -> MOTION node defaults to SLOPE_ANGLE=0.0, SLOPE_DIR=0.0.

        Raises:
            ValueError: If ``gani_tracks`` is empty.
        """
        if not gani_tracks:
            raise ValueError("gani_tracks cannot be empty")

        # Sort UNIT track order to match SKL_LIST -------------------------------------------
        # _write_stringdata_node sorts the SKL_LIST alphabetically, so the UNIT
        # node must carry the same order to keep track indices consistent.
        #
        # Tracks whose names are unresolved hash strings are *not* in the
        # SKL_LIST (the canonical example is the root motion track, whose name
        # hash was never resolved to a string).  Those tracks are placed first,
        # preserving their original relative order.  Real-name tracks are sorted
        # alphabetically to align with the SKL_LIST sort.
        non_skl_tracks = [w for w in gani_tracks if is_hash_string(str(w.name))]
        skl_tracks      = [w for w in gani_tracks if not is_hash_string(str(w.name))]

        # Validate: every non-SKL track placed first should be a root motion
        # track (all-DIFF segments).  Warn loudly if that assumption is violated.
        for track in non_skl_tracks:
            if not is_root_motion_track(track):
                Debug.log_warning(
                    f"write_gani_to_buffer: Non-SKL track '{track.name}' is placed "
                    f"before SKL tracks but does not appear to be a root motion track "
                    f"(expected exclusively DIFF segment types). "
                    f"UNIT node order may be incorrect."
                )

        skl_tracks.sort(key=lambda w: str(w.name))
        gani_tracks = non_skl_tracks + skl_tracks

        # Build UNIT (bone) and optional MTP Tracks structures -------------------------------------------
        unit_tracks = self._build_tracks_from_wrappers(gani_tracks, frame_count, frame_rate)
        mtp_tracks = (self._build_tracks_from_wrappers(motion_point_tracks, frame_count, frame_rate) if motion_point_tracks else None)

        # Build SHADER Tracks structures (old-format only: SHADER is a ROOT sibling of MOTION)
        # Each element is (property_name, Tracks) ready for _write_tracks_node().
        # Accepts 2-tuples (prop_name, prop_tracks) or 3-tuples
        # (prop_name, prop_tracks, header_overrides) where header_overrides is optional.
        # Per-property params are supplied via node_params (e.g. "SHADER/TENSION_CHEEKL").
        shader_tracks_built = None
        if shader_tracks:
            shader_tracks_built = []
            for item in shader_tracks:
                prop_name, prop_tracks = item[0], item[1]
                if not prop_tracks:
                    continue
                hdr: dict = item[2] if len(item) >= 3 and item[2] else {}
                shader_tracks_built.append((
                    prop_name,
                    self._build_tracks_from_wrappers(
                        prop_tracks,
                        frame_count=hdr.get('frame_count', frame_count),
                        frame_rate=hdr.get('frame_rate', frame_rate),
                        t_id=hdr.get('t_id', 0),
                        unknown_a=hdr.get('unknown_a', 0),
                        unknown_b=hdr.get('unknown_b', 1),
                    )
                ))
            if not shader_tracks_built:
                shader_tracks_built = None

        # Resolve effective string lists -------------------------------------------
        # TODO: this is not easy to understand len=0 vs None. Evaluate better solutions

        # SKL_LIST: None -> derive from gani_tracks; [] -> suppress (None means omit)
        # Only real-name (SKL) tracks are included in the SKL_LIST; hash-string
        # tracks (root motion etc.) are deliberately excluded.
        if skeleton_list is None:
            effective_skl_list: Optional[List] = [w.name for w in skl_tracks]
        elif len(skeleton_list) == 0:
            effective_skl_list = None  # explicitly suppress
        else:
            effective_skl_list = skeleton_list

        # MTP_LIST: None -> derive from motion_point_tracks if present; [] -> suppress
        if motion_point_list is None:
            effective_mtp_list: Optional[List] = ([w.name for w in motion_point_tracks] if motion_point_tracks else None)
        elif len(motion_point_list) == 0:
            effective_mtp_list = None
        else:
            effective_mtp_list = motion_point_list

        # MTP_PARENT_LIST: always explicit, no auto-derive
        effective_mtp_parent_list: Optional[List] = (motion_point_parent_list if motion_point_parent_list else None)

        # Write -------------------------------------------
        self._write_foxdata_gani(
            buffer,
            unit_tracks,
            mtp_tracks,
            motion_events,
            foxdata_version,
            skeleton_list=effective_skl_list,
            motion_point_list=effective_mtp_list,
            motion_point_parent_list=effective_mtp_parent_list,
            shader_tracks=shader_tracks_built,
            node_params=node_params,
        )

        Debug.log(
            f"Wrote old-format GANI: {len(gani_tracks)} bone track(s), "
            f"{len(motion_point_tracks) if motion_point_tracks else 0} MTP track(s), "
            f"{len(shader_tracks) if shader_tracks else 0} shader property node(s), "
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
        shader_tracks: Optional[List[tuple]] = None,
        node_params: Optional[Dict[str, List[Tuple[int, Union[float, str, int]]]]] = None,
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
              [SHADER node (container)]          optional — sibling of MOTION
                [TENSION_CHEEKL + TrackHeader]  optional
                [TENSION_CHEEKR + TrackHeader]  optional
                [TENSION_NECK   + TrackHeader]  optional

        ``shader_tracks``, if given, is a list of ``(property_name: str, tracks: Tracks)``
        tuples where ``tracks`` is a pre-built :class:`Tracks` structure (built by
        :meth:`_build_tracks_from_wrappers` before this call).
        """
        start_pos = buffer.tell()

        # ── FoxDataHeader placeholder (back-filled at the end) ──────────────
        header_pos = start_pos
        buffer.seek(header_pos + FoxDataHeader.SIZE)

        # ── ROOT node (container, no payload) ───────────────────────────────
        root_node_pos = buffer.tell()
        self._write_placeholder_node(buffer, FOXDATA_HASH_ROOT, flags=0, name_string="ROOT")

        # ── MOTION node (container, no payload) ───────────────────────────────────
        motion_node_pos = buffer.tell()
        self._write_placeholder_node(buffer, FOXDATA_HASH_MOTION, flags=0, name_string="MOTION")

        # ── MOTION parameters: SLOPE_ANGLE and SLOPE_DIR ─────────────────────────────
        # parameters_offset is relative to MOTION node start; name string area is 16 bytes
        motion_params_offset = buffer.tell() - motion_node_pos
        self._write_node_parameters(buffer, (node_params or {}).get("MOTION"))

        # Backfill MOTION.parameters_offset
        self._backfill_int(
            buffer,
            motion_node_pos + _NODE_OFF_PARAMETERS_OFFSET,
            motion_params_offset,
        )

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

        # ── ROOT-level children: MOTION + optional SHADER sibling ───────────
        # Collect ROOT children in order (MOTION first, then SHADER if present)
        root_children: List[tuple] = []  # (debug_name, node_pos)
        root_children.append(("MOTION", motion_node_pos))

        shader_node_pos: Optional[int] = None
        if shader_tracks:
            shader_node_pos, shader_children = self._write_shader_node(
                buffer, shader_tracks, node_params=node_params
            )
            root_children.append(("SHADER", shader_node_pos))
        else:
            shader_children = []

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

        # ── Back-fill ROOT-level siblings (MOTION ↔ SHADER) ──────────────────
        for i, (_, rc_pos) in enumerate(root_children):
            # parent_node_offset → ROOT (negative)
            self._backfill_int(
                buffer,
                rc_pos + _NODE_OFF_PARENT_NODE_OFFSET,
                root_node_pos - rc_pos,
            )
            # next_node_offset → next sibling (positive), 0 if last
            if i < len(root_children) - 1:
                next_rc_pos = root_children[i + 1][1]
                self._backfill_int(
                    buffer,
                    rc_pos + _NODE_OFF_NEXT_NODE_OFFSET,
                    next_rc_pos - rc_pos,
                )
            # prev_node_offset → previous sibling (negative), 0 if first
            if i > 0:
                prev_rc_pos = root_children[i - 1][1]
                self._backfill_int(
                    buffer,
                    rc_pos + _NODE_OFF_PREV_NODE_OFFSET,
                    prev_rc_pos - rc_pos,
                )

        # ── Back-fill MOTION node ────────────────────────────────────────────
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
        # Trailing alignment is written AFTER file_size is captured so that
        # FoxDataHeader.file_size excludes the padding bytes.
        align_buffer(buffer, 16)

    # ─────────────────────────────────────────────────────────────────────────
    # Node writing helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _write_shader_node(
        self,
        buffer: io.BytesIO,
        shader_tracks: List[tuple],
        node_params: Optional[Dict[str, List[Tuple[int, Union[float, str, int]]]]] = None,
    ) -> tuple:
        """Write the SHADER container node and its property children.

        The SHADER node is a ROOT-level sibling of MOTION.  Each element of
        ``shader_tracks`` is a ``(property_name: str, tracks: Tracks)`` pair
        that produces one TRACKS child node (identical in format to an MTP node).

        Args:
            buffer:        Seekable BytesIO buffer.
            shader_tracks: List of ``(property_name, tracks)`` pairs.  ``tracks``
                           must be a pre-built :class:`Tracks` structure.
            node_params:   Unified node params dict keyed by node path (e.g.
                           ``"SHADER/TENSION_CHEEKL"``). Per-property params are
                           looked up via ``node_params.get(f"SHADER/{prop_name}")``).

        Returns:
            ``(shader_node_pos, property_children)`` where ``property_children``
            is a list of ``(debug_name, node_pos, payload_end)`` tuples used by
            the caller for validation but not for further back-filling (all
            internal SHADER back-fills are done here).
        """
        shader_node_pos = buffer.tell()
        # SHADER is a container node (like ROOT / MOTION) with an inline name string
        self._write_placeholder_node(buffer, FOXDATA_HASH_SHADER, flags=0, name_string="SHADER")

        # Write SHADER container parameters if present (e.g., TARGET_NAME)
        shader_container_params = (node_params or {}).get("SHADER")
        if shader_container_params:
            params_offset = buffer.tell() - shader_node_pos
            self._write_node_parameters(buffer, shader_container_params)
            self._backfill_int(buffer, shader_node_pos + _NODE_OFF_PARAMETERS_OFFSET, params_offset)

        # Write each property as a TRACKS child node
        property_children: List[tuple] = []  # (prop_name, node_pos, payload_end)
        for i, (prop_name, tracks) in enumerate(shader_tracks):
            # Compute hash for this property node
            prop_name_str = str(prop_name)
            if prop_name_str.startswith("shader_prop."):
                # Hash fallback created by GaniReader: "shader_prop.{decimal_hash}"
                try:
                    prop_hash = int(prop_name_str[len("shader_prop."):])
                except ValueError:
                    prop_hash = strcode32(prop_name_str)
                    Debug.log_warning(
                        f"  write_shader_node: Could not parse hash from '{prop_name_str}', "
                        f"using strcode32 as fallback (hash={prop_hash})"
                    )
            elif is_hash_string(prop_name_str):
                prop_hash = parse_hash_string(prop_name_str)
            else:
                prop_hash = strcode32(prop_name_str)

            pos, _, payload_end = self._write_tracks_node(buffer, prop_hash, tracks)
            
            # Write per-property parameters if present, and backfill parameters_offset
            child_params = (node_params or {}).get(f"SHADER/{prop_name_str}")
            if child_params:
                params_offset = buffer.tell() - pos
                self._write_node_parameters(buffer, child_params)
                self._backfill_int(buffer, pos + _NODE_OFF_PARAMETERS_OFFSET, params_offset)
                Debug.log(f"  Shader property '{prop_name_str}': wrote {len(child_params)} param(s)")
            
            property_children.append((prop_name_str, pos, payload_end))

        if not property_children:
            Debug.log_warning("  write_shader_node: No property children written for SHADER node")
            return shader_node_pos, property_children

        # Back-fill SHADER.child_node_offset → first property child
        first_prop_pos = property_children[0][1]
        self._backfill_int(
            buffer,
            shader_node_pos + _NODE_OFF_CHILD_NODE_OFFSET,
            first_prop_pos - shader_node_pos,
        )

        # Back-fill each property child's parent/prev/next offsets and data_size
        for i, (prop_name_str, child_pos, payload_end) in enumerate(property_children):
            # parent_node_offset → SHADER (negative)
            self._backfill_int(
                buffer,
                child_pos + _NODE_OFF_PARENT_NODE_OFFSET,
                shader_node_pos - child_pos,
            )
            # previous sibling (negative)
            if i > 0:
                prev_pos = property_children[i - 1][1]
                self._backfill_int(
                    buffer,
                    child_pos + _NODE_OFF_PREV_NODE_OFFSET,
                    prev_pos - child_pos,
                )
            # next sibling (positive)
            if i < len(property_children) - 1:
                next_pos = property_children[i + 1][1]
                self._backfill_int(
                    buffer,
                    child_pos + _NODE_OFF_NEXT_NODE_OFFSET,
                    next_pos - child_pos,
                )
            # data_size
            data_size = payload_end - (child_pos + FoxDataNode.SIZE)
            self._backfill_uint(buffer, child_pos + _NODE_OFF_DATA_SIZE, data_size)

        return shader_node_pos, property_children

    def _write_placeholder_node(
        self,
        buffer: io.BytesIO,
        name_hash: int,
        flags: int = FoxDataNodeType.STRINGDATA,
        name_string: Optional[str] = None,
    ) -> int:
        """Write a FoxDataNode with all offsets/sizes zeroed and return its position.

        Used for container nodes (ROOT, MOTION) that carry no payload.  All
        back-fill fields (child/parent/prev/next offsets, data_size) are written
        as zero; the caller fills them in via ``_backfill_*``.

        If ``name_string`` is provided, the node body is followed by a 16-byte
        area containing the null-terminated name padded to 16 bytes, and
        ``name_string_offset`` is set to ``FoxDataNode.SIZE`` (48).
        """
        node_pos = buffer.tell()
        name_string_offset = FoxDataNode.SIZE if name_string else 0
        FoxDataNode(
            name_hash=name_hash,
            name_string_offset=name_string_offset,
            flags=flags,
            data_offset=0,   # container: no payload
            data_size=0,
            parent_node_offset=0,
            child_node_offset=0,
            previous_node_offset=0,
            next_node_offset=0,
            parameters_offset=0,
        ).write(buffer)
        if name_string:
            # Write null-terminated name, zero-padded to 16 bytes
            encoded = name_string.encode('ascii') + b'\x00'
            pad_len = _CONTAINER_NAME_STRING_SIZE - len(encoded)
            if pad_len < 0:
                # Truncate if somehow longer than 15 chars + null (shouldn't happen for ROOT/MOTION)
                encoded = encoded[:_CONTAINER_NAME_STRING_SIZE]
                pad_len = 0
            buffer.write(encoded)
            buffer.write(b'\x00' * pad_len)
        return node_pos

    def _write_node_parameters(self, buffer: io.BytesIO, params: Optional[List[Tuple[int, Union[float, str, int]]]] = None) -> None:
        """Write FoxDataNodeParameter chain for any node.

        Writes the FoxDataNodeParameter chain.  If *params* is ``None`` or empty,
        falls back to the canonical defaults (SLOPE_ANGLE=0.0, SLOPE_DIR=0.0)
        for MOTION nodes.

        Entry formats (all little-endian):

        - **FLOAT** (``value`` is ``float``, 16 bytes)::

              ushort type=2, short next_off, uint32 name_hash, uint32 name_str_off=0, float value

        - **STRING hash-only** (``value`` is ``int``, 20 bytes)::

              ushort type=1, short next_off, uint32 name_hash, uint32 name_str_off=0,
              uint32 value_hash=value, uint32 value_str_off=0

        - **STRING inline** (``value`` is ``str``, 20 bytes + string + align-16)::

              ushort type=1, short next_off, uint32 name_hash, uint32 name_str_off=0,
              uint32 value_hash=strcode32(value), uint32 value_str_off=8,
              <null-terminated UTF-8 string>, <zero padding to 16-byte boundary>

        ``next_off`` is 0 for the last entry; for all others it equals the total size
        of the current entry (including any inline string bytes and alignment padding).

        Args:
            buffer: Seekable BytesIO buffer to write to.
            params: List of ``(name_hash, value)`` tuples where ``value`` is
                    ``float`` (FLOAT), ``int`` (STRING hash-only), or
                    ``str`` (STRING inline).  If ``None`` or empty, defaults to
                    ``[(SLOPE_ANGLE, 0.0), (SLOPE_DIR, 0.0)]``.
        """
        effective_params: List[Tuple[int, Union[float, str, int]]] = params if params else [
            (FOXDATA_HASH_PARAM_SLOPE_ANGLE, 0.0),
            (FOXDATA_HASH_PARAM_SLOPE_DIR, 0.0),
        ]

        # Pre-compute per-entry sizes so next_off can be set correctly before writing.
        entry_sizes: List[int] = []
        for _, value in effective_params:
            if isinstance(value, str):
                raw = value.encode('utf-8') + b'\x00'
                total = _STRING_PARAM_ENTRY_SIZE + len(raw)
                entry_sizes.append((total + 15) & ~15)  # align to 16
            elif isinstance(value, int):
                entry_sizes.append(_STRING_PARAM_ENTRY_SIZE)
            else:  # float
                entry_sizes.append(_FLOAT_PARAM_ENTRY_SIZE)

        n = len(effective_params)
        for i, (name_hash, value) in enumerate(effective_params):
            is_last = (i == n - 1)
            next_off = 0 if is_last else entry_sizes[i]

            if isinstance(value, float):
                buffer.write(struct.pack('<HhIIf',
                    FoxDataParamType.FLOAT, next_off,
                    name_hash, 0,
                    value,
                ))
            elif isinstance(value, int):
                # STRING hash-only: Value.hash = value, Value.StringOffset = 0
                buffer.write(struct.pack('<HhIIII',
                    FoxDataParamType.STRING, next_off,
                    name_hash, 0,
                    value, 0,
                ))
            elif isinstance(value, str):
                # STRING inline: Value.StringOffset = 8 (relative to Value.hash field)
                # String starts immediately after the 20-byte entry.
                raw = value.encode('utf-8') + b'\x00'
                total = _STRING_PARAM_ENTRY_SIZE + len(raw)
                pad = ((total + 15) & ~15) - total
                buffer.write(struct.pack('<HhIIII',
                    FoxDataParamType.STRING, next_off,
                    name_hash, 0,
                    strcode32(value), 8,
                ))
                buffer.write(raw)
                buffer.write(b'\x00' * pad)

        # Align the buffer to 16 bytes after the params chain so the next
        # structure (node header or payload) always starts at an aligned offset.
        # FLOAT entries (16 bytes each) are already aligned in practice; this
        # is critical for STRING hash-only entries which are 20 bytes.
        align_buffer(buffer, 16)

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
            EntryCount x { uint32 hash; uint32 StringOffset; }
            [null-terminated name strings, packed after the entry table]

        ``StringOffset`` is relative to the entry's own StringOffset field position.
        For string names (non-integer), the inline strings are written after all
        entries, reproducing the original file layout exactly.  For integer-only
        entries (no source string available), StringOffset is written as 0.

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

        # Sort names alphabetically (hash-string fallbacks sort before real names
        # since ASCII digits precede uppercase letters, which is acceptable).
        names = sorted(names, key=str)

        # Resolve each name to (hash_val, name_str_or_None)
        entries = []
        for name in names:
            if isinstance(name, int):
                entries.append((name, None))
            else:
                s = str(name)
                if is_hash_string(s):
                    # Decimal or 0x-hex literal — no inline string available
                    entries.append((parse_hash_string(s), None))
                else:
                    entries.append((strcode32(s), s))

        # Write EntryCount
        buffer.write(struct.pack('<I', len(entries)))

        # Write all (hash, placeholder_string_offset) entries, recording each
        # entry's start position (= Hash field = start of FoxDataName) and the
        # position of its StringOffset field for later backfilling.
        entry_data = []  # (entry_start_pos, str_off_pos, name_str)
        for hash_val, name_str in entries:
            entry_start_pos = buffer.tell()           # start of FoxDataName (Hash field)
            buffer.write(struct.pack('<I', hash_val))
            str_off_pos = buffer.tell()               # position of StringOffset field
            buffer.write(struct.pack('<I', 0))        # placeholder
            entry_data.append((entry_start_pos, str_off_pos, name_str))

        # Align to 8 bytes, then write 8 zero bytes of padding before the
        # inline string area (matches observed original file layout).
        align_buffer(buffer, 8)
        buffer.write(b'\x00' * 8)

        # Write inline name strings and backfill StringOffset fields.
        # StringOffset is relative to the start of the FoxDataName (Hash field),
        # i.e. startof(entry) + StringOffset == address of the null-terminated string.
        for entry_start_pos, str_off_pos, name_str in entry_data:
            if name_str is not None:
                string_abs_pos = buffer.tell()
                string_offset = string_abs_pos - entry_start_pos  # relative to Hash field
                # Backfill StringOffset
                self._backfill_uint(buffer, str_off_pos, string_offset)
                # Write null-terminated string (packed tightly, no per-string alignment)
                buffer.write(name_str.encode('ascii') + b'\x00')

        align_buffer(buffer, 16)
        payload_end = buffer.tell()

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

        return (node_pos, payload_start, payload_end)

    # ─────────────────────────────────────────────────────────────────────────
    # Track structure builder
    # ─────────────────────────────────────────────────────────────────────────

    def _build_tracks_from_wrappers(
        self,
        track_wrappers: List[TrackUnitWrapper],
        frame_count: int,
        frame_rate: int,
        t_id: int = 0,
        unknown_a: int = 0,
        unknown_b: int = 1,
    ) -> Tracks:
        """Convert a list of ``TrackUnitWrapper`` objects to a ``Tracks`` structure.

        Converts ``TrackDataBlobWrapper`` segments to ``TrackData`` instances with
        keyframe blobs.  The resulting ``Tracks`` object is ready to be serialised
        by ``Tracks.write(write_data_blobs=True)``.

        Args:
            track_wrappers: Unit wrappers to convert.
            frame_count:    Frame count for the ``TrackHeader``.
            frame_rate:     Frame rate for the ``TrackHeader``.
            t_id:           ``TrackHeader.t_id`` override (default 0).
            unknown_a:      ``TrackHeader.unknown_a`` override (default 0).
            unknown_b:      ``TrackHeader.unknown_b`` override (default 1 — asserted
                            in binary template).
        """
        track_units: List[TrackUnit] = []
        absolute_segment_index: int = 0
        for wrapper in track_wrappers:
            track_data_list: List[TrackData] = []
            if wrapper.segments_track_data:
                segment_count = len(wrapper.segments_track_data)
                for seg_idx, blob_wrapper in enumerate(wrapper.segments_track_data):
                    # next_entry_offset: 8 (TrackData.ENTRY_SIZE) for non-last, 0 for last
                    is_last = (seg_idx == segment_count - 1)
                    next_entry_offset = 0 if is_last else TrackData.ENTRY_SIZE
                    track_data_list.append(
                        TrackData(
                            data_offset=0, # calculated by Tracks.write()
                            ms_id=absolute_segment_index,
                            td_type=blob_wrapper.data_blob.type,
                            next_entry_offset=next_entry_offset,
                            component_bit_size=blob_wrapper.data_blob.component_bit_size,
                            data_blob=blob_wrapper.data_blob.keyframes,
                        )
                    )
                    absolute_segment_index += 1

            unit_flags_int = (
                TrackUnitFlags.track_unit_flags_to_int(wrapper.unit_flags)
                if wrapper.unit_flags
                else 0
            )
            track_units.append(
                TrackUnit(
                    name=StrCode32.from_string(wrapper.name),
                    segment_count=len(track_data_list),
                    unit_flags=unit_flags_int,
                    padding=0,
                    segments_data=track_data_list,
                )
            )

        header = TrackHeader(
            unit_count=len(track_units),
            segment_count=sum(u.segment_count for u in track_units),
            t_id=t_id,
            unknown_a=unknown_a,
            unknown_b=unknown_b,  # Must be 1 per binary template assertion
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
