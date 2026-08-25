# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An MCP server that lets an AI paint inside a running [Krita](https://krita.org/) instance. It has
two halves that must be developed and versioned together:

1. **`server.py`** — a [FastMCP](https://github.com/jlowin/fastmcp) server (stdio transport) exposing
   `krita_*` tools. Runs as a normal Python process launched by the MCP client (Claude Desktop, etc.).
2. **`krita-plugin/kritamcp/__init__.py`** — a Krita Python plugin (uses the `krita` and `PyQt5` APIs,
   only importable from *inside* Krita's embedded interpreter) that runs an `HTTPServer` on
   `localhost:5678` in a background `QThread`.

They talk over plain HTTP/JSON: `server.py` POSTs `{"action": ..., "params": {...}}` to
`http://localhost:5678`, the plugin queues it, executes it on Krita's main thread (via a `QTimer`
polling every 50ms — Krita's document/canvas API is not thread-safe), and returns JSON.

```
MCP Client → server.py (FastMCP, stdio) → HTTP :5678 → CommandQueue → Krita main thread → krita API
```

## Commands

```bash
uv sync                  # install/update deps into .venv (from pyproject.toml/uv.lock)
uv run server.py         # run the MCP server directly (stdio — will block waiting for a client)
uv add <package>         # add a new dependency (updates pyproject.toml + uv.lock)
uv lock                  # re-resolve uv.lock after manual pyproject.toml edits
```

There is no test suite, linter, or build step. The Krita-plugin half (`krita-plugin/kritamcp/`) has
no package manager of its own — it's deployed by copying the folder into Krita's `pykrita/` directory
(see README "Setup" section) and can only be exercised by running it inside real Krita, not via `uv run`.

## Adding a new tool

A new capability requires changes in **three places**, kept in sync by the `action` string name:

1. `server.py` — new `@mcp.tool()` function that calls `send_command("your_action", {...})`.
2. `krita-plugin/kritamcp/__init__.py` `KritaMCPExtension.execute_command()` — add an `elif action ==
   "your_action":` dispatch branch.
3. A `cmd_your_action(self, params)` method implementing it, using `self.get_active_document()` /
   `get_active_view()` / `get_active_layer()` helpers.

Painting/drawing commands (`stroke`, `fill`, `draw_shape`, `clear`, `new_canvas`) manipulate pixel data
directly via `layer.setPixelData()` in **BGRA byte order**, not Krita's native brush engine — this
keeps behavior independent of brush/tool state in the UI, at the cost of never reflecting the active
brush preset's actual texture (bristles, scatter, tip shape). `stroke_native` is the counterpart that
*does* go through the real engine (`Node.paintLine`), for when a preset's texture matters (fur/latex
brushes — see the `krita-shading-technique` skill in the hub). After any pixel write, call
`doc.refreshProjection()` to update the canvas view.

Not every capability belongs behind the HTTP command queue. `KritaMCPExtension.createActions()`
also registers a couple of native Krita menu actions (Tools → Scripts) that call the same
underlying logic directly on the main thread — no HTTP round-trip, no Claude/MCP required. Add a
menu action there instead of (or alongside) an `action`/`cmd_*` pair when the capability is
genuinely useful to a plain Krita user with no per-call parameters to supply (toggling a flag,
running a fixed operation on the current selection). Skip it for anything that only makes sense as
scripted/AI-driven input — a human already has a better native equivalent (painting normally with
the brush tool instead of `stroke_native`; Krita's own Enclose-and-Fill tool instead of
`flood_fill`).

Because painting bypasses Krita's brush engine, it also bypasses Krita's native selection clipping.
`stroke`/`fill`/`draw_shape` (rect/ellipse) compensate by reading `doc.selection().pixelData(...)`
themselves via `KritaMCPExtension.get_selection_mask()` and blending each pixel write by the
selection's per-pixel alpha — see that method and its call sites before changing selection behavior
or adding another pixel-writing command that should respect an active selection.

`Node.name()` is not guaranteed unique — `find_node()`/`doc.nodeByName()` return the first match.
Layer tools that take a `name` param (`set_active_layer`, `delete_layer`, `create_layer`'s `parent`,
etc.) inherit this ambiguity; there's no by-ID lookup exposed yet (`Node.uniqueId()` exists in the
Krita API but isn't threaded through).

## Krita Python API reference

`C:\Users\peter\scoop\apps\krita\<version>\lib\krita-python-libs\PyKrita\krita.pyi` is the authoritative,
version-matched method listing for whatever Krita is actually installed — prefer it over the online
docs at apidoc.krita.maou-maou.fr (generated from Krita's master branch, so it can list methods newer
than a given stable release). Both cover the same classes: `Document`, `Node` (and its `GroupLayer`/
`VectorLayer`/etc. subclasses), `Selection`, `Krita`, `View`, `Window`, `Canvas`, `Filter`.

Plugin code changes require **restarting Krita** to take effect — Python Plugin Manager has no
hot-reload. There's no way to test a plugin edit from this MCP server itself; after editing
`krita-plugin/kritamcp/__init__.py`, the user has to restart Krita before the new behavior is live
(the pykrita plugin directory is normally symlinked to this repo's `krita-plugin/`, so no copy step
is needed — just the restart).

## The export timeout fix

Canvas export (`get_canvas`) and file save (`save`) are the one place a naive implementation breaks:
both HTTP-request timeout (`server.py`'s `send_command(..., timeout=120.0)`) and the plugin's command
queue wait (`CommandQueue.get_result(..., timeout=120)`) must be raised **together** — raising only one
side just moves where the timeout fires. See README "The Export Timeout Fix" for the full explanation
if extending this pattern to other slow commands.

**`doc.setBatchmode(True)` does not suppress every export dialog.** It works for
`Document.exportImage()` (what `get_canvas`/`save` use) but `Node.save()` shows Krita's PNG/JPG
export-options dialog regardless of batchmode, which blocks Krita's entire event loop — not just
the HTTP request — until a human clicks it, hanging the whole app from the plugin's perspective.
Confirmed live: `cmd_export_layer` originally used `Node.save()` and froze Krita this way twice
before being rewritten to build a `QImage` from `node.pixelData()` and save via Qt's own
`QImage.save()`, which never touches Krita's importer/exporter UI at all. Prefer that pattern —
read pixels yourself and save via `QImage`/Qt — over `Node.save()` for any new single-node export
command; don't assume `setBatchmode` protects a command just because it worked for `get_canvas`.

## Animation / keyframes

There is no direct scripting call to create a keyframe — `Node` has `animated()`,
`enableAnimation()`, and `hasKeyframeAtTime(frame)`, but nothing like `addKeyframe(frame)`. The
only way to insert one is Krita's own timeline QAction (**"Create Blank Frame"**, confirmed live
against Krita 5.3.2.1 — the initially-guessed "New Blank Frame" doesn't exist and matched nothing),
found at runtime by matching action *text* rather than a hardcoded id (`find_action()` — text can
drift across Krita versions/locales; the action-trigger pattern itself was already proven safe by
`cmd_undo`/`cmd_redo` triggering `app.action('edit_undo'/'edit_redo')`). `cmd_stamp_vector_on_frames`
reports `has_keyframe_after` per frame precisely because the mechanism is a text-match UI-action
trigger, not a guaranteed API contract — don't assume it silently works on a new Krita version
without checking that field. `list_actions(filter="frame"|"keyframe")` is how the real name was
found; re-run it before trusting `find_action`'s keyword list on a different Krita version.

## Known API quirks

- **`QByteArray` indexing returns `bytes`, not `int`, on this PyQt5/sip build.** Any code that
  reads `pixelData()`/`projectionPixelData()`/etc. and indexes it directly (`data[i]`) needs to
  wrap it in `bytearray(...)` first, or per-pixel math and `"{:02x}".format()`-style calls break.
  Bit `cmd_get_color_at` once (pre-existing bug, fixed by wrapping in `bytearray()`) — check any
  new raw pixel read for the same mistake.

## Configuration

- `KRITA_URL` env var (server.py side) — defaults to `http://localhost:5678`.
- `SERVER_PORT` / `CANVAS_OUTPUT_DIR` constants at the top of `krita-plugin/kritamcp/__init__.py`
  (plugin side) — must match `KRITA_URL`'s port if changed.
