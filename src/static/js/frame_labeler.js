"use strict";
import { _populateGpuSelect } from './training.js';

    const flCard         = document.getElementById("frame-labeler-card");
    const flOpenBtn      = document.getElementById("btn-open-frame-labeler");
    const flCloseBtn     = document.getElementById("btn-close-frame-labeler");
    const flStemSelect   = document.getElementById("fl-stem-select");
    const flRefreshBtn   = document.getElementById("fl-refresh-btn");
    const flStemStatus   = document.getElementById("fl-stem-status");
    const flPlayerSec    = document.getElementById("fl-player-section");
    const flBtnPrev      = document.getElementById("fl-btn-prev");
    const flBtnNext      = document.getElementById("fl-btn-next");
    const flFrameInfo    = document.getElementById("fl-frame-info");
    const flFrameName    = document.getElementById("fl-frame-name");
    const flCanvas       = document.getElementById("fl-canvas");
    const flCtx          = flCanvas.getContext("2d");
    const flCanvasLoading = document.getElementById("fl-canvas-loading");
    const flBodypartList = document.getElementById("fl-bodypart-list");
    const flBpHint       = document.getElementById("fl-bp-hint");
    const flBtnSave        = document.getElementById("fl-btn-save");
    const flBtnSaveH5      = document.getElementById("fl-btn-save-h5");
    const flSaveStatus     = document.getElementById("fl-save-status");
    const flLabelCount     = document.getElementById("fl-label-count");
    const flScorerFilename = document.getElementById("fl-scorer-filename");
    const flMarkerSizeInput = document.getElementById("fl-marker-size");
    const flMarkerSizeVal   = document.getElementById("fl-marker-size-val");
    const flShowNamesInput  = document.getElementById("fl-show-names");

    // ── TAPNet propagation elements ──────────────────────────────
    const flTapCheckbox      = document.getElementById("fl-tap-checkbox");
    const flTapOpts          = document.getElementById("fl-tap-opts");
    const flTapCkpt          = document.getElementById("fl-tap-ckpt");
    const flTapAnchor        = document.getElementById("fl-tap-anchor");
    const flTapCheckBtn      = document.getElementById("fl-tap-check-btn");
    const flTapCheckStatus   = document.getElementById("fl-tap-check-status");
    const flTapSeqInfo       = document.getElementById("fl-tap-seq-info");
    const flTapOverwrite     = document.getElementById("fl-tap-overwrite");
    const flTapRunBtn        = document.getElementById("fl-tap-run-btn");
    const flTapStopBtn       = document.getElementById("fl-tap-stop-btn");
    const flTapRerunBtn      = document.getElementById("fl-tap-rerun-btn");
    const flTapConfirmedCount= document.getElementById("fl-tap-confirmed-count");
    const flTapStatus        = document.getElementById("fl-tap-status");
    const flTapProgress      = document.getElementById("fl-tap-progress");
    const flTapTaskId        = document.getElementById("fl-tap-task-id");
    const flTapProgressBar   = document.getElementById("fl-tap-progress-bar");
    const flTapProgressStage = document.getElementById("fl-tap-progress-stage");
    const flTapProgressPct   = document.getElementById("fl-tap-progress-pct");
    const flTapLogOutput     = document.getElementById("fl-tap-log-output");
    // Per-frame confirm elements
    const flTapFrameBadge    = document.getElementById("fl-tap-frame-badge");
    const flTapConfirmBtn    = document.getElementById("fl-tap-confirm-btn");
    const flTapConfirmLabel  = document.getElementById("fl-tap-confirm-label");

    // ── Machine-labeling elements ────────────────────────────────
    const flMlCheckbox     = document.getElementById("fl-ml-checkbox");
    const flMlOpts         = document.getElementById("fl-ml-opts");
    const flMlSnapshot     = document.getElementById("fl-ml-snapshot");
    const flMlRefreshSnap  = document.getElementById("fl-ml-refresh-snap");
    const flMlShuffle      = document.getElementById("fl-ml-shuffle");
    const flMlRunBtn       = document.getElementById("fl-ml-run-btn");
    const flMlRunAllBtn    = document.getElementById("fl-ml-run-all-btn");
    const flMlLikelihood   = document.getElementById("fl-ml-likelihood");
    const flMlStopBtn      = document.getElementById("fl-ml-stop-btn");
    const flMlStatus       = document.getElementById("fl-ml-status");
    const flMlUpdateWrap   = document.getElementById("fl-ml-update-wrap");
    const flMlUpdateBtn    = document.getElementById("fl-ml-update-btn");
    const flMlUpdateStatus = document.getElementById("fl-ml-update-status");
    const flMlProgress     = document.getElementById("fl-ml-progress");
    const flMlTaskId       = document.getElementById("fl-ml-task-id");
    const flMlProgressBar  = document.getElementById("fl-ml-progress-bar");
    const flMlProgressStage= document.getElementById("fl-ml-progress-stage");
    const flMlProgressPct  = document.getElementById("fl-ml-progress-pct");
    const flMlLogOutput    = document.getElementById("fl-ml-log-output");

    // ── State ───────────────────────────────────────────────────
    let _flBodyparts   = [];
    let _flScorer      = "User";
    let _flStemData    = [];      // [{video_stem, frames[]}]
    let _flVideoStem   = null;
    let _flFrames      = [];      // array of filenames
    let _flFrameIdx    = 0;
    let _flLabels      = {};      // {frame_name: {bp: [x, y] | null}}
    let _flDirty       = false;  // unsaved changes since last save/load
    let _flSelectedBp  = null;
    let _flImg         = new Image();
    let _flImgLoaded   = false;
    let _flMarkerRadius   = 4;
    let _flShowNames      = true;
    let _flCursorInCanvas = false;
    let _flHoverBp        = null;   // bodypart marker the cursor is near
    let _flZoom           = 100;
    let _flHidden         = {};  // {frame_name: {bp: bool}} — visibility-toggled markers  // percent of container width (100 = fit to card)

    // Machine labeling state (persists across folder changes)
    let _flMlPollTimer   = null;
    let _flMlActiveTask  = null;
    let _flMlQueue       = [];   // stems queued for "Run All Folders"
    let _flMlQueueIdx    = -1;   // current index in queue (-1 = single-folder mode)
    let _flMlQueueTotal  = 0;

    // ── Machine labeling: toggle panel ──────────────────────────
    flMlCheckbox.addEventListener("change", () => {
      flMlOpts.classList.toggle("hidden", !flMlCheckbox.checked);
      if (flMlCheckbox.checked) {
        _flMlLoadSnapshots();
        _populateGpuSelect("fl-ml-gpu");
      }
    });

    // ── Machine labeling: load snapshots ────────────────────────
    async function _flMlLoadSnapshots() {
      try {
        // No shuffle filter — show all shuffles so models from any shuffle are visible.
        // The backend will auto-correct the shuffle when snapshot_path is provided.
        const res  = await fetch("/dlc/project/snapshots");
        const data = await res.json();
        if (data.error) return;
        flMlSnapshot.innerHTML = "";
        const latestOpt = document.createElement("option");
        // Use the actual latest snapshot path (not -1) so shuffle is auto-derived
        latestOpt.value = data.latest_rel_path || "-1";
        const latestSuffix = data.latest_label
          ? ` — ${data.latest_label}${data.latest_iteration != null ? "  ·  iter " + data.latest_iteration.toLocaleString() : ""}${data.latest_shuffle != null ? "  ·  sh" + data.latest_shuffle : ""}`
          : "";
        latestOpt.textContent = `Latest${latestSuffix}`;
        flMlSnapshot.appendChild(latestOpt);
        (data.snapshots || []).forEach(s => {
          const opt = document.createElement("option");
          opt.value = s.rel_path;
          const shuffleSuffix = s.shuffle != null ? `  ·  sh${s.shuffle}` : "";
          opt.textContent = `${s.label}${s.iteration != null ? "  ·  iter " + s.iteration.toLocaleString() : ""}${shuffleSuffix}`;
          flMlSnapshot.appendChild(opt);
        });
      } catch (err) {
        console.error("flMlLoadSnapshots:", err);
      }
    }

    flMlRefreshSnap.addEventListener("click", _flMlLoadSnapshots);
    flMlShuffle.addEventListener("change", _flMlLoadSnapshots);

    // ── Machine labeling: run / stop ────────────────────────────
    function _flMlSetRunning(running) {
      flMlRunBtn.classList.toggle("hidden",    running);
      flMlRunAllBtn.classList.toggle("hidden", running);
      flMlStopBtn.classList.toggle("hidden",  !running);
      flMlRunBtn.disabled    = running;
      flMlRunAllBtn.disabled = running;
      if (running) flMlUpdateWrap.classList.add("hidden");
    }

    function _flMlQueueLabel() {
      if (_flMlQueueIdx < 0 || _flMlQueueTotal <= 1) return "";
      return ` (${_flMlQueueIdx + 1}/${_flMlQueueTotal}: ${_flMlQueue[_flMlQueueIdx]})`;
    }

    function _flMlStartPolling(taskId) {
      flMlProgress.classList.remove("hidden", "state-success", "state-fail");
      flMlTaskId.textContent        = taskId.slice(0, 12) + "…";
      flMlProgressBar.style.width   = "0%";
      flMlProgressPct.textContent   = "0 %";
      flMlProgressStage.textContent = "Queued" + _flMlQueueLabel();
      flMlLogOutput.textContent     = "Waiting for output…";
      _flMlSetRunning(true);
      if (_flMlPollTimer) clearInterval(_flMlPollTimer);
      _flMlPollTimer = setInterval(() => _flMlPoll(taskId), 2000);
      _flMlPoll(taskId);
    }

    async function _flMlPoll(taskId) {
      try {
        const res  = await fetch(`/status/${taskId}`);
        const data = await res.json();
        const pct  = Math.min(data.progress || 0, 100);
        flMlProgressBar.style.width   = pct + "%";
        flMlProgressPct.textContent   = pct + " %";
        flMlProgressStage.textContent = (data.stage || data.state) + _flMlQueueLabel();
        if (data.log) {
          flMlLogOutput.textContent = data.log;
          flMlLogOutput.scrollTop   = flMlLogOutput.scrollHeight;
        }
        if (data.state === "SUCCESS") {
          clearInterval(_flMlPollTimer); _flMlPollTimer = null;
          if (data.result && data.result.log) flMlLogOutput.textContent = data.result.log;
          // Reload labels for the current stem so the user sees machine labels immediately
          if (_flVideoStem) {
            const found = _flStemData.find(s => s.video_stem === _flVideoStem);
            if (found) await _flSelectStem(found.video_stem, found.frames);
          }
          // Advance queue if running all folders
          if (_flMlQueueIdx >= 0 && _flMlQueueIdx < _flMlQueue.length - 1) {
            _flMlQueueIdx++;
            flMlStatus.textContent = `Folder ${_flMlQueueIdx + 1}/${_flMlQueueTotal} done — starting next…`;
            flMlStatus.className   = "fe-extract-status ok";
            await _flMlDispatch(_flMlQueue[_flMlQueueIdx]);
          } else {
            // Done (single folder or last in queue)
            _flMlQueue = []; _flMlQueueIdx = -1; _flMlQueueTotal = 0;
            flMlProgress.classList.add("state-success");
            flMlProgressStage.textContent = "✓ Machine labeling complete";
            flMlProgressBar.style.width   = "100%";
            flMlProgressPct.textContent   = "100 %";
            flMlStatus.textContent = "Labels loaded — review and correct as needed.";
            flMlStatus.className   = "fe-extract-status ok";
            _flMlSetRunning(false);
          }
        }
        if (data.state === "FAILURE" || data.state === "REVOKED") {
          clearInterval(_flMlPollTimer); _flMlPollTimer = null;
          _flMlQueue = []; _flMlQueueIdx = -1; _flMlQueueTotal = 0;
          const userStopped = data.state === "REVOKED" || (data.error || "").includes("__USER_STOPPED__");
          flMlProgress.classList.add("state-fail");
          flMlProgressStage.textContent = userStopped ? "✗ Stopped by user" : "✗ " + (data.error || "Failed").split("\n")[0];
          if (!userStopped) flMlLogOutput.textContent = data.error || "An unknown error occurred.";
          flMlStatus.textContent = userStopped ? "Machine labeling stopped." : "";
          flMlStatus.className   = "fe-extract-status";
          _flMlSetRunning(false);
        }
      } catch (err) {
        console.error("flMlPoll:", err);
      }
    }

    function _flMlBuildBody(videoStem) {
      const snapVal = flMlSnapshot.value;
      return {
        video_stem:           videoStem,
        shuffle:              parseInt(flMlShuffle.value) || 1,
        trainingsetindex:     parseInt(document.getElementById("fl-ml-tsidx").value) ?? 0,
        gputouse:             document.getElementById("fl-ml-gpu").value !== ""
                                ? parseInt(document.getElementById("fl-ml-gpu").value) : null,
        snapshot_path:        snapVal !== "-1" ? snapVal : null,
        likelihood_threshold: parseFloat(flMlLikelihood.value) || 0.9,
      };
    }

    async function _flMlDispatch(videoStem) {
      try {
        const res  = await fetch("/dlc/project/machine-label-frames", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify(_flMlBuildBody(videoStem)),
        });
        const data = await res.json();
        if (!res.ok) {
          flMlStatus.textContent = data.error || "Failed to start machine labeling.";
          flMlStatus.className   = "fe-extract-status err";
          _flMlQueue = []; _flMlQueueIdx = -1; _flMlQueueTotal = 0;
          _flMlSetRunning(false);
          return;
        }
        _flMlActiveTask = data.task_id;
        _flMlStartPolling(data.task_id);
      } catch (err) {
        flMlStatus.textContent = "Network error: " + err.message;
        flMlStatus.className   = "fe-extract-status err";
        _flMlQueue = []; _flMlQueueIdx = -1; _flMlQueueTotal = 0;
        _flMlSetRunning(false);
      }
    }

    flMlRunBtn.addEventListener("click", async () => {
      if (!_flVideoStem) {
        flMlStatus.textContent = "Select a labeled-data folder first.";
        flMlStatus.className   = "fe-extract-status err";
        return;
      }
      flMlStatus.textContent = "";
      flMlStatus.className   = "fe-extract-status";
      _flMlQueue = []; _flMlQueueIdx = -1; _flMlQueueTotal = 0;
      await _flMlDispatch(_flVideoStem);
    });

    flMlRunAllBtn.addEventListener("click", async () => {
      if (!_flStemData || _flStemData.length === 0) {
        flMlStatus.textContent = "No labeled-data folders loaded.";
        flMlStatus.className   = "fe-extract-status err";
        return;
      }
      flMlStatus.textContent = "";
      flMlStatus.className   = "fe-extract-status";
      _flMlQueue      = _flStemData.map(s => s.video_stem);
      _flMlQueueIdx   = 0;
      _flMlQueueTotal = _flMlQueue.length;
      flMlStatus.textContent = `Starting ${_flMlQueueTotal} folder(s)…`;
      flMlStatus.className   = "fe-extract-status ok";
      await _flMlDispatch(_flMlQueue[0]);
    });

    flMlStopBtn.addEventListener("click", async () => {
      if (!_flMlActiveTask) return;
      // Cancel the whole queue
      _flMlQueue = []; _flMlQueueIdx = -1; _flMlQueueTotal = 0;
      flMlStopBtn.disabled = true;
      try {
        await fetch("/dlc/project/machine-label-frames/stop", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ task_id: _flMlActiveTask }),
        });
        flMlStatus.textContent = "Stop signal sent.";
        flMlStatus.className   = "fe-extract-status";
      } catch (err) {
        flMlStatus.textContent = "Stop error: " + err.message;
        flMlStatus.className   = "fe-extract-status err";
        flMlStopBtn.disabled = false;
      }
    });

    // ══════════════════════════════════════════════════════════════
    // TAPNet propagation
    // ══════════════════════════════════════════════════════════════
    let _flTapPollTimer   = null;
    let _flTapActiveTask  = null;
    let _flTapConfirmed   = new Set();   // confirmed anchor frame names
    let _flTapnetFrames   = new Set();   // frames labeled by TAPNet

    // Load confirmed anchors + tapnet frames from server for current stem
    async function _flTapLoadSidecars() {
      if (!_flVideoStem) return;
      try {
        const res  = await fetch(`/dlc/project/tapnet-confirmed?video_stem=${encodeURIComponent(_flVideoStem)}`);
        if (!res.ok) return;
        const data = await res.json();
        _flTapConfirmed  = new Set(data.confirmed     || []);
        _flTapnetFrames  = new Set(data.tapnet_frames || []);
      } catch (_) { /* non-critical */ }
      _flTapUpdateFrameStatus();
      _flTapUpdateConfirmedCount();
    }

    // Update the per-frame badge + confirm button for the current frame
    function _flTapUpdateFrameStatus() {
      const fname = _flFrames[_flFrameIdx];
      if (!fname || !flTapCheckbox.checked) {
        flTapFrameBadge.classList.add("hidden");
        flTapConfirmBtn.classList.add("hidden");
        return;
      }
      flTapConfirmBtn.classList.remove("hidden");
      const isConfirmed = _flTapConfirmed.has(fname);
      const isTapnet    = _flTapnetFrames.has(fname);
      flTapConfirmLabel.textContent = isConfirmed ? "✓ Confirmed anchor" : "Confirm as anchor";
      flTapConfirmBtn.style.background = isConfirmed ? "var(--accent)" : "";
      flTapConfirmBtn.style.color      = isConfirmed ? "#fff" : "";
      if (isConfirmed) {
        flTapFrameBadge.textContent = "Anchor";
        flTapFrameBadge.style.color = "var(--accent)";
        flTapFrameBadge.style.borderColor = "var(--accent)";
        flTapFrameBadge.classList.remove("hidden");
      } else if (isTapnet) {
        flTapFrameBadge.textContent = "TAPNet";
        flTapFrameBadge.style.color = "var(--text-dim)";
        flTapFrameBadge.style.borderColor = "var(--border)";
        flTapFrameBadge.classList.remove("hidden");
      } else {
        flTapFrameBadge.classList.add("hidden");
      }
    }

    // Update the confirmed-count display and rerun button visibility
    function _flTapUpdateConfirmedCount() {
      const n = _flTapConfirmed.size;
      if (n > 0 && flTapCheckbox.checked) {
        flTapConfirmedCount.textContent = `${n} confirmed anchor${n === 1 ? "" : "s"}`;
        flTapConfirmedCount.classList.remove("hidden");
        flTapRerunBtn.classList.remove("hidden");
      } else {
        flTapConfirmedCount.classList.add("hidden");
        flTapRerunBtn.classList.add("hidden");
      }
    }

    flTapCheckbox.addEventListener("change", () => {
      flTapOpts.classList.toggle("hidden", !flTapCheckbox.checked);
      _flTapUpdateFrameStatus();
      _flTapUpdateConfirmedCount();
      if (flTapCheckbox.checked && _flVideoStem) _flTapLoadSidecars();
    });

    // Confirm / unconfirm current frame as anchor
    flTapConfirmBtn.addEventListener("click", async () => {
      const fname = _flFrames[_flFrameIdx];
      if (!fname || !_flVideoStem) return;
      try {
        const res  = await fetch("/dlc/project/tapnet-confirm-frame", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ video_stem: _flVideoStem, frame_name: fname }),
        });
        const data = await res.json();
        if (data.error) { console.error("confirm-frame:", data.error); return; }
        if (data.confirmed) _flTapConfirmed.add(fname);
        else                _flTapConfirmed.delete(fname);
        _flTapUpdateFrameStatus();
        _flTapUpdateConfirmedCount();
      } catch (err) {
        console.error("confirm-frame error:", err);
      }
    });

    // Re-run with confirmed anchors
    flTapRerunBtn.addEventListener("click", async () => {
      if (!_flVideoStem) {
        flTapStatus.textContent = "Select a labeled-data folder first.";
        flTapStatus.className   = "fe-extract-status err";
        return;
      }
      const ckpt = flTapCkpt.value.trim();
      if (!ckpt) {
        flTapStatus.textContent = "Enter the checkpoint path.";
        flTapStatus.className   = "fe-extract-status err";
        return;
      }
      flTapStatus.textContent = "";
      flTapStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch("/dlc/project/tapnet-propagate-multi", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            video_stem:            _flVideoStem,
            tapnet_checkpoint_path: ckpt,
            gpu_index:             0,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          flTapStatus.textContent = data.error || "Failed to start multi-anchor TAPNet.";
          flTapStatus.className   = "fe-extract-status err";
          return;
        }
        _flTapActiveTask = data.task_id;
        _flTapStartPolling(data.task_id);
      } catch (err) {
        flTapStatus.textContent = "Network error: " + err.message;
        flTapStatus.className   = "fe-extract-status err";
      }
    });

    function _flTapSetRunning(running) {
      flTapRunBtn.classList.toggle("hidden",  running);
      flTapStopBtn.classList.toggle("hidden", !running);
    }

    flTapCheckBtn.addEventListener("click", async () => {
      if (!_flVideoStem) {
        flTapCheckStatus.textContent = "Select a labeled-data folder first.";
        flTapCheckStatus.className   = "fe-extract-status err";
        return;
      }
      flTapCheckStatus.textContent = "Checking…";
      flTapCheckStatus.className   = "fe-extract-status";
      flTapSeqInfo.classList.add("hidden");
      try {
        const res  = await fetch(`/dlc/project/tapnet-check?video_stem=${encodeURIComponent(_flVideoStem)}`);
        const data = await res.json();
        if (!res.ok) {
          flTapCheckStatus.textContent = data.error || "Check failed.";
          flTapCheckStatus.className   = "fe-extract-status err";
          return;
        }
        const n = data.propagatable_count;
        flTapCheckStatus.textContent = `${n} propagatable sequence(s) found`;
        flTapCheckStatus.className   = "fe-extract-status " + (n > 0 ? "ok" : "");
        if (data.sequences && data.sequences.length > 0) {
          flTapSeqInfo.innerHTML = data.sequences.map(s => {
            const badge = s.propagatable
              ? `<span style="color:var(--accent)">✓ anchor: ${s.anchor}</span>`
              : `<span style="color:var(--text-dim)">✗ no labeled anchor</span>`;
            return `<div>${s.first_frame} → ${s.last_frame} &nbsp;(${s.frame_count} frames) &nbsp;${badge}</div>`;
          }).join("");
          flTapSeqInfo.classList.remove("hidden");
        }
      } catch (err) {
        flTapCheckStatus.textContent = "Network error: " + err.message;
        flTapCheckStatus.className   = "fe-extract-status err";
      }
    });

    flTapRunBtn.addEventListener("click", async () => {
      if (!_flVideoStem) {
        flTapStatus.textContent = "Select a labeled-data folder first.";
        flTapStatus.className   = "fe-extract-status err";
        return;
      }
      const ckpt = flTapCkpt.value.trim();
      if (!ckpt) {
        flTapStatus.textContent = "Enter the checkpoint path.";
        flTapStatus.className   = "fe-extract-status err";
        return;
      }
      flTapStatus.textContent = "";
      flTapStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch("/dlc/project/tapnet-propagate", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            video_stem:             _flVideoStem,
            tapnet_checkpoint_path: ckpt,
            anchor:                 flTapAnchor.value,
            gpu_index:              0,
            overwrite:              flTapOverwrite.checked,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          flTapStatus.textContent = data.error || "Failed to start TAPNet.";
          flTapStatus.className   = "fe-extract-status err";
          return;
        }
        _flTapActiveTask = data.task_id;
        _flTapStartPolling(data.task_id);
      } catch (err) {
        flTapStatus.textContent = "Network error: " + err.message;
        flTapStatus.className   = "fe-extract-status err";
      }
    });

    flTapStopBtn.addEventListener("click", async () => {
      if (!_flTapActiveTask) return;
      flTapStopBtn.disabled = true;
      try {
        await fetch("/dlc/project/tapnet-propagate/stop", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ task_id: _flTapActiveTask }),
        });
        flTapStatus.textContent = "Stop signal sent.";
        flTapStatus.className   = "fe-extract-status";
      } catch (err) {
        flTapStatus.textContent = "Stop error: " + err.message;
        flTapStatus.className   = "fe-extract-status err";
        flTapStopBtn.disabled = false;
      }
    });

    function _flTapStartPolling(taskId) {
      flTapProgress.classList.remove("hidden", "state-success", "state-fail");
      flTapTaskId.textContent        = taskId.slice(0, 12) + "…";
      flTapProgressBar.style.width   = "0%";
      flTapProgressPct.textContent   = "0 %";
      flTapProgressStage.textContent = "Queued";
      flTapLogOutput.textContent     = "Waiting for output…";
      _flTapSetRunning(true);
      if (_flTapPollTimer) clearInterval(_flTapPollTimer);
      _flTapPollTimer = setInterval(() => _flTapPoll(taskId), 2000);
      _flTapPoll(taskId);
    }

    async function _flTapPoll(taskId) {
      try {
        const res  = await fetch(`/status/${taskId}`);
        const data = await res.json();
        const pct  = Math.min(data.progress || 0, 100);
        flTapProgressBar.style.width   = pct + "%";
        flTapProgressPct.textContent   = pct + " %";
        flTapProgressStage.textContent = data.stage || data.state;
        if (data.log) {
          flTapLogOutput.textContent = data.log;
          flTapLogOutput.scrollTop   = flTapLogOutput.scrollHeight;
        }
        if (data.state === "SUCCESS") {
          clearInterval(_flTapPollTimer); _flTapPollTimer = null;
          const r = data.result || {};
          if (r.log) flTapLogOutput.textContent = r.log;
          flTapProgress.classList.add("state-success");
          flTapProgressStage.textContent = "✓ Propagation complete";
          flTapProgressBar.style.width   = "100%";
          flTapProgressPct.textContent   = "100 %";
          flTapStatus.textContent = `Done — ${r.frames_labeled || 0} frame(s) labeled across ${r.sequences_found || 0} sequence(s).`;
          flTapStatus.className   = "fe-extract-status ok";
          _flTapSetRunning(false);
          // Reload labels so the user sees propagated markers immediately
          if (_flVideoStem) {
            const found = _flStemData.find(s => s.video_stem === _flVideoStem);
            if (found) await _flSelectStem(found.video_stem, found.frames);
          }
        }
        if (data.state === "FAILURE" || data.state === "REVOKED") {
          clearInterval(_flTapPollTimer); _flTapPollTimer = null;
          const userStopped = data.state === "REVOKED" || (data.error || "").includes("__USER_STOPPED__");
          flTapProgress.classList.add("state-fail");
          flTapProgressStage.textContent = userStopped ? "✗ Stopped" : "✗ " + (data.error || "Failed").split("\n")[0];
          if (!userStopped) flTapLogOutput.textContent = data.error || "An unknown error occurred.";
          flTapStatus.textContent = userStopped ? "TAPNet stopped." : "";
          flTapStatus.className   = "fe-extract-status";
          _flTapSetRunning(false);
        }
      } catch (err) {
        console.error("flTapPoll:", err);
      }
    }

    function _flUpdateScorerFilename() {
      if (flScorerFilename) flScorerFilename.textContent = `CollectedData_${_flScorer}.csv`;
    }

    // ── Napari-inspired color palette ────────────────────────────
    const FL_COLORS = [
      "#f87171","#fb923c","#fbbf24","#a3e635","#34d399",
      "#22d3ee","#818cf8","#e879f9","#f43f5e","#10b981",
      "#3b82f6","#ec4899","#f59e0b","#84cc16","#06b6d4",
    ];
    function _flColor(i) { return FL_COLORS[i % FL_COLORS.length]; }

    // ── Open / close ────────────────────────────────────────────
    if (flOpenBtn) {
      flOpenBtn.addEventListener("click", () => {
        flCard.classList.remove("hidden");
        flCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
        _flLoad();
      });
    }

    flCloseBtn.addEventListener("click", () => {
      flCard.classList.add("hidden");
    });

    // ── Marker display controls ──────────────────────────────────
    const flZoomInput = document.getElementById("fl-zoom");
    const flZoomVal   = document.getElementById("fl-zoom-val");

    flZoomInput.addEventListener("input", () => {
      _flZoom = parseInt(flZoomInput.value, 10);
      flZoomVal.textContent = _flZoom + " %";
      if (_flImgLoaded) { _flFitCanvas(); _flDraw(); }
    });

    flMarkerSizeInput.addEventListener("input", () => {
      _flMarkerRadius = parseInt(flMarkerSizeInput.value, 10);
      flMarkerSizeVal.textContent = _flMarkerRadius;
      _flDraw();
    });

    flShowNamesInput.addEventListener("change", () => {
      _flShowNames = flShowNamesInput.checked;
      _flDraw();
    });

    // ── Load bodyparts + stems ───────────────────────────────────
    async function _flLoad() {
      try {
        const res  = await fetch("/dlc/project/bodyparts");
        const data = await res.json();
        _flBodyparts = data.bodyparts || [];
        _flScorer    = data.scorer    || "User";
        _flUpdateScorerFilename();
        _flRenderBodypartList();
      } catch (e) { console.error("FL bodyparts:", e); }
      await _flLoadStems();
    }

    async function _flLoadStems() {
      flStemStatus.textContent = "";
      flStemStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch("/dlc/project/labeled-frames");
        const data = await res.json();
        if (data.error) {
          flStemStatus.textContent = data.error;
          flStemStatus.className   = "fe-extract-status err";
          return;
        }
        _flStemData = data.video_stems || [];
        const prev  = flStemSelect.value;
        flStemSelect.innerHTML = '<option value="">— select video —</option>';
        _flStemData.forEach(s => {
          const opt = document.createElement("option");
          opt.value       = s.video_stem;
          opt.textContent = `${s.video_stem}  (${s.frames.length} frame${s.frames.length !== 1 ? "s" : ""})`;
          flStemSelect.appendChild(opt);
        });
        // Restore selection or auto-select if only one
        if (_flStemData.length === 1) {
          flStemSelect.value = _flStemData[0].video_stem;
          await _flSelectStem(_flStemData[0].video_stem, _flStemData[0].frames);
        } else if (prev && _flStemData.find(s => s.video_stem === prev)) {
          flStemSelect.value = prev;
          const found = _flStemData.find(s => s.video_stem === prev);
          if (found) await _flSelectStem(found.video_stem, found.frames);
        }
      } catch (e) {
        flStemStatus.textContent = `Error: ${e.message}`;
        flStemStatus.className   = "fe-extract-status err";
      }
    }

    flRefreshBtn.addEventListener("click", () => _flLoadStems());

    flStemSelect.addEventListener("change", async () => {
      const stem = flStemSelect.value;
      if (!stem) { flPlayerSec.classList.add("hidden"); return; }
      const found = _flStemData.find(s => s.video_stem === stem);
      if (found) await _flSelectStem(found.video_stem, found.frames);
    });

    async function _flSelectStem(stem, frames) {
      _flVideoStem = stem;
      _flFrames    = frames;
      _flFrameIdx  = 0;
      _flDirty     = false;

      // Fetch existing labels
      try {
        const res  = await fetch(`/dlc/project/labels/${encodeURIComponent(stem)}`);
        const data = await res.json();
        if (!data.error) {
          _flLabels = data.labels || {};
          _flScorer = data.scorer || _flScorer;
          _flUpdateScorerFilename();
        }
      } catch (_) { _flLabels = {}; }

      // Show "Update Threshold" button only when raw predictions exist
      flMlUpdateWrap.classList.add("hidden");
      flMlUpdateStatus.textContent = "";
      try {
        const r = await fetch(`/dlc/project/machine-label-raw?video_stem=${encodeURIComponent(stem)}`);
        const d = await r.json();
        if (d.exists) flMlUpdateWrap.classList.remove("hidden");
      } catch (_) {}

      flPlayerSec.classList.remove("hidden");
      _flUpdateLabelCount();
      _flShowFrame(0);

      // Load TAPNet sidecars (confirmed anchors + tapnet-labeled frames)
      _flTapConfirmed  = new Set();
      _flTapnetFrames  = new Set();
      _flTapLoadSidecars();
    }

    // ── Update Threshold ─────────────────────────────────────────
    flMlUpdateBtn.addEventListener("click", async () => {
      if (!_flVideoStem) return;
      flMlUpdateBtn.disabled       = true;
      flMlUpdateStatus.textContent = "Dispatching…";
      flMlUpdateStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch("/dlc/project/machine-label-reapply", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            video_stem:           _flVideoStem,
            likelihood_threshold: parseFloat(flMlLikelihood.value) || 0.9,
          }),
        });
        const dispatched = await res.json();
        if (!res.ok || dispatched.error) {
          flMlUpdateStatus.textContent = `Error: ${dispatched.error || "unknown"}`;
          flMlUpdateStatus.className   = "fe-extract-status err";
          flMlUpdateBtn.disabled = false;
          return;
        }

        // Poll until the worker finishes
        const taskId = dispatched.task_id;
        flMlUpdateStatus.textContent = "Applying…";
        let data = null;
        while (true) {
          await new Promise(r => setTimeout(r, 1000));
          const sr = await fetch(`/status/${taskId}`);
          const s  = await sr.json();
          if (s.state === "SUCCESS") {
            data = s.result;
            break;
          } else if (s.state === "FAILURE") {
            throw new Error(s.error || "Worker task failed");
          }
          // PENDING / PROGRESS — keep polling
        }

        flMlUpdateStatus.textContent =
          `Done — ${data.n_machine} machine label(s), ${data.n_human} human preserved`;
        flMlUpdateStatus.className = "fe-extract-status ok";
        // Reload labels so the labeler reflects the new threshold immediately
        const found = _flStemData.find(s => s.video_stem === _flVideoStem);
        if (found) await _flSelectStem(found.video_stem, found.frames);
      } catch (err) {
        flMlUpdateStatus.textContent = `Error: ${err.message}`;
        flMlUpdateStatus.className   = "fe-extract-status err";
      } finally {
        flMlUpdateBtn.disabled = false;
      }
    });

    // ── Render body-part chip list ───────────────────────────────
    function _flRenderBodypartList() {
      flBodypartList.innerHTML = "";
      if (!_flBodyparts.length) {
        flBpHint.classList.remove("hidden");
        return;
      }
      flBpHint.classList.add("hidden");
      _flBodyparts.forEach((bp, i) => {
        const chip = document.createElement("button");
        chip.className = "fl-bp-chip";
        chip.dataset.bp = bp;
        chip.style.setProperty("--fl-color", _flColor(i));
        chip.innerHTML =
          `<span class="fl-bp-dot"></span>` +
          `<span class="fl-bp-name">${bp}</span>` +
          `<svg class="fl-bp-check" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>` +
          `<svg class="fl-bp-eye-slash" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`;
        chip.addEventListener("click", () => _flSelectBp(bp));
        chip.addEventListener("dblclick", e => {
          e.preventDefault();
          _flSelectBp(bp);
          _flToggleVisibility(bp);
        });
        flBodypartList.appendChild(chip);
      });
      // Default: select first
      if (_flBodyparts.length) _flSelectBp(_flBodyparts[0]);
    }

    function _flSelectBp(bp) {
      _flSelectedBp = bp;
      flCanvas.style.cursor = "crosshair";
      flBodypartList.querySelectorAll(".fl-bp-chip").forEach(c => {
        c.classList.toggle("active", c.dataset.bp === bp);
      });
    }

    // ── Silent auto-save (fire-and-forget) ──────────────────────
    function _flAutoSave() {
      if (!_flVideoStem) return;
      fetch(`/dlc/project/labels/${encodeURIComponent(_flVideoStem)}`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ labels: _flLabels }),
      }).catch(() => {});  // silent — user can always use Save button
    }

    // ── Frame display ────────────────────────────────────────────
    function _flShowFrame(idx) {
      if (!_flFrames.length) return;
      // Auto-save unsaved changes before switching frames
      if (_flDirty) { _flDirty = false; _flAutoSave(); }
      idx = Math.max(0, Math.min(idx, _flFrames.length - 1));
      _flFrameIdx = idx;
      const fname = _flFrames[idx];
      flFrameInfo.textContent = `Frame ${idx + 1} / ${_flFrames.length}`;
      flFrameName.textContent = fname;

      _flUpdateBpChipStatus();
      _flUpdateLabelCount();
      _flTapUpdateFrameStatus();

      // Load frame image
      _flHoverBp   = null;
      _flImgLoaded = false;
      flCanvasLoading.classList.remove("hidden");
      const img   = new Image();
      img.onload  = () => {
        _flImg       = img;
        _flImgLoaded = true;
        flCanvasLoading.classList.add("hidden");
        _flFitCanvas();
        _flDraw();
      };
      img.onerror = () => {
        flCanvasLoading.textContent = "Failed to load frame.";
        flCanvasLoading.classList.remove("hidden");
      };
      img.src = `/dlc/project/frame-image/${encodeURIComponent(_flVideoStem)}/${encodeURIComponent(fname)}`;
    }

    function _flFitCanvas() {
      const wrap = flCanvas.parentElement;
      const cs   = getComputedStyle(flCard);
      const padL = parseFloat(cs.paddingLeft)  || 0;
      const padR = parseFloat(cs.paddingRight) || 0;

      // Base width = card's inner content width (canvas at 100% zoom)
      const baseW = flCard.clientWidth - padL - padR;

      // Maximum width: fill the viewport minus a small margin on each side.
      // The card is centred, so the canvas expands symmetrically beyond its border.
      const maxW    = Math.max(baseW, window.innerWidth - 32);
      const targetW = Math.min(Math.round(baseW * (_flZoom / 100)), Math.floor(maxW));

      flCanvas.width  = targetW;
      flCanvas.height = Math.round(_flImg.naturalHeight * (targetW / _flImg.naturalWidth));

      // Break out of card padding symmetrically — card has no overflow:hidden so
      // the wrapper renders beyond the card border without clipping.
      const extra = targetW - baseW;
      if (extra > 0) {
        wrap.style.width      = targetW + "px";
        wrap.style.marginLeft = `-${extra / 2}px`;
      } else {
        wrap.style.width      = "";
        wrap.style.marginLeft = "";
      }
    }

    // Re-fit whenever the card width changes (window resize, layout shifts)
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => {
        if (_flImgLoaded) { _flFitCanvas(); _flDraw(); }
      }).observe(flCard);
    }

    function _flDraw() {
      if (!_flImgLoaded) return;
      flCtx.clearRect(0, 0, flCanvas.width, flCanvas.height);
      flCtx.drawImage(_flImg, 0, 0, flCanvas.width, flCanvas.height);

      const fname       = _flFrames[_flFrameIdx];
      const frameLabels = _flLabels[fname] || {};
      const scaleX      = flCanvas.width  / _flImg.naturalWidth;
      const scaleY      = flCanvas.height / _flImg.naturalHeight;
      const r           = _flMarkerRadius;

      _flBodyparts.forEach((bp, i) => {
        const pt = frameLabels[bp];
        if (!pt) return;
        if (_flHidden[fname] && _flHidden[fname][bp]) return;
        const cx    = pt[0] * scaleX;
        const cy    = pt[1] * scaleY;
        const color = _flColor(i);

        // Selection ring for the active bodypart
        if (bp === _flSelectedBp) {
          flCtx.beginPath();
          flCtx.arc(cx, cy, r + 3.5, 0, Math.PI * 2);
          flCtx.strokeStyle = "rgba(255,255,255,0.85)";
          flCtx.lineWidth   = 2;
          flCtx.stroke();
        }

        // Filled circle with a thin dark outline for contrast
        flCtx.beginPath();
        flCtx.arc(cx, cy, r, 0, Math.PI * 2);
        flCtx.fillStyle = color;
        flCtx.fill();
        flCtx.strokeStyle = "rgba(0,0,0,0.55)";
        flCtx.lineWidth   = 1.2;
        flCtx.stroke();

        if (_flShowNames || bp === _flHoverBp) {
          flCtx.font = "bold 11px 'JetBrains Mono', monospace";
          const tw = flCtx.measureText(bp).width;
          const tx = cx + r + 4;
          const ty = cy + 4;
          flCtx.fillStyle = "rgba(12,13,16,.65)";
          flCtx.fillRect(tx - 2, ty - 11, tw + 6, 14);
          flCtx.fillStyle = color;
          flCtx.fillText(bp, tx + 1, ty);
        }
      });
    }

    // ── Canvas interaction ───────────────────────────────────────
    function _flHitTest(cx, cy, fname) {
      const frameLabels = _flLabels[fname] || {};
      const scaleX = flCanvas.width  / _flImg.naturalWidth;
      const scaleY = flCanvas.height / _flImg.naturalHeight;
      const hitR   = _flMarkerRadius + 6;
      let hit = null;
      _flBodyparts.forEach(bp => {
        const pt = frameLabels[bp];
        if (!pt) return;
        if (_flHidden[fname] && _flHidden[fname][bp]) return;
        const dx = pt[0] * scaleX - cx;
        const dy = pt[1] * scaleY - cy;
        if (Math.sqrt(dx * dx + dy * dy) <= hitR) hit = bp;
      });
      return hit;
    }

    flCanvas.addEventListener("click", e => {
      if (!_flImgLoaded || !_flVideoStem) return;
      const rect = flCanvas.getBoundingClientRect();
      const cx   = e.clientX - rect.left;
      const cy   = e.clientY - rect.top;
      const fname = _flFrames[_flFrameIdx];

      // Click near an existing marker → select it
      const hit = _flHitTest(cx, cy, fname);
      if (hit) {
        _flSelectBp(hit);
        return;
      }

      // Click on empty space → place point for selected bp
      if (!_flSelectedBp) return;
      const scaleX = flCanvas.width  / _flImg.naturalWidth;
      const scaleY = flCanvas.height / _flImg.naturalHeight;
      if (!_flLabels[fname]) _flLabels[fname] = {};
      _flLabels[fname][_flSelectedBp] = [cx / scaleX, cy / scaleY];
      _flDirty = true;
      _flDraw();
      _flUpdateBpChipStatus();
      _flUpdateLabelCount();
      _flAutoAdvanceBp();
    });

    // Right-click → remove current body-part point
    flCanvas.addEventListener("contextmenu", e => {
      e.preventDefault();
      if (!_flSelectedBp || !_flVideoStem) return;
      _flRemoveBpLabel(_flSelectedBp);
    });

    flCanvas.addEventListener("mousemove", e => {
      if (!_flImgLoaded) return;
      const rect  = flCanvas.getBoundingClientRect();
      const cx    = e.clientX - rect.left;
      const cy    = e.clientY - rect.top;
      const fname = _flFrames[_flFrameIdx];
      const found = _flHitTest(cx, cy, fname);
      if (found !== _flHoverBp) {
        _flHoverBp = found;
        _flDraw();
      }
      flCanvas.style.cursor = found ? "pointer" : (_flSelectedBp ? "crosshair" : "default");
    });

    flCanvas.addEventListener("mouseenter", () => { _flCursorInCanvas = true; });
    flCanvas.addEventListener("mouseleave", () => {
      _flCursorInCanvas = false;
      if (_flHoverBp) { _flHoverBp = null; _flDraw(); }
      flCanvas.style.cursor = _flSelectedBp ? "crosshair" : "default";
    });

    function _flRemoveBpLabel(bp) {
      const fname = _flFrames[_flFrameIdx];
      if (!fname || !_flLabels[fname]) return;
      _flLabels[fname][bp] = null;
      // Also clear hidden state when marker is deleted
      if (_flHidden[fname]) delete _flHidden[fname][bp];
      _flDirty = true;
      _flDraw();
      _flUpdateBpChipStatus();
      _flUpdateLabelCount();
    }

    function _flToggleVisibility(bp) {
      const fname = _flFrames[_flFrameIdx];
      if (!fname) return;
      if (!_flHidden[fname]) _flHidden[fname] = {};
      _flHidden[fname][bp] = !_flHidden[fname][bp];
      _flDraw();
      _flUpdateBpChipStatus();
    }

    // Clear all body-part markers on the currently displayed frame
    function _flClearFrame() {
      const fname = _flFrames[_flFrameIdx];
      if (!fname) return;
      if (!_flLabels[fname]) _flLabels[fname] = {};
      _flBodyparts.forEach(bp => { _flLabels[fname][bp] = null; });
      delete _flHidden[fname];
      _flDirty = true;
      _flDraw();
      _flUpdateBpChipStatus();
      _flUpdateLabelCount();
    }

    // Double-click on Clear Frame button erases all markers on current frame
    document.getElementById("fl-btn-clear-frame").addEventListener("dblclick", e => {
      e.preventDefault();
      if (!_flVideoStem) return;
      _flClearFrame();
    });

    document.getElementById("fl-btn-delete-frame").addEventListener("dblclick", async e => {
      e.preventDefault();
      const fname = _flFrames[_flFrameIdx];
      if (!_flVideoStem || !fname) return;

      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        const res = await fetch("/dlc/project/frame", {
          method:  "DELETE",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ video_stem: _flVideoStem, frame_name: fname }),
        });
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          flSaveStatus.textContent = `Delete failed: ${d.error || res.status}`;
          flSaveStatus.className   = "fl-save-status err";
          return;
        }
        // Remove from in-memory state
        delete _flLabels[fname];
        _flFrames.splice(_flFrameIdx, 1);
        _flUpdateLabelCount();

        if (_flFrames.length === 0) {
          // No frames left — reset canvas
          _flFrameIdx = 0;
          flFrameInfo.textContent = "Frame 0 / 0";
          flFrameName.textContent = "";
          const ctx = flCanvas.getContext("2d");
          ctx.clearRect(0, 0, flCanvas.width, flCanvas.height);
        } else {
          _flShowFrame(Math.min(_flFrameIdx, _flFrames.length - 1));
        }
        flSaveStatus.textContent = `Deleted ${fname}`;
        flSaveStatus.className   = "fl-save-status";
      } catch (err) {
        flSaveStatus.textContent = `Delete error: ${err.message}`;
        flSaveStatus.className   = "fl-save-status err";
      } finally {
        btn.disabled = false;
      }
    });

    // Auto-advance to the next unlabeled body part (napari behavior)
    function _flAutoAdvanceBp() {
      const fname       = _flFrames[_flFrameIdx];
      const frameLabels = _flLabels[fname] || {};
      const cur         = _flBodyparts.indexOf(_flSelectedBp);
      for (let i = 1; i <= _flBodyparts.length; i++) {
        const next = _flBodyparts[(cur + i) % _flBodyparts.length];
        if (!frameLabels[next]) { _flSelectBp(next); return; }
      }
      // All body parts labeled on this frame → move to next frame
      if (_flFrameIdx < _flFrames.length - 1) _flShowFrame(_flFrameIdx + 1);
    }

    // ── Chip status updates ──────────────────────────────────────
    function _flUpdateBpChipStatus() {
      const fname       = _flFrames[_flFrameIdx];
      const frameLabels = _flLabels[fname] || {};
      flBodypartList.querySelectorAll(".fl-bp-chip").forEach(c => {
        const bp = c.dataset.bp;
        const pt = frameLabels[bp];
        const isHidden = !!(_flHidden[fname] && _flHidden[fname][bp]);
        c.classList.toggle("labeled",    !!(pt && pt[0] !== null));
        c.classList.toggle("vis-hidden", !!(pt && pt[0] !== null) && isHidden);
      });
    }

    function _flUpdateLabelCount() {
      const labeled = Object.values(_flLabels).filter(fl =>
        _flBodyparts.some(bp => fl && fl[bp] && fl[bp][0] !== null)
      ).length;
      flLabelCount.textContent = `${labeled} / ${_flFrames.length} frame${_flFrames.length !== 1 ? "s" : ""} labeled`;
    }

    // ── Navigation ───────────────────────────────────────────────
    flBtnPrev.addEventListener("click", () => _flShowFrame(_flFrameIdx - 1));
    flBtnNext.addEventListener("click", () => _flShowFrame(_flFrameIdx + 1));

    document.addEventListener("keydown", e => {
      if (flCard.classList.contains("hidden")) return;
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;

      // WASD nudge: move the selected marker when cursor is inside the canvas
      // and the current frame already has a point placed for the active body part.
      const _wasdKeys = ["a", "d", "w", "s"];
      if (_wasdKeys.includes(e.key) && _flCursorInCanvas && _flSelectedBp && _flVideoStem) {
        const fname = _flFrames[_flFrameIdx];
        const pt    = fname && _flLabels[fname] && _flLabels[fname][_flSelectedBp];
        if (pt && pt[0] !== null) {
          e.preventDefault();
          const step = e.shiftKey ? 10 : 1;
          let [x, y] = pt;
          if (e.key === "a") x -= step;
          if (e.key === "d") x += step;
          if (e.key === "w") y -= step;
          if (e.key === "s") y += step;
          // Clamp to image dimensions
          x = Math.max(0, Math.min(x, _flImg.naturalWidth  - 1));
          y = Math.max(0, Math.min(y, _flImg.naturalHeight - 1));
          _flLabels[fname][_flSelectedBp] = [x, y];
          _flDirty = true;
          _flDraw();
          return;
        }
      }

      // Tab / Shift+Tab — cycle through body parts
      if (e.key === "Tab" && _flVideoStem) {
        e.preventDefault();
        const cur  = _flBodyparts.indexOf(_flSelectedBp);
        const next = e.shiftKey
          ? (_flBodyparts.length + cur - 1) % _flBodyparts.length
          : (cur + 1) % _flBodyparts.length;
        _flSelectBp(_flBodyparts[next]);
        return;
      }

      // Spacebar — toggle visibility of selected marker
      if (e.key === " " && _flSelectedBp && _flVideoStem) {
        e.preventDefault();
        _flToggleVisibility(_flSelectedBp);
        return;
      }

      // Backspace — delete selected marker (no cursor-in-canvas requirement)
      if (e.key === "Backspace" && _flSelectedBp && _flVideoStem) {
        e.preventDefault();
        _flRemoveBpLabel(_flSelectedBp);
        return;
      }

      // Frame navigation (arrow keys)
      if (e.key === "ArrowLeft")  { e.preventDefault(); _flShowFrame(_flFrameIdx - 1); }
      if (e.key === "ArrowRight") { e.preventDefault(); _flShowFrame(_flFrameIdx + 1); }

      // Delete (with cursor over canvas) — also deletes selected marker
      if (e.key === "Delete" && _flCursorInCanvas && _flSelectedBp && _flVideoStem) {
        e.preventDefault();
        _flRemoveBpLabel(_flSelectedBp);
      }
    });

    // ── Save ─────────────────────────────────────────────────────
    flBtnSave.addEventListener("click", async () => {
      if (!_flVideoStem) return;
      flBtnSave.disabled      = true;
      flSaveStatus.textContent = "Saving…";
      flSaveStatus.className   = "fl-save-status";
      try {
        const res  = await fetch(`/dlc/project/labels/${encodeURIComponent(_flVideoStem)}`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ labels: _flLabels }),
        });
        const data = await res.json();
        if (res.ok) {
          _flDirty = false;
          const h5note = data.h5_warning ? ` (H5 warning: ${data.h5_warning})` : (data.h5_path ? " + H5" : "");
          flSaveStatus.textContent = `Saved ✓${h5note}`;
          flSaveStatus.className   = "fl-save-status ok";
        } else {
          flSaveStatus.textContent = data.error || "Error saving";
          flSaveStatus.className   = "fl-save-status err";
        }
      } catch (err) {
        flSaveStatus.textContent = `Network error: ${err.message}`;
        flSaveStatus.className   = "fl-save-status err";
      }
      flBtnSave.disabled = false;
      setTimeout(() => {
        flSaveStatus.textContent = "";
        flSaveStatus.className   = "fl-save-status";
      }, 4000);
    });

    // ── Save all to H5 ───────────────────────────────────────────
    flBtnSaveH5.addEventListener("click", async () => {
      if (!_flVideoStem) return;

      // First flush current frame's CSV
      flBtnSaveH5.disabled     = true;
      flSaveStatus.textContent = "Saving CSV…";
      flSaveStatus.className   = "fl-save-status";
      try {
        const csvRes = await fetch(`/dlc/project/labels/${encodeURIComponent(_flVideoStem)}`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ labels: _flLabels }),
        });
        if (!csvRes.ok) {
          const d = await csvRes.json();
          flSaveStatus.textContent = d.error || "CSV save failed";
          flSaveStatus.className   = "fl-save-status err";
          flBtnSaveH5.disabled = false;
          return;
        }
        _flDirty = false;
      } catch (err) {
        flSaveStatus.textContent = `Network error: ${err.message}`;
        flSaveStatus.className   = "fl-save-status err";
        flBtnSaveH5.disabled = false;
        return;
      }

      // Dispatch Celery task for the full convertcsv2h5
      flSaveStatus.textContent = "Converting to H5…";
      try {
        const res  = await fetch("/dlc/project/labels/convert-to-h5", { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
          flSaveStatus.textContent = data.error || "Failed to dispatch H5 conversion";
          flSaveStatus.className   = "fl-save-status err";
          flBtnSaveH5.disabled = false;
          return;
        }

        // Poll until done
        const taskId = data.task_id;
        const poll = setInterval(async () => {
          try {
            const tr   = await fetch(`/status/${taskId}`);
            const td   = await tr.json();
            if (td.state === "SUCCESS") {
              clearInterval(poll);
              const r   = td.result || {};
              const cnt = (r.converted || []).length;
              const sk  = (r.skipped  || []).length;
              const note = sk > 0 ? `, ${sk} skipped` : "";
              flSaveStatus.textContent = `Saved ✓ CSV + H5 (${cnt} folder${cnt !== 1 ? "s" : ""}${note})`;
              flSaveStatus.className   = "fl-save-status ok";
              flBtnSaveH5.disabled = false;
              setTimeout(() => { flSaveStatus.textContent = ""; flSaveStatus.className = "fl-save-status"; }, 6000);
            } else if (td.state === "FAILURE" || td.state === "REVOKED") {
              clearInterval(poll);
              const errFull = td.error || td.state || "";
              // Show the last non-empty line (most specific part of the traceback)
              const lines   = errFull.split("\n").map(l => l.trim()).filter(Boolean);
              const errLine = lines[lines.length - 1] || errFull;
              flSaveStatus.textContent = "H5 failed: " + errLine;
              flSaveStatus.title       = errFull;   // full traceback on hover
              flSaveStatus.className   = "fl-save-status err";
              console.error("H5 conversion traceback:\n", errFull);
              flBtnSaveH5.disabled = false;
            }
          } catch (_) {}
        }, 1500);
      } catch (err) {
        flSaveStatus.textContent = `Network error: ${err.message}`;
        flSaveStatus.className   = "fl-save-status err";
        flBtnSaveH5.disabled = false;
      }
    });

    // ── Redraw on resize ─────────────────────────────────────────
    window.addEventListener("resize", () => {
      if (!flCard.classList.contains("hidden") && _flImgLoaded) {
        _flFitCanvas();
        _flDraw();
      }
    });
