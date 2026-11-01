# krita-mcp — working notes

Session-continuity file, not user docs — see README.md/CLAUDE.md for those. Update this as work
progresses; safe to prune completed sections once they're stable and no longer at risk of regressing.

## Status

Forked from https://github.com/nanayax3/krita-mcp to https://github.com/jfmherokiller/krita-mcp
(remote `fork`) — user plans to open a PR back upstream once satisfied with this branch. All work
so far is pushed to `fork/master`.

Converted from pip/venv to `uv` (`pyproject.toml` + `uv.lock`). Registered as the `krita` plugin in
the AIHelpers hub (`E:\modelStuff\AIHelpers\plugins\krita\`, **not** a git repo — that hub directory
has no `.git`, only `mcp-servers/krita-mcp` does).

Seven upstream PRs opened 2026-09-18/19 against `nanayax3/krita-mcp` (scoped minimally, cherry-picked
onto fresh branches rather than dragging in the rest of this fork's unrelated work — see each PR
body for the reasoning): **#6** uv conversion, **#7** Krita 6.x/PyQt6 support (confirmed live),
**#8** layer management + unique-ID lookup (confirmed live), **#9** Colorize Mask (confirmed live,
stacked on #8), **#10** non-destructive masks (confirmed live, stacked on #8), **#11** resampling
transforms (confirmed live, stacked on #8), **#12** `close_document` save-prompt fix + `krita_quit`
(confirmed live, stacked on #8). None merged yet as of this writing. `find_nodes` (below) not yet
pushed as a PR.

**Autonomous-restart workflow established 2026-09-19** (user granted permission to kill/restart
Krita without asking, for any feature that only needs a blank canvas): use `krita_quit` (closes
every open document with `setModified(False)` first, then `QApplication.quit()`) instead of a raw
process kill wherever possible — a raw kill leaves Krita's crash-recovery system thinking the last
session ended abnormally, and the next launch blocks on a **modal "Recover Files" dialog with no
human present to click it**, which silently hangs any unattended run (confirmed live: exactly this
happened once, mid-session). Confirmed live that `krita_quit` avoids both that and the
"save changes?" prompt that plain `close_document`/window-close could still trigger, even with
`setBatchmode(True)` set. Belt-and-suspenders: before every relaunch, delete any stray
`~/.krita-*-*-autosave.kra` files (the PID-tagged pattern Krita uses for a never-saved document's
autosave — safe to always delete, since it's structurally distinct from the user's own real
`ArtStuff` autosaves, which use a `.<realname>.kra-autosave.kra` pattern in a different directory)
so `checkAutosaveFiles()` (`libs/ui/KisApplication.cpp`) finds nothing to prompt about even if a
`krita_quit` is somehow skipped. **No open document survives a restart** — Krita does not
auto-recreate a blank canvas on launch; always call `new_canvas` again after every restart before
testing anything. Wait ~60s after relaunching before the plugin's HTTP bridge is reliably up.

## Done

- **`find_nodes`** (`Node.findChildNodes`) — **confirmed live 2026-09-19**. Searches the layer
  tree by name/type/color-label, from the document root or a given subtree (`under`). Test
  document: `sketch`/`colors` paint layers, `effects_group` (group) containing a nested `glow`
  paint layer, and a `filtermask` on `colors`. Confirmed: `type="paintlayer"` recursive finds all
  4 paint layers including the nested `glow`; exact `name="glow"` finds only that nested node
  (proving recursion into groups works); `type="filtermask"` finds only the mask;
  `recursive=false` correctly excludes `glow` (nested) while still including `effects_group`
  itself as a top-level match.
- **Built-in resampling transforms** (`scale_layer`/`rotate_layer`/`crop_layer`/`shear_layer`,
  `scale_canvas`/`rotate_canvas`/`shear_canvas`) — **confirmed live 2026-09-19**, all seven. Uses
  Krita's real `Node.scaleNode/rotateNode/cropNode/shearNode` and
  `Document.scaleImage/rotateImage/shearImage`, which (confirmed against Krita's own
  `libs/libkis/Node.cpp`/`Document.cpp`) already call `image->waitForDone()` internally before
  returning — synchronous from Python's side, unlike Colorize Mask's `updateMask()`, so no extra
  sync barrier was needed here. Rotation/shear take degrees (converted to radians for
  `rotateNode`/`rotateImage` since the underlying API wants radians there; shear is degrees
  natively per Krita's own C++ comment "angleX, angleY are in degrees" in `libs/image/kis_image.cc`).
  Test: 100x100 square scaled to 300x300 (origin preserved), 150x80 rectangle rotated 45°, 300x300
  square cropped to its top-left 100x100, 200x200 square sheared 30° into a parallelogram — all
  correct. Canvas-level equivalents smoke-tested via `document_info` size checks rather than visual
  export: 800→400 scale, 400→546 rotate-30° (matches `400*(cos30°+sin30°)≈546.4` exactly), 546→692
  width-only shear-15°-x. **One false alarm during testing, not a bug**: the first `scale_layer`
  test appeared to shrink instead of grow — turned out `cmd_new_canvas`'s "paint" layer is
  pre-filled with the background color across the *entire* canvas (see its own code), so its real
  `exactBounds()` (what `scaleNode` computes the ratio from) was already canvas-sized, not just the
  small shape drawn on top of it. Always draw onto a freshly `create_layer`'d empty layer when
  testing content-bounds-dependent operations, not `new_canvas`'s pre-filled default layer.
- **`krita_quit` + `close_document` `setModified(False)` fix** — see "Autonomous-restart workflow"
  above. New `krita_quit` tool; `close_document`'s existing `setBatchmode(True)` guard alone proved
  insufficient against the save-prompt on this Krita 6.0.3/Qt6 build, confirmed live — added
  `setModified(False)` as the actual fix.
- **Non-destructive masks** (`create_mask` type=filter/transparency/selection,
  `set_filter_mask_filter`) — **confirmed live 2026-09-19**. `create_mask` attaches a
  `FilterMask`/`TransparencyMask`/`SelectionMask` as a child of a layer (same attach pattern as
  Colorize Mask), seeded from the active selection if one exists, else the whole canvas.
  `set_filter_mask_filter` swaps an existing filter mask's filter/config in place — the actual
  point of "non-destructive" (re-edit without delete+recreate). Test: green rectangle on white,
  `invert` filter mask applied — canvas correctly inverted (white→black, green→magenta). Toggled
  `set_layer_visible(visible=False)` on the mask: original green/white returned exactly, confirming
  the underlying pixels were never touched. Re-enabled, then `set_filter_mask_filter` swapped to
  `desaturate` in place — rectangle correctly turned gray. Transparency mask test: selected a
  strip via `select_rectangle`, created a transparency mask from it — only that strip stayed
  visible, everything outside the selection became transparent, exactly as intended. Selection
  mask creates without error too.
  **Caveat found**: while a document has an active global selection, `list_layers` also surfaces a
  phantom top-level `Selection Mask` node with its own id, sitting as a sibling of the real layers
  rather than nested under one — this is Krita's own internal representation of the document's
  active/global selection (the "marching ants"), not something `create_mask` creates; it appears
  regardless of whether `create_mask(type="selection")` is ever called, and disappears once
  `clear_selection` runs. Don't confuse it for a real selection-mask node when parsing `list_layers`
  output — worth a `CLAUDE.md` note if this trips up a future session. Not pushed as a PR yet.
- **Colorize Mask** (`create_colorize_mask`/`update_colorize_mask`) — **confirmed live 2026-09-19**,
  found and fixed a real bug along the way. Test: white canvas, black square line-art outline
  drawn via `draw_shape("line", ...)` x4, then `create_colorize_mask` with one key stroke inside
  the outline (red) and one outside marked `transparent`. First attempt produced no visible fill
  at all despite no errors from any call. Root cause: `mask.updateMask()` runs the lazy-fill
  segmentation as an async job on Krita's own image update scheduler, not on Qt's event loop —
  `QApplication.processEvents()` (the fix already used elsewhere for the `setCurrentTime` race)
  does NOT wait for it. Fixed by adding `doc.waitForDone()` right after `updateMask()`/
  `setShowOutput()`, before `refreshProjection()` — the exact position Krita's own
  `libs/libkis/tests/TestDocument.cpp::testCreateColorizeMask` calls it in, which this
  implementation had NOT been matching despite believing it mirrored that test exactly. **Lesson:
  matching a reference test's call sequence needs literally every call, including the ones that
  look like boilerplate — `waitForDone()` was skipped as "probably not needed" and was in fact the
  whole bug.** Retest confirmed: interior filled solid red, precisely bounded by the line-art edge
  (`edge_detection=True`), exterior stayed unfilled. `update_colorize_mask` re-matching an
  already-registered color via `ManagedColor.fromQColor` also confirmed working (no "color not
  registered" error on re-use).
- **Krita 6.x (PyQt6) support** — the local Krita install turned out to be 6.0.3 (Qt6), and this
  plugin's hardcoded `from PyQt5...` imports made it fail to load at all there
  (`ModuleNotFoundError: This version of Krita is not compatible with PyQt5!`, Krita's own
  `importAvoidWrongPyQtHack` guard). Fixed by branching imports on `pykrita.qt_major_version()` —
  see CLAUDE.md. **Confirmed live**: plugin now loads and responds to `krita_health` on Krita
  6.0.3, where it previously couldn't load at all. Pushed as upstream PR #7, scoped to just this
  fix (no other accumulated feature work) since it's valuable independent of everything else here.
- **By-ID layer lookup** (`Node.uniqueId()`/`Document.nodeByUniqueID()`) — `find_node()` now
  accepts either a name or a node's id string interchangeably (no tool signatures changed; every
  existing `name` param doubles as an id param). Every node-returning command now includes an
  `"id"` field. **Confirmed live**: created two layers both named "Dup", targeted the second one
  specifically by its id, confirmed via `list_layers` that only that exact node was affected while
  the first "Dup" was untouched — the disambiguation this was built for.
- **uv conversion** — `pyproject.toml`, `uv.lock`, `.python-version` (3.12), README/CLAUDE.md updated.
- **Layer management** — create/delete/list/reorder, visibility, opacity, blending mode, merge down,
  duplicate, clipping (inherit alpha).
- **Selections** — rectangle/all/clear/invert/grow/shrink/feather; `stroke`/`fill`/`draw_shape` now
  respect the active selection (pixel-direct painting doesn't natively clip to selection like
  Krita's brush engine does, so this is done manually via `get_selection_mask()`).
- **Filters** — list/apply.
- **Document** — info, color-space get/set, color profile listing, multi-doc management,
  resize/crop/flatten, per-layer export.
- **Native brush strokes** (`stroke_native`) — uses `Node.paintLine`, respects the actual active
  brush preset's texture (fur/bristle brushes etc.), unlike `stroke`'s pixel-direct soft circle.
  Confirmed live with a Bristles preset — visible strand texture in the exported PNG.
- **Scripted flood fill** (`flood_fill`) — bounded/capped scanline fill for AI-driven use (a human
  should use Krita's native Enclose-and-Fill tool instead). Confirmed live, correctly bounded.
- **Fill layers** (`create_layer(type="fill", generator=...)`) — gradient/pattern/color generator
  layers. Confirmed live (creates without error; generator config keys are undocumented/generator-
  specific, left as passthrough).
- **Vector/SVG** (`add_svg_shapes`, `export_layer_svg`, `list_shapes`) — confirmed live, full
  round-trip (import rect+circle, list bounds, export back to valid SVG).
- **Two native Krita menu actions** (Tools → Scripts, no MCP/AI needed): "Toggle Clip to Layer
  Below", "Fill Selection with Gradient (FG→BG)". **Not yet manually tested** — I can't click a
  Krita menu item myself; ask the user to try these when convenient.
- **`krita-shading-technique` skill** (`E:\modelStuff\AIHelpers\plugins\krita\skills\`) — fur/latex
  brush-building guidance (Brush Editor settings, not scriptable) + layered shading structure using
  the tools above. Not independently verified against actually building a brush in the UI.
- **Bug fixes found via live testing:**
  - `export_layer` originally used `Node.save()`, which shows Krita's PNG export dialog regardless
    of `doc.setBatchmode()`, freezing the whole app twice during testing. Rewritten to build a
    `QImage` from `node.pixelData()` and save via Qt directly — confirmed no hang, correct output.
  - `get_color_at` (pre-existing bug): `QByteArray` indexing returns `bytes`, not `int`, on this
    PyQt5/sip build — fixed by wrapping reads in `bytearray()`.
  - `find_action()` keyword guess for keyframe creation was wrong ("New Blank Frame" → actually
    "Create Blank Frame") — found and fixed via `list_actions(filter="frame")` against live Krita.
  - `Document.setCurrentTime()` is asynchronous — confirmed live (immediate read-back in the same
    command handler returns the OLD time). Fixed via `set_current_time_sync()` pumping
    `QApplication.processEvents()`. This was the real root cause of the stamp tool not working, not
    just the action-name typo.

## Animation / keyframe resolution

- **`stamp_on_frames`** (was `stamp_vector_on_frames`) — **confirmed live**. True vector-layer
  keyframing is unavailable, so the tool rasterizes SVG once via `QSvgRenderer`/`QPainter` and
  alpha-blends it onto a paint layer's keyframes via `setPixelData()`. Live test on a throwaway
  128x128 document stamped a 16x16 red SVG at `(20, 20)` on frames 2, 5, and 8. Krita used its
  `Create Blank Frame` action; `has_keyframe_after` was true at all three frames, each frame sampled
  `#ff0000` inside the stamp, pixels outside remained `#ffffff`, and frame 0 remained `#ffffff` at
  the stamp position. The throwaway document was closed afterward; the user's existing document
  was left open.
- **Resolved 2026-09-18, closed for good:** dug into Krita's C++ source (`libs/image/kis_base_node.cpp`,
  `libs/ui/flake/kis_shape_layer.*`) to check for a non-QAction way to add a vector keyframe.
  Keyframe *content* channels only get created by node classes that override
  `requestKeyframeChannel()` — `KisPaintLayer`, `KisSelectionBasedLayer`, masks, transform masks.
  `KisShapeLayer` never overrides it and never calls `addKeyframeChannel()` anywhere in the
  codebase (verified via `gh search code`). `enableAnimation()` just flips a bare bool with no
  content channel attached, which is exactly why `hasKeyframeAtTime()` stayed false. The
  Storyboard docker (`plugins/dockers/storyboarddocker/StoryboardModel.cpp`) explicitly disables
  "Add Keyframe" for vector layers too. **This is a real gap in Krita's own animation model, not a
  missing scripting call** — nothing to find here, the raster-stamp approach (`stamp_on_frames`)
  is the correct and only path. Stop looking.

