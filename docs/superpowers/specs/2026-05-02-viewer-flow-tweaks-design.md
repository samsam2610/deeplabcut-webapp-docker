# Viewer Flow Tweaks — Design

**Date:** 2026-05-02
**Branch:** `feat/posture-match-refiner` (continues from the viewer-flow-improvements work)
**Status:** Approved (brainstorm); pending implementation plan

## Problem

After shipping the layered overlay + Browse-filter + auto-latest-primary improvements, real use surfaced four remaining gaps:

1. **No way to hide just the primary's markers.** Comparison rows have per-layer visibility checkboxes; the primary doesn't. The global "Show Kinematic Markers" master is the only switch and kills everything at once.
2. **Comparison shapes aren't visually distinct enough.** Primary = filled disc, comparison 1 = open ring — both read as "circles" at small marker sizes. Users can't tell which dot belongs to which layer.
3. **Markers visibly lag the video during playback.** Even with the rAF paint barrier, frame N's image swaps before frame N's pose fetch completes, so the user sees frame N's image with frame N-1's markers. The lag is most noticeable at the video's native framerate (30+ fps) when comparison-layer fetches are slow.
4. **Dataset Curation panel is always visible.** It belongs to a different workflow from "view markers"; users want it hidden by default and revealed on demand.

## Goals

- A **per-primary visibility checkbox** in the comparison-layers block, with the primary's shape glyph displayed alongside its label so the legend is always on screen.
- A **shape ladder without `circle-open`**: primary = filled disc (●), comparison 1 = filled diamond (◆), comparison 2 = square (□), comparison 3+ = triangle (△).
- An **editable FPS field** (`va-play-fps`, default 5, range 1–120) in the player controls. Combined with the existing `va-play-step` to compute effective video-time-rate.
- An **atomic frame+markers swap** in `_vaLoadFrame` so the visible image NEVER lands on screen before its markers do. Image preload + pose fetches run in parallel; the visible swap only happens after both resolve.
- A **collapsible Dataset Curation panel** (`va-curation-toggle` / `va-curation-controls`), default hidden, mirroring the existing `va-overlay-toggle` / `va-overlay-controls` pattern.

## Non-Goals

- Per-bodypart visibility per layer (still global).
- Skeleton lines on comparison layers (still primary-only).
- Editing on comparison layers (still single-variant only).
- Pixel-diff regression of canvas output (manual visual sanity in the e2e is enough).
- Per-layer FPS / per-layer step (one global pair).

## UI

### Per-primary visibility row + legend glyph

In `card_viewer.html`, inside the existing comparison-layers block (`va-overlay-compare-block`), insert a new "primary row" between the "Comparison layers / + add comparison" header and the `va-overlay-compare-list` container:

```html
<div id="va-overlay-primary-row"
     style="display:flex;align-items:center;gap:.35rem;font-size:.74rem;padding:.15rem .25rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;margin-bottom:.25rem">
  <input type="checkbox" id="va-overlay-primary-visible" checked
         style="accent-color:var(--accent);width:12px;height:12px;flex-shrink:0"/>
  <span id="va-overlay-primary-shape"
        style="font-family:var(--mono);width:1.1rem;text-align:center;flex-shrink:0">●</span>
  <span style="font-size:.7rem;color:var(--text-dim);flex-shrink:0">Primary</span>
  <span id="va-overlay-primary-label"
        style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>
</div>
```

Behavior:

- Checkbox change → `_vaPrimary().visible = checkbox.checked` (default `true`); call `_vaDrawCurrentFrame()`.
- The shape glyph is updated by `_shapeGlyph(_vaPrimary().shape)` whenever primary swaps (i.e., inside `_vaApplyPrimaryFromSelect`). Always `●` since primary is always slot 0.
- The label text mirrors the currently-selected option in `va-overlay-primary-select`.
- The global `va-overlay-toggle` master remains the kill-switch — when off, `_vaDrawCurrentFrame()` clears the canvas regardless of per-layer `visible` flags. When on, per-layer `visible` decides who renders.

### FPS field in the player controls

In `card_viewer.html`'s player section, place this `<label>` block IMMEDIATELY BEFORE the existing `va-play-step` `<label>` (so the player's controls remain grouped: `fps` → `step` → skip-N):

