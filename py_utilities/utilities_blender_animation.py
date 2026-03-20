"""Utilities for working with Blender animation data.

This module contains helper functions for manipulating Blender actions,
FCurves, keyframes, and other animation-related structures.
"""
from contextlib import contextmanager
from typing import Optional, Dict, Generator, List, Iterator, Set, Tuple

import bpy

from ..py_core.core_logging import Debug


# Global constants
MTAR_ARMATURE_SLOT_NAME = 'mtar_import_armature'
MTAR_OBJECT_SLOT_NAME = 'mtar_import_object'
# Blender groups object transform FCurves under 'Object Transforms'.
# Use this when creating object-level animation curves so they match Blender's UI.
BLENDER_OBJECT_TRANSFORMS_GROUP_NAME = 'Object Transforms'

# Dedicated slot name for shader-nodes armatures.  We create actions
# before the shader armature exists and need a separate slot so that
# ensure_action_fcurve can create channelbags without an assigned datablock.
MTAR_SHADER_SLOT_NAME = 'mtar_shader_armature'

# Layout Action Utilities #########################################################

def try_find_layout_track_action() -> Optional[bpy.types.Action]:
    """Find the layout track action in the scene.
    
    Searches for an action with a name containing '.layout.'.
        
    Returns:
        Layout track action if found, None otherwise
    """
    # Search in all actions
    for action in bpy.data.actions:
        # Check for layout track naming pattern
        if '.layout.' in action.name.lower():
            Debug.log(f"  Found layout track action: '{action.name}'")
            return action
    
    return None


# FCurve Cache Utilities #########################################################

def extract_bone_name_from_fcurve_path(data_path: str) -> Optional[str]:
    """Extract bone name from an fcurve data_path.
    
    Handles paths like:
    - pose.bones["BoneName"].rotation_quaternion
    - pose.bones["BoneName"].location
    
    Args:
        data_path: The fcurve's data_path attribute
        
    Returns:
        Bone name if path matches expected format, None otherwise
    """
    if not data_path or not data_path.startswith('pose.bones["'):
        return None
    
    # Extract bone name between pose.bones[" and "]
    try:
        start_idx = data_path.index('pose.bones["') + len('pose.bones["')
        end_idx = data_path.index('"]', start_idx)
        return data_path[start_idx:end_idx]
    except (ValueError, IndexError):
        return None


def extract_property_from_fcurve_path(data_path: str) -> Optional[str]:
    """Extract property name from an fcurve data_path.
    
    Handles paths like:
    - pose.bones["BoneName"].rotation_quaternion → "rotation_quaternion"
    - pose.bones["BoneName"].location → "location"
    
    Args:
        data_path: The fcurve's data_path attribute
        
    Returns:
        Property name if path matches expected format, None otherwise
    """
    if not data_path or '"].' not in data_path:
        return None
    
    # Extract property after "].
    try:
        property_start = data_path.rindex('"].') + 3
        return data_path[property_start:]
    except (ValueError, IndexError):
        return None


# Pose Bone Data Path Utilities ###################################################

def is_pose_bone_data_path(data_path: str) -> bool:
    """Check if data_path is a pose.bones animation path.
    
    Supports both single and double-quoted formats:
    - 'pose.bones["BoneName"].property'
    - "pose.bones['BoneName'].property"
    
    Args:
        data_path: The data_path string to check
        
    Returns:
        True if data_path matches pose.bones format, False otherwise
    """
    if not data_path:
        return False
    return data_path.startswith('pose.bones["') or data_path.startswith("pose.bones['")


def build_data_path_for_bone(bone_name: str, property_name: str, quote_char: str = '"') -> str:
    """Build a standard pose.bones data_path from components.
    
    Args:
        bone_name: Name of the bone
        property_name: Name of the property (e.g., 'location', 'rotation_quaternion')
        quote_char: Quote character to use ('"' or "'"), defaults to double quote
        
    Returns:
        Data path string in format: pose.bones["BoneName"].property
        
    Example:
        >>> build_data_path_for_bone('Armature', 'location')
        'pose.bones["Armature"].location'
    """
    return f'pose.bones[{quote_char}{bone_name}{quote_char}].{property_name}'


def extract_bone_name_from_data_path(data_path: str, armature: Optional[bpy.types.Object] = None) -> Optional[str]:
    """Extract bone name from a pose.bones data_path with auto-detection of quote character.
    
    Supports both single and double-quoted formats with automatic fallback:
    - 'pose.bones["BoneName"].property'
    - "pose.bones['BoneName'].property"
    
    Args:
        data_path: The fcurve's data_path attribute
        armature: Optional armature to validate that extracted bone exists in pose.bones
        
    Returns:
        Bone name if path matches expected format, None if malformed or bone not found
        
    Example:
        >>> extract_bone_name_from_data_path('pose.bones["Armature"].location')
        'Armature'
    """
    if not data_path or not is_pose_bone_data_path(data_path):
        return None
    
    try:
        # Auto-detect quote character (try double quote first, fall back to single)
        quote_char = '"' if '["' in data_path else "'"
        
        # Extract bone name between bracket+quote and quote+bracket
        start = data_path.index('[' + quote_char) + len('[' + quote_char)
        end = data_path.index(quote_char + ']', start)
        bone_name = data_path[start:end]
        
        # Validate bone exists in armature if provided
        if armature and bone_name not in armature.pose.bones:
            return None
        
        return bone_name
    except (ValueError, IndexError):
        return None


def extract_property_name_from_data_path(data_path: str) -> Optional[str]:
    """Extract property name from a pose.bones data_path.
    
    Handles paths like:
    - pose.bones["BoneName"].rotation_quaternion → "rotation_quaternion"
    - pose.bones["BoneName"].location → "location"
    
    Args:
        data_path: The fcurve's data_path attribute
        
    Returns:
        Property name if path matches expected format, None otherwise
        
    Example:
        >>> extract_property_name_from_data_path('pose.bones["Armature"].location')
        'location'
    """
    if not data_path or '"].' not in data_path:
        return None
    
    try:
        # Extract property name after rightmost "].
        property_start = data_path.rindex('"].') + len('"].')
        return data_path[property_start:]
    except (ValueError, IndexError):
        return None


