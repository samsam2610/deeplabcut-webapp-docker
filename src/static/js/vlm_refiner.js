/**
 * VLM Label Refiner — frontend logic.
 *
 * Three-panel verification dashboard:
 *   Panel 1  Reference  — most-similar human-labeled frame from index
 *   Panel 2  Active     — current frame being verified/corrected (canvas)
 *   Panel 3  Crop+Coords — 128×128 crop around selected bodypart + coord list
 *
 * Three label layers (A/B/V toggle):
 *   M  Machine — raw coords from CSV (ground truth as-is)
 *   V  VLM     — coords suggested by qwen3-vl
 *   H  Human   — user overrides (or accepted VLM coords)
 *
 * Persistence: H layer is saved back to CSV via POST /dlc/project/labels/<stem>
 *              "Commit to H5" triggers /dlc/project/labels/convert-to-h5
 */

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  stems: [],           // [{video_stem, frames: [name,...]}]
  activeStem: null,
  activeFrame: null,
  allFrames: [],       // current stem's frames: [{name, lh, hasVlm}]
  filteredFrames: [],
  lhThreshold: 0,
  hasRawPredictions: false,  // true when _machine_predictions_raw.csv exists for active stem
  vlmFrames: new Set(),      // frame names in this stem that have a saved VLM result

  bodyparts: [],
  scorer: '',
  indexAvailable: false,
  indexStems: [],      // all stems present in the index

  referenceStem: '',   // '' = any other stem; set to a specific stem to pin folder

  similar: [],         // top-5 similar [{video_stem, frame, score, labels}]
  activeRefIdx: 0,     // which similar frame is shown in panel 1

  mode: 'M',           // 'M' | 'V' | 'H'

  // Label layers for the active frame
  machineCoords: {},   // {bp: [x,y] | null}  — from CSV
  vlmCoords:     {},   // {bp: [x,y] | null}  — from VLM
  vlmDebug:      {},   // {bp: {reason, dx, dy, correct}} — from last refine
  humanCoords:   {},   // {bp: [x,y] | null}  — user edits

  selectedBp: null,    // currently selected bodypart for canvas clicks
  activeImg: null,     // HTMLImageElement for active frame
  refImg: null,        // HTMLImageElement for reference frame
};

// ── DOM refs ──────────────────────────────────────────────────────────────────
const els = {
  noProject:      document.getElementById('vlm-no-project'),
  stemSelect:     document.getElementById('vlm-stem-select'),
  lhFilter:       document.getElementById('vlm-lh-filter'),
  lhVal:          document.getElementById('vlm-lh-val'),
  frameCount:     document.getElementById('vlm-frame-count'),
  frameList:      document.getElementById('vlm-frame-list'),

  indexBadge:     document.getElementById('vlm-index-badge'),
  btnBuildIndex:  document.getElementById('btn-build-index'),
  chkOllama:      document.getElementById('chk-use-ollama'),

  refStemSelect:  document.getElementById('vlm-ref-stem-select'),
  refTabs:        document.getElementById('vlm-ref-tabs'),
  refCanvas:      document.getElementById('ref-canvas'),
  refPlaceholder: document.getElementById('ref-placeholder'),
  refScoreBadge:  document.getElementById('ref-score-badge'),
  refFooter:      document.getElementById('ref-footer'),

  activeCanvas:   document.getElementById('vlm-active-canvas'),
  activePlaceholder: document.getElementById('active-placeholder'),
  activeFrameName:document.getElementById('active-frame-name'),

  cropCanvas:     document.getElementById('vlm-crop-canvas'),
  cropPlaceholder:document.getElementById('crop-placeholder'),

  coordList:      document.getElementById('vlm-coord-list'),
  toggleBtns:     document.querySelectorAll('.vlm-toggle-btn'),

  btnRefine:      document.getElementById('btn-vlm-refine'),
  btnAcceptVlm:   document.getElementById('btn-accept-vlm'),
  btnSave:        document.getElementById('btn-save-labels'),
  btnCommitH5:    document.getElementById('btn-commit-h5'),

  globalStatus:   document.getElementById('vlm-global-status'),
  refineStatus:   document.getElementById('vlm-refine-status'),
};

const activeCtx  = els.activeCanvas.getContext('2d');
const refCtx     = els.refCanvas.getContext('2d');
const cropCtx    = els.cropCanvas.getContext('2d');

