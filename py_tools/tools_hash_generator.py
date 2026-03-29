"""
External hash generator tool for Metal Gear Solid V animation tools.

Provides functionality to convert filenames to hashes using an external executable
or pure Python CityHash implementation. Supports multiple hash operations: filename,
extension, with extension, and legacy.
"""
import os
import subprocess
from typing import Dict, Tuple
from pathlib import Path

import bpy

from ..py_core.core_logging import Debug

from ..py_utilities import util_hashing_cityhash


def get_dictionary_folders() -> Tuple[str, str]:
    """Return configured dictionary folders from addon prefs with fallback defaults."""
    default_path64 = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dic", "path64")
    default_str32 = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dic", "str32")

    try:
        addon = bpy.context.preferences.addons.get("fox_engine_mtar_tools_blender")
        if addon is not None:
            prefs = addon.preferences
            path64 = getattr(prefs, 'path64_dictionary_folder', '')
            str32 = getattr(prefs, 'str32_dictionary_folder', '')
            if path64:
                default_path64 = path64
            if str32:
                default_str32 = str32
    except Exception:
        pass

    return default_path64, default_str32


def _parse_input_name_and_ext(input_string: str) -> Tuple[str, str]:
    """Return filename (no extension) and extension (no leading dot)."""
    if not input_string:
        return "", ""
    p = Path(input_string)
    if p.suffix:
        return p.stem, p.suffix.lstrip('.')
    return input_string, ""


def _make_hash_result_template() -> Dict[str, str]:
    return {
        'filename': '',
        'filename_dec': '',
        'extension': '',
        'extension_dec': '',
        'with_extension': '',
        'with_extension_dec': '',
        'legacy': '',
        'legacy_dec': ''
    }


def _try_parse_decimal_value(raw_value: str) -> str:
    if not raw_value:
        return ''
    token = raw_value.strip().split()[0]
    try:
        return str(int(token, 0))
    except ValueError:
        try:
            return str(int(token, 16))
        except ValueError:
            return ''


def hash_filename_all_modes(input_string: str, modes: tuple = None) -> Tuple[bool, Dict[str, str], str]:
    """Hash filename using specified modes with pure Python CityHash.
    
    Computes only the requested hash operations without needing external executable:
    - 'filename': Hash Filename (-d -h <filename>)
    - 'extension': Hash Extension (-d -he <extension>)
    - 'with_extension': Hash With Extension (-d -hwe <filename.ext>)
    - 'legacy': Hash Legacy (-d -hl <filename>)
    
    Args:
        input_string: Input filename (with or without extension)
        modes: Tuple of mode names to compute (e.g., ('with_extension',) or ('filename', 'extension', 'with_extension', 'legacy')).
               If None, computes all modes for backward compatibility.
        
    Returns:
        Tuple of (success: bool, results: Dict[str, str], error: str)
        - success: True (always succeeds with Python implementation)
        - results: Dictionary with keys for requested modes plus corresponding '_dec' keys with decimal representations
        - error: Empty string (no errors with Python implementation)
    """
    if not input_string:
        return False, {}, "No input string provided"
    
    # Default to all modes for backward compatibility
    if modes is None:
        modes = ('filename', 'extension', 'with_extension', 'legacy')
    
    results = _make_hash_result_template()
    filename_only, extension_only = _parse_input_name_and_ext(input_string)
    
    try:
        # Hash Filename: -d -h <filename>
        if 'filename' in modes:
            hash_val = util_hashing_cityhash.hash_file_name(filename_only, remove_extension=False)
            results['filename'] = f"0x{hash_val:016X}"
            results['filename_dec'] = str(hash_val)
            Debug.log(f"  filename (-d -h {filename_only}): 0x{hash_val:016X}")
        
        # Hash Extension: -d -he <extension> (only if extension exists and requested)
        if 'extension' in modes and extension_only:
            hash_val = util_hashing_cityhash.hash_file_extension(extension_only)
            results['extension'] = f"0x{hash_val:016X}"
            results['extension_dec'] = str(hash_val)
            Debug.log(f"  extension (-d -he {extension_only}): 0x{hash_val:016X}")
        
        # Hash With Extension: -d -hwe <filename.ext>
        if 'with_extension' in modes:
            hash_val = util_hashing_cityhash.hash_file_name_with_ext(input_string)
            results['with_extension'] = f"0x{hash_val:016X}"
            results['with_extension_dec'] = str(hash_val)
            Debug.log(f"  with_extension (-d -hwe {input_string}): 0x{hash_val:016X}")
        
        # Hash Legacy: -d -hl <filename>
        if 'legacy' in modes:
            hash_val = util_hashing_cityhash.hash_file_name_legacy(input_string)
            results['legacy'] = f"0x{hash_val:016X}"
            results['legacy_dec'] = str(hash_val)
            Debug.log(f"  legacy (-d -hl {input_string}): 0x{hash_val:016X}")
        
        return True, results, ""
        
    except Exception as e:
        error_msg = f"Python hash failed: {str(e)}"
        Debug.log_error(f"  {error_msg}")
        return False, results, error_msg


