// tracked_files_panel.js — the "Tracked Files & Progress" card.
//
// Two jobs: edit the project's progress-bar definition, and list every tracked
// file with its checkbox and its editable bar. The list itself is the shared
// makeTrackedFiles component — this file owns only the definition editor.
"use strict";

import { makeTrackedFiles } from "./components/tracked_files_tab.js";
import { isValidHexColor } from "./components/hex_color.mjs";

const BAR_API = "/dlc/project/progress-bar";
const DEFAULT_COLOR = "#888888";

const $ = (id) => document.getElementById(id);

let _definition = { segments: [] };
let _tracked = null;

function _status(msg, isError) {
  const el = $("tf-status");
  if (!el) return;
  el.textContent = msg || "";
  el.style.color = isError ? "var(--danger, #e66)" : "";
}

async function _fetchJson(url, opts) {
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error) throw new Error(data.error || `status ${res.status}`);
  return data;
}

// ── Definition editor ───────────────────────────────────────────────────────

function _renderSegments() {
  const host = $("tf-segments");
  if (!host) return;
  host.innerHTML = "";
  _definition.segments.forEach((seg, idx) => {
    const box = document.createElement("div");
    // No margin-bottom: #tf-segments is a grid and its `gap` owns the spacing.
    box.style.cssText =
      "border:1px solid var(--border);border-radius:6px;padding:.4rem .5rem;background:var(--surface)";

    const head = document.createElement("div");
    head.style.cssText = "display:flex;align-items:center;gap:.4rem;margin-bottom:.35rem";
    const num = document.createElement("span");
    num.textContent = `${idx + 1}.`;
    num.style.cssText = "font-size:.72rem;color:var(--text-dim);flex-shrink:0";
    const name = document.createElement("input");
    name.type = "text";
    name.value = seg.name || "";
    name.placeholder = "segment name";
    name.style.cssText =
      "flex:1;min-width:0;font-size:.76rem;background:var(--surface-2);border:1px solid var(--border);border-radius:5px;color:var(--text);padding:.2rem .4rem";
    name.addEventListener("input", () => { seg.name = name.value; });
    const addOpt = document.createElement("button");
    addOpt.className = "btn-sm";
    addOpt.textContent = "＋ Option";
    addOpt.style.cssText = "padding:.15rem .45rem;font-size:.72rem;flex-shrink:0";
    addOpt.addEventListener("click", () => {
      seg.options = seg.options || [];
      seg.options.push({ label: "", color: DEFAULT_COLOR });
      _renderSegments();
    });
    head.appendChild(num); head.appendChild(name); head.appendChild(addOpt);
    box.appendChild(head);

    (seg.options || []).forEach((opt, oIdx) => {
      const row = document.createElement("div");
      row.style.cssText = "display:flex;align-items:center;gap:.4rem;margin-bottom:.25rem";
      const color = document.createElement("input");
      color.type = "color";
      color.value = isValidHexColor(opt.color) ? opt.color : DEFAULT_COLOR;
      color.style.cssText = "width:2rem;height:1.5rem;padding:0;border:1px solid var(--border);border-radius:4px;background:none;flex-shrink:0";
      color.addEventListener("input", () => { opt.color = color.value; });
      const label = document.createElement("input");
      label.type = "text";
      label.value = opt.label || "";
      label.placeholder = "option label";
      label.style.cssText =
        "flex:1;min-width:0;font-size:.75rem;background:var(--surface-2);border:1px solid var(--border);border-radius:5px;color:var(--text);padding:.18rem .4rem";
      label.addEventListener("input", () => { opt.label = label.value; });
      const del = document.createElement("button");
      del.className = "btn-sm";
      del.textContent = "✕";
      del.title = "Remove this option";
      del.style.cssText = "padding:.1rem .4rem;font-size:.7rem;opacity:.7;flex-shrink:0";
      del.addEventListener("click", () => {
        seg.options.splice(oIdx, 1);
        _renderSegments();
      });
      row.appendChild(color); row.appendChild(label); row.appendChild(del);
      box.appendChild(row);
    });

    host.appendChild(box);
  });
}

// Removed segments are kept here so lowering the count and raising it again
// BEFORE saving restores the same ids — and therefore the same file values.
// Once saved, those ids are gone for good.
let _dropped = [];

function _applyCount(n) {
  const count = Math.max(0, Math.min(10, Number(n) || 0));
  const segs = _definition.segments;
  while (segs.length > count) _dropped.unshift(segs.pop());
  while (segs.length < count) {
    segs.push(_dropped.length ? _dropped.shift()
                              : { name: `Stage ${segs.length + 1}`, options: [] });
  }
  _renderSegments();
}

async function _loadDefinition() {
  try {
    const def = await _fetchJson(BAR_API);
    _definition = def && Array.isArray(def.segments) ? def : { segments: [] };
  } catch (err) {
    _definition = { segments: [] };
    _status(err.message, true);
  }
  _dropped = [];
  const hasBar = _definition.segments.length > 0;
  $("tf-bar-editor")?.classList.toggle("hidden", !hasBar);
  $("tf-add-bar-btn")?.classList.toggle("hidden", hasBar);
  const countEl = $("tf-segment-count");
  if (countEl) countEl.value = String(_definition.segments.length);
  _renderSegments();
}

async function _saveDefinition() {
  try {
    const saved = await _fetchJson(BAR_API, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segments: _definition.segments }),
    });
    _definition = { segments: saved.segments || [] };
    _dropped = [];
    _renderSegments();
    _status("Progress bar saved.");
    await _tracked?.refresh();     // rows must repaint against the new definition
  } catch (err) {
    _status(`Could not save: ${err.message}`, true);
  }
}

// ── Wiring ──────────────────────────────────────────────────────────────────

const card = $("tracked-files-card");
if (card) {
  _tracked = makeTrackedFiles({
    refreshBtn: $("tf-refresh"),
    panelEl: card,
    listEl: $("tf-list"),
    onOpen: () => {},          // this card manages files; it does not open videos
    onError: (msg) => _status(msg, true),
  });

  $("btn-open-progress-tracking")?.addEventListener("click", async () => {
    card.classList.remove("hidden");
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    _status("");
    await _loadDefinition();
    await _tracked.refresh();
  });
  $("btn-close-tracked-files")?.addEventListener("click", () => card.classList.add("hidden"));
  $("tf-add-bar-btn")?.addEventListener("click", async () => {
    _definition = { segments: [{ name: "Stage 1", options: [] }] };
    await _saveDefinition();
    await _loadDefinition();
  });
  $("tf-segment-count")?.addEventListener("change", (e) => _applyCount(e.target.value));
  $("tf-save-bar-btn")?.addEventListener("click", _saveDefinition);
}