// ── Helpers ───────────────────────────────────────────────────────────────────
function setStatus(el, msg, type = '') {
  el.textContent = msg;
  el.className   = 'vlm-status' + (type ? ` ${type}` : '');
  if (type === 'ok' || type === 'err') {
    setTimeout(() => { el.textContent = ''; el.className = 'vlm-status'; }, 6000);
  }
}

async function apiFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

function currentCoords() {
  if (state.mode === 'V') return state.vlmCoords;
  if (state.mode === 'H') return state.humanCoords;
  return state.machineCoords;
}

// ── Initialise ────────────────────────────────────────────────────────────────
async function init() {
  await checkProject();
  setupEventListeners();
}

async function checkProject() {
  // Probe index status — if it errors with 400 "No active project", show overlay
  const { ok, data } = await apiFetch('/vlm/index-status');
  if (!ok && data.error && data.error.includes('active')) {
    els.noProject.style.display = 'flex';
    return;
  }
  els.noProject.style.display = 'none';

  updateIndexBadge(data);
  await loadIndexStems();
  await loadStems();
}

async function loadIndexStems() {
  const { ok, data } = await apiFetch('/vlm/index-stems');
  if (!ok) return;
  state.indexStems = data.stems || [];
  populateRefStemSelect();
}

function populateRefStemSelect() {
  const sel = els.refStemSelect;
  const prev = sel.value;
  sel.innerHTML = '<option value="">Any (best match)</option>';
  state.indexStems
    .filter(s => s !== state.activeStem)
    .forEach(s => {
      const opt = document.createElement('option');
      opt.value = s;
      opt.textContent = s;
      sel.appendChild(opt);
    });
  // Restore previous selection if still valid
  if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
  else { sel.value = ''; state.referenceStem = ''; }
}

function updateIndexBadge(data) {
  state.indexAvailable = data.exists || false;
  if (data.exists) {
    let builtLabel = '';
    if (data.built_at) {
      try {
        const d = new Date(data.built_at);
        builtLabel = ` · ${d.toLocaleDateString()} ${d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}`;
      } catch {}
    }
    els.indexBadge.innerHTML =
      `<span style="color:var(--accent)">●</span> Index: ${data.total_frames} frames${builtLabel}`;
    els.indexBadge.title = data.built_at ? `Built at ${data.built_at}` : '';
    els.indexBadge.style.color = 'var(--text)';
    els.indexBadge.style.borderColor = 'var(--accent-dim)';
    els.indexBadge.style.background = 'rgba(99,102,241,.08)';
    els.btnBuildIndex.textContent = '';
    els.btnBuildIndex.insertAdjacentHTML('afterbegin',
      '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Rebuild Index'
    );
    els.btnBuildIndex.title = 'Rebuild and overwrite the existing index';
  } else {
    els.indexBadge.innerHTML =
      `<span style="color:var(--text-dim)">○</span> Index: not built`;
    els.indexBadge.title = 'Build the index to enable reference-frame search';
    els.indexBadge.style.color = 'var(--text-dim)';
    els.indexBadge.style.borderColor = '';
    els.indexBadge.style.background = '';
    els.btnBuildIndex.textContent = '';
    els.btnBuildIndex.insertAdjacentHTML('afterbegin',
      '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg> Build Index'
    );
    els.btnBuildIndex.title = 'Build the visual similarity index for this project';
  }
}

// ── Stems / frames ─────────────────────────────────────────────────────────────
async function loadStems() {
  const { ok, data } = await apiFetch('/dlc/project/labeled-frames');
  if (!ok) return;
  state.stems = data.video_stems || [];

  // Repopulate select
  els.stemSelect.innerHTML = '<option value="">— select folder —</option>';
  state.stems.forEach(({ video_stem }) => {
    const opt = document.createElement('option');
    opt.value = video_stem;
    opt.textContent = video_stem;
    els.stemSelect.appendChild(opt);
  });
}

function buildFrameList(stem) {
  const stemData = state.stems.find(s => s.video_stem === stem);
  if (!stemData) { state.allFrames = []; return; }

  state.allFrames = stemData.frames.map(name => ({ name, lh: null }));
  state.vlmFrames = new Set();
  applyFilter();
  // Load per-frame metadata in the background (lh badges + VLM indicators)
  loadStemLikelihoods(stem);
  loadStemVlmFrames(stem);
}

async function loadStemLikelihoods(stem) {
  const { ok, data } = await apiFetch(
    `/vlm/stem-likelihoods?video_stem=${encodeURIComponent(stem)}`
  );
  if (!ok || !data.likelihoods) return;
  const lhMap = data.likelihoods;
  state.allFrames = state.allFrames.map(f => ({
    ...f,
    lh: lhMap[f.name] !== undefined ? lhMap[f.name] : f.lh,
  }));
  applyFilter();
}

