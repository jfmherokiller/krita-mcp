"""
Krita MCP Server
Bridge between Claude (or any MCP client) and Krita painting application.

Uses FastMCP to expose Krita painting tools over the Model Context Protocol,
communicating with a Krita plugin via HTTP.
"""

from fastmcp import FastMCP
import httpx
import os
from typing import Optional

# Configuration
KRITA_URL = os.environ.get("KRITA_URL", "http://localhost:5678")

mcp = FastMCP("krita-mcp")


def send_command(action: str, params: dict = None, timeout: float = 30.0) -> dict:
    """Send command to Krita plugin and return result."""
    if params is None:
        params = {}

    try:
        response = httpx.post(
            KRITA_URL,
            json={"action": action, "params": params},
            timeout=timeout
        )
        return response.json()
    except httpx.ConnectError:
        return {"error": "Cannot connect to Krita. Is Krita running with the MCP plugin enabled?"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def krita_health() -> str:
    """Check if Krita is running and the MCP plugin is active."""
    try:
        response = httpx.get(f"{KRITA_URL}/health", timeout=5.0)
        data = response.json()
        return f"Krita is running. Plugin: {data.get('plugin', 'unknown')}"
    except:
        return "Cannot connect to Krita. Make sure Krita is running with the MCP plugin enabled."


@mcp.tool()
def krita_new_canvas(
    width: int = 800,
    height: int = 600,
    name: str = "New Canvas",
    background: str = "#1a1a2e"
) -> str:
    """
    Create a new canvas in Krita.

    Args:
        width: Canvas width in pixels (default 800)
        height: Canvas height in pixels (default 600)
        name: Document name
        background: Background color as hex (default dark blue)
    """
    result = send_command("new_canvas", {
        "width": width,
        "height": height,
        "name": name,
        "background": background
    })

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Created canvas: {width}x{height}, background: {background}"


@mcp.tool()
def krita_set_color(color: str) -> str:
    """
    Set the foreground (paint) color.

    Args:
        color: Hex color code (e.g., "#ff6b6b", "#b8a9c9")
    """
    result = send_command("set_color", {"color": color})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Color set to {color}"


@mcp.tool()
def krita_set_brush(
    preset: Optional[str] = None,
    size: Optional[int] = None,
    opacity: Optional[float] = None
) -> str:
    """
    Set brush preset and properties.

    Args:
        preset: Brush preset name (partial match, e.g., "Basic", "Soft", "Airbrush")
        size: Brush size in pixels
        opacity: Brush opacity (0.0 to 1.0)
    """
    params = {}
    if preset:
        params["preset"] = preset
    if size:
        params["size"] = size
    if opacity is not None:
        params["opacity"] = opacity

    result = send_command("set_brush", params)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Brush set: preset={preset}, size={size}, opacity={opacity}"


@mcp.tool()
def krita_stroke(points: list[list[int]], pressure: float = 1.0) -> str:
    """
    Paint a stroke through a series of points.

    Args:
        points: List of [x, y] coordinate pairs, e.g., [[100, 100], [150, 120], [200, 150]]
        pressure: Brush pressure (0.0 to 1.0, affects stroke thickness/opacity)
    """
    if len(points) < 2:
        return "Error: Need at least 2 points for a stroke"

    result = send_command("stroke", {
        "points": points,
        "pressure": pressure
    })

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Stroke painted with {len(points)} points"


@mcp.tool()
def krita_fill(x: int, y: int, radius: int = 50) -> str:
    """
    Fill an area with current color (paints a filled circle at the point).

    Args:
        x: X coordinate
        y: Y coordinate
        radius: Fill radius in pixels
    """
    result = send_command("fill", {"x": x, "y": y, "radius": radius})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Filled at ({x}, {y}) with radius {radius}"


@mcp.tool()
def krita_draw_shape(
    shape: str,
    x: int,
    y: int,
    width: int = 100,
    height: int = 100,
    fill: bool = True,
    stroke: bool = False,
    x2: Optional[int] = None,
    y2: Optional[int] = None
) -> str:
    """
    Draw a shape on the canvas.

    Args:
        shape: Type of shape - "rectangle", "ellipse", or "line"
        x: X coordinate (top-left for shapes, start point for lines)
        y: Y coordinate (top-left for shapes, start point for lines)
        width: Width of shape (ignored for lines if x2/y2 provided)
        height: Height of shape (ignored for lines if x2/y2 provided)
        fill: Whether to fill the shape
        stroke: Whether to draw outline
        x2: End X for lines (optional)
        y2: End Y for lines (optional)
    """
    params = {
        "shape": shape,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "fill": fill,
        "stroke": stroke
    }
    if x2 is not None:
        params["x2"] = x2
    if y2 is not None:
        params["y2"] = y2

    result = send_command("draw_shape", params)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Drew {shape} at ({x}, {y})"


@mcp.tool()
def krita_get_canvas(filename: str = "canvas.png") -> str:
    """
    Export current canvas to a PNG file and return the path.
    Use this to see your painting progress.

    Args:
        filename: Output filename (saved to configured output directory)
    """
    # Extended timeout — canvas export can take a while on large canvases
    result = send_command("get_canvas", {"filename": filename}, timeout=120.0)

    if "error" in result:
        return f"Error: {result['error']}"

    path = result.get("path", "")
    return f"Canvas saved to: {path}"


@mcp.tool()
def krita_undo() -> str:
    """Undo the last action."""
    result = send_command("undo", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Undone"


@mcp.tool()
def krita_redo() -> str:
    """Redo the last undone action."""
    result = send_command("redo", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Redone"


@mcp.tool()
def krita_clear(color: str = "#1a1a2e") -> str:
    """
    Clear the canvas to a solid color.

    Args:
        color: Color to fill canvas with (default dark blue)
    """
    result = send_command("clear", {"color": color})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Canvas cleared to {color}"


@mcp.tool()
def krita_save(path: str) -> str:
    """
    Save the current canvas to a specific file path.

    Args:
        path: Full file path to save to (e.g., "C:/art/my_painting.png")
    """
    # Extended timeout — saving large files can take a while
    result = send_command("save", {"path": path}, timeout=120.0)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Saved to {path}"


@mcp.tool()
def krita_get_color_at(x: int, y: int) -> str:
    """
    Sample the color at a specific pixel (eyedropper).

    Args:
        x: X coordinate
        y: Y coordinate
    """
    result = send_command("get_color_at", {"x": x, "y": y})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Color at ({x}, {y}): {result.get('color', 'unknown')} (R:{result.get('r')}, G:{result.get('g')}, B:{result.get('b')})"


@mcp.tool()
def krita_list_brushes(filter: str = "", limit: int = 20) -> str:
    """
    List available brush presets.

    Args:
        filter: Filter brushes by name (partial match)
        limit: Maximum number to return
    """
    result = send_command("list_brushes", {"filter": filter, "limit": limit})

    if "error" in result:
        return f"Error: {result['error']}"

    brushes = result.get("brushes", [])
    if not brushes:
        return "No brushes found matching filter"

    return f"Available brushes ({len(brushes)}):\n" + "\n".join(f"  - {b}" for b in brushes)


@mcp.tool()
def krita_open_file(path: str) -> str:
    """
    Open an existing file in Krita (.kra, .png, .jpg, etc).

    Args:
        path: Full file path to open (e.g., "C:/art/my_painting.kra")
    """
    result = send_command("open_file", {"path": path}, timeout=30.0)

    if "error" in result:
        return f"Error: {result['error']}"

    return f"Opened: {result.get('name', 'unknown')} ({result.get('width')}x{result.get('height')})"


# --- Layers ---

@mcp.tool()
def krita_list_layers() -> str:
    """
    List the layer tree of the active document (name, id, type, visibility, opacity, blending
    mode). Layer names are NOT guaranteed unique — if two layers share a name, pass the id shown
    here (not the name) to any other tool's "name" parameter (create_layer, delete_layer,
    set_active_layer, etc. all accept either) to target the exact one you mean.
    """
    result = send_command("list_layers", {})

    if "error" in result:
        return f"Error: {result['error']}"

    def format_layers(layers, indent=0):
        lines = []
        for layer in layers:
            prefix = "  " * indent + "- "
            vis = "" if layer["visible"] else " (hidden)"
            lines.append(
                f"{prefix}{layer['name']} (id={layer['id']}) [{layer['type']}] "
                f"opacity={layer['opacity']}/255 blend={layer['blendingMode']}{vis}"
            )
            lines.extend(format_layers(layer["children"], indent + 1))
        return lines

    lines = format_layers(result.get("layers", []))
    if not lines:
        return "No layers"
    return "\n".join(lines)


@mcp.tool()
def krita_find_nodes(
    name: str = "",
    type: Optional[str] = None,
    color_label: int = 0,
    recursive: bool = True,
    partial_match: bool = False,
    under: Optional[str] = None,
) -> str:
    """
    Search the layer tree for nodes matching name/type/color-label criteria — for pulling out
    specific layers/masks in a large document without parsing list_layers' full tree dump.

    Args:
        name: Name to match (empty = match any name)
        type: Node type to filter by, e.g. "paintlayer", "vectorlayer", "grouplayer",
            "filllayer", "clonelayer", "filelayer", "filtermask", "transparencymask",
            "selectionmask", "colorizemask", "transformmask" (omit for any type)
        color_label: Krita's layer color-label index to filter by (omit/0 for any)
        recursive: Search nested groups too (default True)
        partial_match: Substring match instead of exact name match
        under: Restrict the search to this layer/group's subtree (name or id) — default: the
            whole document
    """
    params = {
        "name": name, "recursive": recursive, "partial_match": partial_match,
        "color_label": color_label,
    }
    if type:
        params["type"] = type
    if under:
        params["under"] = under
    result = send_command("find_nodes", params)

    if "error" in result:
        return f"Error: {result['error']}"

    def format_layers(layers, indent=0):
        lines = []
        for layer in layers:
            prefix = "  " * indent + "- "
            vis = "" if layer["visible"] else " (hidden)"
            lines.append(
                f"{prefix}{layer['name']} (id={layer['id']}) [{layer['type']}] "
                f"opacity={layer['opacity']}/255 blend={layer['blendingMode']}{vis}"
            )
            lines.extend(format_layers(layer["children"], indent + 1))
        return lines

    lines = format_layers(result.get("nodes", []))
    if not lines:
        return "No matching nodes"
    return "\n".join(lines)


@mcp.tool()
def krita_create_layer(
    name: str = "New Layer",
    type: str = "paint",
    parent: Optional[str] = None,
    generator: Optional[str] = None,
    config: Optional[dict] = None,
) -> str:
    """
    Create a new layer.

    Args:
        name: Layer name (not required to be unique — the returned id is the reliable way to
            reference this exact layer later, e.g. in parent, or any other tool's "name" param)
        type: "paint", "group", "vector", or "fill" (a non-destructive generator layer)
        parent: Name (or id) of an existing group layer to nest inside (default: top level)
        generator: Required when type="fill" — e.g. "gradient", "pattern", "color"
        config: Generator-specific config dict when type="fill" (keys vary by generator;
            omit for Krita's default). Fills the current selection, or the whole canvas if none.
    """
    params = {"name": name, "type": type}
    if parent:
        params["parent"] = parent
    if generator:
        params["generator"] = generator
    if config:
        params["config"] = config
    result = send_command("create_layer", params)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Created layer '{result.get('name')}' (id={result.get('id')}) ({result.get('type')})"


@mcp.tool()
def krita_create_mask(
    type: str,
    layer: Optional[str] = None,
    name: str = "Mask",
    filter: Optional[str] = None,
    config: Optional[dict] = None,
) -> str:
    """
    Create a non-destructive mask on a layer. Unlike apply_filter (which bakes pixels in
    permanently), a mask stays live: a filter mask's effect can be swapped later via
    set_filter_mask_filter, and any mask can be shown/hidden via set_layer_visible or removed via
    delete_layer without touching the layer's actual pixels.

    Args:
        type: "filter" (apply a live, editable filter), "transparency" (mask out regions —
            content stays but doesn't render/export), or "selection" (mask that IS a saved
            selection, restorable later)
        layer: Layer to attach the mask to (default: the active layer)
        name: Name for the new mask
        filter: Required when type="filter" — filter name (see list_filters)
        config: Filter-specific config dict when type="filter" (keys vary by filter)
    """
    params = {"type": type, "name": name}
    if layer:
        params["layer"] = layer
    if filter:
        params["filter"] = filter
    if config:
        params["config"] = config
    result = send_command("create_mask", params)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Created {result.get('type')} '{result.get('name')}' (id={result.get('id')})"


@mcp.tool()
def krita_set_filter_mask_filter(mask: str, filter: str, config: Optional[dict] = None) -> str:
    """
    Swap an existing filter mask's filter and/or config in place — re-edit the non-destructive
    effect without deleting and recreating the mask.

    Args:
        mask: Name or id of the filter mask (see list_layers)
        filter: New filter name (see list_filters)
        config: Filter-specific config dict (keys vary by filter; omit to keep the filter's default)
    """
    params = {"mask": mask, "filter": filter}
    if config:
        params["config"] = config
    result = send_command("set_filter_mask_filter", params)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Filter mask '{mask}' now using '{filter}'"


@mcp.tool()
def krita_delete_layer(name: str) -> str:
    """Delete a layer by name, or by id (see list_layers) if the name might be ambiguous."""
    result = send_command("delete_layer", {"name": name})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Deleted layer '{name}'"


@mcp.tool()
def krita_set_active_layer(name: str) -> str:
    """Make a layer active (by name or id — see list_layers) — subsequent stroke/fill/draw_shape/
    clear commands target it."""
    result = send_command("set_active_layer", {"name": name})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Active layer set to '{name}'"


@mcp.tool()
def krita_set_layer_visible(name: str, visible: bool = True) -> str:
    """Show or hide a layer."""
    result = send_command("set_layer_visible", {"name": name, "visible": visible})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Layer '{name}' visibility set to {visible}"


@mcp.tool()
def krita_set_layer_opacity(name: str, opacity: float) -> str:
    """
    Set a layer's opacity.

    Args:
        name: Layer name
        opacity: 0-100
    """
    result = send_command("set_layer_opacity", {"name": name, "opacity": opacity})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Layer '{name}' opacity set to {opacity}"


@mcp.tool()
def krita_set_layer_blending_mode(name: str, mode: str) -> str:
    """
    Set a layer's blending mode.

    Args:
        name: Layer name
        mode: e.g. "normal", "multiply", "screen", "overlay", "addition", "darken", "lighten"
    """
    result = send_command("set_layer_blending_mode", {"name": name, "mode": mode})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Layer '{name}' blending mode set to {mode}"


@mcp.tool()
def krita_merge_layer_down(name: str) -> str:
    """Merge a layer with the visible layer beneath it."""
    result = send_command("merge_layer_down", {"name": name})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Merged into '{result.get('merged_name')}'"


@mcp.tool()
def krita_duplicate_layer(name: str) -> str:
    """Duplicate a layer (by name or id — see list_layers), inserting the copy directly above the
    original."""
    result = send_command("duplicate_layer", {"name": name})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Duplicated as '{result.get('name')}' (id={result.get('id')})"


@mcp.tool()
def krita_reorder_layer(name: str, direction: str) -> str:
    """
    Move a layer one step up or down in its parent's stacking order.

    Args:
        name: Layer name
        direction: "up" or "down"
    """
    result = send_command("reorder_layer", {"name": name, "direction": direction})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Moved '{name}' {direction}"


@mcp.tool()
def krita_scale_layer(width: int, height: int, name: Optional[str] = None, strategy: str = "Bicubic") -> str:
    """
    Resample a layer to new pixel dimensions in place (top-left corner stays fixed). Uses Krita's
    real resampling filters, not manual pixel math.

    Args:
        width, height: New size in pixels
        name: Layer name or id (default: the active layer)
        strategy: Resampling filter — "Bicubic" (default), "Bilinear", "Box", "Bell", "BSpline",
            "Lanczos3", "Mitchell", "Hermite" (an unrecognized name silently falls back to Bicubic)
    """
    params = {"width": width, "height": height, "strategy": strategy}
    if name:
        params["name"] = name
    result = send_command("scale_layer", params, timeout=60.0)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Scaled '{result.get('name')}' to {width}x{height}"


@mcp.tool()
def krita_rotate_layer(degrees: float, name: Optional[str] = None) -> str:
    """
    Rotate a layer in place around its own center.

    Args:
        degrees: Rotation angle in degrees (clockwise)
        name: Layer name or id (default: the active layer)
    """
    params = {"degrees": degrees}
    if name:
        params["name"] = name
    result = send_command("rotate_layer", params, timeout=60.0)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Rotated '{result.get('name')}' by {degrees} degrees"


@mcp.tool()
def krita_crop_layer(x: int, y: int, width: int, height: int, name: Optional[str] = None) -> str:
    """
    Crop a layer's content to a rectangle (in document coordinates).

    Args:
        x, y, width, height: Crop rectangle
        name: Layer name or id (default: the active layer)
    """
    params = {"x": x, "y": y, "width": width, "height": height}
    if name:
        params["name"] = name
    result = send_command("crop_layer", params, timeout=60.0)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Cropped '{result.get('name')}' to ({x}, {y}) {width}x{height}"


@mcp.tool()
def krita_shear_layer(angle_x: float = 0, angle_y: float = 0, name: Optional[str] = None) -> str:
    """
    Shear a layer in place.

    Args:
        angle_x, angle_y: Shear angle in degrees along each axis
        name: Layer name or id (default: the active layer)
    """
    params = {"angle_x": angle_x, "angle_y": angle_y}
    if name:
        params["name"] = name
    result = send_command("shear_layer", params, timeout=60.0)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Sheared '{result.get('name')}' ({angle_x}, {angle_y} degrees)"


# --- Selections ---

@mcp.tool()
def krita_select_rectangle(x: int, y: int, width: int, height: int) -> str:
    """Set the active selection to a rectangle. Fill/stroke/draw_shape are clipped to it until cleared."""
    result = send_command("select_rectangle", {"x": x, "y": y, "width": width, "height": height})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Selected rectangle at ({x}, {y}), {width}x{height}"


@mcp.tool()
def krita_select_all() -> str:
    """Select the entire canvas."""
    result = send_command("select_all", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Selected entire canvas"


@mcp.tool()
def krita_clear_selection() -> str:
    """Remove the active selection."""
    result = send_command("clear_selection", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Selection cleared"


@mcp.tool()
def krita_invert_selection() -> str:
    """Invert the active selection."""
    result = send_command("invert_selection", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Selection inverted"


@mcp.tool()
def krita_grow_selection(radius: int = 5) -> str:
    """Grow the active selection outward by radius pixels."""
    result = send_command("grow_selection", {"radius": radius})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Selection grown by {radius}px"


@mcp.tool()
def krita_shrink_selection(radius: int = 5) -> str:
    """Shrink the active selection inward by radius pixels."""
    result = send_command("shrink_selection", {"radius": radius})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Selection shrunk by {radius}px"


@mcp.tool()
def krita_feather_selection(radius: int = 5) -> str:
    """Feather (soften the edge of) the active selection."""
    result = send_command("feather_selection", {"radius": radius})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Selection feathered by {radius}px"


# --- Filters ---

@mcp.tool()
def krita_list_filters() -> str:
    """List filter IDs usable with krita_apply_filter."""
    result = send_command("list_filters", {})

    if "error" in result:
        return f"Error: {result['error']}"

    filters = result.get("filters", [])
    return f"Available filters ({len(filters)}):\n" + "\n".join(f"  - {f}" for f in filters)


@mcp.tool()
def krita_apply_filter(name: str, layer: Optional[str] = None, config: Optional[dict] = None) -> str:
    """
    Apply a named filter destructively to a layer's full bounds.

    Args:
        name: Filter ID (see krita_list_filters)
        layer: Layer name (default: active layer)
        config: Optional dict of filter-specific configuration properties
    """
    params = {"name": name}
    if layer:
        params["layer"] = layer
    if config:
        params["config"] = config
    result = send_command("apply_filter", params, timeout=60.0)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Applied filter '{name}' to layer '{result.get('layer')}'"


# --- Document ---

@mcp.tool()
def krita_document_info() -> str:
    """Report dimensions, color space, and file info for the active document."""
    result = send_command("document_info", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return (
        f"{result.get('name')} ({result.get('fileName') or 'unsaved'})\n"
        f"{result.get('width')}x{result.get('height')} @ {result.get('resolution')}ppi\n"
        f"Color: {result.get('colorModel')} {result.get('colorDepth')}, profile: {result.get('colorProfile')}"
    )


@mcp.tool()
def krita_set_color_space(color_model: str, color_depth: str, color_profile: str) -> str:
    """
    Convert the active document's color space. Use krita_list_color_profiles to find valid
    combinations. For an HDR-capable canvas, try color_depth="F16" or "F32" with a scene-linear
    profile (exact profile names vary by Krita install).

    Args:
        color_model: e.g. "RGBA", "LABA", "CMYKA"
        color_depth: e.g. "U8", "U16", "F16", "F32"
        color_profile: Profile name (see krita_list_color_profiles)
    """
    result = send_command("set_color_space", {
        "color_model": color_model,
        "color_depth": color_depth,
        "color_profile": color_profile,
    })

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Color space set to {color_model}/{color_depth}/{color_profile}"


@mcp.tool()
def krita_list_color_profiles(color_model: str = "RGBA", color_depth: str = "U8") -> str:
    """List color profile names available for a given color model + depth."""
    result = send_command("list_color_profiles", {"color_model": color_model, "color_depth": color_depth})

    if "error" in result:
        return f"Error: {result['error']}"

    profiles = result.get("profiles", [])
    return f"Profiles for {color_model}/{color_depth} ({len(profiles)}):\n" + "\n".join(f"  - {p}" for p in profiles)


@mcp.tool()
def krita_list_documents() -> str:
    """List all open documents."""
    result = send_command("list_documents", {})

    if "error" in result:
        return f"Error: {result['error']}"

    docs = result.get("documents", [])
    if not docs:
        return "No documents open"
    return "\n".join(
        f"  - {d['name']} ({d['width']}x{d['height']}) {d['fileName'] or 'unsaved'}" for d in docs
    )


@mcp.tool()
def krita_close_document(name: Optional[str] = None) -> str:
    """Close a document by name, or the active document if no name given."""
    result = send_command("close_document", {"name": name} if name else {})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Closed '{result.get('name')}'"


@mcp.tool()
def krita_quit() -> str:
    """
    Cleanly quit Krita: closes every open document without prompting to save, then quits the
    application. For unattended/scripted restarts (e.g. after a plugin-code change) — a raw
    process kill can leave Krita treating the next launch as crash recovery, and a plain
    close_document/window-close can still hit a "save changes?" prompt on some Krita builds with
    no human there to dismiss it; this avoids both. The MCP connection will drop once Krita
    exits — expect the next tool call to fail until the plugin's HTTP server is back up.
    """
    result = send_command("quit", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Krita is quitting"


@mcp.tool()
def krita_resize_canvas(x: int, y: int, width: int, height: int) -> str:
    """
    Resize the canvas without scaling pixels (repositions the content origin).

    Args:
        x, y: New position of the current content's top-left corner
        width, height: New canvas dimensions
    """
    result = send_command("resize_canvas", {"x": x, "y": y, "width": width, "height": height})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Canvas resized to {width}x{height}"


@mcp.tool()
def krita_crop_canvas(x: int, y: int, width: int, height: int) -> str:
    """Crop the canvas to a rectangle."""
    result = send_command("crop_canvas", {"x": x, "y": y, "width": width, "height": height})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Canvas cropped to {width}x{height} at ({x}, {y})"


@mcp.tool()
def krita_scale_canvas(width: int, height: int, strategy: str = "Bicubic") -> str:
    """
    Resample the whole canvas (all layers) to new pixel dimensions, preserving DPI. Uses Krita's
    real resampling filters, not manual pixel math. Unlike resize_canvas (which just repositions
    the content origin), this actually resamples pixels.

    Args:
        width, height: New size in pixels
        strategy: Resampling filter — "Bicubic" (default), "Bilinear", "Box", "Bell", "BSpline",
            "Lanczos3", "Mitchell", "Hermite" (an unrecognized name silently falls back to Bicubic)
    """
    result = send_command("scale_canvas", {"width": width, "height": height, "strategy": strategy}, timeout=60.0)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Canvas scaled to {width}x{height}"


@mcp.tool()
def krita_rotate_canvas(degrees: float) -> str:
    """Rotate the whole canvas (all layers), resizing the canvas to fit. degrees: clockwise angle."""
    result = send_command("rotate_canvas", {"degrees": degrees}, timeout=60.0)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Canvas rotated by {degrees} degrees"


@mcp.tool()
def krita_shear_canvas(angle_x: float = 0, angle_y: float = 0) -> str:
    """Shear the whole canvas (all layers). angle_x/angle_y: shear angle in degrees along each axis."""
    result = send_command("shear_canvas", {"angle_x": angle_x, "angle_y": angle_y}, timeout=60.0)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Canvas sheared ({angle_x}, {angle_y} degrees)"


@mcp.tool()
def krita_flatten_image() -> str:
    """Flatten all layers of the active document into one."""
    result = send_command("flatten_image", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Image flattened"


@mcp.tool()
def krita_export_layer(path: str, name: Optional[str] = None) -> str:
    """
    Export a single layer (default: active layer) to an image file, independent of the full canvas.

    Args:
        path: Full output file path
        name: Layer name (default: active layer)
    """
    params = {"path": path}
    if name:
        params["name"] = name
    result = send_command("export_layer", params, timeout=60.0)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Exported layer '{result.get('name')}' to {result.get('path')}"


# --- Native brush / smart fill / clipping / vector ---

@mcp.tool()
def krita_stroke_native(points: list[list[int]], pressure: float = 1.0) -> str:
    """
    Paint a stroke using Krita's real brush engine (respects the active brush preset's texture,
    bristles, scatter, spacing — e.g. an actual fur or latex-shine brush). Call krita_set_brush
    first to pick a preset; unlike krita_stroke, this is meaningless with no preset set beyond
    Krita's default round brush.

    Args:
        points: List of [x, y] coordinate pairs
        pressure: Simulated pen pressure (0.0 to 1.0)
    """
    if len(points) < 2:
        return "Error: Need at least 2 points for a stroke"

    result = send_command("stroke_native", {"points": points, "pressure": pressure})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Native stroke painted with {len(points)} points"


@mcp.tool()
def krita_flood_fill(
    x: int,
    y: int,
    tolerance: int = 20,
    contiguous: bool = True,
    bounds_x: Optional[int] = None,
    bounds_y: Optional[int] = None,
    bounds_width: Optional[int] = None,
    bounds_height: Optional[int] = None,
) -> str:
    """
    Flood-fill from a seed point with the current foreground color. For scripted use — a human
    should use Krita's own (much faster) Enclose-and-Fill tool instead.

    This is a pure-Python scanline fill, capped at 4,000,000px for performance. Bounded by the
    active selection if one exists; otherwise pass bounds_width/height (+ optional bounds_x/y) to
    scope it, or it falls back to the active layer's own content bounds. On a large canvas, make
    a selection around the target area first.

    Args:
        x, y: Seed point (canvas coordinates)
        tolerance: Max per-channel color distance (0-255) to still count as "connected"
        contiguous: True = classic flood fill; False = replace all matching pixels in bounds
        bounds_x, bounds_y, bounds_width, bounds_height: Explicit fill region (ignored if a
            selection is active)
    """
    params = {"x": x, "y": y, "tolerance": tolerance, "contiguous": contiguous}
    if bounds_width is not None:
        params["bounds_width"] = bounds_width
    if bounds_height is not None:
        params["bounds_height"] = bounds_height
    if bounds_x is not None:
        params["bounds_x"] = bounds_x
    if bounds_y is not None:
        params["bounds_y"] = bounds_y

    result = send_command("flood_fill", params, timeout=60.0)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Filled {result.get('filled_pixels')} pixels within {result.get('bounds')}"


@mcp.tool()
def krita_set_layer_clipping(name: str, clip: bool = True) -> str:
    """
    Toggle 'clip to layer below' on a layer — confines its paint to the alpha of the layer
    beneath it. Standard technique for keeping a shading/highlight layer inside line art.
    """
    result = send_command("set_layer_clipping", {"name": name, "clip": clip})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Layer '{name}' clip-to-below set to {clip}"


@mcp.tool()
def krita_add_svg_shapes(svg: str, layer: Optional[str] = None) -> str:
    """
    Add shapes parsed from an SVG string to a vector layer (must already exist — see
    krita_create_layer with type="vector").

    Args:
        svg: SVG markup (a <svg>...</svg> document, or a fragment of path/shape elements)
        layer: Vector layer name (default: active layer)
    """
    params = {"svg": svg}
    if layer:
        params["layer"] = layer
    result = send_command("add_svg_shapes", params)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Added {result.get('shapes_added')} shape(s) to '{result.get('layer')}'"


@mcp.tool()
def krita_export_layer_svg(layer: Optional[str] = None) -> str:
    """Export a vector layer's contents as SVG markup (default: active layer)."""
    result = send_command("export_layer_svg", {"layer": layer} if layer else {})

    if "error" in result:
        return f"Error: {result['error']}"
    return result.get("svg", "")


@mcp.tool()
def krita_list_shapes(layer: Optional[str] = None) -> str:
    """List vector shapes (name, type, bounding box) in a vector layer (default: active layer)."""
    result = send_command("list_shapes", {"layer": layer} if layer else {})

    if "error" in result:
        return f"Error: {result['error']}"

    shapes = result.get("shapes", [])
    if not shapes:
        return "No shapes"
    return "\n".join(
        f"  - {s['name']} [{s['type']}] bounds={s['bounds']}" for s in shapes
    )


# --- Animation ---

@mcp.tool()
def krita_animation_info(layer: Optional[str] = None) -> str:
    """Report animation timing for the active document (current frame, length, fps, clip range) and, optionally, a layer's animation state."""
    result = send_command("animation_info", {"layer": layer} if layer else {})

    if "error" in result:
        return f"Error: {result['error']}"
    lines = [
        f"Frame {result.get('currentTime')} / {result.get('animationLength')} @ {result.get('framesPerSecond')}fps",
        f"Clip range: {result.get('fullClipRangeStart')}-{result.get('fullClipRangeEnd')}",
    ]
    if "layer" in result:
        lines.append(
            f"Layer '{result['layer']}': animated={result.get('layerAnimated')}, "
            f"keyframe at current time={result.get('layerHasKeyframeAtCurrentTime')}"
        )
    return "\n".join(lines)


@mcp.tool()
def krita_set_current_time(time: int) -> str:
    """Move the animation playhead to a specific frame."""
    result = send_command("set_current_time", {"time": time})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Playhead at frame {result.get('currentTime')}"


@mcp.tool()
def krita_set_animation_range(start: int, end: int) -> str:
    """Set the document's full clip range (the animation's start/end frame)."""
    result = send_command("set_animation_range", {"start": start, "end": end})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Animation range set to {start}-{end}"


@mcp.tool()
def krita_enable_layer_animation(name: Optional[str] = None) -> str:
    """Enable animation (keyframe support) on a layer (default: active layer)."""
    result = send_command("enable_layer_animation", {"name": name} if name else {})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Layer '{result.get('name')}' animated={result.get('animated')}"


@mcp.tool()
def krita_list_actions(filter: str = "") -> str:
    """List registered Krita menu/toolbar action names matching a filter (debugging aid — e.g. to find the exact name of a timeline action)."""
    result = send_command("list_actions", {"filter": filter})

    if "error" in result:
        return f"Error: {result['error']}"

    actions = result.get("actions", [])
    if not actions:
        return "No matching actions"
    return f"Matching actions ({len(actions)}):\n" + "\n".join(f"  - {a}" for a in actions)


@mcp.tool()
def krita_stamp_on_frames(
    svg: str,
    frames: list[int],
    x: int = 0,
    y: int = 0,
    layer: Optional[str] = None,
) -> str:
    """
    Rasterize an SVG and paint it as pixels onto a paint layer's keyframes across multiple
    animation frames — e.g. stamping a prop, watermark, or guide shape identically at multiple
    points without hand-copying it. The layer must be a paint layer.

    This paints a flattened raster stamp per frame, NOT an editable vector path — true per-frame
    vector-layer keyframing was tried and confirmed broken (shapes always landed on frame 0's
    shared vector content regardless of timing fixes; best guess is Krita's blank-frame action
    reads Timeline docker UI state the scripting API can't drive). Raster keyframing is proven
    reliable, so this rasterizes once and stamps pixels instead.

    Keyframe creation is done by triggering Krita's own Create Blank Frame/Insert Keyframe action
    where no keyframe exists yet (there's no direct scripting call for that) — check the per-frame
    has_keyframe_after result and verify visually before relying on this across many frames.

    Args:
        svg: SVG markup to stamp (must have width/height on the root <svg> element)
        frames: List of frame numbers to stamp onto
        x, y: Top-left position on the canvas to stamp at
        layer: Paint layer name (default: active layer)
    """
    if len(frames) > 200:
        return f"Error: Too many frames ({len(frames)}), cap is 200 per call"

    params = {"svg": svg, "frames": frames, "x": x, "y": y}
    if layer:
        params["layer"] = layer
    result = send_command("stamp_on_frames", params, timeout=60.0)

    if "error" in result:
        return f"Error: {result['error']}"

    per_frame = result.get("frames", [])
    lines = [f"Stamped onto layer '{result.get('layer')}' (action used: {result.get('used_action')})"]
    for f in per_frame:
        flag = "OK" if f["has_keyframe_after"] else "NO KEYFRAME — check manually"
        lines.append(f"  - frame {f['frame']}: {flag}")
    return "\n".join(lines)


@mcp.tool()
def krita_create_colorize_mask(
    key_strokes: list[dict],
    layer: Optional[str] = None,
    name: str = "Colorize Mask",
    edge_detection: bool = False,
    edge_detection_size: float = 4.0,
    clean_up_amount: float = 0.0,
    limit_to_device_bounds: bool = False,
) -> str:
    """
    Create a Colorize Mask on a layer: mark regions of line art with "key stroke" colors and
    Krita's own lazy-fill segmentation flat-fills every enclosed region in one pass. Better
    suited to coloring line art than flood_fill — handles every region at once and tolerates
    small gaps in the linework.

    Args:
        key_strokes: One entry per fill color:
            {"color": "#rrggbb", "rects": [[x, y, w, h], ...], "transparent": bool}
            "rects" mark axis-aligned boxes where that color's key stroke is painted — rough
            scribble coverage, not an exact outline. Mark at most one entry "transparent": true
            for a region (e.g. line-art background) that should stay unfilled.
        layer: Paint layer to attach the mask to (default: the active layer)
        name: Name for the new mask
        edge_detection: Snap fill boundaries to nearby line art edges
        edge_detection_size: Edge search radius in pixels (only applies if edge_detection)
        clean_up_amount: 0-100, removes small stray fill regions
        limit_to_device_bounds: Restrict segmentation to the layer's own pixel bounds
    """
    result = send_command("create_colorize_mask", {
        "key_strokes": key_strokes,
        "layer": layer,
        "name": name,
        "edge_detection": edge_detection,
        "edge_detection_size": edge_detection_size,
        "clean_up_amount": clean_up_amount,
        "limit_to_device_bounds": limit_to_device_bounds,
    }, timeout=60.0)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Created colorize mask '{result.get('name')}' with {result.get('key_stroke_count')} key strokes"


@mcp.tool()
def krita_update_colorize_mask(mask: str, color: str, rects: list[list[int]], update: bool = True) -> str:
    """
    Repaint one key-stroke color's coverage on an existing colorize mask (adds rects for that
    color) and re-run the fill. The color must already be registered on this mask — i.e. it was
    one of the key_strokes passed to krita_create_colorize_mask.

    Args:
        mask: Name of the colorize mask
        color: Hex color of the key stroke to update — must match one used at creation
        rects: [[x, y, w, h], ...] regions to mark with this color
        update: Re-run the fill after writing (default True)
    """
    result = send_command("update_colorize_mask", {
        "mask": mask, "color": color, "rects": rects, "update": update
    }, timeout=60.0)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Updated colorize mask '{mask}' key stroke '{color}'"


if __name__ == "__main__":
    mcp.run()
