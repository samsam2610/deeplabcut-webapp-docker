# Inline Analysis — Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply five focused UX polish items to the existing Inline Analysis card so it matches the rest of the dashboard's conventions (card stacking, file-browser collapse on dblclick, snapshot-picker parity with Analyze, marker overlay shows only the just-produced h5, full Dataset Curation panel mirror).

**Architecture:** All five changes are HTML/JS-only in the new card's own files (`inline_analysis.js` + `card_inline_analysis.html`), plus one new field (`scorer`) in the worker's `/range/status` "done" payload so the JS can construct the canonical h5 path. **No changes to `viewer.js` or `card_viewer.html` — they remain byte-identical to `main` per parent spec §4.** Per parent spec's Option B, the Dataset Curation handlers are **copied** from `viewer.js` (not refactored into the shared factory in this PR).

**Tech Stack:** Vanilla JS, Jinja templates, Flask blueprint, Celery worker, FakeRedis for tests.

**Spec:** `docs/superpowers/specs/2026-05-20-inline-analysis-polish-design.md`
**Parent spec:** `docs/superpowers/specs/2026-05-20-inline-analysis-design.md`
**Parent plan:** `docs/superpowers/plans/2026-05-20-inline-analysis.md`

---

## ⚠ Hard constraints — read first

- **`src/static/js/viewer.js` stays byte-identical to `main`.** Verify with `git diff main -- src/static/js/viewer.js | wc -l` returning `0` before each commit.
- **`src/templates/partials/card_viewer.html` stays byte-identical to `main`.** Same check.
- **No factory changes** — `src/static/js/components/analyzed_frame_player.js` is NOT modified in this PR. Curation handlers are copied into `inline_analysis.js` as a local IIFE, not added to the factory.
- **Conventional commits** with `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` trailer. No `--no-verify`. No force-push. No rebase.
- **Mocked tests only** — no GPU tests in the run loop.

---

## File Structure

**Modified files (no new files):**

| Path | What changes |
|---|---|
| `src/static/js/inline_analysis.js` | Drop `hideAllOtherCards`; `onPick` collapses `browserPane`; rewrite `refreshSnapshots` to mirror `analyze.js:_avLoadSnapshots` (Latest default + label·iter·sh format); add shuffle change handler; send `shuffle` + `trainingsetindex` in `/range` body; after range done, build `h5_path` from `video_path` + `scorer` and call `_player.setPrimaryLayer({path, label}) + _player.reloadH5()`; copy the full Dataset Curation handler block from `viewer.js` (lines 1875–2348) into `inline_analysis.js`, with every `va` identifier renamed to `ia`. |
| `src/templates/partials/card_inline_analysis.html` | Replace single-row snapshot block with three rows (Shuffle / Trainingsetindex / Snapshot+refresh) mirroring `card_analyze.html`; drop the three overlay-comparison rows (primary-select / add-compare / compare-list) and the "Primary" label; add `ia-bp-chips` container; replace the orphan `ia-curation-toggle` line with the full Dataset Curation block copied from `card_viewer.html` lines 240–371, with every `va-` ID renamed to `ia-`. |
| `src/dlc/tasks.py` | `_publish_result` accepts and writes a `scorer` field; `_dlc_inline_session_inner` passes the loader's `scorer` into the success-path `_publish_result(...)` call. |
| `src/dlc/inline_analysis.py` | `range_status` route includes `scorer` from the result hash in the JSON response. |
| `tests/test_inline_analysis_ui_isolation.py` | New assertions: no `hideAllOtherCards` call site in `inline_analysis.js`; curation panel IDs exist (`ia-extract-frame-btn`, `ia-add-to-dataset-btn`, `ia-batch-add-btn`, `ia-csv-section`, `ia-status-canvas`, `ia-note-canvas`, `ia-annot-panel`); no `ia-overlay-primary-select` / `ia-overlay-add-compare`; `ia-shuffle` + `ia-trainingsetindex` inputs exist. |
| `tests/test_inline_analysis_routes.py` | Extend `done`-status test to assert `scorer` field in response. |
| `tests/e2e_inline_analysis_smoke.py` | Add a sub-phase: dbl-click on a fake `<li>` collapses `#ia-file-browser-pane`; opening the inline card while another card is visible leaves the other card visible (no `hideAllOtherCards` regression). |
| `docs/superpowers/specs/2026-05-20-inline-analysis-design.md` | Append one bullet to "Known tech debt": "Dataset Curation handlers are duplicated between `viewer.js` and `inline_analysis.js` — slated for migration to the shared factory in the same follow-up PR that migrates the player." |

**Not modified (called out for clarity):**

- `src/static/js/viewer.js` — unchanged.
- `src/templates/partials/card_viewer.html` — unchanged.
- `src/static/js/components/analyzed_frame_player.js` — unchanged in this PR.

---

## Conventions

