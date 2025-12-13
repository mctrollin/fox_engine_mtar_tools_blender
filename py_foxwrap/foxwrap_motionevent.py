"""
Utilities for storing and reading motion events in Blender.

This module provides functions to store motion events from MTAR files as:
1. Custom properties on actions (for complete data preservation)
2. NLA markers (for visual timeline representation)
"""
from typing import List, Dict, Optional, TYPE_CHECKING

from ..py_utilities.utilities_logging import Debug
from .foxwrap_metadata import make_event_property_key, iter_event_properties

from ..py_fox.fox_gani_types import EvpHeader, EvpData, EventUnitInfo, TimeSection
from ..py_fox.fox_misc_types import StrCode32

if TYPE_CHECKING:
    import bpy


def store_motion_events_on_action(action: 'bpy.types.Action', motion_events: Optional[EvpHeader]) -> None:
    """Store motion events as custom properties and NLA markers on an action.
    
    Creates two NLA markers per event (start and end) in the format:
    - <event_name>_start at start_frame
    - <event_name>_end at end_frame
    
    Stores event parameters as custom properties in format:
    @event <category>_<event_name> : ints=<i1,i2> ; floats=<f1,f2> ; strings=<s1,s2>
    
    Args:
        action: The Blender action to store motion events on
        motion_events: EvpHeader containing all motion event data, or None
    """
    if not motion_events or motion_events.count == 0:
        return
    
    Debug.log(f"Storing {motion_events.count} motion event categor(ies) on action '{action.name}'")
    
    # Store version as a custom property
    action["motion_events_version"] = motion_events.version
    action.id_properties_ui("motion_events_version").update(
        description="EvpHeader version from MTAR file"
    )
    
    event_index = 0
    
    for category_data in motion_events.data:
        category_name = str(category_data.category_name)
        Debug.log(f"  Category '{category_name}': {category_data.unit_count} event(s)")
        
        for event in category_data.events:
            event_name = str(event.name)
            
            # Build parameter strings
            params_parts = []
            
            # Integer parameters
            if event.int_params:
                ints_str = ','.join(str(i) for i in event.int_params)
                params_parts.append(f"ints={ints_str}")
            
            # Float parameters
            if event.float_params:
                floats_str = ','.join(str(f) for f in event.float_params)
                params_parts.append(f"floats={floats_str}")
            
            # String parameters (stored as uint64 hashes)
            if event.string_params:
                strings_str = ','.join(str(s) for s in event.string_params)
                params_parts.append(f"strings={strings_str}")
            
            # Build format string (TIME_SECTION_FORMAT)
            format_str = f"format={event.format}"
            params_parts.append(format_str)
            
            # Build final @event property value
            metadata_value = f"@event {category_name}_{event_name}"
            if params_parts:
                metadata_value += f" : {' ; '.join(params_parts)}"
            
            # Store as custom property using standardized key format
            property_key = make_event_property_key(event_index, category_name)
            action[property_key] = metadata_value
            
            # Set custom property metadata for UI display
            action.id_properties_ui(property_key).update(
                description=f"Motion event: {category_name}_{event_name}"
            )
            
            # Create NLA markers for each time section
            for section_idx, time_section in enumerate(event.time_sections):
                start_frame = time_section.start_frame
                end_frame = time_section.end_frame
                
                # Handle infinite time sections (start < 0)
                if start_frame < 0:
                    Debug.log(f"    Warning: Skipping infinite time section for event '{event_name}' (start_frame={start_frame})")
                    continue
                
                # Create marker name: <category>_<event_name> (include section index if multiple)
                base_marker_name = f"{category_name}_{event_name}"
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


def read_motion_events_from_action(action: 'bpy.types.Action') -> Optional[EvpHeader]:
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
    
    for event_idx, category_name, metadata_str in event_properties_list:
        if not isinstance(metadata_str, str) or not metadata_str.startswith('@event'):
            continue
        
        # Parse @event format: @event <category>_<event_name> : ints=... ; floats=... ; strings=... ; format=...
        rest = metadata_str[len('@event'):].strip()
        
        if ':' in rest:
            name_part, params_part = rest.split(':', 1)
            name_part = name_part.strip()
            params_part = params_part.strip()
        else:
            name_part = rest.strip()
            params_part = ""
        
        # Split category_eventname
        if '_' not in name_part:
            continue
        
        parts = name_part.split('_', 1)
        category_name = parts[0]
        event_name = parts[1] if len(parts) > 1 else name_part
        
        # Parse parameters
        int_params = []
        float_params = []
        string_params = []
        format_type = 0
        
        if params_part:
            for param in params_part.split(';'):
                param = param.strip()
                if not param or '=' not in param:
                    continue
                key, value = param.split('=', 1)
                key = key.strip()
                value = value.strip()
                
                if key == 'ints' and value:
                    int_params = [int(i.strip()) for i in value.split(',') if i.strip()]
                elif key == 'floats' and value:
                    float_params = [float(f.strip()) for f in value.split(',') if f.strip()]
                elif key == 'strings' and value:
                    string_params = [int(s.strip()) for s in value.split(',') if s.strip()]
                elif key == 'format' and value:
                    format_type = int(value)
        
        # Find corresponding markers to get frame ranges
        # Look for markers matching pattern: 
        # - <category>_<event_name>_start and _end (frame range)
        # - <category>_<event_name> (single frame, infinite end)
        # (or with section index: <category>_<event_name>_<idx>_start / <category>_<event_name>_<idx>)
        time_sections = []
        
        # Build base marker name
        base_marker_name = f"{category_name}_{event_name}"
        
        # Find all matching marker pairs or single markers
        section_idx = 0
        while True:
            # Prefer index-suffixed markers: <base>_<idx>_start / _end / <base>_<idx>
            start_marker_name_idx = f"{base_marker_name}_{section_idx}_start"
            end_marker_name_idx = f"{base_marker_name}_{section_idx}_end"
            single_marker_name_idx = f"{base_marker_name}_{section_idx}"

            start_marker = action.pose_markers.get(start_marker_name_idx)
            end_marker = action.pose_markers.get(end_marker_name_idx)
            single_marker = action.pose_markers.get(single_marker_name_idx)

            # Fallback for section_idx == 0: try non-indexed marker names (single-section writer case)
            if section_idx == 0 and not (start_marker or end_marker or single_marker):
                start_marker_name = f"{base_marker_name}_start"
                end_marker_name = f"{base_marker_name}_end"
                single_marker_name = base_marker_name

                start_marker = action.pose_markers.get(start_marker_name)
                end_marker = action.pose_markers.get(end_marker_name)
                single_marker = action.pose_markers.get(single_marker_name)
            
            if start_marker and end_marker:
                # Found a start/end pair
                time_sections.append(TimeSection(
                    start_frame=int(start_marker.frame),
                    end_frame=int(end_marker.frame)
                ))
                section_idx += 1
            elif single_marker:
                # Found a single marker (infinite end)
                time_sections.append(TimeSection(
                    start_frame=int(single_marker.frame),
                    end_frame=-1
                ))
                section_idx += 1
            else:
                # No more sections found
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
            event_unit = EventUnitInfo(
                name=StrCode32.from_string(event_name),
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
    version = action.get("motion_events_version", 201207030)
    
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
