# Viewer Flow Improvements — Design

**Date:** 2026-05-02
**Branch:** `feat/posture-match-refiner` (continues from the viewer-postproc-layers work)
**Status:** Approved (brainstorm); pending implementation plan

## Problem

The View Analyzed Videos / Frames flow has three usability gaps surfaced by real use:

1. **Browse list is noisy.** The current Browse tab shows every `.mp4`/`.avi` in a directory regardless of whether there's an h5 to overlay. Users have to click around to find videos that actually have analyzed predictions.
2. **Default primary is "Raw companion," not the newest variant.** When the user runs post-process on a video, the resulting `*_filtered.h5` is what they want to inspect first. Today they have to manually pick it from the Primary `<select>`.
3. **The "+ add comparison" dropdown does nothing.** When the user changes the Primary, the add-compare dropdown is not refreshed, leaving stale options that look broken.

Two adjacent capabilities are also missing:

4. **Per-frame render gate.** During playback, comparison-layer markers can lag the primary because the play loop advances before all layers' poses are drawn.
5. **No frame-step.** The player always advances one frame per tick. Other players in this stack (clip-cutter, dlc-3d) expose a "play every N frames" input; the viewer should match.

A directory walk on every Browse-tab navigation is also wasteful — postproc-aware analyzed-h5 discovery scans `<dir>` + `<dir>/postproc/*/` for every dir-view. We have Redis already; cache it.

## Goals

- Browse list defaults to **only videos that have ≥ 1 analyzable h5**, with a toggle to include videos that don't (greyed out + "no h5" badge). Each h5-bearing video shows its h5 count as a small inline badge.
- When a video loads, the **most recent variant by `ts` becomes Primary**, regardless of type. Comparison layers start empty.
- Switching Primary via the `<select>` **clears all active comparison layers** and re-populates the add-comparison dropdown. The empty state is now an inline hint, not a silent dropdown.
- The play loop **awaits a paint barrier** after `_vaDrawCurrentFrame()` so every visible layer's markers for frame N are on screen before frame N+1 starts loading.
- A **frame-step input** (`va-play-step`) controls the play loop's per-tick advance (default 1; clamped to `[1, 100]`).
- A **Redis-backed cache** at `viewer:dir_h5:<abs_dir>` answers Browse-list requests in O(1) for unchanged dirs. Invalidation is automatic via composite mtime (`<dir>` + `<dir>/postproc/` + each `postproc/<ts>_*/`).

## Non-Goals

- Background warming of the cache (lazy only; no app-start scan).
- A user-facing "rescan" button (rely on mtime invalidation + 24h TTL).
- Per-user cache scoping (allowlist already gates access; the cache is keyed per-dir, shared across users).
- Frame-step per layer (one global step for the player).
- Auto-populating "most recent of each type" as comparisons (Q2 = A: comparisons start empty).
- Skeleton lines / per-bodypart visibility per layer (unchanged from prior viewer work).
- Editing on comparison layers (still single-variant only).

## UI

### Browse tab — filter by h5 presence

`src/templates/partials/card_viewer.html`. New checkbox above the breadcrumb input inside the Browse tab panel:

```html
<label style="display:flex;align-items:center;gap:.4rem;font-size:.73rem;color:var(--text-dim);margin-bottom:.3rem;cursor:pointer">
  <input type="checkbox" id="va-browse-hide-no-h5" checked
         style="accent-color:var(--accent);width:13px;height:13px"/>
  Hide videos without h5
</label>
```

The Browse list rendering (currently `_vaRefreshBrowse` in `viewer.js`) switches from `/fs/ls` to `/dlc/viewer/dir-with-h5` and applies the toggle:

- **Checked (default):** filter out `videos[i].has_h5 === false`.
- **Unchecked:** show every video. Videos with `has_h5 === false` get:
  - Inline badge `no h5`, muted colour.
  - The video icon SVG drops to `opacity: 0.45`.
  - The row is still clickable; the user can load the video, but the overlay picker will be empty.
- **Videos with h5** show an inline badge `<count> h5` (e.g., `2 h5`). Quick visual signal of richness.
- **Folders** are always shown regardless of toggle.

The toggle's checked state is persisted in `state.vaBrowseHideNoH5` (the existing shared state object) so it survives Browse-tab navigation within the session.

Each rendered video row also gains a `data-has-h5` attribute for testability:

```html
<div class="fe-video-item" data-has-h5="true|false" …>…</div>
```

### Auto-latest Primary, primary-swap, add-comparison empty hint