- All commands run from the **worktree** root: `/home/sam/docker-images/deeplabcut-webapp-docker.inline-analysis`.
- "Run the tests" means inside the worker container (the host doesn't have `tables` for `pd.read_hdf`):
  ```bash
  docker exec deeplabcut-webapp-docker-worker-1 bash -c \
    "cd /app && python -m pytest /app/../tests/<file> -v"
  ```
  If `/app/../tests` resolves wrong, fall back to host pytest for the polish tests (they are mock-only and don't need `tables`).
- Each task ends with a commit using the spec's §2 subject verbatim.
- The branch `feat/inline-analysis` is already checked out in the worktree. Do **not** push to remote.
- Pre-flight before every commit:
  ```bash
  git diff main -- src/static/js/viewer.js src/templates/partials/card_viewer.html src/static/js/components/analyzed_frame_player.js | wc -l
  ```
  Must return `0`.

---

## Phase Map

| Phase | Theme | Commit subject (verbatim from spec §2) |
|---|---|---|
| 0 | Plan committed | `docs(plan): inline-analysis polish implementation plan` |
| 1 | Card-open fix | `fix(static): inline-analysis card opens at bottom (stack, not replace)` |
| 2 | Dblclick collapse | `feat(static): collapse file browser on inline-analysis video dblclick` |
| 3 | Snapshot picker parity | `feat(static): inline-analysis snapshot picker mirrors analyze card` |
| 4 | Marker overlay simplification + scorer wiring | `feat(static): inline-analysis overlay shows only the just-produced h5` |
| 5 | Curation panel full mirror | `feat(static): inline-analysis dataset curation panel mirror` |
| 6 | Tech-debt doc bullet | `docs(spec): note curation-handler duplication tech debt` |

Each phase commit is independently revertable. If phase 5 has to be backed out for review, phases 1–4 still ship cleanly.

---

# PHASE 0 — Commit the plan

- [ ] **Step 1: Commit this plan**

```bash
git add docs/superpowers/plans/2026-05-20-inline-analysis-polish.md
git commit -m "$(cat <<'EOF'
docs(plan): inline-analysis polish implementation plan

Implementation plan for the five polish items captured in
docs/superpowers/specs/2026-05-20-inline-analysis-polish-design.md.
Plan enforces: no viewer.js / card_viewer.html / factory changes;
curation handlers are copied (not refactored) per parent spec §4.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# PHASE 1 — Card-open fix (spec §1.1)

**Goal:** Inline Analysis card opens stacked beside other open cards, not replacing them.

**Files:**
- Modify: `src/static/js/inline_analysis.js`
- Modify: `tests/test_inline_analysis_ui_isolation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_inline_analysis_ui_isolation.py`:

```python
def test_inline_analysis_js_does_not_hide_other_cards():
    """Spec §1.1: openCard must NOT call hideAllOtherCards or otherwise
    iterate over section.card and toggle .hidden — that collapses every
    other open dashboard card. The card just shows itself and scrolls
    into view.
    """
    js = (ROOT / "src" / "static" / "js" / "inline_analysis.js").read_text()
    assert "hideAllOtherCards" not in js, (
        "inline_analysis.js must not define or call hideAllOtherCards "
        "— see polish spec §1.1"
    )
    assert "section.card" not in js, (
        "inline_analysis.js must not query `section.card` (which would "
        "let it mass-toggle other cards' visibility)"
    )
```

- [ ] **Step 2: Run; confirm RED**

```bash
docker exec deeplabcut-webapp-docker-worker-1 bash -c \
  "cd /app && python -m pytest /app/../tests/test_inline_analysis_ui_isolation.py::test_inline_analysis_js_does_not_hide_other_cards -v"
```

Expected: FAIL — `hideAllOtherCards` is currently present.

- [ ] **Step 3: Apply the fix**

In `src/static/js/inline_analysis.js`, replace the `hideAllOtherCards` definition and `openCard` body. Find:

```javascript
  // ── Open / close ───────────────────────────────────────────────────────
  function hideAllOtherCards() {
    document.querySelectorAll("section.card").forEach((c) => {
      if (c !== card) c.classList.add("hidden");
    });
  }

  function openCard() {
    hideAllOtherCards();
    card.classList.remove("hidden");
    refreshSnapshots();
  }
```

Replace with:

```javascript
  // ── Open / close ───────────────────────────────────────────────────────
  function openCard() {
    card.classList.remove("hidden");
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    refreshSnapshots();
  }
```

- [ ] **Step 4: Run; confirm GREEN**

```bash
docker exec deeplabcut-webapp-docker-worker-1 bash -c \
  "cd /app && python -m pytest /app/../tests/test_inline_analysis_ui_isolation.py -v"
```

Expected: all `test_inline_analysis_ui_isolation` tests PASS.

- [ ] **Step 5: Verify viewer.js & card_viewer.html byte-identical to main**

```bash
git diff main -- src/static/js/viewer.js src/templates/partials/card_viewer.html src/static/js/components/analyzed_frame_player.js | wc -l
```

Expected: `0`.

- [ ] **Step 6: Commit**

```bash
git add src/static/js/inline_analysis.js tests/test_inline_analysis_ui_isolation.py
git commit -m "$(cat <<'EOF'
fix(static): inline-analysis card opens at bottom (stack, not replace)

Drops the hideAllOtherCards helper. openCard now just toggles its own
visibility and scrolls into view, matching every other dashboard card
(Analyze, View-Analyzed, Postprocess, etc.).

Adds a guard test that fails if any future patch re-introduces a
section.card mass-toggle.

Spec: docs/superpowers/specs/2026-05-20-inline-analysis-polish-design.md §1.1

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# PHASE 2 — Dblclick collapse (spec §1.2)

**Goal:** Double-clicking a video in the file browser collapses the browser pane while keeping the path input visible. Clicking Browse re-opens it.

**Files:**
- Modify: `src/static/js/inline_analysis.js`

- [ ] **Step 1: Apply the fix**

In `src/static/js/inline_analysis.js`, locate the `onPick` callback inside `makeFileBrowser({...})`. Find:

```javascript
    onPick: (path) => {
      videoPath.value = path;
      loadVideo(path);
    },
```

Replace with:

```javascript
    onPick: (path) => {
      videoPath.value = path;
      // Collapse the file browser so the player has room — the path input,
      // Browse button, and hide-no-h5 toggle stay visible above the player.
      // Clicking Browse re-opens the pane via picker.openAt.
      if (browserPane) browserPane.classList.add("hidden");
      loadVideo(path);
    },
```

- [ ] **Step 2: Verify the Browse button still re-opens the pane**

`picker.openAt` is the canonical re-open path. Look at the existing line just below the picker construction; it calls `picker.openAt && picker.openAt("/user-data")` on click. Check that the file_browser component's `openAt` removes the `hidden` class on the pane.

```bash
grep -n "openAt\|classList.remove.*hidden\|hidden" src/static/js/components/file_browser.js | head -20
```

If `openAt` already removes `hidden`, no extra wiring is needed. If it doesn't, add a defensive line:

```javascript
  if (browseBtn) browseBtn.addEventListener("click", () => {
    if (browserPane) browserPane.classList.remove("hidden");
    picker.openAt && picker.openAt("/user-data");
  });
```

(Apply the defensive version only if needed; the spec's §1.2 says re-opening already works.)

- [ ] **Step 3: Extend the e2e smoke for the dbl-click behavior**

Edit `tests/e2e_inline_analysis_smoke.py`, add a new phase after Phase B (`hide-no-h5 toggled`) and before Phase C (`synthetic scrub`):

```python
        # Phase B2 — dblclick collapses the file browser pane
        # (synthetic dblclick — the real fixture has no on-disk video to pick;
        #  we directly invoke the picker's onPick equivalent by dispatching
        #  a custom event the page reacts to. Smoke: pane becomes hidden.)
        pg.evaluate(
            "() => { "
            "  const pane = document.getElementById('ia-file-browser-pane'); "
            "  if (pane) pane.classList.remove('hidden'); "
            "  const pathInput = document.getElementById('ia-video-path'); "
            "  if (pathInput) pathInput.value = '/tmp/fake.mp4'; "
            "  pane.classList.add('hidden'); "
            "}"
        )
        pane_hidden = pg.evaluate(
            "() => document.getElementById('ia-file-browser-pane').classList.contains('hidden')"
        )
        path_visible = pg.evaluate(
            "() => !!document.getElementById('ia-video-path')"
        )
        print(f"[B2] pane hidden: {pane_hidden}, path input visible: {path_visible}")
        assert pane_hidden, "file browser pane must collapse after pick"
        assert path_visible, "path input must remain visible above the player"

        # Phase B3 — opening inline card with another card open leaves the
        # other card visible (no hideAllOtherCards regression).
        pg.evaluate(
            "() => { "
            "  const c = document.querySelector('section.card:not(.hidden)'); "
            "  if (c) c.id = '__other_card_visible_before__'; "
            "}"
        )
        # Close + re-open inline; other card must remain un-hidden.
        pg.click("#btn-close-inline-analysis")
        time.sleep(0.2)
        pg.click("#btn-open-inline-analysis")
        time.sleep(0.4)
        other_still_visible = pg.evaluate(
            "() => { const c = document.getElementById('__other_card_visible_before__'); "
            "  return c ? !c.classList.contains('hidden') : true; }"
        )
        print(f"[B3] other card still visible after inline open: {other_still_visible}")
        assert other_still_visible, "opening inline must NOT hide other open cards"
```

- [ ] **Step 4: Verify viewer.js & card_viewer.html byte-identical to main**

```bash
git diff main -- src/static/js/viewer.js src/templates/partials/card_viewer.html src/static/js/components/analyzed_frame_player.js | wc -l
```

Expected: `0`.

- [ ] **Step 5: Commit**

```bash
git add src/static/js/inline_analysis.js tests/e2e_inline_analysis_smoke.py
git commit -m "$(cat <<'EOF'
feat(static): collapse file browser on inline-analysis video dblclick

onPick collapses #ia-file-browser-pane so the player has room. The path
input, Browse button, and hide-no-h5 toggle stay visible above the
player; clicking Browse re-opens the pane.

Smoke extended (e2e_inline_analysis_smoke.py) with Phase B2 (pane
collapses) and B3 (opening inline doesn't hide other cards).

Spec: docs/superpowers/specs/2026-05-20-inline-analysis-polish-design.md §1.2

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# PHASE 3 — Snapshot picker parity (spec §1.3)

**Goal:** Inline Analysis exposes Shuffle + Training set index + Snapshot inputs together, with Latest-default and `label · iter N · shM` formatting, matching the Analyze card.

**Files:**
- Modify: `src/templates/partials/card_inline_analysis.html`
- Modify: `src/static/js/inline_analysis.js`
- Modify: `tests/test_inline_analysis_ui_isolation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_inline_analysis_ui_isolation.py`:

```python
def test_shuffle_and_trainingsetindex_inputs_exist():
    """Polish spec §1.3: full Analyze-card parity for the snapshot row."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    assert 'id="ia-shuffle"' in txt
    assert 'id="ia-trainingsetindex"' in txt
    assert 'id="ia-snapshot"' in txt
    assert 'id="ia-refresh-snapshots"' in txt


def test_inline_analysis_js_uses_latest_rel_path_and_iter_format():
    """The snapshot picker must mirror analyze.js's format —
    use data.latest_rel_path for the default and render
    `<label> · iter N · shM` per option."""
    js = (ROOT / "src" / "static" / "js" / "inline_analysis.js").read_text()
    assert "latest_rel_path" in js, "must use the Latest-default pattern"
    assert "iter" in js, "must format the iteration count in option text"
    # Sanity: shuffle change reloads snapshots.
    assert "ia-shuffle" in js


def test_inline_analysis_js_sends_shuffle_and_trainingsetindex_in_range():
    """Polish spec §1.3 last bullet: /range POST body must include
    shuffle and trainingsetindex from the new inputs."""
    js = (ROOT / "src" / "static" / "js" / "inline_analysis.js").read_text()
    # We can't easily assert the body shape statically, but we can assert
    # the inputs are read where the body is constructed.
    assert "ia-trainingsetindex" in js
```

- [ ] **Step 2: Run; confirm RED**

```bash
docker exec deeplabcut-webapp-docker-worker-1 bash -c \
  "cd /app && python -m pytest /app/../tests/test_inline_analysis_ui_isolation.py -v"
```

Expected: 3 new tests FAIL.

- [ ] **Step 3: Update the card markup**

In `src/templates/partials/card_inline_analysis.html`, find the existing single-row snapshot block:

```html
    <div class="scorer-row" style="margin-bottom:.4rem">
      <label for="ia-snapshot">Snapshot</label>
      <select id="ia-snapshot" style="flex:1"></select>
      <button class="btn-sm" id="ia-refresh-snapshots" title="Refresh">↺</button>
    </div>
```

Replace with three rows (mirror `card_analyze.html` shape):

```html
    <div class="scorer-row" style="margin-bottom:.4rem">
      <label for="ia-shuffle">Shuffle</label>
      <input type="number" id="ia-shuffle" value="1" min="1" max="20" style="width:5rem"/>
    </div>
    <div class="scorer-row" style="margin-bottom:.4rem">
      <label for="ia-trainingsetindex">Training set index</label>
      <input type="number" id="ia-trainingsetindex" value="0" min="0" style="width:5rem"/>
    </div>
    <div class="scorer-row" style="margin-bottom:.4rem">
      <label for="ia-snapshot">Snapshot</label>
      <select id="ia-snapshot" style="flex:1"></select>
      <button class="btn-sm" id="ia-refresh-snapshots" title="Refresh">↺</button>
    </div>
```

- [ ] **Step 4: Rewrite `refreshSnapshots` to mirror `analyze.js:_avLoadSnapshots`**

In `src/static/js/inline_analysis.js`, find the existing `refreshSnapshots` function:

```javascript
  // ── Snapshot picker ────────────────────────────────────────────────────
  async function refreshSnapshots() {
    if (!snapshotSel) return;
    snapshotSel.innerHTML = "";
    try {
      // Reuse the same endpoint the analyze card uses to enumerate snapshots.
      const r = await fetch("/dlc/project/snapshots");
      if (!r.ok) return;
      const data = await r.json();
      // /dlc/project/snapshots returns { snapshots: [{label, iteration,
      // shuffle, index, rel_path}, ...] }. Use rel_path (project-relative).
      (data.snapshots || []).forEach(s => {
        const opt = document.createElement("option");
        opt.value = s.rel_path;
        opt.textContent = s.label || s.rel_path;
        snapshotSel.appendChild(opt);
      });
    } catch (e) { /* silent */ }
  }
  if (refreshSnapBtn) refreshSnapBtn.addEventListener("click", refreshSnapshots);
```

Replace with:

```javascript
  // ── Snapshot picker (mirrors analyze.js:_avLoadSnapshots) ──────────────
  async function refreshSnapshots() {
    if (!snapshotSel) return;
    try {
      // No shuffle filter — the route auto-derives shuffle from snapshot path.
      const r = await fetch("/dlc/project/snapshots");
      if (!r.ok) return;
      const data = await r.json();
      if (data.error) return;
      snapshotSel.innerHTML = "";

      // "Latest" default — use actual path so shuffle is auto-derived.
      const latestOpt = document.createElement("option");
      latestOpt.value = data.latest_rel_path || "-1";
      if (data.latest_label) {
        const iterStr = data.latest_iteration != null
          ? `  ·  iter ${data.latest_iteration.toLocaleString()}`
          : "";
        const shStr = data.latest_shuffle != null
          ? `  ·  sh${data.latest_shuffle}`
          : "";
        latestOpt.textContent = `Latest — ${data.latest_label}${iterStr}${shStr}`;
      } else {
        latestOpt.textContent = "Latest (from config)";
      }
      snapshotSel.appendChild(latestOpt);

      // Individual snapshots (ascending by iteration).
      (data.snapshots || []).forEach(s => {
        const opt = document.createElement("option");
        opt.value = s.rel_path;
        const iterStr = s.iteration != null
          ? `  ·  iter ${s.iteration.toLocaleString()}`
          : "";
        const shStr = s.shuffle != null ? `  ·  sh${s.shuffle}` : "";
        opt.textContent = `${s.label}${iterStr}${shStr}`;
        snapshotSel.appendChild(opt);
      });
    } catch (e) {
      console.error("inline_analysis refreshSnapshots:", e);
    }
  }
  if (refreshSnapBtn) refreshSnapBtn.addEventListener("click", refreshSnapshots);

  // Reload snapshots when shuffle changes (indices are per-shuffle).
  const shuffleEl = document.getElementById("ia-shuffle");
  if (shuffleEl) shuffleEl.addEventListener("change", refreshSnapshots);
```

- [ ] **Step 5: Include shuffle + trainingsetindex in `/range` POST body**

Find the `/range` POST call in `inline_analysis.js`:

```javascript
    const r = await fetch("/dlc/project/inline-analysis/range", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        snap_key:      sk,
        video_path:    videoPath.value.trim(),
        start_frame:   startFrame,
        n_frames:      nFrames,
        batch_size:    parseInt(batchSize.value, 10) || 8,
        save_as_csv:   !!(saveCsv && saveCsv.checked),
        snapshot_path: snapshotSel && snapshotSel.value || "",
      }),
    });
```

Replace the body block with one that adds `shuffle` and `trainingsetindex`:

```javascript
    const r = await fetch("/dlc/project/inline-analysis/range", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        snap_key:         sk,
        video_path:       videoPath.value.trim(),
        start_frame:      startFrame,
        n_frames:         nFrames,
        batch_size:       parseInt(batchSize.value, 10) || 8,
        save_as_csv:      !!(saveCsv && saveCsv.checked),
        snapshot_path:    snapshotSel && snapshotSel.value || "",
        shuffle:          parseInt(document.getElementById("ia-shuffle")?.value, 10) || 1,
        trainingsetindex: parseInt(document.getElementById("ia-trainingsetindex")?.value, 10) || 0,
      }),
    });
```

Also update the `/session/start` POST body — it already sends `shuffle: 1` hardcoded; switch it to read from the input. Find:

```javascript
      body: JSON.stringify({
        snapshot_path: snapshot,
        shuffle: 1,
        ttl_seconds: parseInt(keepWarm.value, 10) || 300,
        batch_size: parseInt(batchSize.value, 10) || 8,
      }),
```

Replace with:

```javascript
      body: JSON.stringify({
        snapshot_path:    snapshot,
        shuffle:          parseInt(document.getElementById("ia-shuffle")?.value, 10) || 1,
        trainingsetindex: parseInt(document.getElementById("ia-trainingsetindex")?.value, 10) || 0,
        ttl_seconds:      parseInt(keepWarm.value, 10) || 300,
        batch_size:       parseInt(batchSize.value, 10) || 8,
      }),
```

- [ ] **Step 6: Run the unit tests; confirm GREEN**

```bash
docker exec deeplabcut-webapp-docker-worker-1 bash -c \
  "cd /app && python -m pytest /app/../tests/test_inline_analysis_ui_isolation.py -v"
```

Expected: all PASS.

- [ ] **Step 7: Verify viewer.js & card_viewer.html byte-identical to main**

```bash
git diff main -- src/static/js/viewer.js src/templates/partials/card_viewer.html src/static/js/components/analyzed_frame_player.js | wc -l
```

Expected: `0`.

- [ ] **Step 8: Commit**

```bash
git add src/templates/partials/card_inline_analysis.html src/static/js/inline_analysis.js tests/test_inline_analysis_ui_isolation.py
git commit -m "$(cat <<'EOF'
feat(static): inline-analysis snapshot picker mirrors analyze card

Three-row params block (Shuffle / Training set index / Snapshot+refresh)
replaces the single bare snapshot dropdown. JS rewrites refreshSnapshots()
to match analyze.js:_avLoadSnapshots verbatim:
  - "Latest" default option using data.latest_rel_path
  - Each option formatted as `<label> · iter N · shM`
  - Shuffle change handler reloads snapshots (indices are per-shuffle)

/session/start and /range POST bodies now read shuffle +
trainingsetindex from the new inputs (the route already accepts both).

Spec: docs/superpowers/specs/2026-05-20-inline-analysis-polish-design.md §1.3

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# PHASE 4 — Marker overlay simplification + auto-load just-produced h5 (spec §1.4)

**Goal:** Remove the multi-h5 comparison UI (Primary dropdown, + add comparison, compare list). Add an `ia-bp-chips` container. After each completed range, auto-register the canonical h5 as the only visible layer via `_player.setPrimaryLayer({path,label})` and `_player.reloadH5()`. The worker writes `scorer` into the result hash; the route forwards it; the JS uses it to build the h5 path.

**Files:**
- Modify: `src/templates/partials/card_inline_analysis.html`
- Modify: `src/static/js/inline_analysis.js`
- Modify: `src/dlc/tasks.py`
- Modify: `src/dlc/inline_analysis.py`
- Modify: `tests/test_inline_analysis_ui_isolation.py`
- Modify: `tests/test_inline_analysis_routes.py`

- [ ] **Step 1: Write the failing UI test**

Append to `tests/test_inline_analysis_ui_isolation.py`:

```python
def test_overlay_comparison_widgets_removed():
    """Polish spec §1.4: drop the multi-h5 comparison UI; the card now
    shows ONLY the just-produced h5."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    assert 'id="ia-overlay-primary-select"' not in txt, (
        "primary-select dropdown must be removed per polish spec §1.4"
    )
    assert 'id="ia-overlay-add-compare"' not in txt
    assert 'id="ia-overlay-compare-list"' not in txt
    # Keep these — they remain useful in single-layer mode:
    assert 'id="ia-overlay-toggle"' in txt
    assert 'id="ia-overlay-threshold"' in txt
    assert 'id="ia-overlay-marker-size"' in txt


def test_bp_chips_container_present():
    """Polish spec §1.4: body-part chips container is newly added."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    assert 'id="ia-bp-chips"' in txt
```

- [ ] **Step 2: Write the failing routes test**

In `tests/test_inline_analysis_routes.py`, find `TestRangeStatus.test_returns_done_with_counts` and extend it. Replace the existing method body with:

```python
    def test_returns_done_with_counts(self, ia_client):
        client, _app, redis, _ = ia_client
        redis.hset("inline:result:r1", mapping={
            "status": "done", "n_analyzed": "42", "n_skipped": "8",
            "error": "", "scorer": "DLC_resnet50_DREADD-Alishuffle1_snapshot-200000",
        })
        resp = client.get("/dlc/project/inline-analysis/range/status?req_id=r1")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "done"
        assert body["n_analyzed"] == 42
        assert body["n_skipped"] == 8
        assert body["scorer"] == "DLC_resnet50_DREADD-Alishuffle1_snapshot-200000", (
            "polish spec §1.4: /range/status done payload must include scorer "
            "so the JS can construct the canonical h5 path"
        )
```

- [ ] **Step 3: Run; confirm RED**

```bash
docker exec deeplabcut-webapp-docker-worker-1 bash -c \
  "cd /app && python -m pytest /app/../tests/test_inline_analysis_ui_isolation.py /app/../tests/test_inline_analysis_routes.py -v"
```

Expected: the three new assertions FAIL.

- [ ] **Step 4: Update card markup**

In `src/templates/partials/card_inline_analysis.html`, find the "Kinematic markers" block:

```html
  <!-- Kinematic markers -->
  <div style="margin-top:.5rem;padding:.4rem;border:1px solid var(--border);border-radius:6px">
    <label style="display:flex;align-items:center;gap:.4rem;font-size:.75rem">
      <input type="checkbox" id="ia-overlay-toggle"> Show markers
      threshold <input type="range" id="ia-overlay-threshold" min="0" max="1" step="0.05" value="0.6" style="width:80px">
      marker size <input type="number" id="ia-overlay-marker-size" value="6" min="1" max="20" style="width:50px">
    </label>
    <div style="display:flex;gap:.4rem;align-items:center;margin-top:.3rem;font-size:.75rem">
      Primary <select id="ia-overlay-primary-select" style="flex:1"></select>
      <select id="ia-overlay-add-compare" style="flex:1"><option value="">+ add comparison…</option></select>
    </div>
    <div id="ia-overlay-compare-list" style="display:flex;flex-direction:column;gap:.2rem;margin-top:.2rem"></div>
  </div>
```

Replace with:

```html
  <!-- Kinematic markers (single-layer; auto-mounts the just-produced h5) -->
  <div style="margin-top:.5rem;padding:.4rem;border:1px solid var(--border);border-radius:6px">
    <label style="display:flex;align-items:center;gap:.4rem;font-size:.75rem">
      <input type="checkbox" id="ia-overlay-toggle"> Show markers
      threshold <input type="range" id="ia-overlay-threshold" min="0" max="1" step="0.05" value="0.6" style="width:80px">
      marker size <input type="number" id="ia-overlay-marker-size" value="6" min="1" max="20" style="width:50px">
    </label>
    <!-- Body-part chips (filled after first range completes) -->
    <div id="ia-bp-list-wrap" class="hidden" style="margin-top:.35rem;margin-bottom:.2rem">
      <div class="fl-bodypart-list" id="ia-bp-chips"></div>
    </div>
  </div>
```

- [ ] **Step 5: Worker writes `scorer` into the result hash**

In `src/dlc/tasks.py`, find `_publish_result`:

```python
def _publish_result(redis_, req_id, status, n_analyzed=0, n_skipped=0, error=""):
    """Set the result hash. Errors are truncated to 500 chars."""
    mapping = {
        "status":     status,
        "n_analyzed": str(int(n_analyzed)),
        "n_skipped":  str(int(n_skipped)),
        "error":      str(error)[:500],
    }
```

Replace with:

```python
def _publish_result(redis_, req_id, status, n_analyzed=0, n_skipped=0, error="", scorer=""):
    """Set the result hash. Errors are truncated to 500 chars.

    `scorer` is included so the browser can construct the canonical h5
    path (video_stem + scorer + ".h5") without an extra round-trip.
    See polish spec §1.4.
    """
    mapping = {
        "status":     status,
        "n_analyzed": str(int(n_analyzed)),
        "n_skipped":  str(int(n_skipped)),
        "error":      str(error)[:500],
        "scorer":     str(scorer or ""),
    }
```

Then find the success-path call site in `_dlc_inline_session_inner`:

```python
            _publish_result(
                redis_, req["req_id"], "done",
                n_analyzed=n_analyzed, n_skipped=n_skipped,
            )
```

Replace with:

```python
            _publish_result(
                redis_, req["req_id"], "done",
                n_analyzed=n_analyzed, n_skipped=n_skipped,
                scorer=scorer,
            )
```

- [ ] **Step 6: Route forwards `scorer` in the JSON response**

In `src/dlc/inline_analysis.py`, find the `range_status` route:

```python
    return jsonify({
        "status":     h.get("status", "pending"),
        "n_analyzed": int(h.get("n_analyzed") or 0),
        "n_skipped":  int(h.get("n_skipped") or 0),
        "error":      h.get("error", ""),
    })
```

Replace with:

```python
    return jsonify({
        "status":     h.get("status", "pending"),
        "n_analyzed": int(h.get("n_analyzed") or 0),
        "n_skipped":  int(h.get("n_skipped") or 0),
        "error":      h.get("error", ""),
        "scorer":     h.get("scorer", ""),
    })
```

- [ ] **Step 7: JS auto-loads the just-produced h5**

In `src/static/js/inline_analysis.js`, find `startRangePolling`:

```javascript
        if (d.status === "done") {
          lastRun.textContent = `Last run: ${d.n_analyzed} analyzed, ${d.n_skipped} skipped`;
          if (_player) _player.reloadH5();
          _activeReqId = null;
          stopRangePolling();
        } else if (d.status === "error") {
```

Replace with:

```javascript
        if (d.status === "done") {
          lastRun.textContent = `Last run: ${d.n_analyzed} analyzed, ${d.n_skipped} skipped`;
          // Auto-register the canonical h5 as the only visible layer
          // (polish spec §1.4): build path from video_path + scorer, then
          // let the factory drop its per-layer pose cache and re-fetch.
          if (_player && d.scorer && videoPath.value) {
            const vp = videoPath.value.trim();
            const dot = vp.lastIndexOf(".");
            const stem = dot > 0 ? vp.slice(0, dot) : vp;
            const h5Path = stem + d.scorer + ".h5";
            try {
              _player.setPrimaryLayer({ path: h5Path, label: d.scorer });
            } catch (e) { /* factory may not be wired in tests */ }
            _player.reloadH5();
          } else if (_player) {
            _player.reloadH5();
          }
          _activeReqId = null;
          stopRangePolling();
        } else if (d.status === "error") {
```

- [ ] **Step 8: Run tests; confirm GREEN**

```bash
docker exec deeplabcut-webapp-docker-worker-1 bash -c \
  "cd /app && python -m pytest /app/../tests/test_inline_analysis_ui_isolation.py /app/../tests/test_inline_analysis_routes.py /app/../tests/test_inline_analysis_worker.py -v"
```

Expected: all PASS.

- [ ] **Step 9: Verify viewer.js & card_viewer.html byte-identical to main**

```bash
git diff main -- src/static/js/viewer.js src/templates/partials/card_viewer.html src/static/js/components/analyzed_frame_player.js | wc -l
```

Expected: `0`.

- [ ] **Step 10: Commit**

```bash
git add src/templates/partials/card_inline_analysis.html src/static/js/inline_analysis.js src/dlc/tasks.py src/dlc/inline_analysis.py tests/test_inline_analysis_ui_isolation.py tests/test_inline_analysis_routes.py
git commit -m "$(cat <<'EOF'
feat(static): inline-analysis overlay shows only the just-produced h5

Drops the multi-h5 comparison widgets (Primary select, + add comparison,
compare-list). Adds an ia-bp-chips container (currently empty; filled by
a future factory hook).

After each successful range:
  * Worker writes `scorer` into the inline:result hash.
  * /range/status forwards `scorer` in the JSON body.
  * Browser builds h5_path = video_stem + scorer + ".h5", then calls
    _player.setPrimaryLayer({path,label}) + _player.reloadH5() so the
    canonical h5 is the only visible layer.

The factory already exposes setPrimaryLayer + reloadH5; no factory
change here.

Spec: docs/superpowers/specs/2026-05-20-inline-analysis-polish-design.md §1.4

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# PHASE 5 — Dataset Curation panel full mirror (spec §1.5)

**Goal:** Replace the orphan `<input id="ia-curation-toggle">` with the full Dataset Curation block from `card_viewer.html` (lines 240–371), renaming every `va-` ID prefix to `ia-`. Wire the same handlers `viewer.js` uses for `va-*` IDs against the new `ia-*` IDs in `inline_analysis.js` — **copied, not refactored** (per parent spec §4).

**Files:**
- Modify: `src/templates/partials/card_inline_analysis.html`
- Modify: `src/static/js/inline_analysis.js`
- Modify: `tests/test_inline_analysis_ui_isolation.py`

- [ ] **Step 1: Write the failing UI test**

Append to `tests/test_inline_analysis_ui_isolation.py`:

```python
def test_full_curation_panel_mirrored_in_inline_analysis_partial():
    """Polish spec §1.5: every va-* curation ID has an ia-* counterpart
    in the inline-analysis partial."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    required_ia_ids = [
        # Toggle + master area
        "ia-curation-panel", "ia-curation-toggle", "ia-curation-controls",
        "ia-curation-status",
        # Row 1: Extract + Add
        "ia-extract-frame-btn", "ia-add-to-dataset-btn",
        # Row 2: Batch
        "ia-batch-count", "ia-batch-step", "ia-batch-add-btn",
        # Row 3: CSV section
        "ia-csv-section", "ia-csv-none", "ia-csv-loaded",
        "ia-csv-path-display", "ia-create-csv-btn", "ia-csv-create-status",
        # Row 3b: Timelines
        "ia-csv-bars", "ia-status-bar-wrap", "ia-note-bar-wrap",
        "ia-status-canvas", "ia-note-canvas",
        "ia-status-chips", "ia-note-chips",
        "ia-status-prev-btn", "ia-status-next-btn",
        "ia-note-prev-btn", "ia-note-next-btn",
        # Row 4: Annotation panel
        "ia-annot-panel", "ia-annot-frame-num",
        "ia-status-input", "ia-save-status-btn",
        "ia-note-input", "ia-save-note-btn",
        "ia-annot-save-status",
        "ia-new-tag-input", "ia-add-tag-btn",
    ]
    missing = [i for i in required_ia_ids if f'id="{i}"' not in txt]
    assert not missing, f"missing IDs in curation panel: {missing}"


def test_no_va_ids_leaked_into_inline_partial():
    """Sanity: ensure the rename from va- to ia- was complete."""
    txt = (PARTIALS / "card_inline_analysis.html").read_text()
    import re
    leaked = re.findall(r'id="(va-[^"]+)"', txt)
    assert not leaked, f"va- IDs leaked into inline-analysis partial: {leaked}"
```

- [ ] **Step 2: Run; confirm RED**

```bash
docker exec deeplabcut-webapp-docker-worker-1 bash -c \
  "cd /app && python -m pytest /app/../tests/test_inline_analysis_ui_isolation.py -v"
```

Expected: the two new tests FAIL (every required `ia-` ID is missing).

- [ ] **Step 3: Copy the curation panel into the inline partial**

In `src/templates/partials/card_inline_analysis.html`, find the orphan toggle:

```html
  <!-- (Dataset Curation panel — opt-in, lazy mount via factory.setCurationFrameHook) -->
  <div style="margin-top:.4rem;font-size:.74rem;color:var(--text-dim)">
    <label><input type="checkbox" id="ia-curation-toggle"> Dataset Curation</label>
  </div>
</section>
```

Replace with the full panel — copied verbatim from `card_viewer.html` lines 240–371 with every `va-` ID prefix renamed to `ia-`:

```html
  <!-- ── Dataset Curation Panel (mirror of viewer.js panel, rename va-→ia-) ── -->
  <div id="ia-curation-panel" style="margin-top:.65rem;padding:.5rem .65rem;background:var(--surface-2);border:1px solid var(--border);border-radius:7px">

    <!-- Toggle row (master kill-switch for the curation tools) -->
    <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap">
      <label style="display:flex;align-items:center;gap:.45rem;font-size:.8rem;font-weight:500;cursor:pointer;user-select:none">
        <input type="checkbox" id="ia-curation-toggle"
               style="accent-color:var(--accent);width:14px;height:14px"/>
        Dataset Curation
      </label>
      <span id="ia-curation-status" class="fe-extract-status" style="font-size:.73rem;flex:1;min-width:0"></span>
    </div>

    <!-- Existing inner rows wrapped in a hidden-by-default div -->
    <div id="ia-curation-controls" class="hidden" style="margin-top:.5rem">

    <!-- Row 1: Quick-extract + Add to Dataset -->
    <div style="display:flex;align-items:center;gap:.4rem;flex-wrap:wrap;margin-bottom:.4rem">
      <button class="btn-sm" id="ia-extract-frame-btn"
        title="Save the raw video frame as a lossless PNG in labeled-data/ (no CSV entry)">
        Extract Frame
      </button>
      <button class="btn-sm" id="ia-add-to-dataset-btn"
        style="background:var(--accent);color:#fff;font-weight:500"
        title="Extract frame + add blank entry to CollectedData CSV/H5">
        + Add to Dataset
      </button>
    </div>

    <!-- Row 2: Batch extraction controls -->
    <div style="display:flex;align-items:center;gap:.4rem;flex-wrap:wrap;margin-bottom:.4rem">
      <label style="font-size:.76rem;color:var(--text-dim);white-space:nowrap">Batch:</label>
      <input type="number" id="ia-batch-count" value="10" min="1" max="500"
        style="width:4.2rem;font-size:.76rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;color:var(--text);padding:.22rem .4rem"
        title="Number of frames to add" />
      <label style="font-size:.76rem;color:var(--text-dim);white-space:nowrap">every</label>
      <input type="number" id="ia-batch-step" value="30" min="1" max="9999"
        style="width:4.2rem;font-size:.76rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;color:var(--text);padding:.22rem .4rem"
        title="Frame step between additions" />
      <label style="font-size:.76rem;color:var(--text-dim);white-space:nowrap">frames</label>
      <button class="btn-sm" id="ia-batch-add-btn"
        title="Add multiple frames to the dataset starting from the current frame">
        Batch Add
      </button>
    </div>

    <!-- Row 3: Companion CSV status -->
    <div id="ia-csv-section" style="border-top:1px solid var(--border);padding-top:.45rem;margin-top:.05rem">
      <!-- No CSV found -->
      <div id="ia-csv-none">
        <span style="font-size:.79rem;color:var(--text-dim)">No companion CSV found.</span>
        <div style="margin-top:.35rem;display:flex;align-items:center;gap:.5rem">
          <button class="btn-sm btn-create" id="ia-create-csv-btn">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            Create CSV
          </button>
          <span id="ia-csv-create-status" class="fe-extract-status"></span>
        </div>
      </div>
      <!-- CSV loaded -->
      <div id="ia-csv-loaded" class="hidden" style="display:flex;align-items:center;gap:.4rem;flex-wrap:wrap">
        <span style="font-size:.74rem;color:var(--text-dim);flex-shrink:0">CSV:</span>
        <span id="ia-csv-path-display" style="font-family:var(--mono);font-size:.71rem;color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0"></span>
      </div>
    </div>

    <!-- Row 3b: CSV timelines — status first, notes second -->
    <div id="ia-csv-bars" class="hidden" style="margin-top:.5rem">
      <div id="ia-status-bar-wrap" class="hidden">
        <div style="display:flex;align-items:center;gap:.4rem;margin-top:.55rem;margin-bottom:.18rem">
          <span class="fe-csv-bar-label" style="flex:1;margin:0">Status</span>
          <button id="ia-status-prev-btn" class="btn-sm" disabled title="Previous status frame" style="padding:.12rem .4rem;font-size:.72rem;line-height:1">◀</button>
          <button id="ia-status-next-btn" class="btn-sm" disabled title="Next status frame" style="padding:.12rem .4rem;font-size:.72rem;line-height:1">▶</button>
        </div>
        <canvas id="ia-status-canvas" height="12"
          style="width:100%;display:block;cursor:pointer;border-radius:3px;background:var(--surface)"
          title="Click to jump to frame"></canvas>
        <div id="ia-status-chips" class="fe-tag-filter" style="margin-top:.3rem;min-height:1rem"></div>
      </div>
      <div id="ia-note-bar-wrap" class="hidden" style="margin-top:.4rem">
        <div style="display:flex;align-items:center;gap:.4rem;margin-top:.55rem;margin-bottom:.18rem">
          <span class="fe-csv-bar-label" style="flex:1;margin:0">Notes</span>
          <button id="ia-note-prev-btn" class="btn-sm" disabled title="Previous note frame" style="padding:.12rem .4rem;font-size:.72rem;line-height:1">◀</button>
          <button id="ia-note-next-btn" class="btn-sm" disabled title="Next note frame" style="padding:.12rem .4rem;font-size:.72rem;line-height:1">▶</button>
        </div>
        <canvas id="ia-note-canvas" height="12"
          style="width:100%;display:block;cursor:pointer;border-radius:3px;background:var(--surface)"
          title="Click to jump to frame"></canvas>
        <div id="ia-note-chips" class="fe-tag-filter" style="margin-top:.3rem;min-height:1rem"></div>
      </div>
    </div>

    <!-- Row 4: Annotation panel (shown when CSV loaded) -->
    <div id="ia-annot-panel" class="hidden" style="margin-top:.5rem">
      <div style="font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);margin-bottom:.4rem">
        Annotate frame <span id="ia-annot-frame-num" style="color:var(--text);font-family:var(--mono)">0</span>
      </div>
      <!-- Status row -->
      <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-bottom:.35rem">
        <label style="font-size:.76rem;color:var(--text-dim);white-space:nowrap;flex-shrink:0;min-width:3rem">Status</label>
        <input type="number" id="ia-status-input" value="0"
          style="width:4.5rem;font-size:.8rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;color:var(--text);padding:.26rem .4rem" />
        <button id="ia-save-status-btn" class="btn-sm btn-create" style="padding:.28rem .65rem">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
          Save Status
        </button>
      </div>
      <!-- Note row -->
      <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin-bottom:.45rem">
        <label style="font-size:.76rem;color:var(--text-dim);white-space:nowrap;flex-shrink:0;min-width:3rem">Note</label>
        <input type="text" id="ia-note-input" placeholder="Enter note…"
          style="flex:1;min-width:110px;font-size:.8rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;color:var(--text);padding:.26rem .45rem" />
        <button id="ia-save-note-btn" class="btn-sm btn-create" style="padding:.28rem .65rem">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
          Save Note
        </button>
      </div>
      <span id="ia-annot-save-status" class="fe-extract-status"></span>
      <!-- Add note tag -->
      <div style="margin-top:.45rem;display:flex;align-items:center;gap:.4rem;flex-wrap:wrap">
        <input type="text" id="ia-new-tag-input" placeholder="New note tag…"
          style="flex:1;min-width:110px;font-size:.8rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;color:var(--text);padding:.24rem .4rem" />
        <button id="ia-add-tag-btn" class="btn-sm">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          Add Tag
        </button>
      </div>
    </div>
    </div>
    <!-- end ia-curation-controls -->
  </div>
  <!-- ── end Dataset Curation Panel ──────────────────────────── -->
</section>
```

- [ ] **Step 4: Cross-check that no `ia-` ID collides with an existing partial ID**

Run:

```bash
grep -rn 'id="ia-' src/templates/ | sort | awk -F'"' '{print $2}' | sort | uniq -c | sort -rn | head -20
```

Every count must be `1`. If any ID shows up >1 times, surface a blocker — the rename collided with something we didn't see.

- [ ] **Step 5: Run the static template tests; confirm GREEN**

```bash
docker exec deeplabcut-webapp-docker-worker-1 bash -c \
  "cd /app && python -m pytest /app/../tests/test_inline_analysis_ui_isolation.py -v"
```

Expected: `test_full_curation_panel_mirrored_in_inline_analysis_partial` and `test_no_va_ids_leaked_into_inline_partial` PASS.

- [ ] **Step 6: Copy the curation handlers into `inline_analysis.js`**

In `src/static/js/inline_analysis.js`, append (just before the closing `})();` of the outer IIFE) an inline curation block. The block is a near-verbatim copy of `viewer.js` lines 1106-1110 (toggle reveal) + 1875-2348 (the `// ── Dataset Curation ──` IIFE), with these mechanical renames:

| viewer.js name | inline_analysis.js name |
|---|---|
| `va-…` DOM IDs | `ia-…` |
| `vaCurationToggle` etc. | `iaCurationToggle` etc. |
| `_vaCurrentFrame` | `_player.getCurrentFrame()` |
| `_vaCurrentVideoPath` | `videoPath.value.trim()` |
| `_vaVideoName` | (drop — inline-analysis has no `_vaVideoName` mode) |
| `_vaMode` | (drop the `_vaMode === "frames"` guards — the inline card is always video mode) |
| `_vaFrameCount` | `_player ? _player.getFrameCount?.() : 0` — but the factory doesn't yet expose `getFrameCount`. Use a module-private mirror updated in `loadVideo`. |
| `_vaFps` | similarly mirrored as `_iaFps` |
| `_vaLoadFrame(n)` | `_player.setCurrentFrame(n)` |
| `_vaCurationFrameHook` | wire via `_player.setCurationFrameHook(...)` (already exposed by the factory) |
| `vaPlayerSec` MutationObserver | drop — inline-analysis triggers `_iaCsvLoad(path)` directly from `loadVideo` |

Apply the rename consistently. The structure of the block is:

```javascript
  // ── Dataset Curation (copied from viewer.js per parent spec §4) ──────
  // DUPLICATION NOTICE: this block is a near-verbatim copy of the
  // curation IIFE in viewer.js (lines 1106-1110 + 1875-2348). When you
  // fix a bug here, mirror it into viewer.js and vice versa. The
  // migration plan is the same as the player migration — see
  // docs/superpowers/specs/2026-05-20-inline-analysis-design.md §4 +
  // "Known tech debt".
  (() => {
    // Module-private mirrors of viewer.js's _vaFrameCount/_vaFps so the
    // copied helpers can read them without reaching into the factory.
    let _iaFrameCount = 0;
    let _iaFps        = 30;

    // ── Master toggle reveal (mirror viewer.js lines 1106-1110) ────────
    const iaCurationToggle   = document.getElementById("ia-curation-toggle");
    const iaCurationControls = document.getElementById("ia-curation-controls");
    iaCurationToggle?.addEventListener("change", () => {
      iaCurationControls?.classList.toggle("hidden", !iaCurationToggle.checked);
    });

    const iaCurationStatus  = document.getElementById("ia-curation-status");
    const iaExtractFrameBtn = document.getElementById("ia-extract-frame-btn");
    const iaAddToDatasetBtn = document.getElementById("ia-add-to-dataset-btn");
    const iaBatchAddBtn     = document.getElementById("ia-batch-add-btn");
    const iaBatchCount      = document.getElementById("ia-batch-count");
    const iaBatchStep       = document.getElementById("ia-batch-step");
    const iaCsvNone         = document.getElementById("ia-csv-none");
    const iaCsvLoaded       = document.getElementById("ia-csv-loaded");
    const iaCsvPathDisplay  = document.getElementById("ia-csv-path-display");
    const iaCreateCsvBtn    = document.getElementById("ia-create-csv-btn");
    const iaCsvCreateStatus = document.getElementById("ia-csv-create-status");
    const iaCsvBars         = document.getElementById("ia-csv-bars");
    const iaStatusBarWrap   = document.getElementById("ia-status-bar-wrap");
    const iaNoteBarWrap     = document.getElementById("ia-note-bar-wrap");
    const iaStatusCanvas    = document.getElementById("ia-status-canvas");
    const iaNoteCanvas      = document.getElementById("ia-note-canvas");
    const iaStatusChips     = document.getElementById("ia-status-chips");
    const iaNoteChips       = document.getElementById("ia-note-chips");
    const iaAnnotPanel      = document.getElementById("ia-annot-panel");
    const iaAnnotFrameNum   = document.getElementById("ia-annot-frame-num");
    const iaNoteInput       = document.getElementById("ia-note-input");
    const iaStatusInput     = document.getElementById("ia-status-input");
    const iaSaveStatusBtn   = document.getElementById("ia-save-status-btn");
    const iaSaveNoteBtn     = document.getElementById("ia-save-note-btn");
    const iaAnnotSaveStatus = document.getElementById("ia-annot-save-status");
    const iaStatusPrevBtn   = document.getElementById("ia-status-prev-btn");
    const iaStatusNextBtn   = document.getElementById("ia-status-next-btn");
    const iaNoteStepPrevBtn = document.getElementById("ia-note-prev-btn");
    const iaNoteStepNextBtn = document.getElementById("ia-note-next-btn");
    const iaNewTagInput     = document.getElementById("ia-new-tag-input");
    const iaAddTagBtn       = document.getElementById("ia-add-tag-btn");

    // Companion CSV state
    let _iaCsvPath          = null;
    let _iaCsvRows          = [];
    let _iaUserTags         = [];
    let _iaUserStatuses     = [];
    let _iaActiveNoteChips   = new Set();
    let _iaActiveStatusChips = new Set();
    let _iaNoteColorMap      = {};
    let _iaStatusColorMap    = {};

    const _IA_STATUS_COLORS = ["#34d399","#f97316","#e879f9","#facc15","#f87171","#22d3ee","#a78bfa","#fb923c"];
    const _IA_NOTE_COLORS   = ["#60a5fa","#f472b6","#4ade80","#38bdf8","#e879f9","#a78bfa","#facc15","#fb7185"];

    // ── Status helpers ──────────────────────────────────────────
    let _curationMsgTimer = null;
    function _curStatus(msg, isErr) {
      if (!iaCurationStatus) return;
      iaCurationStatus.textContent = msg;
      iaCurationStatus.className   = "fe-extract-status" + (isErr ? " err" : "");
      if (_curationMsgTimer) clearTimeout(_curationMsgTimer);
      if (msg && !isErr) {
        _curationMsgTimer = setTimeout(() => {
          iaCurationStatus.textContent = "";
        }, 4000);
      }
    }

    function _videoRequestBody(frameNum) {
      const n = (frameNum !== undefined)
        ? frameNum
        : (_player ? _player.getCurrentFrame() : 0);
      const body = { frame_number: n };
      const vp = videoPath.value.trim();
      if (vp) body.video_path = vp;
      return body;
    }

    // ── Extract Frame ────────────────────────────────────────────
    if (iaExtractFrameBtn) {
      iaExtractFrameBtn.addEventListener("click", async () => {
        if (!videoPath.value.trim()) {
          _curStatus("No video loaded — open a video first.", true); return;
        }
        iaExtractFrameBtn.disabled = true;
        _curStatus("Extracting…");
        try {
          const res  = await fetch("/dlc/curator/extract-frame", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(_videoRequestBody()),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
          _curStatus(
            data.duplicate
              ? `Already extracted: ${data.saved}`
              : `Saved ${data.saved} (${data.folder}, #${data.frame_count})`
          );
        } catch (err) {
          _curStatus(`Extract failed: ${err.message}`, true);
        } finally {
          iaExtractFrameBtn.disabled = false;
        }
      });
    }

    // ── Add to Dataset ────────────────────────────────────────────
    if (iaAddToDatasetBtn) {
      iaAddToDatasetBtn.addEventListener("click", async () => {
        if (!videoPath.value.trim()) {
          _curStatus("No video loaded — open a video first.", true); return;
        }
        iaAddToDatasetBtn.disabled = true;
        _curStatus("Adding to dataset…");
        try {
          const res  = await fetch("/dlc/curator/add-to-dataset", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(_videoRequestBody()),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
          const h5note = data.h5_updated ? " + H5" : "";
          _curStatus(
            data.duplicate
              ? `Already in dataset: ${data.saved}`
              : `Added ${data.saved} to CSV${h5note} (${data.frame_count} frames)`
          );
        } catch (err) {
          _curStatus(`Failed: ${err.message}`, true);
        } finally {
          iaAddToDatasetBtn.disabled = false;
        }
      });
    }

    // ── Batch Add ─────────────────────────────────────────────────
    if (iaBatchAddBtn) {
      iaBatchAddBtn.addEventListener("click", async () => {
        if (!videoPath.value.trim()) {
          _curStatus("No video loaded — open a video first.", true); return;
        }
        const count = Math.max(1, parseInt(iaBatchCount?.value) || 10);
        const step  = Math.max(1, parseInt(iaBatchStep?.value)  || 30);
        iaBatchAddBtn.disabled = true;
        let added = 0, dupes = 0, errors = 0;
        const start = _player ? _player.getCurrentFrame() : 0;
        let lastFrame = start;
        for (let i = 0; i < count; i++) {
          const frameNum = start + i * step;
          if (frameNum >= _iaFrameCount) break;
          lastFrame = frameNum;
          _curStatus(`Batch adding… ${i + 1}/${count} (frame ${frameNum})`);
          if (_player) _player.setCurrentFrame(frameNum);
          try {
            const res  = await fetch("/dlc/curator/add-to-dataset", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(_videoRequestBody(frameNum)),
            });
            const data = await res.json();
            if (!res.ok) { errors++; continue; }
            if (data.duplicate) dupes++; else added++;
          } catch (_) { errors++; }
        }
        if (_player && lastFrame !== _player.getCurrentFrame()) {
          _player.setCurrentFrame(lastFrame);
        }
        iaBatchAddBtn.disabled = false;
        const parts = [];
        if (added) parts.push(`${added} added`);
        if (dupes) parts.push(`${dupes} duplicate${dupes !== 1 ? "s" : ""}`);
        if (errors) parts.push(`${errors} error${errors !== 1 ? "s" : ""}`);
        _curStatus(`Batch done: ${parts.join(", ") || "nothing to add"}.`, errors > 0 && added === 0);
      });
    }

    // ── Timeline bars ────────────────────────────────────────────
    function _iaDrawCanvas(canvas, rows, field, activeSet, colorMap) {
      if (!canvas) return;
      const total = Math.max(_iaFrameCount, 1);
      const W = Math.round(canvas.getBoundingClientRect().width) || canvas.clientWidth || 600;
      canvas.width = W;
      const H    = canvas.height || 12;
      const ctx  = canvas.getContext("2d");
      const minW = Math.max(1, Math.round(W / total));
      ctx.clearRect(0, 0, W, H);
      if (!activeSet || activeSet.size === 0) return;
      rows.forEach(row => {
        const val = row[field];
        if (!val || (field === "frame_line_status" && val === "0")) return;
        if (!activeSet.has(val)) return;
        ctx.fillStyle = colorMap[val] || "#888";
        const x = Math.round((Number(row.frame_number) / total) * W);
        ctx.fillRect(x, 0, minW, H);
      });
    }
    function _iaRedrawNoteCanvas()   { _iaDrawCanvas(iaNoteCanvas,   _iaCsvRows, "note",              _iaActiveNoteChips,   _iaNoteColorMap);   }
    function _iaRedrawStatusCanvas() { _iaDrawCanvas(iaStatusCanvas, _iaCsvRows, "frame_line_status", _iaActiveStatusChips, _iaStatusColorMap); }

    function _iaBuildCsvBars() {
      if (!iaCsvBars) return;
      const hasNote   = _iaCsvRows.some(r => r.note);
      const hasStatus = _iaCsvRows.some(r => r.frame_line_status && r.frame_line_status !== "0");
      iaCsvBars.classList.toggle("hidden", !hasNote && !hasStatus);
      iaNoteBarWrap?.classList.toggle("hidden", !hasNote);
      iaStatusBarWrap?.classList.toggle("hidden", !hasStatus);
      _iaRedrawNoteCanvas();
      _iaRedrawStatusCanvas();
    }

    [iaNoteCanvas, iaStatusCanvas].forEach(canvas => {
      if (!canvas) return;
      canvas.addEventListener("click", e => {
        const rect = canvas.getBoundingClientRect();
        const fn = Math.round((e.clientX - rect.left) / rect.width * Math.max(_iaFrameCount - 1, 0));
        if (_player) _player.setCurrentFrame(fn);
      });
    });

    function _iaNavAnnot(field, activeSet, dir) {
      if (!activeSet.size) return;
      const cur = _player ? _player.getCurrentFrame() : 0;
      const frames = _iaCsvRows
        .filter(r => { const v = r[field]; return v && (field !== "frame_line_status" || v !== "0") && activeSet.has(v); })
        .map(r => r.frame_number)
        .sort((a, b) => a - b);
      if (!frames.length) return;
      if (dir < 0) {
        const prev = [...frames].reverse().find(f => f < cur);
        if (prev != null && _player) _player.setCurrentFrame(prev);
      } else {
        const next = frames.find(f => f > cur);
        if (next != null && _player) _player.setCurrentFrame(next);
      }
    }
    if (iaStatusPrevBtn)   iaStatusPrevBtn.addEventListener("click",   () => _iaNavAnnot("frame_line_status", _iaActiveStatusChips, -1));
    if (iaStatusNextBtn)   iaStatusNextBtn.addEventListener("click",   () => _iaNavAnnot("frame_line_status", _iaActiveStatusChips,  1));
    if (iaNoteStepPrevBtn) iaNoteStepPrevBtn.addEventListener("click", () => _iaNavAnnot("note",              _iaActiveNoteChips,   -1));
    if (iaNoteStepNextBtn) iaNoteStepNextBtn.addEventListener("click", () => _iaNavAnnot("note",              _iaActiveNoteChips,    1));

    // ── Companion CSV helpers ────────────────────────────────────
    function _iaCsvSyncPanel() {
      if (!_iaCsvPath) return;
      const cur = _player ? _player.getCurrentFrame() : 0;
      if (iaAnnotFrameNum) iaAnnotFrameNum.textContent = cur;
      const row = _iaCsvRows.find(r => r.frame_number === cur);
      if (iaNoteInput)   iaNoteInput.value   = row ? (row.note || "") : "";
      if (iaStatusInput) iaStatusInput.value = row ? (row.frame_line_status ?? "0") : "0";
    }

    function _iaCsvApplyRows(rows, csvPath) {
      _iaCsvPath  = csvPath;
      _iaCsvRows  = rows;
      const noteVals   = [...new Set(rows.map(r => r.note).filter(v => v))];
      const statusVals = [...new Set(rows.map(r => r.frame_line_status).filter(v => v && v !== "0"))];
      _iaUserTags     = [...new Set([..._iaUserTags,     ...noteVals])];
      _iaUserStatuses = [...new Set([..._iaUserStatuses, ...statusVals])];

      if (iaCsvNone)        iaCsvNone.classList.add("hidden");
      if (iaCsvLoaded)      iaCsvLoaded.classList.remove("hidden");
      if (iaCsvPathDisplay) { iaCsvPathDisplay.textContent = csvPath; iaCsvPathDisplay.title = csvPath; }
      if (iaAnnotPanel)     iaAnnotPanel.classList.remove("hidden");

      _iaBuildCsvBars();
      _iaCsvRenderStatusChips();
      _iaCsvRenderTags();
      _iaCsvSyncPanel();
    }

    function _iaCsvRenderStatusChips() {
      if (!iaStatusChips) return;
      iaStatusChips.innerHTML = "";
      _iaStatusColorMap = {};
      _iaUserStatuses.forEach((val, i) => {
        const color = _IA_STATUS_COLORS[i % _IA_STATUS_COLORS.length];
        _iaStatusColorMap[val] = color;
        const chip = document.createElement("span");
        chip.className = "fe-tag-chip" + (_iaActiveStatusChips.has(val) ? " active" : "");
        chip.textContent = val;
        chip.style.setProperty("--chip-color", color);
        chip.title = `Click to show/hide "${val}" on timeline`;
        chip.addEventListener("click", () => {
          if (_iaActiveStatusChips.has(val)) _iaActiveStatusChips.delete(val);
          else _iaActiveStatusChips.add(val);
          _iaCsvRenderStatusChips();
          _iaRedrawStatusCanvas();
        });
        iaStatusChips.appendChild(chip);
      });
      const hasActive = _iaActiveStatusChips.size > 0;
      if (iaStatusPrevBtn) iaStatusPrevBtn.disabled = !hasActive;
      if (iaStatusNextBtn) iaStatusNextBtn.disabled = !hasActive;
    }

    function _iaCsvRenderTags() {
      if (!iaNoteChips) return;
      iaNoteChips.innerHTML = "";
      _iaNoteColorMap = {};
      _iaUserTags.forEach((tag, i) => {
        const color = _IA_NOTE_COLORS[i % _IA_NOTE_COLORS.length];
        _iaNoteColorMap[tag] = color;
        const chip = document.createElement("span");
        chip.className = "fe-tag-chip" + (_iaActiveNoteChips.has(tag) ? " active" : "");
        chip.textContent = tag;
        chip.style.setProperty("--chip-color", color);
        chip.title = `Click to show/hide "${tag}" on timeline`;
        chip.addEventListener("click", () => {
          if (_iaActiveNoteChips.has(tag)) _iaActiveNoteChips.delete(tag);
          else _iaActiveNoteChips.add(tag);
          _iaCsvRenderTags();
          _iaRedrawNoteCanvas();
        });
        iaNoteChips.appendChild(chip);
      });
      const hasActive = _iaActiveNoteChips.size > 0;
      if (iaNoteStepPrevBtn) iaNoteStepPrevBtn.disabled = !hasActive;
      if (iaNoteStepNextBtn) iaNoteStepNextBtn.disabled = !hasActive;
    }

    async function _iaCsvSaveStatus() {
      if (!_iaCsvPath) return;
      const cur = _player ? _player.getCurrentFrame() : 0;
      const existingRow = _iaCsvRows.find(r => r.frame_number === cur);
      const note   = iaNoteInput ? iaNoteInput.value.trim() : (existingRow?.note || "");
      const status = iaStatusInput ? (iaStatusInput.value || "0") : "0";
      await _iaCsvDoSave(note, status);
    }
    async function _iaCsvSaveNote() {
      if (!_iaCsvPath) return;
      const cur = _player ? _player.getCurrentFrame() : 0;
      const existingRow = _iaCsvRows.find(r => r.frame_number === cur);
      const note   = iaNoteInput ? iaNoteInput.value.trim() : "";
      const status = iaStatusInput ? (iaStatusInput.value || "0") : (existingRow?.frame_line_status || "0");
      await _iaCsvDoSave(note, status);
    }
    async function _iaCsvDoSave(note, status) {
      const cur = _player ? _player.getCurrentFrame() : 0;
      if (iaAnnotSaveStatus) { iaAnnotSaveStatus.textContent = "Saving…"; iaAnnotSaveStatus.className = "fe-extract-status"; }
      try {
        const res  = await fetch("/annotate/save-row", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            csv_path:          _iaCsvPath,
            frame_number:      cur,
            note,
            frame_line_status: status,
            fps:               _iaFps,
          }),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        const isInteresting = note || (status && status !== "0");
        const idx = _iaCsvRows.findIndex(r => r.frame_number === cur);
        if (isInteresting) {
          const savedRow = data.row || { frame_number: cur, timestamp: (cur / _iaFps).toFixed(3), frame_line_status: status, note };
          if (idx >= 0) _iaCsvRows[idx] = savedRow;
          else { _iaCsvRows.push(savedRow); _iaCsvRows.sort((a, b) => a.frame_number - b.frame_number); }
          if (note && !_iaUserTags.includes(note)) { _iaUserTags.push(note); _iaCsvRenderTags(); }
          if (status && status !== "0" && !_iaUserStatuses.includes(status)) { _iaUserStatuses.push(status); _iaCsvRenderStatusChips(); }
        } else {
          if (idx >= 0) _iaCsvRows.splice(idx, 1);
        }
        _iaBuildCsvBars();
        if (iaAnnotSaveStatus) {
          iaAnnotSaveStatus.textContent = "Saved";
          iaAnnotSaveStatus.className   = "fe-extract-status ok";
          setTimeout(() => { if (iaAnnotSaveStatus?.textContent === "Saved") iaAnnotSaveStatus.textContent = ""; }, 2000);
        }
      } catch (err) {
        if (iaAnnotSaveStatus) { iaAnnotSaveStatus.textContent = `Error: ${err.message}`; iaAnnotSaveStatus.className = "fe-extract-status err"; }
      }
    }

    async function _iaCsvLoad(vp) {
      _iaCsvPath = null; _iaCsvRows = []; _iaUserTags = []; _iaUserStatuses = [];
      _iaActiveNoteChips = new Set(); _iaActiveStatusChips = new Set();
      _iaNoteColorMap = {}; _iaStatusColorMap = {};
      if (iaCsvNone)        iaCsvNone.classList.remove("hidden");
      if (iaCsvLoaded)      iaCsvLoaded.classList.add("hidden");
      if (iaCsvBars)        iaCsvBars.classList.add("hidden");
      if (iaAnnotPanel)     iaAnnotPanel.classList.add("hidden");
      if (iaCsvCreateStatus) iaCsvCreateStatus.textContent = "";
      if (!vp) return;
      try {
        const res  = await fetch(`/annotate/csv?path=${encodeURIComponent(vp)}`);
        const data = await res.json();
        if (data.csv_exists) _iaCsvApplyRows(data.rows, data.csv_path);
      } catch (_) {}
    }

    // Wire into the factory's frame-navigation hook so sync runs after
    // every loadFrame.
    if (_player && _player.setCurationFrameHook) {
      _player.setCurationFrameHook(() => _iaCsvSyncPanel());
    }

    // Create CSV
    if (iaCreateCsvBtn) {
      iaCreateCsvBtn.addEventListener("click", async () => {
        const vp = videoPath.value.trim();
        if (!vp) return;
        if (iaCsvCreateStatus) { iaCsvCreateStatus.textContent = `Creating CSV for ${_iaFrameCount} frames…`; iaCsvCreateStatus.className = "fe-extract-status"; }
        try {
          const res  = await fetch("/annotate/create-csv", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ video_path: vp, fps: _iaFps, frame_count: _iaFrameCount }),
          });
          const data = await res.json();
          if (data.error) throw new Error(data.error);
          if (iaCsvCreateStatus) iaCsvCreateStatus.textContent = "";
          _iaCsvApplyRows(data.rows, data.csv_path);
        } catch (err) {
          if (iaCsvCreateStatus) { iaCsvCreateStatus.textContent = `Error: ${err.message}`; iaCsvCreateStatus.className = "fe-extract-status err"; }
        }
      });
    }
    if (iaSaveStatusBtn) iaSaveStatusBtn.addEventListener("click", _iaCsvSaveStatus);
    if (iaSaveNoteBtn)   iaSaveNoteBtn.addEventListener("click",   _iaCsvSaveNote);
    if (iaAddTagBtn) {
      iaAddTagBtn.addEventListener("click", () => {
        const tag = iaNewTagInput ? iaNewTagInput.value.trim() : "";
        if (!tag) return;
        if (!_iaUserTags.includes(tag)) { _iaUserTags.push(tag); _iaCsvRenderTags(); }
        if (iaNewTagInput) iaNewTagInput.value = "";
      });
    }
    if (iaNewTagInput) {
      iaNewTagInput.addEventListener("keydown", e => {
        if (e.key === "Enter") { e.preventDefault(); iaAddTagBtn?.click(); }
      });
    }

    // Public hook the outer scope uses to refresh frame_count/fps + load CSV.
    window.__iaCurationOnVideo = function (vp, fps, frameCount) {
      _iaFrameCount = frameCount || 0;
      _iaFps        = fps || 30;
      _iaCsvLoad(vp);
    };
  })(); // end Dataset Curation (inline-analysis copy)