async function loadStemVlmFrames(stem) {
  const { ok, data } = await apiFetch(
    `/vlm/stem-vlm-frames?video_stem=${encodeURIComponent(stem)}`
  );
  if (!ok || !data.frames) return;
  state.vlmFrames = new Set(data.frames);
  applyFilter();  // re-render to show/update VLM badges
}

function updateLhSliderState() {
  const active = state.hasRawPredictions;
  els.lhFilter.disabled = !active;
  const hint = active ? '' : ' (re-run machine labeling to enable)';
  if (els.lhVal) els.lhVal.title = active ? '' : 'No raw predictions found' + hint;
}

function applyFilter() {
  // All frames are always shown. The lh slider controls bodypart visibility
  // within each frame (via min_lh passed to /vlm/frame-data), not which
  // frames appear in the list.
  state.filteredFrames = state.allFrames;
  renderFrameList();
}

function renderFrameList() {
  const list = els.frameList;
  list.innerHTML = '';
  els.frameCount.textContent = `${state.allFrames.length} frames`;

  if (!state.filteredFrames.length) {
    list.innerHTML = '<div style="padding:.5rem;color:var(--text-dim);font-size:.76rem">No frames.</div>';
    return;
  }

  state.filteredFrames.forEach(({ name, lh }) => {
    const item = document.createElement('div');
    item.className = 'vlm-frame-item' + (name === state.activeFrame ? ' active' : '');
    item.dataset.frame = name;

    const label = document.createElement('span');
    label.textContent = name;
    label.style.overflow = 'hidden';
    label.style.textOverflow = 'ellipsis';
    label.style.whiteSpace = 'nowrap';
    item.appendChild(label);

    // VLM result badge
    if (state.vlmFrames.has(name)) {
      const vbadge = document.createElement('span');
      vbadge.title = 'VLM result saved';
      vbadge.style.cssText = 'font-size:.6rem;padding:.05rem .25rem;border-radius:3px;background:rgba(251,191,36,.18);color:#fbbf24;flex-shrink:0';
      vbadge.textContent = 'V';
      item.appendChild(vbadge);
    }

    // Likelihood badge
    if (lh !== null) {
      const badge = document.createElement('span');
      badge.className = `vlm-lh-badge ${lh < 0.5 ? 'low' : lh < 0.9 ? 'mid' : 'high'}`;
      badge.textContent = lh.toFixed(2);
      item.appendChild(badge);
    }

    item.addEventListener('click', () => selectFrame(name));
    list.appendChild(item);
  });
}

// ── Frame selection ───────────────────────────────────────────────────────────

/**
 * Build the frame-data URL (shared by selectFrame and reloadMachineCoords).
 */
function _frameDataUrl(stem, frame) {
  const refParam = state.referenceStem
    ? `&reference_stem=${encodeURIComponent(state.referenceStem)}` : '';
  const lhParam  = `&min_lh=${state.lhThreshold}`;
  return `/vlm/frame-data?video_stem=${encodeURIComponent(stem)}&frame=${encodeURIComponent(frame)}${refParam}${lhParam}`;
}

/**
 * Reload only the machine-coord layer (M) for the currently active frame.
 * Does NOT reset the VLM (V) or human (H) layers — used by the likelihood
 * slider and reference-stem picker so in-progress VLM results are preserved.
 */
async function reloadMachineCoords() {
  if (!state.activeFrame) return;
  const { ok, data } = await apiFetch(_frameDataUrl(state.activeStem, state.activeFrame));
  if (!ok) return;
  state.machineCoords = data.current_labels || {};
  state.hasRawPredictions = !!data.has_raw_predictions;
  state.similar      = data.similar    || [];
  state.matchType    = data.match_type || 'none';
  state.activeRefIdx = 0;
  updateLhSliderState();
  renderRefTabs();
  loadRefImage(0);
  renderCoordList();
  drawActiveCanvas();
}