def parse_data_path_components(data_path: str, armature: Optional[bpy.types.Object] = None) -> Optional[tuple]:
    """Parse bone name and property name from pose.bones data_path in one pass.
    
    Optimized for cases where both bone and property extraction is needed.
    Automatically detects quote character and validates bone if armature provided.
    
    Args:
        data_path: The fcurve's data_path attribute
        armature: Optional armature to validate that extracted bone exists
        
    Returns:
        Tuple of (bone_name, property_name) if valid, None if malformed or bone not found
        
    Example:
        >>> parse_data_path_components('pose.bones["Armature"].location')
        ('Armature', 'location')
    """
    if not data_path or not is_pose_bone_data_path(data_path):
        return None
    
    try:
        # Auto-detect quote character
        quote_char = '"' if '["' in data_path else "'"
        
        # Extract bone name
        start = data_path.index('[' + quote_char) + len('[' + quote_char)
        end = data_path.index(quote_char + ']', start)
        bone_name = data_path[start:end]
        
        # Validate bone exists if armature provided
        if armature and bone_name not in armature.pose.bones:
            return None
        
        # Extract property name
        property_start = data_path.index('"].') + len('].')
        property_name = data_path[property_start:]
        
        return (bone_name, property_name)
    except (ValueError, IndexError):
        return None


class FCurveCache:
    """Cache of FCurves indexed by bone name and property name.
    
    This eliminates the need to scan action.fcurves repeatedly for every bone.
    With many fcurves and many bones, this provides 20-100× speedup.
    
    Example usage:
        cache = FCurveCache.build(action)
        fcurves_for_rotation = cache.get_fcurves_for_bone(bone_name, 'rotation_quaternion')
    """
    
    def __init__(self, cache_dict: Optional[Dict[str, Dict[str, List['bpy.types.FCurve']]]] = None):
        """Initialize the FCurve cache.
        
        Args:
            cache_dict: Pre-built cache dictionary, or None for empty cache
        """
        self._cache = cache_dict if cache_dict else {}
    
    @classmethod
    def build(cls, action: bpy.types.Action) -> 'FCurveCache':
        """Build a cache of fcurves indexed by bone name and property name.
        
        Args:
            action: Blender action containing fcurves
            
        Returns:
            FCurveCache instance with all fcurves indexed
        """
        cache_dict: Dict[str, Dict[str, List['bpy.types.FCurve']]] = {}
        
        if not action or not action_has_fcurves(action):
            return cls(cache_dict)
        
        for fcurve in iter_action_fcurves(action):
            bone_name = extract_bone_name_from_fcurve_path(fcurve.data_path)
            if not bone_name:
                # Non-pose-bone path (e.g. custom property or armature object transform).
                # FCurveCache is designed for pose-bone actions; these paths are
                # intentionally not indexed here.
                # Common object-level paths (root motion stored on the armature object)
                # are expected and should not spam warnings.
                if fcurve.data_path in {
                    "location",
                    "rotation_quaternion",
                    "rotation_euler",
                    "scale",
                }:
                    continue

                Debug.log_warning(f"  FCurveCache.build: skipping non-pose-bone path '{fcurve.data_path}'")
                continue

            property_name = extract_property_from_fcurve_path(fcurve.data_path)
            if not property_name:
                continue
            
            # Build nested dict structure
            if bone_name not in cache_dict:
                cache_dict[bone_name] = {}
            if property_name not in cache_dict[bone_name]:
                cache_dict[bone_name][property_name] = []
            
            cache_dict[bone_name][property_name].append(fcurve)
        
        return cls(cache_dict)
    
    def get_fcurves_for_bone(self, bone_name: str, property_name: str) -> List['bpy.types.FCurve']:
        """Get all fcurves for a specific bone and property.
        
        Args:
            bone_name: Name of the bone
            property_name: Name of the property (e.g., 'rotation_quaternion', 'location')
            
        Returns:
            List of matching fcurves (empty list if none found)
        """
        if bone_name not in self._cache:
            return []
        if property_name not in self._cache[bone_name]:
            return []
        return self._cache[bone_name][property_name]
    
    def has_bone(self, bone_name: str) -> bool:
        """Check if cache has fcurves for a bone.
        
        Args:
            bone_name: Name of the bone to check
            
        Returns:
            True if cache has entries for this bone
        """
        return bone_name in self._cache
    
    def get_bones(self) -> List[str]:
        """Get list of all bones in the cache.
        
        Returns:
            List of bone names
        """
        return list(self._cache.keys())
    
    def is_empty(self) -> bool:
        """Check if cache is empty.
        
        Returns:
            True if no bones are cached
        """
        return len(self._cache) == 0
    
    def to_dict(self) -> Dict[str, Dict[str, List['bpy.types.FCurve']]]:
        """Get the underlying cache dictionary.
        
        Useful for passing to functions that expect the raw dict format.
        
        Returns:
            The internal cache dictionary
        """
        return self._cache



def configure_action(action: bpy.types.Action,
                     frame_start: int = 0,
                     frame_end: int = 0,
                     use_fake_user: bool = True,
                     use_frame_range: bool = True) -> None:
    """Configure a Blender action with standard settings.
    
    Sets up the action's frame range, fake user flag, and other common properties.
    
    Args:
        action: The Blender action to configure
        frame_start: Start frame for the action's manual frame range
        frame_end: End frame for the action's manual frame range
        use_fake_user: Whether to enable fake user (prevents deletion when unused)
        use_frame_range: Whether to enable manual frame range
    """
    action.use_fake_user = use_fake_user
    
    if use_frame_range:
        action.frame_start = frame_start
        action.frame_end = frame_end
        action.use_frame_range = True

    if frame_end - frame_start <= 0:
        Debug.log_warning(f"Warning: Invalid frame range: '{frame_end - frame_start}'")


# Action slot handling for Blender 4.4+ #########################################

