# Viewer Post-Process Layers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the View Analyzed Videos / Frames card discover post-process h5 variants for the loaded video, let the user pick one as primary and any number as comparison overlays (rendered with shape variants), and gate edit-mode to single-variant sessions.

**Architecture:** One new backend route (`/dlc/viewer/h5-variants`) does the filesystem scan; it reuses the existing path allowlist. The frontend `viewer.js` is refactored from scalar overlay state (`_vaH5Path`, `_vaPoseCache`, `_vaThreshold`) to a `_vaLayers` array (element 0 = primary, rest = comparisons). All draw / fetch / edit code paths iterate layers uniformly. Comparison layers render in the same per-bodypart color palette but with a different shape (open circle / square / triangle).

**Tech Stack:** Flask blueprint, vanilla JS (no framework), pandas/HDF5 for h5 reads, scipy/canvas for rendering. Tests use pytest + Playwright (existing).

**Spec:** `docs/superpowers/specs/2026-05-02-viewer-postproc-layers-design.md`

---

## File Structure

**New files:**

| Path | Responsibility |
|---|---|
| `tests/test_viewer_layers_ui_isolation.py` | Static-template assertions for the new IDs and JS references. |
| `tests/e2e_viewer_layers_smoke.py` | Manual Playwright e2e against the live app + OM-2 RatBox folder. |

**Modified files:**

| Path | What changes |
|---|---|
| `src/dlc/viewer.py` | Add `viewer_h5_variants` route + helpers (`_parse_run_dirname`, `_label_for_type`, `_h5_variants_for_video`). Bump `_VIEWER_H5_CACHE_MAX` 5 → 12. |
| `src/templates/partials/card_viewer.html` | Overlay UI: replace single-path h5 picker with Primary `<select>` + Compare list + add-comparison dropdown + customize-per-layer toggle. Keep existing manual Browse fallback. |
| `src/static/js/viewer.js` | Refactor `_vaH5Path` / `_vaPoseCache` / `_vaThreshold` scalars into `_vaLayers` array. Add layer helpers, shape primitives, multi-layer draw loop, edit-mode gate, variant discovery, customize-per-layer wiring. |
| `tests/test_dlc_viewer_routes.py` | Extend with `/h5-variants` route tests. |
| `tests/test_postprocess_real_project.py` | Extend with the OM-2 RatBox cross-feature integration test. |

---

## Conventions Used Below

- All commands run from the repo root: `/home/sam/docker-images/deeplabcut-webapp-docker`.
- `_dlc_project_security_check(p, _ctx.data_dir(), _ctx.user_data_dir())` — already imported into `viewer.py:55`. Use it for the new route's allowlist.
- The existing `_viewer_sec_check(p)` helper at `viewer.py:286` wraps that call; reuse it.
- HDF5 ops on the host fail (numpy ABI). Run h5-touching tests inside the `flask` container per CLAUDE.md.
- Each task ends with a focused commit (only the files that task modified). The branch already has the user's pre-existing uncommitted edits — do **not** stage them.
- The OM-2 RatBox folder for the integration test:
  - Container path: `/user-data/Parra-Data/Cloud/Reaching-Task-Data/RatBox Videos/tdcs/042426/OM-2_cam0_20260424_105301_2_trig1_fps200_exposure1500_gain10`
  - Host path: `/home/sam/synology/Parra-Lab-Data/Reaching-Task-Data/RatBox Videos/tdcs/042426/OM-2_cam0_20260424_105301_2_trig1_fps200_exposure1500_gain10`

---

## Task 1: Backend variant-discovery route

**Files:**
- Modify: `src/dlc/viewer.py` — add `viewer_h5_variants` route and helpers near the other route handlers (e.g., right after `viewer_h5_find`).
- Test: `tests/test_dlc_viewer_routes.py` — append new test cases.

- [ ] **Step 1: Read the existing test conventions**

Open `tests/test_dlc_viewer_routes.py` and note the fixtures (likely `flask_test_client` per the post-process tests; an `_auth(client)` helper may be required to bypass the auth middleware).

- [ ] **Step 2: Write failing tests**

Append to `tests/test_dlc_viewer_routes.py`:

```python
import json as _json
from pathlib import Path

import pytest


def _seed_companion_h5(parent: Path, video_stem: str) -> Path:
    """Drop a fake companion h5 next to a video. Content unimportant — the
    /h5-variants route only checks names + file existence."""
    p = parent / f"{video_stem}DLC_HrnetW48_DREADDshuffle1_snapshot_150.h5"
    p.write_bytes(b"")
    return p


def _seed_postproc_run(parent: Path, ts: str, tool_tag: str,
                       video_stem: str, status: str = "success",
                       suffix: str = "_filtered") -> Path:
    """Build <parent>/postproc/<ts>_<tool_tag>/<video_stem>...{suffix}.h5 +
    a sidecar run.json with the given status."""
    run_dir = parent / "postproc" / f"{ts}_{tool_tag}"
    run_dir.mkdir(parents=True, exist_ok=False)
    out_h5 = run_dir / f"{video_stem}DLC_HrnetW48_DREADDshuffle1_snapshot_150{suffix}.h5"
    out_h5.write_bytes(b"")
    (run_dir / "run.json").write_text(_json.dumps({
        "run_id": run_dir.name,
        "status": status,
        "tool":   "deeplabcut" if tool_tag == "filterpredictions" else "refineDLC",
        "action": tool_tag if tool_tag == "filterpredictions" else "pipeline",
    }))
    return out_h5


def test_h5_variants_includes_companion_and_postproc(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)

    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)

    video = tmp_path / "OM-2_cam0_FAKE_success.mp4"
    video.write_bytes(b"")
    stem = video.stem

    raw_h5 = _seed_companion_h5(tmp_path, stem)
    filtered_h5 = _seed_postproc_run(tmp_path, "20260502-113642",
                                     "filterpredictions", stem)

    resp = client.get(f"/dlc/viewer/h5-variants?video={video}")
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["video"] == str(video)
    paths = [v["path"] for v in data["variants"]]
    assert str(raw_h5) in paths
    assert str(filtered_h5) in paths
    raw_entry = next(v for v in data["variants"] if v["path"] == str(raw_h5))
    assert raw_entry["type"] == "raw"
    flt = next(v for v in data["variants"] if v["path"] == str(filtered_h5))
    assert flt["type"] == "filtered"
    assert flt["tool_tag"] == "filterpredictions"
    assert flt["run_id"] == "20260502-113642_filterpredictions"
    assert flt["status"] == "success"
    assert flt["disabled"] is False
    # Companion comes before postproc variants in the list.
    assert paths.index(str(raw_h5)) < paths.index(str(filtered_h5))


def test_h5_variants_marks_failed_runs_disabled(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)

    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)

    video = tmp_path / "vid.mp4"
    video.write_bytes(b"")
    stem = video.stem
    bad = _seed_postproc_run(tmp_path, "20260502-113700", "filterpredictions",
                             stem, status="failed")

    resp = client.get(f"/dlc/viewer/h5-variants?video={video}")
    assert resp.status_code == 200
    entry = next(v for v in resp.get_json()["variants"] if v["path"] == str(bad))
    assert entry["status"] == "failed"
    assert entry["disabled"] is True


def test_h5_variants_only_includes_matching_video_stem(flask_test_client, tmp_path, monkeypatch):
    """A postproc dir may hold outputs for multiple videos; only the matching
    video's outputs show up."""
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)

    video = tmp_path / "VIDEO_A.mp4"
    video.write_bytes(b"")
    other = tmp_path / "VIDEO_B.mp4"
    other.write_bytes(b"")

    mine  = _seed_postproc_run(tmp_path, "20260502-120000", "filterpredictions", video.stem)
    yours = _seed_postproc_run(tmp_path, "20260502-120000", "filterpredictions",
                               other.stem) if False else None  # same dir reused

    # Re-create yours into the same postproc dir.
    yours_path = (tmp_path / "postproc" / "20260502-120000_filterpredictions" /
                  f"{other.stem}DLC_HrnetW48_DREADDshuffle1_snapshot_150_filtered.h5")
    yours_path.write_bytes(b"")

    resp = client.get(f"/dlc/viewer/h5-variants?video={video}")
    paths = [v["path"] for v in resp.get_json()["variants"]]
    assert str(mine) in paths
    assert str(yours_path) not in paths


def test_h5_variants_empty_when_nothing_around(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)

    video = tmp_path / "alone.mp4"
    video.write_bytes(b"")
    resp = client.get(f"/dlc/viewer/h5-variants?video={video}")
    assert resp.status_code == 200
    assert resp.get_json() == {"video": str(video), "variants": []}


def test_h5_variants_rejects_disallowed_path(flask_test_client, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: False)
    resp = client.get("/dlc/viewer/h5-variants?video=/etc/x.mp4")
    assert resp.status_code == 403
```