async function selectFrame(frameName) {
  state.activeFrame = frameName;
  // Full reset of all label layers when switching to a new frame
  state.vlmCoords   = {};
  state.vlmDebug    = {};
  state.humanCoords = {};
  els.btnAcceptVlm.disabled = true;
  els.btnRefine.disabled    = false;
  els.btnSave.disabled      = false;
  els.activeFrameName.textContent = frameName;

  // Highlight in list
  document.querySelectorAll('.vlm-frame-item').forEach(el => {
    el.classList.toggle('active', el.dataset.frame === frameName);
  });

  // Fetch frame data (labels + similar) from combined endpoint
  setStatus(els.globalStatus, 'Loading…');
  const { ok, data } = await apiFetch(_frameDataUrl(state.activeStem, frameName));
  if (!ok) { setStatus(els.globalStatus, data.error || 'Error loading frame', 'err'); return; }

  state.bodyparts = data.bodyparts || [];
  state.scorer    = data.scorer    || 'User';
  state.machineCoords = data.current_labels || {};
  state.hasRawPredictions = !!data.has_raw_predictions;
  updateLhSliderState();
  state.similar    = data.similar   || [];
  state.matchType  = data.match_type || 'none';
  state.activeRefIdx = 0;

  // Restore saved VLM result (persisted from a previous refine)
  if (data.vlm_coords && Object.keys(data.vlm_coords).length > 0) {
    state.vlmCoords = data.vlm_coords;
    state.vlmDebug  = data.vlm_debug || {};
    els.btnAcceptVlm.disabled = false;
    // Update the VLM frames set so the badge shows immediately
    state.vlmFrames.add(frameName);
  }

  // Update index stems if the response includes them
  if (data.index_stems && data.index_stems.length) {
    state.indexStems = data.index_stems;
    populateRefStemSelect();
  }

  // Clone machine → human (user's starting point)
  state.humanCoords = JSON.parse(JSON.stringify(state.machineCoords));

  // Load active frame image
  loadActiveImage(state.activeStem, frameName);

  // Load reference panel
  renderRefTabs();
  loadRefImage(0);

  renderCoordList();
  setStatus(els.globalStatus, 'ok', '');
}

// ── Image loading ─────────────────────────────────────────────────────────────
function loadActiveImage(stem, frame) {
  els.activePlaceholder.style.display = 'none';
  const img = new Image();
  img.onload = () => {
    state.activeImg = img;
    drawActiveCanvas();
  };
  img.onerror = () => { els.activePlaceholder.style.display = ''; };
  img.src = `/dlc/project/frame-image/${encodeURIComponent(stem)}/${encodeURIComponent(frame)}`;
}

function loadRefImage(idx) {
  const ref = state.similar[idx];
  if (!ref) {
    els.refCanvas.style.display = 'none';
    els.refPlaceholder.style.display = '';
    els.refFooter.textContent = state.indexAvailable
      ? 'No similar frames found'
      : 'Index not built — build index to enable reference panel';
    _setMatchBadge(null);
    return;
  }
  els.refPlaceholder.style.display = 'none';
  const img = new Image();
  img.onload = () => {
    state.refImg = img;
    drawRefCanvas(ref);
  };
  img.onerror = () => { els.refPlaceholder.style.display = ''; };
  img.src = `/vlm/reference-image/${encodeURIComponent(ref.video_stem)}/${encodeURIComponent(ref.frame)}`;
  _setMatchBadge(state.matchType);
  els.refFooter.textContent = `${ref.video_stem} / ${ref.frame}`;
}

function drawRefCanvas(ref) {
  if (!state.refImg) return;
  const img    = state.refImg;
  const canvas = els.refCanvas;
  const parent = canvas.parentElement;

  // Fit inside the panel, same logic as active canvas
  const maxW  = parent.clientWidth  - 4;
  const maxH  = parent.clientHeight - 4;
  const scale = Math.min(maxW / img.naturalWidth, maxH / img.naturalHeight, 1);

  canvas.width  = Math.round(img.naturalWidth  * scale);
  canvas.height = Math.round(img.naturalHeight * scale);
  canvas.style.display = 'block';

  // Draw the frame
  refCtx.drawImage(img, 0, 0, canvas.width, canvas.height);

  // Draw label dots — coordinates are in original image space, scale to canvas
  Object.entries(ref.labels || {}).forEach(([bp, pt]) => {
    if (!pt) return;
    const cx = pt[0] * scale;
    const cy = pt[1] * scale;

    refCtx.beginPath();
    refCtx.arc(cx, cy, 4, 0, Math.PI * 2);
    refCtx.fillStyle = 'rgba(110,231,183,0.85)';
    refCtx.fill();
    refCtx.strokeStyle = 'rgba(0,0,0,0.6)';
    refCtx.lineWidth = 1;
    refCtx.stroke();

    // Label text
    refCtx.font = '9px monospace';
    refCtx.fillStyle = 'rgba(110,231,183,0.9)';
    refCtx.fillText(bp, cx + 6, cy - 2);
  });
}

