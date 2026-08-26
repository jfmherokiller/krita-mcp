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

- **`stamp_on_frames`** (was `stamp_vector_on_frames`) — the user's original ask ("stamp a vector
  on multiple frames"). True vector-layer keyframing confirmed broken, twice, independent of an
  async-timing bug that was also real and fixed along the way (see CLAUDE.md "Animation /
  keyframes" for both). **User chose (2026-08-26) to switch to the raster approach** rather than
  keep chasing true vector keyframes. Rewritten: rasterizes the SVG once via `QSvgRenderer`/
  `QPainter` into a `QImage`, then alpha-blends those pixels onto a **paint** layer's keyframes at
  each target frame via `setPixelData()`. Old vector-based action/tool names removed entirely
  (`stamp_vector_on_frames` → `stamp_on_frames`, now takes `x`/`y` placement params, requires a
  paint layer not a vector layer). **Not yet tested live** — written but the plugin hasn't been
  reloaded/retested since this rewrite. Next step when resuming: restart Krita, create a throwaway
  paint layer in `voreanim.kra`, call `stamp_on_frames` on 2-3 frames, check `has_keyframe_after`
  is `True` for each, and verify with `get_color_at` (or export + visual check) at different
  `set_current_time` values that the stamp only appears on the target frames, not everywhere.
- Untried alternative if true vector-per-frame content ever matters enough to revisit: inspect
  Krita's C++ source (`libs/ui/kis_animation_...` / `KisShapeLayer` keyframing) to find whether
  there's an internal, non-QAction way to add a vector keyframe. Explicitly deprioritized in favor
  of the raster approach for now.

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
