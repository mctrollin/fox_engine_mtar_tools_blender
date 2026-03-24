"""
Hash debug operators for MTAR tools.

This module contains operator classes for hash generation and unhash
lookup utilities (PathCode64, StrCode32).
"""

# pyright: reportInvalidTypeForm=false

from typing import Dict, Optional, Set
import os

import bpy
from bpy.types import Operator, Context
from bpy.props import StringProperty

from .py_core.core_logging import Debug
from .py_tools import tools_hash_generator
from .py_utilities import util_hashing_cityhash, util_hashing
from .py_fox.fox_mtar_constants import TABL_PATH


_path64_dict_cache: Optional[Dict[int, str]] = None


def _get_path64_dict() -> Dict[int, str]:
    """Build (or return cached) PathCode64 hash -> path dictionary."""
    global _path64_dict_cache
    if _path64_dict_cache is None:
        dict_path = os.path.join(os.path.dirname(__file__), "dic", "path64", "mtar_dictionary.txt")
        _path64_dict_cache = tools_hash_generator.build_gani_hash_dictionary(dict_path)
    return _path64_dict_cache


class MTAR_OT_ValidateHashGeneratorExe(Operator):
    """Validate hash generator executable path (debug panel)."""
    bl_idname = "mtar.validate_hash_generator_exe"
    bl_label = "Validate Executable"
    bl_description = "Validate that the executable path is valid and accessible"

    def execute(self, context: Context) -> Set[str]:
        props = context.scene.mtar_debug_hash_properties
        exe_path = props.hash_generator_exe_path
        if not exe_path:
            Debug.report_and_log(self, 'ERROR', "Executable path not configured")
            return {'CANCELLED'}
        is_valid, error_msg = tools_hash_generator.validate_executable_path_by_external_generator(exe_path)
        if is_valid:
            Debug.report_and_log(self, 'INFO', "Executable path is valid")
            return {'FINISHED'}
        else:
            Debug.report_and_log(self, 'ERROR', f"Invalid executable: {error_msg}")
            return {'CANCELLED'}


class MTAR_OT_GenerateHash(Operator):
    """Generate hash for input filename using both Python CityHash and external executable."""
    bl_idname = "mtar.generate_hash"
    bl_label = "Hash"
    bl_description = (
        "Hash input filename using Python CityHash (always) and "
        "the external executable (when configured) — all modes"
    )

    def execute(self, context: Context) -> set:
        """Execute the hash computation."""
        props = context.scene.mtar_debug_hash_properties

        if not props.hash_generator_input:
            Debug.report_and_log(self, 'ERROR', "No input filename provided")
            props.hash_generator_error = "No input filename provided"
            self._clear_exe_results(props)
            self._clear_py_results(props)
            return {'CANCELLED'}

        self._run_python(props)
        self._run_exe(context, props)

        Debug.report_and_log(self, 'INFO', "Hash computation complete")
        return {'FINISHED'}

    def _run_python(self, props) -> None:
        """Compute all four hash variants using the pure-Python implementation."""
        text = props.hash_generator_input
        try:
            h_file = util_hashing_cityhash.hash_file_name(text)
            props.hash_generator_py_hash_filename = format(h_file, 'x')
            props.hash_generator_py_hash_filename_dec = str(h_file)

            dot = text.rfind('.')
            if dot != -1:
                ext = text[dot + 1:]
                h_ext = util_hashing_cityhash.hash_file_extension(ext)
                props.hash_generator_py_hash_extension = format(h_ext, 'x')
                props.hash_generator_py_hash_extension_dec = str(h_ext)
            else:
                props.hash_generator_py_hash_extension = ""
                props.hash_generator_py_hash_extension_dec = ""

            h_hwe = util_hashing_cityhash.hash_file_name_with_ext(text)
            props.hash_generator_py_hash_with_extension = format(h_hwe, 'x')
            props.hash_generator_py_hash_with_extension_dec = str(h_hwe)

            h_leg = util_hashing_cityhash.hash_file_name_legacy(text)
            props.hash_generator_py_hash_legacy = format(h_leg, 'x')
            props.hash_generator_py_hash_legacy_dec = str(h_leg)

            props.hash_generator_py_error = ""
        except Exception as exc:
            self._clear_py_results(props)
            props.hash_generator_py_error = str(exc)
            Debug.report_and_log(self, 'ERROR', f"Python hash failed: {exc}")

    def _run_exe(self, context: Context, props) -> None:
        """Compute hash variants using the external executable (if configured)."""
        exe_path = props.hash_generator_exe_path
        if not exe_path:
            self._clear_exe_results(props)
            return

        success, results, error = tools_hash_generator.hash_filename_all_modes_by_external_generator(exe_path, props.hash_generator_input)

        props.hash_generator_hash_filename = results.get('filename', '')
        props.hash_generator_hash_extension = results.get('extension', '')
        props.hash_generator_hash_with_extension = results.get('with_extension', '')
        props.hash_generator_hash_legacy = results.get('legacy', '')
        props.hash_generator_hash_filename_dec = results.get('filename_dec', '')
        props.hash_generator_hash_extension_dec = results.get('extension_dec', '')
        props.hash_generator_hash_with_extension_dec = results.get('with_extension_dec', '')
        props.hash_generator_hash_legacy_dec = results.get('legacy_dec', '')

        if success:
            props.hash_generator_error = ""
        else:
            props.hash_generator_error = error
            Debug.report_and_log(self, 'WARNING', f"Exe hash failed: {error}")

    def _clear_exe_results(self, props) -> None:
        props.hash_generator_hash_filename = ""
        props.hash_generator_hash_extension = ""
        props.hash_generator_hash_with_extension = ""
        props.hash_generator_hash_legacy = ""
        props.hash_generator_hash_filename_dec = ""
        props.hash_generator_hash_extension_dec = ""
        props.hash_generator_hash_with_extension_dec = ""
        props.hash_generator_hash_legacy_dec = ""

    def _clear_py_results(self, props) -> None:
        props.hash_generator_py_hash_filename = ""
        props.hash_generator_py_hash_filename_dec = ""
        props.hash_generator_py_hash_extension = ""
        props.hash_generator_py_hash_extension_dec = ""
        props.hash_generator_py_hash_with_extension = ""
        props.hash_generator_py_hash_with_extension_dec = ""
        props.hash_generator_py_hash_legacy = ""
        props.hash_generator_py_hash_legacy_dec = ""