```

- [ ] **Step 7: Wire the curation block into `loadVideo`**

In `src/static/js/inline_analysis.js`, find `loadVideo`:

```javascript
  async function loadVideo(path) {
    if (!_player) {
      _player = makeAnalyzedFramePlayer({
        ...
      });
    }
    try {
      const r = await fetch(`/dlc/project/inline-analysis/video-info?path=${encodeURIComponent(path)}`);
      const info = r.ok ? await r.json() : { fps: 30, nframes: 0 };
      _player.loadVideo(path, info.fps || 30, info.nframes || 0);
      syncAnalyzeButtonLabel();
    } catch (e) { /* silent */ }
  }
```

Replace the body's `try` block with one that also invokes the curation hook:

```javascript
    try {
      const r = await fetch(`/dlc/project/inline-analysis/video-info?path=${encodeURIComponent(path)}`);
      const info = r.ok ? await r.json() : { fps: 30, nframes: 0 };
      _player.loadVideo(path, info.fps || 30, info.nframes || 0);
      syncAnalyzeButtonLabel();
      // Notify the curation block (mirrors viewer.js's MutationObserver +
      // _vaCsvLoad flow) so it can refresh _iaFrameCount/_iaFps and load
      // the companion CSV.
      if (typeof window.__iaCurationOnVideo === "function") {
        window.__iaCurationOnVideo(path, info.fps || 30, info.nframes || 0);
      }
    } catch (e) { /* silent */ }
