# AGENTS.md

## Repository overview

This project is a bridge between an MCP client and a running Krita instance. It has two Python
halves that form one protocol and must stay in sync:

- `server.py`: a FastMCP stdio server exposing `krita_*` tools. It sends HTTP/JSON requests to the
  plugin.
- `krita-plugin/kritamcp/__init__.py`: a Krita Python plugin that listens on localhost and executes
  commands through Krita's main thread.

The request path is:

```text
MCP client -> server.py -> HTTP localhost:5678 -> CommandQueue -> Krita main thread -> Krita API
```

Read `README.md` for setup and the public tool list. Read `TASKS.md` before resuming unfinished or
live-tested work. `CLAUDE.md` contains additional implementation history and confirmed Krita API
quirks; keep it aligned with this file when project-wide guidance changes.

## Development commands

Use `uv` for the MCP-server environment:

```bash
uv sync
uv run server.py
uv add <package>
uv lock
```

`uv run server.py` starts a stdio MCP server and normally blocks while waiting for a client.

There is currently no test suite, linter, formatter, or build step. At minimum, syntax-check both
halves after Python edits:

```bash
uv run python -m py_compile server.py krita-plugin/kritamcp/__init__.py
```

Do not try to import the plugin with ordinary Python: `krita` and `PyQt5` are supplied by Krita's
embedded interpreter. Functional plugin testing requires a real Krita instance. Restart Krita after
every plugin-code change; the Python Plugin Manager does not hot-reload it.

## Adding or changing MCP tools

A new HTTP-backed capability normally needs all three of these, using the exact same action string:

1. Add an `@mcp.tool()` wrapper in `server.py` that validates/serializes arguments and calls
   `send_command("your_action", params)`.
2. Add the action to `KritaMCPExtension.execute_command()` in the plugin.
3. Add a `cmd_your_action(self, params)` plugin method using the existing document/view/layer
   helpers.

Also update the plugin's `/info` command list and `README.md` when the public capability changes.
Keep user-facing errors useful and preserve the existing response conventions.

Native Tools -> Scripts actions belong in `createActions()` only when they are useful as a simple,
parameter-free Krita operation. AI/script-oriented commands should remain behind the HTTP bridge.

## Architecture invariants

- Krita document, canvas, layer, and view APIs are not thread-safe. The HTTP server runs in a
  background `QThread`; actual Krita operations must continue to run through `CommandQueue` on the
  main thread via the polling `QTimer`.
- The default bridge address is controlled by `KRITA_URL` in `server.py`; the plugin port is
  `SERVER_PORT`. Keep their defaults compatible.
- Canvas export and save can be slow. The client request timeout and plugin queue timeout must be
  increased together. The established export/save value is 120 seconds.
- Avoid `Node.save()` for single-layer image export: it can display a modal export-options dialog
  even in batch mode and freeze the event loop. Read pixels into a `QImage` and save through Qt,
  following `cmd_export_layer()`.
- Layer names are not unique. Existing name-based tools return the first match; do not imply that a
  name uniquely identifies a node unless an ID-based API is added end to end.

## Pixel and painting rules

- Direct painting commands use `setPixelData()` in BGRA byte order and bypass the native brush
  engine. `stroke_native` is the exception and uses `Node.paintLine()`.
- Call `doc.refreshProjection()` after pixel writes so the canvas updates.
- Direct pixel painting also bypasses Krita's native selection clipping. Pixel-writing operations
  that should honor selections must use `get_selection_mask()` and blend against its per-pixel
  alpha, following the existing stroke/fill/shape implementations.
- Convert data returned by `pixelData()` or `projectionPixelData()` to `bytearray` before indexing.
  On the supported PyQt5/sip build, indexing a `QByteArray` can return `bytes` rather than `int`.
- Reuse the existing color parsing, alpha blending, layer lookup, and active-document helpers rather
  than creating subtly different versions.

## Animation rules

- Use `set_current_time_sync()` whenever code changes the current frame and immediately reads or
  modifies frame-dependent state. `Document.setCurrentTime()` is asynchronous.
- Creating a blank frame currently relies on finding Krita's `Create Blank Frame` QAction by visible
  text. Text can vary across versions/locales; retain result checks such as `has_keyframe_after` and
  use `list_actions` when validating against another Krita version.
- Do not attempt vector-layer-per-frame stamping using `addShapesFromSvg()` without first solving
  Krita's vector keyframe limitation. The confirmed implementation rasterizes SVG content and writes
  it to paint-layer keyframes in `cmd_stamp_on_frames()`.

## Source and compatibility guidance

- Preserve compatibility with Python 3.10+ as declared in `pyproject.toml`.
- Prefer the installed Krita version's `krita.pyi` stubs over online API documentation, which may
  describe unreleased APIs. On the documented Windows setup they live under the Krita installation
  at `lib/krita-python-libs/PyKrita/krita.pyi`.
- Keep changes focused. Do not reformat the large plugin or server files incidentally, and do not
  overwrite unrelated working-tree changes.
- Keep `pyproject.toml` and `uv.lock` synchronized when dependencies change.

## Verification and handoff

For server-only changes, run the syntax check and exercise the affected MCP wrapper when practical.
For plugin changes, run the syntax check, then clearly state that Krita must be restarted and list the
specific live checks still needed. When a change affects the wire protocol, verify both sides use the
same action name, parameter names, response fields, and timeout behavior.

Do not claim a Krita-specific behavior is verified unless it was exercised in a real, restarted
Krita instance. Record important live-test findings or unresolved follow-up work in `TASKS.md` and
durable implementation constraints in this file or `CLAUDE.md`.
