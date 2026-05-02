# Viewer Flow Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the View Analyzed Videos / Frames flow snappy and obvious — Browse list filtered by h5 presence, auto-loaded latest variant as Primary, fixed "+ add comparison" dropdown, paint-barriered playback, "play every N frames" step, and a Redis-backed dir cache so browsing doesn't re-walk the filesystem.

**Architecture:** One new backend route (`/dlc/viewer/dir-with-h5`) feeds the Browse list with a Redis-cached, mtime-invalidated dir listing. The frontend `viewer.js` swaps `_vaRefreshBrowse` over to the new route, gains a `Hide videos without h5` toggle, picks the newest-by-`ts` variant as Primary on video load, clears comparisons on primary swap, and adds a `requestAnimationFrame` paint barrier to the play loop along with a `va-play-step` per-tick advance input.

**Tech Stack:** Flask blueprint, Redis (existing `_ctx.redis_client()`), vanilla JS (ES module), pytest + Playwright.

**Spec:** `docs/superpowers/specs/2026-05-02-viewer-flow-improvements-design.md`

---

## File Structure

**Modified files:**

| Path | What changes |
|---|---|
| `src/dlc/viewer.py` | Add `_VIEWER_VIDEO_EXTS`, `_viewer_dir_mtime`, `_build_dir_with_h5`, route `viewer_dir_with_h5`. |
| `src/templates/partials/card_viewer.html` | Browse-tab `va-browse-hide-no-h5` checkbox; player `va-play-step` input; overlay-block `va-overlay-add-compare-empty-hint` span. |
| `src/static/js/viewer.js` | Replace `_vaRefreshBrowse` body (now consumes `/dir-with-h5`); add filter + h5-count badges + `data-has-h5` attribute. Replace selection logic in `_vaDiscoverVariants` with `_vaPickBestPrimary`. Rewrite `_vaApplyPrimaryFromSelect` to clear comparisons. Rewrite `_vaRefreshAddComparisonOptions` to toggle the empty hint. Add `_vaPlayStep`. Update the play loop. Add `await new Promise(requestAnimationFrame)` at the end of `_vaLoadFrame`. |
| `src/static/js/state.js` | Add `vaBrowseHideNoH5: true`. |
| `tests/test_dlc_viewer_routes.py` | Add 6 new dir-with-h5 cases. |
| `tests/test_viewer_layers_ui_isolation.py` | Add new-ID assertions, JS-source asserts. |
| `tests/test_postprocess_real_project.py` | Add OM-2 dir-with-h5 integration case. |
| `tests/e2e_viewer_layers_smoke.py` | Add four new assertions: auto-latest, browse filter, add-compare empty hint, frame-step. |

**New files:** none.

---

## Conventions

- Repo root: `/home/sam/docker-images/deeplabcut-webapp-docker`.
- Pre-existing user edits to ignore (do NOT stage): `src/dlc/README.md`, `src/dlc/labeling.py`, `src/dlc/vlm_indexer.py`, `src/static/js/anipose.js`, `src/static/js/main.js`, `src/static/js/vlm_refiner.js`, `tests/test_dlc_celery_tasks.py`. Untracked: `CLAUDE.md`, `src/static/js/admin.js`, `tests/test_dlc_labeling_routes.py`.
- The `flask_test_client` fixture's teardown LookupError noise is pre-existing (documented in earlier viewer commits). Test bodies that show as "errors" in pytest output but PASSED their assertions are still considered passing.
- Restart flask after backend or template changes: `docker compose restart flask`. Worker not needed for any task here.
- The OM-2 RatBox folder for the integration test:
  - Container: `/user-data/Parra-Data/Cloud/Reaching-Task-Data/RatBox Videos/tdcs/042426/OM-2_cam0_20260424_105301_2_trig1_fps200_exposure1500_gain10`
  - Host: `/home/sam/synology/Parra-Lab-Data/Reaching-Task-Data/RatBox Videos/tdcs/042426/OM-2_cam0_20260424_105301_2_trig1_fps200_exposure1500_gain10`

---

## Task 1: Backend `/dlc/viewer/dir-with-h5` route + Redis cache

**Files:**
- Modify: `src/dlc/viewer.py` (add helpers + route).
- Test: `tests/test_dlc_viewer_routes.py` (append cases).

- [ ] **Step 1: Append failing tests**

Append to `tests/test_dlc_viewer_routes.py`:

```python
def test_dir_with_h5_returns_videos_with_h5_counts(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)

    # Two videos: one with companion + one postproc filtered, one without h5.
    v1 = tmp_path / "vidA.avi"; v1.write_bytes(b"")
    v2 = tmp_path / "vidB.mp4"; v2.write_bytes(b"")
    _seed_companion_h5(tmp_path, v1.stem)
    _seed_postproc_run(tmp_path, "20260502-120000", "filterpredictions", v1.stem)

    resp = client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["path"] == str(tmp_path)
    by_name = {v["name"]: v for v in data["videos"]}
    assert by_name["vidA.avi"]["has_h5"] is True
    assert by_name["vidA.avi"]["h5_count"] == 2  # companion + postproc filtered
    assert by_name["vidB.mp4"]["has_h5"] is False
    assert by_name["vidB.mp4"]["h5_count"] == 0


def test_dir_with_h5_cache_hit_avoids_rebuild(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)
    v = tmp_path / "vid.avi"; v.write_bytes(b"")
    _seed_companion_h5(tmp_path, v.stem)

    calls = {"n": 0}
    real_build = vw._build_dir_with_h5
    def spy(d, mtime):
        calls["n"] += 1
        return real_build(d, mtime)
    monkeypatch.setattr(vw, "_build_dir_with_h5", spy)

    r1 = client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    r2 = client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    assert r1.status_code == 200 and r2.status_code == 200
    assert calls["n"] == 1, "second request must hit Redis cache"


def test_dir_with_h5_invalidates_on_dir_mtime_change(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)
    v = tmp_path / "vid.avi"; v.write_bytes(b"")
    _seed_companion_h5(tmp_path, v.stem)

    calls = {"n": 0}
    real_build = vw._build_dir_with_h5
    def spy(d, mtime):
        calls["n"] += 1
        return real_build(d, mtime)
    monkeypatch.setattr(vw, "_build_dir_with_h5", spy)

    client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    # Touch a new file to bump dir mtime.
    import time as _time
    _time.sleep(1.1)  # mtime granularity = 1s on some FS
    (tmp_path / "newfile.txt").write_text("x")
    client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    assert calls["n"] == 2, "dir mtime change must invalidate cache"


def test_dir_with_h5_invalidates_on_postproc_run(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)
    v = tmp_path / "vid.avi"; v.write_bytes(b"")
    _seed_companion_h5(tmp_path, v.stem)

    calls = {"n": 0}
    real_build = vw._build_dir_with_h5
    def spy(d, mtime):
        calls["n"] += 1
        return real_build(d, mtime)
    monkeypatch.setattr(vw, "_build_dir_with_h5", spy)

    client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    import time as _time
    _time.sleep(1.1)
    _seed_postproc_run(tmp_path, "20260502-130000", "filterpredictions", v.stem)
    client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    assert calls["n"] == 2, "new postproc/<ts>_*/ must invalidate cache"


def test_dir_with_h5_404_on_missing_path(flask_test_client, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: True)
    resp = client.get("/dlc/viewer/dir-with-h5?path=/nonexistent/dir")
    assert resp.status_code == 404


def test_dir_with_h5_403_on_disallowed_path(flask_test_client, tmp_path, monkeypatch):
    client, _app, _redis, _data, _user = flask_test_client
    _auth(client)
    from dlc import viewer as vw
    monkeypatch.setattr(vw, "_viewer_sec_check", lambda p: False)
    resp = client.get(f"/dlc/viewer/dir-with-h5?path={tmp_path}")
    assert resp.status_code == 403
```

The helpers `_seed_companion_h5` and `_seed_postproc_run` already exist in this file from the earlier viewer-layers work — reuse them.

- [ ] **Step 2: Run; confirm RED**

```bash
python -m pytest tests/test_dlc_viewer_routes.py -k dir_with_h5 -v
```

Expected: 6 FAILED with 404 / `AttributeError: module 'dlc.viewer' has no attribute '_build_dir_with_h5'`.

- [ ] **Step 3: Add helpers + route to `src/dlc/viewer.py`**

Place near `viewer_h5_variants` (around line 617). Add `_VIEWER_VIDEO_EXTS` near other module-level constants (e.g., after `_VIEWER_H5_CACHE_MAX` at line 63).

Module-level constant:

```python
_VIEWER_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".mpg", ".mpeg"}
```

Helpers + route:

```python
def _viewer_dir_mtime(d: Path) -> float:
    """Composite mtime that captures any change affecting the dir's listing.

    Captures: dir itself (new/deleted videos), postproc/ root (new run dirs),
    and each postproc/<ts>_*/ run dir (sidecar updates).
    """
    candidates = [d.stat().st_mtime]
    pp = d / "postproc"
    if pp.is_dir():
        try:
            candidates.append(pp.stat().st_mtime)
        except OSError:
            pass
        try:
            for child in pp.iterdir():
                if child.is_dir():
                    try:
                        candidates.append(child.stat().st_mtime)
                    except OSError:
                        pass
        except OSError:
            pass
    return max(candidates)


def _build_dir_with_h5(d: Path, mtime: float) -> dict:
    """Walk a single directory and annotate videos with h5 counts.

    Pure-filesystem; no Redis, no Flask. Caller is responsible for caching.
    """
    parent = str(d.parent) if d.parent != d else None
    dirs: list[dict] = []
    videos: list[dict] = []
    h5_stems_by_video: dict[str, list[str]] = {}

    def _record_h5(h5_path: Path):
        # Companion convention: <video_stem>DLC_<scorer>...{_filtered|_refined}?.h5
        # The h5 stem starts with the video stem. First match wins.
        for vstem in list(h5_stems_by_video.keys()):
            if h5_path.stem.startswith(vstem):
                h5_stems_by_video[vstem].append(str(h5_path))
                return

    # Pass 1: dirs + videos in <d>.
    for entry in sorted(d.iterdir()):
        if entry.is_dir():
            if entry.name == "postproc":
                continue
            dirs.append({"name": entry.name})
        elif entry.is_file() and entry.suffix.lower() in _VIEWER_VIDEO_EXTS:
            h5_stems_by_video[entry.stem] = []
            videos.append({"name": entry.name, "stem": entry.stem})

    # Pass 2: companion h5 in <d> (excludes _filtered/_refined).
    for entry in d.iterdir():
        if entry.is_file() and entry.suffix.lower() == ".h5":
            n = entry.name.lower()
            if "_filtered" in n or "_refined" in n:
                continue
            _record_h5(entry)

    # Pass 3: postproc/<ts>_<tag>/<vstem>...h5
    pp = d / "postproc"
    if pp.is_dir():
        for run_dir in pp.iterdir():
            if not run_dir.is_dir():
                continue
            for h5 in run_dir.glob("*.h5"):
                _record_h5(h5)

    for v in videos:
        h5s = h5_stems_by_video.get(v["stem"], [])
        v["has_h5"]   = bool(h5s)
        v["h5_count"] = len(h5s)
        v.pop("stem", None)

    return {
        "path":   str(d),
        "parent": parent,
        "dirs":   dirs,
        "videos": videos,
        "mtime":  mtime,
    }


@bp.route("/dlc/viewer/dir-with-h5")
def viewer_dir_with_h5():
    """Browse-list helper: every video in <dir> annotated with h5 count.

    Query: ?path=<abs-dir>
    Cached in Redis at viewer:dir_h5:<abs-dir> with composite-mtime invalidation.
    """
    raw = request.args.get("path", "").strip()
    if not raw:
        return jsonify({"error": "path required"}), 400
    d = Path(raw)
    if not d.is_dir():
        return jsonify({"error": f"not a dir: {raw}"}), 404
    if not _viewer_sec_check(d):
        return jsonify({"error": "Access denied."}), 403

    cur_mtime = _viewer_dir_mtime(d)
    redis_key = f"viewer:dir_h5:{d}"
    try:
        redis = _ctx.redis_client()
    except Exception:
        redis = None

    if redis is not None:
        try:
            raw_cached = redis.get(redis_key)
            if raw_cached:
                cached = _json.loads(raw_cached)
                if cached.get("mtime") == cur_mtime:
                    return jsonify(cached)
        except (TypeError, ValueError, Exception):
            pass  # fall through and rebuild

    payload = _build_dir_with_h5(d, cur_mtime)
    if redis is not None:
        try:
            redis.setex(redis_key, 86400, _json.dumps(payload))
        except Exception:
            pass  # cache write best-effort
    return jsonify(payload)
```

- [ ] **Step 4: Run; confirm GREEN**

```bash
python -m pytest tests/test_dlc_viewer_routes.py -k dir_with_h5 -v
```

Expected: 6 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/dlc/viewer.py tests/test_dlc_viewer_routes.py
git commit -m "feat(viewer): add /dlc/viewer/dir-with-h5 route with redis mtime cache"
```

---

## Task 2: Browse-tab UI — `Hide videos without h5` toggle + h5-count badges

**Files:**
- Modify: `src/templates/partials/card_viewer.html`.
- Modify: `src/static/js/state.js`.
- Modify: `src/static/js/viewer.js` (rewrite `_vaRefreshBrowse`).

- [ ] **Step 1: Add `vaBrowseHideNoH5` to `state.js`**

In `src/static/js/state.js`, add the field inside the existing `state` object:

```js
export const state = {
  sessionPollTimer: null,
  dlcBrowsePath: null,
  dlcEngine: "pytorch",
  dlcTrainingActive: false,
  currentRoot: "",
  userDataDir: null,
  dataDir: null,
  currentProjectId: "",
  pollTimer: null,
  vaBrowseHideNoH5: true,   // ← new
};
```

- [ ] **Step 2: Add the toggle to the Browse tab partial**

In `src/templates/partials/card_viewer.html`, find the Browse tab panel (`<div id="va-tab-browse-panel" class="hidden">`). Just inside it (above the existing breadcrumb input row), insert:

```html
<label style="display:flex;align-items:center;gap:.4rem;font-size:.73rem;color:var(--text-dim);margin-bottom:.3rem;cursor:pointer">
  <input type="checkbox" id="va-browse-hide-no-h5" checked
         style="accent-color:var(--accent);width:13px;height:13px"/>
  Hide videos without h5
