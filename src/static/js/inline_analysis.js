// src/static/js/inline_analysis.js
//
// Inline Analysis card controller.
//
// Owns:
//   - Open/close + ESC + hide-other-cards orchestration
//   - File picker (via makeFileBrowser) + hide-no-h5 toggle
//   - Snapshot picker + batch + frames-per-click + keep-warm inputs
//   - Project-type errors (multi-animal / TF) surface server-side as a
//     409 from /session/start and render in the existing lastRun status line
//   - Warm-indicator polling
//   - Range submit + status polling, calls player.reloadH5() on done
//   - Mounts makeAnalyzedFramePlayer({prefix: "ia", ...}) on first video load
//
// See docs/superpowers/specs/2026-05-20-inline-analysis-design.md.

import { makeFileBrowser } from './components/file_browser.js';
import { makeAnalyzedFramePlayer } from './components/analyzed_frame_player.js';

(function () {
  "use strict";

  const card        = document.getElementById("inline-analysis-card");
  const openBtn     = document.getElementById("btn-open-inline-analysis");
  const closeBtn    = document.getElementById("btn-close-inline-analysis");
  const videoPath   = document.getElementById("ia-video-path");
  const browserPane = document.getElementById("ia-file-browser-pane");
  const browseBtn   = document.getElementById("ia-browse-btn");
  const browseUp    = document.getElementById("ia-browse-up");
  const hideNoH5    = document.getElementById("ia-hide-no-h5");
  const snapshotSel = document.getElementById("ia-snapshot");
  const refreshSnapBtn = document.getElementById("ia-refresh-snapshots");
  const batchSize   = document.getElementById("ia-batch-size");
  const framesInput = document.getElementById("ia-frames-per-click");
  const keepWarm    = document.getElementById("ia-keep-warm-seconds");
  const warmIndicator = document.getElementById("ia-warm-indicator");
  const btnAnalyze  = document.getElementById("ia-btn-analyze-range");
  const lastRun     = document.getElementById("ia-last-run-status");
  const saveCsv     = document.getElementById("ia-save-csv");
  const seekEl      = document.getElementById("ia-seek");
  const btnNext     = document.getElementById("ia-btn-next");
  const btnPrev     = document.getElementById("ia-btn-prev");

  if (!card || !openBtn) return;

  let _player = null;
  let _snapKey = null;
  let _statusPoll = null;
  let _activeReqId = null;
  let _activeReqPoll = null;

  // ── Open / close ───────────────────────────────────────────────────────
  function openCard() {
    // Open at bottom like every other dashboard card — do NOT collapse
    // other open cards. See polish spec §1.1.
    card.classList.remove("hidden");
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    refreshSnapshots();
  }

  function closeCard() {
    card.classList.add("hidden");
    // Best-effort tell worker to wind down.
    if (_snapKey) {
      try {
        fetch("/dlc/project/inline-analysis/session/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ snap_key: _snapKey }),
        });
      } catch (e) { /* ignore */ }
    }
    stopStatusPolling();
    stopRangePolling();
    if (_player) { _player.destroy(); _player = null; }
  }

  openBtn.addEventListener("click", openCard);
  closeBtn.addEventListener("click", closeCard);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !card.classList.contains("hidden")) closeCard();
  });
  window.addEventListener("beforeunload", () => {
    if (_snapKey && navigator.sendBeacon) {
      navigator.sendBeacon(
        "/dlc/project/inline-analysis/session/stop",
        new Blob([JSON.stringify({ snap_key: _snapKey })], { type: "application/json" }),
      );
    }
  });

  // ── File picker (canonical component) ──────────────────────────────────
  const picker = makeFileBrowser({
    inputEl: videoPath,
    paneEl:  browserPane,
    dirOnly: false,
    fileFilter: (name) => {
      const dot = name.lastIndexOf(".");
      if (dot < 0) return false;
      const ext = name.slice(dot).toLowerCase();
      return [".mp4", ".avi", ".mov", ".mkv"].includes(ext);
    },
    onPick: (path) => {
      videoPath.value = path;
      // Collapse the file browser so the player has room — the path input,
      // Browse button, and hide-no-h5 toggle stay visible above the player.
      // Clicking Browse re-opens the pane via picker.openAt. See polish spec §1.2.
      if (browserPane) browserPane.classList.add("hidden");
      loadVideo(path);
    },
  });
  if (browseBtn) browseBtn.addEventListener("click", () => picker.openAt && picker.openAt("/user-data"));
  if (browseUp)  browseUp.addEventListener("click", () => picker.up && picker.up());
  // hide-no-h5: default unchecked per spec §1; re-render the picker when toggled.
  if (hideNoH5) {
    hideNoH5.checked = false;
    hideNoH5.addEventListener("change", () => picker.refresh && picker.refresh());
  }

  // ── Snapshot picker (mirrors analyze.js:_avLoadSnapshots) ──────────────
  async function refreshSnapshots() {
    if (!snapshotSel) return;
    try {
      // No shuffle filter — the route auto-derives shuffle from snapshot path.
      const r = await fetch("/dlc/project/snapshots");
      if (!r.ok) return;
      const data = await r.json();
      if (data.error) return;
      snapshotSel.innerHTML = "";

      // "Latest" default — use actual path so shuffle is auto-derived.
      const latestOpt = document.createElement("option");
      latestOpt.value = data.latest_rel_path || "-1";
      if (data.latest_label) {
        const iterStr = data.latest_iteration != null
          ? `  ·  iter ${data.latest_iteration.toLocaleString()}`
          : "";
        const shStr = data.latest_shuffle != null
          ? `  ·  sh${data.latest_shuffle}`
          : "";
        latestOpt.textContent = `Latest — ${data.latest_label}${iterStr}${shStr}`;
      } else {
        latestOpt.textContent = "Latest (from config)";
      }
      snapshotSel.appendChild(latestOpt);

      // Individual snapshots (ascending by iteration).
      (data.snapshots || []).forEach(s => {
        const opt = document.createElement("option");
        opt.value = s.rel_path;
        const iterStr = s.iteration != null
          ? `  ·  iter ${s.iteration.toLocaleString()}`
          : "";
        const shStr = s.shuffle != null ? `  ·  sh${s.shuffle}` : "";
        opt.textContent = `${s.label}${iterStr}${shStr}`;
        snapshotSel.appendChild(opt);
      });
    } catch (e) {
      console.error("inline_analysis refreshSnapshots:", e);
    }
  }
  if (refreshSnapBtn) refreshSnapBtn.addEventListener("click", refreshSnapshots);

  // Reload snapshots when shuffle changes (indices are per-shuffle).
  const shuffleEl = document.getElementById("ia-shuffle");
  if (shuffleEl) shuffleEl.addEventListener("change", refreshSnapshots);

  // ── Session start + status polling ─────────────────────────────────────
  async function ensureSession() {
    const snapshot = snapshotSel && snapshotSel.value;
    if (!snapshot) {
      lastRun.textContent = "Pick a snapshot first.";
      return null;
    }
    const r = await fetch("/dlc/project/inline-analysis/session/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        snapshot_path:    snapshot,
        shuffle:          parseInt(document.getElementById("ia-shuffle")?.value, 10) || 1,
        trainingsetindex: parseInt(document.getElementById("ia-trainingsetindex")?.value, 10) || 0,
        ttl_seconds:      parseInt(keepWarm.value, 10) || 300,
        batch_size:       parseInt(batchSize.value, 10) || 8,
      }),
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      lastRun.textContent =
        data.error || `Could not start session (HTTP ${r.status})`;
      return null;
    }
    const data = await r.json();
    _snapKey = data.snap_key;
    startStatusPolling();
    return _snapKey;
  }

  function startStatusPolling() {
    stopStatusPolling();
    _statusPoll = setInterval(async () => {
      if (!_snapKey) return;
      try {
        const r = await fetch(`/dlc/project/inline-analysis/session/status?snap_key=${_snapKey}`);
        if (!r.ok) return;
        const data = await r.json();
        const status = data.status || "absent";
        const mm = Math.floor((data.idle_remaining_s || 0) / 60);
        const ss = String((data.idle_remaining_s || 0) % 60).padStart(2, "0");
        if (status === "ready")        warmIndicator.textContent = `● warm · ${mm}:${ss}`;
        else if (status === "warming") warmIndicator.textContent = `… warming`;
        else                            warmIndicator.textContent = `○ ${status}`;
      } catch (e) { /* keep polling */ }
    }, 2000);
  }
  function stopStatusPolling() {
    if (_statusPoll) { clearInterval(_statusPoll); _statusPoll = null; }
  }

  // ── Player mount + frame counter → button label sync ───────────────────
  async function loadVideo(path) {
    if (!_player) {
      _player = makeAnalyzedFramePlayer({
        prefix: "ia",
        // /annotate/video-frame/<n>?path=... is the canonical browse-mode
        // frame endpoint (used by frame_labeler / annotator). Per-session
        // VideoCapture cache keeps decoding fast across slider drags.
        frameUrlFn: (n) => `/annotate/video-frame/${n}?path=${encodeURIComponent(path)}`,
        poseUrlFn:  (layer, n) =>
          `/dlc/viewer/h5-pose-window?h5=${encodeURIComponent(layer.path)}&start=${n}&n=30`,
        onCsvSaved: () => { /* no-op */ },
      });
    }
    try {
      const r = await fetch(`/dlc/project/inline-analysis/video-info?path=${encodeURIComponent(path)}`);
      const info = r.ok ? await r.json() : { fps: 30, nframes: 0 };
      _player.loadVideo(path, info.fps || 30, info.nframes || 0);
      syncAnalyzeButtonLabel();
      // Notify the curation block (mirrors viewer.js's MutationObserver +
      // _vaCsvLoad flow) so it can refresh _iaFrameCount/_iaFps and load
      // the companion CSV.
      if (typeof window.__iaCurationOnVideo === "function") {
        window.__iaCurationOnVideo(path, info.fps || 30, info.nframes || 0);
      }
    } catch (e) { /* silent */ }
  }

  function syncAnalyzeButtonLabel() {
    const n = parseInt(framesInput.value, 10) || 0;
    const k = _player ? _player.getCurrentFrame() : 0;
    btnAnalyze.textContent = `▶ Analyze ${n} frames from frame ${k}`;
  }
  if (framesInput) framesInput.addEventListener("input", syncAnalyzeButtonLabel);
  if (seekEl)      seekEl.addEventListener("input", syncAnalyzeButtonLabel);
  if (btnNext)     btnNext.addEventListener("click", syncAnalyzeButtonLabel);
  if (btnPrev)     btnPrev.addEventListener("click", syncAnalyzeButtonLabel);

  // ── Submit a range ─────────────────────────────────────────────────────
  btnAnalyze.addEventListener("click", async () => {
    if (!videoPath.value.trim()) {
      lastRun.textContent = "Pick a video first.";
      return;
    }
    if (!_player) {
      lastRun.textContent = "Loading player…";
      await loadVideo(videoPath.value.trim());
    }
    const sk = await ensureSession();
    if (!sk) return;
    const startFrame = _player ? _player.getCurrentFrame() : 0;
    const nFrames    = parseInt(framesInput.value, 10) || 500;
    const r = await fetch("/dlc/project/inline-analysis/range", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        snap_key:         sk,
        video_path:       videoPath.value.trim(),
        start_frame:      startFrame,
        n_frames:         nFrames,
        batch_size:       parseInt(batchSize.value, 10) || 8,
        save_as_csv:      !!(saveCsv && saveCsv.checked),
        snapshot_path:    snapshotSel && snapshotSel.value || "",
        shuffle:          parseInt(document.getElementById("ia-shuffle")?.value, 10) || 1,
        trainingsetindex: parseInt(document.getElementById("ia-trainingsetindex")?.value, 10) || 0,
      }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      lastRun.textContent = `Error: ${data.error || r.status}`;
      return;
    }
    _activeReqId = data.req_id;
    lastRun.textContent = `Running (${nFrames} frames from ${startFrame})…`;
    startRangePolling();
  });

  function startRangePolling() {
    stopRangePolling();
    _activeReqPoll = setInterval(async () => {
      if (!_activeReqId) { stopRangePolling(); return; }
      try {
        const r = await fetch(`/dlc/project/inline-analysis/range/status?req_id=${_activeReqId}`);
        if (!r.ok) return;
        const d = await r.json();
        if (d.status === "done") {
          lastRun.textContent = `Last run: ${d.n_analyzed} analyzed, ${d.n_skipped} skipped`;
          // Auto-register the canonical h5 as the only visible layer
          // (polish spec §1.4): build path from video_path + scorer, then
          // let the factory drop its per-layer pose cache and re-fetch.
          if (_player && d.scorer && videoPath.value) {
            const vp = videoPath.value.trim();
            const dot = vp.lastIndexOf(".");
            const stem = dot > 0 ? vp.slice(0, dot) : vp;
            const h5Path = stem + d.scorer + ".h5";
            try {
              if (_player.setPrimaryLayer) {
                _player.setPrimaryLayer({ path: h5Path, label: d.scorer });
              }
            } catch (e) { /* factory may not be wired in tests */ }
            _player.reloadH5();
          } else if (_player) {
            _player.reloadH5();
          }
          _activeReqId = null;
          stopRangePolling();
        } else if (d.status === "error") {
          lastRun.textContent = `Error: ${d.error || "unknown"}`;
          _activeReqId = null;
          stopRangePolling();
        }
      } catch (e) { /* keep polling */ }
    }, 500);
  }
  function stopRangePolling() {
    if (_activeReqPoll) { clearInterval(_activeReqPoll); _activeReqPoll = null; }
  }

  // ── Dataset Curation (copied from viewer.js per parent spec §4) ──────
  // DUPLICATION NOTICE: this block is a near-verbatim copy of the
  // curation IIFE in viewer.js (lines 1106-1110 + 1875-2348) with the
  // mechanical rename `va-` → `ia-` per polish spec §1.5. When you fix
  // a bug here, mirror it into viewer.js and vice versa. The migration
  // plan is the same as the player migration — see
  // docs/superpowers/specs/2026-05-20-inline-analysis-design.md §4 and
  // "Known tech debt".
  (() => {
    // Module-private mirrors of viewer.js's _vaFrameCount/_vaFps so the
    // copied helpers can read them without reaching into the factory.
    let _iaFrameCount = 0;
    let _iaFps        = 30;

    // ── Master toggle reveal (mirror viewer.js lines 1106-1110) ────────
    const iaCurationToggle   = document.getElementById("ia-curation-toggle");
    const iaCurationControls = document.getElementById("ia-curation-controls");
    iaCurationToggle?.addEventListener("change", () => {
      iaCurationControls?.classList.toggle("hidden", !iaCurationToggle.checked);
    });

    const iaCurationStatus  = document.getElementById("ia-curation-status");
    const iaExtractFrameBtn = document.getElementById("ia-extract-frame-btn");
    const iaAddToDatasetBtn = document.getElementById("ia-add-to-dataset-btn");
    const iaBatchAddBtn     = document.getElementById("ia-batch-add-btn");
    const iaBatchCount      = document.getElementById("ia-batch-count");
    const iaBatchStep       = document.getElementById("ia-batch-step");
    const iaCsvNone         = document.getElementById("ia-csv-none");
    const iaCsvLoaded       = document.getElementById("ia-csv-loaded");
    const iaCsvPathDisplay  = document.getElementById("ia-csv-path-display");
    const iaCreateCsvBtn    = document.getElementById("ia-create-csv-btn");
    const iaCsvCreateStatus = document.getElementById("ia-csv-create-status");
    const iaCsvBars         = document.getElementById("ia-csv-bars");
    const iaStatusBarWrap   = document.getElementById("ia-status-bar-wrap");
    const iaNoteBarWrap     = document.getElementById("ia-note-bar-wrap");
    const iaStatusCanvas    = document.getElementById("ia-status-canvas");
    const iaNoteCanvas      = document.getElementById("ia-note-canvas");
    const iaStatusChips     = document.getElementById("ia-status-chips");
    const iaNoteChips       = document.getElementById("ia-note-chips");
    const iaAnnotPanel      = document.getElementById("ia-annot-panel");
    const iaAnnotFrameNum   = document.getElementById("ia-annot-frame-num");
    const iaNoteInput       = document.getElementById("ia-note-input");
    const iaStatusInput     = document.getElementById("ia-status-input");
    const iaSaveStatusBtn   = document.getElementById("ia-save-status-btn");
    const iaSaveNoteBtn     = document.getElementById("ia-save-note-btn");
    const iaAnnotSaveStatus = document.getElementById("ia-annot-save-status");
    const iaStatusPrevBtn   = document.getElementById("ia-status-prev-btn");
    const iaStatusNextBtn   = document.getElementById("ia-status-next-btn");
    const iaNoteStepPrevBtn = document.getElementById("ia-note-prev-btn");
    const iaNoteStepNextBtn = document.getElementById("ia-note-next-btn");
    const iaNewTagInput     = document.getElementById("ia-new-tag-input");
    const iaAddTagBtn       = document.getElementById("ia-add-tag-btn");

    // Companion CSV state
    let _iaCsvPath          = null;
    let _iaCsvRows          = [];
    let _iaUserTags         = [];
    let _iaUserStatuses     = [];
    let _iaActiveNoteChips   = new Set();
    let _iaActiveStatusChips = new Set();
    let _iaNoteColorMap      = {};
    let _iaStatusColorMap    = {};

    const _IA_STATUS_COLORS = ["#34d399","#f97316","#e879f9","#facc15","#f87171","#22d3ee","#a78bfa","#fb923c"];
    const _IA_NOTE_COLORS   = ["#60a5fa","#f472b6","#4ade80","#38bdf8","#e879f9","#a78bfa","#facc15","#fb7185"];

    // ── Status helpers ──────────────────────────────────────────
    let _curationMsgTimer = null;
    function _curStatus(msg, isErr) {
      if (!iaCurationStatus) return;
      iaCurationStatus.textContent = msg;
      iaCurationStatus.className   = "fe-extract-status" + (isErr ? " err" : "");
      if (_curationMsgTimer) clearTimeout(_curationMsgTimer);
      if (msg && !isErr) {
        _curationMsgTimer = setTimeout(() => {
          iaCurationStatus.textContent = "";
        }, 4000);
      }
    }

    function _videoRequestBody(frameNum) {
      const n = (frameNum !== undefined)
        ? frameNum
        : (_player ? _player.getCurrentFrame() : 0);
      const body = { frame_number: n };
      const vp = videoPath.value.trim();
      if (vp) body.video_path = vp;
      return body;
    }

    // ── Extract Frame ────────────────────────────────────────────
    if (iaExtractFrameBtn) {
      iaExtractFrameBtn.addEventListener("click", async () => {
        if (!videoPath.value.trim()) {
          _curStatus("No video loaded — open a video first.", true); return;
        }
        iaExtractFrameBtn.disabled = true;
        _curStatus("Extracting…");
        try {
          const res  = await fetch("/dlc/curator/extract-frame", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(_videoRequestBody()),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
          _curStatus(
            data.duplicate
              ? `Already extracted: ${data.saved}`
              : `Saved ${data.saved} (${data.folder}, #${data.frame_count})`
          );
        } catch (err) {
          _curStatus(`Extract failed: ${err.message}`, true);
        } finally {
          iaExtractFrameBtn.disabled = false;
        }
      });
    }

    // ── Add to Dataset ────────────────────────────────────────────
    if (iaAddToDatasetBtn) {
      iaAddToDatasetBtn.addEventListener("click", async () => {
        if (!videoPath.value.trim()) {
          _curStatus("No video loaded — open a video first.", true); return;
        }
        iaAddToDatasetBtn.disabled = true;
        _curStatus("Adding to dataset…");
        try {
          const res  = await fetch("/dlc/curator/add-to-dataset", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(_videoRequestBody()),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
          const h5note = data.h5_updated ? " + H5" : "";
          _curStatus(
            data.duplicate
              ? `Already in dataset: ${data.saved}`
              : `Added ${data.saved} to CSV${h5note} (${data.frame_count} frames)`
          );
        } catch (err) {
          _curStatus(`Failed: ${err.message}`, true);
        } finally {
          iaAddToDatasetBtn.disabled = false;
        }
      });
    }

    // ── Batch Add ─────────────────────────────────────────────────
    if (iaBatchAddBtn) {
      iaBatchAddBtn.addEventListener("click", async () => {
        if (!videoPath.value.trim()) {
          _curStatus("No video loaded — open a video first.", true); return;
        }
        const count = Math.max(1, parseInt(iaBatchCount?.value) || 10);
        const step  = Math.max(1, parseInt(iaBatchStep?.value)  || 30);
        iaBatchAddBtn.disabled = true;
        let added = 0, dupes = 0, errors = 0;
        const start = _player ? _player.getCurrentFrame() : 0;
        let lastFrame = start;
        for (let i = 0; i < count; i++) {
          const frameNum = start + i * step;
          if (frameNum >= _iaFrameCount) break;
          lastFrame = frameNum;
          _curStatus(`Batch adding… ${i + 1}/${count} (frame ${frameNum})`);
          if (_player) _player.setCurrentFrame(frameNum);
          try {
            const res  = await fetch("/dlc/curator/add-to-dataset", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(_videoRequestBody(frameNum)),
            });
            const data = await res.json();
            if (!res.ok) { errors++; continue; }
            if (data.duplicate) dupes++; else added++;
          } catch (_) { errors++; }
        }
        if (_player && lastFrame !== _player.getCurrentFrame()) {
          _player.setCurrentFrame(lastFrame);
        }
        iaBatchAddBtn.disabled = false;
        const parts = [];
        if (added) parts.push(`${added} added`);
        if (dupes) parts.push(`${dupes} duplicate${dupes !== 1 ? "s" : ""}`);
        if (errors) parts.push(`${errors} error${errors !== 1 ? "s" : ""}`);
        _curStatus(`Batch done: ${parts.join(", ") || "nothing to add"}.`, errors > 0 && added === 0);
      });
    }

    // ── Timeline bars ────────────────────────────────────────────
    function _iaDrawCanvas(canvas, rows, field, activeSet, colorMap) {
      if (!canvas) return;
      const total = Math.max(_iaFrameCount, 1);
      const W = Math.round(canvas.getBoundingClientRect().width) || canvas.clientWidth || 600;
      canvas.width = W;
      const H    = canvas.height || 12;
      const ctx  = canvas.getContext("2d");
      const minW = Math.max(1, Math.round(W / total));
      ctx.clearRect(0, 0, W, H);
      if (!activeSet || activeSet.size === 0) return;
      rows.forEach(row => {
        const val = row[field];
        if (!val || (field === "frame_line_status" && val === "0")) return;
        if (!activeSet.has(val)) return;
        ctx.fillStyle = colorMap[val] || "#888";
        const x = Math.round((Number(row.frame_number) / total) * W);
        ctx.fillRect(x, 0, minW, H);
      });
    }
    function _iaRedrawNoteCanvas()   { _iaDrawCanvas(iaNoteCanvas,   _iaCsvRows, "note",              _iaActiveNoteChips,   _iaNoteColorMap);   }
    function _iaRedrawStatusCanvas() { _iaDrawCanvas(iaStatusCanvas, _iaCsvRows, "frame_line_status", _iaActiveStatusChips, _iaStatusColorMap); }

    function _iaBuildCsvBars() {
      if (!iaCsvBars) return;
      const hasNote   = _iaCsvRows.some(r => r.note);
      const hasStatus = _iaCsvRows.some(r => r.frame_line_status && r.frame_line_status !== "0");
      iaCsvBars.classList.toggle("hidden", !hasNote && !hasStatus);
      iaNoteBarWrap?.classList.toggle("hidden", !hasNote);
      iaStatusBarWrap?.classList.toggle("hidden", !hasStatus);
      _iaRedrawNoteCanvas();
      _iaRedrawStatusCanvas();
    }

    [iaNoteCanvas, iaStatusCanvas].forEach(canvas => {
      if (!canvas) return;
      canvas.addEventListener("click", e => {
        const rect = canvas.getBoundingClientRect();
        const fn = Math.round((e.clientX - rect.left) / rect.width * Math.max(_iaFrameCount - 1, 0));
        if (_player) _player.setCurrentFrame(fn);
      });
    });

    function _iaNavAnnot(field, activeSet, dir) {
      if (!activeSet.size) return;
      const cur = _player ? _player.getCurrentFrame() : 0;
      const frames = _iaCsvRows
        .filter(r => { const v = r[field]; return v && (field !== "frame_line_status" || v !== "0") && activeSet.has(v); })
        .map(r => r.frame_number)
        .sort((a, b) => a - b);
      if (!frames.length) return;
      if (dir < 0) {
        const prev = [...frames].reverse().find(f => f < cur);
        if (prev != null && _player) _player.setCurrentFrame(prev);
      } else {
        const next = frames.find(f => f > cur);
        if (next != null && _player) _player.setCurrentFrame(next);
      }
    }
    if (iaStatusPrevBtn)   iaStatusPrevBtn.addEventListener("click",   () => _iaNavAnnot("frame_line_status", _iaActiveStatusChips, -1));
    if (iaStatusNextBtn)   iaStatusNextBtn.addEventListener("click",   () => _iaNavAnnot("frame_line_status", _iaActiveStatusChips,  1));
    if (iaNoteStepPrevBtn) iaNoteStepPrevBtn.addEventListener("click", () => _iaNavAnnot("note",              _iaActiveNoteChips,   -1));
    if (iaNoteStepNextBtn) iaNoteStepNextBtn.addEventListener("click", () => _iaNavAnnot("note",              _iaActiveNoteChips,    1));

    // ── Companion CSV helpers ────────────────────────────────────
    function _iaCsvSyncPanel() {
      if (!_iaCsvPath) return;
      const cur = _player ? _player.getCurrentFrame() : 0;
      if (iaAnnotFrameNum) iaAnnotFrameNum.textContent = cur;
      const row = _iaCsvRows.find(r => r.frame_number === cur);
      if (iaNoteInput)   iaNoteInput.value   = row ? (row.note || "") : "";
      if (iaStatusInput) iaStatusInput.value = row ? (row.frame_line_status ?? "0") : "0";
    }

    function _iaCsvApplyRows(rows, csvPath) {
      _iaCsvPath  = csvPath;
      _iaCsvRows  = rows;
      const noteVals   = [...new Set(rows.map(r => r.note).filter(v => v))];
      const statusVals = [...new Set(rows.map(r => r.frame_line_status).filter(v => v && v !== "0"))];
      _iaUserTags     = [...new Set([..._iaUserTags,     ...noteVals])];
      _iaUserStatuses = [...new Set([..._iaUserStatuses, ...statusVals])];

      if (iaCsvNone)        iaCsvNone.classList.add("hidden");
      if (iaCsvLoaded)      iaCsvLoaded.classList.remove("hidden");
      if (iaCsvPathDisplay) { iaCsvPathDisplay.textContent = csvPath; iaCsvPathDisplay.title = csvPath; }
      if (iaAnnotPanel)     iaAnnotPanel.classList.remove("hidden");

      _iaBuildCsvBars();
      _iaCsvRenderStatusChips();
      _iaCsvRenderTags();
      _iaCsvSyncPanel();
    }

    function _iaCsvRenderStatusChips() {
      if (!iaStatusChips) return;
      iaStatusChips.innerHTML = "";
      _iaStatusColorMap = {};
      _iaUserStatuses.forEach((val, i) => {
        const color = _IA_STATUS_COLORS[i % _IA_STATUS_COLORS.length];
        _iaStatusColorMap[val] = color;
        const chip = document.createElement("span");
        chip.className = "fe-tag-chip" + (_iaActiveStatusChips.has(val) ? " active" : "");
        chip.textContent = val;
        chip.style.setProperty("--chip-color", color);
        chip.title = `Click to show/hide "${val}" on timeline`;
        chip.addEventListener("click", () => {
          if (_iaActiveStatusChips.has(val)) _iaActiveStatusChips.delete(val);
          else _iaActiveStatusChips.add(val);
          _iaCsvRenderStatusChips();
          _iaRedrawStatusCanvas();
        });
        iaStatusChips.appendChild(chip);
      });
      const hasActive = _iaActiveStatusChips.size > 0;
      if (iaStatusPrevBtn) iaStatusPrevBtn.disabled = !hasActive;
      if (iaStatusNextBtn) iaStatusNextBtn.disabled = !hasActive;
    }

    function _iaCsvRenderTags() {
      if (!iaNoteChips) return;
      iaNoteChips.innerHTML = "";
      _iaNoteColorMap = {};
      _iaUserTags.forEach((tag, i) => {
        const color = _IA_NOTE_COLORS[i % _IA_NOTE_COLORS.length];
        _iaNoteColorMap[tag] = color;
        const chip = document.createElement("span");
        chip.className = "fe-tag-chip" + (_iaActiveNoteChips.has(tag) ? " active" : "");
        chip.textContent = tag;
        chip.style.setProperty("--chip-color", color);
        chip.title = `Click to show/hide "${tag}" on timeline`;
        chip.addEventListener("click", () => {
          if (_iaActiveNoteChips.has(tag)) _iaActiveNoteChips.delete(tag);
          else _iaActiveNoteChips.add(tag);
          _iaCsvRenderTags();
          _iaRedrawNoteCanvas();
        });
        iaNoteChips.appendChild(chip);
      });
      const hasActive = _iaActiveNoteChips.size > 0;
      if (iaNoteStepPrevBtn) iaNoteStepPrevBtn.disabled = !hasActive;
      if (iaNoteStepNextBtn) iaNoteStepNextBtn.disabled = !hasActive;
    }

    async function _iaCsvSaveStatus() {
      if (!_iaCsvPath) return;
      const cur = _player ? _player.getCurrentFrame() : 0;
      const existingRow = _iaCsvRows.find(r => r.frame_number === cur);
      const note   = iaNoteInput ? iaNoteInput.value.trim() : (existingRow?.note || "");
      const status = iaStatusInput ? (iaStatusInput.value || "0") : "0";
      await _iaCsvDoSave(note, status);
    }
    async function _iaCsvSaveNote() {
      if (!_iaCsvPath) return;
      const cur = _player ? _player.getCurrentFrame() : 0;
      const existingRow = _iaCsvRows.find(r => r.frame_number === cur);
      const note   = iaNoteInput ? iaNoteInput.value.trim() : "";
      const status = iaStatusInput ? (iaStatusInput.value || "0") : (existingRow?.frame_line_status || "0");
      await _iaCsvDoSave(note, status);
    }
    async function _iaCsvDoSave(note, status) {
      const cur = _player ? _player.getCurrentFrame() : 0;
      if (iaAnnotSaveStatus) { iaAnnotSaveStatus.textContent = "Saving…"; iaAnnotSaveStatus.className = "fe-extract-status"; }
      try {
        const res  = await fetch("/annotate/save-row", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            csv_path:          _iaCsvPath,
            frame_number:      cur,
            note,
            frame_line_status: status,
            fps:               _iaFps,
          }),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        const isInteresting = note || (status && status !== "0");
        const idx = _iaCsvRows.findIndex(r => r.frame_number === cur);
        if (isInteresting) {
          const savedRow = data.row || { frame_number: cur, timestamp: (cur / _iaFps).toFixed(3), frame_line_status: status, note };
          if (idx >= 0) _iaCsvRows[idx] = savedRow;
          else { _iaCsvRows.push(savedRow); _iaCsvRows.sort((a, b) => a.frame_number - b.frame_number); }
          if (note && !_iaUserTags.includes(note)) { _iaUserTags.push(note); _iaCsvRenderTags(); }
          if (status && status !== "0" && !_iaUserStatuses.includes(status)) { _iaUserStatuses.push(status); _iaCsvRenderStatusChips(); }
        } else {
          if (idx >= 0) _iaCsvRows.splice(idx, 1);
        }
        _iaBuildCsvBars();
        if (iaAnnotSaveStatus) {
          iaAnnotSaveStatus.textContent = "Saved";
          iaAnnotSaveStatus.className   = "fe-extract-status ok";
          setTimeout(() => { if (iaAnnotSaveStatus?.textContent === "Saved") iaAnnotSaveStatus.textContent = ""; }, 2000);
        }
      } catch (err) {
        if (iaAnnotSaveStatus) { iaAnnotSaveStatus.textContent = `Error: ${err.message}`; iaAnnotSaveStatus.className = "fe-extract-status err"; }
      }
    }

    async function _iaCsvLoad(vp) {
      _iaCsvPath = null; _iaCsvRows = []; _iaUserTags = []; _iaUserStatuses = [];
      _iaActiveNoteChips = new Set(); _iaActiveStatusChips = new Set();
      _iaNoteColorMap = {}; _iaStatusColorMap = {};
      if (iaCsvNone)        iaCsvNone.classList.remove("hidden");
      if (iaCsvLoaded)      iaCsvLoaded.classList.add("hidden");
      if (iaCsvBars)        iaCsvBars.classList.add("hidden");
      if (iaAnnotPanel)     iaAnnotPanel.classList.add("hidden");
      if (iaCsvCreateStatus) iaCsvCreateStatus.textContent = "";
      if (!vp) return;
      try {
        const res  = await fetch(`/annotate/csv?path=${encodeURIComponent(vp)}`);
        const data = await res.json();
        if (data.csv_exists) _iaCsvApplyRows(data.rows, data.csv_path);
      } catch (_) {}
    }

    // Wire into the factory's frame-navigation hook so sync runs after
    // every loadFrame (factory exposes setCurationFrameHook today).
    function _wireCurationFrameHook() {
      if (_player && _player.setCurationFrameHook) {
        _player.setCurationFrameHook(() => _iaCsvSyncPanel());
      } else {
        // _player isn't built yet; retry once it appears.
        setTimeout(_wireCurationFrameHook, 200);
      }
    }
    _wireCurationFrameHook();

    // Create CSV
    if (iaCreateCsvBtn) {
      iaCreateCsvBtn.addEventListener("click", async () => {
        const vp = videoPath.value.trim();
        if (!vp) return;
        if (iaCsvCreateStatus) { iaCsvCreateStatus.textContent = `Creating CSV for ${_iaFrameCount} frames…`; iaCsvCreateStatus.className = "fe-extract-status"; }
        try {
          const res  = await fetch("/annotate/create-csv", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ video_path: vp, fps: _iaFps, frame_count: _iaFrameCount }),
          });
          const data = await res.json();
          if (data.error) throw new Error(data.error);
          if (iaCsvCreateStatus) iaCsvCreateStatus.textContent = "";
          _iaCsvApplyRows(data.rows, data.csv_path);
        } catch (err) {
          if (iaCsvCreateStatus) { iaCsvCreateStatus.textContent = `Error: ${err.message}`; iaCsvCreateStatus.className = "fe-extract-status err"; }
        }
      });
    }
    if (iaSaveStatusBtn) iaSaveStatusBtn.addEventListener("click", _iaCsvSaveStatus);
    if (iaSaveNoteBtn)   iaSaveNoteBtn.addEventListener("click",   _iaCsvSaveNote);
    if (iaAddTagBtn) {
      iaAddTagBtn.addEventListener("click", () => {
        const tag = iaNewTagInput ? iaNewTagInput.value.trim() : "";
        if (!tag) return;
        if (!_iaUserTags.includes(tag)) { _iaUserTags.push(tag); _iaCsvRenderTags(); }
        if (iaNewTagInput) iaNewTagInput.value = "";
      });
    }
    if (iaNewTagInput) {
      iaNewTagInput.addEventListener("keydown", e => {
        if (e.key === "Enter") { e.preventDefault(); iaAddTagBtn?.click(); }
      });
    }

    // Public hook the outer scope (loadVideo) calls so we can refresh
    // _iaFrameCount/_iaFps and trigger companion-CSV load.
    window.__iaCurationOnVideo = function (vp, fps, frameCount) {
      _iaFrameCount = frameCount || 0;
      _iaFps        = fps || 30;
      _iaCsvLoad(vp);
    };
  })(); // end Dataset Curation (inline-analysis copy)

})();
