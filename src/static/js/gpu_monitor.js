"use strict";
import { state } from './state.js';

    const gmCard          = document.getElementById("gpu-monitor-card");
    const gmOpenBtn       = document.getElementById("btn-open-gpu-monitor");
    const gmCloseBtn      = document.getElementById("btn-close-gpu-monitor");
    const gmRefreshBtn    = document.getElementById("gm-refresh-btn");
    const gmClearBtn      = document.getElementById("gm-clear-btn");
    const gmGpuList       = document.getElementById("gm-gpu-list");
    const gmGpuAge        = document.getElementById("gm-gpu-age");
    const gmJobsList      = document.getElementById("gm-jobs-list");
    const gmQueueList     = document.getElementById("gm-queue-list");
    const gmCancelAllBtn  = document.getElementById("gm-cancel-all-btn");
    const gmBadge         = document.getElementById("gpu-monitor-badge");

    let _gmPollTimer = null;

    // ── Open / close ─────────────────────────────────────────
    gmOpenBtn?.addEventListener("click", () => {
      gmCard.classList.remove("hidden");
      gmCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      _gmRefresh();
      if (!_gmPollTimer) _gmPollTimer = setInterval(_gmRefresh, 5000);
    });
    gmCloseBtn?.addEventListener("click", () => {
      gmCard.classList.add("hidden");
      clearInterval(_gmPollTimer); _gmPollTimer = null;
    });
    gmRefreshBtn?.addEventListener("click", _gmRefresh);

    gmClearBtn?.addEventListener("click", async () => {
      gmClearBtn.disabled = true;
      try {
        await fetch("/dlc/training/jobs/clear", { method: "POST" });
        await _gmRefresh();
      } catch (e) { console.error(e); }
      gmClearBtn.disabled = false;
    });

    gmCancelAllBtn?.addEventListener("click", async () => {
      if (!confirm("Cancel all queued tasks? They will not run.")) return;
      gmCancelAllBtn.disabled = true;
      try {
        await fetch("/dlc/training/queue/cancel-all", { method: "POST" });
        await _gmRefresh();
      } catch (e) { console.error(e); }
      gmCancelAllBtn.disabled = false;
    });

    // ── Render queued tasks ───────────────────────────────────
    function _gmRenderQueue(data, runningIds = new Set()) {
      let tasks = data.tasks || [];
      const INTERNAL_TASKS = new Set(["tasks.dlc_probe_gpu_stats"]);
      tasks = tasks.filter(t => !INTERNAL_TASKS.has(t.task_name) && !runningIds.has(t.task_id));
      if (tasks.length === 0) {
        gmQueueList.innerHTML = '<span style="font-size:.82rem;color:var(--text-dim)">No queued tasks.</span>';
        gmCancelAllBtn.classList.add("hidden");
        return;
      }
      gmCancelAllBtn.classList.remove("hidden");

      const taskLabel = {
        "tasks.dlc_train_network":           "Train",
        "tasks.dlc_create_training_dataset": "Create Dataset",
        "tasks.dlc_analyze":                 "Analyze",
        "tasks.dlc_machine_label_frames":    "Machine Label",
        "tasks.dlc_tapnet_propagate":        "TAPNet",
      };
      const queueColor = { pytorch: "#a78bfa", tensorflow: "#f59e0b", celery: "var(--text-dim)" };

      gmQueueList.innerHTML = tasks.map(t => {
        const label    = taskLabel[t.task_name] || (t.task_name || "task").split(".").pop();
        const project  = t.config_path ? t.config_path.split("/").slice(-2, -1)[0] : "—";
        const qClr     = queueColor[t.queue] || "var(--text-dim)";
        return `
        <div style="display:flex;align-items:center;gap:.55rem;padding:.45rem .65rem;background:var(--surface-2);border:1px solid var(--border);border-radius:6px">
          <span style="font-size:.68rem;font-weight:600;color:#f0a030;flex-shrink:0;text-transform:uppercase;letter-spacing:.04em">QUEUED</span>
          <span style="font-size:.68rem;font-weight:600;color:var(--text-dim);flex-shrink:0;text-transform:uppercase;letter-spacing:.04em">${label}</span>
          <span style="font-family:var(--mono);font-size:.72rem;color:var(--text-dim);flex-shrink:0">${(t.task_id||"").slice(0,8)}…</span>
          <span style="font-size:.78rem;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${project}</span>
          <span style="font-size:.7rem;color:${qClr};flex-shrink:0">${t.queue}</span>
          <button class="btn-sm btn-danger gm-cancel-btn" data-task-id="${t.task_id}" style="padding:.15rem .45rem;font-size:.72rem;flex-shrink:0" title="Cancel this task">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>`;
      }).join("");

      gmQueueList.querySelectorAll(".gm-cancel-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
          btn.disabled = true;
          try {
            await fetch("/dlc/training/queue/cancel", {
              method:  "POST",
              headers: { "Content-Type": "application/json" },
              body:    JSON.stringify({ task_id: btn.dataset.taskId }),
            });
            await _gmRefresh();
          } catch (e) { console.error(e); btn.disabled = false; }
        });
      });
    }

    // ── Render GPU bars ───────────────────────────────────────
    function _gmRenderGpus(data) {
      if (!data.available || !data.gpus || data.gpus.length === 0) {
        gmGpuList.innerHTML = '<span style="font-size:.82rem;color:var(--text-dim)">No GPU data — data updates while training runs.</span>';
        gmGpuAge.textContent = "";
        return;
      }
      gmGpuAge.textContent = data.age_s != null ? `updated ${data.age_s}s ago` : "";
      gmGpuList.innerHTML = data.gpus.map(g => {
        const vramPct  = Math.round(g.memory_used / g.memory_total * 100);
        const utilColor = g.utilization > 80 ? "var(--accent)" : g.utilization > 40 ? "#f0a030" : "var(--text-dim)";
        return `
        <div style="background:var(--surface-2);border:1px solid var(--border);border-radius:7px;padding:.65rem .8rem">
          <div style="display:flex;justify-content:space-between;margin-bottom:.45rem">
            <span style="font-size:.8rem;font-weight:600">GPU ${g.index} &mdash; ${g.name}</span>
            <span style="font-size:.75rem;color:var(--text-dim)">${g.temperature}°C</span>
          </div>
          <div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.3rem">
            <span style="font-size:.72rem;width:3.5rem;color:var(--text-dim)">GPU</span>
            <div class="progress-track" style="flex:1;height:7px"><div class="progress-fill" style="width:${g.utilization}%;background:${utilColor}"></div></div>
            <span style="font-size:.72rem;width:2.5rem;text-align:right;color:${utilColor};font-weight:600">${g.utilization}%</span>
          </div>
          <div style="display:flex;align-items:center;gap:.5rem">
            <span style="font-size:.72rem;width:3.5rem;color:var(--text-dim)">VRAM</span>
            <div class="progress-track" style="flex:1;height:7px"><div class="progress-fill" style="width:${vramPct}%"></div></div>
            <span style="font-size:.72rem;width:4.5rem;text-align:right;color:var(--text-dim)">${Math.round(g.memory_used/1024)}/${Math.round(g.memory_total/1024)} GB</span>
          </div>
        </div>`;
      }).join("");

      // Update badge on the toolbar button
      const busy = data.gpus.some(g => g.utilization > 5);
      gmBadge.style.display = "";
      gmBadge.textContent   = busy ? `${data.gpus[0].utilization}%` : "idle";
      gmBadge.style.background = busy ? "color-mix(in srgb, var(--accent) 20%, transparent)" : "";
      gmBadge.style.color      = busy ? "var(--accent)" : "";
      gmBadge.style.borderColor = busy ? "var(--accent)" : "";
    }

    // ── Render jobs list ──────────────────────────────────────
    function _gmRenderJobs(data) {
      // Update the global training-active flag and the run button
      state.dlcTrainingActive = (data.jobs || []).some(j => (j.status === "running" || j.status === "dead") && j.operation !== "analyze");
      const tnRunBtn = document.getElementById("btn-run-train-network");
      if (tnRunBtn && !tnRunBtn._tnPolling) {
        tnRunBtn.disabled = state.dlcTrainingActive;
        tnRunBtn.title    = state.dlcTrainingActive ? "A training job is already running" : "";
      }

      if (!data.jobs || data.jobs.length === 0) {
        gmJobsList.innerHTML = '<span style="font-size:.82rem;color:var(--text-dim)">No jobs found.</span>';
        return;
      }
      const statusColor = { running: "var(--accent)", complete: "#4caf50", stopped: "var(--text-dim)", failed: "#e05252" };
      const statusIcon  = { running: "▶", complete: "✓", stopped: "■", failed: "✗" };
      const opColor     = { train: "var(--text-dim)", analyze: "#a78bfa" };
      const opLabel     = { train: "train", analyze: "analyze" };

      gmJobsList.innerHTML = data.jobs.map(j => {
        const dotColor  = statusColor[j.status]    || "var(--text-dim)";
        const icon      = statusIcon[j.status]     || "?";
        const op        = j.operation || "train";
        const opBadge   = opLabel[op] || op;
        const opClr     = opColor[op] || "var(--text-dim)";
        const ago       = j.started_at ? _gmTimeAgo(parseFloat(j.started_at)) : "";
        const canStop   = j.status === "running" || j.status === "dead";
        // For analyze jobs show target file/folder name as subtitle
        const subtitle  = op === "analyze" && j.target_path
          ? j.target_path.split("/").pop()
          : (j.engine || "");
        const titleAttr = op === "analyze"
          ? (j.target_path || j.config_path || "")
          : (j.config_path || "");
        return `
        <div style="display:flex;align-items:center;gap:.55rem;padding:.45rem .65rem;background:var(--surface-2);border:1px solid var(--border);border-radius:6px">
          <span style="font-size:.8rem;color:${dotColor};flex-shrink:0" title="${j.status}">${icon}</span>
          <span style="font-size:.68rem;font-weight:600;color:${opClr};flex-shrink:0;text-transform:uppercase;letter-spacing:.04em">${opBadge}</span>
          <span style="font-family:var(--mono);font-size:.72rem;color:var(--text-dim);flex-shrink:0">${(j.task_id||"").slice(0,8)}…</span>
          <span style="font-size:.78rem;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${titleAttr}">${j.project||"?"}</span>
          <span style="font-size:.72rem;color:var(--text-dim);flex-shrink:0;overflow:hidden;text-overflow:ellipsis;max-width:8rem" title="${subtitle}">${subtitle}</span>
          <span style="font-size:.72rem;color:var(--text-dim);flex-shrink:0">${ago}</span>
          ${canStop ? `<button class="btn-sm btn-danger gm-stop-btn" data-task-id="${j.task_id}" data-operation="${op}" style="padding:.15rem .45rem;font-size:.72rem;flex-shrink:0" title="Force stop">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/></svg>
          </button>` : ""}
        </div>`;
      }).join("");

      // Wire up stop buttons — route to correct endpoint based on operation
      gmJobsList.querySelectorAll(".gm-stop-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
          const tid = btn.dataset.taskId;
          const op  = btn.dataset.operation;
          btn.disabled = true;
          const endpoint = op === "analyze"
            ? "/dlc/project/analyze/stop"
            : "/dlc/project/train-network/stop";
          try {
            await fetch(endpoint, {
              method:  "POST",
              headers: { "Content-Type": "application/json" },
              body:    JSON.stringify({ task_id: tid }),
            });
          } catch (e) { console.error(e); }
          setTimeout(_gmRefresh, 1500);
        });
      });
    }

    function _gmTimeAgo(ts) {
      const s = Math.round(Date.now() / 1000 - ts);
      if (s < 60)  return `${s}s ago`;
      if (s < 3600) return `${Math.round(s/60)}m ago`;
      return `${Math.round(s/3600)}h ago`;
    }

    async function _gmRefresh() {
      try {
        const [gpuRes, jobsRes, queueRes] = await Promise.all([
          fetch("/dlc/gpu/status"),
          fetch("/dlc/training/jobs"),
          fetch("/dlc/training/queue"),
        ]);
        _gmRenderGpus(await gpuRes.json());
        const jobsData  = await jobsRes.json();
        const queueData = await queueRes.json();
        _gmRenderJobs(jobsData);
        // Pass running task IDs so the queue renderer can hide duplicates
        const runningIds = new Set((jobsData.jobs || []).map(j => j.task_id));
        _gmRenderQueue(queueData, runningIds);
      } catch (e) {
        console.error("GPU monitor refresh error:", e);
      }
    }

    // Keep badge updated even when the card is closed
    setInterval(async () => {
      try {
        const res  = await fetch("/dlc/gpu/status");
        const data = await res.json();
        if (!data.available) { gmBadge.style.display = "none"; return; }
        const busy = data.gpus.some(g => g.utilization > 5);
        gmBadge.style.display    = "";
        gmBadge.textContent      = busy ? `${data.gpus[0].utilization}%` : "idle";
        gmBadge.style.background = busy ? "color-mix(in srgb, var(--accent) 20%, transparent)" : "";
        gmBadge.style.color      = busy ? "var(--accent)" : "";
        gmBadge.style.borderColor = busy ? "var(--accent)" : "";
      } catch (_) {}
    }, 6000);
