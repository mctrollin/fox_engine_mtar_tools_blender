# Fox Engine MTAR Tools (Blender)

Blender add-on and Python utilities to import and export MTAR animation files (Metal Gear Solid V - Fox Engine).

## Highlights
- Import MTAR containers and convert GANI animation data into Blender Actions
- Export Blender Actions / NLA Strips back to MTAR/GANI format
- Flexible mapping files for bone renaming and rotation offsets
- Designed to interoperate with Rigify custom rigs

## Quick Start
1. Install in Blender: Preferences → Add-ons → Install... and select this repository directory (or copy files into your Blender add-ons folder).
2. Enable the add-on and open the 3D Viewport N-panel → `MTAR` category to access Import/Export panels.
3. Use **Import Animation** to load an `.mtar` file, or **Export Animation** to write an `.mtar` from the selected armature.

Supported Blender versions: 4.0+ (progress UI uses Blender 4.0 progress APIs when available).

## Project Layout
- `py_fox/` – Binary format datatypes and enums (low level Fox types).
- `py_foxwrap/` – Format-agnostic readers/writers and metadata/mapping helpers.
- Top-level modules (`mtar_importer.py`, `mtar_exporter.py`, `blender_panel_import.py`, `blender_panel_export.py`, `blender_operators_*`) – Blender integration and UI.
- `wiki/` – Example files.