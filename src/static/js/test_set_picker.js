"use strict";
import { drawFrame, drawBodyparts } from "./frame_overlay.js";

const tsCard       = document.getElementById("test-set-picker-card");
const tsOpenBtn    = document.getElementById("btn-open-test-set-picker");
const tsCloseBtn   = document.getElementById("btn-close-test-set-picker");
const tsStemSelect = document.getElementById("ts-stem-select");
const tsRefreshBtn = document.getElementById("ts-refresh-btn");
const tsPlayerSec  = document.getElementById("ts-player-section");
const tsBtnPrev    = document.getElementById("ts-btn-prev");
const tsBtnNext    = document.getElementById("ts-btn-next");
const tsFrameInfo  = document.getElementById("ts-frame-info");
const tsFrameName  = document.getElementById("ts-frame-name");
const tsToggleBtn  = document.getElementById("ts-toggle-mark");
const tsMarkLabel  = document.getElementById("ts-mark-label");
const tsCanvas     = document.getElementById("ts-canvas");
const tsCtx        = tsCanvas ? tsCanvas.getContext("2d") : null;
const tsZoom       = document.getElementById("ts-zoom");
const tsZoomVal    = document.getElementById("ts-zoom-val");
const tsMarkerSize = document.getElementById("ts-marker-size");
const tsMarkerSizeVal = document.getElementById("ts-marker-size-val");
const tsShowNames  = document.getElementById("ts-show-names");
const tsFolderCntr = document.getElementById("ts-folder-counter");
const tsProjectCntr= document.getElementById("ts-project-counter");
const tsCleanStale = document.getElementById("ts-clean-stale-btn");
const tsInspectBtn = document.getElementById("ts-inspect-btn");

// ── State ────────────────────────────────────────────────────────
let _tsStems   = [];      // [{video_stem, frames[]}]
let _tsStem    = null;    // currently-selected stem string
let _tsFrames  = [];      // frames in the current stem
let _tsIdx     = 0;       // current frame index
let _tsMarks   = {};      // { stem: Set(image) }
let _tsLabels  = {};      // { image: { bp: [x,y] } } | label dict
let _tsImage   = null;    // currently-loaded Image
let _tsPlacement = null;  // placement returned by drawFrame
let _tsBodyparts = [];
let _tsPalette = {};
let _tsProjectTotal = 0;

const TS_DEFAULT_PALETTE = [
  "#ff5050", "#50c8ff", "#a0e040", "#ffa040", "#c060ff",
  "#40e0c0", "#ff7090", "#80c080", "#f0c020", "#60a0ff",
];

function _buildPalette(bps) {
  const out = {};
  bps.forEach((bp, i) => { out[bp] = TS_DEFAULT_PALETTE[i % TS_DEFAULT_PALETTE.length]; });
  return out;
}

async function _fetchJson(url, opts) {
  const rv = await fetch(url, opts);
  if (!rv.ok) throw new Error(await rv.text());
  return rv.json();
}

async function _loadStems() {
  // dlc_list_labeled_frames returns { video_stems: [{video_stem, frames[]}, ...] }
  const body = await _fetchJson("/dlc/project/labeled-frames");
  _tsStems = body.video_stems || body.frames || (Array.isArray(body) ? body : []);
  tsStemSelect.innerHTML = '<option value="">— select video —</option>';
  for (const s of _tsStems) {
    const stem = s.video_stem || s;
    const opt = document.createElement("option");
    opt.value = stem; opt.textContent = stem;
    tsStemSelect.appendChild(opt);
  }
}

async function _loadMarks() {
  const body = await _fetchJson("/dlc/project/test-set/marks");
  _tsMarks = {};
  for (const [stem, list] of Object.entries(body.marks || {})) {
    _tsMarks[stem] = new Set(list);
  }
  _tsProjectTotal = body.counts?.total_labeled || 0;
  _updateCounters(body.counts || {});
  const mode = body.mode || "random";
  document.querySelectorAll('input[name="ts-mode"]').forEach(el => {
    el.checked = (el.value === mode);
  });
}