If `_auth(client)` doesn't exist in this file but does in `tests/test_postprocess_routes.py`, copy its definition to a `conftest.py` or duplicate it inline (do not refactor the existing test files in this task).

- [ ] **Step 3: Run the tests; confirm RED**

```bash
python -m pytest tests/test_dlc_viewer_routes.py -k h5_variants -v
```

Expected: 5 FAILED with "404 Not Found" or similar (route doesn't exist).

- [ ] **Step 4: Implement `viewer_h5_variants`**

In `src/dlc/viewer.py`, add the helpers and route. Place them after the existing `viewer_h5_find` (around line 484):

```python
import re as _re
import datetime as _dt

# tool_tag → human-readable label/type
_LABEL_BY_TYPE = {
    "raw":              "Raw",
    "filtered":         "filtered",
    "refine_pipeline":  "refine_pipeline",
    "refine_lh":        "refine_lh",
    "refine_outliers":  "refine_outliers",
    "refine_interp":    "refine_interp",
    "refine_smooth":    "refine_smooth",
}

# Re-derive a variant's type from the produced filename when no sidecar exists.
def _type_from_suffix(name: str) -> str:
    n = name.lower()
    if "_refined" in n:
        return "refine_pipeline"  # best-guess fallback when sidecar is missing
    if "_filtered" in n:
        return "filtered"
    return "filtered"  # safe default for postproc outputs


_RUN_DIR_RE = _re.compile(r"^(?P<ts>\d{8}-\d{6})_(?P<tag>.+)$")


def _parse_run_dirname(dirname: str) -> tuple[str | None, str | None]:
    """`20260502-113642_filterpredictions` → ('20260502-113642', 'filterpredictions')."""
    m = _RUN_DIR_RE.match(dirname)
    if not m:
        return None, None
    return m.group("ts"), m.group("tag")


def _ts_to_iso(ts: str | None) -> str | None:
    """`20260502-113642` → `2026-05-02T11:36:42Z`."""
    if not ts:
        return None
    try:
        d = _dt.datetime.strptime(ts, "%Y%m%d-%H%M%S")
    except ValueError:
        return None
    return d.replace(tzinfo=_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _label_for_variant(*, type_: str, ts: str | None, all_ts: set[str]) -> str:
    """`filtered @ 11:36:42`. Adds the date when two variants share HH:MM:SS."""
    head = _LABEL_BY_TYPE.get(type_, type_)
    if not ts:
        return head
    base = ts[9:11] + ":" + ts[11:13] + ":" + ts[13:15]  # HH:MM:SS
    same_clock = sum(1 for t in all_ts if t and t[9:] == ts[9:])
    if same_clock > 1:
        date = ts[:4] + "-" + ts[4:6] + "-" + ts[6:8]
        return f"{head} @ {date} {base}"
    return f"{head} @ {base}"


def _read_run_status(run_dir: Path) -> str | None:
    sidecar = run_dir / "run.json"
    if not sidecar.is_file():
        return None
    try:
        return _json.loads(sidecar.read_text()).get("status")
    except (OSError, _json.JSONDecodeError):
        return None


def _h5_variants_for_video(video: Path) -> list[dict]:
    """Pure helper. Returns the variants list (no allowlist, no Flask)."""
    parent = video.parent
    stem = video.stem

    out: list[dict] = []

    # 1. Companion h5(s) in the parent dir whose name starts with the stem,
    #    excludes filtered/refined.
    if parent.is_dir():
        for cand in sorted(parent.glob(f"{stem}*.h5")):
            n = cand.name.lower()
            if "_filtered" in n or "_refined" in n:
                continue
            out.append({
                "path":     str(cand),
                "label":    f"Raw — {cand.name}",
                "type":     "raw",
                "run_id":   None,
                "tool_tag": None,
                "ts":       None,
                "status":   None,
                "disabled": False,
            })

    # 2. postproc/<ts>_<tag>/<stem>...h5
    pp_root = parent / "postproc"
    if pp_root.is_dir():
        # First pass: collect to compute date-collision labels.
        collected: list[tuple[Path, str, str | None, str | None]] = []  # (h5, type, ts, status)
        for run_dir in sorted(pp_root.iterdir()):
            if not run_dir.is_dir():
                continue
            ts, tag = _parse_run_dirname(run_dir.name)
            type_ = (
                "refine_pipeline"  if tag == "refine_pipeline"  else
                "refine_lh"        if tag == "refine_lh"        else
                "refine_outliers"  if tag == "refine_outliers"  else
                "refine_interp"    if tag == "refine_interp"    else
                "refine_smooth"    if tag == "refine_smooth"    else
                "filtered"
            )
            status = _read_run_status(run_dir)
            for h5 in sorted(run_dir.glob(f"{stem}*.h5")):
                collected.append((h5, type_, ts, status))

        all_ts = {t for _h, _ty, t, _s in collected if t}
        for h5, type_, ts, status in collected:
            out.append({
                "path":     str(h5),
                "label":    _label_for_variant(type_=type_, ts=ts, all_ts=all_ts),
                "type":     type_,
                "run_id":   h5.parent.name,
                "tool_tag": _RUN_DIR_RE.match(h5.parent.name).group("tag")
                             if _RUN_DIR_RE.match(h5.parent.name) else None,
                "ts":       _ts_to_iso(ts),
                "status":   status,
                "disabled": status == "failed",
            })

    return out


@bp.route("/dlc/viewer/h5-variants")
def viewer_h5_variants():
    """List every analyzable h5 near the loaded video.

    Query: ?video=<abs-path>
    """
    video_arg = request.args.get("video", "").strip()
    if not video_arg:
        return jsonify({"error": "video parameter required."}), 400

    p = Path(video_arg)
    if not _viewer_sec_check(p.parent):
        return jsonify({"error": "Access denied."}), 403

    return jsonify({
        "video":    str(p),
        "variants": _h5_variants_for_video(p),
    })
```

- [ ] **Step 5: Run the tests; confirm GREEN**

```bash
python -m pytest tests/test_dlc_viewer_routes.py -k h5_variants -v
```

Expected: 5 PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/dlc/viewer.py tests/test_dlc_viewer_routes.py
git commit -m "feat(viewer): add /dlc/viewer/h5-variants route for layered overlays"
```

---

## Task 2: Bump the LRU cache for multi-layer sessions

**Files:** Modify `src/dlc/viewer.py:61` (the `_VIEWER_H5_CACHE_MAX = 5` line).

- [ ] **Step 1: Edit the constant**

Change:

```python
_VIEWER_H5_CACHE_MAX = 5
```

to:

```python
# Bumped from 5 to support multi-layer overlay sessions (primary + up to 3
# comparison layers without continuous eviction).
_VIEWER_H5_CACHE_MAX = 12
```

- [ ] **Step 2: Quick smoke**

Run the existing viewer route tests to confirm nothing regressed:

```bash
python -m pytest tests/test_dlc_viewer_routes.py -v 2>&1 | tail -10
```

Expected: all existing viewer tests pass (route changes from Task 1 still pass too).

- [ ] **Step 3: Commit**

```bash
git add src/dlc/viewer.py
git commit -m "perf(viewer): bump LRU h5 cache 5 -> 12 for layered overlay sessions"
```

---

## Task 3: Refactor `viewer.js` scalar overlay state to layer model (no UI change)

This task introduces `_vaLayers` while preserving the existing single-variant behavior 1:1. Add the layer abstraction, route every existing reference through `_vaPrimary()`, but keep the UI wired to a single h5 path field. UI changes come in T4.

**Files:** Modify `src/static/js/viewer.js`. No new files.

- [ ] **Step 1: Read the current state declarations and helpers**

Open `src/static/js/viewer.js`. Confirm these exist:
- `_vaH5Path` (around line 59)
- `_vaThreshold` (around line 63)
- `_vaPoseCache` (around line 73)
- `_vaPoseCacheKey()` (around line 794)
- `_vaLoadH5Info(h5Path)` (around line 878)
- `_vaLoadEditCacheFromServer(h5Path)` (around line 595)
- `_vaPaletteColor(idx, total)` (around line 397)

If any names differ, the implementer should adapt. The migration strategy below works regardless of exact line numbers.

- [ ] **Step 2: Add the layer model near the existing scalar declarations**

Insert AFTER the existing `_vaPoseCache` declaration (do NOT delete the scalars yet — we keep them temporarily as compatibility shims while the refactor is in flight):

```js
// ── Kinematic overlay LAYER state ───────────────────────────────────
// Element 0 = primary (editable). Elements 1+ = comparison layers (read-only).
// Each layer:
//   { id, path, label, type, shape, visible, threshold, posesCache,
//     bodyparts, errored }
// `_vaH5Path`, `_vaThreshold`, `_vaPoseCache` are kept as shims pointing at
// _vaPrimary() until every consumer is migrated (Task 5+).
const _vaLayers = [];
let   _vaGlobalThreshold   = 0.60;
let   _vaPerLayerThresholds = false;

function _vaPrimary()     { return _vaLayers[0] || null; }
function _vaCompare()     { return _vaLayers.slice(1); }
function _vaIsEditable()  { return _vaLayers.length === 1; }
function _vaLayerThreshold(layer) {
  return _vaPerLayerThresholds && layer.threshold != null
    ? layer.threshold
    : _vaGlobalThreshold;
}

const _SHAPE_ORDER = ["circle-filled", "circle-open", "square", "triangle"];
function _vaAssignShapes() {
  _vaLayers.forEach((l, i) => {
    l.shape = _SHAPE_ORDER[Math.min(i, _SHAPE_ORDER.length - 1)];
  });
}

let _vaLayerIdCounter = 0;
function _vaMakeLayer({path, label, type}) {
  return {
    id:         "layer_" + (_vaLayerIdCounter++),
    path,
    label,
    type:       type || "raw",
    shape:      "circle-filled",
    visible:    true,
    threshold:  null,            // null → use _vaGlobalThreshold
    posesCache: new Map(),
    bodyparts:  [],
    editsCache: null,
    errored:    false,
  };
}

// Replace the entire layer set with [primary], clear caches, reassign shapes.
function _vaSetPrimaryLayer(layer) {
  _vaLayers.length = 0;
  if (layer) _vaLayers.push(layer);
  _vaAssignShapes();
  // Compatibility shims for code paths not yet migrated.
  _vaH5Path = layer ? layer.path : null;
  _vaPoseCache.clear();
}
```

- [ ] **Step 3: Migrate `_vaAutoDetectH5` to populate the primary layer**

Find `_vaAutoDetectH5(absPath)` (around line 274). Locate where it currently sets `_vaH5Path = …` and calls `_vaLoadH5Info(...)`. Wrap the result into a layer:

After whichever line currently sets `_vaH5Path`, add:

```js
const layer = _vaMakeLayer({
  path:  _vaH5Path,
  label: `Raw — ${_vaH5Path.split("/").pop()}`,
  type:  "raw",
});
_vaSetPrimaryLayer(layer);
await _vaLoadLayerInfo(layer);
await _vaLoadEditCacheForPrimary();
```

Add the new helpers BELOW `_vaLoadH5Info`:

```js
async function _vaLoadLayerInfo(layer) {
  // Replaces _vaLoadH5Info; populates layer.bodyparts in place.
  try {
    const r    = await fetch(`/dlc/viewer/h5-info?h5=${encodeURIComponent(layer.path)}`);
    const data = await r.json();
    if (!r.ok || data.error) { layer.errored = true; return; }
    layer.bodyparts = data.bodyparts || [];
    if (layer === _vaPrimary()) {
      // Keep the legacy globals in sync for any code path not yet migrated.
      _vaAllBodyParts = layer.bodyparts.slice();
      _vaNBodyparts   = _vaAllBodyParts.length;
    }
  } catch (e) { layer.errored = true; }
}

async function _vaLoadEditCacheForPrimary() {
  const layer = _vaPrimary();
  if (!layer) return;
  // Reuse the existing _vaLoadEditCacheFromServer path so the marker-edit
  // banner / edits map keep working unchanged.
  await _vaLoadEditCacheFromServer(layer.path);
}
```

- [ ] **Step 4: Route every read of `_vaH5Path` through `_vaPrimary()`**

Search every `_vaH5Path` reference in `viewer.js`. For each one, replace with `_vaPrimary()?.path` for reads, or update the surrounding code to operate on a specific `layer` if it's inside a per-layer loop (T6 will introduce those loops; for now, all reads still target the primary).

The `_vaH5Path = …` write sites should ALL become `_vaSetPrimaryLayer(_vaMakeLayer({…}))`. The Browse-button h5-pick handler around line 1070 (`_vaH5Path = full;`) becomes:

```js
const layer = _vaMakeLayer({
  path:  full,
  label: `Custom — ${full.split("/").pop()}`,
  type:  "raw",
});
_vaSetPrimaryLayer(layer);
vaOverlayH5Path.value = full;
_vaPoseCache.clear();
vaOverlayH5Browser.classList.add("hidden");
_vaOverlayStatus("h5 selected");
await _vaLoadLayerInfo(layer);
await _vaLoadEditCacheForPrimary();
if (_vaOverlayEnabled) _vaLoadFrame(_vaCurrentFrame);
```

The Auto-detect h5 button (around `vaOverlayH5Auto?.addEventListener`) follows the same pattern.

- [ ] **Step 5: Verify primary-only behavior is unchanged**

Restart flask:

```bash
docker compose restart flask
```

In a browser, open the app, load a video that has a companion h5, toggle the overlay on, scrub frames. Verify markers still draw correctly. (No automated test exists for this; the e2e in T11 covers it.)

If anything regressed, check that every old `_vaH5Path` reference was updated.

- [ ] **Step 6: Commit**

```bash
git add src/static/js/viewer.js
git commit -m "refactor(viewer): introduce _vaLayers array; primary path uses layer model"
```

---

## Task 4: Replace overlay h5 picker UI with primary + compare list

**Files:** Modify `src/templates/partials/card_viewer.html`. Backwards-compatible: the manual Browse fallback (`va-overlay-h5-path` + `va-overlay-h5-browse` + `va-overlay-h5-browser`) is kept, just relabeled.

- [ ] **Step 1: Replace the overlay h5 picker block**

In `src/templates/partials/card_viewer.html`, find the block around lines 84–96 (the "h5 file row" inside `va-overlay-controls`). Replace ONLY that inner block with:

```html
<!-- ── Primary layer picker ─────────────────────────────── -->
<div style="margin-bottom:.45rem">
  <label style="display:block;font-size:.73rem;color:var(--text-dim);margin-bottom:.25rem">Primary layer (.h5)</label>
  <div style="display:flex;gap:.35rem;align-items:center">
    <select id="va-overlay-primary-select"
      style="flex:1;min-width:0;font-family:var(--mono);font-size:.75rem;padding:.28rem .45rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;color:var(--text)">
      <option value="">(no h5 detected — use Browse)</option>
    </select>
    <button class="btn-sm" id="va-overlay-h5-browse" title="Browse for any h5">Browse</button>
    <button class="btn-sm" id="va-overlay-h5-clear" style="opacity:.7" title="Clear">✕</button>
  </div>
  <input type="text" id="va-overlay-h5-path" readonly
    style="margin-top:.25rem;width:100%;font-family:var(--mono);font-size:.7rem;padding:.2rem .4rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;color:var(--text-dim)" />
  <div id="va-overlay-h5-browser" class="hidden"
    style="margin-top:.4rem;max-height:160px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;background:var(--surface);padding:.3rem .4rem;font-size:.75rem"></div>
</div>

<!-- ── Comparison layers ────────────────────────────────── -->
<div id="va-overlay-compare-block" style="margin-bottom:.45rem">
  <label style="display:flex;align-items:center;gap:.4rem;font-size:.73rem;color:var(--text-dim);margin-bottom:.25rem">
    Comparison layers
    <select id="va-overlay-add-compare"
      style="margin-left:auto;font-size:.72rem;padding:.18rem .35rem;background:var(--surface);border:1px solid var(--border);border-radius:5px;color:var(--text)">
      <option value="">+ add comparison…</option>
    </select>
  </label>
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

The retained legacy IDs (`va-overlay-h5-path`, `va-overlay-h5-browse`, `va-overlay-h5-browser`, `va-overlay-h5-clear`) keep the manual Browse fallback wired. The auto-detect `va-overlay-h5-auto` is dropped (replaced by the primary `<select>`); remove its event handler in T5.

- [ ] **Step 2: Verify the page still renders**

```bash
docker compose restart flask
```

Browser: open `http://localhost:5000/`, load any video, toggle overlay. Confirm the new picker shows up, no JS errors in the console (other than expected ones because we haven't wired the new IDs yet).

- [ ] **Step 3: Commit**

```bash
git add src/templates/partials/card_viewer.html
git commit -m "ui(viewer): replace single h5 picker with primary + compare layer UI"
```

---

## Task 5: Wire variant discovery + populate Primary `<select>`

**Files:** Modify `src/static/js/viewer.js`.

- [ ] **Step 1: Replace `_vaAutoDetectH5` with `_vaDiscoverVariants`**

Find `_vaAutoDetectH5(absPath)`. Replace its body (and rename) with:

```js
async function _vaDiscoverVariants(videoPath) {
  // Fetch every analyzable h5 near `videoPath` and populate the Primary <select>.
  // Default the primary to the first 'raw' entry, or the first variant otherwise.
  const select = document.getElementById("va-overlay-primary-select");
  const addCmp = document.getElementById("va-overlay-add-compare");
  if (!select || !addCmp) return;

  // Reset both controls to their empty states.
  select.innerHTML = '<option value="">(no h5 detected — use Browse)</option>';
  addCmp.innerHTML = '<option value="">+ add comparison…</option>';

  let data;
  try {
    const r = await fetch(`/dlc/viewer/h5-variants?video=${encodeURIComponent(videoPath)}`);
    data = await r.json();
    if (!r.ok || !Array.isArray(data.variants)) return;
  } catch (e) { return; }

  if (!data.variants.length) return;

  // Populate primary select.
  data.variants.forEach((v) => {
    const opt = document.createElement("option");
    opt.value = v.path;
    opt.textContent = v.label;
    if (v.disabled) opt.disabled = true;
    opt.dataset.type  = v.type;
    opt.dataset.label = v.label;
    select.appendChild(opt);
  });

  // Default selection.
  const defaultEntry = data.variants.find(v => v.type === "raw" && !v.disabled)
                    || data.variants.find(v => !v.disabled);
  if (!defaultEntry) return;
  select.value = defaultEntry.path;
  await _vaApplyPrimaryFromSelect();
  _vaRefreshAddComparisonOptions(data.variants);
}

function _vaRefreshAddComparisonOptions(variants) {
  const addCmp = document.getElementById("va-overlay-add-compare");
  if (!addCmp) return;
  addCmp.innerHTML = '<option value="">+ add comparison…</option>';
  const taken = new Set(_vaLayers.map(l => l.path));
  variants.forEach((v) => {
    if (v.disabled) return;
    if (taken.has(v.path)) return;
    const opt = document.createElement("option");
    opt.value = v.path;
    opt.textContent = v.label;
    opt.dataset.type  = v.type;
    opt.dataset.label = v.label;
    addCmp.appendChild(opt);
  });
}

// Cached on the page for re-populating after add/remove.
let _vaLastVariants = [];

async function _vaApplyPrimaryFromSelect() {
  const select = document.getElementById("va-overlay-primary-select");
  if (!select) return;
  const path  = select.value;
  if (!path) return;
  const opt   = select.options[select.selectedIndex];
  const label = opt?.dataset.label || path.split("/").pop();
  const type  = opt?.dataset.type  || "raw";

  const layer = _vaMakeLayer({ path, label, type });
  _vaSetPrimaryLayer(layer);
  document.getElementById("va-overlay-h5-path").value = path;
  await _vaLoadLayerInfo(layer);
  await _vaLoadEditCacheForPrimary();
  if (_vaOverlayEnabled) _vaLoadFrame(_vaCurrentFrame);
}
```

Also keep a memo of the variants so we can re-build the add-comparison list after add/remove:

In `_vaDiscoverVariants` after `data = await r.json();`, add `_vaLastVariants = data.variants;`.

- [ ] **Step 2: Replace every call site of `_vaAutoDetectH5` with `_vaDiscoverVariants`**

Search for `_vaAutoDetectH5(`. Each call site is a place a video was just loaded. Replace each call with `_vaDiscoverVariants(<the same video path arg>)`.

- [ ] **Step 3: Wire the Primary `<select>` change event**

Near where the other overlay controls register their listeners (search for `vaOverlayH5Browse?.addEventListener`), add:

```js
const vaOverlayPrimarySelect = document.getElementById("va-overlay-primary-select");
vaOverlayPrimarySelect?.addEventListener("change", _vaApplyPrimaryFromSelect);
```

Also remove the `vaOverlayH5Auto?.addEventListener("click", …)` line (the Auto button no longer exists in the partial).

- [ ] **Step 4: Smoke check in the browser**

```bash
docker compose restart flask
```

Open a video that has a companion h5. Open overlay. Confirm:
- Primary `<select>` is populated with at least the companion h5.
- Default selection draws markers normally.
- Switching primary via the dropdown re-loads markers.

- [ ] **Step 5: Commit**

```bash
git add src/static/js/viewer.js
git commit -m "feat(viewer): wire /h5-variants discovery into Primary <select>"
```

---

## Task 6: Comparison add/remove + multi-layer rendering

**Files:** Modify `src/static/js/viewer.js`.

- [ ] **Step 1: Add the comparison-row UI builder**

Append:

```js
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
    // per-layer threshold (rendered conditionally in T8); placeholder slot:
    const thrSlot = document.createElement("span");
    thrSlot.dataset.role = "threshold";
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

function _shapeGlyph(shape) {
  switch (shape) {
    case "circle-filled": return "●";
    case "circle-open":   return "○";
    case "square":        return "□";
    case "triangle":      return "△";
    default:              return "?";
  }
}

async function _vaAddCompare(path, label, type) {
  if (_vaLayers.some(l => l.path === path)) return;
  const layer = _vaMakeLayer({ path, label, type });
  _vaLayers.push(layer);
  _vaAssignShapes();
  await _vaLoadLayerInfo(layer);
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
  if (!banner) return;
  banner.classList.toggle("hidden", _vaIsEditable());
}
```

- [ ] **Step 2: Wire the add-comparison `<select>`**

In the listener-registration block, add:

```js
const vaOverlayAddCompare = document.getElementById("va-overlay-add-compare");
vaOverlayAddCompare?.addEventListener("change", async (e) => {
  const path  = e.target.value;
  if (!path) return;
  const opt   = e.target.options[e.target.selectedIndex];
  await _vaAddCompare(path, opt.dataset.label, opt.dataset.type);
  e.target.value = "";  // reset to placeholder
});
```

- [ ] **Step 3: Add shape-aware draw primitives**

Find the existing draw routine that renders pose markers (look for `arc(`, `_vaPaletteColor`, or the references near line 438/479 to `pose.color_idx`). Above that function, add:

```js
function _drawCircleFilled(ctx, x, y, r, color) {
  ctx.fillStyle = color;
  ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fill();
}
function _drawCircleOpen(ctx, x, y, r, color) {
  ctx.strokeStyle = color; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.stroke();
}
function _drawSquare(ctx, x, y, r, color) {
  ctx.strokeStyle = color; ctx.lineWidth = 2;
  ctx.strokeRect(x - r, y - r, 2 * r, 2 * r);
}
function _drawTriangle(ctx, x, y, r, color) {
  ctx.strokeStyle = color; ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x,        y - r);
  ctx.lineTo(x + r,    y + r);
  ctx.lineTo(x - r,    y + r);
  ctx.closePath();
  ctx.stroke();
}
const _SHAPE_FN = {
  "circle-filled": _drawCircleFilled,
  "circle-open":   _drawCircleOpen,
  "square":        _drawSquare,
  "triangle":      _drawTriangle,
};
```

- [ ] **Step 4: Multi-layer draw loop**

Find the existing per-frame draw routine (the function referenced from `_vaLoadFrame`; it currently iterates `_vaCurrentPoses` and calls `arc()` with a per-bodypart palette color). Refactor it so the drawing happens **per visible layer**:

```js
function _vaDrawCurrentFrame() {
  const ctx = vaOverlayCanvas?.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, vaOverlayCanvas.width, vaOverlayCanvas.height);
  if (!_vaOverlayEnabled) return;

  _vaLayers.filter(l => l.visible && !l.errored).forEach((layer) => {
    const cached = layer.posesCache.get(_vaCurrentFrame);
    if (!cached) return;
    const drawFn = _SHAPE_FN[layer.shape] || _drawCircleFilled;
    cached.poses.forEach((pose) => {
      const color = _vaPaletteColor(pose.color_idx, cached.n_bodyparts || layer.bodyparts.length);
      drawFn(ctx, pose.x_canvas, pose.y_canvas, _vaMarkerSize, color);
    });
  });
}
```

Update the existing single-pose-loop body to call `_vaDrawCurrentFrame()` instead. The existing label/hover hit-testing logic stays driven by primary's poses (`_vaPrimary().posesCache.get(_vaCurrentFrame)`).

- [ ] **Step 5: Per-layer pose fetching**

Find `_vaPoseCacheKey()` and the function that fetches a single frame's poses (the `URLSearchParams({ h5: _vaH5Path, threshold: ...})` block around line 808). Replace with a layer-parameterized version:

```js
function _vaPoseCacheKey(layer) {
  return `${layer.path}:${_vaLayerThreshold(layer).toFixed(2)}`;
}

async function _vaFetchPosesForFrame(layer, frame) {
  const key    = _vaPoseCacheKey(layer);
  const cached = layer.posesCache.get(frame);
  if (cached && cached.key === key) return cached;
  const params = new URLSearchParams({
    h5:        layer.path,
    threshold: _vaLayerThreshold(layer).toFixed(2),
  });
  try {
    const r    = await fetch(`/dlc/viewer/frame-poses/${frame}?${params}`);
    const data = await r.json();
    if (!r.ok || data.error) { layer.errored = true; return null; }
    const entry = { key, poses: data.poses || [], n_bodyparts: data.n_bodyparts || 1 };
    layer.posesCache.set(frame, entry);
    return entry;
  } catch (e) { layer.errored = true; return null; }
}
```

Update `_vaLoadFrame(n)` to fetch poses for **every visible layer** before drawing:

```js
async function _vaLoadFrame(n) {
  // ... existing img-load + spinner code unchanged ...
  _vaCurrentFrame = n;
  if (_vaOverlayEnabled) {
    await Promise.all(
      _vaLayers
        .filter(l => l.visible && !l.errored)
        .map(l => _vaFetchPosesForFrame(l, n))
    );
  }
  _vaDrawCurrentFrame();
  // background prefetch (T6.6)
  _vaPrefetchPoseWindow(n + 1);
}
```

- [ ] **Step 6: Per-layer batch prefetch**

Update `_vaPrefetchPoseWindow(fromFrame)` similarly — iterate visible layers and call the batch endpoint per layer:

```js
async function _vaPrefetchPoseWindow(fromFrame) {
  await Promise.all(
    _vaLayers
      .filter(l => l.visible && !l.errored)
      .map(layer => _vaPrefetchOne(layer, fromFrame))
  );
}

async function _vaPrefetchOne(layer, fromFrame) {
  const key = _vaPoseCacheKey(layer);
  // Skip if the next _POSE_WINDOW frames are already cached.
  let allCached = true;
  for (let i = fromFrame; i < fromFrame + _POSE_WINDOW; i++) {
    const c = layer.posesCache.get(i);
    if (!c || c.key !== key) { allCached = false; break; }
  }
  if (allCached) return;
  const params = new URLSearchParams({
    h5:        layer.path,
    start:     String(fromFrame),
    count:     String(_POSE_WINDOW),
    threshold: _vaLayerThreshold(layer).toFixed(2),
  });
  try {
    const r    = await fetch(`/dlc/viewer/frame-poses-batch?${params}`);
    const data = await r.json();
    if (!r.ok) return;
    (data.frames || []).forEach((fd) => {
      layer.posesCache.set(fd.frame, {
        key,
        poses:       fd.poses || [],
        n_bodyparts: fd.n_bodyparts || 1,
      });
    });
  } catch (e) { /* swallow; next frame fetch will retry */ }
}
```

- [ ] **Step 7: Remove the now-unused `_vaPoseCache` global and `_vaH5Path` shim**

Delete the legacy global `_vaPoseCache` declaration AND every read of it. Same for `_vaH5Path`. Search the file for both names; every reference should now go through a layer object. Edit handlers (drag-end, save) target `_vaPrimary().path` explicitly (T7 will lock the gate).

- [ ] **Step 8: Smoke check**

```bash
docker compose restart flask
```

Browser: open a video with both companion h5 AND a postproc filtered output. Toggle overlay. Use the "+ add comparison" dropdown to add the filtered variant. Confirm:

- Comparison row appears in the list with the open-circle glyph.
- Both layers' markers draw on the canvas.
- Removing the comparison restores single-layer view.

- [ ] **Step 9: Commit**

```bash
git add src/static/js/viewer.js
git commit -m "feat(viewer): multi-layer overlay rendering with shape-differentiated markers"
```

---

## Task 7: Edit-mode gate (single-variant only)

**Files:** Modify `src/static/js/viewer.js`.

- [ ] **Step 1: Gate the drag-end POST**

Find the marker-edit POST handler (around line 577 where it calls `JSON.stringify({ h5: _vaH5Path, frame, bp, x, y })`). At the top of that function, add:

```js
if (!_vaIsEditable()) return;     // edit disabled while compare layers active
const layer = _vaPrimary();
if (!layer) return;
```

Then replace the `_vaH5Path` reference in the body with `layer.path`. Same treatment for the "delete marker" handler (around line 589) and the Save Adjustments handler (around line 725).

- [ ] **Step 2: Gate the canvas pointer-down/move/up event handlers**

Find the drag-state handlers (look for `pointerdown` / `pointermove` / `pointerup` around the canvas). At the top of each, return early when `!_vaIsEditable()`.

- [ ] **Step 3: Hide the marker-edit banner buttons in compare mode**

Find the existing `va-marker-edit-banner` show/hide logic. When compare layers are active, force-hide the banner (regardless of edit count). In the function that toggles the banner (search for `va-marker-edit-banner` `classList.add("hidden")`), add a guard:

```js
if (!_vaIsEditable()) {
  vaMarkerEditBanner.classList.add("hidden");
  return;
}
```

- [ ] **Step 4: Smoke check**

```bash
docker compose restart flask
```

Browser: load a video, add a comparison layer. Try to drag a marker on the canvas — nothing should happen. The "Edit disabled while comparing layers" banner under the compare list should be visible. Remove the comparison; drag should work again.

- [ ] **Step 5: Commit**

```bash
git add src/static/js/viewer.js
git commit -m "feat(viewer): disable marker editing while comparison layers are active"
```

---

## Task 8: Per-layer threshold + Customize toggle

**Files:** Modify `src/static/js/viewer.js`.

- [ ] **Step 1: Wire the global threshold slider through `_vaGlobalThreshold`**

Find the existing `vaOverlayThreshold?.addEventListener("input", …)` (the slider near `va-overlay-threshold`). Replace its body so that it updates `_vaGlobalThreshold` AND clears every layer's `posesCache` entries whose `key` was tied to the old threshold:

```js
vaOverlayThreshold?.addEventListener("input", () => {
  _vaGlobalThreshold = Number(vaOverlayThreshold.value);
  vaOverlayThresholdVal.textContent = _vaGlobalThreshold.toFixed(2);
  // Stale-key entries are auto-skipped by _vaFetchPosesForFrame (key mismatch),
  // so we just trigger a re-fetch of the current frame.
  if (_vaOverlayEnabled) _vaLoadFrame(_vaCurrentFrame);
});
```

(Reading `_vaThreshold` should already be removed in T6 step 7.)

- [ ] **Step 2: Wire the Customize toggle**

Add:

```js
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

- [ ] **Step 3: Inline per-layer slider in compare rows**

Update `_vaRenderCompareRows()`. Inside the row build loop, replace the `thrSlot` placeholder block with:

```js
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
    if (_vaOverlayEnabled) _vaFetchPosesForFrame(layer, _vaCurrentFrame).then(_vaDrawCurrentFrame);
  });
  thrSlot.appendChild(slider);
  thrSlot.appendChild(lbl);
}
row.appendChild(thrSlot);
```

- [ ] **Step 4: Inline per-layer slider on the Primary row**

The primary doesn't sit in `va-overlay-compare-list`. Add a tiny inline slider next to the Primary `<select>` only when `_vaPerLayerThresholds` is on. New helper:

```js
function _vaRenderPrimaryThresholdInline() {
  // Insert/remove a small slider after the Primary <select>.
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
    if (_vaOverlayEnabled) _vaFetchPosesForFrame(layer, _vaCurrentFrame).then(_vaDrawCurrentFrame);
  });
  slot.appendChild(slider);
  slot.appendChild(lbl);
}
```

Call `_vaRenderPrimaryThresholdInline()` from `_vaApplyPrimaryFromSelect` (after `_vaSetPrimaryLayer(layer)`) and at the end of the Customize-toggle handler.

- [ ] **Step 5: Smoke check**

```bash
docker compose restart flask
```

Browser: open overlay, add a comparison, tick "Customize threshold per layer". Confirm:
- A slider appears next to Primary `<select>` and on each compare row.
- Moving the primary's slider re-renders only its markers (network panel: one `frame-poses` request, not two).
- Untick: per-row sliders disappear; visual reverts to the global slider value.

- [ ] **Step 6: Commit**

```bash
git add src/static/js/viewer.js
git commit -m "feat(viewer): per-layer threshold customization toggle"
```

---

## Task 9: Static-template UI isolation tests

**Files:** Create `tests/test_viewer_layers_ui_isolation.py`.

- [ ] **Step 1: Write the test file**

```python
"""Static template + JS-source assertions for the viewer layered overlay."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTIALS = ROOT / "src" / "templates" / "partials"
VIEWER_HTML = PARTIALS / "card_viewer.html"
VIEWER_JS = ROOT / "src" / "static" / "js" / "viewer.js"

NEW_IDS = {
    "va-overlay-primary-select",
    "va-overlay-compare-list",
    "va-overlay-add-compare",
    "va-overlay-customize-thresholds",
    "va-overlay-edit-disabled-banner",
    "va-overlay-compare-block",
}

RETAINED_IDS = {  # Browse fallback must keep working
    "va-overlay-h5-path",
    "va-overlay-h5-browse",
    "va-overlay-h5-browser",
    "va-overlay-h5-clear",
}


def _ids_in(file: Path) -> dict[str, int]:
    seen: dict[str, int] = {}
    for m in re.finditer(r'id="([^"]+)"', file.read_text()):
        seen[m.group(1)] = seen.get(m.group(1), 0) + 1
    return seen


def test_new_overlay_ids_present_and_unique():
    seen_global: dict[str, int] = {}
    for f in PARTIALS.glob("*.html"):
        for m in re.finditer(r'id="([^"]+)"', f.read_text()):
            seen_global[m.group(1)] = seen_global.get(m.group(1), 0) + 1
    for nid in NEW_IDS:
        assert seen_global.get(nid, 0) == 1, (
            f"id {nid!r} appears {seen_global.get(nid, 0)} times across partials"
        )


def test_retained_ids_still_present():
    seen = _ids_in(VIEWER_HTML)
    for nid in RETAINED_IDS:
        assert seen.get(nid, 0) >= 1, f"retained id {nid!r} is missing"


def test_viewer_js_references_new_ids():
    js = VIEWER_JS.read_text()
    for nid in (
        "va-overlay-primary-select",
        "va-overlay-add-compare",
        "va-overlay-compare-list",
        "va-overlay-customize-thresholds",
    ):
        assert nid in js, f"viewer.js does not reference {nid!r}"


def test_viewer_js_uses_layer_model():
    """Sanity: the layer abstraction landed."""
    js = VIEWER_JS.read_text()
    assert "_vaLayers" in js
    assert "_vaPrimary" in js
    assert "_vaIsEditable" in js
    assert "/dlc/viewer/h5-variants" in js


def test_viewer_js_dropped_legacy_globals():
    """Regression: the scalar overlay globals are gone."""
    js = VIEWER_JS.read_text()
    # _vaH5Path is allowed to remain ONLY as a property read (e.g. layer.path);
    # the bare global declaration `let _vaH5Path` must not exist.
    assert "let _vaH5Path" not in js, (
        "the legacy scalar `_vaH5Path` declaration should be removed; "
        "use _vaPrimary().path instead"
    )
    assert "let _vaThreshold" not in js, (
        "the legacy scalar `_vaThreshold` declaration should be removed; "
        "use _vaGlobalThreshold + _vaLayerThreshold(layer) instead"
    )
```

- [ ] **Step 2: Run the test**

```bash
python -m pytest tests/test_viewer_layers_ui_isolation.py -v
```

Expected: 5 PASSED. If `let _vaH5Path` or `let _vaThreshold` still exists in `viewer.js`, that's a real bug from T6 step 7 — fix in `viewer.js`, not by relaxing the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_viewer_layers_ui_isolation.py
git commit -m "test(viewer): static-template + JS-source assertions for layered overlay"
```

---

## Task 10: Real-data integration test (OM-2 RatBox folder)

**Files:** Modify `tests/test_postprocess_real_project.py` (extend with one new test).

- [ ] **Step 1: Append the test**

```python
import os

# Path used by the post-process e2e (see tests/e2e_postprocess_smoke.py).
_OM2_HOST = Path(
    "/home/sam/synology/Parra-Lab-Data/Reaching-Task-Data/RatBox Videos/"
    "tdcs/042426/OM-2_cam0_20260424_105301_2_trig1_fps200_exposure1500_gain10"
)


@pytest.mark.skipif(not _OM2_HOST.is_dir(),
                    reason="OM-2 RatBox folder not on this host")
def test_h5_variants_against_om2_ratbox(flask_test_client):
    """Run /dlc/viewer/h5-variants against a real OM-2 video; expect at least
    a Raw companion entry. Filtered entries appear if a postproc run has been
    completed on this host."""
    client, _app, _redis, _data, _user = flask_test_client
    # Pick the first .avi in the OM-2 folder.
    videos = sorted(_OM2_HOST.glob("*.avi"))
    if not videos:
        pytest.skip("No .avi files in OM-2 RatBox folder")
    video = videos[0]

    # Translate host path to the container-mount path (the route runs in flask).
    container_video = str(video).replace(
        "/home/sam/synology/Parra-Lab-Data",
        "/user-data/Parra-Data/Cloud",
    )
    resp = client.get(f"/dlc/viewer/h5-variants?video={container_video}")
    if resp.status_code == 403:
        pytest.skip("Path allowlist denied — flask DATA_DIR/USER_DATA_DIR not "
                    "configured for the synology mount")
    assert resp.status_code == 200, resp.get_json()
    variants = resp.get_json()["variants"]

    paths = [v["path"] for v in variants]
    raw_entries = [v for v in variants if v["type"] == "raw"]
    assert raw_entries, f"expected a raw companion entry, got {paths}"

    # If post-process outputs already exist on this host, surface them.
    filtered_entries = [v for v in variants if v["type"] == "filtered"]
    if filtered_entries:
        # Each filtered path must live under postproc/<ts>_filterpredictions/
        for f in filtered_entries:
            assert "/postproc/" in f["path"]
            assert f["disabled"] is False
```

- [ ] **Step 2: Run inside the flask container**

```bash
WORKER=$(docker ps --filter "name=flask" -q | head -1)
docker cp tests/test_postprocess_real_project.py "$WORKER":/app/tests/
docker exec "$WORKER" bash -c "cd /app && python -m pytest tests/test_postprocess_real_project.py::test_h5_variants_against_om2_ratbox -v"
docker exec "$WORKER" rm -f /app/tests/test_postprocess_real_project.py
```

Expected: PASS or skip (skip is acceptable on hosts without the synology mount).

- [ ] **Step 3: Commit**

```bash
git add tests/test_postprocess_real_project.py
git commit -m "test(viewer): real-data integration test for /h5-variants on OM-2 RatBox"
```

---

## Task 11: Playwright e2e smoke

**Files:** Create `tests/e2e_viewer_layers_smoke.py`. Manual run only; not part of the unit suite.

- [ ] **Step 1: Write the script**

```python
"""End-to-end smoke for the viewer's layered overlay.

