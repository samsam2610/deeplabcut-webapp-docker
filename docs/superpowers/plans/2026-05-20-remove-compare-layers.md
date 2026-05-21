# Remove Compare-Layer + Per-Layer Threshold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mechanically remove the unused comparison-layer overlay and per-layer-threshold toggle from both pose-overlay cards (View-Analyzed + Inline Analysis) without disturbing primary-layer rendering, the global threshold slider, marker editing, or any other current behaviour.

**Architecture:** Pure deletion. Markup blocks come out of two partials. Named functions + state come out of two JS files (one being a verbatim clone of the other, with `va-` → `ia-`). Every dead call site drops with the symbol. The `_vaLayers` / `_iaLayers` arrays stay — they now wrap a 1-element primary-only collection and existing iteration code continues to work unchanged. Two obsolete test files are deleted. Two static regression-guard tests are added to prevent the dead symbols from being reintroduced.

**Tech Stack:** Vanilla JS modules, Jinja2 partials, pytest with `Path.read_text()` static-source assertions, `node --input-type=module --check` for syntax validation.

**Spec:** `docs/superpowers/specs/2026-05-20-remove-compare-layers-design.md`

**Branch:** Stay on `feat/test-set-picker`. Do NOT push, rebase, or switch.

---

## File Structure

| Path | Action | Notes |
|---|---|---|
| `src/templates/partials/card_viewer.html` | edit | Drop `va-overlay-compare-block` + `va-overlay-customize-thresholds` wrapper divs |
| `src/templates/partials/card_inline_analysis.html` | edit | Same as above with `ia-` prefix |
| `src/static/js/viewer.js` | edit | Drop 9 named functions + 1 state flag + ~13 call-site guards; collapse `_vaLayerThreshold(layer)` → `_vaGlobalThreshold` |
| `src/static/js/inline_analysis_player.js` | edit | Same as viewer.js with `_ia` prefix |
| `tests/e2e_viewer_layers_smoke.py` | delete | Whole file gone |
| `tests/test_viewer_layers_ui_isolation.py` | delete | Whole file gone |
| `tests/test_inline_analysis_ui_isolation.py` | edit | Append compare-absent regression guards |
| `tests/test_view_analyzed_no_compare.py` | create | New regression-guard test for `card_viewer.html` + `viewer.js` |

---

## Task 0: Commit the plan

**Files:**
- Create: `docs/superpowers/plans/2026-05-20-remove-compare-layers.md` (this file)

- [ ] **Step 1: Commit the plan**

```bash
git add docs/superpowers/plans/2026-05-20-remove-compare-layers.md
git commit -m "$(cat <<'EOF'
docs(plan): remove compare-layer + per-layer threshold from both pose cards

Implementation plan corresponding to spec
docs/superpowers/specs/2026-05-20-remove-compare-layers-design.md.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

Expected: clean commit on `feat/test-set-picker`.

---

## Task 1: Delete compare/customize markup in `card_viewer.html`

**Files:**
- Modify: `src/templates/partials/card_viewer.html` lines 105-138

The two blocks to delete are contiguous in the file:
- Lines 105-130 — `<!-- ── Comparison layers ─── -->` block (the `va-overlay-compare-block` div). This wraps the primary-row chip AND the compare list/banner/add-compare select.
- Lines 132-138 — `<!-- ── Customize per layer threshold toggle ─── -->` block.

The primary-row chip (`va-overlay-primary-row`, `va-overlay-primary-visible`, `va-overlay-primary-shape`, `va-overlay-primary-label`) lives inside `va-overlay-compare-block` and goes with it. The JS already guards every reference to these elements with `?.addEventListener` and `if (vaOverlayPrimaryShape)` patterns, so removing them is safe.

The Primary `<select id="va-overlay-primary-select">` lives in a **separate, earlier** block (around lines 85-103) and stays untouched.

- [ ] **Step 1: Verify the surrounding context with Read**

Read lines 95-145 of `src/templates/partials/card_viewer.html`. Confirm:
- Line 103 ends the `<!-- Primary layer / h5 file selector -->` block with `</div>`.
- Line 105 starts `<!-- ── Comparison layers ──── -->`.
- Line 130 closes `</div>` for `va-overlay-compare-block`.
- Line 132 starts `<!-- ── Customize per layer threshold toggle ── -->`.
- Line 138 closes `</div>` for the customize-threshold wrapper.
- Line 140 starts `<!-- Likelihood threshold -->` (this stays).

- [ ] **Step 2: Delete lines 105-138 in one Edit**

Use the Edit tool to replace this exact block (including the blank line at 104 / 131 / 139 — verify with the Read tool first):

```html
            <!-- ── Comparison layers ────────────────────────────────── -->
            <div id="va-overlay-compare-block" style="margin-bottom:.45rem">
              <label style="display:flex;align-items:center;gap:.4rem;font-size:.73rem;color:var(--text-dim);margin-bottom:.25rem">
                Comparison layers
                <select id="va-overlay-add-compare"
                  style="margin-left:auto;font-size:.72rem;padding:.18rem .35rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;color:var(--text)">
                  <option value="">+ add comparison…</option>
                </select>
                <span id="va-overlay-add-compare-empty-hint" class="hidden"
                      style="margin-left:auto;font-size:.7rem;color:var(--text-dim);font-style:italic">
                  no other variants for this video
                </span>
              </label>
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
              <div id="va-overlay-compare-list" style="display:flex;flex-direction:column;gap:.2rem"></div>
              <span id="va-overlay-edit-disabled-banner" class="hidden" style="display:inline-block;margin-top:.25rem;font-size:.72rem;color:var(--text-dim);font-style:italic">⚠ Edit disabled while comparing layers — remove comparisons to edit.</span>
            </div>

            <!-- ── Customize per layer threshold toggle ─────────────── -->
            <div style="margin-bottom:.45rem">
              <label style="display:flex;align-items:center;gap:.4rem;font-size:.73rem;color:var(--text-dim);cursor:pointer">
                <input type="checkbox" id="va-overlay-customize-thresholds" style="accent-color:var(--accent);width:13px;height:13px"/>
                Customize threshold per layer
              </label>
            </div>

```

Replace with an empty string (i.e., delete entirely).

- [ ] **Step 3: Verify no orphan IDs remain**

```bash
grep -nE "(va-overlay-(compare|primary-row|primary-visible|primary-shape|primary-label|add-compare|customize-thresholds|edit-disabled-banner|primary-threshold-slot))" src/templates/partials/card_viewer.html
```
Expected: no output.

```bash
grep -n "va-overlay-primary-select" src/templates/partials/card_viewer.html
```
Expected: still present (this stays).

- [ ] **Step 4: Commit**

```bash
git add src/templates/partials/card_viewer.html
git commit -m "$(cat <<'EOF'
refactor(viewer): drop compare-layer + customize-threshold markup