def assign_action_to_datablock(datablock: bpy.types.ID, action: bpy.types.Action, slot_name: Optional[str] = None) -> None:
    """Assign an Action to a datablock and ensure a slot is selected on Blender >= 4.4.

    If `slot_name` is not provided, a default mapping is used:
      - ARMATURE -> MTAR_ARMATURE_SLOT_NAME
      - otherwise -> MTAR_OBJECT_SLOT_NAME
    A custom slot (e.g. MTAR_SHADER_SLOT_NAME) can be supplied for
    non-standard armatures such as shader‑nodes rigs.

    Args:
        datablock: Any ID datablock that supports animation_data (e.g. an Object/Armature)
        action: The Action to assign
        slot_name: Optional explicit slot name to prefer/create
    """
    if action is None or datablock is None:
        return

    # Ensure animation data exists
    anim_data = getattr(datablock, 'animation_data', None)
    if anim_data is None:
        try:
            anim_data = datablock.animation_data_create()
        except Exception as e:
            Debug.log_warning(f"Could not create animation_data on '{getattr(datablock, 'name', '<unknown>')}': {e}")
            return

    # On Blender 4.4+/5 the slot-based API must be used before (or instead of)
    # the direct anim_data.action assignment, which may be read-only once a slot
    # is active.  Use slots as the primary path; fall back to direct assignment
    # only on older Blender builds that have no slot API.
    if hasattr(anim_data, 'action_slot'):
        # Decide slot name if none provided
        if slot_name is None:
            dtype = getattr(datablock, 'type', None)
            slot_name = MTAR_ARMATURE_SLOT_NAME if dtype == 'ARMATURE' else MTAR_OBJECT_SLOT_NAME

        try:
            slot = get_action_slot(action, slot_name)
        except RuntimeError as e:
            Debug.log_warning(f"Failed to get or create slot '{slot_name}' for action '{getattr(action, 'name', '<unknown>')}': {e}")
            raise

        # Assign the action first.  This prevents the "Cannot set slot without an
        # assigned Action" error when the datablock has no action yet.  On
        # Blender 5 the assignment may be read-only once a slot exists, but the
        # slot setter below will also set the action implicitly.
        try:
            anim_data.action = action
        except Exception:
            pass

        try:
            anim_data.action_slot = slot
        except Exception as e:
            Debug.log_warning(f"Failed to set action slot for '{getattr(datablock, 'name', '<unknown>')}': {e}")
            raise

        # Also attempt to set action again in case the slot setter didn't.
        try:
            anim_data.action = action
        except Exception:
            pass

        try:
            anim_data.last_slot_identifier = slot.identifier
        except Exception:
            pass
        try:
            action.slots.active = slot
        except Exception as e:
            Debug.log_warning(f"Could not set active slot on action '{getattr(action, 'name', '<unknown>')}': {e}")
        Debug.log(f"  Assigned action '{action.name}' to datablock '{getattr(datablock, 'name', '<unknown>')}' using slot '{getattr(slot, 'name_display', '<unknown>')}'")
    else:
        # Legacy Blender (<4.4): direct assignment is the only path
        anim_data.action = action


def remove_action_from_datablock(datablock: bpy.types.ID) -> None:
    """Remove the active action from a datablock and clear action slot information.

    Args:
        datablock: Any ID datablock that supports animation_data
    """
    if datablock is None:
        return

    anim_data = getattr(datablock, 'animation_data', None)
    if not anim_data:
        return

    try:
        # Clear slot and identifier BEFORE clearing the action
        # NOTE: Setting action_slot to None requires an action to be assigned to the datablock.
        # If no action is assigned, setting action_slot will raise an error.
        if hasattr(anim_data, 'action_slot') and anim_data.action:
            try:
                anim_data.action_slot = None
            except Exception as e:
                # Silently catch if it still fails; the priority is clearing the action
                pass
            
        if hasattr(anim_data, 'last_slot_identifier'):
            try:
                anim_data.last_slot_identifier = ''
            except Exception:
                pass
        
        # Now clear the action itself
        anim_data.action = None
        Debug.log(f"Removed action from datablock '{getattr(datablock, 'name', '<unknown>')}' and cleared slot info")
    except Exception as e:
        Debug.log_warning(f"Failed to remove action from datablock '{getattr(datablock, 'name', '<unknown>')}': {e}")


def _create_temporary_export_track_with_strip(
    armature: bpy.types.Object,
    source_track: bpy.types.NlaTrack,
    source_strip: Optional[bpy.types.NlaStrip] = None,
    export_track_name: str = "__mtar_export__",
) -> Tuple[
    Optional[bpy.types.NlaTrack],
    Optional[bpy.types.NlaStrip],
    Optional[bpy.types.NlaTrack],
    Optional[bpy.types.NlaStrip],
]:
    """Create a temporary export NLA track and duplicate a strip into it.

    This is used by :func:`set_nla_solo` to isolate NLA evaluation without
    muting the original track directly.

    Returns:
        (export_track, export_strip, effective_keep_track, effective_keep_strip)
    """
    if armature is None or armature.animation_data is None:
        return None, None, source_track, source_strip

    nla_tracks = getattr(armature.animation_data, 'nla_tracks', None)
    if not nla_tracks:
        return None, None, source_track, source_strip

    try:
        # Remove any stale export track from prior runs
        existing = next((t for t in nla_tracks if getattr(t, 'name', '') == export_track_name), None)
        if existing is not None:
            try:
                nla_tracks.remove(existing)
            except Exception:
                pass

        export_track = nla_tracks.new()
        export_track.name = export_track_name

        # Use the explicitly requested strip if provided, otherwise pick the first strip
        if source_strip is None:
            source_strip = next(iter(getattr(source_track, 'strips', [])), None)

        if source_strip is None:
            # Nothing to duplicate; clean up and bail.
            try:
                nla_tracks.remove(export_track)
            except Exception:
                pass
            return None, None, source_track, source_strip

        export_strip = export_track.strips.new(
            name=getattr(source_strip, 'name', 'export_strip'),
            start=int(source_strip.frame_start),
            action=source_strip.action,
        )

        # Copy key timing/behavior properties
        for attr in (
            'frame_end',
            'action_frame_start',
            'action_frame_end',
            'blend_in',
            'blend_out',
            'extrapolation',
            'use_animated_influence',
            'influence',
            'use_animated_time',
        ):
            if hasattr(source_strip, attr) and hasattr(export_strip, attr):
                try:
                    setattr(export_strip, attr, getattr(source_strip, attr))
                except Exception as e:
                    Debug.log_warning(f"set_nla_solo: failed to copy '{attr}' from source strip: {e}")

        export_strip.mute = False
        return export_track, export_strip, export_track, export_strip
    except Exception as e:
        Debug.log_warning(f"set_nla_solo: failed to create/duplicate export track: {e}")
        return None, None, source_track, source_strip


