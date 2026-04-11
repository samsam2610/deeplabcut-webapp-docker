/**
 * Posture-Centric VLM Refiner — UI Logic
 *
 * Panels:
 *   Left sidebar  — stem picker + frame list
 *   Centre canvas — active frame with machine labels
 *   Right panel   — best posture-matching reference frame + skeleton
 *
 * Data flow:
 *   /posture/frame-data  →  machine coords + posture-similar references
 *   /posture/refine      →  posture-aware VLM correction
 *   /posture/reference-image  →  serve reference PNG
 */

"use strict";

/* ── State ─────────────────────────────────────────────────────────────────── */
const state = {
  projectPath:    null,
  videoStem:      null,
  frame:          null,
  bodyparts:      [],
  machineCoords:  {},   // {bp: [x,y] | null}
  vlmCoords:      {},   // {bp: [x,y] | null}  (posture-VLM result)
  similar:        [],   // top-3 posture matches from index
  activeRefIdx:   0,    // which reference is shown
  showLayer:      "M",  // "M" | "V"
  indexAvailable: false,
  building:       false,
  refining:       false,
};

/* ── Canvas helpers ─────────────────────────────────────────────────────────── */
const BP_COLORS = {
  M: "#4ade80",   // green  — machine
  V: "#facc15",   // yellow — VLM
};
const REF_COLOR = "#818cf8";   // indigo — reference human labels

function drawLabels(canvas, imgSrc, labels, color, title) {
  const ctx = canvas.getContext("2d");
  const img = new Image();
  img.onload = () => {
    canvas.width  = img.naturalWidth;
    canvas.height = img.naturalHeight;
    ctx.drawImage(img, 0, 0);
    _drawDots(ctx, labels, color);
    if (title) {
      ctx.fillStyle = "rgba(0,0,0,.55)";
      ctx.fillRect(4, 4, ctx.measureText(title).width + 8, 18);
      ctx.fillStyle = "#fff";
      ctx.font = "11px JetBrains Mono, monospace";
      ctx.fillText(title, 8, 17);
    }
  };
  img.onerror = () => { ctx.clearRect(0, 0, canvas.width, canvas.height); };
  img.src = imgSrc;
}

function _drawDots(ctx, labels, color) {
  ctx.fillStyle = color;
  ctx.strokeStyle = "rgba(0,0,0,.6)";
  ctx.lineWidth = 1;
  for (const [, xy] of Object.entries(labels)) {
    if (!xy || xy[0] == null) continue;
    ctx.beginPath();
    ctx.arc(xy[0], xy[1], 5, 0, 2 * Math.PI);
    ctx.fill();
    ctx.stroke();
  }
}

function drawSkeleton(canvas, imgSrc, labels, color) {
  const ctx = canvas.getContext("2d");
  const img = new Image();
  img.onload = () => {
    canvas.width  = img.naturalWidth;
    canvas.height = img.naturalHeight;
    ctx.drawImage(img, 0, 0);
    _drawDots(ctx, labels, color);
  };
  img.onerror = () => { ctx.clearRect(0, 0, canvas.width, canvas.height); };
  img.src = imgSrc;
}

/* ── DOM refs ───────────────────────────────────────────────────────────────── */
const elStemSelect    = () => document.getElementById("stemSelect");
const elFrameList     = () => document.getElementById("frameList");
const elActiveCanvas  = () => document.getElementById("activeCanvas");
const elRefCanvas     = () => document.getElementById("refCanvas");
const elRefMeta       = () => document.getElementById("refMeta");
const elRefTabs       = () => document.getElementById("refTabs");
const elStatus        = () => document.getElementById("statusBar");
const elBuildBtn      = () => document.getElementById("buildIndexBtn");
const elRefineBtn     = () => document.getElementById("refineBtn");
const elLayerM        = () => document.getElementById("layerM");
const elLayerV        = () => document.getElementById("layerV");
const elIndexBadge    = () => document.getElementById("indexBadge");
const elPoseScore     = () => document.getElementById("poseScore");

function setStatus(msg, cls = "") {
  const el = elStatus();
  if (!el) return;
  el.textContent = msg;
  el.className = "status-bar " + cls;
}