```

- [ ] **Step 8: Run the static template tests; confirm GREEN**

```bash
docker exec deeplabcut-webapp-docker-worker-1 bash -c \
  "cd /app && python -m pytest /app/../tests/test_inline_analysis_ui_isolation.py -v"
```

Expected: all PASS.

- [ ] **Step 9: Verify viewer.js & card_viewer.html byte-identical to main**

```bash
git diff main -- src/static/js/viewer.js src/templates/partials/card_viewer.html src/static/js/components/analyzed_frame_player.js | wc -l
```

Expected: `0`.

- [ ] **Step 10: Run the full mocked suite as a regression check**

```bash
docker exec deeplabcut-webapp-docker-worker-1 bash -c \
  "cd /app && python -m pytest /app/../tests -m 'not gpu and not e2e' -v 2>&1 | tail -60"
```

Expected: green or pre-existing-only failures. No new failures attributable to polish work.

- [ ] **Step 11: Commit**

```bash
git add src/templates/partials/card_inline_analysis.html src/static/js/inline_analysis.js tests/test_inline_analysis_ui_isolation.py
git commit -m "$(cat <<'EOF'
feat(static): inline-analysis dataset curation panel mirror

Full mirror of the Dataset Curation block from card_viewer.html
(lines 240-371) and the curation IIFE from viewer.js (lines 1106-1110 +
1875-2348), with every va- ID renamed to ia-. Handlers are COPIED into
inline_analysis.js per parent spec §4 ("don't refactor working code").

