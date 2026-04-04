"""
Utilities for storing and reading motion events in Blender.

This module provides functions to store motion events from MTAR files as:
1. Custom properties on actions (for complete data preservation)
2. NLA markers (for visual timeline representation)
"""
from typing import List, Dict, Optional

import bpy

from ..py_core.core_logging import Debug

from ..py_utilities import util_hashing, util_hashing_cityhash, util_parsing

from ..py_fox import fox_gani_constants as gani_const
from ..py_fox.fox_gani_types import EvpHeader, EvpData, EventUnitInfo, TimeSection
from ..py_fox.fox_hash_types import StrCode32

from .fwrap_metadata import EVENT_PROP_PREFIX, make_event_property_key, iter_event_properties


def store_motion_events_on_action(action: bpy.types.Action, motion_events: Optional[EvpHeader]) -> None:
    """Store motion events as custom properties and NLA markers on an action.

    Creates two NLA markers per event (start and end) in the format:
    - <event_name>_start at start_frame
    - <event_name>_end at end_frame

    Stores event parameters as custom properties in format:
    name=<event_name> ; category=<category> ; ints=<i1,i2> ; floats=<f1,f2> ; strings=<s1,s2> ; format=<n>

    Args:
        action: The Blender action to store motion events on
        motion_events: EvpHeader containing all motion event data, or None
    """
    if not motion_events or motion_events.count == 0:
        return

    Debug.log(f"Storing {motion_events.count} motion event categor(ies) on action '{action.name}'")

    # Store version as a custom property
    action[gani_const.EVPH_VERSION] = motion_events.version
    action.id_properties_ui(gani_const.EVPH_VERSION).update(
        description="EvpHeader version from MTAR file"
    )

    event_index = 0

    for category_data in motion_events.data:
        category_name = str(category_data.category_name.to_int())
        Debug.log(f"  Category '{category_name}': {category_data.unit_count} event(s)")

        for event in category_data.events:
            # Look up event name from the events dictionary
            hash_val = event.name.to_int()
            event_name = util_hashing.unhash_event_name(hash_val)
            if event_name is None:
                event_name = str(hash_val)
                Debug.log(f"StrCode32 hash {hash_val} not found in events dictionary, using hash as name")

            # Build parameter strings
            params_parts = []

            # Integer parameters
            if event.int_params:
                ints_str = ','.join(str(i) for i in event.int_params)
                params_parts.append(f"ints={ints_str}")

            # Float parameters
            if event.float_params:
                floats_str = ','.join(util_parsing.format_float_for_metadata(f) for f in event.float_params)
                params_parts.append(f"floats={floats_str}")

            # String parameters (stored as uint64 hashes)
            if event.string_params:
                strings_str = ','.join(str(s) for s in event.string_params)
                params_parts.append(f"strings={strings_str}")

            # Build format string (TIME_SECTION_FORMAT)
            format_str = f"format={event.format}"
            params_parts.append(format_str)

            # Build unified metadata value: name=X ; category=Y ; ints=... ; floats=... ; strings=... ; format=N
            all_parts = [f"name={event_name}", f"category={category_name}"]
            all_parts.extend(params_parts)
            metadata_value = ' ; '.join(all_parts)

            # Store as custom property using standardized key format
            property_key = make_event_property_key(event_index)
            action[property_key] = metadata_value

            # Set custom property metadata for UI display
            action.id_properties_ui(property_key).update(
                description=f"Motion event {event_index}: {event_name} (category {category_name})"
            )

            # Create NLA markers for each time section
            for section_idx, time_section in enumerate(event.time_sections):
                start_frame = time_section.start_frame
                end_frame = time_section.end_frame

                # Create marker name: <event_index>_<event_name> (include section index if multiple)
                base_marker_name = f"{event_index}_{event_name}"
                if len(event.time_sections) > 1:
                    base_marker_name += f"_{section_idx}"

                # If end_frame is <= -1, create only a single marker without _start suffix
                if end_frame <= -1:
                    marker = action.pose_markers.new(base_marker_name)
                    marker.frame = start_frame
                    Debug.log(f"    Event '{event_name}': frame {start_frame} (marker: {base_marker_name})")
                else:
                    # Create start marker
                    start_marker_name = f"{base_marker_name}_start"
                    start_marker = action.pose_markers.new(start_marker_name)
                    start_marker.frame = start_frame

                    # Create end marker
                    end_marker_name = f"{base_marker_name}_end"
                    end_marker = action.pose_markers.new(end_marker_name)
                    end_marker.frame = end_frame

                    Debug.log(f"    Event '{event_name}': frames {start_frame}-{end_frame} (markers: {start_marker_name}, {end_marker_name})")

            event_index += 1

    Debug.log(f"Stored {event_index} motion event(s) with markers on action '{action.name}'")


