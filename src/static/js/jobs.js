"use strict";

// ─── jobs.js — unified monitor for every computationally intensive DLC task ──
//
// Reads the merged list from /dlc/jobs/all every 3s when the tab is visible.
// That endpoint merges three sources server-side (dlc.monitoring.dlc_jobs_all):
//   a. the dlc_train_jobs/dlc_analyze_jobs zsets (train, analyze, triangulate,
//      machine-label, tapnet, ...)
//   b. Celery's live inspect(active()+reserved()) — catches tasks that dispatch
//      via send_task with no Redis job-hash of their own (peaks emission, LP
//      3D tasks, range-triangulate, ...)
//   c. warm inline-analysis sessions (inline:session:<user_id>:<snap_key>),
//      each carrying its LLEN inline:queue:<...> pending count so a stranded
//      queue (a session's Celery task died mid-drain) is discoverable.
//
// Every row has a uniform shape: {id, type, kind, label, state, started_at,
// detail, cancellable}. `type` selects the /dlc/jobs/cancel dispatch:
//   "celery" -> control.revoke(id, terminate=True)  (kills running work now)
//   "inline" -> stops the session + drops its pending queue
//
// Selecting a row opens a backfill+SSE stream to /dlc/task/<id>/log-stream
// via the shared window.logStream module (unchanged from before this task —
// celery-backed rows keep the same rail/detail/log-view/Stop-button flow they
// always had). Inline-session rows have no log stream; selecting one shows a
// small summary instead.
//
// Heartbeat-bearing SSE + the shared client (see log_stream.js + spec
// docs/superpowers/specs/2026-05-19-jobs-sse-heartbeat-hybrid-design.md)
// removed the need for the previous client-side idle timeout and bespoke
// reconnect logic.

const State = {
  selectedId:       null,
  unsubscribeLog:   null,   // returned by logStream.subscribe()
  stopPollFallback: null,   // returned by logStream.pollTail() when demoted
  listPollTimer:    null,
  jobs:             [],     // last-rendered list (for Cancel confirmation)
  celeryReachable:  true,
};

const POLL_MS = 3000;

function _escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ─── Cancel confirmation copy ────────────────────────────────────────────
// The confirm must state the actual consequence, not a generic "Are you
// sure?" — a running task loses in-flight work, an inline session discards
// a concrete, visible number of pending ranges.
function _cancelConfirmText(job) {
  if (!job) return "Cancel this task?";
  if (job.type === "inline") {
    const pending = (job.detail && job.detail.pending) || 0;
    const noun = pending === 1 ? "range" : "ranges";
    return `Cancel inline analysis?\n\n${pending} pending ${noun} will be discarded.`;
  }
  const label = job.label || job.kind || "this task";
  return `Cancel ${label}?\n\n` +
    "It will be stopped mid-run — any work already in progress on it is lost.";
}

function _isCancellable(job) {
  return !!(job && job.cancellable);
}

// ─── Rail rendering ──────────────────────────────────────────────────────
function _statusGlyph(state) {
  return ({
    running:  "●",   // ●
    reserved: "○",   // ○
    warming:  "○",
    ready:    "●",
    paused:   "⏸",   // ⏸
    complete: "✓",   // ✓
    failed:   "✗",   // ✗
    dead:     "⚠",   // ⚠
    orphaned: "⚠",
    error:    "⚠",
    expired:  "■",   // ■
    stopped:  "■",
    stopping: "■",
  })[state] || "·";
}

function _statusColor(state) {
  return ({
    running:  "var(--accent)",
    reserved: "var(--accent)",
    warming:  "var(--accent)",
    ready:    "var(--accent)",
    paused:   "#d29922",
    complete: "#3fb950",
    failed:   "#f85149",
    dead:     "#f85149",
    orphaned: "#f85149",
    error:    "#f85149",
    stopped:  "var(--text-dim)",
    stopping: "var(--text-dim)",
    expired:  "var(--text-dim)",
  })[state] || "var(--text-dim)";
}