// ── Active canvas ─────────────────────────────────────────────────────────────
function drawActiveCanvas() {
  if (!state.activeImg) return;
  const img  = state.activeImg;
  const canvas = els.activeCanvas;

  // Fit inside panel
  const parent = canvas.parentElement;
  const maxW = parent.clientWidth  - 4;
  const maxH = parent.clientHeight - 4;
  const scale = Math.min(maxW / img.naturalWidth, maxH / img.naturalHeight, 1);

  canvas.width  = Math.round(img.naturalWidth  * scale);
  canvas.height = Math.round(img.naturalHeight * scale);
  canvas.style.display = 'block';

  activeCtx.drawImage(img, 0, 0, canvas.width, canvas.height);

  const coords = currentCoords();
  const COLORS = { M: '#6ee7b7', V: '#fbbf24', H: '#818cf8' };
  const color  = COLORS[state.mode];

  state.bodyparts.forEach((bp, i) => {
    const pt = coords[bp];
    if (!pt) return;
    const cx = pt[0] * scale;
    const cy = pt[1] * scale;
    const r  = bp === state.selectedBp ? 6 : 4;

    activeCtx.beginPath();
    activeCtx.arc(cx, cy, r, 0, Math.PI * 2);
    activeCtx.fillStyle = color;
    activeCtx.globalAlpha = 0.85;
    activeCtx.fill();
    activeCtx.globalAlpha = 1;
    activeCtx.strokeStyle = 'rgba(0,0,0,.6)';
    activeCtx.lineWidth = 1;
    activeCtx.stroke();

    if (bp === state.selectedBp) {
      activeCtx.font = '10px var(--mono, monospace)';
      activeCtx.fillStyle = color;
      activeCtx.fillText(bp, cx + 7, cy - 2);
      updateCropCanvas(pt[0], pt[1]);
    }
  });
}

function updateCropCanvas(cx, cy) {
  if (!state.activeImg) return;
  const img   = state.activeImg;
  const half  = 64;
  const sx    = Math.max(0, cx - half);
  const sy    = Math.max(0, cy - half);
  const sw    = Math.min(128, img.naturalWidth  - sx);
  const sh    = Math.min(128, img.naturalHeight - sy);

  els.cropCanvas.style.display = 'block';
  els.cropPlaceholder.style.display = 'none';
  cropCtx.clearRect(0, 0, 128, 128);
  cropCtx.drawImage(img, sx, sy, sw, sh, 0, 0, 128, 128);

  // Draw crosshair at centre
  cropCtx.strokeStyle = 'var(--accent)';
  cropCtx.lineWidth = 1;
  cropCtx.setLineDash([3, 2]);
  cropCtx.beginPath();
  cropCtx.moveTo(64, 0); cropCtx.lineTo(64, 128);
  cropCtx.moveTo(0, 64); cropCtx.lineTo(128, 64);
  cropCtx.stroke();
  cropCtx.setLineDash([]);
}

// Canvas click → place bodypart (only in H mode)
els.activeCanvas.addEventListener('click', e => {
  if (state.mode !== 'H' || !state.selectedBp || !state.activeImg) return;
  const rect  = els.activeCanvas.getBoundingClientRect();
  const scale = els.activeCanvas.width / state.activeImg.naturalWidth;
  const x     = (e.clientX - rect.left) / scale;
  const y     = (e.clientY - rect.top)  / scale;
  state.humanCoords[state.selectedBp] = [Math.round(x * 10) / 10, Math.round(y * 10) / 10];
  drawActiveCanvas();
  renderCoordList();
});

// ── Match-type badge ───────────────────────────────────────────────────────────
function _setMatchBadge(matchType) {
  const el = els.refScoreBadge;
  if (!matchType || matchType === 'none') {
    el.textContent = '';
    el.removeAttribute('style');
    return;
  }
  const isPosture = matchType === 'posture';
  el.textContent  = isPosture ? '⬡ Posture' : '◎ Pixel';
  el.style.cssText = [
    'font-size:.68rem',
    'font-weight:600',
    'letter-spacing:.03em',
    'padding:.15rem .45rem',
    'border-radius:99px',
    isPosture
      ? 'background:rgba(129,140,248,.18);border:1px solid rgba(129,140,248,.5);color:#a5b4fc'
      : 'background:rgba(100,116,139,.12);border:1px solid rgba(100,116,139,.35);color:var(--text-dim)',
  ].join(';');
}