def read_motion_events_from_action(action: bpy.types.Action) -> Optional[EvpHeader]:
    """Read motion events from custom properties and NLA markers on an action.

    Reconstructs EvpHeader from stored custom properties and marker frames.

    Args:
        action: The Blender action to read motion events from

    Returns:
        EvpHeader object with all motion event data, or None if no events found
    """
    if not action:
        return None

    # Get all event properties sorted by event index
    event_properties_list = iter_event_properties(action)

    if not event_properties_list:
        return None

    Debug.log(f"Reading {len(event_properties_list)} motion event(s) from action '{action.name}'")

    # Group events by category
    categories: Dict[str, List[tuple]] = {}  # category -> list of (event_name, params, frames)

    for event_idx, metadata_str in event_properties_list:
        if not isinstance(metadata_str, str):
            continue

        # Parse unified key=value format: name=X ; category=Y ; ints=... ; floats=... ; strings=... ; format=N
        kv: dict = {}
        for part in metadata_str.split(';'):
            part = part.strip()
            if not part or '=' not in part:
                continue
            k, v = part.split('=', 1)
            kv[k.strip()] = v.strip()

        event_name = kv.get('name', '')
        if not event_name:
            continue

        # Category comes exclusively from the property value
        category_name = kv.get('category', '')
        if not category_name:
            Debug.log_warning(f"  Event index {event_idx} has no 'category' field in its property value; skipping")
            continue

        # Keep the readable event name for NLA marker lookup; compute hash for binary export.
        event_name_for_marker = event_name
        if not event_name.isdigit():
            # Compute StrCode32 hash for the event name to use during export
            event_name = str(util_hashing_cityhash.strcode32(event_name))

        # Parse parameters
        int_params = []
        float_params = []
        string_params = []
        format_type = 0

        if kv.get('ints'):
            int_params = [int(i.strip()) for i in kv['ints'].split(',') if i.strip()]
        if kv.get('floats'):
            float_params = [float(f.strip()) for f in kv['floats'].split(',') if f.strip()]
        if kv.get('strings'):
            string_params = [int(s.strip()) for s in kv['strings'].split(',') if s.strip()]
        if kv.get('format'):
            try:
                format_type = int(kv['format'])
            except (ValueError, TypeError):
                pass

        # Find corresponding markers to get frame ranges.
        # Marker naming (written by store_motion_events_on_action):
        #   single section : {event_idx}_{event_name}_start / _end  (or just the base for infinite)
        #   multiple sections: {event_idx}_{event_name}_0_start / _end, _1_start / _end, ...
        time_sections = []
        base_marker_name = f"{event_idx}_{event_name_for_marker}"

        # Try non-indexed markers first (single-section write path)
        start_marker = action.pose_markers.get(f"{base_marker_name}_start")
        end_marker   = action.pose_markers.get(f"{base_marker_name}_end")
        single_marker = action.pose_markers.get(base_marker_name)

        if start_marker and end_marker:
            time_sections.append(TimeSection(
                start_frame=int(start_marker.frame),
                end_frame=int(end_marker.frame)
            ))
        elif single_marker:
            time_sections.append(TimeSection(
                start_frame=int(single_marker.frame),
                end_frame=-1
            ))
        else:
            # No non-indexed markers found — try indexed (multi-section write path)
            section_idx = 0
            while True:
                s_start = action.pose_markers.get(f"{base_marker_name}_{section_idx}_start")
                s_end   = action.pose_markers.get(f"{base_marker_name}_{section_idx}_end")
                s_single = action.pose_markers.get(f"{base_marker_name}_{section_idx}")

                if s_start and s_end:
                    time_sections.append(TimeSection(
                        start_frame=int(s_start.frame),
                        end_frame=int(s_end.frame)
                    ))
                    section_idx += 1
                elif s_single:
                    time_sections.append(TimeSection(
                        start_frame=int(s_single.frame),
                        end_frame=-1
                    ))
                    section_idx += 1
                else:
                    break

        # Events without time sections are allowed (time_section_count = 0)
        if not time_sections:
            Debug.log(f"  Info: Event '{category_name}_{event_name}' has no time sections (count=0)")

        # Add to category
        if category_name not in categories:
            categories[category_name] = []

        categories[category_name].append((
            event_name,
            int_params,
            float_params,
            string_params,
            format_type,
            time_sections
        ))

    if not categories:
        return None

    # Build EvpHeader
    evp_data_list = []
    entry_offsets = []

    for category_name, events in categories.items():
        # Create EventUnitInfo objects
        event_units = []
        unit_offsets = []

        for event_name, int_params, float_params, string_params, format_type, time_sections in events:
            # Convert name string to StrCode32, handling both numeric and enum names.
            try:
                name_code = StrCode32.from_string(event_name)
            except ValueError:
                # Fallback: try simple integer conversion; if that also fails, log and
                # default to zero so export still succeeds.
                try:
                    name_code = StrCode32(int(event_name))
                except (ValueError, TypeError):
                    Debug.log_warning(f"Unrecognized event name '{event_name}' in action; using 0")
                    name_code = StrCode32(0)

            event_unit = EventUnitInfo(
                name=name_code,
                time_section_count=len(time_sections),
                format=format_type,
                int_param_count=len(int_params),
                float_param_count=len(float_params),
                string_param_count=len(string_params),
                time_sections=time_sections,
                int_params=int_params,
                float_params=float_params,
                string_params=string_params
            )
            event_units.append(event_unit)
            unit_offsets.append(0)  # Offsets will be calculated during write

        # Create EvpData
        evp_data = EvpData(
            category_name=StrCode32(int(category_name)),
            unit_count=len(event_units),
            cache_offset=0,
            unit_offsets=unit_offsets,
            events=event_units
        )
        evp_data_list.append(evp_data)
        entry_offsets.append(0)  # Offsets will be calculated during write

        Debug.log(f"  Reconstructed category '{category_name}': {len(event_units)} event(s)")

    # Read stored version from action, default to 201207030 if not found
    version = action.get(gani_const.EVPH_VERSION, 201207030)

    # Create EvpHeader
    evp_header = EvpHeader(
        version=version,
        count=len(evp_data_list),
        padding=0,
        entry_offsets=entry_offsets,
        data=evp_data_list
    )

    Debug.log(f"Reconstructed EvpHeader with {evp_header.count} categor(ies), version={version}")

    return evp_header