def hash_animation_name_from_blender_context(input_string: str) -> Tuple[bool, Dict[str, str], str]:
    """Hash animation name using pure Python CityHash (no external executable).

    Pure-Python wrapper around hash_filename_all_modes_python(). Optimized for export by only
    computing the 'with_extension' mode, which is all that export path hashing needs.
    
    Args:
        input_string: Animation name string to hash (e.g., "/Assets/tpp/Walk/walk_001")
        
    Returns:
        Tuple of (success: bool, results: Dict[str, str], error: str)
        - success: True if hashing succeeded
        - results: Dictionary with 'with_extension_dec' key containing the hash value
        - error: Error message if hashing failed
    """
    try:
        # Only compute 'with_extension' mode for export optimization
        return hash_filename_all_modes(input_string, modes=('with_extension',))
    except Exception as e:
        return False, {}, f"Error in Python hash: {str(e)}"


def build_gani_hash_dictionary(dictionary_path: str = None) -> Dict[int, str]:
    """Build a GANI path hash dictionary from dic/path64/mtar_dictionary.txt using pure Python CityHash.

    Replaces the external executable approach with a pure-Python CityHash v1.0.3
    implementation, making it dramatically faster and cross-platform.

    For each path in the dictionary file, appends '.gani' and computes the 64-bit hash
    using hash_file_name_with_ext, then maps hash → path in the result.

    Each phase is wrapped in a timing log so the cost of reading vs. hashing is visible.

    Args:
        dictionary_path: Optional path to mtar_dictionary.txt. If not provided, uses addon preferences or default.

    Returns:
        Dict mapping 64-bit hash integer to asset path string (same format as
        load_gani_hash_dictionary and build_gani_hash_dictionary_from_exe, i.e.
        {hash_int: "/Assets/..."})
    """
    result: Dict[int, str] = {}

    dict_file = Path(dictionary_path)
    if not dict_file.exists():
        Debug.log_warning(f"GANI dictionary not found: {dictionary_path}")
        return result

    # Phase 1: read the plain-path dictionary file
    Debug.start_timer("Build GANI hash dict: read file")
    try:
        paths = [
            line.strip()
            for line in dict_file.read_text(encoding='utf-8').splitlines()
            if line.strip()
        ]
    except OSError as e:
        Debug.log_error(f"Failed to read dictionary file: {e}")
        Debug.stop_timer("Build GANI hash dict: read file")
        return result
    Debug.stop_timer("Build GANI hash dict: read file")
    Debug.log(f"  Read {len(paths)} paths from '{dictionary_path}'")

    # Phase 2: hash every path with pure-Python CityHash64
    Debug.start_timer("Build GANI hash dict: hash generation")
    failed = 0
    for path in paths:
        path_with_ext = f"{path}.gani"
        try:
            hash_value = util_hashing_cityhash.hash_file_name_with_ext(path_with_ext)
            result[hash_value] = path
        except Exception:
            failed += 1
    Debug.stop_timer("Build GANI hash dict: hash generation")
    Debug.log(f"  Built {len(result)} hash entries ({failed} failed) from '{dictionary_path}'")
    return result