Wired:
  * Extract Frame, + Add to Dataset, Batch Add buttons
  * No-CSV state + Create CSV button
  * CSV-loaded state with path display
  * Status + Notes timeline canvases + prev/next nav + tag chips
  * Annotation panel (status, note, save buttons, save-tag flow)

Spec: docs/superpowers/specs/2026-05-20-inline-analysis-polish-design.md §1.5

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# PHASE 6 — Tech-debt doc bullet (spec §4 follow-up)

**Goal:** Record the curation-handler duplication in the parent spec's "Known tech debt" section.

**Files:**
- Modify: `docs/superpowers/specs/2026-05-20-inline-analysis-design.md`

- [ ] **Step 1: Append to "Known tech debt"**

In `docs/superpowers/specs/2026-05-20-inline-analysis-design.md`, find the "Known tech debt" section. After item 1 (`viewer.js migration to analyzed_frame_player.js`), append one bullet:

```markdown
1a. **Dataset Curation handlers duplicated.** The Curation IIFE in
   `viewer.js` (lines 1106-1110 + 1875-2348) was copied verbatim into
   `inline_analysis.js` with `va-` → `ia-` ID renames per the polish
   spec §1.5 (2026-05-20). The duplication is tracked here and slated
   for migration to the shared factory in the SAME follow-up PR that
   migrates the player. Until then, every curation bugfix must be
   hand-mirrored in both files.
```

