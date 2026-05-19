"use strict";
import { state } from './state.js';
import { _populateGpuSelect } from './training.js';
import { makeFileBrowser } from './components/file_browser.js';

    const avCard         = document.getElementById("analyze-card");
    const avOpenBtn      = document.getElementById("btn-open-analyze");
    const avCloseBtn     = document.getElementById("btn-close-analyze");
    const avTargetPath   = document.getElementById("av-target-path");
    const avBrowseUp     = document.getElementById("av-browse-up");
    const avBrowseBtn    = document.getElementById("av-browse-btn");
    const avBrowser      = document.getElementById("av-browser");
    const avSnapshot     = document.getElementById("av-snapshot");
    const avRefreshSnaps = document.getElementById("av-refresh-snapshots");
    const avRunBtn       = document.getElementById("btn-run-analyze");
    const avStopBtn      = document.getElementById("btn-stop-analyze");
    const avRunStatus    = document.getElementById("av-run-status");
    const avProgress     = document.getElementById("av-progress");
    const avTaskId       = document.getElementById("av-task-id");
    const avProgressBar  = document.getElementById("av-progress-bar");
    const avProgressStage= document.getElementById("av-progress-stage");
    const avProgressPct  = document.getElementById("av-progress-pct");
    const avLogOutput    = document.getElementById("av-log-output");

    // Batch selection state
    const avBatchList    = document.getElementById("av-batch-list");
    const avBatchAddBtn  = document.getElementById("av-batch-add-btn");
    const avBatchClearBtn= document.getElementById("av-batch-clear-btn");
    let _avBatchList     = [];         // ordered array of selected paths

    let _avPollTimer  = null;
    let _avActiveTask = null;
    let _avProjectPath   = null;   // set when browse data arrives

    // ── Labeled video params toggle ──────────────────────────────
    const avCreateLabeledCb  = document.getElementById("av-create-labeled");
    const avLabeledParamsSection = document.getElementById("av-labeled-params-section");

    function _avSyncLabeledParams() {
      if (!avLabeledParamsSection) return;
      avLabeledParamsSection.style.display = avCreateLabeledCb?.checked ? "" : "none";
    }
    avCreateLabeledCb?.addEventListener("change", _avSyncLabeledParams);
    _avSyncLabeledParams();  // apply initial state (checkbox unchecked → hidden)

    // ── Snapshots ─────────────────────────────────────────────
    async function _avLoadSnapshots() {
      try {
        // No shuffle filter — show all shuffles so models from any shuffle are visible.
        // The backend will auto-correct the shuffle when snapshot_path is provided.
        const res  = await fetch("/dlc/project/snapshots");
        const data = await res.json();
        if (data.error) return;

        avSnapshot.innerHTML = "";

        // "Latest" default option — use actual path so shuffle is auto-derived
        const latestOpt = document.createElement("option");
        latestOpt.value = data.latest_rel_path || "-1";
        if (data.latest_label) {
          const iterStr = data.latest_iteration != null
            ? `  ·  iter ${data.latest_iteration.toLocaleString()}`
            : "";
          const shStr = data.latest_shuffle != null ? `  ·  sh${data.latest_shuffle}` : "";
          latestOpt.textContent = `Latest — ${data.latest_label}${iterStr}${shStr}`;
        } else {
          latestOpt.textContent = "Latest (from config)";
        }
        avSnapshot.appendChild(latestOpt);

        // Individual snapshots (ascending by iteration)
        (data.snapshots || []).forEach(s => {
          const opt = document.createElement("option");
          opt.value = s.rel_path;
          const iterStr = s.iteration != null
            ? `  ·  iter ${s.iteration.toLocaleString()}`
            : "";
          const shStr = s.shuffle != null ? `  ·  sh${s.shuffle}` : "";
          opt.textContent = `${s.label}${iterStr}${shStr}`;
          avSnapshot.appendChild(opt);
        });
      } catch (err) {
        console.error("avLoadSnapshots:", err);
      }
    }

    avRefreshSnaps.addEventListener("click", _avLoadSnapshots);

    // Reload snapshots when shuffle changes (indices are per-shuffle)
    document.getElementById("av-shuffle").addEventListener("change", _avLoadSnapshots);

    // ── Batch list management ─────────────────────────────────
    function _avRenderBatchList() {
      if (!avBatchList) return;
      if (_avBatchList.length === 0) {
        avBatchList.style.display = "none";
        avBatchList.innerHTML = "";
        return;
      }
      avBatchList.style.display = "";
      avBatchList.innerHTML = "";
      _avBatchList.forEach((p, idx) => {
        const row = document.createElement("div");
        row.style.cssText = "display:flex;align-items:center;gap:.35rem;padding:.15rem 0;border-bottom:1px solid var(--border)";
        const label = document.createElement("span");
        label.textContent = p;
        label.style.cssText = "flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
        label.title = p;
        const rm = document.createElement("button");
        rm.textContent = "✕";
        rm.style.cssText = "flex-shrink:0;background:none;border:none;color:var(--text-dim);cursor:pointer;font-size:.75rem;padding:0 .2rem;line-height:1";
        rm.title = "Remove";
        rm.addEventListener("click", () => {
          _avBatchList.splice(idx, 1);
          _avRenderBatchList();
        });
        row.appendChild(label);
        row.appendChild(rm);
        avBatchList.appendChild(row);
      });
    }

    function _avAddToList(path) {
      if (!path) return;
      if (_avBatchList.includes(path)) return;   // no duplicates
      _avBatchList.push(path);
      _avRenderBatchList();
    }

    avBatchAddBtn?.addEventListener("click", () => {
      // The component writes the highlighted path into avTargetPath.value on
      // single-click, so the typed-input fallback IS the highlighted path.
      const typed = avTargetPath.value.trim();
      if (typed) _avAddToList(typed);
    });

    avBatchClearBtn?.addEventListener("click", () => {
      _avBatchList = [];
      _avRenderBatchList();
    });

    // ── Project browser (canonical file-browser component) ────
    const avPicker = makeFileBrowser({
      inputEl: avTargetPath,
      paneEl:  avBrowser,
      onPick:  _avAddToList,
    });

    avBrowseBtn.addEventListener("click", async () => {
      const isHidden = avBrowser.classList.contains("hidden");
      if (!isHidden) {
        // closing
        avBrowser.classList.add("hidden");
        return;
      }
      const typed = avTargetPath.value.trim();
      if (typed) { avPicker.openAt(typed); return; }
      // No typed path → bootstrap from project browse endpoint.
      try {
        const res  = await fetch("/dlc/project/browse");
        const data = await res.json();
        if (data.error) {
          avBrowser.classList.remove("hidden");
          avBrowser.textContent = data.error;
          return;
        }
        _avProjectPath = data.project_path;
        avPicker.openAt(data.project_path);
      } catch (err) {
        avBrowser.classList.remove("hidden");
        avBrowser.textContent = "Failed to load project.";
        console.error("avBrowse:", err);
      }
    });

    avBrowseUp?.addEventListener("click", () => avPicker.up());

    avTargetPath?.addEventListener("keydown", e => {
      if (e.key === "Enter")  { e.preventDefault(); avPicker.browseDir(avTargetPath.value.trim()); avBrowser.classList.remove("hidden"); }
      if (e.key === "Escape") { avBrowser.classList.add("hidden"); avTargetPath.blur(); }
    });
    avTargetPath?.addEventListener("paste", e => {
      if (avBrowser.classList.contains("hidden")) return;  // only navigate when browser is open
      setTimeout(() => avPicker.browseDir(avTargetPath.value.trim()), 0);
    });

    // ── Running state helpers ─────────────────────────────────
    function _avSetRunning(running) {
      avRunBtn.classList.toggle("hidden",  running);
      avStopBtn.classList.toggle("hidden", !running);
      avRunBtn.disabled  = running;
      const _clvBtn = document.getElementById("btn-create-labeled-video");
      if (_clvBtn) _clvBtn.disabled = running;
    }

    // ── Polling ───────────────────────────────────────────────
    function _avStartPolling(taskId) {
      avProgress.classList.remove("hidden", "state-success", "state-fail");
      avTaskId.textContent    = taskId.slice(0, 12) + "…";
      avProgressBar.style.width = "0%";
      avProgressPct.textContent = "0 %";
      avProgressStage.textContent = "Queued";
      avLogOutput.textContent = "Waiting for output…";
      _avSetRunning(true);

      if (_avPollTimer) clearInterval(_avPollTimer);
      _avPollTimer = setInterval(() => _avPoll(taskId), 2000);
      _avPoll(taskId);
    }

    async function _avPoll(taskId) {
      try {
        const res  = await fetch(`/status/${taskId}`);
        const data = await res.json();

        const pct = Math.min(data.progress || 0, 100);
        avProgressBar.style.width   = pct + "%";
        avProgressPct.textContent   = pct + " %";
        avProgressStage.textContent = data.stage || data.state;

        if (data.log) {
          avLogOutput.textContent = data.log;
          avLogOutput.scrollTop   = avLogOutput.scrollHeight;
        }

        if (data.state === "SUCCESS") {
          clearInterval(_avPollTimer); _avPollTimer = null;
          avProgress.classList.add("state-success");
          avProgressStage.textContent = "✓ Analysis complete";
          avProgressBar.style.width   = "100%";
          avProgressPct.textContent   = "100 %";
          avRunStatus.textContent = "Analysis finished successfully.";
          avRunStatus.className   = "fe-extract-status ok";
          _avSetRunning(false);
          if (data.result && data.result.log) avLogOutput.textContent = data.result.log;
        }

        if (data.state === "FAILURE" || data.state === "REVOKED") {
          clearInterval(_avPollTimer); _avPollTimer = null;
          const userStopped = data.state === "REVOKED" ||
            (data.error || "").includes("__USER_STOPPED__");
          avProgress.classList.add("state-fail");
          avProgressStage.textContent = userStopped
            ? "✗ Stopped by user"
            : "✗ " + (data.error || "Failed").split("\n")[0];
          if (!userStopped) avLogOutput.textContent = data.error || "An unknown error occurred.";
          avRunStatus.textContent = userStopped ? "Analysis stopped." : "";
          avRunStatus.className   = "fe-extract-status";
          _avSetRunning(false);
        }
      } catch (err) {
        console.error("Analyze poll error:", err);
      }
    }

    // ── Open / Close ──────────────────────────────────────────
    avOpenBtn?.addEventListener("click", async () => {
      avCard.classList.remove("hidden");
      avCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      _avLoadSnapshots();
      _populateGpuSelect("av-gputouse");
      avBrowser.classList.add("hidden");
      // Auto-reconnect to a running analyze job
      if (!_avActiveTask) {
        try {
          const res  = await fetch("/dlc/training/jobs");
          const data = await res.json();
          const activeAnalyze = (data.jobs || []).find(
            j => (j.status === "running" || j.status === "dead") && j.operation === "analyze"
          );
          if (activeAnalyze) {
            _avActiveTask = activeAnalyze.task_id;
            _avStartPolling(activeAnalyze.task_id);
          }
        } catch (_) {}
      }
    });

    avCloseBtn?.addEventListener("click", () => {
      avCard.classList.add("hidden");
      if (_avPollTimer) { clearInterval(_avPollTimer); _avPollTimer = null; }
    });

    // ── Run ───────────────────────────────────────────────────
    avRunBtn.addEventListener("click", async () => {
      // Build the list of target paths: batch list takes priority; fall back to text input
      const target_paths = _avBatchList.length > 0
        ? [..._avBatchList]
        : (avTargetPath.value.trim() ? [avTargetPath.value.trim()] : []);

      if (!target_paths.length) {
        avRunStatus.textContent = "Please select or enter at least one target path.";
        avRunStatus.className   = "fe-extract-status err";
        return;
      }

      avRunStatus.textContent = "";
      avRunStatus.className   = "fe-extract-status";

      const snapshotVal = avSnapshot.value;

      const batchSizeVal = document.getElementById("av-batch-size").value;
      const clvPcutoff   = document.getElementById("clv-pcutoff").value;
      const body = {
        target_paths,
        shuffle:          parseInt(document.getElementById("av-shuffle").value) || 1,
        trainingsetindex: parseInt(document.getElementById("av-trainingsetindex").value) ?? 0,
        gputouse:         document.getElementById("av-gputouse").value !== ""
                            ? parseInt(document.getElementById("av-gputouse").value)
                            : null,
        batch_size:       batchSizeVal !== "" ? parseInt(batchSizeVal) : null,
        save_as_csv:      document.getElementById("av-save-csv").checked,
        create_labeled:   document.getElementById("av-create-labeled").checked,
        snapshot_path:    snapshotVal !== "-1" ? snapshotVal : null,
        // labeled video params (only relevant when create_labeled=true)
        pcutoff:          clvPcutoff !== "" ? parseFloat(clvPcutoff) : null,
        dotsize:          parseInt(document.getElementById("clv-dotsize").value) || 8,
        colormap:         document.getElementById("clv-colormap").value,
        modelprefix:      (document.getElementById("clv-modelprefix").value || "").trim(),
        filtered:         document.getElementById("clv-filtered").checked,
        draw_skeleton:    document.getElementById("clv-draw-skeleton").checked,
        overwrite:        document.getElementById("clv-overwrite").checked,
        destfolder:       (document.getElementById("clv-destfolder").value || "").trim() || null,
      };

      try {
        const res  = await fetch("/dlc/project/analyze", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) {
          avRunStatus.textContent = data.error || "Failed to start analysis.";
          avRunStatus.className   = "fe-extract-status err";
          return;
        }
        // Support both single task_id and batch task_ids
        const firstId = (data.task_ids && data.task_ids[0]) || data.task_id;
        _avActiveTask = firstId;
        _avStartPolling(firstId);
        if (data.task_ids && data.task_ids.length > 1) {
          avRunStatus.textContent = `${data.task_ids.length} analysis jobs dispatched.`;
          avRunStatus.className   = "fe-extract-status ok";
        }
      } catch (err) {
        avRunStatus.textContent = "Network error: " + err.message;
        avRunStatus.className   = "fe-extract-status err";
      }
    });

    // ── Stop ──────────────────────────────────────────────────
    avStopBtn.addEventListener("click", async () => {
      if (!_avActiveTask) return;
      avStopBtn.disabled = true;
      try {
        await fetch("/dlc/project/analyze/stop", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ task_id: _avActiveTask }),
        });
        avRunStatus.textContent = "Stop signal sent — analysis will terminate shortly.";
        avRunStatus.className   = "fe-extract-status";
      } catch (err) {
        avRunStatus.textContent = "Stop error: " + err.message;
        avRunStatus.className   = "fe-extract-status err";
        avStopBtn.disabled = false;
      }
    });

    // ── Create Labeled Video (standalone) ─────────────────────
    const clvBtn        = document.getElementById("btn-create-labeled-video");
    const clvStatus     = document.getElementById("av-clv-status");
    const clvDestInput  = document.getElementById("clv-destfolder");
    const clvDestUp     = document.getElementById("clv-dest-up");
    const clvDestBrowse = document.getElementById("clv-dest-browse-btn");
    const clvDestClear  = document.getElementById("clv-dest-clear-btn");
    const clvDestBrowser= document.getElementById("clv-dest-browser");

    // destfolder browser — directories only (canonical file-browser component)
    const clvDestPicker = clvDestInput && clvDestBrowser ? makeFileBrowser({
      inputEl: clvDestInput,
      paneEl:  clvDestBrowser,
      dirOnly: true,
    }) : null;

    clvDestBrowse?.addEventListener("click", () => {
      const startPath = clvDestInput.value.trim() || _avProjectPath || "/";
      clvDestPicker?.openAt(startPath);
    });

    clvDestUp?.addEventListener("click", () => clvDestPicker?.up());

    clvDestInput?.addEventListener("keydown", e => {
      if (e.key === "Enter")  { e.preventDefault(); clvDestPicker?.browseDir(clvDestInput.value.trim()); clvDestBrowser.classList.remove("hidden"); }
      if (e.key === "Escape") { clvDestBrowser.classList.add("hidden"); clvDestInput.blur(); }
    });
    clvDestInput?.addEventListener("paste", e => {
      setTimeout(() => { clvDestPicker?.browseDir(clvDestInput.value.trim()); clvDestBrowser.classList.remove("hidden"); }, 0);
    });

    clvDestClear?.addEventListener("click", () => { clvDestInput.value = ""; });

    clvBtn?.addEventListener("click", async () => {
      const target = (document.getElementById("av-target-path")?.value || "").trim();
      if (!target) {
        clvStatus.textContent = "Select a video file first.";
        clvStatus.className   = "fe-extract-status err";
        return;
      }
      clvStatus.textContent = "Dispatching…";
      clvStatus.className   = "fe-extract-status";
      clvBtn.disabled = true;

      const pcutoffVal = document.getElementById("clv-pcutoff").value;
      const destVal    = (clvDestInput?.value || "").trim();
      try {
        const res = await fetch("/dlc/project/create-labeled-video", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            video_path:       target,
            shuffle:          parseInt(document.getElementById("av-shuffle").value) || 1,
            trainingsetindex: parseInt(document.getElementById("av-trainingsetindex").value) ?? 0,
            pcutoff:          pcutoffVal !== "" ? parseFloat(pcutoffVal) : null,
            dotsize:          parseInt(document.getElementById("clv-dotsize").value) || 8,
            colormap:         document.getElementById("clv-colormap").value,
            modelprefix:      (document.getElementById("clv-modelprefix").value || "").trim(),
            filtered:         document.getElementById("clv-filtered").checked,
            draw_skeleton:    document.getElementById("clv-draw-skeleton").checked,
            overwrite:        document.getElementById("clv-overwrite").checked,
            destfolder:       destVal || null,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          clvStatus.textContent = data.error || "Failed to start.";
          clvStatus.className   = "fe-extract-status err";
          clvBtn.disabled = false;
          return;
        }
        _avActiveTask = data.task_id;
        _avStartPolling(data.task_id);
        clvStatus.textContent = "Rendering… see progress below.";
        clvStatus.className   = "fe-extract-status ok";
      } catch (err) {
        clvStatus.textContent = "Network error: " + err.message;
        clvStatus.className   = "fe-extract-status err";
        clvBtn.disabled = false;
      }
    });