def build_event_hash_dictionary(dictionary_path: str = None) -> Dict[int, str]:
    """Build an event name hash dictionary from events_dictionary.txt using StrCode32 hashing.

    Reads plain event names from the dictionary file and computes StrCode32 hashes,
    creating a hash → name lookup table for animation event identification.

    For each event name in the dictionary file, computes its StrCode32 hash and
    maps hash → name in the result dict. This enables runtime lookup of event names
    by their binary hash values without maintaining hardcoded enum definitions.

    Args:
        dictionary_path: Optional path to events_dictionary.txt. If not provided, uses addon preferences or default.

    Returns:
        Dict mapping StrCode32 hash (32-bit int) to event name string
        (e.g., {312449893: "FX_CREATE_EFFECT_WITH_SKL", ...})
    """
    result: Dict[int, str] = {}

    if not dictionary_path:
        _, str32_folder = get_dictionary_folders()
        dictionary_path = os.path.join(str32_folder, "events_dictionary.txt")

    dict_file = Path(dictionary_path)
    if not dict_file.exists():
        Debug.log_warning(f"Event dictionary not found: {dictionary_path}")
        return result

    # Phase 1: read the plain-text event name dictionary file
    Debug.start_timer("Build event hash dict: read file")
    try:
        event_names = [
            line.strip()
            for line in dict_file.read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.strip().startswith('#')  # skip blank lines and comments
        ]
    except Exception as e:
        Debug.log_warning(f"Failed to read event dictionary: {e}")
        return result
    Debug.stop_timer("Build event hash dict: read file")
    Debug.log(f"  Read {len(event_names)} event names from '{dictionary_path}'")

    # Phase 2: hash every event name with StrCode32
    Debug.start_timer("Build event hash dict: hash generation")
    failed = 0
    for event_name in event_names:
        try:
            hash_value = util_hashing_cityhash.strcode32(event_name)
            result[hash_value] = event_name
        except Exception:
            failed += 1
    Debug.stop_timer("Build event hash dict: hash generation")
    Debug.log(f"  Built {len(result)} event hash entries ({failed} failed) from '{dictionary_path}'")
    return result



# External Hash Generator (only for debug purposes) ####################################################


def _resolve_external_generator_executable_path(exe_path: str) -> Path:
    """Resolve executable path handling Blender relative paths and user paths.

    This will attempt to use Blender's path resolution (bpy.path.abspath) when
    running inside Blender, expand user (~) and also try resolving relative to
    the current .blend file directory if provided.
    """
    # Prefer Blender's resolver when available (handles // paths)
    # bpy.path.abspath will convert Blender's // paths to absolute paths
    abs_path = bpy.path.abspath(exe_path)

    # If path is not absolute, attempt to resolve relative to blend file dir
    try:
        p = Path(abs_path)
        if not p.is_absolute():
            try:
                blend_fp = bpy.data.filepath
                if blend_fp:
                    blend_dir = Path(blend_fp).parent
                    candidate = (blend_dir / p).resolve()
                    if candidate.exists():
                        return candidate
            except Exception:
                # ignore and proceed
                pass

        return p.resolve()
    except Exception:
        return Path(abs_path)


def validate_executable_path_by_external_generator(hash_generator_exe_path: str) -> Tuple[bool, str]:
    """Validate that the executable path exists and is accessible.
    
    Args:
        exe_path: Path to validate
        
    Returns:
        Tuple of (is_valid: bool, error_message: str)
    """
    if not hash_generator_exe_path:
        return False, "No path specified"

    exe_file = _resolve_external_generator_executable_path(hash_generator_exe_path)
    Debug.log(f"Validating executable path: {exe_file}")

    if not exe_file.exists():
        return False, f"File does not exist: {exe_file}"

    if not exe_file.is_file():
        return False, f"Path is not a file: {exe_file}"

    # Check if it's an executable (has .exe extension on Windows)
    if exe_file.suffix.lower() not in ['.exe', '.bat', '.cmd', '']:
        return False, f"Not a recognized executable type: {exe_file.suffix}"

    return True, ""