When a video opens (Project Content tab OR Browse tab), `_vaDiscoverVariants(videoPath)` populates the Primary `<select>` and selects the newest-by-`ts` non-disabled entry. Comparisons start empty.

**Selection logic** (replaces today's "first raw or first non-disabled"):

```js
const bestByTs = (variants) => {
  const dated = variants.filter(v => !v.disabled && v.ts);
  if (dated.length) {
    return dated.reduce((a, b) => (a.ts > b.ts ? a : b));
  }
  return variants.find(v => !v.disabled) || null;
};
```

If no dated variants exist (e.g., only the Raw companion), Raw wins and today's behaviour is preserved.

**Primary swap clears comparisons.** `_vaApplyPrimaryFromSelect` resets `_vaLayers` to `[primary]`, re-renders the compare list (now empty), and refreshes the add-comparison dropdown:

```js
async function _vaApplyPrimaryFromSelect() {
  const select = document.getElementById("va-overlay-primary-select");
  if (!select) return;
  const path  = select.value;
  if (!path) return;
  const opt   = select.options[select.selectedIndex];
  const label = opt?.dataset.label || path.split("/").pop();
  const type  = opt?.dataset.type  || "raw";

  _vaLayers.length = 0;                  // drop every comparison
  const layer = _vaMakeLayer({ path, label, type });
  _vaSetPrimaryLayer(layer);
  document.getElementById("va-overlay-h5-path").value = path;
  await _vaLoadLayerInfo(layer);
  await _vaLoadEditCacheForPrimary();
  _vaRenderCompareRows();
  _vaRefreshAddComparisonOptions(_vaLastVariants);   // ← THE BUG FIX
  _vaRenderPrimaryThresholdInline();
  if (_vaOverlayEnabled) _vaLoadFrame(_vaCurrentFrame);
}
```

**Add-comparison empty hint.** The dropdown is hidden when there are zero non-taken variants. An inline hint takes its place:

```html
<span id="va-overlay-add-compare-empty-hint" class="hidden"
      style="margin-left:auto;font-size:.7rem;color:var(--text-dim);font-style:italic">
  no other variants for this video
</span>
```

`_vaRefreshAddComparisonOptions` toggles between the `<select>` and the hint based on whether any non-taken, non-disabled variants exist.

### Player — frame-step input

`src/templates/partials/card_viewer.html`. New number input adjacent to the existing skip-N control in the player section:

```html
<label style="display:flex;align-items:center;gap:.3rem;font-size:.75rem;color:var(--text-dim);white-space:nowrap"
       title="Play every N frames (1 = every frame, 2 = every other, …)">
  step
  <input type="number" id="va-play-step" value="1" min="1" max="100" step="1"
         style="width:46px;text-align:center;font-family:var(--mono);font-size:.78rem;padding:.18rem .3rem;background:var(--surface-2);border:1px solid var(--border);border-radius:5px;color:var(--text)">
</label>
```

The play loop reads this on every tick:

```js
function _vaPlayStep() {
  const v = parseInt(document.getElementById("va-play-step")?.value || "1", 10);
  return Math.max(1, Math.min(100, isNaN(v) ? 1 : v));
}
```

Default = 1. Skip-back / skip-forward buttons (`va-skip-n`, Ctrl+←/→) keep their existing semantics — that's button-driven jump, separate from per-tick step.

### Per-frame render gate

`_vaLoadFrame(n)` already does `await Promise.all(layers.map(l => _vaFetchPosesForFrame(l, n)))` then `_vaDrawCurrentFrame()`. Add a `requestAnimationFrame` paint barrier before resolving so the play loop never advances mid-render:

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
  // Barrier: wait for the browser to paint before resolving so the
  // play loop never advances mid-render.
  await new Promise(requestAnimationFrame);
  _vaPrefetchPoseWindow(n + 1);   // fire-and-forget
}
```

### IDs (for unique-ID assertions)

New: `va-browse-hide-no-h5`, `va-overlay-add-compare-empty-hint`, `va-play-step`.

Retained from prior viewer work (must keep passing the existing assertions): `va-overlay-primary-select`, `va-overlay-compare-list`, `va-overlay-add-compare`, `va-overlay-customize-thresholds`, `va-overlay-edit-disabled-banner`, `va-overlay-compare-block`, plus the Browse-fallback IDs `va-overlay-h5-path`, `va-overlay-h5-browse`, `va-overlay-h5-browser`, `va-overlay-h5-clear`.

## Backend

### New route: `GET /dlc/viewer/dir-with-h5?path=<abs-dir>`

Returns the dir listing pre-annotated for the viewer:

```json
{
  "path":   "/abs/dir",
  "parent": "/abs",
  "dirs":   [{"name": "subA"}, {"name": "subB"}],
  "videos": [
    {"name": "OM-2_..._success.avi", "has_h5": true,  "h5_count": 2},
    {"name": "no-h5-here.mp4",        "has_h5": false, "h5_count": 0}
  ],
  "mtime":  1735000000.123
}
```

`mtime` is the cache invalidation token (composite mtime; see below).

### Redis cache

- Key: `viewer:dir_h5:<abs_dir>` (the `viewer:` prefix avoids collisions with the `webapp:` keys used by the project store).
- Value: JSON blob of the response (including the `mtime` field).
- TTL: 86400 (24h). Defensive against stale entries that mtime check somehow misses.

### Composite mtime

```python
def _viewer_dir_mtime(d: Path) -> float:
    """Composite mtime that captures any change affecting the dir's listing."""
    candidates = [d.stat().st_mtime]
    pp = d / "postproc"
    if pp.is_dir():
        candidates.append(pp.stat().st_mtime)
        for child in pp.iterdir():
            if child.is_dir():
                candidates.append(child.stat().st_mtime)
    return max(candidates)