## Feature backlog (from a 2026-09-18 deep scan of Krita's libs/libkis/ C++ binding layer)

Everything below is confirmed real and unused by the current tool set, gathered across two
research passes (one curated, one exhaustive across all ~40 headers in `libs/libkis/`). Not
started — pick from here when picking up new work. Roughly prioritized; each already exposed via
the same Python `krita` module already in use, no Krita rebuild needed, just the usual
server.py-tool → plugin-action → cmd_* plumbing (see "Adding a new tool" in CLAUDE.md/AGENTS.md).

**High value:**
- `Node.layerStyleToAsl()`/`setLayerStyleFromAsl()` — Photoshop-style non-destructive layer effects
  (drop shadow, bevel/emboss, stroke, glow) as an ASL string blob.
- `Preset.toXML()`/`fromXML()` — full brush preset parameter schema (dynamics, texture, scatter,
  tip shape), not just size/opacity.
- `Extension`/`Notifier` event system (`imageCreated`/`imageSaved`/`imageClosed`/`viewCreated`/etc.)
  — real reactive-tooling hook instead of polling, currently completely unused.
- Full painting-engine state on `View` — `eraserMode`, `globalAlphaLock`, `disablePressure`,
  `HDRExposure`/`HDRGamma`, `brushFade`, `brushRotation`, `paintingFlow`, `patternSize`,
  `currentBlendingMode` — the rest of the brush-engine dial set beyond `set_brush`'s preset/size/opacity.