async function _loadBodyparts() {
  const body = await _fetchJson("/dlc/project/bodyparts");
  _tsBodyparts = body.bodyparts || [];
  _tsPalette = _buildPalette(_tsBodyparts);
}

async function _loadStemFrames(stem) {
  const found = _tsStems.find(s => (s.video_stem || s) === stem);
  _tsFrames = (found && found.frames) || [];
  _tsIdx = 0;
  // Fetch labels for the stem (read-only display)
  try {
    const body = await _fetchJson(`/dlc/project/labels/${encodeURIComponent(stem)}`);
    _tsLabels = body.labels || {};
  } catch {
    _tsLabels = {};
  }
}

function _updateCounters(counts) {
  const stemCount = (_tsMarks[_tsStem] || new Set()).size;
  const folderTotal = _tsFrames.length;
  if (tsFolderCntr) tsFolderCntr.textContent = `${stemCount} / ${folderTotal} marked in this folder`;
  const projMarked = Object.values(_tsMarks).reduce((acc, s) => acc + s.size, 0);
  const projTotal = counts.total_labeled ?? _tsProjectTotal;
  if (tsProjectCntr) tsProjectCntr.textContent = `${projMarked} / ${projTotal} marked in project`;
}

function _currentFrameName() {
  return _tsFrames[_tsIdx] || "";
}

function _isCurrentMarked() {
  if (!_tsStem) return false;
  const s = _tsMarks[_tsStem] || new Set();
  return s.has(_currentFrameName());
}

function _updateToggleButton() {
  if (!tsMarkLabel) return;
  const m = _isCurrentMarked();
  tsMarkLabel.textContent = m ? "✓ In test set" : "▢ Mark for test set";
  tsToggleBtn.style.background = m ? "rgba(80, 200, 120, 0.18)" : "";
  tsToggleBtn.style.borderColor = m ? "rgba(80, 200, 120, 0.6)" : "";
}

function _draw() {
  if (!tsCtx) return;
  const placement = drawFrame(tsCtx, _tsImage);
  _tsPlacement = placement || null;
  const name = _currentFrameName();
  const labels = _tsLabels[name] || _tsLabels[`labeled-data/${_tsStem}/${name}`] || {};
  const markerSize = parseInt(tsMarkerSize?.value || "4");
  drawBodyparts(tsCtx, labels, _tsPalette, _tsPlacement, {
    markerSize,
    showNames: !!tsShowNames?.checked,
  });
}

// Size the canvas to fit the card width (× zoom), with height proportional
// to the image's aspect ratio so drawFrame fills it edge-to-edge. Same
// approach as frame_labeler.js → _flFitCanvas.
function _fitCanvas() {
  if (!tsCanvas || !_tsImage) return;
  const card = tsCard;
  if (!card) return;
  const cs = getComputedStyle(card);
  const padL = parseFloat(cs.paddingLeft) || 0;
  const padR = parseFloat(cs.paddingRight) || 0;
  const baseW = card.clientWidth - padL - padR;
  const zoom = parseInt(tsZoom?.value || "100") / 100;
  const maxW = Math.max(baseW, window.innerWidth - 32);
  const targetW = Math.min(Math.round(baseW * zoom), Math.floor(maxW));
  const naturalW = _tsImage.naturalWidth || _tsImage.width || 1;
  const naturalH = _tsImage.naturalHeight || _tsImage.height || 1;
  tsCanvas.width = targetW;
  tsCanvas.height = Math.round(naturalH * (targetW / naturalW));

  // Break out of card padding symmetrically (matches labeler behavior)
  const wrap = tsCanvas.parentElement;
  const extra = targetW - baseW;
  if (extra > 0) {
    wrap.style.width = targetW + "px";
    wrap.style.marginLeft = `-${extra / 2}px`;
  } else {
    wrap.style.width = "";
    wrap.style.marginLeft = "";
  }
}

