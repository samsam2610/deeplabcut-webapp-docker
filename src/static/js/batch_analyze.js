// batch_analyze.js — the "Batch Analyze" panel on the Analyze Video / Frames card.
//
// Runs the INLINE-analysis pipeline (warm session + range queue) over many
// videos, rather than the one-shot analyze_videos path the rest of that card
// drives. This module is deliberately thin: it collects a queue and some
// options and POSTs them. Every decision that matters — which model, which
// frames, when to start — is made server-side in dlc/batch_analyze.py, so a
// batch survives the browser closing.
//
// The card is a shared partial: it renders on both / and /dlc-3d/, and this
// module loads on both. The only per-page difference is the default of the
// "Both cameras" checkbox.
//
// See docs/superpowers/specs/2026-08-06-batch-analyze-panel-design.md.
"use strict";

import { state } from './state.js';
import { makeTrackedFiles } from './components/tracked_files_tab.js';
import {
  addTag, removeTag, toggleSelected, submittedTags, canRunForTag, parseStored,
} from './internal/batch_tags.mjs';

const $ = (id) => document.getElementById(id);
const JSON_HEADERS = { "Content-Type": "application/json" };

const VIDEO_EXTS = new Set([".avi", ".mp4", ".mkv", ".mov", ".m4v", ".webm"]);

const SETTING_API = "/dlc/project/ui-setting";
const START_API   = "/dlc/project/batch-analyze/start";
const STATUS_API  = "/dlc/project/batch-analyze/status";
const LIST_API    = "/dlc/project/batch-analyze/list";
const CANCEL_API  = "/dlc/project/batch-analyze/cancel";

let _queue = [];            // ordered absolute video paths
let _tags = [];             // saved tag chips (per project)
let _selected = [];         // chips chosen for the next run — NOT persisted:
                            // a stale selection firing off 200k frames is a
                            // worse failure than re-picking two chips.
let _projectPath = null;
let _browsePath = null;
let _pollTimer = null;
let _activeBatch = null;
let _tracked = null;

// ── settings ──────────────────────────────────────────────────────────────

async function _getSetting(key) {
  try {
    const r = await fetch(`${SETTING_API}?key=${encodeURIComponent(key)}`);
    const d = await r.json();
    return d && d.value ? d.value : "";
  } catch (_) { return ""; }
}

function _setSetting(key, value) {
  fetch(SETTING_API, {
    method: "POST", headers: JSON_HEADERS,
    body: JSON.stringify({ key, value: String(value) }),
  }).catch(() => {});    // best-effort; the in-memory value stays correct
}

let _prefsTimer = null;
function _savePrefs() {
  if (_prefsTimer) clearTimeout(_prefsTimer);
  _prefsTimer = setTimeout(() => {
    _setSetting("batch_prefs", JSON.stringify({
      both_cams: !!$("ba-both-cams")?.checked,
      policy: _policy(),
      wait_for_training: !!$("ba-wait-training")?.checked,
    }));
    _setSetting("batch_window", JSON.stringify({
      before: _int("ba-before", 200), after: _int("ba-after", 599),
    }));
  }, 400);
}

// ── small helpers ─────────────────────────────────────────────────────────

function _int(id, dflt) {
  const v = parseInt($(id)?.value, 10);
  return Number.isFinite(v) && v >= 0 ? v : dflt;
}

function _policy() {
  const el = document.querySelector('input[name="ba-policy"]:checked');
  return el ? el.value : "pinned";
}

function _status(msg, isErr = false) {
  const el = $("ba-status");
  if (!el) return;
  el.textContent = msg || "";
  el.className = "fe-extract-status" + (isErr ? " err" : "");
}

function _launcherError(msg) {
  const el = $("ba-launcher-error");
  if (el) el.textContent = msg || "";
}

function _isVideo(name) {
  const i = name.lastIndexOf(".");
  return i > 0 && VIDEO_EXTS.has(name.slice(i).toLowerCase());
}

// ── queue ─────────────────────────────────────────────────────────────────

function _add(path) {
  if (!path) return;
  if (_queue.includes(path)) { _status(`Already queued: ${path.split("/").pop()}`); return; }
  _queue.push(path);
  _renderQueue();
  _status(`Queued ${path.split("/").pop()} (${_queue.length} total).`);
}

function _remove(path) {
  _queue = _queue.filter((p) => p !== path);
  _renderQueue();
}

