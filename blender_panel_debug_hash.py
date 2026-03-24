"""Debug Hash page extracted from blender_panel_debug."""

import bpy
from bpy.types import UILayout, Context


def draw_hash_path(layout: UILayout, context: Context) -> None:
    props = context.scene.mtar_debug_hash_properties

    pathcode_box = layout.box()
    input_box = pathcode_box.box()
    input_box.label(text="Filename", icon='IMPORT')
    col = input_box.column(align=True)
    col.prop(props, "hash_generator_input", text="")

    button_box = pathcode_box.box()
    col = button_box.column(align=True)
    col.scale_y = 1.3

    row = col.row(align=True)
    row.operator("mtar.generate_hash", text="Hash", icon='PLAY')
    row.operator("mtar.clear_hash_generator_results", text="Clear", icon='X')

    results_box = pathcode_box.box()
    results_box.label(text="Hash Results", icon='INFO')

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
    unhash_path_box.label(text="Unhash PathCode64", icon='VIEWZOOM')
    col = unhash_path_box.column(align=True)
    col.prop(props, "unhash_path_input", text="")
    row = unhash_path_box.row(align=True)
    row.scale_y = 1.3
    row.operator("mtar.unhash_path", text="Unhash", icon='VIEWZOOM')
    row.operator("mtar.clear_unhash_path", text="Clear", icon='X')
    if props.unhash_path_result:
        result_row = unhash_path_box.row()
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
    unhash_str32_box.label(text="Unhash StrCode32", icon='VIEWZOOM')
    col = unhash_str32_box.column(align=True)
    col.prop(props, "unhash_strcode32_input", text="")
    row = unhash_str32_box.row(align=True)
    row.scale_y = 1.3
    row.operator("mtar.unhash_strcode32", text="Unhash", icon='VIEWZOOM')
    row.operator("mtar.clear_unhash_strcode32", text="Clear", icon='X')
    if props.unhash_strcode32_result:
        result_row = unhash_str32_box.row()
        is_str32_success = props.unhash_strcode32_result not in ("(not found)", "Invalid hash value")
        result_row.alert = not is_str32_success
        result_row.label(
            text=props.unhash_strcode32_result,
            icon='CHECKMARK' if is_str32_success else 'ERROR',
        )

def draw_hash_page(layout: UILayout, context: Context) -> None:
    """Draw the contents originally provided by the old Hash panel."""
    props = context.scene.mtar_debug_hash_properties

    exe_configured = bool(props.hash_generator_exe_path)

    exe_box = layout.box()
    exe_box.label(text="External Hash Generator", icon='FILE_SCRIPT')
    exe_box.label(text="Needed for custom hashes.")
    row = exe_box.row(align=True)
    row.prop(props, "hash_generator_exe_path", text="")
    row.operator("mtar.validate_hash_generator_exe", text="", icon='FORCE_HARMONIC')
    exe_box.label(text="https://mgsvmoddingwiki.github.io/GzsTool/")

    if not exe_configured:
        info_box = layout.box()
        info_box.label(text="Exe not configured — Python only", icon='INFO')
        info_box.label(text="Configure path above for exe column")

  

    draw_hash_path(layout=layout, context=context)

    draw_unhash_path(layout=layout, context=context)

    draw_unhash_str32(layout=layout, context=context)


