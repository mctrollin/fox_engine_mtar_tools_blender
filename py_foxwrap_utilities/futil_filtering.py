from typing import List, Optional

from ..py_core.core_logging import Debug

from ..py_utilities import util_filtering, util_hashing

from ..py_fox import fox_mtar_constants as mtar_const

from ..py_foxwrap_utilities.futil_action_types import ExportActionData
from ..py_foxwrap.fwrap_mtar_import_types import GaniImportData


def filter_gani_import_data(
    all_gani_data: List[GaniImportData],
    filter_filepath: str,
    gani_hash_dict: Optional[dict[int, str]] = None,
) -> List[GaniImportData]:
    """Filter imported GANI data by file filter path, include/exclude semantics."""
    if not filter_filepath:
        return all_gani_data

    Debug.log(f"Applying GANI filter in fwrap_filtering for import from '{filter_filepath}'")
    allowed_hashes, excluded_hashes, allowed_paths, excluded_paths = util_filtering.load_gani_filter_list(
        filter_filepath, 
        gani_hash_dict=gani_hash_dict
    )

    filtered = []
    skipped_infos = []
    for data in all_gani_data:
        path_hash = None
        path_str = None
        if data.file_header and hasattr(data.file_header, 'path'):
            path_hash = data.file_header.path
            if gani_hash_dict:
                path_str = util_hashing.unhash_gani_path(path_hash, gani_hash_dict)

        if util_filtering.is_gani_path_allowed(
            path_hash,
            path_str,
            allowed_hashes,
            excluded_hashes,
            allowed_paths,
            excluded_paths,
            gani_hash_dict,
        ):
            filtered.append(data)
        else:
            path_hash_str = f"0x{path_hash:016X}" if path_hash is not None else "None"
            path_display = path_str if path_str else path_hash_str
            skipped_infos.append(path_display)
            Debug.log(f"fwrap_filtering.filter_gani_import_data: skipped {path_display}")

    if not filtered:
        warning_msg = "fwrap_filtering.filter_gani_import_data: no GANIs matched filter"
        if skipped_infos:
            warning_msg += ": " + ", ".join(skipped_infos)
        Debug.log(warning_msg)

    return filtered


def filter_gani_export_actions(
    actions_to_export: List[ExportActionData],
    filter_filepath: str,
) -> List[ExportActionData]:
    """Filter export action list by GANI filter file (include/exclude semantics)."""
    if not filter_filepath:
        return actions_to_export

    Debug.log(f"Applying GANI filter in fwrap_filtering for export from '{filter_filepath}'")
    allowed_hashes, excluded_hashes, allowed_paths, excluded_paths = util_filtering.load_gani_filter_list(filter_filepath)

    filtered = []
    skipped_infos = []
    for item in actions_to_export:
        path_hash = None
        path_str = None

        if item.action and mtar_const.TABL_PATH in item.action.keys():
            path_val = str(item.action[mtar_const.TABL_PATH])
            path_str = path_val
            if util_hashing.is_gani_path_a_hash(path_val):
                path_hash = util_hashing.parse_gani_hash_str(path_val)

        if util_filtering.is_gani_path_allowed(
            path_hash,
            path_str,
            allowed_hashes,
            excluded_hashes,
            allowed_paths,
            excluded_paths,
            None,
        ):
            filtered.append(item)
        else:
            action_name = item.action.name if item.action else 'None'
            item_display = path_str or action_name
            skipped_infos.append(item_display)
            Debug.log(f"fwrap_filtering.filter_gani_export_actions: skipped action '{item_display}'")

    if not filtered:
        warning_msg = "fwrap_filtering.filter_gani_export_actions: no actions matched filter"
        if skipped_infos:
            warning_msg += ": " + ", ".join(skipped_infos)
        Debug.log(warning_msg)

    return filtered