function _renderQueue() {
  const list = $("ba-queue-list");
  const count = $("ba-queue-count");
  if (count) count.textContent = String(_queue.length);
  if (!list) return;
  list.innerHTML = "";
  list.style.display = _queue.length ? "block" : "none";
  for (const p of _queue) {
    const row = document.createElement("div");
    row.className = "ba-queue-row";
    const name = document.createElement("span");
    name.textContent = p.split("/").pop();
    name.title = p;
    const x = document.createElement("span");
    x.className = "x";
    x.textContent = "×";
    x.addEventListener("click", () => _remove(p));
    row.appendChild(name);
    row.appendChild(x);
    list.appendChild(row);
  }
  // Re-render the source panes so already-queued rows dim.
  document.querySelectorAll(".ba-row").forEach((r) => {
    r.classList.toggle("queued", _queue.includes(r.dataset.path || ""));
  });
}

/** One clickable source row. Double-click queues, matching the card's
 *  existing browser ("single-click to highlight · double-click to add"). */
function _sourceRow(label, path, subtitle) {
  const row = document.createElement("div");
  row.className = "ba-row" + (_queue.includes(path) ? " queued" : "");
  row.dataset.path = path;
  row.title = path;
  const name = document.createElement("span");
  name.style.cssText = "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
  name.textContent = label;
  row.appendChild(name);
  if (subtitle) {
    const sub = document.createElement("span");
    sub.style.cssText = "font-size:.7rem;color:var(--text-dim);flex-shrink:0";
    sub.textContent = subtitle;
    row.appendChild(sub);
  }
  row.addEventListener("dblclick", () => _add(path));
  return row;
}

// ── tabs ──────────────────────────────────────────────────────────────────

const TABS = [
  { btn: "ba-tab-project", panel: "ba-tab-project-panel", load: () => _loadProject() },
  { btn: "ba-tab-browse",  panel: "ba-tab-browse-panel",  load: () => _loadBrowse(_browsePath || state.userDataDir || state.dataDir || "/") },
  { btn: "ba-tab-tracked", panel: "ba-tab-tracked-panel", load: () => {} },
];

function _showTab(which) {
  for (const t of TABS) {
    const btn = $(t.btn), panel = $(t.panel);
    if (btn) btn.classList.toggle("active", t.btn === which);
    if (panel) panel.classList.toggle("hidden", t.btn !== which);
  }
  _launcherError("");
  const tab = TABS.find((t) => t.btn === which);
  if (tab) tab.load();
}

// ── source: Project Content ───────────────────────────────────────────────

// Lists the project's SOURCE videos (project/videos/), not the "_labeled"
// outputs the inline cards' Project Content tab shows. A labeled video already
// has markers burned into its pixels; running the model over one is never what
// a batch wants.
async function _loadProject() {
  const list = $("ba-project-list");
  if (!list) return;
  list.innerHTML = '<p class="explorer-empty">Loading…</p>';
  try {
    if (!_projectPath) {
      const proj = await (await fetch("/dlc/project")).json();
      _projectPath = proj && (proj.project_path || (proj.project && proj.project.project_path)) || null;
    }
    const d = await (await fetch("/dlc/project/videos")).json();
    if (d.error) { list.innerHTML = `<p class="explorer-empty">${d.error}</p>`; return; }
    const vids = (d.videos || []).filter((v) => !v.name.includes("_labeled"));
    if (!vids.length) {
      list.innerHTML = '<p class="explorer-empty">No videos in this project’s videos/ folder.</p>';
      return;
    }
    if (!_projectPath) {
      list.innerHTML = '<p class="explorer-empty">Could not resolve the project path.</p>';
      return;
    }
    list.innerHTML = "";
    for (const v of vids) {
      const sub = v.size ? `${Math.round(v.size / 1048576)} MB` : "";
      list.appendChild(_sourceRow(v.name, `${_projectPath}/videos/${v.name}`, sub));
    }
  } catch (err) {
    list.innerHTML = `<p class="explorer-empty">Error: ${err.message}</p>`;
  }
}

// ── source: Browse Folders ────────────────────────────────────────────────

