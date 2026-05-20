# Inline Analysis — Polish (revision spec)

**Date:** 2026-05-20
**Status:** Approved for implementation planning
**Parent spec:** `docs/superpowers/specs/2026-05-20-inline-analysis-design.md`
**Branch:** `feat/inline-analysis`

## Goal

The first-pass Inline Analysis card shipped functional end-to-end (verified: 200 frames analyzed cleanly on a real video). Five focused polish items remain before it matches the rest of the dashboard's UX. This spec captures them.

## §1 — Five changes

### 1.1 Open at bottom like every other card

`src/static/js/inline_analysis.js` currently does:

```javascript
function openCard() {
  hideAllOtherCards();         // ← bug: collapses all other open cards
  card.classList.remove("hidden");
}
```

Every other dashboard card (Analyze, View-Analyzed, Postprocess, etc.) just toggles its own visibility and scrolls itself into view. Match that:

```javascript
function openCard() {
  card.classList.remove("hidden");
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  refreshSnapshots();
}
```

Delete the `hideAllOtherCards` helper and its only caller.

### 1.2 Double-click collapses the file browser, leaves path input visible

The file browser's `onPick` callback already loads the video; it just doesn't collapse the pane. Add one line:

```javascript
onPick: (path) => {
  videoPath.value = path;
  browserPane.classList.add("hidden");    // ← new
  loadVideo(path);
}
```

- The path text input + Browse button + "Hide videos without h5" toggle stay visible above the player.
- Clicking Browse again re-opens the pane.
- The player section that's already in the markup renders the chosen frame; the start frame for the next Analyze click is whatever the user has scrubbed to (`_player.getCurrentFrame()` — already wired).

### 1.3 Snapshot picker — full parity with Analyze card

The Analyze card exposes three model-selection inputs together: snapshot dropdown (with `<label> · iter N · shM` format and a "Latest" default), shuffle integer, and trainingsetindex integer. Inline Analysis currently has only a bare snapshot dropdown showing `s.label`.

Changes:

- **Card markup** (`card_inline_analysis.html`) — replace the existing single-row snapshot block with three rows mirroring `card_analyze.html`:
  ```html
  <div class="scorer-row">
    <label for="ia-shuffle">Shuffle</label>
    <input type="number" id="ia-shuffle" value="1" min="1" max="20" style="width:5rem"/>
  </div>
  <div class="scorer-row">
    <label for="ia-trainingsetindex">Training set index</label>
    <input type="number" id="ia-trainingsetindex" value="0" min="0" style="width:5rem"/>
  </div>
  <div class="scorer-row">
    <label for="ia-snapshot">Snapshot</label>
    <select id="ia-snapshot" style="flex:1"></select>
    <button class="btn-sm" id="ia-refresh-snapshots" title="Refresh">↺</button>
  </div>
  ```
- **JS** (`inline_analysis.js`) — rewrite `refreshSnapshots()` to mirror `analyze.js:_avLoadSnapshots` verbatim:
  - Same `/dlc/project/snapshots` fetch (no shuffle filter; the route auto-derives shuffle from the chosen snapshot path).
  - "Latest" default option uses `data.latest_rel_path` as `value` and `Latest — <label> · iter N · shM` as text.
  - Each snapshot option: `value = s.rel_path`, `textContent = '<label> · iter N · shM'`.
  - Wire `ia-shuffle` change handler to reload snapshots (snapshot indices are per-shuffle).