/* ── Bootstrap ──────────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", async () => {
  await loadProject();
  await checkIndexStatus();
  populateStems();

  elLayerM()?.addEventListener("click", () => setLayer("M"));
  elLayerV()?.addEventListener("click", () => setLayer("V"));
  elBuildBtn()?.addEventListener("click", buildIndex);
  elRefineBtn()?.addEventListener("click", runRefine);
});

async function loadProject() {
  try {
    const r = await fetch("/config");
    const d = await r.json();
    state.projectPath = d.project_path || null;
  } catch (_) {}
}

async function checkIndexStatus() {
  try {
    const r = await fetch("/posture/index-status");
    const d = await r.json();
    state.indexAvailable = d.exists || false;
    const badge = elIndexBadge();
    if (badge) {
      badge.textContent = d.exists
        ? `Index: ${d.total_frames} frames`
        : "Index: not built";
      badge.className = "badge " + (d.exists ? "badge-ok" : "badge-warn");
    }
  } catch (_) {
    state.indexAvailable = false;
  }
}

async function populateStems() {
  try {
    const r = await fetch("/vlm/index-stems");
    const d = await r.json();
    const sel = elStemSelect();
    if (!sel) return;
    sel.innerHTML = '<option value="">— select stem —</option>';
    (d.stems || []).forEach(s => {
      const o = document.createElement("option");
      o.value = o.textContent = s;
      sel.appendChild(o);
    });
    sel.addEventListener("change", () => loadFrameList(sel.value));
  } catch (e) {
    setStatus("Could not load stems: " + e.message, "error");
  }
}

async function loadFrameList(stem) {
  state.videoStem = stem;
  state.frame     = null;
  const list = elFrameList();
  if (!list || !stem) return;
  list.innerHTML = '<li class="frame-item dim">Loading…</li>';
  try {
    const r = await fetch(`/dlc/project/labeled-frames?video_stem=${encodeURIComponent(stem)}`);
    const d = await r.json();
    const frames = d.frames || [];
    list.innerHTML = "";
    if (!frames.length) {
      list.innerHTML = '<li class="frame-item dim">No frames found.</li>';
      return;
    }
    frames.forEach(f => {
      const li = document.createElement("li");
      li.className = "frame-item";
      li.textContent = f;
      li.addEventListener("click", () => selectFrame(f, li));
      list.appendChild(li);
    });
  } catch (e) {
    list.innerHTML = `<li class="frame-item error">${e.message}</li>`;
  }
}

async function selectFrame(frame, liEl) {
  document.querySelectorAll(".frame-item.active").forEach(el => el.classList.remove("active"));
  liEl?.classList.add("active");

  state.frame     = frame;
  state.vlmCoords = {};
  setStatus("Loading frame data…");

  try {
    const url = `/posture/frame-data?video_stem=${encodeURIComponent(state.videoStem)}&frame=${encodeURIComponent(frame)}`;
    const r   = await fetch(url);
    const d   = await r.json();
    if (d.error) throw new Error(d.error);

    state.bodyparts     = d.bodyparts || [];
    state.machineCoords = d.current_labels || {};
    state.similar       = d.similar || [];
    state.indexAvailable = d.index_available || false;

    if (d.vlm_coords && Object.keys(d.vlm_coords).length) {
      state.vlmCoords = d.vlm_coords;
    }

    renderActiveCanvas();
    renderReferenceTabs();
    showReference(0);
    updateLayerButtons();

    const scoreEl = elPoseScore();
    if (scoreEl && state.similar.length) {
      scoreEl.textContent = `Best match: ${(state.similar[0].score * 100).toFixed(1)}%`;
    } else if (scoreEl) {
      scoreEl.textContent = state.indexAvailable ? "No posture matches" : "Build index first";
    }

    const refineBtn = elRefineBtn();
    if (refineBtn) refineBtn.disabled = !state.similar.length;

    setStatus(`Frame loaded — ${state.similar.length} posture reference(s)`, "ok");
  } catch (e) {
    setStatus("Error: " + e.message, "error");
  }
}

/* ── Canvas rendering ───────────────────────────────────────────────────────── */
function renderActiveCanvas() {
  const canvas = elActiveCanvas();
  if (!canvas || !state.videoStem || !state.frame) return;
  const imgSrc = `/dlc/project/frame-image?video_stem=${encodeURIComponent(state.videoStem)}&frame=${encodeURIComponent(state.frame)}`;
  const coords = state.showLayer === "V" && Object.keys(state.vlmCoords).length
    ? state.vlmCoords
    : state.machineCoords;
  const color = BP_COLORS[state.showLayer] || BP_COLORS.M;
  drawLabels(canvas, imgSrc, coords, color, state.showLayer === "V" ? "VLM" : "Machine");
}