async function _loadBrowse(path) {
  _browsePath = path;
  const input = $("ba-browse-path");
  const list = $("ba-browse-list");
  if (input && input.value !== path) input.value = path;
  if (!list) return;
  list.innerHTML = '<p class="explorer-empty">Loading…</p>';

  let data = null;
  try {
    const res = await fetch(`/dlc/viewer/dir-with-h5?path=${encodeURIComponent(path)}`);
    data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || `status ${res.status}`);
  } catch (_) {
    // Same fallback the inline cards use — dir-with-h5 is the richer route but
    // /fs/ls works anywhere under the data root.
    try {
      const d2 = await (await fetch(`/fs/ls?path=${encodeURIComponent(path)}`)).json();
      if (d2.error) { list.innerHTML = `<p class="explorer-empty">${d2.error}</p>`; return; }
      const entries = d2.entries || [];
      data = {
        dirs: entries.filter((e) => e.type === "dir").map((e) => ({ name: e.name })),
        videos: entries.filter((e) => e.type === "file" && _isVideo(e.name))
                       .map((e) => ({ name: e.name, has_h5: false })),
      };
    } catch (err) {
      list.innerHTML = `<p class="explorer-empty">Error: ${err.message}</p>`;
      return;
    }
  }

  list.innerHTML = "";
  for (const d of data.dirs || []) {
    const row = document.createElement("div");
    row.className = "ba-row";
    row.textContent = `📁 ${d.name}`;
    row.addEventListener("click", () => _loadBrowse(`${path.replace(/\/$/, "")}/${d.name}`));
    list.appendChild(row);
  }
  for (const v of data.videos || []) {
    list.appendChild(_sourceRow(v.name, `${path.replace(/\/$/, "")}/${v.name}`,
                                v.has_h5 ? "h5" : ""));
  }
  if (!list.children.length) {
    list.innerHTML = '<p class="explorer-empty">No folders or videos here.</p>';
  }
}

// ── tag chips ─────────────────────────────────────────────────────────────
//
// The chips ARE the selection: clicking one toggles whether that tag is part of
// the next run. The text field only mints new chips. All the rules live in
// internal/batch_tags.mjs so they can be tested without a DOM.

function _renderTags() {
  const c = $("ba-tags");
  if (!c) return;
  c.innerHTML = "";
  for (const t of _tags) {
    const pill = document.createElement("span");
    pill.className = "fe-tag-chip ba-ptag" + (_selected.includes(t) ? " active" : "");
    pill.title = _selected.includes(t) ? "Selected — click to deselect"
                                       : "Click to select for the next run";
    pill.appendChild(document.createTextNode(t));
    const x = document.createElement("span");
    x.className = "x";
    x.textContent = "×";
    x.title = "Remove this tag from the project";
    x.addEventListener("click", (ev) => {
      ev.stopPropagation();      // removing must not also toggle selection
      const r = removeTag(_tags, _selected, t);
      _tags = r.tags; _selected = r.selected;
      _renderTags();
      _setSetting("batch_tags", JSON.stringify(_tags));
    });
    pill.appendChild(x);
    pill.addEventListener("click", () => {
      _selected = toggleSelected(_tags, _selected, t);
      _renderTags();
    });
    c.appendChild(pill);
  }
  if (!_tags.length) {
    const hint = document.createElement("span");
    hint.className = "ba-hint";
    hint.textContent = "no tags yet — type one above and press + Add";
    c.appendChild(hint);
  }
  _syncTagEnablement();
}

/** Add whatever is in the field as a new chip. Duplicates vanish silently. */
function _onAddTag() {
  const el = $("ba-tag-input");
  if (!el) return;
  const r = addTag(_tags, el.value);
  if (r.added) {
    _tags = r.tags;
    _setSetting("batch_tags", JSON.stringify(_tags));
  }
  // Cleared either way: a duplicate is "already there", so leaving the text
  // behind would read as a failure when nothing is wrong.
  if (r.reason !== "empty") el.value = "";
  _renderTags();
}

/** "Analyze for tag" is inert until at least one chip is selected. */
function _syncTagEnablement() {
  const btn = $("ba-run-tag");
  const hint = $("ba-tag-hint");
  const ok = canRunForTag(_tags, _selected);
  if (btn) {
    btn.disabled = !ok;
    btn.title = ok ? "Analyse the window around every frame carrying a selected tag"
                   : "Select at least one tag below";
  }
  if (hint) {
    const names = submittedTags(_tags, _selected);
    hint.textContent = names.length
      ? `${names.length} tag(s) selected: ${names.join(", ")}`
      : "select a tag to enable “Analyze for tag”";
  }
}

// ── run ───────────────────────────────────────────────────────────────────

