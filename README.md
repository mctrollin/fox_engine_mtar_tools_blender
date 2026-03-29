# Fox Engine MTAR Tools (Blender)

Blender add-on and Python utilities to import and export MTAR animation files (Metal Gear Solid V - Fox Engine).

This tool is designed to map Fox Engine animation tracks into user-made rigs; the built-in auto-generated import armature is handy for inspection but typically requires custom mapping for production-ready results.


## Quick Start

### Install steps
1. Blender 4.0+ / 4.4+ (recommended): Preferences → Add-ons → Install... → select the repository folder (`fox_engine_mtar_tools_blender`) or `.zip` package; then enable the add-on.
2. Legacy Blender: copy to the Blender user addons folder manually and enable in Preferences.
3. Open the 3D Viewport Sidebar (N-panel) and find `Fox MTAR` category.

### Version note
Actually supported Blender versions are 4.4+ but you can try 3.0+ as well and hope.


## What it is

### What this plugin can do
- Import `.mtar` containers and decode embedded GANI "files" into Blender Actions.
- Export selected armature animation back to `.mtar`.
- Integrate with custom rigs (e.g. based on rigify) based on bone mapping files for name remaps, rotation/translation offset corrections, and axis reorders.
- Handle root motion, motion points, motion events and shader parameters (gani1).

#### GANI format support
- GANI1: legacy GANI format, fully supported for basic transform track import/export as used in early Fox Engine MTARs.
- GANI2: newer GANI format with improved framing and track sectioning, supported with metadata/segment mapping and multi-track interleaving.
- Has automatic format detection based on file headers in the MTAR reader path.
- Keeps import/export path consistent so a GANI1 source in an MTAR imported to Blender can be re-exported as GANI1/GANI2 depending on pipeline options.

### What this plugin does not do
- Does not reconstruct full Fox Engine rig logic (e.g. animations using complex pseudo-IK chains or in-game procedural states).
- Does not import/export MTARs with encrypted/compressed non-standard variants (only standard MTAR data layout currently supported).
- Does not guarantee 100% perfect manual joint/axis orientation compensation for every custom rig; mapping files are required.
- Does not support big endian files


## Where it is
The add-on uses a top-level `Fox MTAR` panel in Blender’s 3D Viewport N-panel.

### Main Panel
#### Import tab
- Loading MTAR
- Applying bone mappings
- Generating mapping templates
- Linking the resulting Action to the selected custom rig armature

#### Export tab
- Writing one or many GANI segments into an MTAR container
- Including NLA strip-based exports and optional motion point armature handling
- Export settings: use NLA, strip range, and motion points armature path

#### Settings tab
- Advanced settings button to expose debugging and fine-tuning options
- User-configurable import/export defaults, and path preservation behaviors
- Preferential controls for auto conversion modes and animation channel filtering

### Debug Panel
The `Fox MTAR` category includes a separate `Debug Tools` panel that uses internal tabs.

Needs to be enabled in the addon preferences.

#### Hash tab
- Generate/validate external hash generator executable path
- Compute hashes used by the MTAR/GANI pipeline (including StrCode32/PathCode64)
- Invert and copy hash values in both directions for track and bone identifiers

#### NLA tab
- Filter and copy NLA path/header/data indices for subsets of strips
- Mute/unmute/select NLA strips by hash/filter rules
- Quickly isolate animation subsets prior to export or debugging

#### Bake tab
- Run a full debug bake pipeline for currently selected action
- Setup Graph Editor context for manual bake-related adjustments
- Validate bake results and log warnings for potential frame/resolution issues

#### Transform tab
- Inspect local/world bone transforms at a chosen frame
- Create transform helper dummies, copy results to clipboard
- Verify exact coordinate mapping for imported/exported bone transforms

#### Map_R tab
- Calculate and verify `map_r` rotation correction parameter for a bone
- Convert test quaternions and rest pose mapping into Blender/FOX representations
- Apply inverted rest pose and mapped rotation for debugging rig alignment


## Project Layout
- `py_core/` - Core addon properties and logging infrastructure.
- `py_fox/` - Fox Engine binary format definitions, constants, and low-level read/write dataclasses.
- `py_foxwrap/` - Format-agnostic wrapper layer.
- `py_foxwrap_utilities/` - Helper utilities for action selection, filtering, rest pose correction, and shared transform routines.
- `py_utilities/` - Blender + animation utility functions for armature, fcurve, and transform handling.
- `py_frontend/` - Blender UI and operator panels, including import/export panels, debug panels, and NLA/tools operators.
- `py_tools/` - Workbench helper scripts, main entry point for big operations.
- `wiki/` - Usage examples.
