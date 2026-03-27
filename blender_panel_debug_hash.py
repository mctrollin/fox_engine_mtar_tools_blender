"""Debug Hash page extracted from blender_panel_debug."""

import bpy
from bpy.types import UILayout, Context


def draw_hash_path(layout: UILayout, context: Context) -> None:
    props = context.scene.mtar_debug_hash_properties

    pathcode_box = layout.box()
    pathcode_box.label(text="Hash Filename", icon='HAND')

    
    row = pathcode_box.row(align=True)
    row.prop(props, "hash_generator_input", text="")
    row.operator("mtar.generate_hash", text="", icon='PLAY')
    row.operator("mtar.clear_hash_generator_results", text="", icon='X')


    has_py_results = bool(
        props.hash_generator_py_hash_filename
        or props.hash_generator_py_hash_with_extension
        or props.hash_generator_py_hash_legacy
    )
    has_exe_results = bool(
        props.hash_generator_hash_filename
        or props.hash_generator_hash_with_extension
        or props.hash_generator_hash_legacy
    )

    if has_py_results or has_exe_results:
        results_box = pathcode_box.box()

        header = results_box.row(align=False)
        header.label(text="")
        header.label(text="Python")
        header.label(text="Exe")
        header.label(text="")

        def _row(label, py_val, exe_val):
            row = results_box.row(align=True)
            row.label(text=label)
            row.label(text=str(py_val))
            row.label(text=str(exe_val))
            if py_val == exe_val and py_val:
                row.label(text="=", icon='CHECKMARK')
            else:
                row.label(text="", icon='NONE')

        _row("Hash Filename  (-d -h)", props.hash_generator_py_hash_filename,
             props.hash_generator_hash_filename)
        _row("Hash Ext       (-d -he)", props.hash_generator_py_hash_extension,
             props.hash_generator_hash_extension)
        _row("Hash With Ext   (-d -hwe)", props.hash_generator_py_hash_with_extension,
             props.hash_generator_hash_with_extension)
        _row("Legacy Hash     (-d -hl)", props.hash_generator_py_hash_legacy,
             props.hash_generator_hash_legacy)

        _row("Filename (dec)", props.hash_generator_py_hash_filename_dec,
             props.hash_generator_hash_filename_dec)
        _row("Ext (dec)", props.hash_generator_py_hash_extension_dec,
             props.hash_generator_hash_extension_dec)
        _row("With Ext (dec)", props.hash_generator_py_hash_with_extension_dec,
             props.hash_generator_hash_with_extension_dec)
        _row("Legacy (dec)", props.hash_generator_py_hash_legacy_dec,
             props.hash_generator_hash_legacy_dec)

        if props.hash_generator_error:
            err_box = results_box.box()
            err_box.alert = True
            err_box.label(text=f"Error: {props.hash_generator_error}")
        if props.hash_generator_py_error:
            err_box = results_box.box()
            err_box.alert = True
            err_box.label(text=f"Python Error: {props.hash_generator_py_error}")
    
def draw_unhash_path(layout: UILayout, context: Context) -> None:
    """Unhash PathCode64"""
    props = context.scene.mtar_debug_hash_properties
    unhash_path_box = layout.box()
    unhash_path_box.label(text="Unhash PathCode64", icon='HAND')
    row = unhash_path_box.row(align=True)
    row.prop(props, "unhash_path_input", text="")
    row.operator("mtar.unhash_path", text="", icon='VIEWZOOM')
    row.operator("mtar.clear_unhash_path", text="", icon='X')
    if props.unhash_path_result:
        result_box = unhash_path_box.box()
        result_row = result_box.row()
        is_path_success = props.unhash_path_result not in (
            "(not found)", "Invalid hash value", "Dictionary not found or empty"
        )
        result_row.alert = not is_path_success
        result_row.label(
            text=props.unhash_path_result,
            icon='CHECKMARK' if is_path_success else 'ERROR',
        )

def draw_unhash_str32(layout: UILayout, context: Context) -> None:
    """Unhash StrCod32"""
    props = context.scene.mtar_debug_hash_properties
    unhash_str32_box = layout.box()
    unhash_str32_box.label(text="Unhash StrCode32", icon='HAND')
    row = unhash_str32_box.row(align=True)
    row.prop(props, "unhash_strcode32_input", text="")
    row.operator("mtar.unhash_strcode32", text="", icon='VIEWZOOM')
    row.operator("mtar.clear_unhash_strcode32", text="", icon='X')
    if props.unhash_strcode32_result:
        result_box = unhash_str32_box.box()
        result_row = result_box.row()
        is_str32_success = props.unhash_strcode32_result not in ("(not found)", "Invalid hash value")
        result_row.alert = not is_str32_success
        result_row.label(
            text=props.unhash_strcode32_result,
            icon='CHECKMARK' if is_str32_success else 'ERROR',
        )

def draw_hash_page(layout: UILayout, context: Context) -> None:
    """Draw the contents originally provided by the old Hash panel."""
    props = context.scene.mtar_debug_hash_properties

    exe_box = layout.box()
    exe_box.label(text="Optional External Hash Generator", icon='FILE_SCRIPT')
    row = exe_box.row(align=True)
    row.prop(props, "hash_generator_exe_path", text="")
    row.operator("mtar.validate_hash_generator_exe", text="", icon='FORCE_HARMONIC')
    exe_box.label(text="https://mgsvmoddingwiki.github.io/GzsTool/")

  

    draw_hash_path(layout=layout, context=context)

    draw_unhash_path(layout=layout, context=context)

    draw_unhash_str32(layout=layout, context=context)


