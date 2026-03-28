"""Shader-node-specific Blender import utilities (old-format GANI only).

Old-format (FoxData/GZ) GANI files may contain a SHADER node that holds
facial/property animation tracks (e.g. tension controllers for facial rigs).
The SHADER node is a sibling of MOTION under ROOT, containing one child per
shader property (TENSION_CHEEKL, TENSION_CHEEKR, TENSION_NECK).  Each property
child carries a TrackHeader payload with one or more TrackUnit objects (the
individual animation channels, e.g. TensionController).

This module creates the Blender representation:
 - A dedicated "ShaderNodes" armature whose bones mirror the SHADER hierarchy:
     • One *property bone* per shader property (e.g. "TENSION_CHEEKL").
     • One *unit bone* per track unit inside the property
       (e.g. "TENSION_CHEEKL.3462891356"), parented to the property bone.
 - One NLA action per GANI that contains FCurves for the unit bones.
 - The unit bone name is ``"{property_name}.{unit_decimal_hash}"``.  The decimal
   hash matches the ``StrCode32`` value stored in the binary file so that
   round-trip export can reconstruct the original hash.

Dependency chain (no circularity):
    tools_mtar_importer
        → tools_gani_shader_importer   (this module)
            → tools_gani_track_importer
            → py_foxwrap / py_fox / py_utilities
"""

from typing import Optional, List, Dict, Tuple

import bpy
from ..py_foxwrap_utilities import futil_naming

from ..py_core.core_logging import Debug

from ..py_utilities.util_blender_armature_types import BoneSpec
from ..py_utilities import util_blender_animation, util_blender_armature

from ..py_fox import fox_mtar_constants as mtar_const
from ..py_fox import fox_gani_constants as gani_const
from ..py_fox.fox_mtar_types import MtarTableList2

from ..py_foxwrap.fwrap_mtar_import_types import Gani1ShaderTrackWrapper
from ..py_foxwrap.fwrap_track_types import TrackUnitWrapper
from ..py_foxwrap.fwrap_metadata_types import TrackMetaData
from ..py_foxwrap import fwrap_metadata, fwrap_track

# TODO: Tools shoud not import other tools
from .tools_gani_track_importer import import_gani_track
from .tools_motion_points_importer import create_nla_strips_for_actions


def _store_shader_property_header_on_action(
    action: bpy.types.Action,
    prop_name: str,
    track_header: object,
) -> None:
    """Store per-property ``TrackHeader`` fields as an action custom property.

    Stored format::

        "t_id=X ; unknown_a=Y ; unknown_b=Z ; frame_count=N ; frame_rate=W"

    Read back by
    :func:`~py_tools.tools_gani_shader_exporter.collect_shader_property_headers`.

    Args:
        action:       Blender action to store the property on.
        prop_name:    SHADER property name (e.g. ``"TENSION_CHEEKL"``).
        track_header: Source ``TrackHeader`` object.
    """
    key = f"{gani_const.SHADER_HDR_PREFIX}{prop_name}"
    value = (
        f"t_id={int(track_header.t_id)} ; "
        f"unknown_a={int(track_header.unknown_a)} ; "
        f"unknown_b={int(track_header.unknown_b)} ; "
        f"frame_count={int(track_header.frame_count)} ; "
        f"frame_rate={int(track_header.frame_rate)}"
    )
    action[key] = value
    action.id_properties_ui(key).update(
        description=f"Shader TrackHeader for property '{prop_name}'"
    )


def _convert_shader_track_to_unit_wrappers(
    shader_track: Gani1ShaderTrackWrapper,
) -> Tuple[List[TrackUnitWrapper], str]:
    """Convert a ShaderTrackWrapper to a list of unit TrackUnitWrapper objects.

    Each unit inside the property is named ``"{property_name}.{unit_decimal_hash}"``
    so that bones are uniquely identifiable across all properties in the armature
    and the hash is preserved for round-trip export.

    Args:
        shader_track: Parsed shader property wrapper.

    Returns:
        Tuple of (unit_wrappers, property_name).
    """
    prop_name = shader_track.property_name

    # Convert to gani tracks using decimal hashes for unit names (no unhashing —
    # the unit hashes are not in the rig/gani dictionaries and decimal preserves
    # the original StrCode32 value needed for export round-trip).
    gani_tracks: List[TrackUnitWrapper] = fwrap_track.apply_track_naming(
        shader_track.tracks.as_wrapper(),
        use_decimal_only=True,
    )
    fwrap_track.apply_segment_suffixes_to_tracks(gani_tracks)

    # Prefix every track/segment name with the property name so bones are
    # globally unique within the armature.
    prefixed: List[TrackUnitWrapper] = []
    for gani_track in gani_tracks:
        prefixed_name = f"{prop_name}.{gani_track.name}"
        new_segs = [
            type(seg)(
                name=f"{prop_name}.{seg.name}",
                segment_index=seg.segment_index,
                data_blob=seg.data_blob,
            )
            for seg in gani_track.segments_track_data
        ]
        prefixed.append(TrackUnitWrapper(
            name=prefixed_name,
            segments_track_data=new_segs,
            unit_flags=gani_track.unit_flags,
            rig_unit_type=gani_track.rig_unit_type,
        ))

    return prefixed, prop_name