function _renderFrame() {
  const name = _currentFrameName();
  if (!name) { tsFrameInfo.textContent = "Frame 0 / 0"; tsFrameName.textContent = ""; return; }
  tsFrameInfo.textContent = `Frame ${_tsIdx + 1} / ${_tsFrames.length}`;
  tsFrameName.textContent = name;
  _updateToggleButton();
  const img = new Image();
  img.onload = () => { _tsImage = img; _fitCanvas(); _draw(); };
  img.src = `/dlc/project/frame-image/${encodeURIComponent(_tsStem)}/${encodeURIComponent(name)}`;
}

// Re-fit on window/card resize so the canvas stays bounded.
if (typeof ResizeObserver !== "undefined" && tsCard) {
  new ResizeObserver(() => { if (_tsImage) { _fitCanvas(); _draw(); } }).observe(tsCard);
}

async function _toggleCurrentMark() {
  if (!_tsStem || !_currentFrameName()) return;
  const name = _currentFrameName();
  const willBeMarked = !_isCurrentMarked();
  // Optimistic
  if (willBeMarked) (_tsMarks[_tsStem] ||= new Set()).add(name);
  else _tsMarks[_tsStem]?.delete(name);
  _updateToggleButton();
  _updateCounters({});
  try {
    await _fetchJson(
      `/dlc/project/test-set/marks/${encodeURIComponent(_tsStem)}/${encodeURIComponent(name)}`,
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ marked: willBeMarked }) },
    );
  } catch (e) {
    // Rollback on error
    if (willBeMarked) _tsMarks[_tsStem]?.delete(name);
    else (_tsMarks[_tsStem] ||= new Set()).add(name);
    _updateToggleButton();
    _updateCounters({});
    console.error("toggle failed:", e);
  }
}

