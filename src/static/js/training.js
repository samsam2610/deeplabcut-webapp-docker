"use strict";
import { state } from './state.js';

    const card       = document.getElementById("dlc-project-config-card");
    const openBtn    = document.getElementById("btn-open-dlc-config-editor");
    const closeBtn   = document.getElementById("btn-close-dlc-config-editor");
    const pathLabel  = document.getElementById("dlc-project-config-path");
    const editor     = document.getElementById("dlc-config-editor");
    const saveBtn    = document.getElementById("save-dlc-config-btn");
    const saveStatus = document.getElementById("dlc-config-save-status");

    async function _load() {
      try {
        const res  = await fetch("/dlc/project/config");
        const data = await res.json();
        if (!res.ok) {
          pathLabel.textContent  = data.error || "Failed to load config.yaml";
          editor.value           = "";
          return;
        }
        pathLabel.textContent = data.config_path || "";
        editor.value          = data.content     || "";
        saveStatus.textContent = "";
        saveStatus.className   = "config-save-status";
      } catch (err) {
        pathLabel.textContent = `Error: ${err.message}`;
      }
    }

    if (openBtn) {
      openBtn.addEventListener("click", () => {
        card.classList.remove("hidden");
        card.scrollIntoView({ behavior: "smooth", block: "nearest" });
        _load();
      });
    }

    closeBtn.addEventListener("click", () => card.classList.add("hidden"));

    saveBtn.addEventListener("click", async () => {
      const content = editor.value;
      if (!content.trim()) return;
      saveBtn.disabled       = true;
      saveStatus.textContent = "Saving…";
      saveStatus.className   = "config-save-status";
      try {
        const res  = await fetch("/dlc/project/config", {
          method:  "PATCH",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ content }),
        });
        const data = await res.json();
        if (res.ok) {
          saveStatus.textContent = "Saved ✓";
          saveStatus.className   = "config-save-status ok";
        } else {
          saveStatus.textContent = data.error || "Save failed";
          saveStatus.className   = "config-save-status err";
        }
      } catch (err) {
        saveStatus.textContent = `Network error: ${err.message}`;
        saveStatus.className   = "config-save-status err";
      }
      saveBtn.disabled = false;
      setTimeout(() => {
        saveStatus.textContent = "";
        saveStatus.className   = "config-save-status";
      }, 3000);
    });

  // ── Create Training Dataset ──────────────────────────────────
    const ctdCard          = document.getElementById("create-training-dataset-card");
    const ctdOpenBtn       = document.getElementById("btn-open-create-training-dataset");
    const ctdCloseBtn      = document.getElementById("btn-close-create-training-dataset");
    const ctdNumShuffles   = document.getElementById("ctd-num-shuffles");
    const ctdFreezeSplit   = document.getElementById("ctd-freeze-split");
    const ctdRunBtn        = document.getElementById("btn-run-create-training-dataset");
    const ctdRunStatus     = document.getElementById("ctd-run-status");
    const ctdProgress      = document.getElementById("ctd-progress");
    const ctdTaskId        = document.getElementById("ctd-task-id");
    const ctdProgressBar   = document.getElementById("ctd-progress-bar");
    const ctdProgressStage = document.getElementById("ctd-progress-stage");
    const ctdProgressPct   = document.getElementById("ctd-progress-pct");
    const ctdLogOutput     = document.getElementById("ctd-log-output");
    const ctdPytorchSec    = document.getElementById("ctd-pytorch-section");
    const ctdPytorchSelect = document.getElementById("ctd-pytorch-config-select");
    const ctdRefreshBtn    = document.getElementById("ctd-refresh-pytorch-btn");
    const ctdPytorchPath   = document.getElementById("ctd-pytorch-path");
    const ctdPytorchEditor = document.getElementById("ctd-pytorch-editor");
    const ctdSaveBtn            = document.getElementById("ctd-save-pytorch-btn");
    const ctdSaveStatus         = document.getElementById("ctd-save-status");
    const ctdAddDatasetsBtn     = document.getElementById("btn-ctd-add-datasets");
    const ctdAddDatasetsStatus  = document.getElementById("ctd-add-datasets-status");

    let _ctdPollTimer  = null;
    let _ctdRelPath    = null;

    // ── Open / close ────────────────────────────────────────────
    if (ctdOpenBtn) {
      ctdOpenBtn.addEventListener("click", () => {
        ctdCard.classList.remove("hidden");
        ctdCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
        _ctdLoadPytorchConfigs();
      });
    }
    ctdCloseBtn.addEventListener("click", () => {
      ctdCard.classList.add("hidden");
      if (_ctdPollTimer) { clearInterval(_ctdPollTimer); _ctdPollTimer = null; }
    });

    // ── Run create_training_dataset ──────────────────────────────
    ctdRunBtn.addEventListener("click", async () => {
      const numShuffles = parseInt(ctdNumShuffles.value, 10) || 1;
      const freezeSplit = ctdFreezeSplit ? ctdFreezeSplit.checked : true;
      ctdRunBtn.disabled    = true;
      ctdRunStatus.textContent = "";
      ctdRunStatus.className   = "fe-extract-status";

      try {
        const res  = await fetch("/dlc/project/create-training-dataset", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ num_shuffles: numShuffles, freeze_split: freezeSplit }),
        });
        const data = await res.json();
        if (!res.ok) {
          ctdRunStatus.textContent = data.error || "Error dispatching task.";
          ctdRunStatus.className   = "fe-extract-status err";
          ctdRunBtn.disabled = false;
          return;
        }
        _ctdStartPolling(data.task_id);
      } catch (err) {
        ctdRunStatus.textContent = `Network error: ${err.message}`;
        ctdRunStatus.className   = "fe-extract-status err";
        ctdRunBtn.disabled = false;
      }
    });

    // ── Poll task status ─────────────────────────────────────────
    function _ctdStartPolling(taskId) {
      ctdProgress.classList.remove("hidden");
      ctdProgress.classList.remove("state-success", "state-fail");
      ctdTaskId.textContent      = taskId.slice(0, 12) + "…";
      ctdProgressBar.style.width = "0%";
      ctdProgressPct.textContent = "0 %";
      ctdProgressStage.textContent = "Queued";
      ctdLogOutput.textContent   = "Waiting for output…";

      if (_ctdPollTimer) clearInterval(_ctdPollTimer);
      _ctdPollTimer = setInterval(() => _ctdPoll(taskId), 2000);
      _ctdPoll(taskId);
    }

    async function _ctdPoll(taskId) {
      try {
        const res  = await fetch(`/status/${taskId}`);
        const data = await res.json();

        const pct = Math.min(data.progress || 0, 100);
        ctdProgressBar.style.width   = pct + "%";
        ctdProgressPct.textContent   = pct + " %";
        ctdProgressStage.textContent = data.stage || data.state;

        if (data.log) {
          ctdLogOutput.textContent = data.log;
          ctdLogOutput.scrollTop   = ctdLogOutput.scrollHeight;
        }

        if (data.state === "SUCCESS") {
          clearInterval(_ctdPollTimer); _ctdPollTimer = null;
          ctdProgress.classList.add("state-success");
          ctdProgressStage.textContent = "✓ Complete";
          ctdProgressBar.style.width   = "100%";
          ctdProgressPct.textContent   = "100 %";
          if (data.result && data.result.log) ctdLogOutput.textContent = data.result.log;
          ctdRunBtn.disabled = false;
          ctdRunStatus.textContent = "Training dataset created.";
          ctdRunStatus.className   = "fe-extract-status ok";
          await _ctdLoadPytorchConfigs();
        }

        if (data.state === "FAILURE") {
          clearInterval(_ctdPollTimer); _ctdPollTimer = null;
          ctdProgress.classList.add("state-fail");
          ctdProgressStage.textContent = "✗ " + (data.error || "Failed");
          ctdLogOutput.textContent     = data.error || "An unknown error occurred.";
          ctdRunBtn.disabled = false;
        }
      } catch (err) {
        console.error("CTD poll error:", err);
      }
    }

    // ── Load pytorch_config.yaml list ───────────────────────────
    async function _ctdLoadPytorchConfigs() {
      try {
        const res  = await fetch("/dlc/project/pytorch-configs");
        const data = await res.json();
        if (data.error || !data.configs || !data.configs.length) {
          ctdPytorchSec.classList.add("hidden");
          return;
        }
        const configs = data.configs;
        const prev    = ctdPytorchSelect.value;

        ctdPytorchSelect.innerHTML = "";
        configs.forEach(c => {
          const opt     = document.createElement("option");
          opt.value       = c.rel_path;
          opt.textContent = c.rel_path;
          ctdPytorchSelect.appendChild(opt);
        });

        // Restore previous selection or use first
        if (prev && configs.find(c => c.rel_path === prev)) {
          ctdPytorchSelect.value = prev;
        } else {
          ctdPytorchSelect.value = configs[0].rel_path;
        }

        ctdPytorchSec.classList.remove("hidden");
        await _ctdLoadSelectedConfig();
      } catch (err) {
        console.error("CTD pytorch configs:", err);
      }
    }

    ctdRefreshBtn.addEventListener("click", () => _ctdLoadPytorchConfigs());

    ctdPytorchSelect.addEventListener("change", () => _ctdLoadSelectedConfig());

    async function _ctdLoadSelectedConfig() {
      const relPath = ctdPytorchSelect.value;
      if (!relPath) return;
      _ctdRelPath = relPath;
      try {
        const res  = await fetch(`/dlc/project/pytorch-config?rel_path=${encodeURIComponent(relPath)}`);
        const data = await res.json();
        if (data.error) {
          ctdPytorchPath.textContent = data.error;
          return;
        }
        ctdPytorchPath.textContent  = data.config_path || "";
        ctdPytorchEditor.value      = data.content || "";
        ctdSaveStatus.textContent   = "";
        ctdSaveStatus.className     = "config-save-status";
      } catch (err) {
        ctdPytorchPath.textContent = `Error: ${err.message}`;
      }
    }

    // ── Sync datasets & video list ───────────────────────────────
    ctdAddDatasetsBtn.addEventListener("click", async () => {
      if (!confirm("Run adddatasetstovideolistandviceversa?\n\nThis will update the project config.yaml to sync all labeled datasets and videos. Continue?")) return;
      ctdAddDatasetsBtn.disabled       = true;
      ctdAddDatasetsStatus.textContent = "Running…";
      ctdAddDatasetsStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch("/dlc/project/add-datasets-to-video-list", { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
          ctdAddDatasetsStatus.textContent = data.error || "Error.";
          ctdAddDatasetsStatus.className   = "fe-extract-status err";
          ctdAddDatasetsBtn.disabled = false;
          return;
        }
        // Poll until the task completes (max 60 s — guards against worker-killed tasks stuck at STARTED)
        const taskId = data.task_id;
        let _pollCount = 0;
        const timer = setInterval(async () => {
          _pollCount++;
          if (_pollCount > 40) {
            clearInterval(timer);
            ctdAddDatasetsStatus.textContent = "Timed out — worker may have restarted. Retry.";
            ctdAddDatasetsStatus.className   = "fe-extract-status err";
            ctdAddDatasetsBtn.disabled = false;
            return;
          }
          try {
            const sr   = await fetch(`/status/${taskId}`);
            const sd   = await sr.json();
            if (sd.state === "SUCCESS") {
              clearInterval(timer);
              ctdAddDatasetsStatus.textContent = "Done ✓";
              ctdAddDatasetsStatus.className   = "fe-extract-status ok";
              ctdAddDatasetsBtn.disabled = false;
            } else if (sd.state === "FAILURE") {
              clearInterval(timer);
              ctdAddDatasetsStatus.textContent = sd.error || "Failed.";
              ctdAddDatasetsStatus.className   = "fe-extract-status err";
              ctdAddDatasetsBtn.disabled = false;
            }
          } catch { clearInterval(timer); ctdAddDatasetsBtn.disabled = false; }
        }, 1500);
      } catch (err) {
        ctdAddDatasetsStatus.textContent = `Network error: ${err.message}`;
        ctdAddDatasetsStatus.className   = "fe-extract-status err";
        ctdAddDatasetsBtn.disabled = false;
      }
    });

    // ── Save pytorch_config.yaml ─────────────────────────────────
    ctdSaveBtn.addEventListener("click", async () => {
      const content = ctdPytorchEditor.value;
      if (!content.trim()) return;
      ctdSaveBtn.disabled      = true;
      ctdSaveStatus.textContent = "Saving…";
      ctdSaveStatus.className   = "config-save-status";
      try {
        const res  = await fetch("/dlc/project/pytorch-config", {
          method:  "PATCH",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ content, rel_path: _ctdRelPath }),
        });
        const data = await res.json();
        if (res.ok) {
          ctdSaveStatus.textContent = "Saved ✓";
          ctdSaveStatus.className   = "config-save-status ok";
        } else {
          ctdSaveStatus.textContent = data.error || "Error saving.";
          ctdSaveStatus.className   = "config-save-status err";
        }
      } catch (err) {
        ctdSaveStatus.textContent = `Network error: ${err.message}`;
        ctdSaveStatus.className   = "config-save-status err";
      }
      ctdSaveBtn.disabled = false;
      setTimeout(() => {
        ctdSaveStatus.textContent = "";
        ctdSaveStatus.className   = "config-save-status";
      }, 4000);
    });

  // ── Train Network ────────────────────────────────────────────
    const tnCard          = document.getElementById("train-network-card");
    const tnOpenBtn       = document.getElementById("btn-open-train-network");
    const tnCloseBtn      = document.getElementById("btn-close-train-network");
    const tnEngineBadge   = document.getElementById("tn-engine-badge");
    const tnTfParams      = document.getElementById("tn-tf-params");
    const tnPtParams      = document.getElementById("tn-pt-params");
    const tnRunBtn        = document.getElementById("btn-run-train-network");
    const tnStopBtn       = document.getElementById("btn-stop-train-network");
    const tnRunStatus     = document.getElementById("tn-run-status");
    const tnProgress      = document.getElementById("tn-progress");
    const tnTaskId        = document.getElementById("tn-task-id");
    const tnProgressBar   = document.getElementById("tn-progress-bar");
    const tnProgressStage = document.getElementById("tn-progress-stage");
    const tnProgressPct   = document.getElementById("tn-progress-pct");
    const tnLogOutput     = document.getElementById("tn-log-output");

    let _tnPollTimer  = null;
    let _tnActiveTask = null;
    let _tnEngine     = "pytorch";

    // ── Engine display (reads module-level state.dlcEngine set when project loads) ──
    function _tnDetectEngine() {
      _tnEngine = state.dlcEngine;
      tnEngineBadge.textContent = _tnEngine;
      tnEngineBadge.style.color = _tnEngine === "pytorch" ? "var(--accent)" : "var(--text)";
      _tnShowEngineParams(_tnEngine);
    }

    function _tnShowEngineParams(engine) {
      if (engine === "tensorflow") {
        tnTfParams.classList.remove("hidden");
        tnPtParams.classList.add("hidden");
      } else {
        tnPtParams.classList.remove("hidden");
        tnTfParams.classList.add("hidden");
      }
    }

    // ── Open / close ─────────────────────────────────────────────
    if (tnOpenBtn) {
      tnOpenBtn.addEventListener("click", async () => {
        tnCard.classList.remove("hidden");
        tnCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
        _tnDetectEngine(); // sync — reads state.dlcEngine set when project was loaded
        _populateGpuSelect("tn-gputouse");
        // Check if a training job is already running; if so, reconnect to it
        try {
          const res  = await fetch("/dlc/training/jobs");
          const data = await res.json();
          const activeJob = (data.jobs || []).find(
            j => (j.status === "running" || j.status === "dead") && j.operation !== "analyze"
          );
          state.dlcTrainingActive = !!activeJob;
          if (activeJob && !tnRunBtn._tnPolling) {
            _tnActiveTask = activeJob.task_id;
            _tnStartPolling(activeJob.task_id);
          }
        } catch (_) {}
      });
    }
    if (tnCloseBtn) {
      tnCloseBtn.addEventListener("click", () => {
        tnCard.classList.add("hidden");
        if (_tnPollTimer) { clearInterval(_tnPollTimer); _tnPollTimer = null; }
      });
    }

    // ── Run train_network ────────────────────────────────────────
    tnRunBtn.addEventListener("click", async () => {
      if (state.dlcTrainingActive) {
        tnRunStatus.textContent = "A training job is already running. Stop it first.";
        tnRunStatus.className   = "fe-extract-status err";
        return;
      }
      tnRunBtn.disabled        = true;
      tnRunStatus.textContent  = "";
      tnRunStatus.className    = "fe-extract-status";

      const intVal = (id) => {
        const v = document.getElementById(id)?.value;
        return (v && v.trim() !== "") ? parseInt(v, 10) : null;
      };
      const strVal = (id) => {
        const v = document.getElementById(id)?.value;
        return (v && v.trim() !== "") ? v.trim() : null;
      };

      const body = {
        engine:           _tnEngine,
        shuffle:          intVal("tn-shuffle") || 1,
        trainingsetindex: intVal("tn-trainingsetindex") ?? 0,
        gputouse:         intVal("tn-gputouse"),
      };

      if (_tnEngine === "tensorflow") {
        Object.assign(body, {
          maxiters:    intVal("tn-maxiters"),
          displayiters: intVal("tn-displayiters"),
          saveiters:   intVal("tn-saveiters"),
        });
      } else {
        Object.assign(body, {
          epochs:              intVal("tn-epochs"),
          save_epochs:         intVal("tn-save-epochs"),
          batch_size:          intVal("tn-batch-size"),
          device:              strVal("tn-device"),
          detector_epochs:     intVal("tn-detector-epochs"),
          detector_batch_size: intVal("tn-detector-batch-size"),
        });
      }

      try {
        const res  = await fetch("/dlc/project/train-network", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) {
          tnRunStatus.textContent = data.error || "Error dispatching task.";
          tnRunStatus.className   = "fe-extract-status err";
          tnRunBtn.disabled = false;
          return;
        }
        _tnActiveTask = data.task_id;
        _tnStartPolling(data.task_id);
      } catch (err) {
        tnRunStatus.textContent = `Network error: ${err.message}`;
        tnRunStatus.className   = "fe-extract-status err";
        tnRunBtn.disabled = false;
      }
    });

    // ── Stop training ─────────────────────────────────────────────
    tnStopBtn.addEventListener("click", async () => {
      if (!_tnActiveTask) return;
      tnStopBtn.disabled = true;
      try {
        await fetch("/dlc/project/train-network/stop", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ task_id: _tnActiveTask }),
        });
        tnRunStatus.textContent = "Stop signal sent — training will terminate shortly.";
        tnRunStatus.className   = "fe-extract-status";
      } catch (err) {
        tnRunStatus.textContent = `Stop error: ${err.message}`;
        tnRunStatus.className   = "fe-extract-status err";
        tnStopBtn.disabled = false;
      }
    });

    // ── Poll task status ─────────────────────────────────────────
    function _tnSetRunning(running) {
      tnRunBtn.disabled      = running;
      tnRunBtn._tnPolling    = running; // flag read by GPU monitor to avoid double-disabling
      state.dlcTrainingActive     = running; // keep module-level flag in sync
      if (running) {
        tnStopBtn.classList.remove("hidden");
        tnStopBtn.disabled = false;
      } else {
        tnStopBtn.classList.add("hidden");
        _tnActiveTask = null;
      }
    }

    function _tnStartPolling(taskId) {
      tnProgress.classList.remove("hidden");
      tnProgress.classList.remove("state-success", "state-fail");
      tnTaskId.textContent        = taskId.slice(0, 12) + "…";
      tnProgressBar.style.width   = "0%";
      tnProgressPct.textContent   = "0 %";
      tnProgressStage.textContent = "Queued";
      tnLogOutput.textContent     = "Waiting for output…";
      _tnSetRunning(true);

      if (_tnPollTimer) clearInterval(_tnPollTimer);
      _tnPollTimer = setInterval(() => _tnPoll(taskId), 2000);
      _tnPoll(taskId);
    }

    async function _tnPoll(taskId) {
      try {
        const res  = await fetch(`/status/${taskId}`);
        const data = await res.json();

        const pct = Math.min(data.progress || 0, 100);
        tnProgressBar.style.width    = pct + "%";
        tnProgressPct.textContent    = pct + " %";
        tnProgressStage.textContent  = data.stage || data.state;

        if (data.log) {
          tnLogOutput.textContent = data.log;
          tnLogOutput.scrollTop   = tnLogOutput.scrollHeight;
        }

        if (data.state === "SUCCESS") {
          clearInterval(_tnPollTimer); _tnPollTimer = null;
          tnProgress.classList.add("state-success");
          tnProgressStage.textContent = "✓ Training complete";
          tnProgressBar.style.width   = "100%";
          tnProgressPct.textContent   = "100 %";
          if (data.result && data.result.log) tnLogOutput.textContent = data.result.log;
          _tnSetRunning(false);
          tnRunStatus.textContent = "Training finished successfully.";
          tnRunStatus.className   = "fe-extract-status ok";
        }

        if (data.state === "FAILURE" || data.state === "REVOKED") {
          clearInterval(_tnPollTimer); _tnPollTimer = null;
          const userStopped = data.state === "REVOKED" ||
            (data.error || "").includes("__USER_STOPPED__");
          tnProgress.classList.add("state-fail");
          tnProgressStage.textContent = userStopped
            ? "✗ Stopped by user"
            : "✗ " + (data.error || "Failed").split("\n")[0];
          if (!userStopped) tnLogOutput.textContent = data.error || "An unknown error occurred.";
          tnRunStatus.textContent = userStopped ? "Training stopped." : "";
          tnRunStatus.className   = "fe-extract-status";
          _tnSetRunning(false);
        }
      } catch (err) {
        console.error("Train network poll error:", err);
      }
    }

  // ── Shared GPU select helper ──────────────────────────────────
  export async function _populateGpuSelect(selectId) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    const prevVal = sel.value;
    // Keep only the "auto" placeholder while loading
    sel.innerHTML = '<option value="">auto (detecting…)</option>';
    try {
      const res  = await fetch("/dlc/gpu/status");
      const data = await res.json();
      sel.innerHTML = '<option value="">auto</option>';
      if (data.available && data.gpus && data.gpus.length > 0) {
        data.gpus.forEach(g => {
          const opt  = document.createElement("option");
          opt.value  = String(g.index);
          const util = `${g.utilization}% util`;
          const mem  = `${Math.round(g.memory_used / 1024)}/${Math.round(g.memory_total / 1024)} GB`;
          opt.textContent = `GPU ${g.index} — ${g.name}  (${util} · ${mem})`;
          sel.appendChild(opt);
        });
        // Restore previously selected value if still present
        if (prevVal && sel.querySelector(`option[value="${prevVal}"]`)) {
          sel.value = prevVal;
        }
      } else {
        sel.innerHTML = '<option value="">auto (no GPU detected)</option>';
      }
    } catch (e) {
      sel.innerHTML = '<option value="">auto</option>';
      console.error("_populateGpuSelect:", e);
    }
  }

  // ── Analyze Video / Frames ────────────────────────────────────