```

Captures: new video copied in (parent mtime), new postproc run (`postproc/` mtime), updated `run.json` (run dir mtime).

### Route handler skeleton

```python
@bp.route("/dlc/viewer/dir-with-h5")
def viewer_dir_with_h5():
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
    redis = _ctx.redis_client()

    raw_cached = redis.get(redis_key)
    if raw_cached:
        try:
            cached = _json.loads(raw_cached)
            if cached.get("mtime") == cur_mtime:
                return jsonify(cached)
        except (TypeError, ValueError):
            pass  # fall through and rebuild

    payload = _build_dir_with_h5(d, cur_mtime)
    try:
        redis.setex(redis_key, 86400, _json.dumps(payload))
    except Exception:
        pass  # cache write best-effort; still return the payload
    return jsonify(payload)
```

### Builder

```python
_VIEWER_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".mpg", ".mpeg"}


def _build_dir_with_h5(d: Path, mtime: float) -> dict:
    """Walk a single directory and annotate videos with h5 counts."""
    parent = str(d.parent) if d.parent != d else None
    dirs   = []
    videos = []
    h5_stems_by_video: dict[str, list[str]] = {}

    def _record_h5(h5_path: Path):
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
```

### What stays untouched

- `/dlc/viewer/h5-variants` — still the source of truth for per-video variant detail (label, type, run_id, status, disabled).
- `/dlc/viewer/h5-info`, `/frame-poses`, `/frame-poses-batch`, `/marker-edit`, `/save-marker-edits`, `/edit-cache` — all unchanged.

## Error handling

- `/dir-with-h5` 404 on non-existent path; 403 on disallowed path.
- Redis `setex` failure → log + return the freshly-built payload anyway. Read fallback already covered by the try/except.
- `_viewer_dir_mtime` raises for a bad symlink in `postproc/` → caught; the dir is treated as "no postproc" for that scan.
- Frontend fall-through: if `/dir-with-h5` returns 5xx, the Browse list falls back to the existing `/fs/ls` so the feature degrades to today's behaviour rather than going blank.
- Frame-step input clamps to `[1, 100]`; out-of-range values silently clamp.
- Per-frame render gate: a layer's pose-fetch error sets `errored = true`; the `Promise.all` resolves with that layer skipped. Same resilience as today.
- Add-comparison empty hint covers the previously confusing "(no other variants)" case.

## Edge cases

- **Browse-tab toggle persistence:** `state.vaBrowseHideNoH5` survives Browse-tab navigation within the session; resets on page reload.
- **Auto-latest with only Raw available:** Raw has `ts === null`; the `bestByTs` filter falls into the no-dated fallback → Raw becomes primary. Today's behaviour preserved.
- **Auto-latest selecting a `disabled` variant:** filter excludes `disabled`; next-best non-disabled dated variant wins.
- **Frame-step playback past the end:** the existing `nextFrame >= _vaFrameCount` guard already handles it.
- **Cache mtime collision on coarse-mtime filesystems:** mitigated by the 24h TTL and the user's natural "reload page" reset.
- **Concurrent users on `/dir-with-h5`:** Redis `setex` is atomic; the last write wins. Both compute the same payload anyway.

## Testing

| Test file | Coverage |
|---|---|
| `tests/test_dlc_viewer_routes.py` (extend) | `test_dir_with_h5_returns_videos_with_h5_counts`, `test_dir_with_h5_cache_hit_returns_immediately`, `test_dir_with_h5_invalidates_on_dir_mtime_change`, `test_dir_with_h5_invalidates_on_postproc_run`, `test_dir_with_h5_404_on_missing_path`, `test_dir_with_h5_403_on_disallowed_path`. |
| `tests/test_viewer_layers_ui_isolation.py` (extend) | New IDs `va-browse-hide-no-h5`, `va-overlay-add-compare-empty-hint`, `va-play-step` are unique. `viewer.js` references each. JS-source asserts: `_vaPlayStep` defined; auto-latest selection uses `bestByTs`-style logic (string match: `dated.reduce` or `bestByTs`); the primary-swap path explicitly empties `_vaLayers` before pushing the new primary. |
| `tests/test_postprocess_real_project.py` (extend) | `test_dir_with_h5_against_om2_ratbox`: real OM-2 RatBox folder; assert one of the .avi files has `has_h5: true` and `h5_count >= 1`. Skipped on hosts without the synology mount (same as the existing OM-2 test). |
| `tests/e2e_viewer_layers_smoke.py` (extend) | Auto-latest assertion: load a video that has both Raw + a filtered variant; verify the Primary `<select>`'s default selected option's text starts with `filtered @ ` (or `refine_*`), not `Raw —`. Browse-list filter assertion: untick `Hide videos without h5` → at least one row appears with `data-has-h5="false"`. Add-comparison assertion: after primary swap, the compare dropdown's option count matches `<total variants> - 1`; if zero, the empty-hint span is visible. Frame-step assertion: set `va-play-step = 5`, click play, wait 1s, click pause, verify `_vaCurrentFrame` advanced by a multiple of 5 (or > 4) — i.e., it didn't advance by 1. |
| `tests/e2e_postprocess_smoke.py` (regression) | Re-run after each commit; must continue to pass. |

The OM-2 RatBox folder (host: `/home/sam/synology/Parra-Lab-Data/Reaching-Task-Data/RatBox Videos/tdcs/042426/OM-2_cam0_..._gain10`; container: `/user-data/Parra-Data/Cloud/...`) is the canonical real-data integration target — same path used by the post-process e2e and the existing viewer-layers e2e.

## File summary

**Modified files:**

- `src/dlc/viewer.py` — add `_VIEWER_VIDEO_EXTS`, `_viewer_dir_mtime`, `_build_dir_with_h5`, route `viewer_dir_with_h5`.
- `src/templates/partials/card_viewer.html` — Browse-tab `va-browse-hide-no-h5` checkbox; player `va-play-step` input; overlay-block `va-overlay-add-compare-empty-hint` span.
- `src/static/js/viewer.js` — refactor `_vaRefreshBrowse` to use `/dir-with-h5`; add filter + h5-count badges + `data-has-h5`; replace selection logic in `_vaDiscoverVariants` with `bestByTs`; rewrite `_vaApplyPrimaryFromSelect` to clear `_vaLayers`; rewrite `_vaRefreshAddComparisonOptions` to toggle hint; add `_vaPlayStep`; update play loop; add the RAF paint barrier in `_vaLoadFrame`.
- `tests/test_dlc_viewer_routes.py` — add the six new dir-with-h5 cases.
- `tests/test_viewer_layers_ui_isolation.py` — add new-ID assertions, JS-source asserts.
- `tests/test_postprocess_real_project.py` — add the OM-2 dir-with-h5 integration case.
- `tests/e2e_viewer_layers_smoke.py` — extend with the four new assertions.

**New files:** none.

## Open items for the implementation plan

- Confirm the exact existing `_vaRefreshBrowse` signature so the refactor preserves its current callers (Browse-tab activation, breadcrumb input, folder-row clicks).
- Confirm `state.js` already exists and is shared (it does, per the prior plan); the new `state.vaBrowseHideNoH5` field is a one-line addition.
- The cache key uses `f"viewer:dir_h5:{d}"` — paths can contain spaces and unicode. Redis keys handle them fine, but verify the existing `webapp:dlc_project:<uid>` key style for consistency before final naming.
- The play-step input lives in the player controls. Confirm there's room in the layout next to `va-skip-n` without breaking the existing flex/wrap behaviour.