Removes va-overlay-compare-block (wrapping the comparison-layers UI,
primary-row chip, edit-disabled banner) and the customize-per-layer-
threshold toggle from card_viewer.html. The primary-layer select,
global threshold slider, marker-size slider, body-part chips, and
marker-edit banner all stay.

Refs: docs/superpowers/specs/2026-05-20-remove-compare-layers-design.md §1

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Delete compare/customize markup in `card_inline_analysis.html`

**Files:**
- Modify: `src/templates/partials/card_inline_analysis.html` lines 142-175

This is the same block with `va-` → `ia-`. Same blank-line structure.

- [ ] **Step 1: Verify context with Read**

Read lines 130-180 of `src/templates/partials/card_inline_analysis.html`. Confirm the block runs from line 142 (`<!-- ── Comparison layers ── -->`) through line 175 (closing `</div>` of customize-threshold wrapper).

- [ ] **Step 2: Delete lines 142-175 with Edit**

Match the same pattern as Task 1, but with `ia-` prefix. The full block text:

```html
            <!-- ── Comparison layers ────────────────────────────────── -->
            <div id="ia-overlay-compare-block" style="margin-bottom:.45rem">
              <label style="display:flex;align-items:center;gap:.4rem;font-size:.73rem;color:var(--text-dim);margin-bottom:.25rem">
                Comparison layers
                <select id="ia-overlay-add-compare"
                  style="margin-left:auto;font-size:.72rem;padding:.18rem .35rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;color:var(--text)">
                  <option value="">+ add comparison…</option>
                </select>
                <span id="ia-overlay-add-compare-empty-hint" class="hidden"
                      style="margin-left:auto;font-size:.7rem;color:var(--text-dim);font-style:italic">
                  no other variants for this video
                </span>
              </label>
              <div id="ia-overlay-primary-row"
                   style="display:flex;align-items:center;gap:.35rem;font-size:.74rem;padding:.15rem .25rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;margin-bottom:.25rem">
                <input type="checkbox" id="ia-overlay-primary-visible" checked
                       style="accent-color:var(--accent);width:12px;height:12px;flex-shrink:0"/>
                <span id="ia-overlay-primary-shape"
                      style="font-family:var(--mono);width:1.1rem;text-align:center;flex-shrink:0">●</span>
                <span style="font-size:.7rem;color:var(--text-dim);flex-shrink:0">Primary</span>
                <span id="ia-overlay-primary-label"
                      style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>
              </div>
              <div id="ia-overlay-compare-list" style="display:flex;flex-direction:column;gap:.2rem"></div>
              <span id="ia-overlay-edit-disabled-banner" class="hidden" style="display:inline-block;margin-top:.25rem;font-size:.72rem;color:var(--text-dim);font-style:italic">⚠ Edit disabled while comparing layers — remove comparisons to edit.</span>
            </div>

            <!-- ── Customize per layer threshold toggle ─────────────── -->
            <div style="margin-bottom:.45rem">
              <label style="display:flex;align-items:center;gap:.4rem;font-size:.73rem;color:var(--text-dim);cursor:pointer">
                <input type="checkbox" id="ia-overlay-customize-thresholds" style="accent-color:var(--accent);width:13px;height:13px"/>
                Customize threshold per layer
              </label>
            </div>

```

Replace with an empty string.

- [ ] **Step 3: Verify no orphan IDs**

```bash
grep -nE "(ia-overlay-(compare|primary-row|primary-visible|primary-shape|primary-label|add-compare|customize-thresholds|edit-disabled-banner|primary-threshold-slot))" src/templates/partials/card_inline_analysis.html
```
Expected: no output.

```bash
grep -n "ia-overlay-primary-select" src/templates/partials/card_inline_analysis.html
```
Expected: still present.

- [ ] **Step 4: Commit**

```bash
git add src/templates/partials/card_inline_analysis.html
git commit -m "$(cat <<'EOF'
refactor(inline-analysis): drop compare-layer + customize-threshold markup

Mirror of card_viewer.html change with ia- prefix. Removes the
compare-layers block (wrapping UI, primary-row chip, edit-disabled
banner) and the customize-per-layer-threshold toggle.

Refs: docs/superpowers/specs/2026-05-20-remove-compare-layers-design.md §1

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Delete compare/customize JS in `viewer.js`

**Files:**
- Modify: `src/static/js/viewer.js` (multiple line ranges; see steps)

This is the substantive deletion task. Work in **discrete, locally-scoped Edits** — do NOT try to do the whole file in one Edit. After each Edit, re-grep to confirm the deletion stuck.

### 3a — State + helper functions (top of module)

- [ ] **Step 1: Delete `_vaPerLayerThresholds`, `_vaCompare`, `_vaIsEditable`, `_vaLayerThreshold` declarations (lines 87-96)**

Use Edit to replace this exact block in `src/static/js/viewer.js`:

```javascript
    const _vaLayers = [];
    let   _vaGlobalThreshold    = 0.60;
    let   _vaPerLayerThresholds = false;

    function _vaPrimary()     { return _vaLayers[0] || null; }
    function _vaCompare()     { return _vaLayers.slice(1); }
    function _vaIsEditable()  { return _vaLayers.length === 1; }
    function _vaLayerThreshold(layer) {
      return _vaPerLayerThresholds && layer.threshold != null
        ? layer.threshold
        : _vaGlobalThreshold;
    }
```

With:

```javascript
    const _vaLayers = [];
    let   _vaGlobalThreshold    = 0.60;

    function _vaPrimary()     { return _vaLayers[0] || null; }