def create_shader_animation_actions(
    context: bpy.types.Context,
    mtar_file_name: str,
    all_shader_gani_tracks: List[List[Gani1ShaderTrackWrapper]],
    all_file_headers: List[MtarTableList2],
    path_to_indices: Dict[int, Tuple[int, int]],
    use_verbose_naming: bool,
    gani_hash_dict: Optional[Dict[int, str]] = None,
    all_node_params: Optional[List[Dict]] = None,
) -> List[Optional[bpy.types.Action]]:
    """Create Blender animation actions for shader nodes from MTAR data.

    Creates one action per GANI that contains shader tracks.  The action stores
    FCurves for each unit bone (named ``"{property_name}.{unit_decimal_hash}"``).

    Args:
        context:                    Blender context.
        mtar_file_name:             Base name for generated action names.
        all_shader_gani_tracks:     Per-GANI :class:`ShaderTrackWrapper` lists.
        all_file_headers:           Per-GANI file headers (path hashes).
        path_to_indices:            Maps path hash → ``(h_index, d_index)``.
        use_verbose_naming:         Include h/d indices in action names.
        gani_hash_dict:             Optional GANI-path dictionary.

    Returns:
        List of actions (one per GANI); ``None`` entries for GANIs without shader data.
    """
    shader_actions: List[Optional[bpy.types.Action]] = []

    Debug.log(
        f"\nProcessing {len(all_shader_gani_tracks)} GANI file(s) for shader nodes..."
    )

    for gani_index, shader_tracks in enumerate(all_shader_gani_tracks):
        if not shader_tracks:
            shader_actions.append(None)
            continue

        Debug.log(
            f"\n  --- Shader Nodes GANI {gani_index + 1}/"
            f"{len(all_shader_gani_tracks)} ---"
        )

        file_header = all_file_headers[gani_index]
        h_idx, d_idx = path_to_indices.get(file_header.path, (0, 0))

        gani_full_path, gani_name_segment = futil_naming.resolve_gani_name_segment(file_header, gani_hash_dict)

        action_name: str = futil_naming.format_action_name(
            mtar_file_name, gani_index, h_idx, d_idx,
            use_verbose_naming,
            is_shader_nodes=True,
            gani_name=gani_name_segment,
        )

        action: bpy.types.Action = bpy.data.actions.new(name=action_name)
        shader_actions.append(action)
        Debug.log(f"  Created shader nodes action: {action_name}")

        if hasattr(file_header, 'path'):
            if gani_full_path is not None:
                action[mtar_const.TABL_PATH] = gani_full_path
            else:
                action[mtar_const.TABL_PATH] = str(file_header.path)

        gani_frame_count: int = 0

        # Accumulate per-action track metadata for lossless round-trip export
        all_track_metadata: List[TrackMetaData] = []

        Debug.log(f"  Processing {len(shader_tracks)} shader property node(s)...")
        for shader_track in shader_tracks:
            Debug.log(f"    Property: {shader_track.property_name}")

            unit_wrappers, _prop_name = _convert_shader_track_to_unit_wrappers(shader_track)

            # Use the track header frame_count from the property's Tracks object
            if shader_track.tracks and shader_track.tracks.header:
                prop_frame_count = shader_track.tracks.header.frame_count
                if prop_frame_count > gani_frame_count:
                    gani_frame_count = prop_frame_count

            # Import animation tracks using the shared import_gani_track with
            # transforms disabled (shader values are raw — no axis-swap or rest-pose).
            for unit_wrapper in unit_wrappers:
                track_max_frame = import_gani_track(
                    context, action, unit_wrapper,
                    slot_name=util_blender_animation.MTAR_SHADER_SLOT_NAME,
                    apply_transforms=False,
                )
                gani_frame_count = max(gani_frame_count, track_max_frame)

            # Build TrackMetaData from the original TrackUnit objects (lossless
            # component_bit_sizes, unit_flags, and segment types).
            track_units = (
                shader_track.tracks.track_units
                if shader_track.tracks else []
            )
            for unit_wrapper, track_unit in zip(unit_wrappers, track_units):
                bone_name = unit_wrapper.name
                segment_types = [seg.td_type for seg in track_unit.segments_data]
                component_bit_sizes = [seg.component_bit_size for seg in track_unit.segments_data]
                all_track_metadata.append(TrackMetaData(
                    track_name=bone_name,
                    name_hash=(
                        track_unit.name.to_int()
                        if track_unit.name else None
                    ),
                    segment_types=segment_types,
                    component_bit_sizes=component_bit_sizes,
                    unit_flags=track_unit.unit_flags,
                    flags_list=None,
                    rig_unit_type=None,
                ))

            # Store per-property TrackHeader for lossless round-trip
            if shader_track.tracks and shader_track.tracks.header:
                _store_shader_property_header_on_action(action, shader_track.property_name, shader_track.tracks.header)

        # Store all SHADER node params (container and per-property) for lossless round-trip
        if all_node_params and gani_index < len(all_node_params):
            for node_key, params in all_node_params[gani_index].items():
                if node_key.startswith("SHADER"):
                    fwrap_metadata.store_node_params_on_action(action, node_key, params)

        # Store per-unit metadata on the action (bits, flags — no segment abbrevs
        # or hash needed since the bone name already encodes the decimal hash).
        if all_track_metadata:
            fwrap_metadata.store_track_metadata_on_action(
                action, all_track_metadata,
                include_segments=False,
            )

        util_blender_animation.configure_action(action, frame_start=0, frame_end=gani_frame_count)
        Debug.log(f"  Shader nodes frame range: 0 - {gani_frame_count}")

    return shader_actions