async function _run(mode) {
  if (!_queue.length) { _status("Queue at least one video.", true); return; }
  const tags = submittedTags(_tags, _selected);
  if (mode === "tag" && !tags.length) {
    _status("Select at least one tag chip first.", true);
    return;
  }
  const before = _int("ba-before", 200);
  const after = _int("ba-after", 599);
  const bothCams = !!$("ba-both-cams")?.checked;
  const wait = !!$("ba-wait-training")?.checked;

  const what = mode === "tag"
    ? `tag(s) ${tags.map((t) => `"${t}"`).join(" + ")} · ${before + after + 1} frames per tagged frame`
    : "every frame";
  const ok = window.confirm(
    `Batch analyze ${_queue.length} video(s)${bothCams ? " × both cameras" : ""}:\n` +
    `${what}\nModel: ${_policy().replace(/_/g, " ")}\n` +
    (wait ? "\nWill wait until a training job has run and finished.\n" : "") +
    `\nProceed?`
  );
  if (!ok) return;

  _status("Submitting…");
  try {
    const res = await fetch(START_API, {
      method: "POST", headers: JSON_HEADERS,
      body: JSON.stringify({
        videos: _queue, mode, tags, before, after,
        both_cams: bothCams, policy: _policy(), wait_for_training: wait,
        shuffle: _int("av-shuffle", 1),
        trainingsetindex: _int("av-trainingsetindex", 0),
        batch_size: _int("av-batch-size", 8),
        save_as_csv: !!$("av-save-csv")?.checked,
      }),
    });
    const d = await res.json();
    if (!res.ok || d.error) { _status(d.error || `status ${res.status}`, true); return; }
    _activeBatch = d.batch_id;
    _status(`Batch queued (${d.n_videos} video(s)). Tracking…`);
    _startPolling();
  } catch (err) {
    _status(`Error: ${err.message}`, true);
  }
}

function _startPolling() {
  if (_pollTimer) clearInterval(_pollTimer);
  $("ba-cancel")?.classList.remove("hidden");
  _pollTimer = setInterval(_poll, 3000);
  _poll();
}

function _stopPolling() {
  if (_pollTimer) clearInterval(_pollTimer);
  _pollTimer = null;
  $("ba-cancel")?.classList.add("hidden");
}

async function _cancel() {
  if (!_activeBatch) return;
  try {
    const d = await (await fetch(CANCEL_API, {
      method: "POST", headers: JSON_HEADERS,
      body: JSON.stringify({ batch_id: _activeBatch }),
    })).json();
    if (d.error) { _status(d.error, true); return; }
    // Cancelling only stops a batch that has not submitted yet; once ranges
    // are on the session queue, stopping is the inline session's job.
    if (d.state === "cancelled") {
      _status("Batch cancelled.");
      _activeBatch = null;
      _stopPolling();
    } else {
      _status("Ranges are already queued — stop the inline session to halt them.", true);
    }
  } catch (err) { _status(`Error: ${err.message}`, true); }
}

async function _poll() {
  if (!_activeBatch) { _stopPolling(); return; }
  try {
    const d = await (await fetch(`${STATUS_API}?batch_id=${encodeURIComponent(_activeBatch)}`)).json();
    if (d.error) { _status(d.error, true); _stopPolling(); return; }
    const skipped = (d.skipped || []).length;
    const skipNote = skipped ? ` · ${skipped} skipped` : "";
    if (d.state === "waiting") {
      _status(`Waiting — ${d.reason || "for training"}. Safe to close the browser.${skipNote}`);
    } else if (d.state === "failed") {
      _status(`Failed: ${d.reason || d.last_error || "unknown"}`, true);
      _stopPolling();
    } else if (d.state === "submitted" || d.state === "complete") {
      const pct = d.n_ranges ? Math.round((d.ranges_done / d.n_ranges) * 100) : 0;
      const head = d.state === "complete" ? "Complete" : "Running";
      _status(`${head} — ${d.ranges_done}/${d.n_ranges} ranges (${pct}%) · `
            + `${d.frames_analyzed} frames analysed, ${d.frames_skipped} already done`
            + `${d.ranges_error ? ` · ${d.ranges_error} errored` : ""}`
            + ` · model ${d.snapshot || "?"}${skipNote}`,
              d.ranges_error > 0);
      if (d.state === "complete") _stopPolling();
    } else {
      _status(`${d.state}${d.reason ? ` — ${d.reason}` : ""}${skipNote}`);
    }
  } catch (_) { /* transient; the next tick retries */ }
}

