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

## Done

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

## In progress / needs retest

- **`stamp_vector_on_frames`** — the user's original ask ("stamp a vector on multiple frames").
  **Confirmed broken, twice, two different root causes, still unresolved.**
  1. First attempt: all shapes landed on frame 0's shared vector content. Diagnosed as
     `Document.setCurrentTime()` being asynchronous (confirmed live — immediate read-back in the
     same command handler returned the stale time). Fixed via `set_current_time_sync()`.
  2. Retested live after that fix, in a fresh test vector layer, same `voreanim.kra` document:
     **identical symptom** — all 3 stamped circles still landed on frame 0's shared content,
     `has_keyframe_after` still `False` for every target frame. So the timing fix was necessary but
     not sufficient; something else is also wrong.
  - Current best hypothesis (unverified): the "Create Blank Frame" QAction likely reads the
    Timeline docker widget's own internal UI selection (which layer/frame cell is highlighted in
    that specific widget), not `doc.activeNode()`/`doc.currentTime()` — and nothing in the scripting
    API exposes that widget's selection model, so the action probably silently no-ops when
    triggered from a script regardless of timing. Not confirmed against Krita source or further
    experiments; ran out of restart-cycle budget on the user's real animation file for this session.
  - **Recommended next step:** stop trying to drive the Timeline docker via `find_action()` and
    switch to a **raster-layer stamp** instead — rasterize the SVG once (Qt can render SVG to a
    `QImage` via `QSvgRenderer`), then `setPixelData()` it onto a **paint** layer's keyframes at
    each target frame. Raster keyframing is proven reliable (`Paint Layer 1` in this same file has
    a real, correctly-detected keyframe at frame 0 in every test). This loses per-frame vector
    editability (it becomes a rasterized stamp, not an editable path) but actually delivers "the
    same shape appears identically at multiple frames," which was the real goal. Needs the user's
    explicit buy-in before switching since it changes what the tool delivers vs. what was literally
    asked ("stamp a *vector*").
  - Untried alternative if true vector-per-frame content still matters enough to chase further:
    inspect Krita's C++ source (`libs/ui/kis_animation_...` / `KisShapeLayer` keyframing) to find
    whether there's an internal, non-QAction way to add a vector keyframe — likely a multi-hour
    detour, only worth it if the raster fallback is rejected.

## Known limitations / open questions

- Fill-layer `generator` config keys (for `create_layer(type="fill")`) are undocumented — no
  `config` dict currently produces Krita's actual default gradient; correct keys would need to be
  reverse-engineered from Krita's C++ source or found by trial in the UI's own Fill Layer dialog.
- `reorder_layer`'s "move to the very bottom of an already-populated stack" edge case relies on an
  unverified assumption about `addChildNode(child, above=None)` semantics.
- `Node.name()` isn't guaranteed unique; all `name`-based lookups (`find_node`) return the first
  match only — no by-ID (`Node.uniqueId()`) lookup is exposed yet.
- Every plugin-side code change requires a full Krita restart to test (no hot-reload for Python
  plugins) — budget for that when planning multi-step live-testing work.
