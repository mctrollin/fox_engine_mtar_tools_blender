"""
External hash generator tool for Metal Gear Solid V animation tools.

Provides functionality to convert filenames to hashes using an external executable.
Supports multiple hash operations: filename, extension, with extension, and legacy.
"""
import subprocess
from typing import Dict, Tuple
from pathlib import Path

import bpy

from ..py_utilities.utilities_logging import Debug


def resolve_executable_path(exe_path: str) -> Path:
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


def hash_filename_all_modes(exe_path: str, 
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
    if not exe_path:
        return False, {}, "No executable path specified"

    # Resolve the executable path (handle Blender relative paths like //path)
    exe_file = resolve_executable_path(exe_path)
    Debug.log(f"Resolved executable path: {exe_file}")

    if not exe_file.exists():
        return False, {}, f"Executable not found: {exe_file}"

    if not exe_file.is_file():
        return False, {}, f"Path is not a file: {exe_file}"
    
    # Validate input
    if not input_string:
        return False, {}, "No input string provided"
    
    Debug.log(f"Hashing with external exe: {exe_path}")
    Debug.log(f"  Input: {input_string}")
    
    results = {
        'filename': '',
        'extension': '',
        'with_extension': '',
        'legacy': ''
    }
    
    # Parse input to extract filename and extension
    input_path = Path(input_string)
    filename_only = input_path.stem if input_path.suffix else input_string
    extension_only = input_path.suffix.lstrip('.') if input_path.suffix else ''
    
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
                # Attempt to parse a decimal value from the output (support hex like 0x... or plain decimal)
                dec_value = None
                try:
                    token = output.split()[0]
                    # Try automatic base detection (0x for hex) then fall back to decimal
                    try:
                        dec_value = int(token, 0)
                    except ValueError:
                        # Try explicit hex without 0x
                        try:
                            dec_value = int(token, 16)
                        except ValueError:
                            dec_value = None
                    if dec_value is not None:
                        results[f"{operation_name}_dec"] = str(dec_value)
                    else:
                        results[f"{operation_name}_dec"] = ""
                except Exception:
                    results[f"{operation_name}_dec"] = ""
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


def validate_executable_path(exe_path: str) -> Tuple[bool, str]:
    """Validate that the executable path exists and is accessible.
    
    Args:
        exe_path: Path to validate
        
    Returns:
        Tuple of (is_valid: bool, error_message: str)
    """
    if not exe_path:
        return False, "No path specified"

    exe_file = resolve_executable_path(exe_path)
    Debug.log(f"Validating executable path: {exe_file}")

    if not exe_file.exists():
        return False, f"File does not exist: {exe_file}"

    if not exe_file.is_file():
        return False, f"Path is not a file: {exe_file}"

    # Check if it's an executable (has .exe extension on Windows)
    if exe_file.suffix.lower() not in ['.exe', '.bat', '.cmd', '']:
        return False, f"Not a recognized executable type: {exe_file.suffix}"

    return True, ""


def hash_animation_name_from_blender_context(input_string: str) -> Tuple[bool, Dict[str, str], str]:
    """Hash animation name using the hash generator executable path configured in Blender.

    Wrapper around hash_filename_all_modes() that retrieves the hash generator exe path from the
    main scene settings (`context.scene.mtar_properties.settings_props.hash_generator_exe_path`).
    
    Args:
        input_string: Animation name string to hash (e.g., "/Assets/tpp/Walk/walk_001")
        
    Returns:
        Tuple of (success: bool, results: Dict[str, str], error: str)
        - success: True if at least one hash succeeded
        - results: Dictionary with keys: 'filename', 'extension', 'with_extension', 'legacy'
        - error: Error message if all conversions failed
    """
    try:
        # Get converter properties from the scene
        if not hasattr(bpy.data, 'scenes') or not bpy.context.scene:
            return False, {}, "No active Blender scene"
        scene = bpy.context.scene
        # Retrieve the executable path from the main settings (no backward compatibility)
        if not hasattr(scene, 'mtar_properties') or not scene.mtar_properties.settings_props.hash_generator_exe_path:
            return False, {}, "MTAR property 'hash_generator_exe_path' not found in scene settings"
        exe_path = scene.mtar_properties.settings_props.hash_generator_exe_path
        if not exe_path:
            return False, {}, "Hash Generator executable path not set in properties"
        
        # Call the main hash function
        return hash_filename_all_modes(exe_path, input_string)
        
    except ImportError:
        return False, {}, "Blender not available (not running inside Blender)"
    except Exception as e:
        return False, {}, f"Error accessing Blender properties: {str(e)}"