- `Node.channels()`/`Channel` — per-channel visibility + isolated per-channel pixelData/setPixelData.
- `Document.importAnimation(files, firstFrame, step)` — bulk image-sequence import as animation
  frames in one call, vs. frame-by-frame `stamp_on_frames`.
- `GuidesConfig`/`GridConfig` (full read/write + XML) — programmatic composition guides and grid setup.

**Useful, more specific:**
- `Node.thumbnail(w,h)`/`Document.thumbnail(w,h)` — fast low-res preview without a full `get_canvas` export.
- `Document.createFilterLayer()` — non-destructive filter as a full layer (vs. FilterMask).
- `Node.pixelDataAtTime(x,y,w,h,time)` — reads pixels at an arbitrary frame without moving the
  playhead, sidestepping the async `setCurrentTime()` race documented above.
- `Node.paintRectangle/paintEllipse/paintPolygon/paintPath` — native-brush-engine shape drawing,
  texture-respecting counterpart to `draw_shape` (like `stroke_native` is to `stroke`).
- Layer-stack metadata: `setPinnedToTimeline`, `setCollapsed`, `colorLabel`, `inheritAlpha`, `alphaLocked`.
- `Canvas` view-state: `zoomLevel`, `rotation`, `mirror`, `wrapAroundMode`, `pan`/`preferredCenter`
  — could make `get_canvas` screenshots consistent/framed.