</label>
```

- [ ] **Step 3: Replace `_vaRefreshBrowse` body in `viewer.js`**

In `src/static/js/viewer.js`, locate `async function _vaRefreshBrowse(path) {` (around line 383). Replace the entire function body with:

```js
async function _vaRefreshBrowse(path) {
  _vaBrowsePath = path;
  vaBrowseBreadcrumb.value = path;
  vaBrowseList.innerHTML = '<p class="explorer-empty">Loading…</p>';

  // Try the new dir-with-h5 endpoint; fall back to /fs/ls on failure.
  let data;
  try {
    const res = await fetch(`/dlc/viewer/dir-with-h5?path=${encodeURIComponent(path)}`);
    if (!res.ok) throw new Error(`status ${res.status}`);
    data = await res.json();
    if (data.error) throw new Error(data.error);
  } catch (newRouteErr) {
    // Fallback: legacy /fs/ls. Treat every video as has_h5=false (we can't tell).
    try {
      const res2 = await fetch(`/fs/ls?path=${encodeURIComponent(path)}`);
      const d2   = await res2.json();
      if (d2.error) { vaBrowseList.innerHTML = `<p class="explorer-empty">${d2.error}</p>`; return; }
      const entries = d2.entries || [];
      data = {
        path,
        dirs:   entries.filter(e => e.type === "dir").map(e => ({name: e.name})),
        videos: entries
          .filter(e => e.type === "file" && _VA_VIDEO_EXTS.has(e.name.slice(e.name.lastIndexOf(".")).toLowerCase()))
          .map(e => ({name: e.name, has_h5: false, h5_count: 0})),
      };
    } catch (fbErr) {
      vaBrowseList.innerHTML = `<p class="explorer-empty">Error: ${fbErr.message}</p>`;
      return;
    }
  }

  const dirs = data.dirs || [];
  const videos = data.videos || [];
  const hideNoH5 = !!state.vaBrowseHideNoH5;
  const visibleVideos = hideNoH5 ? videos.filter(v => v.has_h5) : videos;

  if (!dirs.length && !visibleVideos.length) {
    vaBrowseList.innerHTML = hideNoH5
      ? '<p class="explorer-empty">No videos with analyzed h5 here. Untick "Hide videos without h5" to show all.</p>'
      : '<p class="explorer-empty">No folders or videos found here.</p>';
    return;
  }

  vaBrowseList.innerHTML = "";

  dirs.forEach(d => {
    const row = document.createElement("div");
    row.className = "fe-video-item";
    row.style.cursor = "pointer";
    row.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>`;
    row.querySelector("span").textContent = d.name + "/";
    row.addEventListener("click", () => _vaRefreshBrowse(path + "/" + d.name));
    vaBrowseList.appendChild(row);
  });

  visibleVideos.forEach(v => {
    const fullPath = path + "/" + v.name;
    const row = document.createElement("div");
    row.className = "fe-video-item";
    row.style.cursor = "pointer";
    row.dataset.hasH5 = v.has_h5 ? "true" : "false";
    const iconOpacity = v.has_h5 ? "1" : "0.45";
    const badge = v.has_h5
      ? `<span style="font-size:.68rem;color:var(--text-dim);margin-left:auto;padding:.05rem .35rem;background:var(--surface);border:1px solid var(--border);border-radius:8px">${v.h5_count} h5</span>`
      : `<span style="font-size:.68rem;color:var(--text-dim);margin-left:auto;font-style:italic">no h5</span>`;
    row.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:${iconOpacity}"><rect x="2" y="2" width="20" height="20" rx="3"/><polygon points="10 8 16 12 10 16 10 8" fill="currentColor" stroke="none"/></svg><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;opacity:${iconOpacity === "1" ? "1" : "0.7"}"></span>${badge}`;
    row.querySelector("span").textContent = v.name;
    row.addEventListener("click", () => _vaOpenBrowseVideo(fullPath, v.name));
    vaBrowseList.appendChild(row);
  });
}
```

- [ ] **Step 4: Wire the toggle change handler**

Near where the other Browse-tab elements register listeners (search for `vaTabBrowse?.addEventListener`), add:

```js
const vaBrowseHideNoH5 = document.getElementById("va-browse-hide-no-h5");
vaBrowseHideNoH5?.addEventListener("change", () => {
  state.vaBrowseHideNoH5 = !!vaBrowseHideNoH5.checked;
  if (_vaBrowsePath) _vaRefreshBrowse(_vaBrowsePath);
});
// On startup, sync the checkbox to state (state.vaBrowseHideNoH5 defaults true).
if (vaBrowseHideNoH5) vaBrowseHideNoH5.checked = !!state.vaBrowseHideNoH5;
```

- [ ] **Step 5: Restart flask and visually verify**

```bash
docker compose restart flask
```

Then headless verify with Playwright (no h5 are needed for the smoke; we just check the toggle wires up):

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width":1500,"height":1100})
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://localhost:5000/?token=deeplabcut")
    pg.wait_for_load_state("networkidle")
    pg.evaluate("document.getElementById('view-analyzed-card').classList.remove('hidden')")
    has_toggle = pg.evaluate("!!document.getElementById('va-browse-hide-no-h5')")
    print("toggle present:", has_toggle, "errors:", errs)
    b.close()
```

Expected: `toggle present: True`, `errors: []`.

- [ ] **Step 6: Commit**

```bash
git add src/static/js/state.js src/templates/partials/card_viewer.html src/static/js/viewer.js
git commit -m "feat(viewer): browse-tab Hide-videos-without-h5 toggle + h5-count badges"
```

---

## Task 3: Auto-latest primary, primary-swap clears comparisons, add-comparison empty hint

**Files:**
- Modify: `src/templates/partials/card_viewer.html` (add the empty-hint span).
- Modify: `src/static/js/viewer.js`.

- [ ] **Step 1: Add the empty-hint span to the partial**

In `src/templates/partials/card_viewer.html`, find the `va-overlay-add-compare` `<select>`. Immediately after its closing tag (still inside the same `<label>` row), add:

```html
<span id="va-overlay-add-compare-empty-hint" class="hidden"
      style="margin-left:auto;font-size:.7rem;color:var(--text-dim);font-style:italic">
  no other variants for this video
</span>
```

- [ ] **Step 2: Replace the default-selection logic in `_vaDiscoverVariants`**

In `src/static/js/viewer.js`, find `_vaDiscoverVariants`. Locate the existing line:

```js
const defaultEntry = data.variants.find(v => v.type === "raw" && !v.disabled)
                  || data.variants.find(v => !v.disabled);
```

Replace with:

```js
const defaultEntry = _vaPickBestPrimary(data.variants);
```

And add this helper near the other `_va*` helpers (e.g., right above `_vaDiscoverVariants`):

```js
function _vaPickBestPrimary(variants) {
  // Newest variant by ts wins. Raw companion has ts=null and is the
  // fallback when no dated variants exist.
  const dated = (variants || []).filter(v => !v.disabled && v.ts);
  if (dated.length) {
    return dated.reduce((a, b) => (a.ts > b.ts ? a : b));
  }
  return (variants || []).find(v => !v.disabled) || null;
}
```

- [ ] **Step 3: Rewrite `_vaApplyPrimaryFromSelect` to clear comparisons + refresh dropdown**

Find the existing `async function _vaApplyPrimaryFromSelect()` and replace it with:

```js
async function _vaApplyPrimaryFromSelect() {
  const select = document.getElementById("va-overlay-primary-select");
  if (!select) return;
  const path  = select.value;
  if (!path) return;
  const opt   = select.options[select.selectedIndex];
  const label = opt?.dataset.label || path.split("/").pop();
  const type  = opt?.dataset.type  || "raw";

  // Primary swap = fresh slate. Drop every comparison layer.
  _vaLayers.length = 0;
  const layer = _vaMakeLayer({ path, label, type });
  _vaSetPrimaryLayer(layer);
  document.getElementById("va-overlay-h5-path").value = path;
  await _vaLoadLayerInfo(layer);
  await _vaLoadEditCacheForPrimary();
  _vaRenderCompareRows();
  _vaRefreshAddComparisonOptions(_vaLastVariants);
  _vaRenderPrimaryThresholdInline();
  if (_vaOverlayEnabled) _vaLoadFrame(_vaCurrentFrame);
}
```

- [ ] **Step 4: Rewrite `_vaRefreshAddComparisonOptions` to toggle the empty hint**

Replace the existing `_vaRefreshAddComparisonOptions` body with:

```js
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

- [ ] **Step 5: Restart flask and smoke**

```bash
docker compose restart flask
```

Playwright load:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width":1500,"height":1100})
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://localhost:5000/?token=deeplabcut")
    pg.wait_for_load_state("networkidle")
    print("errors:", errs)
    b.close()
```

Expected: 0 page errors.

- [ ] **Step 6: Commit**

```bash
git add src/templates/partials/card_viewer.html src/static/js/viewer.js
git commit -m "fix(viewer): auto-latest primary, primary-swap clears comparisons, add-compare empty hint"
```

---

## Task 4: Per-frame paint barrier + frame-step input

**Files:**
- Modify: `src/templates/partials/card_viewer.html` (add the `va-play-step` input).
- Modify: `src/static/js/viewer.js`.

- [ ] **Step 1: Add `va-play-step` to the partial**

In `src/templates/partials/card_viewer.html`, find the existing `<input type="number" id="va-skip-n" …>` (around line 168). Insert this `<label>` block immediately before that skip-N control (so the player's controls remain grouped):

```html
<label style="display:flex;align-items:center;gap:.3rem;font-size:.75rem;color:var(--text-dim);white-space:nowrap"
       title="Play every N frames (1 = every frame, 2 = every other, …)">
  step
  <input type="number" id="va-play-step" value="1" min="1" max="100" step="1"
         style="width:46px;text-align:center;font-family:var(--mono);font-size:.78rem;padding:.18rem .3rem;background:var(--surface-2);border:1px solid var(--border);border-radius:5px;color:var(--text)">
</label>
```

- [ ] **Step 2: Add `_vaPlayStep` and the paint barrier in `viewer.js`**

In `src/static/js/viewer.js`, find `_vaLoadFrame(n)`. Locate where it currently calls `_vaDrawCurrentFrame()` followed (eventually) by `_vaPrefetchPoseWindow(n + 1)`. After the `_vaDrawCurrentFrame()` call and BEFORE the prefetch line, insert:

```js
// Barrier: wait for the browser to paint before resolving so the play
// loop never advances mid-render.
await new Promise(requestAnimationFrame);
```

Add `_vaPlayStep` somewhere near the other `_va*` helpers (e.g., next to `_vaPickBestPrimary`):

```js
function _vaPlayStep() {
  const v = parseInt(document.getElementById("va-play-step")?.value || "1", 10);
  return Math.max(1, Math.min(100, isNaN(v) ? 1 : v));
}
```

Find the play-loop tick (search for `_vaPlayTimeoutId = setTimeout(_vaPlayLoop, delay)` around line 1639). Inside `_vaPlayLoop`, locate where the next frame is computed (it currently advances `_vaCurrentFrame + 1`). Replace that arithmetic with:

```js
const nextFrame = _vaCurrentFrame + _vaPlayStep();
```

If the existing code references `_vaCurrentFrame + 1` in multiple places inside the play loop, update each. The end-of-video guard (`if (nextFrame >= _vaFrameCount)`) keeps its existing logic — just compare against the new `nextFrame`.

- [ ] **Step 3: Restart flask and smoke**

```bash
docker compose restart flask
```

Playwright:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width":1500,"height":1100})
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://localhost:5000/?token=deeplabcut")
    pg.wait_for_load_state("networkidle")
    has_step = pg.evaluate("!!document.getElementById('va-play-step')")
    print("step input present:", has_step, "errors:", errs)
    b.close()
```

Expected: `step input present: True`, `errors: []`.

- [ ] **Step 4: Commit**

```bash
git add src/templates/partials/card_viewer.html src/static/js/viewer.js
git commit -m "feat(viewer): per-frame paint barrier + play-every-N-frames step input"
```

---

## Task 5: Real-data integration test for `/dir-with-h5` (OM-2 RatBox)

**Files:**
- Modify: `tests/test_postprocess_real_project.py` (append).

- [ ] **Step 1: Append the test**

```python
@pytest.mark.skipif(not _OM2_HOST.is_dir(),
                    reason="OM-2 RatBox folder not on this host")
def test_dir_with_h5_against_om2_ratbox(flask_test_client):
    """Run /dlc/viewer/dir-with-h5 against the OM-2 RatBox folder; expect at
    least one .avi to have has_h5: true."""
    client, _app, _redis, _data, _user = flask_test_client
    # Translate host path to container path (route runs in flask).
    container_dir = str(_OM2_HOST).replace(
        "/home/sam/synology/Parra-Lab-Data",
        "/user-data/Parra-Data/Cloud",
    )
    resp = client.get(f"/dlc/viewer/dir-with-h5?path={container_dir}")
    if resp.status_code in (403, 404):
        pytest.skip(
            f"Path not visible to host pytest "
            f"({resp.status_code}); test requires the container path to "
            f"resolve from the test environment."
        )
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["videos"], "expected at least one video"
    with_h5 = [v for v in data["videos"] if v["has_h5"]]
    assert with_h5, f"expected at least one .avi with has_h5=True, got {data['videos'][:3]}"
    for v in with_h5:
        assert v["h5_count"] >= 1
```

`_OM2_HOST` is already defined in this file by the earlier viewer-layers task. Reuse it.

- [ ] **Step 2: Run on host**

```bash
python -m pytest tests/test_postprocess_real_project.py::test_dir_with_h5_against_om2_ratbox -v
```

Expected: PASSED or SKIPPED (skip is fine on hosts without the synology mount).

- [ ] **Step 3: Commit**

```bash
git add tests/test_postprocess_real_project.py
git commit -m "test(viewer): real-data integration test for /dir-with-h5 on OM-2 RatBox"
```

---

## Task 6: UI isolation tests — extend with new IDs + JS-source assertions

**Files:**
- Modify: `tests/test_viewer_layers_ui_isolation.py`.

- [ ] **Step 1: Append the new assertions**

Append to `tests/test_viewer_layers_ui_isolation.py`:

```python
NEW_FLOW_IDS = {
    "va-browse-hide-no-h5",
    "va-overlay-add-compare-empty-hint",
    "va-play-step",
}


def test_new_flow_ids_present_and_unique():
    seen_global: dict[str, int] = {}
    for f in PARTIALS.glob("*.html"):
        for m in re.finditer(r'id="([^"]+)"', f.read_text()):
            seen_global[m.group(1)] = seen_global.get(m.group(1), 0) + 1
    for nid in NEW_FLOW_IDS:
        assert seen_global.get(nid, 0) == 1, (
            f"id {nid!r} appears {seen_global.get(nid, 0)} times across partials"
        )


def test_viewer_js_uses_dir_with_h5_route():
    js = VIEWER_JS.read_text()
    assert "/dlc/viewer/dir-with-h5" in js, (
        "viewer.js must consume /dir-with-h5 instead of /fs/ls for the Browse list"
    )


def test_viewer_js_pick_best_primary_helper_present():
    js = VIEWER_JS.read_text()
    assert "_vaPickBestPrimary" in js, (
        "auto-latest selection helper _vaPickBestPrimary must be defined"
    )


def test_viewer_js_primary_swap_clears_layers():
    """Regression: primary swap must reset _vaLayers, not just push a new primary."""
    js = VIEWER_JS.read_text()
    # The new _vaApplyPrimaryFromSelect contains "_vaLayers.length = 0".
    assert "_vaLayers.length = 0" in js, (
        "_vaApplyPrimaryFromSelect must explicitly empty _vaLayers before "
        "pushing the new primary"
    )


def test_viewer_js_paint_barrier_present():
    """Regression: _vaLoadFrame must await an rAF barrier so the play loop
    never advances mid-render."""
    js = VIEWER_JS.read_text()
    assert "new Promise(requestAnimationFrame)" in js, (
        "_vaLoadFrame must await `new Promise(requestAnimationFrame)` before "
        "the prefetch step"
    )


def test_viewer_js_play_step_helper_present():
    js = VIEWER_JS.read_text()
    assert "_vaPlayStep" in js, "_vaPlayStep helper must exist"
    assert "va-play-step" in js, "viewer.js must read the va-play-step input"
```

- [ ] **Step 2: Run; confirm pass**

```bash
python -m pytest tests/test_viewer_layers_ui_isolation.py -v
```

Expected: all 11 (5 existing + 6 new) PASSED.

- [ ] **Step 3: Commit**

```bash
git add tests/test_viewer_layers_ui_isolation.py
git commit -m "test(viewer): UI-isolation assertions for flow improvements"
```

---

## Task 7: Playwright e2e — auto-latest, browse filter, add-compare empty hint, frame-step

**Files:**
- Modify: `tests/e2e_viewer_layers_smoke.py`.

- [ ] **Step 1: Read the existing script structure**

Read `tests/e2e_viewer_layers_smoke.py`. Note where the Phase A/B/C/D blocks live; the new assertions are appended as Phases E (browse filter), F (auto-latest primary), G (add-compare empty hint), H (frame-step).

- [ ] **Step 2: Add the four new phases**

Append, immediately BEFORE `browser.close()` and the `print("\nALL CHECKS PASSED")` line:

```python
        # ── Phase E: Browse filter toggle ────────────────────────────
        page.click("#va-tab-browse")
        time.sleep(0.5)
        # Untick "Hide videos without h5" → at least one row may appear with
        # data-has-h5="false" (if the dir has any h5-less videos).
        page.click("#va-browse-hide-no-h5")  # untick
        time.sleep(1.5)
        no_h5_rows = page.evaluate(
            "() => document.querySelectorAll('#va-browse-list [data-has-h5=\"false\"]').length"
        )
        with_h5_rows = page.evaluate(
            "() => document.querySelectorAll('#va-browse-list [data-has-h5=\"true\"]').length"
        )
        print(f"[E] rows with h5: {with_h5_rows}, without h5: {no_h5_rows}")
        # At least one row with has_h5=true must exist (the OM-2 dir has analyzed videos).
        assert with_h5_rows >= 1, "expected at least one video with h5"
        # Re-tick to restore default.
        page.click("#va-browse-hide-no-h5")
        time.sleep(0.5)

        # ── Phase F: Auto-latest primary ─────────────────────────────
        # Re-load the same OM-2 video and confirm the Primary <select>'s
        # default selection prefers the newest dated variant over Raw, when
        # one exists.
        primary_default_label = page.evaluate(
            "() => { const s = document.getElementById('va-overlay-primary-select');"
            "  return s ? s.options[s.selectedIndex]?.textContent : null; }"
        )
        print(f"[F] primary default label: {primary_default_label!r}")
        # If at least 2 primary options exist (raw + filtered), the default
        # MUST be the dated one (label starts with 'filtered @' or 'refine_').
        if primary_options and len(primary_options) >= 2 and primary_default_label:
            assert (primary_default_label.startswith("filtered @") or
                    primary_default_label.startswith("refine_")), (
                f"expected newest dated variant as default, got {primary_default_label!r}"
            )

        # ── Phase G: Add-compare empty hint ──────────────────────────
        # Add the only available comparison; the dropdown should now be hidden
        # and the empty-hint span should be visible.
        if compare_options:
            page.select_option("#va-overlay-add-compare", value=compare_options[0])
            time.sleep(0.4)
            hint_visible = page.evaluate(
                "() => { const e = document.getElementById('va-overlay-add-compare-empty-hint');"
                "  return e && !e.classList.contains('hidden'); }"
            )
            select_hidden = page.evaluate(
                "() => document.getElementById('va-overlay-add-compare').classList.contains('hidden')"
            )
            print(f"[G] empty-hint visible: {hint_visible}, dropdown hidden: {select_hidden}")
            assert hint_visible and select_hidden, (
                "after taking the only compare option, hint must show + dropdown must hide"
            )

        # ── Phase H: Frame-step (play every N frames) ────────────────
        # Set step=5, play briefly, pause, verify _vaCurrentFrame advanced
        # by more than 1 (proxy for "step worked").
        before = page.evaluate("() => window._vaCurrentFrame ?? null")
        # _vaCurrentFrame is module-scoped inside an IIFE — not on window.
        # Instead read the visible frame counter span.
        before = page.text_content("#va-frame-counter") or ""
        page.fill("#va-play-step", "5")
        page.click("#va-btn-play")
        time.sleep(1.2)
        page.click("#va-btn-play")  # pause
        after = page.text_content("#va-frame-counter") or ""
        print(f"[H] frame counter: before={before!r} after={after!r}")
        # The counter looks like "Frame N / M". Parse N out of each.
        import re as _re
        m1 = _re.search(r"Frame\s+(\d+)", before or "")
        m2 = _re.search(r"Frame\s+(\d+)", after  or "")
        if m1 and m2:
            advanced = int(m2.group(1)) - int(m1.group(1))
            print(f"[H] frames advanced: {advanced}")
            assert advanced >= 4, (
                f"with step=5 and ~1.2s of playback, expected ≥ 4 frame advance, got {advanced}"
            )
```

- [ ] **Step 3: Run the e2e**

```bash
python tests/e2e_viewer_layers_smoke.py 2>&1 | tail -40
```

Expected: `ALL CHECKS PASSED` on a host with the OM-2 folder. If frame-step Phase H is too racy on slower hardware, increase the `time.sleep(1.2)` to 2.0.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e_viewer_layers_smoke.py
git commit -m "test(viewer): e2e assertions for browse filter, auto-latest, empty hint, frame-step"
```

---

## Task 8: Final verification

- [ ] **Step 1: Full pytest pass on the host**

```bash
python -m pytest \
  tests/test_dlc_viewer_routes.py \
  tests/test_viewer_layers_ui_isolation.py \
  tests/test_postprocess_real_project.py \
  tests/test_frontend_assets.py \
  -v 2>&1 | tail -10
```

All non-skipped tests must pass. The `flask_test_client` teardown LookupError noise is acceptable as before.

- [ ] **Step 2: Re-run the post-process e2e to confirm no regression**

```bash
python tests/e2e_postprocess_smoke.py 2>&1 | tail -15
```

Expected: `ALL CHECKS PASSED`.

- [ ] **Step 3: Re-run the viewer-layers e2e (now with the new flow improvements)**

```bash
python tests/e2e_viewer_layers_smoke.py 2>&1 | tail -25
```

Expected: `ALL CHECKS PASSED`.

- [ ] **Step 4: Manual browser sanity**

Per CLAUDE.md, exercise in a real browser:
1. Open the View Analyzed Videos / Frames card → Browse tab.
2. Confirm the "Hide videos without h5" checkbox is present and ticked by default. Untick → h5-less videos appear with a greyed icon and "no h5" badge.
3. Click a video that has both Raw + a postproc filtered output. Confirm Primary defaults to `filtered @ HH:MM:SS`, not `Raw — …`.
4. Add a comparison via the "+ add comparison" dropdown. Verify markers from BOTH layers appear on the canvas with different shapes.
5. Switch Primary via the dropdown. Verify ALL comparison layers disappear (compare list emptied; dropdown re-populated with the previous primary as a candidate).
6. With only one variant in the dropdown, take it as a comparison. Verify the dropdown disappears and the "no other variants for this video" hint shows.
7. Set `step` to 5, click Play. Confirm playback skips by 5 frames per tick. Set back to 1 → normal playback.

If any step fails, file as a follow-up — the unit + e2e tests should already have caught the obvious issues.

- [ ] **Step 5: Final commit if anything else changed**

```bash
git status
# Only if there are incidental fixes:
# git add -A && git commit -m "chore(viewer): final cleanups"
```

---

## Self-Review

**Spec coverage check:**

| Spec section | Implemented in task |
|---|---|
| Browse list filters videos with h5 + toggle to include all | T2 |
| h5-count badges on rows | T2 |
| Auto-load latest variant by ts as Primary | T3 |
| Primary swap clears comparisons | T3 |
| Add-comparison empty-hint replaces silent dropdown | T3 |
| `requestAnimationFrame` paint barrier | T4 |
| Frame-step input (`va-play-step`) | T4 |
| Redis-backed dir-with-h5 cache | T1 |
| Composite-mtime invalidation | T1 |
| `/dir-with-h5` route | T1 |
| Real-data integration test (OM-2) | T5 |
| UI isolation tests for new IDs + JS asserts | T6 |
| Playwright e2e extended | T7 |
| Final verification | T8 |

**Type/name consistency:** `_vaPickBestPrimary(variants)`, `_vaApplyPrimaryFromSelect()`, `_vaRefreshAddComparisonOptions(variants)`, `_vaPlayStep()`, `_vaRefreshBrowse(path)`, `_VIEWER_VIDEO_EXTS`, `_viewer_dir_mtime(d)`, `_build_dir_with_h5(d, mtime)`, `viewer_dir_with_h5()` — all consistent across tasks.

**Placeholder scan:** No "TBD"/"add error handling"/"similar to Task X" violations. The "Open items" list in the spec was research-shaped, not placeholder-shaped.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-02-viewer-flow-improvements.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session with checkpoints.

Which approach?
