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

`Node.name()` is not guaranteed unique, but this is resolved: `find_node()` accepts either a name
or a node's `uniqueId()` string (as returned in every node's `"id"` field — see `_node_info`,
surfaced by `list_layers`/`create_layer`/`duplicate_layer`/`create_colorize_mask`) — `QUuid(name)`
parses to a null UUID for anything that isn't a valid `{...}`-bracketed uuid string, including any
real layer name, so passing a name still resolves exactly as before and passing an id disambiguates
exactly. No tool signatures needed to change since every existing `name` param doubles as an id
param this way. Confirmed live: two layers named identically, targeted individually by id,
correctly affected only the intended one each time.

`create_mask` (filter/transparency/selection) is the non-destructive counterpart to `apply_filter`
— it attaches a `FilterMask`/`TransparencyMask`/`SelectionMask` as a child of the target layer
(same attach pattern as `create_colorize_mask`: `target.addChildNode(mask, None)`), seeded from the
active selection if one exists, else a full-canvas `Selection` (so a fresh mask starts fully "on").
`set_filter_mask_filter` swaps an existing filter mask's `Filter`/config in place via
`mask.setFilter(filt)` — this is the actual non-destructive-editing story; without it, `create_mask`
alone would just be a fancier `apply_filter`. Toggling any mask's visibility (`set_layer_visible`,
already generic across node types) or deleting it (`delete_layer`) leaves the underlying layer's
pixels untouched — confirmed live by toggling a mask off and getting the pre-mask canvas back
exactly. **While a document has an active selection, `list_layers` also surfaces a phantom
top-level `Selection Mask` node** (its own id, sibling of the real layers, not nested under any of
them) — this is Krita's own internal representation of the document's active/global selection, not
something `create_mask` creates. It appears regardless of whether a selection mask was ever
created via this API, and disappears once the selection is cleared. Don't parse it as a real node
a caller made.

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
`cmd_undo`/`cmd_redo` triggering `app.action('edit_undo'/'edit_redo')`). `cmd_stamp_on_frames`
reports `has_keyframe_after` per frame precisely because the mechanism is a text-match UI-action
trigger, not a guaranteed API contract — don't assume it silently works on a new Krita version
without checking that field. `list_actions(filter="frame"|"keyframe")` is how the real name was
found; re-run it before trusting `find_action`'s keyword list on a different Krita version.

