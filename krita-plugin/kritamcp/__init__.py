"""
Krita MCP Bridge - HTTP server for external paint commands in Krita
Allows Claude (or any MCP client) to paint by sending commands to this plugin.
"""

from krita import *
from PyQt5.QtCore import QTimer, QThread, pyqtSignal, QPoint, QPointF, QRectF
from PyQt5.QtGui import QColor, QImage
from PyQt5.QtWidgets import QMessageBox
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import os

# Configuration - customize these as needed
SERVER_PORT = 5678
CANVAS_OUTPUT_DIR = os.path.expanduser("~/krita-mcp-output")

class CommandQueue:
    """Thread-safe command queue for passing commands from HTTP thread to main thread."""
    def __init__(self):
        self.queue = []
        self.results = {}
        self.lock = threading.Lock()
        self.result_event = threading.Event()

    def push(self, command_id, command):
        with self.lock:
            self.queue.append((command_id, command))

    def pop(self):
        with self.lock:
            if self.queue:
                return self.queue.pop(0)
            return None

    def set_result(self, command_id, result):
        with self.lock:
            self.results[command_id] = result
        self.result_event.set()

    def get_result(self, command_id, timeout=120):
        """Wait for result with timeout.

        The default timeout of 120s is important — canvas export and save
        operations can take a long time on large canvases. The original 30s
        default caused frequent timeouts. The MCP server's send_command()
        timeout must match or exceed this value.
        """
        start = threading.Event()
        for _ in range(int(timeout * 10)):  # Check every 100ms
            with self.lock:
                if command_id in self.results:
                    result = self.results.pop(command_id)
                    return result
            self.result_event.wait(0.1)
            self.result_event.clear()
        return {"error": "Timeout waiting for command execution"}

# Global command queue
command_queue = CommandQueue()
command_counter = 0

class PaintRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for paint commands."""

    def log_message(self, format, *args):
        # Suppress HTTP logging
        pass

    def send_json_response(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        """Handle GET requests - mainly for health check."""
        parsed = urlparse(self.path)

        if parsed.path == '/health':
            self.send_json_response({"status": "ok", "plugin": "kritamcp"})
        elif parsed.path == '/info':
            self.send_json_response({
                "status": "ok",
                "canvas_dir": CANVAS_OUTPUT_DIR,
                "commands": [
                    "new_canvas", "set_color", "set_brush", "stroke",
                    "fill", "draw_shape", "get_canvas", "undo", "redo",
                    "clear", "save", "get_color_at", "list_brushes", "open_file",
                    "list_layers", "create_layer", "delete_layer", "set_active_layer",
                    "set_layer_visible", "set_layer_opacity", "set_layer_blending_mode",
                    "merge_layer_down", "duplicate_layer", "reorder_layer",
                    "select_rectangle", "select_all", "clear_selection", "invert_selection",
                    "grow_selection", "shrink_selection", "feather_selection",
                    "list_filters", "apply_filter",
                    "document_info", "set_color_space", "list_color_profiles",
                    "list_documents", "close_document", "resize_canvas", "crop_canvas",
                    "flatten_image", "export_layer",
                    "stroke_native", "flood_fill", "set_layer_clipping",
                    "add_svg_shapes", "export_layer_svg", "list_shapes",
                    "animation_info", "set_current_time", "set_animation_range",
                    "enable_layer_animation", "list_actions", "stamp_vector_on_frames"
                ]
            })
        else:
            self.send_json_response({"error": "Unknown endpoint"}, 404)

    def do_POST(self):
        """Handle POST requests - paint commands."""
        global command_counter

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')

        try:
            command = json.loads(body)
        except json.JSONDecodeError:
            self.send_json_response({"error": "Invalid JSON"}, 400)
            return

        # Assign command ID and queue it
        command_counter += 1
        command_id = command_counter
        command_queue.push(command_id, command)

        # Wait for result from main thread
        result = command_queue.get_result(command_id)

        if "error" in result:
            self.send_json_response(result, 500)
        else:
            self.send_json_response(result)


class ServerThread(QThread):
    """Thread to run HTTP server without blocking Krita UI."""

    def __init__(self, port):
        super().__init__()
        self.port = port
        self.server = None

    def run(self):
        self.server = HTTPServer(('localhost', self.port), PaintRequestHandler)
        self.server.serve_forever()

    def stop(self):
        if self.server:
            self.server.shutdown()


class KritaMCPExtension(Extension):
    """Main Krita extension class."""

    def __init__(self, parent):
        super().__init__(parent)
        self.server_thread = None
        self.timer = None
        self.current_brush_size = 20
        self.current_opacity = 1.0

    def setup(self):
        """Called when extension is loaded."""
        pass

    def createActions(self, window):
        """Called when a new window is created."""
        # Ensure output directory exists
        os.makedirs(CANVAS_OUTPUT_DIR, exist_ok=True)

        # Start HTTP server
        if self.server_thread is None:
            self.server_thread = ServerThread(SERVER_PORT)
            self.server_thread.start()
            print(f"[KritaMCP] HTTP server started on port {SERVER_PORT}")

        # Start timer to process command queue
        if self.timer is None:
            self.timer = QTimer()
            self.timer.timeout.connect(self.process_commands)
            self.timer.start(50)  # Check every 50ms

        # Native menu actions — usable without Claude/MCP at all, for a plain Krita user.
        # Only actions that make sense as a single click with no per-call parameters belong
        # here; scripted-only capabilities (flood_fill, stroke_native) stay HTTP-only, since a
        # human already has Krita's own (better) tools for the interactive equivalent.
        toggle_clip_action = window.createAction(
            "kritamcp_toggle_clip", "MCP: Toggle Clip to Layer Below", "tools/scripts"
        )
        toggle_clip_action.triggered.connect(self.action_toggle_clip)

        gradient_fill_action = window.createAction(
            "kritamcp_gradient_fill", "MCP: Fill Selection with Gradient (FG→BG)", "tools/scripts"
        )
        gradient_fill_action.triggered.connect(self.action_gradient_fill_selection)

    def action_toggle_clip(self):
        """Menu action: toggle 'clip to layer below' on the active layer."""
        doc = self.get_active_document()
        if not doc:
            return
        node = doc.activeNode()
        if not node:
            return
        node.setInheritAlpha(not node.inheritAlpha())
        doc.refreshProjection()

    def action_gradient_fill_selection(self):
        """Menu action: add a gradient fill layer over the current selection (or whole canvas)."""
        doc = self.get_active_document()
        if not doc:
            return
        selection = doc.selection()
        if selection is None:
            selection = Selection()
            selection.select(0, 0, doc.width(), doc.height(), 255)
        node = doc.createFillLayer("Gradient Fill", "gradient", InfoObject(), selection)
        if node:
            root = doc.rootNode()
            root.addChildNode(node, None)
            doc.refreshProjection()

    def process_commands(self):
        """Process commands from queue in main thread."""
        item = command_queue.pop()
        if item is None:
            return

        command_id, command = item
        result = self.execute_command(command)
        command_queue.set_result(command_id, result)

    def execute_command(self, command):
        """Execute a paint command and return result."""
        try:
            action = command.get("action")
            params = command.get("params", {})

            if action == "new_canvas":
                return self.cmd_new_canvas(params)
            elif action == "set_color":
                return self.cmd_set_color(params)
            elif action == "set_brush":
                return self.cmd_set_brush(params)
            elif action == "stroke":
                return self.cmd_stroke(params)
            elif action == "fill":
                return self.cmd_fill(params)
            elif action == "draw_shape":
                return self.cmd_draw_shape(params)
            elif action == "get_canvas":
                return self.cmd_get_canvas(params)
            elif action == "undo":
                return self.cmd_undo(params)
            elif action == "redo":
                return self.cmd_redo(params)
            elif action == "clear":
                return self.cmd_clear(params)
            elif action == "save":
                return self.cmd_save(params)
            elif action == "get_color_at":
                return self.cmd_get_color_at(params)
            elif action == "list_brushes":
                return self.cmd_list_brushes(params)
            elif action == "open_file":
                return self.cmd_open_file(params)
            elif action == "list_layers":
                return self.cmd_list_layers(params)
            elif action == "create_layer":
                return self.cmd_create_layer(params)
            elif action == "delete_layer":
                return self.cmd_delete_layer(params)
            elif action == "set_active_layer":
                return self.cmd_set_active_layer(params)
            elif action == "set_layer_visible":
                return self.cmd_set_layer_visible(params)
            elif action == "set_layer_opacity":
                return self.cmd_set_layer_opacity(params)
            elif action == "set_layer_blending_mode":
                return self.cmd_set_layer_blending_mode(params)
            elif action == "merge_layer_down":
                return self.cmd_merge_layer_down(params)
            elif action == "duplicate_layer":
                return self.cmd_duplicate_layer(params)
            elif action == "reorder_layer":
                return self.cmd_reorder_layer(params)
            elif action == "select_rectangle":
                return self.cmd_select_rectangle(params)
            elif action == "select_all":
                return self.cmd_select_all(params)
            elif action == "clear_selection":
                return self.cmd_clear_selection(params)
            elif action == "invert_selection":
                return self.cmd_invert_selection(params)
            elif action == "grow_selection":
                return self.cmd_grow_selection(params)
            elif action == "shrink_selection":
                return self.cmd_shrink_selection(params)
            elif action == "feather_selection":
                return self.cmd_feather_selection(params)
            elif action == "list_filters":
                return self.cmd_list_filters(params)
            elif action == "apply_filter":
                return self.cmd_apply_filter(params)
            elif action == "document_info":
                return self.cmd_document_info(params)
            elif action == "set_color_space":
                return self.cmd_set_color_space(params)
            elif action == "list_color_profiles":
                return self.cmd_list_color_profiles(params)
            elif action == "list_documents":
                return self.cmd_list_documents(params)
            elif action == "close_document":
                return self.cmd_close_document(params)
            elif action == "resize_canvas":
                return self.cmd_resize_canvas(params)
            elif action == "crop_canvas":
                return self.cmd_crop_canvas(params)
            elif action == "flatten_image":
                return self.cmd_flatten_image(params)
            elif action == "export_layer":
                return self.cmd_export_layer(params)
            elif action == "stroke_native":
                return self.cmd_stroke_native(params)
            elif action == "flood_fill":
                return self.cmd_flood_fill(params)
            elif action == "set_layer_clipping":
                return self.cmd_set_layer_clipping(params)
            elif action == "add_svg_shapes":
                return self.cmd_add_svg_shapes(params)
            elif action == "export_layer_svg":
                return self.cmd_export_layer_svg(params)
            elif action == "list_shapes":
                return self.cmd_list_shapes(params)
            elif action == "animation_info":
                return self.cmd_animation_info(params)
            elif action == "set_current_time":
                return self.cmd_set_current_time(params)
            elif action == "set_animation_range":
                return self.cmd_set_animation_range(params)
            elif action == "enable_layer_animation":
                return self.cmd_enable_layer_animation(params)
            elif action == "list_actions":
                return self.cmd_list_actions(params)
            elif action == "stamp_vector_on_frames":
                return self.cmd_stamp_vector_on_frames(params)
            else:
                return {"error": f"Unknown action: {action}"}

        except Exception as e:
            return {"error": str(e)}

    def get_active_document(self):
        """Get active document or return None."""
        app = Krita.instance()
        return app.activeDocument()

    def get_active_view(self):
        """Get active view or return None."""
        app = Krita.instance()
        window = app.activeWindow()
        if window:
            return window.activeView()
        return None

    def get_active_layer(self):
        """Get active paint layer."""
        doc = self.get_active_document()
        if doc:
            return doc.activeNode()
        return None

    def get_selection_mask(self, doc, x, y, w, h):
        """Per-pixel selectedness (0-255) for a region, or None if no selection is active.

        Krita's own selection() only clips its native brush/fill tools — since this
        plugin paints via setPixelData() directly, callers must multiply it in manually.
        """
        selection = doc.selection()
        if selection is None:
            return None
        return bytearray(selection.pixelData(x, y, w, h))

    def find_node(self, doc, name):
        """Find a node by name anywhere in the layer tree, or None."""
        return doc.nodeByName(name)

    def find_action(self, *keywords):
        """Find a registered QAction whose text contains all given keywords (case-insensitive).

        Krita's Python API has no direct 'insert a keyframe' method on Node — the timeline's
        Create Blank Frame/Insert Keyframe/etc. only exist as QActions. Matching by keyword instead of
        a hardcoded action id survives Krita renaming/relocalizing them across versions. The
        action()/actions()-based trigger pattern itself is already proven in this file (cmd_undo,
        cmd_redo use app.action('edit_undo'/'edit_redo').trigger()).
        """
        for action in Krita.instance().actions():
            text = (action.text() or "").lower()
            if all(k.lower() in text for k in keywords):
                return action
        return None

    def cmd_new_canvas(self, params):
        """Create a new canvas."""
        width = params.get("width", 800)
        height = params.get("height", 600)
        name = params.get("name", "New Canvas")
        bg_color = params.get("background", "#1a1a2e")

        app = Krita.instance()

        # Create document with background color
        doc = app.createDocument(width, height, name, "RGBA", "U8", "", 120.0)

        window = app.activeWindow()
        if window:
            window.addView(doc)

        # Create a paint layer
        root = doc.rootNode()
        layer = doc.createNode("paint", "paintlayer")
        root.addChildNode(layer, None)

        # Fill background using pixel data
        color = QColor(bg_color)
        r, g, b = color.red(), color.green(), color.blue()

        # Create pixel data for entire canvas (BGRA format)
        pixel_data = bytes([b, g, r, 255] * (width * height))
        layer.setPixelData(pixel_data, 0, 0, width, height)

        doc.refreshProjection()

        return {"status": "ok", "width": width, "height": height, "name": name}

    def cmd_set_color(self, params):
        """Set foreground color."""
        color_hex = params.get("color", "#ffffff")

        view = self.get_active_view()
        if not view:
            return {"error": "No active view"}

        color = QColor(color_hex)
        mc = ManagedColor.fromQColor(color, view.canvas())
        view.setForeGroundColor(mc)

        return {"status": "ok", "color": color_hex}

    def cmd_set_brush(self, params):
        """Set brush preset and size."""
        preset_name = params.get("preset", None)
        size = params.get("size", None)
        opacity = params.get("opacity", None)

        view = self.get_active_view()
        if not view:
            return {"error": "No active view"}

        if preset_name:
            # Find brush preset
            presets = Krita.instance().resources("preset")
            found = None
            for name, preset in presets.items():
                if preset_name.lower() in name.lower():
                    found = preset
                    break
            if found:
                view.setCurrentBrushPreset(found)
            else:
                return {"error": f"Brush preset not found: {preset_name}"}

        if size is not None:
            self.current_brush_size = size
            view.setBrushSize(size)

        if opacity is not None:
            self.current_opacity = opacity
            # Opacity is set per-stroke, store for later

        return {"status": "ok", "preset": preset_name, "size": size, "opacity": opacity}

    def cmd_stroke(self, params):
        """Paint a stroke along points using pixel-level drawing with soft edges."""
        points = params.get("points", [])
        brush_size = params.get("size", self.current_brush_size)
        hardness = params.get("hardness", 0.5)  # 0.0 = very soft, 1.0 = hard edge
        opacity = params.get("opacity", 1.0)

        if len(points) < 2:
            return {"error": "Need at least 2 points for a stroke"}

        layer = self.get_active_layer()
        if not layer:
            return {"error": "No active layer"}

        doc = self.get_active_document()
        view = self.get_active_view()

        if not view:
            return {"error": "No active view"}

        # Get current foreground color
        fg = view.foregroundColor()
        qcolor = fg.colorForCanvas(view.canvas())
        r, g, b = qcolor.red(), qcolor.green(), qcolor.blue()

        width = doc.width()
        height = doc.height()
        radius = max(1, brush_size // 2)

        # Calculate bounding box for all points plus brush radius
        min_x = max(0, int(min(p[0] for p in points)) - radius - 2)
        min_y = max(0, int(min(p[1] for p in points)) - radius - 2)
        max_x = min(width, int(max(p[0] for p in points)) + radius + 2)
        max_y = min(height, int(max(p[1] for p in points)) + radius + 2)

        w = max_x - min_x
        h = max_y - min_y

        if w <= 0 or h <= 0:
            return {"error": "Stroke out of bounds"}

        # Get existing pixel data for the affected region
        existing = layer.pixelData(min_x, min_y, w, h)
        pixels = bytearray(existing)
        sel_mask = self.get_selection_mask(doc, min_x, min_y, w, h)

        import math

        def draw_soft_circle(cx, cy, point_opacity=1.0):
            """Draw a soft circle with falloff at canvas coordinates."""
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    dist_sq = dx*dx + dy*dy
                    if dist_sq <= radius*radius:
                        px = int(cx) + dx - min_x
                        py = int(cy) + dy - min_y
                        if 0 <= px < w and 0 <= py < h:
                            # Calculate distance from center (0.0 to 1.0)
                            dist = math.sqrt(dist_sq) / radius if radius > 0 else 0

                            # Apply hardness curve
                            # hardness=1.0: sharp edge, hardness=0.0: gradual fade from center
                            if hardness >= 1.0:
                                alpha_factor = 1.0
                            else:
                                # Soft falloff: starts fading at hardness point
                                if dist < hardness:
                                    alpha_factor = 1.0
                                else:
                                    # Smooth falloff from hardness to edge
                                    falloff = (dist - hardness) / (1.0 - hardness) if hardness < 1.0 else 0
                                    alpha_factor = 1.0 - falloff

                            sel_factor = (sel_mask[py * w + px] / 255.0) if sel_mask else 1.0
                            final_alpha = int(255 * alpha_factor * opacity * point_opacity * sel_factor)

                            if final_alpha > 0:
                                idx = (py * w + px) * 4
                                # Alpha blending with existing pixel
                                existing_b = pixels[idx]
                                existing_g = pixels[idx+1]
                                existing_r = pixels[idx+2]
                                existing_a = pixels[idx+3]

                                # Simple alpha blend
                                blend = final_alpha / 255.0
                                new_r = int(existing_r * (1 - blend) + r * blend)
                                new_g = int(existing_g * (1 - blend) + g * blend)
                                new_b = int(existing_b * (1 - blend) + b * blend)
                                new_a = max(existing_a, final_alpha)

                                pixels[idx] = new_b
                                pixels[idx+1] = new_g
                                pixels[idx+2] = new_r
                                pixels[idx+3] = new_a

        def draw_line(x1, y1, x2, y2):
            """Draw a line using interpolation with soft brush circles."""
            dist = math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
            # More steps for smoother lines
            steps = max(1, int(dist / max(1, radius / 3)))

            for i in range(steps + 1):
                t = i / steps if steps > 0 else 0
                x = x1 + t * (x2 - x1)
                y = y1 + t * (y2 - y1)
                draw_soft_circle(x, y)

        # Draw soft circles at each point and lines between them
        for i in range(len(points)):
            draw_soft_circle(points[i][0], points[i][1])
            if i > 0:
                draw_line(points[i-1][0], points[i-1][1], points[i][0], points[i][1])

        layer.setPixelData(bytes(pixels), min_x, min_y, w, h)
        doc.refreshProjection()

        return {"status": "ok", "points_count": len(points), "hardness": hardness}

    def cmd_stroke_native(self, params):
        """
        Paint a stroke using Krita's real brush engine (Node.paintLine), not the pixel-direct
        soft-circle renderer `stroke` uses. Respects the active view's brush preset — its texture,
        bristle scatter, spacing, etc. — so a real fur/latex brush preset actually looks like one.
        Call set_brush first to pick a preset; this is meaningless with the default round brush.
        """
        points = params.get("points", [])
        if len(points) < 2:
            return {"error": "Need at least 2 points for a stroke"}
        pressure = params.get("pressure", 1.0)

        layer = self.get_active_layer()
        if not layer:
            return {"error": "No active layer"}
        doc = self.get_active_document()
        view = self.get_active_view()
        if not view:
            return {"error": "No active view"}

        for i in range(1, len(points)):
            p1 = QPoint(int(round(points[i - 1][0])), int(round(points[i - 1][1])))
            p2 = QPoint(int(round(points[i][0])), int(round(points[i][1])))
            layer.paintLine(p1, p2, pressure, pressure)

        doc.refreshProjection()
        return {"status": "ok", "points_count": len(points)}

    def cmd_flood_fill(self, params):
        """
        Flood-fill from a seed point with the current foreground color, for scripted/AI use where
        there's no human to click Krita's native (and much faster) Enclose-and-Fill tool.

        Pure-Python scanline fill — bounded by the active selection if one exists, otherwise by
        explicit bounds_x/y/width/height, otherwise the active layer's own content bounds, and
        capped at 4,000,000 px for performance. Make a selection around the area first on a large
        canvas.
        """
        x, y = params.get("x"), params.get("y")
        if x is None or y is None:
            return {"error": "x and y are required"}
        tolerance = params.get("tolerance", 20)
        contiguous = params.get("contiguous", True)

        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        layer = self.get_active_layer()
        if not layer:
            return {"error": "No active layer"}
        view = self.get_active_view()
        if not view:
            return {"error": "No active view"}

        selection = doc.selection()
        if selection:
            bx, by, bw, bh = selection.x(), selection.y(), selection.width(), selection.height()
        elif "bounds_width" in params and "bounds_height" in params:
            bw, bh = params["bounds_width"], params["bounds_height"]
            bx = params.get("bounds_x", max(0, x - bw // 2))
            by = params.get("bounds_y", max(0, y - bh // 2))
        else:
            b = layer.bounds()
            bx, by, bw, bh = b.x(), b.y(), b.width(), b.height()

        MAX_PIXELS = 4_000_000
        if bw <= 0 or bh <= 0:
            return {"error": "Fill bounds are empty"}
        if bw * bh > MAX_PIXELS:
            return {"error": f"Fill region too large ({bw}x{bh} = {bw*bh}px, cap {MAX_PIXELS}). Select a smaller area first."}
        if not (bx <= x < bx + bw and by <= y < by + bh):
            return {"error": "Seed point (x, y) is outside the fill bounds"}

        fg = view.foregroundColor()
        qcolor = fg.colorForCanvas(view.canvas())
        fr, fgc, fb = qcolor.red(), qcolor.green(), qcolor.blue()

        original = bytes(layer.pixelData(bx, by, bw, bh))  # read-only snapshot for matching
        pixels = bytearray(original)  # working copy we paint into
        sel_mask = self.get_selection_mask(doc, bx, by, bw, bh)

        def orig_px(i):
            idx = i * 4
            return original[idx + 2], original[idx + 1], original[idx], original[idx + 3]

        seed_idx = (y - by) * bw + (x - bx)
        seed_r, seed_g, seed_b, seed_a = orig_px(seed_idx)

        def matches(i):
            r, g, b, a = orig_px(i)
            return (
                abs(r - seed_r) <= tolerance
                and abs(g - seed_g) <= tolerance
                and abs(b - seed_b) <= tolerance
                and abs(a - seed_a) <= tolerance
            )

        def paint(i):
            sel_alpha = sel_mask[i] if sel_mask else 255
            if sel_alpha == 0:
                return
            idx = i * 4
            blend = sel_alpha / 255.0
            pixels[idx] = int(pixels[idx] * (1 - blend) + fb * blend)
            pixels[idx + 1] = int(pixels[idx + 1] * (1 - blend) + fgc * blend)
            pixels[idx + 2] = int(pixels[idx + 2] * (1 - blend) + fr * blend)
            pixels[idx + 3] = max(pixels[idx + 3], sel_alpha)

        filled = 0
        if contiguous:
            visited = bytearray(bw * bh)
            stack = [seed_idx]
            visited[seed_idx] = 1
            while stack:
                i = stack.pop()
                paint(i)
                filled += 1
                row, col = divmod(i, bw)
                for n in (i - 1 if col > 0 else -1, i + 1 if col < bw - 1 else -1,
                          i - bw if row > 0 else -1, i + bw if row < bh - 1 else -1):
                    if n >= 0 and not visited[n]:
                        visited[n] = 1
                        if matches(n):
                            stack.append(n)
        else:
            for i in range(bw * bh):
                if matches(i):
                    paint(i)
                    filled += 1

        layer.setPixelData(bytes(pixels), bx, by, bw, bh)
        doc.refreshProjection()
        return {"status": "ok", "filled_pixels": filled, "bounds": {"x": bx, "y": by, "width": bw, "height": bh}}

    def cmd_fill(self, params):
        """Fill a circular area with current color."""
        x = params.get("x", 0)
        y = params.get("y", 0)
        radius = params.get("radius", 50)

        layer = self.get_active_layer()
        if not layer:
            return {"error": "No active layer"}

        doc = self.get_active_document()
        view = self.get_active_view()

        if not view:
            return {"error": "No active view"}

        # Get current foreground color
        fg = view.foregroundColor()
        qcolor = fg.colorForCanvas(view.canvas())
        r, g, b = qcolor.red(), qcolor.green(), qcolor.blue()

        # Paint a filled circle using pixel data
        # Create a bounding box
        x1 = max(0, x - radius)
        y1 = max(0, y - radius)
        x2 = min(doc.width(), x + radius)
        y2 = min(doc.height(), y + radius)
        w = x2 - x1
        h = y2 - y1

        if w <= 0 or h <= 0:
            return {"error": "Fill area out of bounds"}

        # Get existing pixel data
        existing = layer.pixelData(x1, y1, w, h)
        pixels = bytearray(existing)
        sel_mask = self.get_selection_mask(doc, x1, y1, w, h)

        # Draw circle
        for py in range(h):
            for px in range(w):
                # Check if point is in circle
                dx = (x1 + px) - x
                dy = (y1 + py) - y
                if dx*dx + dy*dy <= radius*radius:
                    idx = (py * w + px) * 4
                    sel_alpha = sel_mask[py * w + px] if sel_mask else 255
                    if sel_alpha == 0:
                        continue
                    if sel_alpha == 255:
                        pixels[idx] = b      # B
                        pixels[idx+1] = g    # G
                        pixels[idx+2] = r    # R
                        pixels[idx+3] = 255  # A
                    else:
                        blend = sel_alpha / 255.0
                        pixels[idx] = int(pixels[idx] * (1 - blend) + b * blend)
                        pixels[idx+1] = int(pixels[idx+1] * (1 - blend) + g * blend)
                        pixels[idx+2] = int(pixels[idx+2] * (1 - blend) + r * blend)
                        pixels[idx+3] = max(pixels[idx+3], sel_alpha)

        layer.setPixelData(bytes(pixels), x1, y1, w, h)
        doc.refreshProjection()

        return {"status": "ok", "x": x, "y": y, "radius": radius}

    def cmd_draw_shape(self, params):
        """Draw a shape (rectangle, ellipse, line)."""
        shape = params.get("shape", "rectangle")
        x = params.get("x", 0)
        y = params.get("y", 0)
        width = params.get("width", 100)
        height = params.get("height", 100)
        fill = params.get("fill", True)

        layer = self.get_active_layer()
        if not layer:
            return {"error": "No active layer"}

        doc = self.get_active_document()
        view = self.get_active_view()

        if not view:
            return {"error": "No active view"}

        # Get current foreground color
        fg = view.foregroundColor()
        qcolor = fg.colorForCanvas(view.canvas())
        r, g, b = qcolor.red(), qcolor.green(), qcolor.blue()

        if shape == "line":
            # Draw line using pixel data
            x2 = params.get("x2", x + width)
            y2 = params.get("y2", y + height)
            line_width = params.get("line_width", 2)

            # Calculate bounding box
            x1_bound = max(0, int(min(x, x2)) - line_width)
            y1_bound = max(0, int(min(y, y2)) - line_width)
            x2_bound = min(doc.width(), int(max(x, x2)) + line_width)
            y2_bound = min(doc.height(), int(max(y, y2)) + line_width)
            w = x2_bound - x1_bound
            h = y2_bound - y1_bound

            if w > 0 and h > 0:
                existing = layer.pixelData(x1_bound, y1_bound, w, h)
                pixels = bytearray(existing)

                # Draw line with thickness
                dist = max(abs(x2 - x), abs(y2 - y))
                steps = max(1, int(dist))
                radius = max(1, line_width // 2)

                for i in range(steps + 1):
                    t = i / steps if steps > 0 else 0
                    cx = x + t * (x2 - x)
                    cy = y + t * (y2 - y)
                    for dy in range(-radius, radius + 1):
                        for dx in range(-radius, radius + 1):
                            if dx*dx + dy*dy <= radius*radius:
                                px = int(cx) + dx - x1_bound
                                py = int(cy) + dy - y1_bound
                                if 0 <= px < w and 0 <= py < h:
                                    idx = (py * w + px) * 4
                                    pixels[idx] = b
                                    pixels[idx+1] = g
                                    pixels[idx+2] = r
                                    pixels[idx+3] = 255

                layer.setPixelData(bytes(pixels), x1_bound, y1_bound, w, h)
        elif shape == "rectangle" and fill:
            # Draw filled rectangle using pixel data
            x1 = max(0, int(x))
            y1 = max(0, int(y))
            x2 = min(doc.width(), int(x + width))
            y2 = min(doc.height(), int(y + height))
            w = x2 - x1
            h = y2 - y1

            if w > 0 and h > 0:
                sel_mask = self.get_selection_mask(doc, x1, y1, w, h)
                if sel_mask is None:
                    pixel_data = bytes([b, g, r, 255] * (w * h))
                    layer.setPixelData(pixel_data, x1, y1, w, h)
                else:
                    pixels = bytearray(layer.pixelData(x1, y1, w, h))
                    for i in range(w * h):
                        idx = i * 4
                        sel_alpha = sel_mask[i]
                        if sel_alpha == 0:
                            continue
                        blend = sel_alpha / 255.0
                        pixels[idx] = int(pixels[idx] * (1 - blend) + b * blend)
                        pixels[idx+1] = int(pixels[idx+1] * (1 - blend) + g * blend)
                        pixels[idx+2] = int(pixels[idx+2] * (1 - blend) + r * blend)
                        pixels[idx+3] = max(pixels[idx+3], sel_alpha)
                    layer.setPixelData(bytes(pixels), x1, y1, w, h)
        elif shape == "ellipse" and fill:
            # Draw filled ellipse using pixel data
            cx = x + width / 2
            cy = y + height / 2
            rx = width / 2
            ry = height / 2

            x1 = max(0, int(x))
            y1 = max(0, int(y))
            x2 = min(doc.width(), int(x + width))
            y2 = min(doc.height(), int(y + height))
            w = x2 - x1
            h = y2 - y1

            if w > 0 and h > 0:
                existing = layer.pixelData(x1, y1, w, h)
                pixels = bytearray(existing)
                sel_mask = self.get_selection_mask(doc, x1, y1, w, h)

                for py in range(h):
                    for px in range(w):
                        # Check if point is in ellipse
                        dx = (x1 + px - cx) / rx if rx > 0 else 0
                        dy = (y1 + py - cy) / ry if ry > 0 else 0
                        if dx*dx + dy*dy <= 1:
                            sel_alpha = sel_mask[py * w + px] if sel_mask else 255
                            if sel_alpha == 0:
                                continue
                            idx = (py * w + px) * 4
                            if sel_alpha == 255:
                                pixels[idx] = b
                                pixels[idx+1] = g
                                pixels[idx+2] = r
                                pixels[idx+3] = 255
                            else:
                                blend = sel_alpha / 255.0
                                pixels[idx] = int(pixels[idx] * (1 - blend) + b * blend)
                                pixels[idx+1] = int(pixels[idx+1] * (1 - blend) + g * blend)
                                pixels[idx+2] = int(pixels[idx+2] * (1 - blend) + r * blend)
                                pixels[idx+3] = max(pixels[idx+3], sel_alpha)

                layer.setPixelData(bytes(pixels), x1, y1, w, h)
        else:
            return {"error": f"Shape '{shape}' with current options not supported"}

        doc.refreshProjection()

        return {"status": "ok", "shape": shape}

    def cmd_get_canvas(self, params):
        """Export current canvas to file and return path."""
        filename = params.get("filename", "canvas.png")

        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        # Ensure filename has extension
        if not filename.endswith('.png'):
            filename += '.png'

        filepath = os.path.join(CANVAS_OUTPUT_DIR, filename)

        # Export image (batch mode suppresses export dialog)
        doc.setBatchmode(True)
        doc.exportImage(filepath, InfoObject())
        doc.setBatchmode(False)

        return {"status": "ok", "path": filepath}

    def cmd_undo(self, params):
        """Undo last action."""
        app = Krita.instance()
        action = app.action('edit_undo')
        if action:
            action.trigger()
            return {"status": "ok"}
        return {"error": "Could not trigger undo"}

    def cmd_redo(self, params):
        """Redo last undone action."""
        app = Krita.instance()
        action = app.action('edit_redo')
        if action:
            action.trigger()
            return {"status": "ok"}
        return {"error": "Could not trigger redo"}

    def cmd_clear(self, params):
        """Clear the canvas."""
        layer = self.get_active_layer()
        if not layer:
            return {"error": "No active layer"}

        doc = self.get_active_document()

        # Get canvas dimensions
        width = doc.width()
        height = doc.height()

        # Clear by filling with background color
        bg_color = params.get("color", "#1a1a2e")
        color = QColor(bg_color)
        r, g, b = color.red(), color.green(), color.blue()

        # Fill entire layer with color
        pixel_data = bytes([b, g, r, 255] * (width * height))
        layer.setPixelData(pixel_data, 0, 0, width, height)

        doc.refreshProjection()

        return {"status": "ok", "color": bg_color}

    def cmd_save(self, params):
        """Save to specific path."""
        filepath = params.get("path")
        if not filepath:
            return {"error": "No path specified"}

        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        # Batch mode suppresses export dialog
        doc.setBatchmode(True)
        doc.exportImage(filepath, InfoObject())
        doc.setBatchmode(False)

        return {"status": "ok", "path": filepath}

    def cmd_get_color_at(self, params):
        """Get color at specific pixel (eyedropper)."""
        x = params.get("x", 0)
        y = params.get("y", 0)

        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        # Get projection pixel data at point
        layer = doc.rootNode()
        # bytearray(), not raw QByteArray — indexing a QByteArray yields 1-byte `bytes`
        # objects on this PyQt5/sip version, not int, which breaks "{:02x}".format() below.
        pixel_data = bytearray(layer.projectionPixelData(x, y, 1, 1))

        if len(pixel_data) >= 4:
            # RGBA
            b, g, r, a = pixel_data[0], pixel_data[1], pixel_data[2], pixel_data[3]
            hex_color = "#{:02x}{:02x}{:02x}".format(r, g, b)
            return {"status": "ok", "color": hex_color, "r": r, "g": g, "b": b, "a": a}

        return {"error": "Could not read pixel"}

    def cmd_list_brushes(self, params):
        """List available brush presets."""
        filter_str = params.get("filter", "")
        limit = params.get("limit", 50)

        presets = Krita.instance().resources("preset")
        brush_list = []

        for name, preset in presets.items():
            if filter_str.lower() in name.lower():
                brush_list.append(name)
                if len(brush_list) >= limit:
                    break

        return {"status": "ok", "brushes": brush_list, "count": len(brush_list)}

    def cmd_open_file(self, params):
        """Open an existing file in Krita."""
        filepath = params.get("path")
        if not filepath:
            return {"error": "No path specified"}

        if not os.path.exists(filepath):
            return {"error": f"File not found: {filepath}"}

        app = Krita.instance()

        # Open the document
        doc = app.openDocument(filepath)
        if not doc:
            return {"error": f"Failed to open: {filepath}"}

        # Add view to active window
        window = app.activeWindow()
        if window:
            window.addView(doc)

        return {"status": "ok", "path": filepath, "name": doc.name(), "width": doc.width(), "height": doc.height()}

    # --- Layers ---

    LAYER_TYPE_ALIASES = {"paint": "paintlayer", "group": "grouplayer", "vector": "vectorlayer", "fill": "filllayer"}

    def _node_info(self, node):
        return {
            "name": node.name(),
            "type": node.type(),
            "visible": node.visible(),
            "opacity": node.opacity(),
            "blendingMode": node.blendingMode(),
            "children": [self._node_info(c) for c in node.childNodes()],
        }

    def cmd_list_layers(self, params):
        """List the layer tree (bottom-to-top per group) of the active document."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        root = doc.rootNode()
        return {"status": "ok", "layers": [self._node_info(c) for c in root.childNodes()]}

    def cmd_create_layer(self, params):
        """Create a new layer (paint, group, or vector) under a parent (default: root)."""
        name = params.get("name", "New Layer")
        layer_type = params.get("type", "paint")
        parent_name = params.get("parent")

        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        node_type = self.LAYER_TYPE_ALIASES.get(layer_type, layer_type)
        if node_type == "grouplayer":
            node = doc.createGroupLayer(name)
        elif node_type == "vectorlayer":
            node = doc.createVectorLayer(name)
        elif node_type == "filllayer":
            generator = params.get("generator")
            if not generator:
                return {"error": "type='fill' requires a 'generator' param, e.g. 'gradient', 'pattern', 'color' (see list_filters — generator config keys are generator-specific)"}
            config = InfoObject()
            if params.get("config"):
                config.setProperties(params["config"])
            selection = doc.selection()
            if selection is None:
                selection = Selection()
                selection.select(0, 0, doc.width(), doc.height(), 255)
            node = doc.createFillLayer(name, generator, config, selection)
        else:
            node = doc.createNode(name, node_type)

        if not node:
            return {"error": f"Failed to create layer of type: {layer_type}"}

        parent = self.find_node(doc, parent_name) if parent_name else doc.rootNode()
        if not parent:
            return {"error": f"Parent layer not found: {parent_name}"}

        parent.addChildNode(node, None)
        doc.refreshProjection()
        return {"status": "ok", "name": node.name(), "type": node.type()}

    def cmd_delete_layer(self, params):
        """Remove a layer by name."""
        node = self.find_node(self.get_active_document(), params.get("name"))
        if not node:
            return {"error": f"Layer not found: {params.get('name')}"}
        node.remove()
        self.get_active_document().refreshProjection()
        return {"status": "ok", "name": params.get("name")}

    def cmd_set_active_layer(self, params):
        """Make a layer the active one (subsequent paint/fill/shape commands target it)."""
        doc = self.get_active_document()
        node = self.find_node(doc, params.get("name"))
        if not node:
            return {"error": f"Layer not found: {params.get('name')}"}
        doc.setActiveNode(node)
        return {"status": "ok", "name": params.get("name")}

    def cmd_set_layer_visible(self, params):
        """Show or hide a layer."""
        doc = self.get_active_document()
        node = self.find_node(doc, params.get("name"))
        if not node:
            return {"error": f"Layer not found: {params.get('name')}"}
        node.setVisible(params.get("visible", True))
        doc.refreshProjection()
        return {"status": "ok", "name": params.get("name"), "visible": params.get("visible", True)}

    def cmd_set_layer_opacity(self, params):
        """Set a layer's opacity (0-100)."""
        doc = self.get_active_document()
        node = self.find_node(doc, params.get("name"))
        if not node:
            return {"error": f"Layer not found: {params.get('name')}"}
        opacity = params.get("opacity", 100)
        node.setOpacity(int(round(opacity / 100 * 255)))
        doc.refreshProjection()
        return {"status": "ok", "name": params.get("name"), "opacity": opacity}

    def cmd_set_layer_blending_mode(self, params):
        """Set a layer's blending mode (e.g. 'normal', 'multiply', 'screen', 'overlay')."""
        doc = self.get_active_document()
        node = self.find_node(doc, params.get("name"))
        if not node:
            return {"error": f"Layer not found: {params.get('name')}"}
        node.setBlendingMode(params.get("mode", "normal"))
        doc.refreshProjection()
        return {"status": "ok", "name": params.get("name"), "mode": params.get("mode")}

    def cmd_merge_layer_down(self, params):
        """Merge a layer with the visible layer beneath it."""
        doc = self.get_active_document()
        node = self.find_node(doc, params.get("name"))
        if not node:
            return {"error": f"Layer not found: {params.get('name')}"}
        merged = node.mergeDown()
        doc.refreshProjection()
        return {"status": "ok", "merged_name": merged.name() if merged else None}

    def cmd_duplicate_layer(self, params):
        """Duplicate a layer, inserting the copy directly above the original."""
        doc = self.get_active_document()
        node = self.find_node(doc, params.get("name"))
        if not node:
            return {"error": f"Layer not found: {params.get('name')}"}
        dup = node.duplicate()
        parent = node.parentNode() or doc.rootNode()
        parent.addChildNode(dup, node)
        doc.refreshProjection()
        return {"status": "ok", "name": dup.name()}

    def cmd_reorder_layer(self, params):
        """Move a layer one step up or down within its parent's stacking order."""
        doc = self.get_active_document()
        node = self.find_node(doc, params.get("name"))
        if not node:
            return {"error": f"Layer not found: {params.get('name')}"}

        parent = node.parentNode()
        if not parent:
            return {"error": "Cannot reorder the root node"}

        siblings = parent.childNodes()  # bottom-to-top
        try:
            idx = siblings.index(node)
        except ValueError:
            return {"error": "Layer not found among its parent's children"}

        direction = params.get("direction", "up")
        if direction == "up":
            if idx + 1 >= len(siblings):
                return {"error": "Layer is already at the top"}
            target = siblings[idx + 1]
        elif direction == "down":
            if idx - 1 < 0:
                return {"error": "Layer is already at the bottom"}
            target = siblings[idx - 1]
        else:
            return {"error": "direction must be 'up' or 'down'"}

        parent.removeChildNode(node)
        parent.addChildNode(node, target)
        doc.refreshProjection()
        return {"status": "ok", "name": params.get("name"), "direction": direction}

    def cmd_set_layer_clipping(self, params):
        """
        Toggle 'clip to layer below' (Node.setInheritAlpha) — confines this layer's paint to the
        alpha of the layer beneath it. Standard technique for keeping a shading/highlight layer
        inside existing line art without needing a selection.
        """
        doc = self.get_active_document()
        node = self.find_node(doc, params.get("name"))
        if not node:
            return {"error": f"Layer not found: {params.get('name')}"}
        clip = params.get("clip", True)
        node.setInheritAlpha(clip)
        doc.refreshProjection()
        return {"status": "ok", "name": params.get("name"), "clip": clip}

    # --- Selections ---

    def cmd_select_rectangle(self, params):
        """Set the active selection to a rectangle."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        x, y = params.get("x", 0), params.get("y", 0)
        w, h = params.get("width", 100), params.get("height", 100)
        sel = Selection()
        sel.select(x, y, w, h, params.get("value", 255))
        doc.setSelection(sel)
        return {"status": "ok", "x": x, "y": y, "width": w, "height": h}

    def cmd_select_all(self, params):
        """Select the entire canvas."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        sel = Selection()
        sel.select(0, 0, doc.width(), doc.height(), 255)
        doc.setSelection(sel)
        return {"status": "ok"}

    def cmd_clear_selection(self, params):
        """Remove the active selection (nothing/everything, per subsequent tool)."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        doc.setSelection(None)
        return {"status": "ok"}

    def cmd_invert_selection(self, params):
        doc = self.get_active_document()
        sel = doc.selection() if doc else None
        if not sel:
            return {"error": "No active selection"}
        sel.invert()
        doc.setSelection(sel)
        return {"status": "ok"}

    def cmd_grow_selection(self, params):
        doc = self.get_active_document()
        sel = doc.selection() if doc else None
        if not sel:
            return {"error": "No active selection"}
        radius = params.get("radius", 5)
        sel.grow(radius, radius)
        doc.setSelection(sel)
        return {"status": "ok", "radius": radius}

    def cmd_shrink_selection(self, params):
        doc = self.get_active_document()
        sel = doc.selection() if doc else None
        if not sel:
            return {"error": "No active selection"}
        radius = params.get("radius", 5)
        sel.shrink(radius, radius, params.get("edge_lock", False))
        doc.setSelection(sel)
        return {"status": "ok", "radius": radius}

    def cmd_feather_selection(self, params):
        doc = self.get_active_document()
        sel = doc.selection() if doc else None
        if not sel:
            return {"error": "No active selection"}
        radius = params.get("radius", 5)
        sel.feather(radius)
        doc.setSelection(sel)
        return {"status": "ok", "radius": radius}

    # --- Filters ---

    def cmd_list_filters(self, params):
        """List registered filter IDs usable with apply_filter."""
        return {"status": "ok", "filters": Krita.instance().filters()}

    def cmd_apply_filter(self, params):
        """Apply a named filter (destructively) to a layer's full bounds."""
        name = params.get("name")
        if not name:
            return {"error": "No filter name specified"}

        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}

        layer_name = params.get("layer")
        node = self.find_node(doc, layer_name) if layer_name else self.get_active_layer()
        if not node:
            return {"error": f"Layer not found: {layer_name}" if layer_name else "No active layer"}

        filt = Krita.instance().filter(name)
        if not filt:
            return {"error": f"Unknown filter: {name}. See list_filters for valid names."}

        config = params.get("config")
        if config:
            info = InfoObject()
            info.setProperties(config)
            filt.setConfiguration(info)

        bounds = node.bounds()
        filt.apply(node, bounds.x(), bounds.y(), bounds.width(), bounds.height())
        doc.refreshProjection()
        return {"status": "ok", "filter": name, "layer": node.name()}

    # --- Document ---

    def cmd_document_info(self, params):
        """Report dimensions, color space, and file info for the active document."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        return {
            "status": "ok",
            "name": doc.name(),
            "fileName": doc.fileName(),
            "width": doc.width(),
            "height": doc.height(),
            "colorModel": doc.colorModel(),
            "colorDepth": doc.colorDepth(),
            "colorProfile": doc.colorProfile(),
            "resolution": doc.resolution(),
        }

    def cmd_set_color_space(self, params):
        """Convert the active document's color model/depth/profile (e.g. for HDR: RGBA/F16/scRGB-linear)."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        color_model = params.get("color_model")
        color_depth = params.get("color_depth")
        color_profile = params.get("color_profile")
        ok = doc.setColorSpace(color_model, color_depth, color_profile)
        if not ok:
            return {"error": "Failed to set color space — check list_color_profiles for valid model/depth/profile combinations"}
        doc.refreshProjection()
        return {"status": "ok", "colorModel": color_model, "colorDepth": color_depth, "colorProfile": color_profile}

    def cmd_list_color_profiles(self, params):
        """List color profile names available for a given color model + depth."""
        color_model = params.get("color_model", "RGBA")
        color_depth = params.get("color_depth", "U8")
        profiles = Krita.instance().profiles(color_model, color_depth)
        return {"status": "ok", "profiles": profiles}

    def cmd_list_documents(self, params):
        """List all open documents."""
        docs = Krita.instance().documents()
        return {
            "status": "ok",
            "documents": [
                {"name": d.name(), "fileName": d.fileName(), "width": d.width(), "height": d.height()}
                for d in docs
            ],
        }

    def cmd_close_document(self, params):
        """Close a document by name, or the active document if no name given."""
        name = params.get("name")
        app = Krita.instance()
        if name:
            target = next((d for d in app.documents() if d.name() == name), None)
            if not target:
                return {"error": f"Document not found: {name}"}
        else:
            target = self.get_active_document()
            if not target:
                return {"error": "No active document"}
        closed_name = target.name()
        # Guard against a "save changes?" prompt blocking the event loop, same as export_layer.
        target.setBatchmode(True)
        target.close()
        return {"status": "ok", "name": closed_name}

    def cmd_resize_canvas(self, params):
        """Resize the canvas (repositions the content origin; does not scale pixels)."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        x, y = params.get("x", 0), params.get("y", 0)
        w, h = params.get("width", doc.width()), params.get("height", doc.height())
        doc.resizeImage(x, y, w, h)
        doc.refreshProjection()
        return {"status": "ok", "x": x, "y": y, "width": w, "height": h}

    def cmd_crop_canvas(self, params):
        """Crop the canvas to a rectangle."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        x, y = params.get("x", 0), params.get("y", 0)
        w, h = params.get("width", doc.width()), params.get("height", doc.height())
        doc.crop(x, y, w, h)
        doc.refreshProjection()
        return {"status": "ok", "x": x, "y": y, "width": w, "height": h}

    def cmd_flatten_image(self, params):
        """Flatten all layers into one."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        doc.flatten()
        doc.refreshProjection()
        return {"status": "ok"}

    def cmd_export_layer(self, params):
        """Export a single layer (default: active layer) to an image file."""
        path = params.get("path")
        if not path:
            return {"error": "No path specified"}
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        layer_name = params.get("name")
        node = self.find_node(doc, layer_name) if layer_name else self.get_active_layer()
        if not node:
            return {"error": f"Layer not found: {layer_name}" if layer_name else "No active layer"}

        # Node.save() shows Krita's PNG/JPG export-options dialog regardless of
        # doc.setBatchmode() — unlike Document.exportImage(), which get_canvas/save already
        # use safely. Building the QImage ourselves and saving via Qt (not Krita's importer/
        # exporter) sidesteps that dialog entirely.
        bounds = node.bounds()
        if bounds.width() <= 0 or bounds.height() <= 0:
            return {"error": f"Layer '{node.name()}' has no content to export"}

        pixel_data = node.pixelData(bounds.x(), bounds.y(), bounds.width(), bounds.height())
        image = QImage(pixel_data, bounds.width(), bounds.height(), QImage.Format_ARGB32)
        if not image.save(path):
            return {"error": f"Failed to save image to {path}"}

        return {
            "status": "ok",
            "name": node.name(),
            "path": path,
            "width": bounds.width(),
            "height": bounds.height(),
        }

    # --- Vector / SVG ---

    def cmd_add_svg_shapes(self, params):
        """Add shapes parsed from an SVG string to a vector layer (default: active layer)."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        svg = params.get("svg")
        if not svg:
            return {"error": "No svg content provided"}
        layer_name = params.get("layer")
        node = self.find_node(doc, layer_name) if layer_name else self.get_active_layer()
        if not node:
            return {"error": f"Layer not found: {layer_name}" if layer_name else "No active layer"}
        if node.type() != "vectorlayer":
            return {"error": f"Layer '{node.name()}' is not a vector layer"}
        shapes = node.addShapesFromSvg(svg)
        doc.refreshProjection()
        return {"status": "ok", "layer": node.name(), "shapes_added": len(shapes)}

    def cmd_export_layer_svg(self, params):
        """Export a vector layer's contents as SVG markup (default: active layer)."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        layer_name = params.get("layer") or params.get("name")
        node = self.find_node(doc, layer_name) if layer_name else self.get_active_layer()
        if not node:
            return {"error": f"Layer not found: {layer_name}" if layer_name else "No active layer"}
        if node.type() != "vectorlayer":
            return {"error": f"Layer '{node.name()}' is not a vector layer"}
        return {"status": "ok", "layer": node.name(), "svg": node.toSvg()}

    def cmd_list_shapes(self, params):
        """List vector shapes (name, type, bounding box) in a vector layer (default: active layer)."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        layer_name = params.get("layer") or params.get("name")
        node = self.find_node(doc, layer_name) if layer_name else self.get_active_layer()
        if not node:
            return {"error": f"Layer not found: {layer_name}" if layer_name else "No active layer"}
        if node.type() != "vectorlayer":
            return {"error": f"Layer '{node.name()}' is not a vector layer"}
        shapes = node.shapes()
        return {
            "status": "ok",
            "layer": node.name(),
            "shapes": [
                {
                    "name": s.name(),
                    "type": s.type(),
                    "bounds": {
                        "x": s.boundingBox().x(),
                        "y": s.boundingBox().y(),
                        "width": s.boundingBox().width(),
                        "height": s.boundingBox().height(),
                    },
                }
                for s in shapes
            ],
        }

    # --- Animation ---

    def cmd_animation_info(self, params):
        """Report animation timing for the active document and (optionally) a specific layer."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        info = {
            "status": "ok",
            "currentTime": doc.currentTime(),
            "animationLength": doc.animationLength(),
            "framesPerSecond": doc.framesPerSecond(),
            "fullClipRangeStart": doc.fullClipRangeStartTime(),
            "fullClipRangeEnd": doc.fullClipRangeEndTime(),
        }
        layer_name = params.get("layer")
        node = self.find_node(doc, layer_name) if layer_name else self.get_active_layer()
        if node:
            info["layer"] = node.name()
            info["layerAnimated"] = node.animated()
            info["layerHasKeyframeAtCurrentTime"] = node.hasKeyframeAtTime(doc.currentTime())
        return info

    def cmd_set_current_time(self, params):
        """Move the animation playhead to a specific frame."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        time = params.get("time")
        if time is None:
            return {"error": "time is required"}
        doc.setCurrentTime(time)
        return {"status": "ok", "currentTime": doc.currentTime()}

    def cmd_set_animation_range(self, params):
        """Set the document's full clip range (the animation's start/end frame)."""
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        start = params.get("start")
        end = params.get("end")
        if start is None or end is None:
            return {"error": "start and end are required"}
        doc.setFullClipRangeStartTime(start)
        doc.setFullClipRangeEndTime(end)
        return {"status": "ok", "start": start, "end": end}

    def cmd_enable_layer_animation(self, params):
        """Enable animation (keyframe support) on a layer."""
        doc = self.get_active_document()
        layer_name = params.get("name")
        node = self.find_node(doc, layer_name) if layer_name else self.get_active_layer()
        if not node:
            return {"error": f"Layer not found: {layer_name}" if layer_name else "No active layer"}
        if not node.animated():
            node.enableAnimation()
        return {"status": "ok", "name": node.name(), "animated": node.animated()}

    def cmd_list_actions(self, params):
        """List registered Krita action names matching a filter (debugging aid for find_action)."""
        filter_str = params.get("filter", "").lower()
        names = [a.text() for a in Krita.instance().actions() if a.text() and filter_str in a.text().lower()]
        return {"status": "ok", "actions": names}

    def cmd_stamp_vector_on_frames(self, params):
        """
        Add the same SVG shape(s) as a new keyframe on a vector layer at each of several frames —
        e.g. a watermark, a prop, or a guide shape that should appear identically at multiple
        points in an animation without hand-copying it frame by frame.

        The layer must be a vector layer (see create_layer type="vector"). Animation is enabled on
        it automatically if not already. For each frame: if the layer has no keyframe there yet,
        this tries to insert one via Krita's own Create Blank Frame / Insert Keyframe action (found by
        matching action text — there's no direct scripting call for this), THEN adds the SVG
        shapes. Reports per-frame whether a keyframe actually ended up there, since keyframe
        creation via a triggered UI action is less certain than a direct API call — check the
        `frames` result and verify visually before relying on this for many frames.
        """
        doc = self.get_active_document()
        if not doc:
            return {"error": "No active document"}
        svg = params.get("svg")
        if not svg:
            return {"error": "No svg content provided"}
        frames = params.get("frames")
        if not frames:
            return {"error": "frames (a list of frame numbers) is required"}
        if len(frames) > 200:
            return {"error": f"Too many frames ({len(frames)}), cap is 200 per call"}

        layer_name = params.get("layer")
        node = self.find_node(doc, layer_name) if layer_name else self.get_active_layer()
        if not node:
            return {"error": f"Layer not found: {layer_name}" if layer_name else "No active layer"}
        if node.type() != "vectorlayer":
            return {"error": f"Layer '{node.name()}' is not a vector layer"}

        doc.setActiveNode(node)
        if not node.animated():
            node.enableAnimation()

        # Confirmed live against Krita 5.3.2.1's actual action text — "Create Blank Frame",
        # not the guessed "New Blank Frame". "Insert Keyframe Left/Right" is the fallback.
        blank_frame_action = self.find_action("blank", "frame") or self.find_action("insert", "keyframe")

        original_time = doc.currentTime()
        results = []
        for frame in frames:
            doc.setCurrentTime(frame)
            had_keyframe = node.hasKeyframeAtTime(frame)
            if not had_keyframe and blank_frame_action:
                blank_frame_action.trigger()
            shapes = node.addShapesFromSvg(svg)
            doc.refreshProjection()
            results.append({
                "frame": frame,
                "had_keyframe_before": had_keyframe,
                "has_keyframe_after": node.hasKeyframeAtTime(frame),
                "shapes_added": len(shapes),
            })

        doc.setCurrentTime(original_time)
        doc.refreshProjection()
        return {
            "status": "ok",
            "layer": node.name(),
            "used_action": blank_frame_action.text() if blank_frame_action else None,
            "frames": results,
        }


# Register the extension
Krita.instance().addExtension(KritaMCPExtension(Krita.instance()))