// ── Reference tabs ─────────────────────────────────────────────────────────────
function renderRefTabs() {
  els.refTabs.innerHTML = '';
  if (!state.similar.length) {
    const msg = document.createElement('span');
    msg.style.cssText = 'font-size:.72rem;color:var(--text-dim);align-self:center';
    msg.textContent = state.indexAvailable ? 'No matches found' : 'Index not built';
    els.refTabs.appendChild(msg);
    _setMatchBadge(null);
    return;
  }
  state.similar.forEach((ref, i) => {
    const btn = document.createElement('button');
    btn.className = 'vlm-ref-tab' + (i === state.activeRefIdx ? ' active' : '');
    btn.textContent = `#${i + 1} · ${ref.score != null ? (ref.score * 100).toFixed(0) + '%' : '?'}`;
    btn.title = `${ref.video_stem}/${ref.frame}`;
    btn.addEventListener('click', () => {
      state.activeRefIdx = i;
      document.querySelectorAll('.vlm-ref-tab').forEach((b, j) => b.classList.toggle('active', j === i));
      loadRefImage(i);
    });
    els.refTabs.appendChild(btn);
  });
}

// ── Coord list (panel 3) ───────────────────────────────────────────────────────
function _debugDeltaEl(bp) {
  // Returns a small element showing VLM delta/reason, or null if no debug for this bp.
  const d = state.vlmDebug && state.vlmDebug[bp];
  if (!d) return null;

  const el = document.createElement('div');
  el.className = 'vlm-coord-delta';

  if (d.reason === 'ok') {
    const sign = n => (n >= 0 ? '+' : '') + Number(n).toFixed(0);
    const moved = d.dx !== 0 || d.dy !== 0;
    el.textContent = moved ? `Δ${sign(d.dx)},${sign(d.dy)}` : '✓';
    el.style.color = moved ? '#fbbf24' : 'var(--text-dim)';
    el.title = d.correct ? 'VLM: correct placement' : `VLM offset: dx=${d.dx}, dy=${d.dy}`;
  } else if (d.reason === 'no_ref_label') {
    el.textContent = 'no ref';
    el.style.color = '#f59e0b';
    el.title = 'Reference frame has no label for this bodypart — machine coord kept';
  } else if (d.reason === 'no_machine_coord') {
    el.textContent = 'no M';
    el.style.color = 'var(--text-dim)';
    el.title = 'No machine label to refine from';
  } else if (d.reason === 'ollama_failed') {
    el.textContent = 'VLM!';
    el.style.color = '#f87171';
    el.title = `Ollama call failed — machine coord kept${d.raw ? '\n' + d.raw : ''}`;
  } else if (d.reason === 'parse_failed') {
    el.textContent = 'parse?';
    el.style.color = '#fb923c';
    el.title = `VLM response could not be parsed — machine coord kept${d.raw ? '\n' + d.raw : ''}`;
  } else if (d.reason === 'crop_failed') {
    el.textContent = 'crop!';
    el.style.color = '#fb923c';
    el.title = 'Could not crop patch from image — coord out of bounds?';
  }
  return el;
}

function renderCoordList() {
  const coords = currentCoords();
  const hasDebug = state.vlmDebug && Object.keys(state.vlmDebug).length > 0;
  els.coordList.innerHTML = '';

  if (!state.bodyparts.length) {
    els.coordList.innerHTML = '<span style="color:var(--text-dim)">No bodyparts</span>';
    return;
  }

  // Header row when VLM debug is available
  if (hasDebug) {
    const hdr = document.createElement('div');
    hdr.style.cssText = 'font-size:.62rem;color:var(--text-dim);display:grid;grid-template-columns:80px 1fr auto;gap:.3rem;padding-bottom:.2rem;border-bottom:1px solid var(--border);margin-bottom:.2rem';
    hdr.innerHTML = '<span>bodypart</span><span>coord</span><span>vlm</span>';
    els.coordList.appendChild(hdr);
  }

  state.bodyparts.forEach(bp => {
    const pt  = coords[bp];
    const row = document.createElement('div');
    row.className = 'vlm-coord-row';
    row.style.cursor = 'pointer';
    row.title = `Click to select ${bp}`;
    if (bp === state.selectedBp) row.style.background = 'rgba(110,231,183,.05)';

    const nameEl = document.createElement('div');
    nameEl.className = 'vlm-coord-name';
    nameEl.textContent = bp;

    const valEl = document.createElement('div');
    valEl.className = 'vlm-coord-val' + (pt ? ' has-val' : '');
    valEl.textContent = pt ? `${pt[0].toFixed(1)},${pt[1].toFixed(1)}` : 'null';

    row.appendChild(nameEl);
    row.appendChild(valEl);

    // Third column: VLM delta (always rendered to keep grid alignment)
    const deltaEl = _debugDeltaEl(bp) || document.createElement('div');
    deltaEl.className = (deltaEl.className || '') + ' vlm-coord-delta';
    row.appendChild(deltaEl);

    row.addEventListener('click', () => {
      state.selectedBp = bp;
      drawActiveCanvas();
      renderCoordList();
    });
    els.coordList.appendChild(row);
  });
}