@contextmanager
def set_nla_solo(
    main_armature: bpy.types.Object,
    keep_track: Optional[bpy.types.NlaTrack] = None,
    keep_strip: Optional[bpy.types.NlaStrip] = None,
    keep_armatures: Optional[List[bpy.types.Object]] = None,
) -> Generator[None, None, None]:
    """Solo NLA evaluation for a specific armature.

    This mutes NLA tracks on *all other* armatures, while allowing the given
    main armature to keep a specific track/strip unmuted.

    To isolate evaluation (and verify whether track muting is sufficient for
    performance), this will also create a temporary export track and duplicate the
    requested strip into it, then disable the original track.

    Args:
        main_armature: The armature currently being exported/baked.
        keep_track: If set, that track is left unmuted on the main armature.
        keep_strip: If set, that strip is left unmuted on the main armature.
        keep_armatures: Additional armatures whose NLA should remain active.
                        (E.g. when baking, both the imported armature and the
                        custom rig should remain active.)

    Yields:
        None. Restores original mute states on exit.
    """
    # yield
    # return
    if keep_armatures is None:
        keep_armatures = []

    # Always keep the main armature active.
    keep_armatures = [main_armature] + keep_armatures
    # Use names rather than object IDs so this can restore state even if Blender
    # swaps out the Python object for the same datablock during evaluation.
    keep_names = {a.name for a in keep_armatures if a is not None}

    # Record original mute states for all relevant armatures (keyed by armature name)
    original_mute_states: Dict[str, Dict[str, bool]] = {}
    armatures_to_manage: List[bpy.types.Object] = []

    for obj in bpy.data.objects:
        if obj.type != 'ARMATURE':
            continue
        armatures_to_manage.append(obj)

    # Ensure the main armature is included (even if it has no NLA tracks)
    if main_armature and main_armature not in armatures_to_manage:
        armatures_to_manage.append(main_armature)

    for obj in armatures_to_manage:
        track_states: Dict[str, bool] = {}
        if obj.animation_data and getattr(obj.animation_data, 'nla_tracks', None):
            for track in obj.animation_data.nla_tracks:
                track_states[track.name] = bool(getattr(track, 'mute', False))
        original_mute_states[obj.name] = track_states

    export_track = None
    original_keep_track = keep_track

    try:
        # Mute all armature tracks that are not in the keep list.
        for obj in armatures_to_manage:
            if obj.name in keep_names:
                continue
            if not obj.animation_data or not getattr(obj.animation_data, 'nla_tracks', None):
                continue
            for track in obj.animation_data.nla_tracks:
                track.mute = True

        # Now solo the main armature's NLA according to keep_track/keep_strip
        if main_armature and main_armature.animation_data and getattr(main_armature.animation_data, 'nla_tracks', None):
            # Resolve parent track if only a strip is provided
            if keep_track is None and keep_strip is not None:
                for track in main_armature.animation_data.nla_tracks:
                    if any(strip is keep_strip for strip in track.strips):
                        keep_track = track
                        break
                if keep_track is None:
                    Debug.log_warning("set_nla_solo: keep_strip provided but could not resolve its parent track")

            # If requested, create a dedicated export track and duplicate the strip.
            # This helps verify whether muting (as opposed to completely isolating the
            # NLA evaluation) is sufficient for performance.
            if keep_track is not None:
                export_track, _, keep_track, keep_strip = _create_temporary_export_track_with_strip(
                    main_armature,
                    keep_track,
                    keep_strip,
                )
                if export_track is not None and original_keep_track is not None:
                    # Prevent the original track from contributing to evaluation.
                    original_keep_track.mute = True

            # Mute everything first (track-level only)
            for track in main_armature.animation_data.nla_tracks:
                track.mute = True

            # Unmute the selected track
            if keep_track is not None:
                keep_track.mute = False

        #raise RuntimeError("Mute NLA tracks test complete")
        yield
    finally:
        # raise RuntimeError("Mute NLA tracks test complete")
        # Restore original mute states for all armatures we touched
        for obj_name, state_map in original_mute_states.items():
            # Objects may have been removed/renamed during the context.
            # Use the stored object name to locate it for restoration.
            obj = bpy.data.objects.get(obj_name)
            if obj is None:
                Debug.log_warning(f"set_nla_solo: object '{obj_name}' no longer exists (skipping restore)")
                continue

            # Restore mute state if this object had NLA tracks
            if obj.animation_data and getattr(obj.animation_data, 'nla_tracks', None):
                for track in obj.animation_data.nla_tracks:
                    # Preserve previous mute state exactly (no special-case tracks)
                    if track.name in state_map:
                        desired_mute = state_map[track.name]
                        track.mute = desired_mute

        # Remove temporary export track if it was created
        if export_track is not None and main_armature and main_armature.animation_data:
            try:
                # Collection membership checks can be picky in Blender; just attempt removal.
                main_armature.animation_data.nla_tracks.remove(export_track)
            except Exception as e:
                Debug.log_warning(f"set_nla_solo: failed to remove temporary export track: {e}")

def get_action_slot(action: bpy.types.Action, slot_name: Optional[str] = None) -> 'bpy.types.ActionSlot':
    """Return or create an Action slot.

    If `slot_name` is provided, this will look for a slot whose `name` or
    `name_display` matches `slot_name` and return it. If not found, it will
    create a new slot with `name=slot_name` and return it. If `slot_name` is
    None, the function follows the legacy behavior: prefer 'Legacy Slot', then
    a slot matching preferred types, then create a 'Legacy Slot'.

    Raises:
        RuntimeError: If the slot cannot be found or created.
    """
    if action is None:
        raise RuntimeError("Action is None when requesting slot")

    # Check if the action has a 'slots' attribute AND if it's a bpy_prop_collection 
    if not hasattr(action, "slots"):
        raise RuntimeError("Action does not expose 'slots' collection")

    slots = getattr(action, 'slots', None)

    # If a specific slot name is requested, prefer that
    if slot_name is not None:
        for s in slots:
            try:
                if getattr(s, 'name', '') == slot_name or getattr(s, 'name_display', '') == slot_name:
                    return s
            except Exception:
                continue
        # Not found → try to create
        try:
            new_slot = action.slots.new(id_type='OBJECT', name=slot_name)
            Debug.log(f"  Created slot '{slot_name}' on action '{action.name}'")
            return new_slot
        except Exception as e:
            raise RuntimeError(f"Could not create slot '{slot_name}' on action '{getattr(action, 'name', '<unknown>')}': {e}")

    # No explicit slot name: legacy behavior
    # Prefer an explicit 'Legacy Slot' if present
    for s in slots:
        try:
            if getattr(s, 'name_display', '') == 'Legacy Slot':
                return s
        except Exception:
            continue

    # Try to pick a slot suitable for armature/object as a best-effort fallback
    preferred_types = ('ARMATURE', 'OBJECT', 'UNSPECIFIED')
    for t in preferred_types:
        for s in slots:
            try:
                if getattr(s, 'target_id_type', None) == t:
                    return s
            except Exception:
                continue