def hash_filename_all_modes_by_external_generator(hash_generator_exe_path: str, 
                            input_string: str,
                            timeout: int = 30) -> Tuple[bool, Dict[str, str], str]:
    """Hash filename using all available modes.
    
    Runs the hash executable with multiple operations:
    - Hash Filename: -d -h <filename>
    - Hash Extension: -d -he <extension>
    - Hash With Extension: -d -hwe <filename.ext>
    - Hash Legacy: -d -hl <filename>
    
    Args:
        exe_path: Path to the hash executable
        input_string: Input filename (with or without extension)
        timeout: Timeout in seconds per operation (default: 30)
        
    Returns:
        Tuple of (success: bool, results: Dict[str, str], error: str)
        - success: True if at least one hash succeeded
        - results: Dictionary with keys: 'filename', 'extension', 'with_extension', 'legacy'
        - error: Error message if all conversions failed
    """
    # Validate exe path
    if not hash_generator_exe_path:
        return False, {}, "No executable path specified"

    # Resolve the executable path (handle Blender relative paths like //path)
    exe_file = _resolve_external_generator_executable_path(hash_generator_exe_path)
    Debug.log(f"Resolved executable path: {exe_file}")

    if not exe_file.exists():
        return False, {}, f"Executable not found: {exe_file}"

    if not exe_file.is_file():
        return False, {}, f"Path is not a file: {exe_file}"
    
    # Validate input
    if not input_string:
        return False, {}, "No input string provided"
    
    Debug.log(f"Hashing with external exe: {hash_generator_exe_path}")
    Debug.log(f"  Input: {input_string}")
    
    results = _make_hash_result_template()
    filename_only, extension_only = _parse_input_name_and_ext(input_string)
    
    # Define hash operations
    operations = []
    
    # Hash Filename: -d -h <filename>
    operations.append(('filename', [str(exe_file), '-d', '-h', filename_only]))
    
    # Hash Extension: -d -he <extension> (only if extension exists)
    if extension_only:
        operations.append(('extension', [str(exe_file), '-d', '-he', extension_only]))
    
    # Hash With Extension: -d -hwe <filename.ext>
    operations.append(('with_extension', [str(exe_file), '-d', '-hwe', input_string]))
    
    # Hash Legacy: -d -hl <filename>
    operations.append(('legacy', [str(exe_file), '-d', '-hl', input_string]))
    
    # Run all operations
    any_success = False
    errors = []
    
    for operation_name, command in operations:
        try:
            Debug.log(f"  Running: {' '.join(command[1:])}")
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                check=False
            )
            
            if result.returncode == 0:
                output = result.stdout.strip()
                results[operation_name] = output
                results[f"{operation_name}_dec"] = _try_parse_decimal_value(output)
                any_success = True
                Debug.log(f"    {operation_name}: {output}")
            else:
                error = result.stderr.strip()
                error_msg = error if error else f"Process exited with code {result.returncode}"
                results[operation_name] = f"Error: {error_msg}"
                errors.append(f"{operation_name}: {error_msg}")
                Debug.log_error(f"    {operation_name} failed: {error_msg}")
                
        except subprocess.TimeoutExpired:
            error_msg = f"Timed out after {timeout} seconds"
            results[operation_name] = f"Error: {error_msg}"
            results[f"{operation_name}_dec"] = ""
            errors.append(f"{operation_name}: {error_msg}")
            Debug.log_error(f"    {operation_name}: {error_msg}")
            
        except Exception as e:
            error_msg = f"Failed to run: {str(e)}"
            results[operation_name] = f"Error: {error_msg}"
            results[f"{operation_name}_dec"] = ""
            errors.append(f"{operation_name}: {error_msg}")
            Debug.log_error(f"    {operation_name}: {error_msg}")
    
    if any_success:
        # Log each successful/failed result to the debug log for traceability
        Debug.log("Hash results:")
        for k, v in results.items():
            # Use error log if value indicates an error
            if isinstance(v, str) and v.startswith('Error:'):
                Debug.log_error(f"  {k}: {v}")
            else:
                Debug.log(f"  {k}: {v}")

        return True, results, ""
    else:
        combined_error = "; ".join(errors)
        # Log all results (including errors) for debugging
        Debug.log("Hash results (all operations failed):")
        for k, v in results.items():
            Debug.log_error(f"  {k}: {v}")
        return False, results, combined_error