// ── A/B/V toggle ─────────────────────────────────────────────────────────────
els.toggleBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    state.mode = btn.dataset.mode;
    els.toggleBtns.forEach(b => b.classList.toggle('active', b === btn));
    drawActiveCanvas();
    renderCoordList();
  });
});

// ── Build index ───────────────────────────────────────────────────────────────
els.btnBuildIndex.addEventListener('click', async () => {
  els.btnBuildIndex.disabled = true;
  setStatus(els.globalStatus, 'Building index…');

  try {
    const resp = await fetch('/vlm/index/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ use_ollama: els.chkOllama.checked }),
    });
    const reader = resp.body.getReader();
    const dec    = new TextDecoder();
    let last = {};

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const lines = dec.decode(value).split('\n').filter(Boolean);
      for (const line of lines) {
        try {
          last = JSON.parse(line);
          if (last.error) {
            setStatus(els.globalStatus, `Index error: ${last.error}`, 'err');
          } else if (!last.finished) {
            setStatus(els.globalStatus, `Indexing… ${last.done}/${last.total}`);
          } else {
            setStatus(els.globalStatus, `Index built: ${last.done} frames`, 'ok');
            updateIndexBadge({ exists: true, total_frames: last.done, built_at: last.built_at || '' });
            loadIndexStems();
          }
        } catch {}
      }
    }
  } catch (e) {
    setStatus(els.globalStatus, `Network error: ${e.message}`, 'err');
  }
  els.btnBuildIndex.disabled = false;
});

// ── Stem select ───────────────────────────────────────────────────────────────
els.stemSelect.addEventListener('change', () => {
  state.activeStem = els.stemSelect.value;
  state.activeFrame = null;
  state.machineCoords = {};
  state.vlmCoords     = {};
  state.vlmDebug      = {};
  state.humanCoords   = {};
  state.similar = [];
  els.btnRefine.disabled = true;
  els.btnSave.disabled   = true;
  buildFrameList(state.activeStem);
  populateRefStemSelect();   // remove active stem from ref options
  // Reset panels
  els.refCanvas.style.display = 'none';
  refCtx.clearRect(0, 0, els.refCanvas.width, els.refCanvas.height);
  els.refPlaceholder.style.display = '';
  els.activeCanvas.style.display = 'none';
  els.activePlaceholder.style.display = '';
  activeCtx.clearRect(0, 0, els.activeCanvas.width, els.activeCanvas.height);
  renderRefTabs();
  renderCoordList();
});

// ── Reference folder select ───────────────────────────────────────────────────
els.refStemSelect.addEventListener('change', () => {
  state.referenceStem = els.refStemSelect.value;
  // Reload reference matches only — preserve VLM/human layers
  if (state.activeFrame) reloadMachineCoords();
});

// ── Likelihood filter ─────────────────────────────────────────────────────────
els.lhFilter.addEventListener('input', () => {
  state.lhThreshold = parseFloat(els.lhFilter.value);
  els.lhVal.textContent = state.lhThreshold.toFixed(2);
  applyFilter();
  // Reload machine coords with new threshold — preserve VLM/human layers
  if (state.activeFrame && state.hasRawPredictions) {
    reloadMachineCoords();
  }
});