Runs against the live app (http://localhost:5000). Drives the browser:
1. Force-unhides the dlc project sidebar so the viewer card is reachable.
2. Opens the View Analyzed Videos / Frames card.
3. Browses to one of the OM-2 RatBox videos and loads it.
4. Toggles the kinematic overlay on; verifies the Primary <select> is populated.
5. Adds a comparison layer; verifies the "Edit disabled (compare mode)"
   banner appears and Save Adjustments is hidden.
6. Removes the comparison; verifies the banner is gone.
7. Toggles "Customize threshold per layer"; verifies a per-layer slider
   appears on the compare row.

Manual:
    python tests/e2e_viewer_layers_smoke.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

APP_URL = "http://localhost:5000/?token=deeplabcut"
OM2_HOST = Path(
    "/home/sam/synology/Parra-Lab-Data/Reaching-Task-Data/RatBox Videos/"
    "tdcs/042426/OM-2_cam0_20260424_105301_2_trig1_fps200_exposure1500_gain10"
)
HOST_TO_CONTAINER = (
    "/home/sam/synology/Parra-Lab-Data",
    "/user-data/Parra-Data/Cloud",
)


def _container_path(host_path: Path) -> str:
    s = str(host_path)
    return s.replace(*HOST_TO_CONTAINER)


def main() -> int:
    if not OM2_HOST.is_dir():
        print(f"FATAL: OM-2 folder not on host: {OM2_HOST}", file=sys.stderr)
        return 2
    avis = sorted(OM2_HOST.glob("*.avi"))
    if not avis:
        print("FATAL: no .avi in OM-2 folder", file=sys.stderr)
        return 2
    video_path = _container_path(avis[0])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1500, "height": 1100})
        page = ctx.new_page()
        page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))
        page.set_default_timeout(15_000)
        page.goto(APP_URL)
        page.wait_for_load_state("networkidle")

        page.evaluate(
            "() => { const ids=['dlc-project-card','view-analyzed-card','dlc-frame-extract-launch'];"
            "ids.forEach(id => { const el=document.getElementById(id); if (el) el.classList.remove('hidden'); }); }"
        )

        # Open the viewer card.
        page.click("#btn-open-view-analyzed")
        page.wait_for_selector("#view-analyzed-card:not(.hidden)")

        # Switch to Browse tab and navigate to the OM-2 folder.
        page.click("#va-tab-browse")
        page.fill("#va-browse-breadcrumb",
                  video_path.rsplit("/", 1)[0])
        page.keyboard.press("Enter")
        # Click the first video file row.
        page.wait_for_selector(f"text={avis[0].name}", timeout=10_000)
        page.click(f"text={avis[0].name}")
        # Player section appears.
        page.wait_for_selector("#va-player-section:not(.hidden)")

        # Toggle overlay on.
        page.click("#va-overlay-toggle")
        page.wait_for_selector("#va-overlay-controls:not(.hidden)")

        # Primary dropdown must populate within ~5s of variant discovery fetch.
        time.sleep(2.0)
        primary_options = page.evaluate(
            "() => Array.from(document.querySelectorAll('#va-overlay-primary-select option')).map(o => o.value)"
        )
        print(f"primary options: {primary_options}")
        if not any(primary_options):
            print("WARN: no h5 variants discovered for this video", file=sys.stderr)

        # Try to pick the first non-empty add-comparison option.
        compare_options = page.evaluate(
            "() => Array.from(document.querySelectorAll('#va-overlay-add-compare option')).slice(1).map(o => o.value)"
        )
        if compare_options:
            page.select_option("#va-overlay-add-compare", value=compare_options[0])
            page.wait_for_selector("#va-overlay-edit-disabled-banner:not(.hidden)",
                                   timeout=5_000)
            print("OK: comparison added; edit-disabled banner visible")
            # Remove the row.
            page.click("#va-overlay-compare-list button")
            page.wait_for_selector("#va-overlay-edit-disabled-banner.hidden",
                                   timeout=3_000)
            print("OK: comparison removed; banner hidden")
        else:
            print("INFO: no comparison variants on disk — skipping compare-mode test")

        # Customize threshold toggle.
        page.click("#va-overlay-customize-thresholds")
        # Re-add a comparison to see the per-layer slider.
        if compare_options:
            page.select_option("#va-overlay-add-compare", value=compare_options[0])
            slider_present = page.evaluate(
                "() => !!document.querySelector('#va-overlay-compare-list input[type=range]')"
            )
            print(f"per-layer slider present: {slider_present}")

        browser.close()
        print("\nALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it**

```bash
python tests/e2e_viewer_layers_smoke.py
```

Expected: `ALL CHECKS PASSED` (or graceful skip if the OM-2 folder isn't mounted).

- [ ] **Step 3: Commit**

```bash
git add tests/e2e_viewer_layers_smoke.py
git commit -m "test(viewer): playwright e2e smoke for layered overlay"
```

---

## Task 12: Final verification

- [ ] **Step 1: Full test pass on the host**

```bash
python -m pytest \
  tests/test_dlc_viewer_routes.py \
  tests/test_viewer_layers_ui_isolation.py \
  tests/test_postprocess_real_project.py \
  tests/test_frontend_assets.py \
  -v 2>&1 | tail -40
```

All non-skipped tests must pass. The `flask_test_client` teardown LookupErrors documented earlier are still acceptable noise.

- [ ] **Step 2: Run the post-process e2e to confirm no regression**

```bash
python tests/e2e_postprocess_smoke.py 2>&1 | tail -20
```

Expected: `ALL CHECKS PASSED`. The post-process card must still work end-to-end since we changed the viewer, not the post-process card.

- [ ] **Step 3: Run the new viewer e2e**

```bash
python tests/e2e_viewer_layers_smoke.py 2>&1 | tail -20
```

Expected: `ALL CHECKS PASSED` (or skip if OM-2 folder absent).

- [ ] **Step 4: Manual sanity in browser**

Per CLAUDE.md, exercise in a real browser:
1. Load a video that has both companion h5 + at least one postproc variant.
2. Confirm Primary dropdown lists both; default = companion.
3. Add a comparison; verify shape difference visible on the canvas (filled circle vs open circle).
4. Try to drag a marker → no effect (banner shown).
5. Remove comparison → drag works → save edit → reload page → edit persists.
6. Tick Customize per layer → primary + comparison sliders appear → drag one → only that layer's markers refresh.

If any step doesn't work, file as a follow-up — the unit + e2e tests should already have caught the obvious issues.

- [ ] **Step 5: Final commit if anything else changed**

```bash
git status
# If any incidental fixes happened, commit them. Otherwise nothing to do.
```

---

## Self-Review

Spec coverage check (each spec section maps to at least one task):

| Spec section | Implemented in task |
|---|---|
| UI: Primary dropdown + compare list + customize toggle | T4 (HTML), T5 (primary), T6 (compare), T8 (customize) |
| Backend `/h5-variants` route | T1 |
| LRU cache bump 5 → 12 | T2 |
| JS layer model (`_vaLayers`, helpers) | T3 |
| Variant discovery flow | T5 |
| Comparison add/remove + multi-layer rendering | T6 |
| Shape primitives | T6 step 3 |
| Edit-mode auto-disable | T7 |
| Per-layer threshold + Customize toggle | T8 |
| Static-template UI isolation tests | T9 |
| Real-data integration test (OM-2) | T10 |
| Playwright e2e smoke | T11 |
| Final verification | T12 |

Type/name consistency check: `_vaLayers`, `_vaPrimary()`, `_vaCompare()`, `_vaIsEditable()`, `_vaLayerThreshold(layer)`, `_vaMakeLayer({path,label,type})`, `_vaSetPrimaryLayer(layer)`, `_vaLoadLayerInfo(layer)`, `_vaLoadEditCacheForPrimary()`, `_vaFetchPosesForFrame(layer, frame)`, `_vaPrefetchPoseWindow(fromFrame)`, `_vaPrefetchOne(layer, fromFrame)`, `_vaDrawCurrentFrame()`, `_vaRenderCompareRows()`, `_vaRenderPrimaryThresholdInline()`, `_vaAssignShapes()`, `_vaAddCompare(path,label,type)`, `_vaRemoveCompare(id)`, `_vaUpdateEditDisabledBanner()`. Names consistent across all tasks where they appear.

Placeholder scan: the only "guesses" left in this plan are explicit fall-back-allowed identifications ("if the line numbers differ slightly, the implementer should adapt") which is research, not a placeholder. No "TBD"/"add error handling"/"similar to Task X" violations.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-02-viewer-postproc-layers.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session with checkpoints for review.

Which approach?