class MTAR_OT_CopyHashGeneratorOutput(Operator):
    """Copy hash result to clipboard."""
    bl_idname = "mtar.copy_hash_generator_output"
    bl_label = "Copy Result"
    bl_description = "Copy the selected hash result to clipboard"

    result_key: StringProperty(
        name="Result Key",
        description="Which result to copy",
        default="filename",
        maxlen=64
    )

    def execute(self, context: Context) -> set:
        """Execute the copy."""
        props = context.scene.mtar_debug_hash_properties

        result_map = {
            'filename': props.hash_generator_hash_filename,
            'extension': props.hash_generator_hash_extension,
            'with_extension': props.hash_generator_hash_with_extension,
            'legacy': props.hash_generator_hash_legacy,
            'filename_dec': props.hash_generator_hash_filename_dec,
            'extension_dec': props.hash_generator_hash_extension_dec,
            'with_extension_dec': props.hash_generator_hash_with_extension_dec,
            'legacy_dec': props.hash_generator_hash_legacy_dec,
            'py_filename': props.hash_generator_py_hash_filename,
            'py_extension': props.hash_generator_py_hash_extension,
            'py_with_extension': props.hash_generator_py_hash_with_extension,
            'py_legacy': props.hash_generator_py_hash_legacy,
            'py_filename_dec': props.hash_generator_py_hash_filename_dec,
            'py_extension_dec': props.hash_generator_py_hash_extension_dec,
            'py_with_extension_dec': props.hash_generator_py_hash_with_extension_dec,
            'py_legacy_dec': props.hash_generator_py_hash_legacy_dec,
        }

        output = result_map.get(self.result_key, '')

        if not output:
            Debug.report_and_log(self, 'WARNING', f"No result to copy for {self.result_key}")
            return {'CANCELLED'}

        if output.startswith('Error:'):
            Debug.report_and_log(self, 'WARNING', "Cannot copy error message")
            return {'CANCELLED'}

        context.window_manager.clipboard = output
        Debug.report_and_log(self, 'INFO', f"Copied {self.result_key} to clipboard")
        return {'FINISHED'}