```html
<label style="display:flex;align-items:center;gap:.3rem;font-size:.75rem;color:var(--text-dim);white-space:nowrap"
       title="Playback rate (frames per wall-clock second)">
  fps
  <input type="number" id="va-play-fps" value="5" min="1" max="120" step="1"
         style="width:46px;text-align:center;font-family:var(--mono);font-size:.78rem;padding:.18rem .3rem;background:var(--surface-2);border:1px solid var(--border);border-radius:5px;color:var(--text)">
</label>
```

Default = 5, range 1–120. JS reads the value on every play-loop tick.

**Effective playback formula:** `video-frames advanced per wall-clock second = play-fps × play-step`. Example: fps=5, step=1 → 5 video-frames/sec (slow review with markers always synced). fps=10, step=3 → 30 video-frames/sec but each tick still waits for full marker render.

### Collapsible Dataset Curation panel

Find `<div id="va-curation-panel" …>` (around line 224 of `card_viewer.html`). Replace its current header with a `va-overlay-toggle`-style master:

```html
<div id="va-curation-panel" style="margin-top:.65rem;padding:.5rem .65rem;background:var(--surface-2);border:1px solid var(--border);border-radius:7px">
  <!-- Toggle row (master kill-switch for the curation tools) -->
  <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap">
    <label style="display:flex;align-items:center;gap:.45rem;font-size:.8rem;font-weight:500;cursor:pointer;user-select:none">
      <input type="checkbox" id="va-curation-toggle"
             style="accent-color:var(--accent);width:14px;height:14px"/>
      Dataset Curation
    </label>
    <span id="va-curation-status" class="fe-extract-status" style="font-size:.73rem;flex:1;min-width:0"></span>
  </div>

  <!-- Existing inner content moves here, wrapped in a hidden-by-default div -->
  <div id="va-curation-controls" class="hidden" style="margin-top:.5rem">
    <!-- … all the existing rows (extract frame, add to dataset, etc.) … -->
  </div>
</div>
```

Default = unchecked → `va-curation-controls` starts hidden. The existing handlers attached to inner buttons are unaffected; they just become inaccessible while the toggle is off.

JS wiring (in `viewer.js`, near other listener registrations):

```js
const vaCurationToggle   = document.getElementById("va-curation-toggle");
const vaCurationControls = document.getElementById("va-curation-controls");
vaCurationToggle?.addEventListener("change", () => {
  vaCurationControls?.classList.toggle("hidden", !vaCurationToggle.checked);
});
```

### IDs (for unique-ID assertions)

New: `va-overlay-primary-row`, `va-overlay-primary-visible`, `va-overlay-primary-shape`, `va-overlay-primary-label`, `va-play-fps`, `va-curation-toggle`, `va-curation-controls`.

Retained from prior work: every existing `va-overlay-*`, `va-curation-*`, `va-play-step`, `va-skip-n`, `va-overlay-toggle`, `va-overlay-controls`.

## Shape ladder change

Drop `circle-open`. New ladder:

| Slot | Shape glyph | Canvas primitive |
|---|---|---|
| Primary (layer 0) | ● filled disc | `_drawCircleFilled(ctx, x, y, r, color)` |
| Comparison 1 | ◆ filled diamond | `_drawDiamond(ctx, x, y, r, color)` (new) |
| Comparison 2 | □ square outline | `_drawSquare(ctx, x, y, r, color)` |
| Comparison 3+ | △ triangle outline | `_drawTriangle(ctx, x, y, r, color)` |

JS changes:

- `_SHAPE_ORDER` becomes `["circle-filled", "diamond", "square", "triangle"]`.
- New primitive:
  ```js
  function _drawDiamond(ctx, x, y, r, color) {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(x,     y - r);
    ctx.lineTo(x + r, y    );
    ctx.lineTo(x,     y + r);
    ctx.lineTo(x - r, y    );
    ctx.closePath();
    ctx.fill();
  }
  ```
- `_drawCircleOpen` and its `_SHAPE_FN` entry are removed.
- `_shapeGlyph` switch updated:
  ```js
  function _shapeGlyph(shape) {
    switch (shape) {
      case "circle-filled": return "●";
      case "diamond":       return "◆";
      case "square":        return "□";
      case "triangle":      return "△";
      default:              return "?";
    }
  }
  ```

The new primary row uses `_shapeGlyph(_vaPrimary().shape)` to render its badge, so primary always shows `●`.

## Strict load gate + FPS playback

### Atomic image+markers swap in `_vaLoadFrame`