function renderReferenceTabs() {
  const tabs = elRefTabs();
  if (!tabs) return;
  tabs.innerHTML = "";
  state.similar.forEach((ref, i) => {
    const btn = document.createElement("button");
    btn.className = "ref-tab" + (i === state.activeRefIdx ? " active" : "");
    btn.textContent = `Ref ${i + 1} (${(ref.score * 100).toFixed(0)}%)`;
    btn.addEventListener("click", () => showReference(i));
    tabs.appendChild(btn);
  });
}

function showReference(idx) {
  state.activeRefIdx = idx;
  const ref = state.similar[idx];
  if (!ref) {
    const canvas = elRefCanvas();
    if (canvas) {
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
    elRefMeta() && (elRefMeta().textContent = "No reference available.");
    return;
  }

  document.querySelectorAll(".ref-tab").forEach((btn, i) => {
    btn.classList.toggle("active", i === idx);
  });

  const imgSrc = `/posture/reference-image/${encodeURIComponent(ref.video_stem)}/${encodeURIComponent(ref.frame)}`;
  drawSkeleton(elRefCanvas(), imgSrc, ref.labels || {}, REF_COLOR);

  const metaEl = elRefMeta();
  if (metaEl) {
    metaEl.textContent = `${ref.video_stem} / ${ref.frame}  |  score ${(ref.score * 100).toFixed(1)}%`;
  }
}

/* ── Layer toggle ───────────────────────────────────────────────────────────── */
function setLayer(layer) {
  state.showLayer = layer;
  updateLayerButtons();
  renderActiveCanvas();
}

function updateLayerButtons() {
  const hasVlm = Object.keys(state.vlmCoords).length > 0;
  elLayerM()?.classList.toggle("active", state.showLayer === "M");
  elLayerV()?.classList.toggle("active", state.showLayer === "V");
  elLayerV()?.classList.toggle("has-data", hasVlm);
}

/* ── Build posture index ────────────────────────────────────────────────────── */
async function buildIndex() {
  if (state.building) return;
  state.building = true;
  elBuildBtn() && (elBuildBtn().disabled = true);
  setStatus("Building posture index…");

  try {
    const r = await fetch("/posture/index/build", { method: "POST" });
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const ev = JSON.parse(line);
          if (ev.error) throw new Error(ev.error);
          if (ev.finished) {
            state.indexAvailable = true;
            await checkIndexStatus();
            setStatus(`Index built — ${ev.done} frames indexed`, "ok");
          } else {
            setStatus(`Indexing… ${ev.done}/${ev.total}`);
          }
        } catch (e) {
          setStatus("Index build error: " + e.message, "error");
        }
      }
    }
  } catch (e) {
    setStatus("Index build failed: " + e.message, "error");
  } finally {
    state.building = false;
    elBuildBtn() && (elBuildBtn().disabled = false);
  }
}

/* ── VLM Refine ─────────────────────────────────────────────────────────────── */
async function runRefine() {
  if (state.refining || !state.similar.length) return;
  state.refining = true;
  elRefineBtn() && (elRefineBtn().disabled = true);
  setStatus("Calling VLM (posture-aware)…");

  const ref = state.similar[state.activeRefIdx] || state.similar[0];

  const body = {
    active_video_stem:    state.videoStem,
    active_frame:         state.frame,
    reference_video_stem: ref.video_stem,
    reference_frame:      ref.frame,
    reference_labels:     ref.labels || {},
    machine_coords:       state.machineCoords,
    bodyparts:            state.bodyparts,
  };

  try {
    const r = await fetch("/posture/refine", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(body),
    });
    const d = await r.json();
    if (d.error) throw new Error(d.error);

    state.vlmCoords = d.vlm_coords || {};
    setLayer("V");
    setStatus("VLM refinement complete — showing V layer", "ok");
  } catch (e) {
    setStatus("Refine failed: " + e.message, "error");
  } finally {
    state.refining = false;
    elRefineBtn() && (elRefineBtn().disabled = false);
  }
}