- **`/range` POST body** — include `shuffle` from `#ia-shuffle` and `trainingsetindex` from `#ia-trainingsetindex` (the route already accepts these; today's JS hardcodes them to 1 and 0).

### 1.4 Strip marker overlay to "show only labels just generated"

Remove from `card_inline_analysis.html`:

- `<select id="ia-overlay-primary-select">` (Primary layer dropdown)
- `<select id="ia-overlay-add-compare">` (`+ add comparison…`)
- `<div id="ia-overlay-compare-list">` (the rendered comparison list)
- The "Primary" label and the row that contains them

Keep:

- `Show markers` toggle (`ia-overlay-toggle`)
- Threshold slider (`ia-overlay-threshold`)
- Marker size input (`ia-overlay-marker-size`)
- Body-part chips container `ia-bp-chips` (newly added — currently missing; mirrors `va-bp-chips` from `card_viewer.html`)

After each successful range completes (status=done in the result poll), `inline_analysis.js` calls the factory's existing `setPrimaryLayer({ path, label })` to register the canonical h5 as the single visible layer, then `reloadH5()` to drop the per-layer pose cache and re-fetch poses for the visible window. Both methods are already exposed by the factory (verified at lines 258 / 231 of `analyzed_frame_player.js`); no factory changes needed.

`h5_path` is constructed client-side from `video_path` + `scorer`. The `scorer` is **not** currently returned in the result hash. **Decision:** include `scorer` in the `/range/status` `done` payload so the JS can construct the path without a second round-trip. The worker already has it in scope at the call site that publishes the result.

### 1.5 Dataset Curation — full mirror

The card currently has only a useless `<input type="checkbox" id="ia-curation-toggle">` with no panel. The curation workflow is broken.

**Markup change** (`card_inline_analysis.html`): copy the full Dataset Curation block from `card_viewer.html` (lines 240–371 — the `va-curation-panel` and everything inside it), replacing every `va-` ID prefix with `ia-`. That brings in:

- Extract Frame button
- + Add to Dataset button
- Batch add (count + every-N-frames + Batch Add button)
- Companion CSV section (no-CSV state + create-CSV button; CSV-loaded state with path display)
- Status + Notes timeline canvases with prev/next buttons + tag chips
- Annotation panel (status input + save, note input + save, save-tag flow)

**JS change** (`inline_analysis.js`): wire the same handlers `viewer.js` uses for the `va-*` IDs, against the new `ia-*` IDs. Per the parent spec's §4 (Option B / "don't refactor working code"), we **copy** the handlers into `inline_analysis.js` rather than refactor `viewer.js`'s implementation into the shared factory in this PR. Tech debt: documented in the parent spec's "Known tech debt" section already; this PR adds one more bullet noting curation handlers are now duplicated too.

Curation routes (`/dlc/curator/extract-frame`, `/dlc/curator/add-to-dataset`, `/dlc/curator/save-annotation`, `/annotate/create-csv`) are unchanged — they're video-path agnostic and serve both cards.

## §2 — Implementation order

The five changes are mostly independent; landing them as small commits makes review easier and lets us roll back any single one without disturbing the others:

1. **Card-open fix** — 1-line change (`inline_analysis.js`). Smoke: open card while another is open; both visible. Commit: `fix(static): inline-analysis card opens at bottom (stack, not replace)`.
2. **Dblclick collapse** — 1-line change. Smoke: dbl-click a video; browser pane hidden, path input visible. Commit: `feat(static): collapse file browser on inline-analysis video dblclick`.
3. **Snapshot picker parity** — markup (~6 lines) + JS rewrite (~30 lines). Smoke: snapshot dropdown shows `iter N · shM`, "Latest" default works, shuffle change reloads snapshots, range POST includes shuffle + trainingsetindex. Commit: `feat(static): inline-analysis snapshot picker mirrors analyze card`.
4. **Marker overlay simplification + auto-load** — markup (drop 3 elements, add 1) + JS (call `_player.loadH5Layer` after range done). Worker change: include `scorer` in `/range/status` done payload. Smoke: run range, see markers automatically on the player. Commit: `feat(static): inline-analysis overlay shows only the just-produced h5`.
5. **Curation panel full mirror** — biggest patch (~400 lines markup, ~600 lines JS handlers). Smoke: open card → enable Dataset Curation → Extract Frame writes a PNG into `labeled-data/`; Add to Dataset appends a row to the CollectedData CSV; Batch Add works; Create CSV works; status/notes save back; tag chips clickable. Commit: `feat(static): inline-analysis dataset curation panel mirror`.

## §3 — Out of scope (explicit)

- **No new server routes.** All five changes are JS + HTML, except 1.4's one-field addition to the existing `/range/status` response (worker code, server-side).
- **No refactor of `viewer.js`.** Per the parent spec's Option B; the duplication is accepted.
- **No changes to the warm-worker code path.** No model-load, no Celery, no Redis changes (except the worker's result-hash adds one field).

## §4 — Files touched / created

Modified:
- `src/templates/partials/card_inline_analysis.html` — drop overlay primary/compare row, add body-part chips container, add shuffle + trainingsetindex inputs, mirror full curation panel block.
- `src/static/js/inline_analysis.js` — drop `hideAllOtherCards`; `onPick` collapses browser pane; rewrite `refreshSnapshots` to match Analyze; add shuffle change handler; wire curation panel handlers; auto-mount h5 layer after range completes.
- `src/dlc/inline_analysis.py` — `/range/status` done response includes `scorer` so the JS can construct the canonical h5 path without guessing.
- `src/dlc/tasks.py` — worker writes `scorer` to the result hash (one line in `_publish_result` call site).
- `docs/superpowers/specs/2026-05-20-inline-analysis-design.md` — "Known tech debt" gets one bullet: "Dataset Curation handlers duplicated between `viewer.js` and `inline_analysis.js` — slated for migration to the shared factory in the same follow-up PR that migrates the player."

Not modified:
- `src/static/js/viewer.js` — stays byte-identical to main per parent spec §4.
- `src/templates/partials/card_viewer.html` — same.
- `src/static/js/components/analyzed_frame_player.js` — no changes needed; the factory's layer rendering already supports a single-layer flow (no comparison required).
- Any DLC primitives or routes.
- The warm-worker session protocol.

## §5 — Tests

This is a UI polish PR; the existing test suite stays valid. New assertions in the affected test files:

- `tests/test_inline_analysis_ui_isolation.py`:
  - assert `card.classList` toggling doesn't touch other `section.card` elements (i.e., no `hideAllOtherCards` call).
  - assert the curation panel markup IDs exist (`ia-extract-frame-btn`, `ia-add-to-dataset-btn`, `ia-batch-add-btn`, `ia-csv-section`, `ia-status-canvas`, `ia-note-canvas`, `ia-annot-panel`).
  - assert no `ia-overlay-primary-select` or `ia-overlay-add-compare` IDs in the partial.
  - assert `ia-shuffle` and `ia-trainingsetindex` inputs exist.
- `tests/test_inline_analysis_routes.py`:
  - extend the existing `done`-status test to assert the response includes `scorer`.
- `tests/e2e_inline_analysis_smoke.py`:
  - extend the smoke to verify: dbl-click hides browser pane; opening another card doesn't close inline.
- `tests/test_inline_analysis_gpu_smoke.py` — unchanged; this PR doesn't affect the warm-worker round-trip.

No new test files. All changes are additive within the existing test fixtures.

## §6 — Acceptance criteria

- All five smoke checks in §2 pass manually on the running app.
- `viewer.js` and `card_viewer.html` byte-identical to `main` (already guarded by tests).
- `pytest -m "not gpu and not e2e"` passes.
- The card looks visually consistent with Analyze + View-Analyzed (same params block style, same curation panel layout, same dropdown formatting).