**Vector-layer keyframing via scripting does not work, confirmed live, twice.** The original
`cmd_stamp_vector_on_frames` used `Node.addShapesFromSvg()` after `setCurrentTime(frame)` +
triggering Create Blank Frame — every stamped shape landed on frame 0's shared vector content
regardless of the target frame, and `hasKeyframeAtTime()` stayed `False` even at frame 0 despite
`enableAnimation()` reporting `animated=True`. This reproduced identically after fixing the
`setCurrentTime()` timing bug below, ruling that out as the sole cause. Best-guess explanation
(unconfirmed against Krita's source): Create Blank Frame likely reads the Timeline docker widget's
own UI selection state, which nothing in the scripting API can drive. **Don't build another
vector-per-frame-keyframe feature on this API without solving that first** — `cmd_stamp_on_frames`
was rewritten to rasterize the SVG via `QSvgRenderer`/`QPainter` and paint pixels onto a **paint**
layer's keyframes instead, since raster keyframing (verified against `hasKeyframeAtTime` on a real
animation's existing paint layer) works correctly.

**`Document.setCurrentTime()` is asynchronous.** Confirmed live: `doc.setCurrentTime(5)` followed
immediately by `doc.currentTime()` *in the same command handler* returned `0`, while a later,
separate command correctly saw `5` — Krita's internal frame-changed signal fires on a queued
connection, not synchronously. Fixed by `set_current_time_sync()`, which calls
`QApplication.processEvents()` right after `setCurrentTime()` to force queued handlers to run
before returning. **Any new command that calls `setCurrentTime()` and then immediately reads or
acts on "the current frame" must go through `set_current_time_sync()`, not call it directly** —
same for triggering a QAction that itself schedules async work (the blank-frame action also gets a
`processEvents()` pump before its effect is trusted).

## Known API quirks

- **`QByteArray` indexing returns `bytes`, not `int`, on this PyQt5/sip build.** Any code that
  reads `pixelData()`/`projectionPixelData()`/etc. and indexes it directly (`data[i]`) needs to
  wrap it in `bytearray(...)` first, or per-pixel math and `"{:02x}".format()`-style calls break.
  Bit `cmd_get_color_at` once (pre-existing bug, fixed by wrapping in `bytearray()`) — check any
  new raw pixel read for the same mistake.

- **`ColorizeMask.setKeyStrokePixelData`/`keyStrokePixelData` use alpha8 (1 byte/pixel coverage),
  not BGRA.** Every other pixel-writing command in this file (`setPixelData`/`pixelData` on a
  regular `Node`) uses 4-byte BGRA pixels. Colorize masks store each key stroke's coverage in its
  own internal alpha8 paint device — confirmed against Krita's `libs/libkis/ColorizeMask.cpp`
  (`keyStroke.dev = new KisPaintDevice(KoColorSpaceRegistry::instance()->alpha8())`) and its own
  test (`libs/libkis/tests/TestDocument.cpp::testCreateColorizeMask`), which
  `cmd_create_colorize_mask`'s call order mirrors: create mask → parent under the target layer →
  tune edge-detection/clean-up → `initializeKeyStrokeColors()` once with the full color list →
  `setKeyStrokePixelData()` per color/region (plain `bytes([255]) * (w*h)` for full coverage) →
  `updateMask()` → `setEditKeyStrokes(False)` → `setShowOutput(True)` → **`doc.waitForDone()`** →
  `doc.refreshProjection()`. Calling `initializeKeyStrokeColors()` more than once on the same mask,
  or calling `setKeyStrokePixelData()` before it, is not supported by the underlying C++.
  **Confirmed live 2026-09-19** — line-art square outline, one key stroke inside (fills), one
  outside marked `transparent` (stays empty): interior filled precisely to the line-art edge.

- **`ColorizeMask.updateMask()` needs `doc.waitForDone()`, not `QApplication.processEvents()`, to
  actually wait for its result.** The lazy-fill segmentation runs as an async job on Krita's own
  image update scheduler — a completely different queue from Qt's event loop, so
  `processEvents()` (the fix that works for the `setCurrentTime()` race above) does nothing for
  it. First live test of `create_colorize_mask` produced no visible fill at all, no error from any
  call — `updateMask()`/`setShowOutput(True)`/`refreshProjection()` all "succeeded" while the
  segmentation hadn't actually finished yet. Root cause found by rereading
  `libs/libkis/tests/TestDocument.cpp::testCreateColorizeMask` line-by-line: it calls
  `d.waitForDone()` between `setShowOutput(true)` and `refreshProjection()` — a call this
  implementation had skipped as apparent boilerplate despite the surrounding code otherwise
  claiming to mirror that test exactly. Lesson for any future "confirmed against Krita's own test"
  claim in this codebase: match literally every call in the reference, not just the ones that look
  load-bearing.

- **`Document.setBatchmode(True)` alone does not reliably suppress the "save changes?" prompt on
  close, at least on Krita 6.0.3/Qt6.** `cmd_close_document` originally relied on it (matching the
  precedent set by the export-timeout fix above); a live "Recover Files"-adjacent test still hit a
  save-changes prompt with only that set. `doc.setModified(False)` before `.close()` is the actual
  fix — it makes Krita's own dirty-check see nothing to save in the first place, rather than hoping
  a UI-suppression flag also suppresses this particular prompt. `cmd_quit` (closes every open
  document this way, then `QApplication.instance().quit()`) exists specifically for unattended
  restarts, where no human is present to click either this prompt or the crash-recovery dialog a
  raw process kill can trigger on the next launch (Krita's `checkAutosaveFiles()` in
  `libs/ui/KisApplication.cpp` — a properly closed document doesn't leave an autosave file behind
  to be offered for recovery).

- **`Node.scaleNode/rotateNode/cropNode/shearNode` and `Document.scaleImage/rotateImage/shearImage`
  are synchronous from Python's side** — confirmed against Krita's own `libs/libkis/Node.cpp` and
  `Document.cpp`: each one already calls `image->waitForDone()` internally before returning. Unlike
  `ColorizeMask.updateMask()` above, none of `cmd_scale_layer`/`cmd_rotate_layer`/`cmd_crop_layer`/
  `cmd_shear_layer`/`cmd_scale_canvas`/`cmd_rotate_canvas`/`cmd_shear_canvas` need an extra
  `doc.waitForDone()` of their own. `rotateNode`/`rotateImage` take radians (converted from the
  degrees these tools accept at the Python layer); `shearNode`/`shearImage` take degrees natively —
  confirmed via Krita's own comment "angleX, angleY are in degrees" in `libs/image/kis_image.cc`,
  don't convert those. An unrecognized `strategy` name for `scaleNode`/`scaleImage` silently falls
  back to `"Bicubic"` at the C++ level (`KisFilterStrategyRegistry`) rather than erroring — valid
  names are `Bicubic`, `Bilinear`, `Box`, `Bell`, `BSpline`, `Lanczos3`, `Mitchell`, `Hermite`.

## Configuration

- `KRITA_URL` env var (server.py side) — defaults to `http://localhost:5678`.
- `SERVER_PORT` / `CANVAS_OUTPUT_DIR` constants at the top of `krita-plugin/kritamcp/__init__.py`
  (plugin side) — must match `KRITA_URL`'s port if changed.