def clear_motion_events_from_action(action: bpy.types.Action) -> None:
    """Remove all motion event custom properties and their associated pose markers from an action.

    Handles both old-style markers ({category}_{event_name}_start/end) and new-style
    ({event_idx}_{event_name}_start/end) by reading the name field from property values.
    Also removes the EVPH_VERSION property.

    Args:
        action: The Blender action to clear motion event data from
    """
    markers_to_remove: set = set()
    keys_to_remove = []

    for key in action.keys():
        if not key.startswith(EVENT_PROP_PREFIX):
            continue
        # Accept both old format (event_000_something) and new format (event_000).
        # The first token after the prefix must be numeric digits.
        suffix = key[len(EVENT_PROP_PREFIX):]
        idx_str = suffix.split('_')[0]
        if not idx_str.isdigit():
            continue

        keys_to_remove.append(key)
        event_idx = int(idx_str)
        metadata_str = action.get(key, '')
        if not isinstance(metadata_str, str):
            continue

        # Parse name and category from the value string
        kv: dict = {}
        for part in metadata_str.split(';'):
            part = part.strip()
            if '=' not in part:
                continue
            k, v = part.split('=', 1)
            kv[k.strip()] = v.strip()

        event_name_raw = kv.get('name', '')
        category_name = kv.get('category', '')

        if not event_name_raw:
            continue

        # Collect old-style markers: {category}_{event_name}[_*]
        if category_name:
            old_base = f"{category_name}_{event_name_raw}"
            for marker in action.pose_markers:
                mn = marker.name
                if mn == old_base or mn.startswith(old_base + '_'):
                    markers_to_remove.add(mn)

        # Collect new-style markers: {event_idx}_{event_name}[_*]
        new_base = f"{event_idx}_{event_name_raw}"
        for marker in action.pose_markers:
            mn = marker.name
            if mn == new_base or mn.startswith(new_base + '_'):
                markers_to_remove.add(mn)

    # Remove collected pose markers
    for marker_name in markers_to_remove:
        marker = action.pose_markers.get(marker_name)
        if marker:
            action.pose_markers.remove(marker)

    # Remove event custom properties
    for key in keys_to_remove:
        if key in action:
            del action[key]

    # Remove the EvpHeader version property
    if gani_const.EVPH_VERSION in action:
        del action[gani_const.EVPH_VERSION]

    Debug.log(f"Cleared {len(keys_to_remove)} event property/properties and {len(markers_to_remove)} marker(s) from action '{action.name}'")