async function _setMode(mode) {
  try {
    await _fetchJson("/dlc/project/test-set/mode", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
  } catch (e) {
    console.error("mode update failed:", e);
  }
}

async function _openPicker() {
  if (tsCard) tsCard.classList.remove("hidden");
  await Promise.all([_loadBodyparts(), _loadStems(), _loadMarks()]);
}
function _closePicker() {
  if (tsCard) tsCard.classList.add("hidden");
}

function _onStemChange() {
  _tsStem = tsStemSelect.value || null;
  if (!_tsStem) { tsPlayerSec.classList.add("hidden"); return; }
  tsPlayerSec.classList.remove("hidden");
  _loadStemFrames(_tsStem).then(() => { _renderFrame(); _updateCounters({}); });
}

function _next() { if (_tsIdx < _tsFrames.length - 1) { _tsIdx++; _renderFrame(); } }
function _prev() { if (_tsIdx > 0) { _tsIdx--; _renderFrame(); } }
function _firstInFolder() { if (_tsFrames.length) { _tsIdx = 0; _renderFrame(); } }
function _lastInFolder()  { if (_tsFrames.length) { _tsIdx = _tsFrames.length - 1; _renderFrame(); } }
function _nextFolder() {
  if (!_tsStems.length) return;
  const cur = _tsStems.findIndex(s => (s.video_stem || s) === _tsStem);
  const next = (cur + 1) % _tsStems.length;
  const stem = _tsStems[next].video_stem || _tsStems[next];
  tsStemSelect.value = stem;
  _onStemChange();
}
function _prevFolder() {
  if (!_tsStems.length) return;
  const cur = _tsStems.findIndex(s => (s.video_stem || s) === _tsStem);
  const prev = (cur - 1 + _tsStems.length) % _tsStems.length;
  const stem = _tsStems[prev].video_stem || _tsStems[prev];
  tsStemSelect.value = stem;
  _onStemChange();
}
function _cycleMode() {
  const modes = ["random", "hybrid", "manual"];
  const cur = [...document.querySelectorAll('input[name="ts-mode"]')].find(el => el.checked)?.value || "random";
  const next = modes[(modes.indexOf(cur) + 1) % modes.length];
  document.querySelectorAll('input[name="ts-mode"]').forEach(el => { el.checked = (el.value === next); });
  _setMode(next);
}

// ── Wire up ─────────────────────────────────────────────────────
if (tsOpenBtn)  tsOpenBtn.addEventListener("click", _openPicker);
if (tsCloseBtn) tsCloseBtn.addEventListener("click", _closePicker);
if (tsRefreshBtn) tsRefreshBtn.addEventListener("click", () => _loadStems());
if (tsStemSelect) tsStemSelect.addEventListener("change", _onStemChange);
if (tsBtnPrev)  tsBtnPrev.addEventListener("click", _prev);
if (tsBtnNext)  tsBtnNext.addEventListener("click", _next);
if (tsToggleBtn) tsToggleBtn.addEventListener("click", _toggleCurrentMark);
if (tsZoom)      tsZoom.addEventListener("input", () => {
  tsZoomVal.textContent = `${tsZoom.value} %`;
  if (_tsImage) { _fitCanvas(); _draw(); }
});
if (tsMarkerSize) tsMarkerSize.addEventListener("input", () => { tsMarkerSizeVal.textContent = tsMarkerSize.value; _draw(); });
if (tsShowNames) tsShowNames.addEventListener("change", _draw);
if (tsCleanStale) tsCleanStale.addEventListener("click", async () => {
  await _fetchJson("/dlc/project/test-set/marks/clean-stale", { method: "POST" });
  await _loadMarks();
  _updateCounters({});
});
document.querySelectorAll('input[name="ts-mode"]').forEach(el => {
  el.addEventListener("change", () => { if (el.checked) _setMode(el.value); });
});

document.addEventListener("keydown", (ev) => {
  if (!tsCard || tsCard.classList.contains("hidden")) return;
  if (ev.target && /input|textarea|select/i.test(ev.target.tagName)) return;
  switch (ev.key) {
    case "ArrowLeft":  if (ev.shiftKey) _prevFolder(); else _prev(); ev.preventDefault(); break;
    case "ArrowRight": if (ev.shiftKey) _nextFolder(); else _next(); ev.preventDefault(); break;
    case "Home": _firstInFolder(); ev.preventDefault(); break;
    case "End":  _lastInFolder();  ev.preventDefault(); break;
    case "t": case "T": _toggleCurrentMark(); ev.preventDefault(); break;
    case "m": case "M": _cycleMode(); ev.preventDefault(); break;
    case "Escape": _closePicker(); break;
  }
});

// ── Inspect mode ────────────────────────────────────────────────
const tsInspectDialog = document.getElementById("ts-inspect-dialog");
const tsInspectIter   = document.getElementById("ts-inspect-iter");
const tsInspectShuffle= document.getElementById("ts-inspect-shuffle");
const tsInspectCancel = document.getElementById("ts-inspect-cancel");
const tsInspectGo     = document.getElementById("ts-inspect-go");
const tsInspectBanner = document.getElementById("ts-inspect-banner");
const tsInspectBannerTxt = document.getElementById("ts-inspect-banner-text");
const tsInspectExit   = document.getElementById("ts-inspect-exit");

let _tsInspect = null;  // null = picker mode; otherwise { iteration, shuffle, trainSet, testSet, trainFraction }

function _openInspectDialog() {
  if (tsInspectDialog) tsInspectDialog.classList.remove("hidden");
}
function _closeInspectDialog() {
  if (tsInspectDialog) tsInspectDialog.classList.add("hidden");
}

async function _runInspect() {
  const iter = parseInt(tsInspectIter.value || "0");
  const shuffle = parseInt(tsInspectShuffle.value || "1");
  try {
    const body = await _fetchJson(
      `/dlc/project/training-dataset/inspect?iteration=${iter}&shuffle=${shuffle}`,
    );
    const ds = (body.datasets || []).find(d => d.shuffle === shuffle)
            || (body.datasets || [])[0];
    if (!ds) {
      alert(`No frozen split found for iteration ${iter} / shuffle ${shuffle}.`);
      return;
    }
    const trainSet = new Set(ds.train.map(d => `${d.video_stem}|${d.image_name}`));
    const testSet  = new Set(ds.test.map(d  => `${d.video_stem}|${d.image_name}`));
    _tsInspect = {
      iteration: iter, shuffle, trainSet, testSet,
      trainFraction: ds.train_fraction,
    };
    if (tsInspectBanner) tsInspectBanner.classList.remove("hidden");
    if (tsInspectBannerTxt) tsInspectBannerTxt.textContent =
      `Inspecting iteration-${iter} / shuffle-${shuffle} ` +
      `(trainset${Math.round(ds.train_fraction * 100)}) — read-only`;
    _closeInspectDialog();
    _renderFrameInspectAware();
  } catch (e) {
    alert(`Inspect failed: ${e.message || e}`);
  }
}

function _exitInspect() {
  _tsInspect = null;
  if (tsInspectBanner) tsInspectBanner.classList.add("hidden");
  // Re-enable the toggle button (it was disabled in inspect mode)
  if (tsToggleBtn) tsToggleBtn.disabled = false;
  _renderFrameInspectAware();
}

function _updateToggleButtonInspectAware() {
  if (!_tsInspect) {
    // Picker mode — use the normal toggle rendering
    _updateToggleButton();
    if (tsToggleBtn) tsToggleBtn.disabled = false;
    return;
  }
  if (!tsMarkLabel) return;
  const key = `${_tsStem}|${_currentFrameName()}`;
  const inTrain = _tsInspect.trainSet.has(key);
  const inTest  = _tsInspect.testSet.has(key);
  if (inTest) {
    tsMarkLabel.textContent = "TEST";
    tsToggleBtn.style.background = "rgba(255,170,40,0.18)";
    tsToggleBtn.style.borderColor = "rgba(255,170,40,0.7)";
  } else if (inTrain) {
    tsMarkLabel.textContent = "TRAIN";
    tsToggleBtn.style.background = "rgba(80,200,255,0.14)";
    tsToggleBtn.style.borderColor = "rgba(80,200,255,0.6)";
  } else {
    tsMarkLabel.textContent = "—";
    tsToggleBtn.style.background = "";
    tsToggleBtn.style.borderColor = "";
  }
  if (tsToggleBtn) tsToggleBtn.disabled = true;
}

function _renderFrameInspectAware() {
  _renderFrame();
  _updateToggleButtonInspectAware();
}

// Wire up inspect buttons
if (tsInspectBtn)    tsInspectBtn.addEventListener("click", _openInspectDialog);
if (tsInspectCancel) tsInspectCancel.addEventListener("click", _closeInspectDialog);
if (tsInspectGo)     tsInspectGo.addEventListener("click", _runInspect);
if (tsInspectExit)   tsInspectExit.addEventListener("click", _exitInspect);

// Replace nav-button click handlers so they call the inspect-aware update too.
// We do this by adding a SECOND listener — the original picker-mode handler
// (added in Task 7) still runs first and updates _tsIdx + redraws. Our new
// listener then patches the toggle-button render.
if (tsBtnPrev) tsBtnPrev.addEventListener("click", _updateToggleButtonInspectAware);
if (tsBtnNext) tsBtnNext.addEventListener("click", _updateToggleButtonInspectAware);
if (tsStemSelect) tsStemSelect.addEventListener("change", _updateToggleButtonInspectAware);

// In picker mode, the keyboard handler also calls _renderFrame via _next/_prev/etc.
// Patch the keyboard handler at the document level: after any key fires that
// changed the frame, re-run inspect-aware rendering. We attach a CAPTURING
// listener that runs AFTER the existing one (which uses default bubble phase
// at line ~250 of this file).
document.addEventListener("keyup", (ev) => {
  if (!tsCard || tsCard.classList.contains("hidden")) return;
  if (ev.target && /input|textarea|select/i.test(ev.target.tagName)) return;
  if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(ev.key)) {
    _updateToggleButtonInspectAware();
  }
});

// Suppress mark-toggling in inspect mode by intercepting the toggle button click.
if (tsToggleBtn) {
  tsToggleBtn.addEventListener("click", (ev) => {
    if (_tsInspect) ev.stopImmediatePropagation();
  }, true /* capture: run before the toggle handler */);
}
