# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is **BatchRenderer**, a Blender addon for mass rendering with palette, rotation, and camera controls. It lives in a single file: `blender_render_addon.py`.

## Architecture

### Single-File Addon Structure
The addon uses Blender's standard Python API (`bpy`) and follows the standard addon pattern:

- **`bl_info` (lines 1-9)**: Addon metadata displayed in Blender's preferences
- **Configuration/Constants (lines 19-37)**: Default paths, regex patterns for parsing names
- **Helper Functions (lines 39-189)**: Material/object/collection discovery, palette application
- **Image Processing (lines 191-237)**: PIL-based resize with Blender fallback
- **Rotation Logic (lines 239-265)**: Z-axis rotation around cursor or object median
- **Core Render Routine (lines 267-410)**: Main `run_render_process()` function
- **UI Classes (lines 412-630)**: Property groups, operators, and the main panel
- **Registration (lines 632-660)**: `register()` and `unregister()` functions

### Naming Conventions (Critical)

The addon relies on specific naming patterns to match materials to objects:

**Materials (Palettes):** `PALETTE_ID.OBJECT_ID - ColorName`
- Example: `1.1 - Red`, `1.2 - Blue` = Palette 1, objects 1 and 2
- Single-color palettes apply to all objects in the palette

**Objects:** `OBJECT_ID - Name`
- Example: `1 - Body`, `2 - Wings`

**Collections:** `COLLECTION_ID - Name`
- Example: `1 - Character`, `2 - Props`

### Key Functions

- `get_palettes_data()`: Scans `bpy.data.materials` to build palette mappings
- `get_grouped_objects()`: Groups scene objects by their numeric ID prefix
- `get_grouped_collections()`: Recursively collects mesh objects from collections
- `apply_palette()`: Assigns materials to objects based on ID matching
- `run_render_process()`: Main entry point for batch rendering

## Development

### Installing/Testing in Blender

1. Open Blender → Edit → Preferences → Add-ons → Install
2. Select `blender_render_addon.py`
3. Enable the addon
4. Access the panel in the 3D Viewport sidebar (N key) → "BatchRenderer" tab

### Reloading During Development

In Blender's Python console or Scripting workspace:
```python
import addon_utils
import importlib
import sys

# Disable
addon_utils.disable("blender_render_addon")

# Re-enable
addon_utils.enable("blender_render_addon")
```

Or use Blender's F3 → "Reload Scripts" after saving changes.

### Key Dependencies

- **PIL/Pillow**: Optional, for image resizing. If unavailable, falls back to Blender's internal image API (slower, lower quality)
- **mathutils**: Blender's vector/matrix math library (always available)

## Common Tasks

### Adding a New UI Setting
1. Add property to `SC_Settings` class (lines 499-559)
2. Add UI element in `VIEW3D_PT_BatchRendererPanel.draw()` (lines 568-629)
3. Access via `context.scene.batch_renderer_settings.property_name`

### Modifying Render Logic
The main render loop is in `run_render_process()` (lines 271-410). It:
1. Parses palette input (ranges like "1-5" or "cam" mode)
2. Identifies target objects and cameras
3. Applies palette materials
4. Renders from each camera at specified rotation angles
5. Resizes and moves output files

### Adding New Operators
Follow the pattern of `SC_OT_Render` (lines 456-464):
- Define `bl_idname`, `bl_label`, `bl_description`
- Implement `execute(self, context)` returning `{'FINISHED'}`
- Register in the `classes` tuple (line 635-643)