// ── VLM Refine ────────────────────────────────────────────────────────────────
els.btnRefine.addEventListener('click', async () => {
  const ref = state.similar[state.activeRefIdx];
  if (!ref) {
    setStatus(els.refineStatus, 'No reference frame available — build index first.', 'err');
    return;
  }
  els.btnRefine.disabled = true;
  setStatus(els.refineStatus, 'Calling VLM…');

  const { ok, data } = await apiFetch('/vlm/refine', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      active_video_stem:    state.activeStem,
      active_frame:         state.activeFrame,
      reference_video_stem: ref.video_stem,
      reference_frame:      ref.frame,
      reference_labels:     ref.labels || {},
      machine_coords:       state.machineCoords,
      bodyparts:            state.bodyparts,
    }),
  });

  els.btnRefine.disabled = false;
  if (!ok) { setStatus(els.refineStatus, data.error || 'VLM error', 'err'); return; }

  state.vlmCoords = data.vlm_coords || {};
  state.vlmDebug  = data.vlm_debug  || {};
  els.btnAcceptVlm.disabled = false;
  // Mark this frame as having a saved VLM result and refresh the frame list badge
  state.vlmFrames.add(state.activeFrame);
  renderFrameList();
  renderCoordList();   // re-render to show delta column
  drawActiveCanvas();  // redraw canvas so V mode shows VLM coords immediately

  // Build a meaningful status line from the debug
  const dbg = state.vlmDebug;
  const refined   = Object.values(dbg).filter(d => d.reason === 'ok' && (d.dx !== 0 || d.dy !== 0)).length;
  const correct   = Object.values(dbg).filter(d => d.reason === 'ok' && d.dx === 0 && d.dy === 0).length;
  const noRef     = Object.values(dbg).filter(d => d.reason === 'no_ref_label').length;
  const vlmFail  = Object.values(dbg).filter(d => d.reason === 'ollama_failed').length;
  const parseFail= Object.values(dbg).filter(d => d.reason === 'parse_failed').length;
  const cropFail = Object.values(dbg).filter(d => d.reason === 'crop_failed').length;
  const failed   = vlmFail + parseFail + cropFail;
  const parts = [];
  if (refined)   parts.push(`${refined} adjusted`);
  if (correct)   parts.push(`${correct} confirmed correct`);
  if (noRef)     parts.push(`${noRef} skipped (no ref label)`);
  if (vlmFail)   parts.push(`${vlmFail} VLM call failed (hover for details)`);
  if (parseFail) parts.push(`${parseFail} parse error`);
  if (cropFail)  parts.push(`${cropFail} crop error`);
  const summary = parts.length ? parts.join(', ') : 'nothing to refine';
  const type = failed > 0 && refined === 0 && correct === 0 ? 'err' : 'ok';
  setStatus(els.refineStatus, `VLM done: ${summary}. Toggle V to preview.`, type);
});

// Accept VLM coords as human baseline
els.btnAcceptVlm.addEventListener('click', () => {
  state.humanCoords = JSON.parse(JSON.stringify(state.vlmCoords));
  // Switch to H mode
  state.mode = 'H';
  els.toggleBtns.forEach(b => b.classList.toggle('active', b.dataset.mode === 'H'));
  drawActiveCanvas();
  renderCoordList();
  setStatus(els.refineStatus, 'VLM coords accepted as H — review and save.', 'ok');
});

// ── Save [H] labels ───────────────────────────────────────────────────────────
els.btnSave.addEventListener('click', async () => {
  if (!state.activeStem || !state.activeFrame) return;
  els.btnSave.disabled = true;

  // Merge human coords back into a full labels payload
  // We need to load all frames' labels, update this frame, and POST
  const { ok: getOk, data: getLabelData } = await apiFetch(
    `/dlc/project/labels/${encodeURIComponent(state.activeStem)}`
  );
  if (!getOk) {
    setStatus(els.refineStatus, 'Could not load labels for save', 'err');
    els.btnSave.disabled = false;
    return;
  }

  const labels = getLabelData.labels || {};
  labels[state.activeFrame] = state.humanCoords;

  const { ok, data } = await apiFetch(
    `/dlc/project/labels/${encodeURIComponent(state.activeStem)}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ labels }),
    }
  );
  els.btnSave.disabled = false;
  if (ok) {
    state.machineCoords = JSON.parse(JSON.stringify(state.humanCoords));
    setStatus(els.refineStatus, 'Saved to CSV.', 'ok');
  } else {
    setStatus(els.refineStatus, data.error || 'Save failed', 'err');
  }
});

// ── Commit to H5 ─────────────────────────────────────────────────────────────
els.btnCommitH5.addEventListener('click', async () => {
  if (!confirm('Convert all labeled-data CSV files to HDF5? This runs DeepLabCut\'s convertcsv2h5.')) return;
  els.btnCommitH5.disabled = true;
  setStatus(els.globalStatus, 'Dispatching H5 conversion…');

  const { ok, data } = await apiFetch('/dlc/project/labels/convert-to-h5', { method: 'POST' });
  els.btnCommitH5.disabled = false;
  if (ok) {
    setStatus(els.globalStatus, `H5 conversion queued (task ${data.task_id?.slice(0, 8)}…)`, 'ok');
  } else {
    setStatus(els.globalStatus, data.error || 'Error', 'err');
  }
});

// ── Init ──────────────────────────────────────────────────────────────────────
init();