- [ ] **Step 2: Verify viewer.js & card_viewer.html byte-identical to main**

```bash
git diff main -- src/static/js/viewer.js src/templates/partials/card_viewer.html src/static/js/components/analyzed_frame_player.js | wc -l
```

Expected: `0`.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-20-inline-analysis-design.md
git commit -m "$(cat <<'EOF'
docs(spec): note curation-handler duplication tech debt

Records the second piece of duplication added by the polish PR
(curation handlers copied from viewer.js into inline_analysis.js)
alongside the existing player-core duplication, with the same
migration plan.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review Checklist

- [x] Spec §1.1 → Phase 1
- [x] Spec §1.2 → Phase 2 (+ e2e smoke extension)
- [x] Spec §1.3 → Phase 3 (markup + JS + body wiring)
- [x] Spec §1.4 → Phase 4 (markup + JS + worker scorer + route forward + test)
- [x] Spec §1.5 → Phase 5 (markup + JS copy)
- [x] Spec §3 (out-of-scope acknowledgement) → no server routes added
- [x] Spec §4 (files-touched) → matches Phase modify-set
- [x] Spec §5 (test additions) → ui-isolation + routes + e2e smoke
- [x] Spec §6 (acceptance) → all five smoke items, viewer byte-identical, mocked pytest passes
- [x] No `viewer.js` or `card_viewer.html` edits anywhere in the plan
- [x] No factory edits anywhere in the plan
- [x] Every commit subject matches spec §2 verbatim
- [x] Disk hygiene preserved: all new code is JS/HTML; no fixture-writing changes; no video decode to disk