def get_all_fcurves_from_action(action):
    """
    Return all F-Curves from an Action.
    Supports Blender < 4.4 and Blender >= 4.4.
    """
    if action is None:
        return []

    # --- Blender < 4.4 ---
    # Classic F-Curves directly on the action
    if hasattr(action, "fcurves"):
        try:
            return list(action.fcurves)
        except:
            pass

    # --- Blender >= 4.4 ---
    fcurves = []

    if hasattr(action, "layers"):
        for layer in action.layers:
            for strip in layer.strips:
                # Blender 5+: strips expose channelbags (collection), a single channelbag property,
                # or legacy slots. Use `iter_channelbags` helper to handle all of these cases.
                try:
                    for ch in iter_channelbags(strip):
                        try:
                            if ch and hasattr(ch, "fcurves"):
                                fcurves.extend(ch.fcurves)
                        except Exception as e:
                            Debug.log_warning(f"Error iterating channelbag on strip '{getattr(strip, 'name', '<unknown>')}': {e}")
                except Exception as e:
                    Debug.log_warning(f"Error retrieving channelbags from strip '{getattr(strip, 'name', '<unknown>')}': {e}")

    return fcurves


def action_has_fcurves(action: bpy.types.Action) -> bool:
    """Return True if the Action has or can manage fcurves in this Blender API.

    This handles both the older API where `action.fcurves` exists and the
    newer Blender 5.0+ API where fcurves may be managed via channelbags or
    `fcurve_ensure_for_datablock`.
    """
    if action is None:
        return False
    # Old API: direct fcurves collection
    if hasattr(action, 'fcurves'):
        try:
            return len(action.fcurves) > 0
        except Exception as e:
            Debug.log_warning(f"Error counting action.fcurves for action '{getattr(action, 'name', '<unknown>')}': {e}")
            return False

    # Check for channelbag-style API (Blender 5.0+)
    try:
        for _ in iter_channelbags(action):
            # Presence of any channelbag indicates the action can manage fcurves
            return True
    except Exception as e:
        Debug.log_warning(f"Error checking channelbags on action '{getattr(action, 'name', '<unknown>')}': {e}")

    # For Blender 5.0+ also accept fcurve_ensure_for_datablock availability
    if hasattr(action, 'fcurve_ensure_for_datablock'):
        return True

    # Best-effort fallback 
    return False


def is_relevant_strip(strip) -> bool:
    """Return True if a strip represents a GANI (i.e., should be exported/baked).

    A GANI strip is defined as one that:
      - is not None and has an action
      - is not muted (strip.mute is False)
      - its action name does NOT contain 'layout' (case-insensitive)
      - its frame end is not entirely in negative time (strip.action_frame_end or strip.frame_end > 0)

    This consolidates common checks (muted, layout, negative time) in one place
    so call sites do not need to duplicate the logic.
    """
    try:
        if strip is None:
            return False

        # Skip muted strips
        try:
            if getattr(strip, 'mute', False):
                return False
        except Exception:
            pass

        action = getattr(strip, 'action', None)
        if not action:
            return False

        name = getattr(action, 'name', '') or ''
        if 'layout' in name.lower():
            return False

        # Prefer strip.action_frame_end if available, fall back to strip.frame_end
        end = None
        if hasattr(strip, 'action_frame_end') and getattr(strip, 'action_frame_end') is not None:
            try:
                end = int(strip.action_frame_end)
            except Exception:
                end = None
        elif hasattr(strip, 'frame_end') and getattr(strip, 'frame_end') is not None:
            try:
                end = int(strip.frame_end)
            except Exception:
                end = None

        if end is not None and end <= 0:
            return False

        return True
    except Exception:
        # Be conservative: treat unknowns as non-GANI by default
        return False

def iter_channelbags(owner) -> Iterator:
    """Yield channelbag objects using the canonical slot-based API.

    Supports both Action and Strip objects:
    - For Actions: iterates action.layers -> strips, gets slots from action,
      calls strip.channelbag(slot) for each
    - For Strips: gets slots from strip's parent action, calls strip.channelbag(slot)

    Example usage:
        strip = action.layers[0].strips[0]
        channelbag = strip.channelbag(slot)
    """
    if owner is None:
        return

    # Determine if we have an action or a strip
    if hasattr(owner, 'layers'):
        # This is an Action - iterate through layers and strips
        action = owner
        if not hasattr(action, 'slots'):
            return
        
        for layer in action.layers:
            for strip in layer.strips:
                for slot in action.slots:
                    ch = strip.channelbag(slot)
                    if ch:
                        yield ch
    else:
        # This is a Strip - get slots from parent action
        strip = owner
        action = strip.id_data
        if action is None or not hasattr(action, 'slots'):
            return

        for slot in action.slots:
            ch = strip.channelbag(slot)
            if ch:
                yield ch


def iter_action_fcurves(action: bpy.types.Action) -> Iterator['bpy.types.FCurve']:
    """ Return an iterator over all F-Curves in an Action. 
    Uses Python's built-in iter() on the list returned by get_all_fcurves_from_action(). """ 
    return iter(get_all_fcurves_from_action(action))


