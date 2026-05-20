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
  function hideAllOtherCards() {
    document.querySelectorAll("section.card").forEach((c) => {
      if (c !== card) c.classList.add("hidden");
    });
  }

  function openCard() {
    hideAllOtherCards();
    card.classList.remove("hidden");
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

  // ── Snapshot picker ────────────────────────────────────────────────────
  async function refreshSnapshots() {
    if (!snapshotSel) return;
    snapshotSel.innerHTML = "";
    try {
      // Reuse the same endpoint the analyze card uses to enumerate snapshots.
      const r = await fetch("/dlc/project/snapshots");
      if (!r.ok) return;
      const data = await r.json();
      (data.snapshots || []).forEach(s => {
        const opt = document.createElement("option");
        opt.value = s.path || s;
        opt.textContent = s.label || s.path || s;
        snapshotSel.appendChild(opt);
      });
    } catch (e) { /* silent */ }
  }
  if (refreshSnapBtn) refreshSnapBtn.addEventListener("click", refreshSnapshots);

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
        snapshot_path: snapshot,
        shuffle: 1,
        ttl_seconds: parseInt(keepWarm.value, 10) || 300,
        batch_size: parseInt(batchSize.value, 10) || 8,
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
        frameUrlFn: (n) => `/annotate/frame?path=${encodeURIComponent(path)}&frame=${n}`,
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
        snap_key:      sk,
        video_path:    videoPath.value.trim(),
        start_frame:   startFrame,
        n_frames:      nFrames,
        batch_size:    parseInt(batchSize.value, 10) || 8,
        save_as_csv:   !!(saveCsv && saveCsv.checked),
        snapshot_path: snapshotSel && snapshotSel.value || "",
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
          if (_player) _player.reloadH5();
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

})();