function _formatRuntime(startedAt) {
  if (!startedAt) return "";
  const elapsed = Date.now() / 1000 - parseFloat(startedAt);
  if (!isFinite(elapsed) || elapsed < 0) return "";
  if (elapsed < 60)   return `${Math.round(elapsed)}s`;
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)}m`;
  const h = Math.floor(elapsed / 3600);
  const m = Math.floor((elapsed - h * 3600) / 60);
  return `${h}h ${m}m`;
}

function _renderCeleryWarning(reachable) {
  const el = document.getElementById("jobs-celery-warn");
  if (!el) return;
  el.hidden = reachable !== false;
}

function _renderRail(jobs) {
  const rail = document.getElementById("jobs-rail");
  if (!rail) return;
  if (!jobs.length) {
    rail.innerHTML = '<p class="jobs-empty">No jobs running.</p>';
    return;
  }
  rail.innerHTML = jobs.map(j => {
    const id = j.id || "";
    const kind = j.kind || j.type || "job";
    const state = j.state || "";
    const isSel = id === State.selectedId ? "selected" : "";
    const project = (j.detail && (j.detail.project || j.detail.target_path)) || "";
    const gpuId = (j.detail && j.detail.gpu_id) || "";
    return `
      <div class="jobs-row ${isSel}" data-id="${_escapeHtml(id)}" data-state="${_escapeHtml(state)}">
        <div class="jobs-row-top">
          <span class="jobs-row-op">${_escapeHtml(kind)}</span>
          <span class="jobs-row-id">${_escapeHtml(id.slice(0, 8))}</span>
          <span class="jobs-row-status" style="color:${_statusColor(state)}">${_statusGlyph(state)} ${_escapeHtml(state)}</span>
        </div>
        <div class="jobs-row-meta">
          <span>${_escapeHtml(j.label || "")}</span>
          ${project ? `<span>${_escapeHtml(project)}</span>` : ""}
          ${gpuId ? `<span>GPU${_escapeHtml(gpuId)}</span>` : ""}
          <span>${_escapeHtml(_formatRuntime(j.started_at))}</span>
        </div>
        ${_isCancellable(j) ? `
        <div class="jobs-row-cancel-wrap">
          <button type="button" class="jobs-row-cancel-btn" data-action="cancel" data-id="${_escapeHtml(id)}">Cancel</button>
        </div>` : ""}
      </div>`;
  }).join("");

  rail.querySelectorAll(".jobs-row").forEach(row => {
    row.addEventListener("click", (evt) => {
      if (evt.target.closest('[data-action="cancel"]')) return;  // handled below
      _onRowClick(row.dataset.id);
    });
  });
  rail.querySelectorAll('[data-action="cancel"]').forEach(btn => {
    btn.addEventListener("click", (evt) => {
      evt.stopPropagation();
      const job = State.jobs.find(j => j.id === btn.dataset.id);
      _onCancelClick(job, btn);
    });
  });
}

async function _onCancelClick(job, btnEl) {
  if (!job) return;
  const ok = window.confirm(_cancelConfirmText(job));
  if (!ok) return;
  if (btnEl) btnEl.disabled = true;
  try {
    const res = await fetch("/dlc/jobs/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: job.id, type: job.type }),
    });
    if (!res.ok) {
      const errText = await res.text();
      alert(`Cancel failed: ${errText}`);
      if (btnEl) btnEl.disabled = false;
      return;
    }
  } catch (err) {
    alert(`Cancel failed: ${err.message}`);
    if (btnEl) btnEl.disabled = false;
    return;
  }
  // Status flip surfaces on the next list poll (within ~3s); also refresh now.
  _fetchJobs();
}

async function _fetchJobs() {
  try {
    const res = await fetch("/dlc/jobs/all");
    if (!res.ok) return;
    const data = await res.json();
    const isFirstFetch = State.jobs.length === 0;
    State.jobs = data.jobs || [];
    State.celeryReachable = data.celery_reachable !== false;
    _renderCeleryWarning(State.celeryReachable);
    _renderRail(State.jobs);
    // Auto-select the most-recent running job on first load (only — don't
    // hijack the user's selection on subsequent polls).
    if (isFirstFetch && !State.selectedId) {
      const firstRunning = State.jobs.find(j => j.state === "running");
      if (firstRunning && firstRunning.id) {
        _onRowClick(firstRunning.id);
      }
    }
  } catch (err) {
    console.error("[jobs] _fetchJobs failed:", err);
  }
}

function _startListPoll() {
  if (State.listPollTimer) clearInterval(State.listPollTimer);
  _fetchJobs();
  State.listPollTimer = setInterval(_fetchJobs, POLL_MS);
}

function _stopListPoll() {
  if (State.listPollTimer) {
    clearInterval(State.listPollTimer);
    State.listPollTimer = null;
  }
}

// ─── Detail pane: backfill + SSE stream (celery-backed rows) ────────────
function _setStatusPill(text, cls) {
  const pill = document.getElementById("jobs-status-pill");
  if (!pill) return;
  pill.textContent = text;
  pill.className = "jobs-status-pill " + (cls || "");
}

function _renderDetailHeader(job) {
  const state = job.state || "";
  const showStop = state === "running" || state === "paused";
  const startedTxt = job.started_at
    ? new Date(parseFloat(job.started_at) * 1000).toLocaleTimeString()
    : "?";
  const project = (job.detail && job.detail.project) || "?";
  const engine = (job.detail && job.detail.engine) || "?";
  const gpuId = (job.detail && job.detail.gpu_id) || "?";
  const stage = job.detail && job.detail.stage;
  return `
    <div class="jobs-detail-header">
      <h3>${_escapeHtml(job.kind || "job")} ${_escapeHtml(job.id || "")}</h3>
      <div class="jobs-detail-meta">
        <span>project: ${_escapeHtml(project)}</span>
        <span>engine: ${_escapeHtml(engine)}</span>
        <span>GPU${_escapeHtml(gpuId)}</span>
        <span>started: ${_escapeHtml(startedTxt)}</span>
        <span>status: ${_escapeHtml(state)}</span>
      </div>
      ${stage ? `<div class="jobs-detail-stage" style="color:var(--text-dim)">${_escapeHtml(stage)}</div>` : ""}
      ${showStop ? `<button class="jobs-stop-btn" data-action="stop">Stop</button>` : ""}
    </div>
    <pre id="jobs-terminal" class="jobs-terminal"></pre>
  `;
}

function _renderInlineDetail(job) {
  const startedTxt = job.started_at
    ? new Date(parseFloat(job.started_at) * 1000).toLocaleTimeString()
    : "?";
  const d = job.detail || {};
  return `
    <div class="jobs-detail-header">
      <h3>inline analysis ${_escapeHtml(d.project || "")}</h3>
      <div class="jobs-detail-meta">
        <span>snapshot: ${_escapeHtml(d.snapshot || "?")}</span>
        <span>pending: ${_escapeHtml(String(d.pending != null ? d.pending : "?"))}</span>
        <span>started: ${_escapeHtml(startedTxt)}</span>
        <span>status: ${_escapeHtml(job.state || "")}</span>
      </div>
      ${_isCancellable(job) ? `<button class="jobs-stop-btn" data-action="cancel-inline">Cancel</button>` : ""}
    </div>
    <p class="jobs-empty">Inline-analysis sessions don't have a log stream. Use the
    pending-ranges count above to confirm whether it's safe to cancel.</p>
  `;
}

async function _backfillLog(taskId, terminalEl) {
  try {
    const res = await fetch(`/dlc/task/${taskId}/log-tail?n=2000`);
    if (!res.ok) return;
    const data = await res.json();
    const lines = (data.lines || []).join("\n");
    terminalEl.textContent = lines + (lines ? "\n" : "");
    terminalEl.scrollTop = terminalEl.scrollHeight;
  } catch (err) {
    console.error("[jobs] backfill failed:", err);
  }
}

function _isAtBottom(el) {
  return Math.abs(el.scrollHeight - el.clientHeight - el.scrollTop) < 6;
}

function _appendLine(taskId, terminalEl, line) {
  if (taskId !== State.selectedId) return;  // raced past selection change
  const wasBottom = _isAtBottom(terminalEl);
  terminalEl.textContent += line + "\n";
  if (wasBottom) terminalEl.scrollTop = terminalEl.scrollHeight;
}

function _closeStream() {
  if (State.unsubscribeLog) { try { State.unsubscribeLog(); } catch (_) {} State.unsubscribeLog = null; }
  if (State.stopPollFallback) { try { State.stopPollFallback(); } catch (_) {} State.stopPollFallback = null; }
}

function _openStream(taskId, terminalEl) {
  _closeStream();
  const ls = window.logStream;
  if (!ls) {
    _setStatusPill("error: log_stream.js not loaded", "error");
    console.error("[jobs] window.logStream missing — log_stream.js was not loaded before jobs.js");
    return;
  }
  State.unsubscribeLog = ls.subscribe(taskId, {
    onLine: (line) => _appendLine(taskId, terminalEl, line),
    onDone: () => {
      _setStatusPill("stream ended", "closed");
    },
    onDemoted: () => {
      // Another consumer took the SSE for a different task. Fall back to
      // polling so we keep seeing log updates (at a slower cadence).
      _setStatusPill("polling (shared SSE busy)", "paused");
      if (State.stopPollFallback) { try { State.stopPollFallback(); } catch (_) {} }
      State.stopPollFallback = ls.pollTail(taskId, {
        intervalMs: 60000,
        onLines: (newLines) => {
          newLines.forEach(l => _appendLine(taskId, terminalEl, l));
        },
      });
    },
    onStatus: (text, cls) => _setStatusPill(text, cls),
  });
}

async function _showJob(id) {
  State.selectedId = id;
  _renderRail(State.jobs);
  const detail = document.getElementById("jobs-detail");
  if (!detail) return;
  const job = State.jobs.find(j => j.id === id) || { id, type: "celery" };

  if (job.type === "inline") {
    _closeStream();
    detail.innerHTML = _renderInlineDetail(job);
    const cancelBtn = detail.querySelector('button[data-action="cancel-inline"]');
    if (cancelBtn) {
      cancelBtn.addEventListener("click", () => _onCancelClick(job, cancelBtn));
    }
    return;
  }

  detail.innerHTML = _renderDetailHeader(job);
  const terminal = detail.querySelector("#jobs-terminal");
  await _backfillLog(id, terminal);
  _openStream(id, terminal);

  const stopBtn = detail.querySelector('button[data-action="stop"]');
  if (stopBtn) {
    stopBtn.addEventListener("click", async () => {
      const ok = window.confirm(`Stop ${job.kind || "task"} ${id}?\n\nThis cannot be undone.`);
      if (!ok) return;
      stopBtn.disabled = true;
      try {
        const res = await fetch(`/dlc/task/${id}/terminate`, { method: "POST" });
        if (!res.ok) {
          const errText = await res.text();
          alert(`Stop failed: ${errText}`);
          stopBtn.disabled = false;
          return;
        }
        // Status flip surfaces on the next list poll (within ~3s).
      } catch (err) {
        alert(`Stop failed: ${err.message}`);
        stopBtn.disabled = false;
      }
    });
  }
}

// ─── Row click ──────────────────────────────────────────────────────────
function _onRowClick(id) {
  if (!id || id === State.selectedId) return;
  _showJob(id).catch(err => console.error("[jobs] _showJob:", err));
}

// ─── Visibility ─────────────────────────────────────────────────────────
// On hide: pause the list poll (cheap fetch) and close the shared SSE so
// other consumers can claim it. On show: resume the list poll and re-open
// the stream. The shared logStream module owns reconnection concerns —
// no bespoke retry / idle timer needed here.
function _onHidden() {
  _closeStream();
  _stopListPoll();
  _setStatusPill("paused (tab hidden)", "paused");
}

function _onVisible() {
  _startListPoll();
  const selected = State.jobs.find(j => j.id === State.selectedId);
  if (State.selectedId && selected && selected.type !== "inline") {
    const term = document.querySelector("#jobs-terminal");
    if (term) {
      _backfillLog(State.selectedId, term).then(() => {
        _openStream(State.selectedId, term);
      });
    }
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) _onHidden();
  else                  _onVisible();
});

// ─── Bootstrap ──────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  _startListPoll();
});

// Test seam — exposed for cross-session E2E tests to wait on the first poll.
window.__jobsState = State;
// Test seam — pure helpers exercised by tests/test_jobs_js_cancel_ui.mjs
// (no DOM/browser needed: these two are plain functions of a job object).
window.__jobsTestHooks = { _cancelConfirmText, _isCancellable };