// Re-attach to a batch still in flight. Without this, a run deferred until
// training finishes vanishes from the UI the moment the tab reloads — which is
// precisely the case that option exists for.
async function _reattach() {
  try {
    const d = await (await fetch(LIST_API)).json();
    const live = (d.batches || [])[0];
    if (live) { _activeBatch = live.batch_id; _startPolling(); }
  } catch (_) { /* nothing in flight, or redis down */ }
}

// ── init ──────────────────────────────────────────────────────────────────

export function initBatchAnalyze() {
  const enable = $("ba-enable");
  const panel = $("ba-panel");
  if (!enable || !panel) return;      // card not on this page

  // "Both cameras" defaults ON where stereo pairs are the norm. The checkbox
  // is present and honoured on both pages; only the default differs.
  const bothCams = $("ba-both-cams");
  if (bothCams) bothCams.checked = window.location.pathname.startsWith("/dlc-3d");

  let _booted = false;
  enable.addEventListener("change", async () => {
    panel.classList.toggle("hidden", !enable.checked);
    if (!enable.checked || _booted) return;
    _booted = true;

    const [tagsRaw, prefsRaw, windowRaw] = await Promise.all([
      _getSetting("batch_tags"), _getSetting("batch_prefs"), _getSetting("batch_window"),
    ]);
    _tags = parseStored(tagsRaw);
    _selected = [];
    try {
      const p = prefsRaw ? JSON.parse(prefsRaw) : {};
      if (bothCams && typeof p.both_cams === "boolean") bothCams.checked = p.both_cams;
      if (p.policy) {
        const radio = document.querySelector(`input[name="ba-policy"][value="${p.policy}"]`);
        if (radio) radio.checked = true;
      }
      const w = $("ba-wait-training");
      if (w && typeof p.wait_for_training === "boolean") w.checked = p.wait_for_training;
    } catch (_) { /* defaults stand */ }
    try {
      const w = windowRaw ? JSON.parse(windowRaw) : {};
      if (Number.isFinite(w.before)) $("ba-before").value = w.before;
      if (Number.isFinite(w.after)) $("ba-after").value = w.after;
    } catch (_) { /* defaults stand */ }

    _renderTags();
    _syncWindowTotal();
    _showTab("ba-tab-project");
    _reattach();
  });

  for (const t of TABS) $(t.btn)?.addEventListener("click", () => _showTab(t.btn));

  $("ba-project-refresh")?.addEventListener("click", () => { _projectPath = null; _loadProject(); });
  $("ba-browse-up")?.addEventListener("click", () => {
    if (!_browsePath) return;
    const parent = _browsePath.replace(/\/$/, "").split("/").slice(0, -1).join("/") || "/";
    if (parent !== _browsePath) _loadBrowse(parent);
  });
  $("ba-browse-path")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") _loadBrowse($("ba-browse-path").value.trim() || "/");
  });

  _tracked = makeTrackedFiles({
    tabBtn: $("ba-tab-tracked"),
    refreshBtn: $("ba-tracked-refresh"),
    sortMount: $("ba-tracked-sort"),
    panelEl: $("ba-tab-tracked-panel"),
    listEl: $("ba-tracked-list"),
    headerCheckbox: null, headerLabel: null, headerBarMount: null,
    onOpen: (path) => _add(path),
    onError: (msg) => _launcherError(msg),
  });

  $("ba-queue-clear")?.addEventListener("click", () => { _queue = []; _renderQueue(); });
  $("ba-tag-add")?.addEventListener("click", _onAddTag);
  $("ba-tag-input")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); _onAddTag(); }
  });
  $("ba-run-all")?.addEventListener("click", () => _run("all"));
  $("ba-run-tag")?.addEventListener("click", () => _run("tag"));
  $("ba-cancel")?.addEventListener("click", _cancel);

  for (const id of ["ba-before", "ba-after"]) {
    $(id)?.addEventListener("input", () => { _syncWindowTotal(); _savePrefs(); });
  }
  $("ba-both-cams")?.addEventListener("change", _savePrefs);
  $("ba-wait-training")?.addEventListener("change", _savePrefs);
  document.querySelectorAll('input[name="ba-policy"]')
    .forEach((r) => r.addEventListener("change", _savePrefs));
}

function _syncWindowTotal() {
  const el = $("ba-window-total");
  if (el) el.textContent = `${_int("ba-before", 200) + _int("ba-after", 599) + 1} frames`;
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initBatchAnalyze);
} else {
  initBatchAnalyze();
}