def find_action_fcurve(action: bpy.types.Action, data_path: str, index: int, slot_name: Optional[str] = None):
    """Find an existing fcurve on `action` matching `data_path` and `index`.

    Returns the FCurve if found, otherwise None.
    """
    if action is None:
        return None

    # Try anim_utils helper first (Blender 5+), which can return the proper
    # channelbag for the currently selected slot (works with layered/slot actions)
    try:
        from bpy_extras import anim_utils # Blender provided helper module
        if hasattr(anim_utils, 'action_get_channelbag_for_slot'):
            slot = get_action_slot(action, slot_name)
            chbag = None
            if slot is not None:
                chbag = anim_utils.action_get_channelbag_for_slot(action, slot)

            if chbag and hasattr(chbag, 'fcurves'):
                try:
                    fc = chbag.fcurves.find(data_path, index=index)
                    if fc:
                        return fc
                except Exception as e:
                    Debug.log_warning(f"chbag.fcurves.find failed for action '{getattr(action, 'name', '<unknown>')}', path '{data_path}', index {index}: {e}")
                try:
                    for fc in chbag.fcurves:
                        if fc.data_path == data_path and getattr(fc, 'array_index', None) == index:
                            return fc
                except Exception as e:
                    Debug.log_warning(f"Iterating chbag.fcurves failed for action '{getattr(action, 'name', '<unknown>')}', path '{data_path}', index {index}: {e}")
    except Exception as e:
        Debug.log_warning(f"anim_utils import unavailable: {e}")
        # anim_utils not available (older Blender) — continue

    # Old API: scan direct fcurves
    if hasattr(action, 'fcurves'):
        try:
            for fc in action.fcurves:
                if fc.data_path == data_path and getattr(fc, 'array_index', None) == index:
                    return fc
        except Exception as e:
            Debug.log_warning(f"Error scanning action.fcurves for action '{getattr(action, 'name', '<unknown>')}': {e}")
            return None

    # Blender 5+: search channelbags / channels collections as a fallback
    try:
        for ch in iter_channelbags(action):
            if not hasattr(ch, 'fcurves'):
                continue
            # Preferred fast-path if the API supports find
            try:
                fc = ch.fcurves.find(data_path, index=index)
                if fc:
                    return fc
            except Exception as e:
                Debug.log_warning(f"ch.fcurves.find failed on channel '{getattr(ch, 'name', '<unknown>')}' for action '{getattr(action, 'name', '<unknown>')}', path '{data_path}', index {index}: {e}")

            # Fallback: linear scan
            try:
                for fc in ch.fcurves:
                    if fc.data_path == data_path and getattr(fc, 'array_index', None) == index:
                        return fc
            except Exception as e:
                Debug.log_warning(f"Iterating ch.fcurves failed on channel '{getattr(ch, 'name', '<unknown>')}' for action '{getattr(action, 'name', '<unknown>')}', path '{data_path}', index {index}: {e}")
                continue
    except Exception as e:
        Debug.log_warning(f"Error iterating channelbags in find_action_fcurve for action '{getattr(action, 'name', '<unknown>')}': {e}")

    return None


def ensure_action_fcurve(action: bpy.types.Action, data_path: str, index: int, datablock=None, action_group_name: Optional[str] = None, slot_name: Optional[str] = None):
    """Return an existing FCurve or create one in a version-safe way.

    If creation is not supported on the current API, returns None.
    """
    if action is None:
        return None

    # Return existing if present
    existing = find_action_fcurve(action, data_path, index, slot_name=slot_name)
    if existing:
        return existing

    # Blender 5+: prefer anim_utils channelbag ensure helper when available
    try:
        from bpy_extras import anim_utils # Blender provided helper module
        if hasattr(anim_utils, 'action_ensure_channelbag_for_slot'):
            try:
                try:
                    slot = get_action_slot(action, slot_name)
                except RuntimeError as e:
                    Debug.log_warning(f"Could not obtain slot '{slot_name}' for action '{getattr(action, 'name', '<unknown>')}': {e}")
                    slot = None

                # Prefer signature with (action, slot, datablock) when datablock is provided
                chbag = None
                if slot is not None and datablock is not None:
                    try:
                        chbag = anim_utils.action_ensure_channelbag_for_slot(action, slot, datablock)
                    except Exception:
                        try:
                            chbag = anim_utils.action_ensure_channelbag_for_slot(action, slot)
                        except Exception as e:
                            Debug.log_warning(f"action_ensure_channelbag_for_slot failed for action '{getattr(action, 'name', '<unknown>')}' with slot and datablock fallbacks: {e}")
                elif slot is not None:
                    try:
                        chbag = anim_utils.action_ensure_channelbag_for_slot(action, slot)
                    except Exception as e:
                        Debug.log_warning(f"action_ensure_channelbag_for_slot failed for action '{getattr(action, 'name', '<unknown>')}' when called with slot: {e}")
                        chbag = None
                else:
                    chbag = None
            except Exception as e:
                Debug.log_warning(f"anim_utils.action_ensure_channelbag_for_slot failed for action '{getattr(action, 'name', '<unknown>')}' when called with legacy slot: {e}")
                chbag = None

            # If caller provided a datablock preference, ensure the returned chbag matches; otherwise continue 
            if chbag and hasattr(chbag, 'fcurves'):
                try:
                    if datablock is not None and getattr(chbag, 'id_data', None) != datablock:
                        Debug.log(f"anim_utils returned a chbag that does not match the requested datablock for action '{getattr(action, 'name', '<unknown>')}'; falling back to other channelbag search")
                    else:
                        if action_group_name is not None:
                            fc = chbag.fcurves.ensure(data_path, index=index, group_name=action_group_name)
                        else:
                            fc = chbag.fcurves.ensure(data_path, index=index)
                        if fc:
                            return fc
                except Exception as e:
                    Debug.log_warning(f"chbag.fcurves.ensure failed for action '{getattr(action, 'name', '<unknown>')}', path '{data_path}', index {index}: {e}")
                    # fallback to new() on the channelbag's fcurves collection
                    try:
                        if hasattr(chbag.fcurves, 'new'):
                            if action_group_name is not None:
                                return chbag.fcurves.new(data_path=data_path, index=index, group_name=action_group_name)
                            return chbag.fcurves.new(data_path=data_path, index=index)
                    except Exception as e:
                        Debug.log_warning(f"chbag.fcurves.new also failed for action '{getattr(action, 'name', '<unknown>')}', path '{data_path}', index {index}: {e}")
    except Exception as e:
        Debug.log_warning(f"anim_utils import/availability error: {e}")
        # anim_utils not available; continue

    # Old API path (direct fcurves)
    if hasattr(action, 'fcurves'):
        try:
            if action_group_name is not None:
                return action.fcurves.new(data_path=data_path, index=index, action_group=action_group_name)
            return action.fcurves.new(data_path=data_path, index=index)
        except Exception as e:
            Debug.log_warning(f"Failed to create fcurve via action.fcurves.new for action '{getattr(action, 'name', '<unknown>')}', path '{data_path}', index {index}: {e}")
            return None

    # Blender 5+: prefer channelbag-style ensure() when available
    try:
        # Prefer channelbag matching provided datablock (if any)
        preferred = []
        for ch in iter_channelbags(action):
            try:
                if datablock is not None and getattr(ch, 'id_data', None) == datablock:
                    preferred.insert(0, ch)
                else:
                    preferred.append(ch)
            except Exception as e:
                Debug.log_warning(f"Error checking channelbag id_data for action '{getattr(action, 'name', '<unknown>')}': {e}")
                preferred.append(ch)

        for ch in preferred:
            if not hasattr(ch, 'fcurves'):
                continue
            try:
                # Use the channelbag's ensure API if present (preferred)
                if action_group_name is not None:
                    fc = ch.fcurves.ensure(data_path, index=index, group_name=action_group_name)
                else:
                    fc = ch.fcurves.ensure(data_path, index=index)
                if fc:
                    return fc
            except Exception as e:
                Debug.log_warning(f"ch.fcurves.ensure failed on channel '{getattr(ch, 'name', '<unknown>')}' for action '{getattr(action, 'name', '<unknown>')}', path '{data_path}', index {index}: {e}")
                # Try a more traditional new() fallback on the fcurves collection
                try:
                    if hasattr(ch.fcurves, 'new'):
                        if action_group_name is not None:
                            return ch.fcurves.new(data_path=data_path, index=index, group_name=action_group_name)
                        return ch.fcurves.new(data_path=data_path, index=index)
                except Exception as e:
                    Debug.log_warning(f"ch.fcurves.new failed on channel '{getattr(ch, 'name', '<unknown>')}' for action '{getattr(action, 'name', '<unknown>')}', path '{data_path}', index {index}: {e}")
                    continue
    except Exception as e:
        Debug.log_warning(f"Error iterating channelbag collections on action '{getattr(action, 'name', '<unknown>')}': {e}")

    # Last resort: Action-level fcurve_ensure_for_datablock (signature varies by Blender build)
    if hasattr(action, 'fcurve_ensure_for_datablock'):
        # Only call this helper if a datablock was explicitly provided; calling with a string (data_path) as first arg
        # can lead to confusing errors where the function expects a datablock ID. 
        if datablock is not None:
            try:
                try:
                    return action.fcurve_ensure_for_datablock(datablock, data_path, index)
                except Exception as e:
                    Debug.log_warning(f"action.fcurve_ensure_for_datablock initial call failed for action '{getattr(action, 'name', '<unknown>')}', path '{data_path}', index {index}: {e}")
                    try:
                        # Sometimes signatures differ; attempt other ordering
                        return action.fcurve_ensure_for_datablock(data_path, index, datablock)
                    except Exception as e2:
                        Debug.log_warning(f"action.fcurve_ensure_for_datablock failed with alternate signature for action '{getattr(action, 'name', '<unknown>')}', path '{data_path}', index {index}: {e2}")
                        return None
            except Exception as e:
                Debug.log_warning(f"action.fcurve_ensure_for_datablock calls failed for action '{getattr(action, 'name', '<unknown>')}', path '{data_path}', index {index}: {e}")
        else:
            Debug.log_warning(f"Skipping action.fcurve_ensure_for_datablock for action '{getattr(action, 'name', '<unknown>')}' because no datablock was provided")

    return None