```

Rationale: Drop the per-layer flag, the compare getter, the editability getter, and the per-layer threshold accessor. `_vaPrimary` is kept (used widely).

- [ ] **Step 2: Verify**

```bash
grep -nE "_vaPerLayerThresholds|_vaCompare\b|_vaIsEditable|_vaLayerThreshold" src/static/js/viewer.js
```
Expected: only call-site references remain — they'll be removed in later steps.

### 3b — Remove `if (!_vaIsEditable()) return;` guards and the early-return in `_vaUpdateEditBanner`

The guards are clustered. Read lines 722-736 first to see the `_vaUpdateEditBanner` early-return.

- [ ] **Step 1: Drop the early-return in `_vaUpdateEditBanner` (lines 722-728)**

Edit replace:

```javascript
    function _vaUpdateEditBanner() {
      if (!vaMarkerEditBanner) return;
      // Force-hide while comparison layers are active — editing is disabled.
      if (!_vaIsEditable()) {
        vaMarkerEditBanner.classList.add("hidden");
        return;
      }
      const n = _vaEditCount();
```

With:

```javascript
    function _vaUpdateEditBanner() {
      if (!vaMarkerEditBanner) return;
      const n = _vaEditCount();
```

- [ ] **Step 2: Drop the 10 `if (!_vaIsEditable()) return;` guards at the canvas/edit call sites**

These appear at viewer.js lines 770, 784, 825, 840, 858, 874, 909, 925, 959, 990 (all with the same trailing comment `// edit disabled while compare layers active`).

Run `grep -n "if (!_vaIsEditable())" src/static/js/viewer.js` first to confirm the exact line numbers (they may shift slightly after Step 1).

For each occurrence, use Edit with `replace_all: true` on this single line:

Old:
```javascript
      if (!_vaIsEditable()) return;     // edit disabled while compare layers active
```

New: (empty — delete the line entirely)

Two of the 10 are indented at 6 spaces (`      if (...)`); the other 8 are at 8 spaces (`        if (...)`). Run two separate `replace_all` Edits — one for each indent level. Check `grep -nE '^      if \(!_vaIsEditable' src/static/js/viewer.js` and `grep -nE '^        if \(!_vaIsEditable' src/static/js/viewer.js` to count each before / after.

- [ ] **Step 3: Verify no `_vaIsEditable` references remain**

```bash
grep -n "_vaIsEditable" src/static/js/viewer.js
```
Expected: no output.

### 3c — Inline `_vaLayerThreshold(layer)` → `_vaGlobalThreshold`

There are 4 call sites (around viewer.js lines 1007, 1018, 1084 before edits — line numbers shift; re-grep). Each looks like `_vaLayerThreshold(layer)`.

- [ ] **Step 1: Find and replace**

```bash
grep -n "_vaLayerThreshold" src/static/js/viewer.js
```
Expected (sample, line numbers may differ):
- One inside `_vaPoseCacheKey`: `return \`${layer.path}:${_vaLayerThreshold(layer).toFixed(2)}\`;`
- One inside `_vaFetchPosesForFrame`: `threshold: _vaLayerThreshold(layer).toFixed(2),`
- One inside `_vaPrefetchOne`: `threshold: _vaLayerThreshold(layer).toFixed(2),`

Use Edit with `replace_all: true`:

Old: `_vaLayerThreshold(layer)`
New: `_vaGlobalThreshold`

- [ ] **Step 2: Verify**

```bash
grep -n "_vaLayerThreshold" src/static/js/viewer.js
```
Expected: no output.

### 3d — Delete the comparison-layer functions

These are the larger deletions. Re-grep current line numbers after 3a-3c:

```bash
grep -n "function _vaRefreshAddComparisonOptions\|function _vaRenderPrimaryThresholdInline\|function _vaRenderCompareRows\|async function _vaAddCompare\|function _vaRemoveCompare\|function _vaUpdateEditDisabledBanner" src/static/js/viewer.js
```

- [ ] **Step 1: Delete `_vaRefreshAddComparisonOptions(variants)`**

Edit to remove the entire function body (originally viewer.js:1303-1322). Block to delete:

```javascript
    function _vaRefreshAddComparisonOptions(variants) {
      const addCmp = document.getElementById("va-overlay-add-compare");
      const hint   = document.getElementById("va-overlay-add-compare-empty-hint");
      if (!addCmp) return;
      addCmp.innerHTML = '<option value="">+ add comparison…</option>';
      const taken = new Set(_vaLayers.map(l => l.path));
      const available = (variants || []).filter(v => !v.disabled && !taken.has(v.path));
      available.forEach((v) => {
        const opt = document.createElement("option");
        opt.value = v.path;
        opt.textContent = v.label;
        opt.dataset.type  = v.type;
        opt.dataset.label = v.label;
        addCmp.appendChild(opt);
      });
      // Show the dropdown only when at least one non-taken option exists;
      // otherwise show the inline "(no other variants)" hint.
      addCmp.classList.toggle("hidden", available.length === 0);
      if (hint) hint.classList.toggle("hidden", available.length > 0);
    }

```

Replace with empty string.

- [ ] **Step 2: Remove the two call sites of `_vaRefreshAddComparisonOptions`**

```bash
grep -n "_vaRefreshAddComparisonOptions" src/static/js/viewer.js
```

Expected remaining references (after 3d-1):
- Inside `_vaDiscoverVariants` (was line 1300): `_vaRefreshAddComparisonOptions(data.variants);` — delete this line.
- Inside `_vaApplyPrimaryFromSelect` (was line 1341): `_vaRefreshAddComparisonOptions(_vaLastVariants);` — delete this line.

Use Edit twice (one per call site). Sample:

Old:
```javascript
      select.value = defaultEntry.path;
      await _vaApplyPrimaryFromSelect();
      _vaSyncPrimaryRow();
      _vaRefreshAddComparisonOptions(data.variants);
    }
```

New:
```javascript
      select.value = defaultEntry.path;
      await _vaApplyPrimaryFromSelect();
      _vaSyncPrimaryRow();
    }
```

For the second:

Old (inside `_vaApplyPrimaryFromSelect`):
```javascript
      await _vaLoadLayerInfo(layer);
      await _vaLoadEditCacheForPrimary();
      _vaRenderCompareRows();
      _vaRefreshAddComparisonOptions(_vaLastVariants);
      _vaRenderPrimaryThresholdInline();
      if (_vaOverlayEnabled) _vaLoadFrame(_vaCurrentFrame);
      _vaSyncPrimaryRow();
    }
```

New:
```javascript
      await _vaLoadLayerInfo(layer);
      await _vaLoadEditCacheForPrimary();
      if (_vaOverlayEnabled) _vaLoadFrame(_vaCurrentFrame);
      _vaSyncPrimaryRow();
    }
```

- [ ] **Step 3: Delete `_vaRenderPrimaryThresholdInline()`**

Edit to delete this entire function (originally viewer.js:1347-1380):

```javascript
    function _vaRenderPrimaryThresholdInline() {
      const host = document.getElementById("va-overlay-primary-select");
      if (!host) return;
      let slot = document.getElementById("va-overlay-primary-threshold-slot");
      if (!_vaPerLayerThresholds) {
        if (slot) slot.remove();
        return;
      }
      if (!slot) {
        slot = document.createElement("span");
        slot.id = "va-overlay-primary-threshold-slot";
        slot.style.cssText = "display:flex;align-items:center;gap:.25rem;margin-left:.4rem";
        host.parentElement?.appendChild(slot);
      }
      slot.innerHTML = "";
      const layer = _vaPrimary();
      if (!layer) return;
      const slider = document.createElement("input");
      slider.type = "range"; slider.min = "0"; slider.max = "1"; slider.step = "0.05";
      slider.value = String(layer.threshold ?? _vaGlobalThreshold);
      slider.style.cssText = "width:60px;accent-color:var(--accent)";
      const lbl = document.createElement("span");
      lbl.style.cssText = "font-family:var(--mono);font-size:.7rem;min-width:2.2rem";
      lbl.textContent = Number(slider.value).toFixed(2);
      slider.addEventListener("input", () => {
        layer.threshold = Number(slider.value);
        lbl.textContent = layer.threshold.toFixed(2);
        if (_vaOverlayEnabled) {
          _vaFetchPosesForFrame(layer, _vaCurrentFrame).then(_vaDrawCurrentFrame);
        }
      });
      slot.appendChild(slider);
      slot.appendChild(lbl);
    }

```

Replace with empty string.

- [ ] **Step 4: Delete `_vaRenderCompareRows()`, `_vaAddCompare()`, `_vaRemoveCompare()`, `_vaUpdateEditDisabledBanner()`, and the `// ── Comparison-row UI ───` header**

Re-grep:

```bash
grep -n "// ── Comparison-row UI\|function _shapeGlyph\|function _vaRenderCompareRows\|async function _vaAddCompare\|function _vaRemoveCompare\|function _vaUpdateEditDisabledBanner" src/static/js/viewer.js
```

**Important:** `_shapeGlyph` is used elsewhere — keep it! Check:

```bash
grep -n "_shapeGlyph" src/static/js/viewer.js
```

It's referenced in `_vaSyncPrimaryRow()` at the bottom. But `_vaSyncPrimaryRow` itself only writes to `vaOverlayPrimaryShape` / `vaOverlayPrimaryLabel` / `vaOverlayPrimaryVisible` — those DOM elements were deleted in Task 1. So `_vaSyncPrimaryRow` is now also dead and will be removed in step 3e below.

Delete the **whole comparison-row section** as one block, from the `// ── Comparison-row UI ───` header through the end of `_vaUpdateEditDisabledBanner`:

```javascript
    // ── Comparison-row UI ──────────────────────────────────────────
    function _shapeGlyph(shape) {
      switch (shape) {
        case "circle-filled": return "●";
        case "diamond":       return "◆";
        case "square":        return "□";
        case "triangle":      return "△";
        default:              return "?";
      }
    }

    function _vaRenderCompareRows() {
      const list = document.getElementById("va-overlay-compare-list");
      if (!list) return;
      list.innerHTML = "";
      _vaCompare().forEach((layer) => {
        const row = document.createElement("div");
        row.id = `va-layer-row-${layer.id}`;
        row.style.cssText = "display:flex;align-items:center;gap:.35rem;font-size:.74rem;padding:.15rem .25rem;background:var(--surface);border:1px solid var(--border);border-radius:5px";
        // visibility checkbox
        const vis = document.createElement("input");
        vis.type = "checkbox";
        vis.checked = layer.visible;
        vis.style.cssText = "accent-color:var(--accent);width:12px;height:12px;flex-shrink:0";
        vis.addEventListener("change", () => {
          layer.visible = vis.checked;
          _vaDrawCurrentFrame();
        });
        row.appendChild(vis);
        // shape badge
        const badge = document.createElement("span");
        badge.textContent = _shapeGlyph(layer.shape);
        badge.style.cssText = "font-family:var(--mono);width:1.1rem;text-align:center;flex-shrink:0";
        row.appendChild(badge);
        // label
        const lbl = document.createElement("span");
        lbl.textContent = layer.label;
        lbl.style.cssText = "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
        row.appendChild(lbl);
        // per-layer threshold (rendered conditionally when Customize is on)
        const thrSlot = document.createElement("span");
        thrSlot.dataset.role = "threshold";
        thrSlot.style.cssText = "display:flex;align-items:center;gap:.25rem;flex-shrink:0";
        if (_vaPerLayerThresholds) {
          const slider = document.createElement("input");
          slider.type = "range"; slider.min = "0"; slider.max = "1"; slider.step = "0.05";
          slider.value = String(layer.threshold ?? _vaGlobalThreshold);
          slider.style.cssText = "width:60px;accent-color:var(--accent)";
          const lbl = document.createElement("span");
          lbl.style.cssText = "font-family:var(--mono);font-size:.7rem;min-width:2.2rem";
          lbl.textContent = Number(slider.value).toFixed(2);
          slider.addEventListener("input", () => {
            layer.threshold = Number(slider.value);
            lbl.textContent = layer.threshold.toFixed(2);
            if (_vaOverlayEnabled) {
              _vaFetchPosesForFrame(layer, _vaCurrentFrame).then(_vaDrawCurrentFrame);
            }
          });
          thrSlot.appendChild(slider);
          thrSlot.appendChild(lbl);
        }
        row.appendChild(thrSlot);
        // remove button
        const rm = document.createElement("button");
        rm.className = "btn-sm";
        rm.style.cssText = "padding:.05rem .35rem;font-size:.7rem;flex-shrink:0";
        rm.textContent = "×";
        rm.title = "Remove this comparison layer";
        rm.addEventListener("click", () => _vaRemoveCompare(layer.id));
        row.appendChild(rm);
        list.appendChild(row);
      });
      _vaUpdateEditDisabledBanner();
    }

    async function _vaAddCompare(path, label, type) {
      if (_vaLayers.some(l => l.path === path)) return;
      const layer = _vaMakeLayer({ path, label, type });
      _vaLayers.push(layer);
      _vaAssignShapes();
      await _vaLoadLayerInfo(layer);
      // Pre-fetch poses for the current frame so the new layer paints immediately.
      if (_vaOverlayEnabled) await _vaFetchPosesForFrame(layer, _vaCurrentFrame);
      _vaRenderCompareRows();
      _vaRefreshAddComparisonOptions(_vaLastVariants);
      _vaDrawCurrentFrame();
    }

    function _vaRemoveCompare(id) {
      const idx = _vaLayers.findIndex(l => l.id === id);
      if (idx < 1) return;  // never remove primary
      _vaLayers.splice(idx, 1);
      _vaAssignShapes();
      _vaRenderCompareRows();
      _vaRefreshAddComparisonOptions(_vaLastVariants);
      _vaDrawCurrentFrame();
    }

    function _vaUpdateEditDisabledBanner() {
      const banner = document.getElementById("va-overlay-edit-disabled-banner");
      if (banner) banner.classList.toggle("hidden", _vaIsEditable());
      // Re-evaluate the marker-edit banner: when compare layers are active it
      // must be force-hidden regardless of unsaved-edit count.
      _vaUpdateEditBanner();
    }

```

Replace with empty string.

- [ ] **Step 5: Verify**

```bash
grep -nE "_vaCompare\b|_vaAddCompare|_vaRemoveCompare|_vaRenderCompareRows|_vaRefreshAddComparisonOptions|_vaRenderPrimaryThresholdInline|_vaUpdateEditDisabledBanner|_shapeGlyph" src/static/js/viewer.js
```
Expected: only `_vaSyncPrimaryRow`'s reference to `_shapeGlyph` remains (it will go in step 3e).

### 3e — Drop the now-dead `_vaSyncPrimaryRow`, primary-row element lookups, add-compare listener, customize-threshold listener

- [ ] **Step 1: Re-grep current locations**

```bash
grep -n "vaOverlayPrimaryVisible\|vaOverlayPrimaryShape\|vaOverlayPrimaryLabel\|_vaSyncPrimaryRow\|vaOverlayAddCompare\|vaCustomizeThr\|va-overlay-add-compare\|va-overlay-customize-thresholds" src/static/js/viewer.js
```

- [ ] **Step 2: Delete the `vaOverlayAddCompare` lookup + listener (was viewer.js:1505-1512)**

Edit to remove:

```javascript
    const vaOverlayAddCompare = document.getElementById("va-overlay-add-compare");
    vaOverlayAddCompare?.addEventListener("change", async (e) => {
      const path = e.target.value;
      if (!path) return;
      const opt  = e.target.options[e.target.selectedIndex];
      await _vaAddCompare(path, opt.dataset.label, opt.dataset.type);
      e.target.value = "";  // reset to placeholder
    });

```

Replace with empty string.

- [ ] **Step 3: Delete `vaOverlayPrimary*` lookups + `_vaSyncPrimaryRow` + the `change` listener (was viewer.js:1514-1536)**

Edit to remove:

```javascript
    const vaOverlayPrimaryVisible = document.getElementById("va-overlay-primary-visible");
    const vaOverlayPrimaryShape   = document.getElementById("va-overlay-primary-shape");
    const vaOverlayPrimaryLabel   = document.getElementById("va-overlay-primary-label");

    vaOverlayPrimaryVisible?.addEventListener("change", () => {
      const layer = _vaPrimary();
      if (!layer) return;
      layer.visible = !!vaOverlayPrimaryVisible.checked;
      _vaDrawCurrentFrame();
    });

    function _vaSyncPrimaryRow() {
      const layer = _vaPrimary();
      if (!layer) {
        if (vaOverlayPrimaryShape) vaOverlayPrimaryShape.textContent = "—";
        if (vaOverlayPrimaryLabel) vaOverlayPrimaryLabel.textContent = "(no primary)";
        if (vaOverlayPrimaryVisible) vaOverlayPrimaryVisible.checked = false;
        return;
      }
      if (vaOverlayPrimaryShape) vaOverlayPrimaryShape.textContent = _shapeGlyph(layer.shape);
      if (vaOverlayPrimaryLabel) vaOverlayPrimaryLabel.textContent = layer.label || "";
      if (vaOverlayPrimaryVisible) vaOverlayPrimaryVisible.checked = !!layer.visible;
    }

```

Replace with empty string.

- [ ] **Step 4: Remove remaining `_vaSyncPrimaryRow();` call sites**

Re-grep:

```bash
grep -n "_vaSyncPrimaryRow" src/static/js/viewer.js
```

Expected: two remaining call sites — one inside `_vaDiscoverVariants` (after the await `_vaApplyPrimaryFromSelect()` line), one at the end of `_vaApplyPrimaryFromSelect`. Delete both lines.

Use Edit with `replace_all: true` on the literal line:

Old: `      _vaSyncPrimaryRow();`
New: (empty — delete the line)

- [ ] **Step 5: Delete the customize-threshold listener (was viewer.js:1564-1578)**

Edit to remove:

```javascript
    // Customize per-layer thresholds toggle
    const vaCustomizeThr = document.getElementById("va-overlay-customize-thresholds");
    vaCustomizeThr?.addEventListener("change", () => {
      _vaPerLayerThresholds = vaCustomizeThr.checked;
      if (!_vaPerLayerThresholds) {
        // Forget per-layer overrides; revert to global.
        _vaLayers.forEach(l => l.threshold = null);
      } else {
        // Seed each layer's override with the current global so toggling on
        // produces no immediate visual change.
        _vaLayers.forEach(l => l.threshold = _vaGlobalThreshold);
      }
      _vaRenderCompareRows();
      _vaRenderPrimaryThresholdInline();
      if (_vaOverlayEnabled) _vaLoadFrame(_vaCurrentFrame);
    });

```

Replace with empty string.

- [ ] **Step 6: Final sweep — verify nothing compare-related remains**

```bash
grep -nE "_vaPerLayerThresholds|_vaCompare\b|_vaIsEditable|_vaLayerThreshold|_vaRenderCompareRows|_vaAddCompare|_vaRemoveCompare|_vaRefreshAddComparisonOptions|_vaRenderPrimaryThresholdInline|_vaUpdateEditDisabledBanner|_vaSyncPrimaryRow|_shapeGlyph|vaOverlayPrimaryVisible|vaOverlayPrimaryShape|vaOverlayPrimaryLabel|vaOverlayAddCompare|vaCustomizeThr|va-overlay-compare|va-overlay-add-compare|va-overlay-customize-thresholds|va-overlay-primary-threshold-slot|va-overlay-primary-row|va-overlay-primary-visible|va-overlay-primary-shape|va-overlay-primary-label|va-overlay-edit-disabled-banner|va-overlay-add-compare-empty-hint" src/static/js/viewer.js
```
Expected: no output.

- [ ] **Step 7: Syntax-check viewer.js**

```bash
node --input-type=module --check < src/static/js/viewer.js
```
Expected: no output, exit code 0.

If it fails, read the error message and fix the dangling syntax (most likely a stray blank line or orphan semicolon — read the surrounding context).

- [ ] **Step 8: Commit**

```bash
git add src/static/js/viewer.js
git commit -m "$(cat <<'EOF'
refactor(viewer): drop compare-layer + per-layer threshold JS

Removes the dead comparison-layer code path from viewer.js:
- Functions: _vaCompare, _vaIsEditable, _vaLayerThreshold,
  _vaRefreshAddComparisonOptions, _vaRenderPrimaryThresholdInline,
  _vaRenderCompareRows, _vaAddCompare, _vaRemoveCompare,
  _vaUpdateEditDisabledBanner, _vaSyncPrimaryRow, _shapeGlyph.
- State: _vaPerLayerThresholds flag.
- All call sites of the above + every "if (!_vaIsEditable()) return;"
  guard at canvas/edit handlers.
- DOM lookups for va-overlay-{primary-row,primary-visible,primary-shape,
  primary-label,add-compare,customize-thresholds} now that the markup
  is gone.

_vaLayerThreshold(layer) collapses to _vaGlobalThreshold at every call.
The _vaLayers array stays — it now wraps a single primary layer and
existing forEach/filter/some calls work unchanged.

Primary-layer selection, global threshold slider, marker-size slider,
body-part chips, marker edit + Save/Discard/Clear, frame navigation,
pose endpoints, and Dataset Curation are untouched.

Refs: docs/superpowers/specs/2026-05-20-remove-compare-layers-design.md §1

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Delete compare/customize JS in `inline_analysis_player.js`

Same as Task 3 with `_va` → `_ia` and `va-overlay-*` → `ia-overlay-*`. Line numbers are identical (the file is a verbatim clone).

**Files:**
- Modify: `src/static/js/inline_analysis_player.js`

- [ ] **Step 1: Repeat Task 3a (state + helpers, lines 87-96)**

Old:
```javascript
    const _iaLayers = [];
    let   _iaGlobalThreshold    = 0.60;
    let   _iaPerLayerThresholds = false;

    function _iaPrimary()     { return _iaLayers[0] || null; }
    function _iaCompare()     { return _iaLayers.slice(1); }
    function _iaIsEditable()  { return _iaLayers.length === 1; }
    function _iaLayerThreshold(layer) {
      return _iaPerLayerThresholds && layer.threshold != null
        ? layer.threshold
        : _iaGlobalThreshold;
    }
```

New:
```javascript
    const _iaLayers = [];
    let   _iaGlobalThreshold    = 0.60;

    function _iaPrimary()     { return _iaLayers[0] || null; }
```

- [ ] **Step 2: Repeat Task 3b (`_iaUpdateEditBanner` early-return + 10 guard lines)**

Drop the early-return inside `_iaUpdateEditBanner`:

Old:
```javascript
    function _iaUpdateEditBanner() {
      if (!iaMarkerEditBanner) return;
      // Force-hide while comparison layers are active — editing is disabled.
      if (!_iaIsEditable()) {
        iaMarkerEditBanner.classList.add("hidden");
        return;
      }
      const n = _iaEditCount();
```

New:
```javascript
    function _iaUpdateEditBanner() {
      if (!iaMarkerEditBanner) return;
      const n = _iaEditCount();
```

Then two `replace_all` Edits to drop all `if (!_iaIsEditable()) return;` guard lines (6-space and 8-space indents).

Verify: `grep -n "_iaIsEditable" src/static/js/inline_analysis_player.js` returns nothing.

- [ ] **Step 3: Repeat Task 3c — inline `_iaLayerThreshold(layer)` → `_iaGlobalThreshold`**

Edit with `replace_all: true`:
- Old: `_iaLayerThreshold(layer)`
- New: `_iaGlobalThreshold`

Verify: `grep -n "_iaLayerThreshold" src/static/js/inline_analysis_player.js` returns nothing.

- [ ] **Step 4: Repeat Task 3d — delete the 6 comparison-layer functions + the `// ── Comparison-row UI ───` header section**

Mirror the same block-delete logic as Task 3d, with `_ia` / `ia-overlay-` substitutions.

Verify:
```bash
grep -nE "_iaCompare\b|_iaAddCompare|_iaRemoveCompare|_iaRenderCompareRows|_iaRefreshAddComparisonOptions|_iaRenderPrimaryThresholdInline|_iaUpdateEditDisabledBanner|_shapeGlyph" src/static/js/inline_analysis_player.js
```
Expected: only `_iaSyncPrimaryRow`'s reference remains; it'll be removed next step.

- [ ] **Step 5: Repeat Task 3e — delete `iaOverlayPrimary*`, `iaOverlayAddCompare`, `iaCustomizeThr`, `_iaSyncPrimaryRow` and its call sites**

Mirror Task 3e exactly with `_ia` prefix.

- [ ] **Step 6: Final sweep**

```bash
grep -nE "_iaPerLayerThresholds|_iaCompare\b|_iaIsEditable|_iaLayerThreshold|_iaRenderCompareRows|_iaAddCompare|_iaRemoveCompare|_iaRefreshAddComparisonOptions|_iaRenderPrimaryThresholdInline|_iaUpdateEditDisabledBanner|_iaSyncPrimaryRow|_shapeGlyph|iaOverlayPrimaryVisible|iaOverlayPrimaryShape|iaOverlayPrimaryLabel|iaOverlayAddCompare|iaCustomizeThr|ia-overlay-compare|ia-overlay-add-compare|ia-overlay-customize-thresholds|ia-overlay-primary-threshold-slot|ia-overlay-primary-row|ia-overlay-primary-visible|ia-overlay-primary-shape|ia-overlay-primary-label|ia-overlay-edit-disabled-banner|ia-overlay-add-compare-empty-hint" src/static/js/inline_analysis_player.js
```
Expected: no output.

- [ ] **Step 7: Syntax-check inline_analysis_player.js**

```bash
node --input-type=module --check < src/static/js/inline_analysis_player.js
```
Expected: no output, exit code 0.

- [ ] **Step 8: Commit**

```bash
git add src/static/js/inline_analysis_player.js
git commit -m "$(cat <<'EOF'
refactor(inline-analysis): drop compare-layer + per-layer threshold JS

Mirror of viewer.js change in the cloned inline_analysis_player.js.
Removes the same set of functions, state, call-site guards, and DOM
lookups with the ia- prefix. The cloned player remains a verbatim
clone of viewer.js — the test_cloned_player_is_viewer_js_with_prefix_
renamed guard in test_inline_analysis_ui_isolation.py still passes.

Refs: docs/superpowers/specs/2026-05-20-remove-compare-layers-design.md §1

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Delete obsolete test files

**Files:**
- Delete: `tests/e2e_viewer_layers_smoke.py`
- Delete: `tests/test_viewer_layers_ui_isolation.py`

- [ ] **Step 1: Delete both files**

```bash
git rm tests/e2e_viewer_layers_smoke.py tests/test_viewer_layers_ui_isolation.py
```

Expected: both files removed from the index.

- [ ] **Step 2: Commit**

```bash
git commit -m "$(cat <<'EOF'
test(viewer): delete obsolete compare-layer smoke + ui-isolation tests

These tests asserted the presence of va-overlay-compare-block,
va-overlay-add-compare, va-overlay-customize-thresholds, and related
DOM ids and JS symbols — all of which were removed in the preceding
markup and JS deletions.

Refs: docs/superpowers/specs/2026-05-20-remove-compare-layers-design.md §1

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Add compare-absent regression tests

### 6a — Extend `tests/test_inline_analysis_ui_isolation.py`

**Files:**
- Modify: `tests/test_inline_analysis_ui_isolation.py`

- [ ] **Step 1: Append regression-guard tests at end of file**

Use Edit to append new test functions after the last existing function (`test_hide_no_h5_unchecked_by_default`):

Old (last lines of file):
```python
    assert " checked" not in tag, (
        "ia-browse-hide-no-h5 must default UNCHECKED (opposite of View-Analyzed)"
    )
```

New:
```python
    assert " checked" not in tag, (
        "ia-browse-hide-no-h5 must default UNCHECKED (opposite of View-Analyzed)"
    )


# ─── compare-layer + per-layer-threshold regression guards ───────────


_FORBIDDEN_HTML_FRAGMENTS = (
    "ia-overlay-compare-block",
    "ia-overlay-add-compare",
    "ia-overlay-add-compare-empty-hint",
    "ia-overlay-compare-list",
    "ia-overlay-edit-disabled-banner",
    "ia-overlay-customize-thresholds",
    "ia-overlay-primary-threshold-slot",
    "ia-overlay-primary-row",
    "ia-overlay-primary-visible",
    "ia-overlay-primary-shape",
    "ia-overlay-primary-label",
    "Comparison layers",
    "Customize threshold per layer",
)


_FORBIDDEN_JS_SYMBOLS = (
    "_iaCompare(",
    "_iaIsEditable",
    "_iaPerLayerThresholds",
    "_iaLayerThreshold",
    "_iaRenderCompareRows",
    "_iaAddCompare",
    "_iaRemoveCompare",
    "_iaRefreshAddComparisonOptions",
    "_iaRenderPrimaryThresholdInline",
    "_iaUpdateEditDisabledBanner",
    "_iaSyncPrimaryRow",
)


def test_card_partial_has_no_compare_layer_markup():
    """Compare-layer + customize-threshold DOM ids must NOT reappear in
    the inline-analysis partial. See
    docs/superpowers/specs/2026-05-20-remove-compare-layers-design.md.
    """
    html = CARD.read_text()
    for frag in _FORBIDDEN_HTML_FRAGMENTS:
        assert frag not in html, (
            f"forbidden compare-layer markup reintroduced: {frag!r}"
        )


def test_player_js_has_no_compare_layer_symbols():
    """Compare-layer JS functions + per-layer-threshold state must NOT
    reappear in inline_analysis_player.js.
    """
    src = PLAYER_JS.read_text()
    for sym in _FORBIDDEN_JS_SYMBOLS:
        assert sym not in src, (
            f"forbidden compare-layer symbol reintroduced: {sym!r}"
        )


def test_player_js_uses_global_threshold_directly():
    """After collapse of _iaLayerThreshold(layer), every pose-fetch
    URL builder must read _iaGlobalThreshold directly. No per-layer
    threshold getter calls remain.
    """
    src = PLAYER_JS.read_text()
    assert "_iaGlobalThreshold" in src, (
        "_iaGlobalThreshold must still drive the threshold query parameter"
    )
    # Sanity: the three pose-cache / fetch / prefetch builders should all
    # reference _iaGlobalThreshold rather than the removed helper.
    assert src.count("_iaGlobalThreshold") >= 4, (
        "expected >=4 uses of _iaGlobalThreshold (state init + 3 builders); "
        "if fewer, the helper-collapse step probably missed a call site"
    )
```

- [ ] **Step 2: Run extended tests**

```bash
python -m pytest tests/test_inline_analysis_ui_isolation.py -v
```
Expected: all existing tests + 3 new tests PASS.

### 6b — Create `tests/test_view_analyzed_no_compare.py`

**Files:**
- Create: `tests/test_view_analyzed_no_compare.py`

- [ ] **Step 1: Write the file**

```python
"""Regression guards for the View-Analyzed card after compare-layer removal.

Mirrors tests/test_inline_analysis_ui_isolation.py's compare-absent
section but for src/templates/partials/card_viewer.html and
src/static/js/viewer.js.

See docs/superpowers/specs/2026-05-20-remove-compare-layers-design.md.
"""
from __future__ import annotations

from pathlib import Path

ROOT        = Path(__file__).resolve().parents[1]
PARTIALS    = ROOT / "src" / "templates" / "partials"
CARD        = PARTIALS / "card_viewer.html"
VIEWER_JS   = ROOT / "src" / "static" / "js" / "viewer.js"


_FORBIDDEN_HTML_FRAGMENTS = (
    "va-overlay-compare-block",
    "va-overlay-add-compare",
    "va-overlay-add-compare-empty-hint",
    "va-overlay-compare-list",
    "va-overlay-edit-disabled-banner",
    "va-overlay-customize-thresholds",
    "va-overlay-primary-threshold-slot",
    "va-overlay-primary-row",
    "va-overlay-primary-visible",
    "va-overlay-primary-shape",
    "va-overlay-primary-label",
    "Comparison layers",
    "Customize threshold per layer",
)


_FORBIDDEN_JS_SYMBOLS = (
    "_vaCompare(",
    "_vaIsEditable",
    "_vaPerLayerThresholds",
    "_vaLayerThreshold",
    "_vaRenderCompareRows",
    "_vaAddCompare",
    "_vaRemoveCompare",
    "_vaRefreshAddComparisonOptions",
    "_vaRenderPrimaryThresholdInline",
    "_vaUpdateEditDisabledBanner",
    "_vaSyncPrimaryRow",
)


# ─── markup invariants ───────────────────────────────────────────────


def test_card_partial_has_no_compare_layer_markup():
    html = CARD.read_text()
    for frag in _FORBIDDEN_HTML_FRAGMENTS:
        assert frag not in html, (
            f"forbidden compare-layer markup reintroduced: {frag!r}"
        )


def test_card_partial_keeps_primary_select_and_threshold():
    """Primary-layer dropdown + global threshold slider + marker-size +
    body-part chips MUST stay — these are the surfaces the user still
    relies on after compare removal.
    """
    html = CARD.read_text()
    for needed in (
        'id="va-overlay-primary-select"',
        'id="va-overlay-h5-path"',
        'id="va-overlay-h5-browse"',
        'id="va-overlay-threshold"',
        'id="va-overlay-marker-size"',
        'id="va-bp-chips"',
        'id="va-marker-edit-banner"',
    ):
        assert needed in html, f"required surface missing from card_viewer: {needed!r}"


# ─── JS invariants ───────────────────────────────────────────────────


def test_viewer_js_has_no_compare_layer_symbols():
    src = VIEWER_JS.read_text()
    for sym in _FORBIDDEN_JS_SYMBOLS:
        assert sym not in src, (
            f"forbidden compare-layer symbol reintroduced: {sym!r}"
        )


def test_viewer_js_uses_global_threshold_directly():
    src = VIEWER_JS.read_text()
    assert "_vaGlobalThreshold" in src
    # state init + 3 builders (_vaPoseCacheKey, _vaFetchPosesForFrame, _vaPrefetchOne)
    assert src.count("_vaGlobalThreshold") >= 4, (
        "expected >=4 uses of _vaGlobalThreshold; if fewer, the "
        "_vaLayerThreshold->_vaGlobalThreshold collapse missed a call site"
    )


def test_viewer_js_keeps_primary_apply_path():
    """_vaApplyPrimaryFromSelect must still:
      - clear _vaLayers and push a fresh primary
      - load layer info + edit cache
      - trigger _vaLoadFrame when overlay enabled
    """
    src = VIEWER_JS.read_text()
    assert "_vaApplyPrimaryFromSelect" in src
    assert "_vaLayers.length = 0" in src
    assert "_vaSetPrimaryLayer" in src
    assert "_vaLoadLayerInfo" in src
    assert "_vaLoadEditCacheForPrimary" in src


def test_viewer_js_keeps_marker_edit_save_path():
    """Save/Discard/Clear-Frame editing surfaces stay wired up."""
    src = VIEWER_JS.read_text()
    assert "va-save-adjustments-btn" in src
    assert "va-discard-adjustments-btn" in src
    assert "va-clear-frame-btn" in src
    assert "/dlc/viewer/save-marker-edits" in src
```

- [ ] **Step 2: Run the new test**

```bash
python -m pytest tests/test_view_analyzed_no_compare.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 3: Commit both test additions together**

```bash
git add tests/test_inline_analysis_ui_isolation.py tests/test_view_analyzed_no_compare.py
git commit -m "$(cat <<'EOF'
test(viewer,inline-analysis): regression guards for compare-layer removal

Adds three new tests to tests/test_inline_analysis_ui_isolation.py
asserting compare-layer DOM ids and JS symbols stay gone from
card_inline_analysis.html + inline_analysis_player.js.

Creates tests/test_view_analyzed_no_compare.py with the same shape
for card_viewer.html + viewer.js, plus positive assertions that the
primary-select, global-threshold slider, marker-edit Save/Discard/
Clear-Frame surfaces, and the _vaApplyPrimaryFromSelect path remain
wired up.

Refs: docs/superpowers/specs/2026-05-20-remove-compare-layers-design.md §1, §2

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Final verification

- [ ] **Step 1: Both JS files syntactically valid**

```bash
node --input-type=module --check < src/static/js/viewer.js
node --input-type=module --check < src/static/js/inline_analysis_player.js
```
Expected: no output for either.

- [ ] **Step 2: All targeted tests pass**

```bash
python -m pytest tests/test_inline_analysis_ui_isolation.py tests/test_view_analyzed_no_compare.py -v
```
Expected: ~15 passed (12 existing inline-analysis + 3 new compare-absent guards in that file + 6 new tests in test_view_analyzed_no_compare.py = 21 total). All PASS.

- [ ] **Step 3: Live Playwright smoke against running flask**

Pre-flight: ensure the user's running webapp is at `http://localhost:5000/?token=deeplabcut`.

Set the active DLC project first via curl:

```bash
curl -X POST http://localhost:5000/dlc/project \
     -H "Content-Type: application/json" \
     -d '{"path":"/user-data/NAS-Data-Share/Motor-Learning/DLC-Projects/DREADD-Ali-2026-01-07"}'
```
Expected: 200 OK.

**View-Analyzed smoke** (Playwright pseudo-script, executed via the MCP playwright tool or `pytest` Playwright fixture if one exists):

1. Navigate to `http://localhost:5000/?token=deeplabcut`.
2. Listen for `console` events; assert no `error`-level entries occur.
3. Open View-Analyzed card → Browse Folders → navigate to `/user-data/Parra-Data/Cloud/Reaching-Task-Data/RatBox Videos/tdcs/050726/` → click the AVI row.
4. Wait for `#va-overlay-canvas` to receive paint.
5. Count `nonTransparent` canvas pixels via a `page.evaluate` like:

   ```js
   const c = document.getElementById("va-overlay-canvas");
   const ctx = c.getContext("2d");
   const data = ctx.getImageData(0,0,c.width,c.height).data;
   let n = 0;
   for (let i = 3; i < data.length; i += 4) if (data[i] > 0) n++;
   return n;
   ```
   Expected: `n > 100`.

**Inline Analysis smoke**:

1. Open the Inline Analysis card.
2. Pick the same `050726/khoai-lang-1*.avi` video.
3. Compute `slider = round(24015 / (total - 1) * 1000)` (where `total` is the video frame count from `/dlc/viewer/h5-info` or the seek-slider's `data-frames` attribute).
4. Drive `#ia-seek` to that value and dispatch an `input` event.
5. Set `#ia-frames-per-click=10`, `#ia-keep-warm-seconds=15`.
6. Click `#ia-btn-analyze-range`.
7. Poll `#ia-last-run-status` until it contains `Last run:` (success indicator).
8. Re-evaluate the canvas pixel count as above. Expected: `n > 100`.
9. Throughout, listen for browser `console` errors and assert none occurred.

**STOP-AND-REPORT trigger:** If `n == 0` after analysis completes on the Inline Analysis card, that indicates we regressed the marker render path. Commit WIP and surface immediately rather than papering over it.

- [ ] **Step 4: No regressions in adjacent test suites (optional defence-in-depth)**

```bash
python -m pytest tests/test_dlc_viewer_routes.py tests/test_inline_analysis_routes.py tests/test_inline_analysis_session_lifecycle.py tests/test_inline_analysis_worker.py tests/test_analyzed_marker_adjustment.py tests/test_video_viewer_backend.py tests/test_unified_viewer_backend.py -v
```
Expected: PASS (server-side untouched per spec §1 / §5).

- [ ] **Step 5: Git stat summary**

```bash
git log --oneline feat/test-set-picker --since=today
git diff --stat 301f5df..HEAD -- src/templates/partials/card_viewer.html src/templates/partials/card_inline_analysis.html src/static/js/viewer.js src/static/js/inline_analysis_player.js tests/
```
Confirm the net deletion volume roughly matches spec §7 (~100-200 lines per JS file + ~30 markup lines per partial + 2 test deletions, plus the new compare-absent guard tests).

---

## Self-Review

**Spec coverage check:**

- §1 markup deletions → Tasks 1, 2.
- §1 JS function/state deletions → Tasks 3a-3e, 4 (full mirror).
- §1 `_vaLayerThreshold` → `_vaGlobalThreshold` collapse → Task 3c, 4-Step3.
- §1 every `if (!_vaIsEditable()) return;` dropped → Task 3b, 4-Step2.
- §1 `_vaLayers` array preserved → preserved by construction (only the helpers around it change; existing iteration patterns continue to work for a 1-element array).
- §1 test deletions → Task 5.
- §1 add regression guards → Task 6a (extend inline-analysis), 6b (create view-analyzed).
- §2 invariants → Task 6b explicitly asserts primary-select, global threshold, marker-size, body-part chips, marker-edit Save/Discard/Clear surfaces remain.
- §3 cleanup strategy → matched by the order of subtasks in Tasks 3 and 4.
- §4 verification (node check + pytest + Playwright smoke + curl) → Task 7.
- §6 files-touched table → matches the File Structure section above.
- §7 acceptance criteria → covered by Task 6 (grep guards) + Task 7 (Playwright smoke + diff stat).

**Placeholder scan:** No "TBD", no "implement later", no "add appropriate error handling". Every Edit shows the exact text to delete and the exact replacement. Every test step shows the expected pass output.

**Type consistency:** Function names referenced in Task 6 (`_vaApplyPrimaryFromSelect`, `_vaSetPrimaryLayer`, `_vaLoadLayerInfo`, `_vaLoadEditCacheForPrimary`, `_vaGlobalThreshold`) all match the survival list implied by Tasks 3a-3e (those are never deleted). The `_FORBIDDEN_JS_SYMBOLS` tuples match the exact set deleted in Tasks 3 and 4. Pre-tense: `_vaCompare(` includes the opening paren to avoid matching `_vaCompareXxxNew` accidentally (defensive).