- `Document.documentInfo()`/`setDocumentInfo()` — read/write author/title/description metadata.
- `Krita.writeSetting()`/`readSetting()` — persist MCP-tool config into `kritarc` across sessions.
- `Document.lock()/unlock()/waitForDone()/tryBarrierLock()` — explicit sync primitives, could
  harden `get_canvas`/export further alongside the existing timeout fix.
- `Resource`/`Krita.resources(type)` beyond brushes — patterns, gradients, palettes, workspaces are
  all listable/settable as `Resource` objects, not just read.

**Niche / lower priority:** `Document.audioLevel`/`audioTracks` (reference audio for lip-sync),
`Document.annotationTypes`/`annotation`/`setAnnotation` (arbitrary embedded metadata blobs),
`Scratchpad` widget (UI-widget-bound, more relevant to docker-plugin authors), `DockWidgetFactoryBase`
(custom docker UI registration — pure UI convenience).

**Explicitly ruled out:** text layers/content — no dedicated text-shape class in `libkis` at all
(`KoSvgTextShape` exists only in `libs/flake/text/`, never bound into libkis); the only path to text
is raw SVG `<text>` markup through the existing `add_svg_shapes`, with no way to introspect/edit
font or content afterward. Filter config schema introspection — `Filter.h` confirms config is an
opaque `InfoObject` key/value map with no per-filter parameter-schema enumeration API; `apply_filter`
already allows blind get/set via config dicts, which is all libkis supports.

## Known limitations / open questions

- Fill-layer `generator` config keys (for `create_layer(type="fill")`) are undocumented — no
  `config` dict currently produces Krita's actual default gradient; correct keys would need to be
  reverse-engineered from Krita's C++ source or found by trial in the UI's own Fill Layer dialog.
- `reorder_layer`'s "move to the very bottom of an already-populated stack" edge case relies on an
  unverified assumption about `addChildNode(child, above=None)` semantics.
- Every plugin-side code change requires a full Krita restart to test (no hot-reload for Python
  plugins) — budget for that when planning multi-step live-testing work.