def remove_action_fcurve(action: bpy.types.Action, fcurve) -> None:
    """Remove an FCurve from an action in a version-safe manner."""
    if action is None or fcurve is None:
        return

    # Old API
    if hasattr(action, 'fcurves'):
        try:
            action.fcurves.remove(fcurve)
            return
        except Exception as e:
            Debug.log_warning(f"Failed to remove fcurve from action.fcurves for action '{getattr(action, 'name', '<unknown>')}': {e}")

    # Blender 5+: try to remove from channelbags where it lives
    try:
        for ch in iter_channelbags(action):
            if not hasattr(ch, 'fcurves'):
                continue
            try:
                ch.fcurves.remove(fcurve)
                return
            except Exception as e:
                Debug.log_warning(f"ch.fcurves.remove failed on channel '{getattr(ch, 'name', '<unknown>')}' for action '{getattr(action, 'name', '<unknown>')}': {e}")
                # Try identity-based search and remove
                try:
                    to_remove = None
                    for fc in ch.fcurves:
                        if fc == fcurve or (getattr(fc, 'data_path', None) == getattr(fcurve, 'data_path', None) and getattr(fc, 'array_index', None) == getattr(fcurve, 'array_index', None)):
                            to_remove = fc
                            break
                    if to_remove is not None:
                        ch.fcurves.remove(to_remove)
                        return
                except Exception as e:
                    Debug.log_warning(f"Identity-based ch.fcurves remove search failed on channel '{getattr(ch, 'name', '<unknown>')}' for action '{getattr(action, 'name', '<unknown>')}': {e}")
                    continue
    except Exception as e:
        Debug.log_warning(f"Error iterating channelbag collections while removing fcurve on action '{getattr(action, 'name', '<unknown>')}': {e}")

    # Best-effort else: nothing to do


def add_dummy_keyframes_to_action(
    action: bpy.types.Action,
    frames: Optional[list[float]] = None,
) -> None:
    """Add dummy location keyframes to a layout‑track action.

    The function creates a baseline reference so the action is never empty and
    therefore suitable for use in an NLA strip.  By default two keyframes are
    inserted at ``-100`` and ``-50``; a custom list of frames may be supplied
    to control the range.  The keyframes live on a virtual bone named ``dummy``
    (``pose.bones["dummy"].location``) which is ignored during export.

    Args:
        action: The layout track action to add keyframes to.
        frames: Optional list of frame numbers at which to insert dummy keys.
            If ``None`` the default ``[-100.0, -50.0]`` range is used.
    """
    Debug.log(f"Adding dummy location keyframes to layout action '{action.name}'")

    # Use provided frames or fall back to legacy values
    if frames is None:
        frames = [-100.0, -50.0]

    # Create a single dummy location track on a virtual bone named "dummy"
    data_path = 'pose.bones["dummy"].location'
    values = [0.0, 0.0, 0.0]

    # Group name for the dummy bone (creation-time grouping is attempted when supported)
    group_name = "dummy"

    # Create FCurve(s) for each component (X, Y, Z)
    for component_idx, value in enumerate(values):
        fcurve = ensure_action_fcurve(
            action,
            data_path=data_path,
            index=component_idx,
            action_group_name=group_name,
            slot_name=MTAR_ARMATURE_SLOT_NAME,
        )
        # Add keyframes at all requested frames
        for frame in frames:
            keyframe = fcurve.keyframe_points.insert(frame=frame, value=value)
            keyframe.interpolation = 'LINEAR'

    Debug.log(
        f"    Added dummy location keyframes at frames {frames}: (0.0, 0.0, 0.0)"
    )


