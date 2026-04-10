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
  allFrames: [],       // current stem's frames: [{name, lh}] — lh = min likelihood or null
  filteredFrames: [],  // after likelihood filter
  lhThreshold: 0,

  bodyparts: [],
  scorer: '',
  indexAvailable: false,

  similar: [],         // top-3 similar [{video_stem, frame, score, labels}]
  activeRefIdx: 0,     // which similar frame is shown in panel 1

  mode: 'M',           // 'M' | 'V' | 'H'

  // Label layers for the active frame
  machineCoords: {},   // {bp: [x,y] | null}  — from CSV
  vlmCoords:     {},   // {bp: [x,y] | null}  — from VLM
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
  await loadStems();
}

function updateIndexBadge(data) {
  state.indexAvailable = data.exists || false;
  if (data.exists) {
    els.indexBadge.textContent = `Index: ${data.total_frames} frames`;
    els.indexBadge.style.color = 'var(--accent)';
    els.indexBadge.style.borderColor = 'var(--accent-dim)';
  } else {
    els.indexBadge.textContent = 'Index: not built';
    els.indexBadge.style.color = '';
    els.indexBadge.style.borderColor = '';
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

  // allFrames: frames without likelihood info initially; likelihood loaded per-frame lazily
  state.allFrames = stemData.frames.map(name => ({ name, lh: null }));
  applyFilter();
}

function applyFilter() {
  state.filteredFrames = state.allFrames.filter(f =>
    f.lh === null || f.lh >= state.lhThreshold
  );
  renderFrameList();
}

function renderFrameList() {
  const list = els.frameList;
  list.innerHTML = '';
  els.frameCount.textContent = `${state.filteredFrames.length} / ${state.allFrames.length} frames`;

  if (!state.filteredFrames.length) {
    list.innerHTML = '<div style="padding:.5rem;color:var(--text-dim);font-size:.76rem">No frames match the filter.</div>';
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
async function selectFrame(frameName) {
  state.activeFrame = frameName;
  state.vlmCoords   = {};
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
  const { ok, data } = await apiFetch(
    `/vlm/frame-data?video_stem=${encodeURIComponent(state.activeStem)}&frame=${encodeURIComponent(frameName)}`
  );
  if (!ok) { setStatus(els.globalStatus, data.error || 'Error loading frame', 'err'); return; }

  state.bodyparts = data.bodyparts || [];
  state.scorer    = data.scorer    || 'User';
  state.machineCoords = data.current_labels || {};
  state.similar   = data.similar  || [];
  state.activeRefIdx = 0;

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
    els.refScoreBadge.textContent = '';
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
  els.refScoreBadge.textContent = `sim=${ref.score?.toFixed(3) ?? ''}`;
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

// ── Reference tabs ─────────────────────────────────────────────────────────────
function renderRefTabs() {
  els.refTabs.innerHTML = '';
  if (!state.similar.length) {
    const msg = document.createElement('span');
    msg.style.cssText = 'font-size:.72rem;color:var(--text-dim);align-self:center';
    msg.textContent = state.indexAvailable ? 'No matches found' : 'Index not built';
    els.refTabs.appendChild(msg);
    return;
  }
  state.similar.forEach((ref, i) => {
    const btn = document.createElement('button');
    btn.className = 'vlm-ref-tab' + (i === state.activeRefIdx ? ' active' : '');
    btn.textContent = `#${i + 1} (${ref.score?.toFixed(2) ?? '?'})`;
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
function renderCoordList() {
  const coords = currentCoords();
  els.coordList.innerHTML = '';

  if (!state.bodyparts.length) {
    els.coordList.innerHTML = '<span style="color:var(--text-dim)">No bodyparts</span>';
    return;
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
    valEl.textContent = pt ? `${pt[0].toFixed(1)}, ${pt[1].toFixed(1)}` : 'null';

    row.appendChild(nameEl);
    row.appendChild(valEl);
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
            updateIndexBadge({ exists: true, total_frames: last.done });
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
  state.humanCoords   = {};
  state.similar = [];
  els.btnRefine.disabled = true;
  els.btnSave.disabled   = true;
  buildFrameList(state.activeStem);
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

// ── Likelihood filter ─────────────────────────────────────────────────────────
els.lhFilter.addEventListener('input', () => {
  state.lhThreshold = parseFloat(els.lhFilter.value);
  els.lhVal.textContent = state.lhThreshold.toFixed(2);
  applyFilter();
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
      bodyparts:            state.bodyparts,
    }),
  });

  els.btnRefine.disabled = false;
  if (!ok) { setStatus(els.refineStatus, data.error || 'VLM error', 'err'); return; }

  state.vlmCoords = data.vlm_coords || {};
  els.btnAcceptVlm.disabled = false;
  setStatus(els.refineStatus, 'VLM refinement done — toggle V to preview.', 'ok');
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
