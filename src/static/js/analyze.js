"use strict";
import { state } from './state.js';
import { _populateGpuSelect } from './training.js';

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
    let _avHighlightedRow= null;       // currently highlighted browser row element
    let _avHighlightedPath = null;     // path of highlighted row

    let _avPollTimer  = null;
    let _avActiveTask = null;
    let _avBrowserLoaded = false;
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
      if (_avHighlightedPath) {
        _avAddToList(_avHighlightedPath);
      } else {
        // Fall back to the typed text-input value
        const typed = avTargetPath.value.trim();
        if (typed) _avAddToList(typed);
      }
    });

    avBatchClearBtn?.addEventListener("click", () => {
      _avBatchList = [];
      _avHighlightedPath = null;
      _avHighlightedRow = null;
      _avRenderBatchList();
    });

    // ── Project browser ───────────────────────────────────────
    const _AV_VIDEO_EXTS = new Set(['.mp4','.avi','.mov','.mkv','.wmv','.m4v']);
    const _AV_IMAGE_EXTS = new Set(['.jpg','.jpeg','.png','.bmp','.tif','.tiff']);
    function _avSupportedFile(name) {
      const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')).toLowerCase() : '';
      return _AV_VIDEO_EXTS.has(ext) || _AV_IMAGE_EXTS.has(ext);
    }

    function _avSetHighlight(row, path) {
      // Clear previous highlight
      if (_avHighlightedRow && _avHighlightedRow !== row) {
        _avHighlightedRow.style.background = "";
        _avHighlightedRow.style.outline = "";
      }
      _avHighlightedRow  = row;
      _avHighlightedPath = path;
      avTargetPath.value = path;
      row.style.background = "var(--accent-dim, rgba(99,179,237,.18))";
      row.style.outline = "1px solid var(--accent, #63b3ed)";
    }

    function _avMakeEntry(name, fullPath, isDir) {
      const wrapper = document.createElement("div");
      const row = document.createElement("div");
      row.style.cssText = "display:flex;align-items:center;gap:.3rem;padding:.18rem .4rem;cursor:pointer;border-radius:4px;user-select:none";
      row.title = fullPath;

      const arrow = document.createElement("span");
      arrow.style.cssText = "font-size:.62rem;color:var(--text-dim);flex-shrink:0;width:.8rem;text-align:center";
      arrow.textContent = isDir ? "▶" : "";

      const icon = document.createElement("span");
      icon.style.flexShrink = "0";
      icon.textContent = isDir ? "📁" : "📄";

      const label = document.createElement("span");
      label.textContent = name;
      label.style.cssText = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono);font-size:.75rem;flex:1;min-width:0";

      row.appendChild(arrow); row.appendChild(icon); row.appendChild(label);
      wrapper.appendChild(row);

      row.addEventListener("mouseenter", () => {
        if (row !== _avHighlightedRow) row.style.background = "var(--surface-3,#2a2a2a)";
      });
      row.addEventListener("mouseleave", () => {
        if (row !== _avHighlightedRow) row.style.background = "";
      });

      if (isDir) {
        const childContainer = document.createElement("div");
        childContainer.style.cssText = "display:none;padding-left:1rem";
        wrapper.appendChild(childContainer);
        let loaded = false, expanded = false;

        row.addEventListener("click", async (e) => {
          // Single-click: highlight the directory path
          _avSetHighlight(row, fullPath);

          // Also expand/collapse the tree
          if (!expanded && !loaded) {
            childContainer.innerHTML = `<span style="font-size:.72rem;color:var(--text-dim);padding:.15rem .4rem;display:block">Loading…</span>`;
            childContainer.style.display = "block";
            try {
              const res = await fetch(`/fs/ls?path=${encodeURIComponent(fullPath)}`);
              const d   = await res.json();
              childContainer.innerHTML = "";
              if (!d.error) {
                const vis = d.entries.filter(e => (e.type === "dir" && e.has_media !== false) || (e.type === "file" && _avSupportedFile(e.name)));
                vis.forEach(e =>
                  childContainer.appendChild(_avMakeEntry(e.name, fullPath.replace(/\/+$/, "") + "/" + e.name, e.type === "dir"))
                );
                if (!vis.length)
                  childContainer.innerHTML = `<span style="font-size:.72rem;color:var(--text-dim);padding:.15rem .4rem;display:block">(no supported files)</span>`;
              } else {
                childContainer.innerHTML = `<span style="font-size:.72rem;color:var(--text-dim);padding:.15rem .4rem;display:block">${d.error}</span>`;
              }
            } catch (e) {
              childContainer.innerHTML = `<span style="font-size:.72rem;color:var(--text-dim);padding:.15rem .4rem;display:block">Error loading.</span>`;
            }
            loaded = true; expanded = true; arrow.textContent = "▼";
          } else {
            expanded = !expanded;
            childContainer.style.display = expanded ? "block" : "none";
            arrow.textContent = expanded ? "▼" : "▶";
          }
        });
      } else {
        // Files: single-click highlights only
        row.addEventListener("click", () => {
          _avSetHighlight(row, fullPath);
        });
      }

      // Double-click: add to batch list immediately and close browser
      row.addEventListener("dblclick", (e) => {
        e.stopPropagation();
        _avAddToList(fullPath);
        avTargetPath.value = fullPath;
        avBrowser.classList.add("hidden");
        _avBrowserLoaded = false;
      });

      return wrapper;
    }

    async function _avBrowseDir(dirPath) {
      _avBrowserLoaded = false;
      _avProjectPath = dirPath;
      avTargetPath.value = dirPath;
      avBrowser.innerHTML = `<span style="font-size:.8rem;color:var(--text-dim)">Loading…</span>`;
      try {
        const res  = await fetch(`/fs/ls?path=${encodeURIComponent(dirPath)}`);
        const data = await res.json();
        if (data.error) { avBrowser.textContent = data.error; return; }
        avBrowser.innerHTML = "";

        const visible = data.entries.filter(e => (e.type === "dir" && e.has_media !== false) || (e.type === "file" && _avSupportedFile(e.name)));
        if (!visible.length) {
          const empty = document.createElement("span");
          empty.style.cssText = "font-size:.78rem;color:var(--text-dim);padding:.3rem;display:block";
          empty.textContent = "(no supported video or image files)";
          avBrowser.appendChild(empty);
        } else {
          visible.forEach(e =>
            avBrowser.appendChild(_avMakeEntry(e.name, data.path.replace(/\/+$/, "") + "/" + e.name, e.type === "dir"))
          );
        }
        _avBrowserLoaded = true;
      } catch (err) {
        avBrowser.textContent = "Failed to load.";
        console.error("avBrowseDir:", err);
      }
    }

    avBrowseBtn.addEventListener("click", async () => {
      const isHidden = avBrowser.classList.contains("hidden");
      avBrowser.classList.toggle("hidden");
      if (!isHidden) return;  // closing
      const typed = avTargetPath.value.trim();
      if (typed) { await _avBrowseDir(typed); return; }
      if (_avBrowserLoaded) return;  // already showing content
      try {
        const res  = await fetch("/dlc/project/browse");
        const data = await res.json();
        if (data.error) { avBrowser.textContent = data.error; return; }
        await _avBrowseDir(data.project_path);
      } catch (err) {
        avBrowser.textContent = "Failed to load project.";
        console.error("avBrowse:", err);
      }
    });

    avBrowseUp?.addEventListener("click", () => {
      const cur = (avTargetPath.value.trim() || _avProjectPath || "").replace(/\/$/, "");
      if (!cur) return;
      const parent = cur.split("/").slice(0, -1).join("/") || "/";
      if (parent !== cur) { _avBrowseDir(parent); avBrowser.classList.remove("hidden"); }
    });

    avTargetPath?.addEventListener("keydown", e => {
      if (e.key === "Enter")  { e.preventDefault(); _avBrowseDir(avTargetPath.value.trim()); avBrowser.classList.remove("hidden"); }
      if (e.key === "Escape") { avBrowser.classList.add("hidden"); avTargetPath.blur(); }
    });
    avTargetPath?.addEventListener("paste", e => {
      if (avBrowser.classList.contains("hidden")) return;  // only navigate when browser is open
      setTimeout(() => _avBrowseDir(avTargetPath.value.trim()), 0);
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
      _avBrowserLoaded = false;
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

    // destfolder browser — shows directories only, double-click selects
    async function _clvBrowseDir(dirPath) {
      clvDestInput.value = dirPath;
      clvDestBrowser.innerHTML = `<span style="font-size:.8rem;color:var(--text-dim)">Loading…</span>`;
      try {
        const res  = await fetch(`/fs/ls?path=${encodeURIComponent(dirPath)}`);
        const data = await res.json();
        if (data.error) { clvDestBrowser.textContent = data.error; return; }
        clvDestBrowser.innerHTML = "";

        const dirs = data.entries.filter(e => e.type === "dir");
        if (!dirs.length) {
          const em = document.createElement("span");
          em.style.cssText = "font-size:.78rem;color:var(--text-dim);padding:.3rem;display:block";
          em.textContent = "(no subdirectories)";
          clvDestBrowser.appendChild(em);
        }
        dirs.forEach(e => {
          const row = document.createElement("div");
          const fullPath = data.path.replace(/\/+$/, "") + "/" + e.name;
          row.style.cssText = "display:flex;align-items:center;gap:.4rem;padding:.18rem .4rem;cursor:pointer;border-radius:4px;user-select:none;font-size:.78rem";
          row.innerHTML = `<span style="flex-shrink:0">📁</span><span style="font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${e.name}</span>`;
          row.title = fullPath;
          row.addEventListener("mouseenter", () => row.style.background = "var(--surface-3,#2a2a2a)");
          row.addEventListener("mouseleave", () => row.style.background = "");
          row.addEventListener("click",    () => _clvBrowseDir(fullPath));
          row.addEventListener("dblclick", () => {
            clvDestInput.value = fullPath;
            clvDestBrowser.classList.add("hidden");
          });
          clvDestBrowser.appendChild(row);
        });
      } catch (err) {
        clvDestBrowser.textContent = "Failed to load.";
      }
    }

    clvDestBrowse?.addEventListener("click", () => {
      const isHidden = clvDestBrowser.classList.contains("hidden");
      clvDestBrowser.classList.toggle("hidden");
      if (isHidden) {
        const startPath = clvDestInput.value.trim() || _avProjectPath || "/";
        _clvBrowseDir(startPath);
      }
    });

    clvDestUp?.addEventListener("click", () => {
      const cur = clvDestInput.value.trim().replace(/\/$/, "");
      if (!cur) return;
      const parent = cur.split("/").slice(0, -1).join("/") || "/";
      if (parent !== cur) { _clvBrowseDir(parent); clvDestBrowser.classList.remove("hidden"); }
    });

    clvDestInput?.addEventListener("keydown", e => {
      if (e.key === "Enter")  { e.preventDefault(); _clvBrowseDir(clvDestInput.value.trim()); clvDestBrowser.classList.remove("hidden"); }
      if (e.key === "Escape") { clvDestBrowser.classList.add("hidden"); clvDestInput.blur(); }
    });
    clvDestInput?.addEventListener("paste", e => {
      setTimeout(() => { _clvBrowseDir(clvDestInput.value.trim()); clvDestBrowser.classList.remove("hidden"); }, 0);
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
