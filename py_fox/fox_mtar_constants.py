"""
Canonical MTAR format field constants derived from Fox Engine binary templates.
"""

# MtarTableList / Entry fields
TABL_PATH = "Path"
TABL_UNKNOWN = "MtarTableUnknown"  # Old-format MtarTableList.unknown field (ushort, typically 7)

# MTAR-level properties (stored on layout action custom properties)
MTAR_VERSION = "MtarVersion"  # int — e.g., 201304220 (old) or 201403250 (new)
MTAR_FLAGS = "MtarFlags"      # int — e.g., 0x1000 (UseMini flag for new format) or 0x0 (old)