Today's `_vaLoadFrame` sets `vaFrameImg.src = newUrl` early, before pose fetches return. The browser swaps the visible image as soon as it decodes; the canvas redraw with new poses lands hundreds of ms later when comparison-layer fetches complete. The user sees frame N's image with frame N-1's markers in that interval.

Fix: preload the new image off-DOM in parallel with all visible-layer pose fetches; only after BOTH resolve do we (a) set `vaFrameImg.src`, (b) `_vaDrawCurrentFrame()`, (c) await rAF, (d) resolve.

```js
async function _vaLoadFrame(n) {
  if (n === _vaCurrentFrame && /* not the initial load */ ...) return;
  _vaCurrentFrame = n;
  vaFrameSpinner.classList.remove("hidden");

  const newUrl = _vaFrameUrl(n);

  // Preload image off-DOM in parallel with pose fetches.
  const imgReady = new Promise((resolve, reject) => {
    const im = new Image();
    im.onload  = () => resolve(newUrl);
    im.onerror = reject;
    im.src = newUrl;
  });

  const posesReady = _vaOverlayEnabled
    ? Promise.all(
        _vaLayers
          .filter(l => l.visible && !l.errored)
          .map(l => _vaFetchPosesForFrame(l, n).catch(() => null))
      )
    : Promise.resolve();

  try {
    await Promise.all([imgReady, posesReady]);
  } catch (e) {
    vaFrameSpinner.classList.add("hidden");
    return;
  }

  // Atomic swap: image + markers go to screen together.
  vaFrameImg.src = newUrl;
  _vaDrawCurrentFrame();
  vaFrameSpinner.classList.add("hidden");

  // Paint barrier — guarantees the canvas + image have landed before
  // the play loop schedules the next tick.
  await new Promise(requestAnimationFrame);
  _vaPrefetchPoseWindow(n + 1);
}
```

The per-layer `.catch(() => null)` ensures one slow/failing layer doesn't block the swap forever.

### FPS-driven play loop

Replace the existing tick-delay computation (which uses `_vaFps` from the video file) with two helpers:

```js
function _vaPlaybackFps() {
  const v = parseInt(document.getElementById("va-play-fps")?.value || "5", 10);
  return Math.max(1, Math.min(120, isNaN(v) ? 5 : v));
}

function _vaPlayDelayMs() {
  return Math.round(1000 / _vaPlaybackFps());
}
```

The play loop already awaits `_vaLoadFrame()` before scheduling the next tick. Combined with the strict atomic swap above, the next tick fires `1000/_vaPlaybackFps()` ms after the previous frame fully landed. If pose fetches are slower than the budget, the loop naturally throttles — the user dialed FPS down to 5 for exactly this reason.

`play-step` continues to advance multiple video-frames per tick (`effective video-frames/sec = play-fps × play-step`).

The pre-existing `_vaFps` (detected from the video file) stays available for the time-display readout (`Frame N / count` + `seconds`) but is no longer consumed by the play loop's tick delay.

## Error handling

- **Image preload fails** (404 / network blip): `imgReady` rejects, `_vaLoadFrame` cleans up the spinner and returns early without swapping. Previous frame stays on screen. Next play tick retries `n`.
- **All visible-layer pose fetches fail simultaneously**: per-layer `.catch(() => null)` already neutralizes per-layer rejections, so `Promise.all` resolves with nulls; `_vaDrawCurrentFrame` skips errored layers. The atomic swap proceeds — we'd rather show the new image without markers than block playback indefinitely.
- **Play loop with `play-fps = 1`**: tick delay = 1000ms; fine. With `play-fps = 120`: tick delay = 8ms; the load gate naturally throttles to whatever the slowest pose fetch allows.
- **User changes `play-fps` mid-playback**: the next tick reads the new value (via `_vaPlayDelayMs()`); no special restart needed.
- **User toggles primary visibility off then on while paused**: `_vaDrawCurrentFrame()` runs immediately on each toggle; primary's cached poses redraw without re-fetch. Same for comparison checkboxes.
- **Curation panel toggle**: `va-curation-toggle` change handler toggles `va-curation-controls.hidden`. Existing curation handlers are unaffected — they're just inaccessible while the toggle is off.
- **Visibility checkbox interaction with the global master**: `va-overlay-toggle` gates `_vaOverlayEnabled`. When `_vaOverlayEnabled === false`, `_vaDrawCurrentFrame` clears the canvas regardless of per-layer `visible` flags. When `true`, per-layer `visible` decides.