# #########################################

def get_fcurves_for_bones(action: bpy.types.Action, bone_names: Set[str]) -> List[bpy.types.FCurve]:
    """Get all fcurves for specific bones in an action.
    
    Args:
        action: Action to search
        bone_names: Set of bone names to filter by
        
    Returns:
        List of fcurves that belong to the specified bones
    """
    if not action or not action_has_fcurves(action):
        return []
    
    fcurves: List[bpy.types.FCurve] = []
    for fcurve in iter_action_fcurves(action):
        # Check if data_path references one of the target bones
        # Example: 'pose.bones["BoneName"].location'
        if 'pose.bones[' in fcurve.data_path:
            for bone_name in bone_names:
                if f'pose.bones["{bone_name}"]' in fcurve.data_path:
                    fcurves.append(fcurve)
                    break
    
    return fcurves

def is_fcurve_linear(fcurve: bpy.types.FCurve) -> bool:
    """Check if an fcurve uses only LINEAR interpolation.
    
    Args:
        fcurve: FCurve to check
        
    Returns:
        True if all keyframes use LINEAR interpolation, False otherwise
    """
    if not fcurve.keyframe_points:
        return True
    
    for keyframe in fcurve.keyframe_points:
        if keyframe.interpolation != 'LINEAR':
            return False
    
    return True


# ---------------------------------------------------------------------------
# FCurve keyframe-point utilities (used by root-motion and other tools)
# ---------------------------------------------------------------------------

def collect_keyframe_times(fcurves: List[Optional["bpy.types.FCurve"]]) -> Set[float]:
    """Return the union of all keyframe ``co[0]`` times across *fcurves*."""
    times: Set[float] = set()
    for fc in fcurves:
        if fc is None:
            continue
        for kp in fc.keyframe_points:
            times.add(kp.co[0])
    return times


def set_keypoint_value(
    fc: "bpy.types.FCurve",
    frame: float,
    value: float,
    interpolation: Optional[str] = None,
) -> None:
    """Overwrite the value of the existing keyframe point nearest to *frame*.

    Shifts Bezier handles by the same delta so the curve shape is preserved.
    This is a no-op if no keyframe point is found within ±0.001 frames.

    If *interpolation* is provided, the keyframe point's interpolation is also updated.
    """
    for kp in fc.keyframe_points:
        if abs(kp.co[0] - frame) < 0.001:
            delta = value - kp.co[1]
            kp.co[1] = value
            # Shift handles (stored as absolute y positions) by the same delta
            kp.handle_left = (kp.handle_left[0], kp.handle_left[1] + delta)
            kp.handle_right = (kp.handle_right[0], kp.handle_right[1] + delta)
            if interpolation is not None:
                kp.interpolation = interpolation
            break


def densify_bone_fcurves(
    action: bpy.types.Action,
    bone_name: str,
    target_frame_times: List[float],
) -> int:
    """Insert keyframe points in bone FCurves at *target_frame_times* where missing.

    Evaluates each FCurve at the target time using ``fc.evaluate(t)`` (which reads
    the existing curve value without disturbing the shape) and inserts a new
    keypoint at that time.  This ensures downstream code can overwrite a correct
    value at every target frame rather than silently skipping frames.

    Returns the total number of keyframe points inserted across all channels.
    """
    loc_path = build_data_path_for_bone(bone_name, 'location')
    rot_path = build_data_path_for_bone(bone_name, 'rotation_quaternion')
    fcs = (
        [find_action_fcurve(action, loc_path, i) for i in range(3)]
        + [find_action_fcurve(action, rot_path, i) for i in range(4)]
    )

    inserted = 0
    for fc in fcs:
        if fc is None:
            continue
        for t in target_frame_times:
            already_keyed = any(abs(kp.co[0] - t) < 0.001 for kp in fc.keyframe_points)
            if already_keyed:
                continue
            value = fc.evaluate(t)
            kp = fc.keyframe_points.insert(t, value, options={"FAST"})
            kp.interpolation = 'LINEAR'
            inserted += 1

    for fc in fcs:
        if fc is not None:
            fc.update()

    return inserted


def delete_bone_fcurves(action: bpy.types.Action, bone_name: str) -> int:
    """Delete all location and rotation_quaternion FCurves for *bone_name*.

    Returns the number of FCurves removed.
    """
    loc_path = build_data_path_for_bone(bone_name, 'location')
    rot_path = build_data_path_for_bone(bone_name, 'rotation_quaternion')
    removed = 0

    for path, count in ((loc_path, 3), (rot_path, 4)):
        for i in range(count):
            fc = find_action_fcurve(action, path, i)
            if fc is not None:
                remove_action_fcurve(action, fc)
                removed += 1

    return removed


def prune_action_fcurves_to_frames(
    action: bpy.types.Action,
    data_path: str,
    indices: List[int],
    keep_frames: Set[int],
    slot_name: Optional[str] = None,
) -> None:
    """Remove keyframes from an action's FCurves that fall outside *keep_frames*.

    The *slot_name* parameter ensures we operate on the same channelbag where
    the FCurves were written (important for Blender 4.4+ channelbag/slot API).

    Safe approach: snapshot kept values, clear, re-insert to avoid iterator
    invalidation caused by Blender's C-level keyframe array shifting indices on
    removal.
    """
    for i in indices:
        fc = find_action_fcurve(action, data_path, i, slot_name=slot_name)
        if fc is None:
            continue

        kept_keypoints = [
            (kp.co[0], kp.co[1], kp.interpolation)
            for kp in fc.keyframe_points
            if int(round(kp.co[0])) in keep_frames
        ]

        fc.keyframe_points.clear()
        for t, v, interp in kept_keypoints:
            new_kp = fc.keyframe_points.insert(t, v, options={"FAST"})
            new_kp.interpolation = interp
        fc.update()