def create_and_setup_shader_nodes_armature(
    context: bpy.types.Context,
    mtar_file_name: str,
    all_shader_gani_tracks: List[List[Gani1ShaderTrackWrapper]],
    shader_actions: List[Optional[bpy.types.Action]],
    all_file_headers: List[MtarTableList2],
    path_to_indices: Dict[int, Tuple[int, int]],
    use_verbose_naming: bool,
    strip_padding: int = 10,
    reference_actions: Optional[List[Optional[bpy.types.Action]]] = None,
) -> Optional[bpy.types.Object]:
    """Create and set up the shader nodes armature.

    Builds an armature containing:
     - One **property bone** per shader property (e.g. "TENSION_CHEEKL").
     - One **unit bone** per track unit inside each property
       (e.g. "TENSION_CHEEKL.3462891356"), parented to the property bone.

    Args:
        context:                    Blender context.
        mtar_file_name:             Base name for the armature object.
        all_shader_gani_tracks:     Per-GANI :class:`ShaderTrackWrapper` lists
                                    (used to collect all bone names across GANIs).
        shader_actions:             Pre-created shader animation actions.
        all_file_headers:           Per-GANI file headers for NLA strip naming.
        path_to_indices:            Maps path hash → ``(h_index, d_index)``.
        use_verbose_naming:         Include h/d indices in NLA strip names.
        strip_padding:              Gap (frames) between NLA strips.
        reference_actions:          Optional main-armature actions for frame sync.

    Returns:
        The created armature object, or ``None`` if no shader data exists.
    """
    # Collect all unique property names and their unit bone names across all GANIs.
    # property_name → Set[unit_bone_name]
    all_shader_properties: Dict[str, set] = {}
    for shader_tracks in all_shader_gani_tracks:
        for shader_track in shader_tracks:
            prop_name = shader_track.property_name
            if prop_name not in all_shader_properties:
                all_shader_properties[prop_name] = set()
            unit_wrappers, _ = _convert_shader_track_to_unit_wrappers(shader_track)
            for unit_wrapper in unit_wrappers:
                all_shader_properties[prop_name].add(unit_wrapper.name)

    if not all_shader_properties:
        return None

    Debug.log("\nCreating shader nodes armature...")
    armature_name = f"{mtar_file_name}_ShaderNodes"

    # Build ordered BoneSpec list: property parent bones first, then unit children.
    bone_specs: List[BoneSpec] = []
    seen: set = set()
    for prop_name, unit_names in all_shader_properties.items():
        if prop_name not in seen:
            bone_specs.append(BoneSpec(name=prop_name, parent_name=None))
            seen.add(prop_name)
        for unit_bone_name in sorted(unit_names):
            if unit_bone_name not in seen:
                bone_specs.append(BoneSpec(name=unit_bone_name, parent_name=prop_name))
                seen.add(unit_bone_name)

    armature_obj: bpy.types.Object = util_blender_armature.create_track_armature(context, armature_name, bone_specs)

    # Assign NLA actions if any
    if any(a is not None for a in shader_actions):
        Debug.log("\n=== Processing Shader Node Animations ===")

        if not armature_obj.animation_data:
            armature_obj.animation_data_create()

        # ensure each shader action has a slot on the armature before
        # we start inserting strips.  this matches the slot name used
        # during FCurve import so that channelbags already exist.
        for action in shader_actions:
            if action is not None:
                util_blender_animation.assign_action_to_datablock(
                    armature_obj,
                    action,
                    slot_name=util_blender_animation.MTAR_SHADER_SLOT_NAME,
                )

        nla_track: bpy.types.NlaTrack = armature_obj.animation_data.nla_tracks.new()
        nla_track.name = f"{mtar_file_name}_ShaderNodes_Animations"
        Debug.log(f"Created NLA track: {nla_track.name}")

        create_nla_strips_for_actions(
            nla_track,
            shader_actions,
            mtar_file_name,
            all_file_headers,
            path_to_indices,
            use_verbose_naming,
            is_shader_nodes=True,
            strip_padding=strip_padding,
            reference_actions=reference_actions,
        )

        Debug.log("Shader node animations import complete")

    return armature_obj