## Edge cases

- **Layer added or removed mid-playback**: `_vaPrefetchPoseWindow` re-runs from `n + 1`; new layers populate their pose cache lazily on the next tick.
- **`play-step` skips past `_vaFrameCount`**: existing guard (`next >= _vaFrameCount`) stops playback at the end. With `step=10` near frame 795 of 800, the loop stops gracefully.
- **Image preload race when user scrubs the seek bar during playback**: pause first (existing seek handler does this); the scrub triggers a fresh `_vaLoadFrame(n)` which awaits image+poses normally.
- **Per-primary visibility with edit-mode**: edit mode is gated by `_vaIsEditable()` (`_vaLayers.length === 1`). Primary visibility doesn't affect that — the user can hide primary's markers and still edit (drag handlers operate on `_vaPrimary().path`, not on visible layers). Documented in code comment; not a behavioral change.
- **Diamond at very small marker sizes** (3 px): becomes a tiny rotated square. Still visually distinct from a circle. No floor change required.

## Testing

| Test file | New / extended cases |
|---|---|
| `tests/test_viewer_layers_ui_isolation.py` (extend) | New IDs `va-overlay-primary-row`, `va-overlay-primary-visible`, `va-overlay-primary-shape`, `va-overlay-primary-label`, `va-play-fps`, `va-curation-toggle`, `va-curation-controls` are unique across partials. JS-source asserts: `_drawDiamond` defined; `_drawCircleOpen` removed (string `_drawCircleOpen` not present); `_SHAPE_ORDER` contains `"diamond"` and not `"circle-open"`; `_vaPlaybackFps` and `_vaPlayDelayMs` defined; `va-play-fps` referenced; the atomic-swap pattern (`Promise.all([imgReady, posesReady])` literal) appears in `viewer.js`; the curation toggle handler references `va-curation-controls`. |
| `tests/e2e_viewer_layers_smoke.py` (extend) | **Phase I (per-primary visibility):** uncheck `va-overlay-primary-visible`, verify primary markers gone (sample canvas pixel at a known marker coord; expect background colour). Re-check; markers reappear. **Phase J (FPS field):** set `va-play-fps = 2`, click Play, time the wall-clock between two `va-frame-counter` updates → must be ≥ 400ms (1000/2 = 500ms ±100ms). **Phase K (atomic swap):** hook `vaFrameImg.onload` and a counter that increments on each `_vaDrawCurrentFrame` call (instrument via JS injection); after a single playback tick, the difference between the two counts is ≤ 1 (i.e., they fire within the same tick). **Phase L (curation toggle):** verify `va-curation-controls` starts hidden; click `va-curation-toggle`; verify it's now visible. |
| `tests/test_dlc_viewer_routes.py` | No changes — backend unchanged. |

## File summary

**Modified files:**

- `src/templates/partials/card_viewer.html` — primary-row block + `va-play-fps` input + curation panel restructure.
- `src/static/js/viewer.js` — drop `_drawCircleOpen`, add `_drawDiamond`, update `_SHAPE_ORDER` + `_shapeGlyph`; add `_vaPlaybackFps` + `_vaPlayDelayMs`; rewrite `_vaLoadFrame` for atomic swap; rewrite play loop tick delay; add per-primary visibility checkbox handler; add curation toggle handler; on primary swap update `va-overlay-primary-shape` + `va-overlay-primary-label`.
- `tests/test_viewer_layers_ui_isolation.py` — new IDs + JS-source asserts.
- `tests/e2e_viewer_layers_smoke.py` — Phases I/J/K/L.

**New files:** none.

## Open items for the implementation plan

- Confirm whether the existing play loop (`_vaPlayLoop`) reads `_vaFps` directly or via a helper; the helper rename from "video-detected fps" to "user-overridden fps" needs to land cleanly.
- Verify the existing `vaFrameImg.onload` handler doesn't have side effects beyond the spinner (the atomic swap may need to skip those side effects since the off-DOM preload no longer triggers them on the visible img).
- Confirm there's room in the player controls layout for the new `fps` field next to the existing `step` and skip-N (player flex-wrap behavior).
- The Phase K test's "instrument via JS injection" needs a stable hook; the simplest is to wrap `_vaDrawCurrentFrame` to bump a `window.__vaDrawCount` counter from the test prelude. Confirm `_vaDrawCurrentFrame` is module-scoped and accessible for monkey-patching from the test.
