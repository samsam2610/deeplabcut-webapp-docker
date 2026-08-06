"use strict";
import { state } from './state.js';
import { _populateGpuSelect } from './training.js';
import { makeFileBrowser } from './components/file_browser.js';

    const avCard         = document.getElementById("analyze-card");
    const avOpenBtn      = document.getElementById("btn-open-analyze");
    const avCloseBtn     = document.getElementById("btn-close-analyze");
    const avSnapshot     = document.getElementById("av-snapshot");
    const avRefreshSnaps = document.getElementById("av-refresh-snapshots");
    const avProgress     = document.getElementById("av-progress");
    const avTaskId       = document.getElementById("av-task-id");
    const avProgressBar  = document.getElementById("av-progress-bar");
    const avProgressStage= document.getElementById("av-progress-stage");
    const avProgressPct  = document.getElementById("av-progress-pct");
    const avLogOutput    = document.getElementById("av-log-output");


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
        const pinItems = [{ value: latestOpt.value, label: latestOpt.textContent }];

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
          pinItems.push({ value: opt.value, label: opt.textContent });
        });

        _avRenderPinList(pinItems);
        await _avApplyPin(pinItems);
      } catch (err) {
        console.error("avLoadSnapshots:", err);
      }
    }

    avRefreshSnaps.addEventListener("click", _avLoadSnapshots);

    // Reload snapshots when shuffle changes (indices are per-shuffle)
    document.getElementById("av-shuffle").addEventListener("change", _avLoadSnapshots);

    // ── Running state helpers ─────────────────────────────────
    // The only job this card still dispatches is Create Labeled Video, so
    // "busy" means exactly one thing: that button is unavailable. Enablement
    // otherwise follows the queue (see _avSyncClvEnabled).
    function _avSetRunning(running) {
      const btn = document.getElementById("btn-create-labeled-video");
      if (btn) btn.disabled = running;
      if (!running) _avSyncClvEnabled();
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
          avProgressStage.textContent = "✓ Labeled video complete";
          avProgressBar.style.width   = "100%";
          avProgressPct.textContent   = "100 %";
          _avClvStatus("Labeled video finished.", "ok");
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
          _avClvStatus(userStopped ? "Stopped." : "", "");
          _avSetRunning(false);
        }
      } catch (err) {
        console.error("Analyze poll error:", err);
      }
    }

    // ── Queue-driven enablement ───────────────────────────────
    // batch_analyze.js owns the queue and mirrors it onto `state.baQueue`;
    // this module only reads it. Polled rather than evented because the two
    // controllers are deliberately independent — a missed event would leave
    // the button wrong, a missed poll corrects itself a second later.
    function _avSyncClvEnabled() {
      const btn = document.getElementById("btn-create-labeled-video");
      if (!btn) return;
      const n = (state.baQueue || []).length;
      btn.disabled = n === 0;
      btn.title = n
        ? `Render a labeled video for ${(state.baQueue[0] || "").split("/").pop()}`
        : "Queue a video first";
    }
    setInterval(_avSyncClvEnabled, 1000);
    _avSyncClvEnabled();

    function _avClvStatus(msg, kind) {
      const el = document.getElementById("av-clv-status");
      if (!el) return;
      el.textContent = msg || "";
      el.className = "fe-extract-status" + (kind ? " " + kind : "");
    }

    // ── Output folder mode ────────────────────────────────────
    // "Same as target" is the default and simply sends no destfolder.
    function _avSyncOutputMode() {
      const custom = document.getElementById("av-output-custom")?.checked;
      document.getElementById("av-output-custom-row")
        ?.classList.toggle("hidden", !custom);
    }
    Array.from(document.getElementsByName("av-output-mode"))
      .forEach((r) => r.addEventListener("change", _avSyncOutputMode));
    _avSyncOutputMode();

    // ── Snapshot pin list ─────────────────────────────────────
    // Mirrors #ia3d-snapshot-pin-list: checking a row pins that snapshot for
    // the project AND sets the dropdown, because the dropdown's value is what
    // every run actually sends. Only one row may be checked at a time.
    const AV_PIN_KEY = "pinned_snapshot";

    function _avRenderPinList(items) {
      const list = document.getElementById("av-snapshot-pin-list");
      if (!list) return;
      list.innerHTML = "";
      items.forEach((item) => {
        const row = document.createElement("label");
        row.style.cssText = "display:flex;align-items:center;gap:.4rem;padding:.15rem 0;cursor:pointer";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.name = "av-snap-pin";
        cb.value = item.value;
        cb.style.cssText = "accent-color:var(--accent);width:13px;height:13px;flex-shrink:0";
        cb.addEventListener("change", () => _avOnPinToggle(cb));
        const span = document.createElement("span");
        span.textContent = item.label;
        span.style.cssText = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
        row.appendChild(cb);
        row.appendChild(span);
        list.appendChild(row);
      });
    }

    function _avOnPinToggle(changed) {
      const boxes = Array.from(document.getElementsByName("av-snap-pin"));
      if (changed.checked) {
        boxes.forEach((b) => { if (b !== changed) b.checked = false; });
        const sel = document.getElementById("av-snapshot");
        if (sel) sel.value = changed.value;
        _avSavePin(changed.value);
      } else {
        _avSavePin("");
      }
    }

    function _avSavePin(value) {
      fetch("/dlc/project/ui-setting", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: AV_PIN_KEY, value: value || "" }),
      }).catch(() => {});
    }

    // If the pinned snapshot is gone (model deleted, new iteration trained)
    // this deliberately does NOT silently fall back to a different model: it
    // leaves the dropdown at its default, checks nothing, and says so.
    async function _avApplyPin(items) {
      const note = document.getElementById("av-snapshot-pin-note");
      let pinned = "";
      try {
        const d = await (await fetch(`/dlc/project/ui-setting?key=${AV_PIN_KEY}`)).json();
        pinned = (d && d.value) || "";
      } catch (_) { /* leave unpinned */ }
      if (note) note.textContent = "";
      if (!pinned) return;
      const hit = items.find((i) => i.value === pinned);
      if (!hit) {
        if (note) note.textContent = `pinned model is no longer on disk: ${pinned.split("/").pop()}`;
        return;
      }
      const box = Array.from(document.getElementsByName("av-snap-pin"))
        .find((b) => b.value === pinned);
      if (box) box.checked = true;
      const sel = document.getElementById("av-snapshot");
      if (sel) sel.value = pinned;
    }

    // ── Open / Close ──────────────────────────────────────────
    avOpenBtn?.addEventListener("click", async () => {
      avCard.classList.remove("hidden");
      avCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      _avLoadSnapshots();
      _populateGpuSelect("av-gputouse");
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
      // The queue is the single source of files on this card now; the old
      // target-path box is gone. First queued file wins.
      const target = (state.baQueue || [])[0] || "";
      if (!target) {
        clvStatus.textContent = "Queue a video first.";
        clvStatus.className   = "fe-extract-status err";
        return;
      }
      clvStatus.textContent = "Dispatching…";
      clvStatus.className   = "fe-extract-status";
      clvBtn.disabled = true;

      const pcutoffVal = document.getElementById("clv-pcutoff").value;
      // "Same as target" sends no destfolder at all, even if a path is
      // still sitting in the (now hidden) custom box.
      const useCustom  = !!document.getElementById("av-output-custom")?.checked;
      const destVal    = useCustom ? (clvDestInput?.value || "").trim() : "";
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