class MTAR_OT_ClearHashGeneratorResults(Operator):
    """Clear hash generator input and results."""
    bl_idname = "mtar.clear_hash_generator_results"
    bl_label = "Clear"
    bl_description = "Clear hash generator input and all hash results"

    def execute(self, context: Context) -> set:
        props = context.scene.mtar_debug_hash_properties

        props.hash_generator_input = ""
        props.hash_generator_hash_filename = ""
        props.hash_generator_hash_extension = ""
        props.hash_generator_hash_with_extension = ""
        props.hash_generator_hash_legacy = ""
        props.hash_generator_hash_filename_dec = ""
        props.hash_generator_hash_extension_dec = ""
        props.hash_generator_hash_with_extension_dec = ""
        props.hash_generator_hash_legacy_dec = ""
        props.hash_generator_error = ""
        props.hash_generator_py_hash_filename = ""
        props.hash_generator_py_hash_filename_dec = ""
        props.hash_generator_py_hash_extension = ""
        props.hash_generator_py_hash_extension_dec = ""
        props.hash_generator_py_hash_with_extension = ""
        props.hash_generator_py_hash_with_extension_dec = ""
        props.hash_generator_py_hash_legacy = ""
        props.hash_generator_py_hash_legacy_dec = ""
        props.hash_generator_py_error = ""

        Debug.report_and_log(self, 'INFO', "Hash Generator cleared")
        return {'FINISHED'}


class MTAR_OT_ComputeStrCode32(Operator):
    """Compute StrCode32 hash for an animation track/bone name."""
    bl_idname = "mtar.compute_strcode32"
    bl_label = "Compute StrCode32"
    bl_description = "Compute StrCode32 hash for animation track names, bone names, event names, etc."

    def execute(self, context: Context) -> set:
        props = context.scene.mtar_debug_hash_properties

        input_text = props.strcode32_input.strip()
        remove_ext = props.strcode32_remove_extension

        props.strcode32_result = ""
        props.strcode32_result_dec = ""
        props.strcode32_error = ""

        if not input_text:
            props.strcode32_error = "Input is empty"
            Debug.report_and_log(self, 'WARNING', "StrCode32: Input is empty")
            return {'FINISHED'}

        try:
            hash_val = util_hashing_cityhash.strcode32_path(input_text, remove_extension=remove_ext)
            props.strcode32_result = f"0x{hash_val:08X}"
            props.strcode32_result_dec = str(hash_val)
            Debug.report_and_log(self, 'INFO', f"StrCode32('{input_text}', remove_ext={remove_ext}) = {props.strcode32_result} ({props.strcode32_result_dec})")
        except Exception as e:
            props.strcode32_error = f"Exception: {str(e)}"
            Debug.report_and_log(self, 'ERROR', f"StrCode32 computation failed: {e}")

        return {'FINISHED'}


class MTAR_OT_ClearStrCode32Results(Operator):
    """Clear StrCode32 results."""
    bl_idname = "mtar.clear_strcode32_results"
    bl_label = "Clear StrCode32 Results"
    bl_description = "Clear all StrCode32 results"

    def execute(self, context: Context) -> set:
        props = context.scene.mtar_debug_hash_properties
        props.strcode32_input = ""
        props.strcode32_result = ""
        props.strcode32_result_dec = ""
        props.strcode32_error = ""
        Debug.report_and_log(self, 'INFO', "StrCode32 results cleared")
        return {'FINISHED'}


class MTAR_OT_CopyStrCode32Result(Operator):
    """Copy StrCode32 result to clipboard."""
    bl_idname = "mtar.copy_strcode32_result"
    bl_label = "Copy StrCode32 Result"
    bl_description = "Copy the StrCode32 result to clipboard"

    is_decimal: bpy.props.BoolProperty(
        name="Is Decimal",
        description="If True, copy decimal; if False, copy hexadecimal",
        default=False
    )

    def execute(self, context: Context) -> set:
        props = context.scene.mtar_debug_hash_properties
        text_to_copy = props.strcode32_result_dec if self.is_decimal else props.strcode32_result
        if not text_to_copy:
            Debug.report_and_log(self, 'WARNING', "StrCode32: No result to copy")
            return {'FINISHED'}
        context.window_manager.clipboard = text_to_copy
        Debug.report_and_log(self, 'INFO', f"Copied to clipboard: {text_to_copy}")
        return {'FINISHED'}


