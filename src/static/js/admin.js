"use strict";
import { _populateGpuSelect } from './training.js';

const btnFlushCache    = document.getElementById("btn-flush-cache");
const btnHardReset     = document.getElementById("btn-hard-reset");
const flushCacheStatus = document.getElementById("flush-cache-status");

function _setStatus(msg, cls = "", ttl = 8000) {
  if (!flushCacheStatus) return;
  flushCacheStatus.textContent = msg;
  flushCacheStatus.className   = "flush-cache-status" + (cls ? " " + cls : "");
  if (ttl) setTimeout(() => {
    flushCacheStatus.textContent = "";
    flushCacheStatus.className   = "flush-cache-status";
  }, ttl);
}

// ── Flush Redis cache ─────────────────────────────────────────────
btnFlushCache?.addEventListener("click", async () => {
  if (!confirm(
    "Delete all Celery task results from Redis and clear the task queue?\n\n" +
    "This will break any in-progress jobs but fixes a crashed/looping worker."
  )) return;
  btnFlushCache.disabled = true;
  try {
    const res  = await fetch("/admin/flush-task-cache", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      _setStatus(`Cleared ${data.deleted} task result(s) + queue.`, "ok");
    } else {
      _setStatus(data.error || "Error", "err");
    }
  } catch {
    _setStatus("Network error", "err");
  }
  btnFlushCache.disabled = false;
});

// ── Hard Reset ────────────────────────────────────────────────────
// 1. Kill all running subprocesses + clear Redis state.
// 2. Check if the Celery worker is alive (via /admin/worker-status).
// 3. If the worker is dead, trigger a container restart via
//    /admin/restart-worker (requires docker.sock mounted in flask).
// 4. Poll until the worker responds or 30 s elapses, then refresh the
//    GPU dropdowns so the UI immediately reflects the restored state.
btnHardReset?.addEventListener("click", async () => {
  if (!confirm(
    "Hard Reset kills ALL running and queued jobs, clears Redis state, and\n" +
    "restores the GPU pool.\n\n" +
    "If the worker is down it will also be restarted automatically.\n\n" +
    "Continue?"
  )) return;

  btnHardReset.disabled  = true;
  btnFlushCache.disabled = true;
  _setStatus("Resetting…", "", 0);

  try {
    // ── Step 1: reset Redis ───────────────────────────────────────
    const resetRes  = await fetch("/admin/hard-reset-jobs", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ passphrase: "deeplabcut" }),
    });
    const resetData = await resetRes.json();
    if (!resetRes.ok) {
      _setStatus(resetData.error || "Reset failed.", "err");
      return;
    }
    const parts = [];
    if (resetData.processes_killed)     parts.push(`${resetData.processes_killed} process(es) killed`);
    if (resetData.queued_tasks_cleared) parts.push(`${resetData.queued_tasks_cleared} queued task(s) cleared`);
    if (resetData.jobs_cleared)         parts.push(`${resetData.jobs_cleared} job record(s) cleared`);
    const resetSummary = parts.join(", ") || "nothing was running";
    _setStatus(`Redis reset (${resetSummary}). Checking worker…`, "", 0);

    // ── Step 2: check worker health ───────────────────────────────
    const statusRes  = await fetch("/admin/worker-status");
    const statusData = await statusRes.json();

    if (statusData.alive) {
      _setStatus(`Done — ${resetSummary}. Worker is up. GPU pool restored.`, "ok");
      _refreshGpuSelects();
      return;
    }

    // ── Step 3: worker is dead — try to restart ───────────────────
    _setStatus("Worker is down — attempting restart…", "", 0);
    const restartRes  = await fetch("/admin/restart-worker", { method: "POST" });
    const restartData = await restartRes.json();

    if (!restartRes.ok) {
      _setStatus(
        `Redis reset OK. Worker restart failed: ${restartData.error || "unknown error"}. ` +
        "Run: docker compose up -d worker",
        "err"
      );
      return;
    }

    // ── Step 4: poll until worker responds (30 s max) ─────────────
    _setStatus("Worker restarting… waiting for it to come online.", "", 0);
    let alive = false;
    for (let i = 0; i < 15; i++) {
      await new Promise(r => setTimeout(r, 2000));
      try {
        const poll = await fetch("/admin/worker-status");
        const pd   = await poll.json();
        if (pd.alive) { alive = true; break; }
      } catch { /* network blip during restart — keep polling */ }
      _setStatus(`Worker restarting… ${(i + 1) * 2}s`, "", 0);
    }

    if (alive) {
      _setStatus(`Done — ${resetSummary}. Worker restarted and online. GPU pool restored.`, "ok");
      _refreshGpuSelects();
    } else {
      _setStatus(
        "Redis reset OK. Worker did not come online in 30 s. " +
        "Run: docker compose up -d worker",
        "err"
      );
    }

  } catch (err) {
    _setStatus("Network error: " + err.message, "err");
  } finally {
    btnHardReset.disabled  = false;
    btnFlushCache.disabled = false;
  }
});

function _refreshGpuSelects() {
  ["av-gputouse", "tn-gputouse", "fl-ml-gpu"].forEach(id => {
    _populateGpuSelect(id).catch(() => {});
  });
}
