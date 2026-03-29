# Fox Engine MTAR Tools (Blender)

**Blender add-on to import and export MTAR animation files (Metal Gear Solid V - Fox Engine).**

This tool is designed to map Fox Engine animation tracks to user-made rigs - the auto-generated armature is often useless beside storing the animation data.


## Quick Start

### ⛺ Install steps
1. **Blender 4.0+ / 4.4+ (recommended)**: Preferences → Add-ons → Install... → select the repository folder (`fox_engine_mtar_tools_blender`) or `.zip` package; then enable the add-on.
2. **Legacy Blender**: copy to the Blender user addons folder manually and enable in Preferences.
3. **N-panel**: Open the 3D Viewport Sidebar and find the `Fox MTAR` category.

### ℹ️ Version note
Actually supported Blender versions are 4.4+ (but you can try 3.0+ as well and hope).

### ‼️ Tips
- **Read the tooltips** The UI is designed around this principle.
- **Check the example files** In the `wiki\` folder. 
    - A ready-to-import file `human_finger_animations.blend` referencing the example rig `human_finger_rig.blend`.
    - Best practise is to not directly work in the rig file so you can have multiple animation blend files pointing all to one shared rig blend file.
    - An example mapping file `human_finger_mapping.txt` to get you kick-started.
- **Start with an original file** Building a new mtar from scratch is possible but it is easier to import an existing one and modify it.
- **Work NLA-based** It allows for faster editing of many animations, check also the NLA debug tools.

## What it is

### ✅ What this plugin can do
- It can import `.mtar` containers and decode embedded GANI "files" into Blender Actions.
- It can export such Actions back to `.mtar`.
- It can integrate custom rigs (e.g. based on Rigify) and supports bone name remaps, rotation/translation offset corrections, and axis reorders.
- It can handle root motion, motion points, motion events and shader parameters (gani1).
- It can export custom path hash mtars for use with Infinite Heaven's custom motion play feature.

#### GANI format support
- GANI1: GANI format used for GZ and even for some use cases like facial animations in TPP.
- GANI2: newer GANI format with improved framing and track sectioning.
- Has automatic format detection based on file headers in the MTAR reader path.
- Keeps import/export path consistent so a GANI1 source in an MTAR imported to Blender can be re-exported as GANI1/GANI2 depending on pipeline options.

### ❌ What this plugin does not do
- Does not import/export MTARs of all versions - only MGS5 GZ/TPP versions are supported.
- Does not build an actual skeleton as you might expect - it only creates dummies to hold animation data which are used during the mapping and baking process.
- Does not import the original animation source. The data was already lossy post processed by Kojima Productions during export.
- Does not reconstruct full Fox Engine in-game rig logic like twist bones, you have to simulate them via your own rig based on best-guess.
- Does not guarantee 100% perfect rotation and locaton preservation. Repeated imports and exports of the same data can degrade it. Import once and from there on only export.
- Does not support all track types (e.g. animal legs).
- Does not support big endian files.
- Does not offer any kind of functionality to working with animations or rigs in general - use dedicated tools for this.


## Where it is

### Main Panel
The add-on uses the N-panel category `Fox MTAR`. The main panel has the following tabs:

- **Import**: Importing + generate a raw mapping file as foundation for a custom rig setup.
- **Export**: Exporting + output an info file (for use with Infinite Heaven).
- **Settings**: Logging options and advanced fine-tuning toggle.

### Debug Panel
The `Fox MTAR` category also includes a separate `Debug Tools` panel with following tabs:

**Needs to be enabled in the addon preferences!**

- **Hash**: Hash/unhash `StrCode32` and `PathCode64` values.
- **NLA**: Find/isolate animation subsets before export/debug.
- **Bake**: Run bake pass manually to verify action timeline baking.
- **Transform**: Inspect tests for local/world bone transforms on selected frames.
- **Map_R**: Debug map_r rotation remap and axis compensation setup.


## Notes

### 🕐 Speed
- Animation import and export can be very slow. Especially when using all features on big files like player2_resident.mtar.
- Best approach is to import only the ganis you want to edit and export by using a reference file.

### 🍪 Data

#### Metadata
- MTAR data which can not be represented in blender (typical metadata) is stored as custom properties on the actions.
- Imported actions receive track metadata in `bpy.data.actions[...]` custom properties (e.g. `track_0_...`, `gani_version`, `MTAR_FLAGS`).
- To inspect properties:
    - Select an action in the `Dope Sheet` editor + `Dope Sheet` or `Action Editor` mode
    - Select a keyframes channel
    - Expand the `Custom Properties` area.
- Mapping files and action metadata are the two key mechanisms for bone transform remap in export.
- There is a lot of redundant information stored but this represents the actual mtar data.

#### Motion Events
- Motion Events are represented as pose markers. 
- They are bit hidden in blender: Open the `Dope Sheet` in `Action Editor` mode. 
- The `Settings > (advanced) Show Event Markers` button helps to make sure they are visible.
- Known Issue: Currently the markers are drawn in their relative location to frame 0 and ignore the absolute nla track location: https://projects.blender.org/blender/blender/issues/97323 

#### Layout Track
- GANI2 format uses a **Layout Track**
- It is positioned in the negative frame range and carries a specific set of custom properties.
- For GANI format selection: the writer picks GANI1 or GANI2 based on layout action / `MTAR_FLAGS` found in the armature/action data.
- It is required for the GANI2 export


## Project Code Layout

- `py_core/` - Core addon properties and logging infrastructure.
- `py_fox/` - Fox Engine binary format definitions, constants, and low-level read/write dataclasses.
- `py_foxwrap/` - Format-agnostic wrapper layer.
- `py_foxwrap_utilities/` - Helper utilities for action selection, filtering, rest pose correction, and shared transform routines.
- `py_frontend/` - Blender UI and operator panels, including import/export panels, debug panels, and NLA/tools operators.
- `py_tools/` - Workbench helper scripts, main entry point for big operations.
- `py_utilities/` - Blender + animation utility functions for armature, fcurve, and transform handling.
- `wiki/` - Usage examples.

## About

Based on work, research, utilities and support by:

Joey, Atvaark, BobDoleOwndU, TinManTex, topher-au, Morbidslinky, ZipfsLaw, caplag, Unknown, Choc, JinMar, at al 

@ Modders' Heaven

- **File format bit templates**: https://github.com/kapuragu/FoxEngineTemplates
- **Hash dictionaries**: https://github.com/TinManTex/mgsv-lookup-strings
- **Maya importer**: https://github.com/Joey35233/FoxMayaTools
- **GzsTool**: https://github.com/Atvaark/GzsTool and it's modified version https://github.com/BobDoleOwndU/GzsTool and its modified version https://github.com/TinManTex/GzsTool
- **FoxKit 3**: https://github.com/Joey35233/FoxKit-3
- **Motion information 1**: https://unknown321.github.io/mgsv_research/motions.html
- **Motion information 2**: https://chocmake.github.io/guides/mgsv-adding-player-motions/
- **Infinite Heaven custom motion feature** https://github.com/TinManTex/InfiniteHeaven
- **Snake Bite Mod Manager** https://github.com/topher-au/SnakeBite

Created with the help of AI