class MTAR_OT_UnhashPath(Operator):
    """Reverse-lookup a PathCode64 hash in dic/path64/mtar_dictionary.txt."""
    bl_idname = "mtar.unhash_path"
    bl_label = "Unhash PathCode64"
    bl_description = (
        "Reverse-lookup a PathCode64 hash (decimal or 0x hex) in dic/path64/mtar_dictionary.txt. "
        "The dictionary is built from plain asset paths on first use (acceptable one-time delay)."
    )

    def execute(self, context: Context) -> set:
        props = context.scene.mtar_debug_hash_properties
        raw = (props.unhash_path_input or "").strip()
        props.unhash_path_result = ""

        if not raw:
            Debug.report_and_log(self, 'WARNING', "Unhash PathCode64: Input is empty")
            return {'FINISHED'}

        try:
            hash_val = util_hashing.parse_gani_hash_str(raw)
        except ValueError:
            props.unhash_path_result = "Invalid hash value"
            Debug.report_and_log(self, 'WARNING', f"Unhash PathCode64: Cannot parse '{raw}' as integer")
            return {'FINISHED'}

        path_dict = _get_path64_dict()
        if not path_dict:
            props.unhash_path_result = "Dictionary not found or empty"
            Debug.report_and_log(self, 'WARNING', "Unhash PathCode64: Dictionary is empty or not found")
            return {'FINISHED'}

        result = path_dict.get(hash_val)
        if result is not None:
            props.unhash_path_result = result
            Debug.report_and_log(self, 'INFO', f"Unhash PathCode64: {hash_val} 192 {result}")
        else:
            props.unhash_path_result = "(not found)"
            Debug.report_and_log(self, 'INFO', f"Unhash PathCode64: {hash_val} not found in dictionary")

        return {'FINISHED'}


class MTAR_OT_ClearUnhashPath(Operator):
    """Clear PathCode64 unhash input and result."""
    bl_idname = "mtar.clear_unhash_path"
    bl_label = "Clear"
    bl_description = "Clear PathCode64 unhash input and result"

    def execute(self, context: Context) -> set:
        props = context.scene.mtar_debug_hash_properties
        props.unhash_path_input = ""
        props.unhash_path_result = ""
        return {'FINISHED'}


class MTAR_OT_UnhashStrCode32(Operator):
    """Reverse-lookup a StrCode32 hash in the combined dic/str32/*.txt dictionaries."""
    bl_idname = "mtar.unhash_strcode32"
    bl_label = "Unhash StrCode32"
    bl_description = (
        "Reverse-lookup a StrCode32 hash (decimal or 0x hex) in the combined dic/str32/*.txt dictionaries. "
        "Dictionaries are loaded lazily on first use."
    )

    def execute(self, context: Context) -> set:
        props = context.scene.mtar_debug_hash_properties
        raw = (props.unhash_strcode32_input or "").strip()
        props.unhash_strcode32_result = ""

        if not raw:
            Debug.report_and_log(self, 'WARNING', "Unhash StrCode32: Input is empty")
            return {'FINISHED'}

        try:
            hash_val = util_hashing.parse_hash_string(raw)
        except ValueError:
            props.unhash_strcode32_result = "Invalid hash value"
            Debug.report_and_log(self, 'WARNING', f"Unhash StrCode32: Cannot parse '{raw}' as integer")
            return {'FINISHED'}

        hash_val &= 0xFFFFFFFF

        result = util_hashing.lookup_strcode32(hash_val)
        if result is not None:
            props.unhash_strcode32_result = result
            Debug.report_and_log(self, 'INFO', f"Unhash StrCode32: {hash_val} 192 {result}")
        else:
            props.unhash_strcode32_result = "(not found)"
            Debug.report_and_log(self, 'INFO', f"Unhash StrCode32: {hash_val} not found in dictionary")

        return {'FINISHED'}


class MTAR_OT_ClearUnhashStrCode32(Operator):
    """Clear StrCode32 unhash input and result."""
    bl_idname = "mtar.clear_unhash_strcode32"
    bl_label = "Clear"
    bl_description = "Clear StrCode32 unhash input and result"

    def execute(self, context: Context) -> set:
        props = context.scene.mtar_debug_hash_properties
        props.unhash_strcode32_input = ""
        props.unhash_strcode32_result = ""
        return {'FINISHED'}
