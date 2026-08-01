// tracked_files_tab.js — "Tracked Files" source tab for the 3D Inline Analysis card.
//
// Owns the tab panel's list, the in-memory set of tracked paths, and the
// track checkbox in the player header. The consumer (inline_analysis_3d.js)
// only constructs it and calls setCurrent() — no tracking logic lives there.
//
// Persistence is per DLC project, served by the main webapp's
// /dlc/project/tracked-files blueprint. The list is a pure DB read: a tracked
// file that has since moved still lists, and only fails when opened.
"use strict";

import { formatRelative } from "./relative_time.mjs";
import { makeProgressBar } from "./progress_bar.js";

const API = "/dlc/project/tracked-files";
const BAR_API = "/dlc/project/progress-bar";
const JSON_HEADERS = { "Content-Type": "application/json" };

export function makeTrackedFiles({
  tabBtn, refreshBtn, panelEl, listEl, headerCheckbox, headerLabel, onOpen, onError,
}) {
  let _rows = new Map();     // path -> {path, name, dir, tracked_at, last_opened_at}
  let _current = null;       // the path currently open in the player, or null
  let _loaded = false;       // has a list fetch ever succeeded?
  let _definition = { segments: [] };   // project's bar definition, fetched with the list
  const _ac = new AbortController();
  const _sig = { signal: _ac.signal };

  // ── Transport ─────────────────────────────────────────────────────────────

  async function _fetchJson(url, opts) {
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error) throw new Error(data.error || `status ${res.status}`);
    return data;
  }

  const _body = (payload) => ({ headers: JSON_HEADERS, body: JSON.stringify(payload) });

  // ── Rendering ─────────────────────────────────────────────────────────────

  function _empty(text) {
    listEl.innerHTML = "";
    const p = document.createElement("p");
    p.className = "explorer-empty";
    p.textContent = text;
    listEl.appendChild(p);
  }

  function _render() {
    if (_rows.size === 0) {
      _empty("No tracked files. Open a video from Browse Folders and tick the box next to its name.");
      return;
    }
    listEl.innerHTML = "";
    const now = Date.now();
    for (const row of _rows.values()) listEl.appendChild(_makeRow(row, now));
  }

  function _makeRow(f, now) {
    const row = document.createElement("div");
    row.className = "fe-video-item";
    row.style.cursor = "pointer";
    row.dataset.path = f.path;

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.title = "Untrack this file";
    cb.style.cssText = "accent-color:var(--accent);width:13px;height:13px;flex-shrink:0";
    // The row opens the video; the checkbox must not.
    cb.addEventListener("click", (e) => e.stopPropagation(), _sig);
    cb.addEventListener("change", () => _untrack(f, cb), _sig);

    const bar = makeProgressBar({
      definition: _definition,
      values: f.progress || {},
      onChange: (segmentId, optionId) =>
        _setSegment(f.path, f.video_id, segmentId, optionId),
    });

    const col = document.createElement("div");
    col.style.cssText = "display:flex;flex-direction:column;min-width:0;flex:1";
    const nameEl = document.createElement("span");
    nameEl.textContent = f.name;
    nameEl.style.cssText = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
    const dirEl = document.createElement("span");
    dirEl.textContent = f.dir;
    dirEl.style.cssText = "font-size:.7rem;color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
    col.appendChild(nameEl);
    col.appendChild(dirEl);

    const when = document.createElement("span");
    when.textContent = formatRelative(f.last_opened_at, now);
    when.style.cssText = "font-size:.68rem;color:var(--text-dim);flex-shrink:0;padding-left:.6rem";

    // Order: checkbox · filename · progress bar · last-opened.
    // `col` is flex:1 so every row's bar starts at the same x — a ragged bar
    // column is much harder to scan down than a right-aligned one.
    row.appendChild(cb);
    row.appendChild(col);
    row.appendChild(bar);
    row.appendChild(when);
    row.addEventListener("click", () => onOpen?.(f.path, f.name), _sig);
    return row;
  }

  function _syncHeader() {
    if (!headerCheckbox) return;
    const wrap = headerLabel || headerCheckbox;
    if (!_current) {
      wrap.classList.add("hidden");
      headerCheckbox.checked = false;
      return;
    }
    wrap.classList.remove("hidden");
    headerCheckbox.checked = _rows.has(_current);
  }

  // ── Mutations ─────────────────────────────────────────────────────────────

  // Tracking is by path — you track a location, and the server mints or
  // reuses the video identity for it.
  async function _track(path) {
    try {
      await _fetchJson(API, { method: "POST", ..._body({ path }) });
      await refresh();
    } catch (err) {
      if (headerCheckbox) headerCheckbox.checked = false;   // revert
      onError?.(`Could not track ${path} — ${err.message}`);
    }
  }

  // Untracking is by video_id: the row already knows it, and a path that
  // changed underneath us would otherwise untrack the wrong thing.
  async function _untrack(f, cb) {
    try {
      await _fetchJson(API, { method: "DELETE", ..._body({ video_id: f.video_id }) });
      _rows.delete(f.path);
      _render();
      _syncHeader();
    } catch (err) {
      if (cb) cb.checked = true;                            // revert
      onError?.(`Could not untrack ${f.path} — ${err.message}`);
    }
  }

  // Persist one segment of one file. Rejecting lets the component revert its
  // optimistic paint, so the bar never shows a value the server does not have.
  // Keyed by video_id, not path: a rename mid-session must not misdirect this.
  async function _setSegment(path, videoId, segmentId, optionId) {
    await _fetchJson(BAR_API + "/value", {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify({ video_id: videoId, segment_id: segmentId, option_id: optionId }),
    });
    const row = _rows.get(path);
    if (row) {
      row.progress = row.progress || {};
      if (optionId === null) delete row.progress[segmentId];
      else row.progress[segmentId] = optionId;
    }
  }

  // Ordering is cosmetic, so a failure here never disturbs the open.
  function _noteOpened(path) {
    const row = _rows.get(path);
    const payload = row && row.video_id ? { video_id: row.video_id } : { path };
    _fetchJson(API + "/opened", { method: "POST", ..._body(payload) }).catch(() => {});
  }

  // ── Public surface ────────────────────────────────────────────────────────

  async function refresh() {
    try {
      const [data, def] = await Promise.all([
        _fetchJson(API),
        // The bar is decorative next to the list: if it fails, still show files.
        _fetchJson(BAR_API).catch(() => ({ segments: [] })),
      ]);
      _definition = def && Array.isArray(def.segments) ? def : { segments: [] };
      _rows = new Map((data.files || []).map((f) => [f.path, f]));
      _loaded = true;
      _render();
    } catch (err) {
      _rows = new Map();
      _empty(err.message);
    }
    _syncHeader();
  }

  // Called with the open video's absolute path, or null when nothing is open /
  // the open thing is not a browse video (project content, frame folders).
  async function setCurrent(path) {
    _current = path || null;
    // The header checkbox reflects _rows, so the list must have been fetched at
    // least once — otherwise opening an ALREADY-tracked video before ever
    // visiting the tab would show its checkbox unticked.
    if (_current && !_loaded) await refresh();
    _syncHeader();
    if (_current && _rows.has(_current)) _noteOpened(_current);
  }

  function destroy() {
    _ac.abort();
    _rows = new Map();
    _current = null;
  }

  // ── Wiring ────────────────────────────────────────────────────────────────

  tabBtn?.addEventListener("click", () => refresh(), _sig);
  refreshBtn?.addEventListener("click", (e) => { e.stopPropagation(); refresh(); }, _sig);
  headerCheckbox?.addEventListener("change", () => {
    if (!_current) return;
    if (headerCheckbox.checked) _track(_current);
    else {
      const row = _rows.get(_current);
      if (row) _untrack(row, headerCheckbox);
    }
  }, _sig);

  return { refresh, setCurrent, destroy };
}
