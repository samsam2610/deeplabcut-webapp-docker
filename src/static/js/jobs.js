"use strict";

// ─── jobs.js — session-independent monitor for DLC train/analyze tasks ───
//
// Reads list state from /dlc/training/jobs (global Redis-backed) every 3s
// when the tab is visible. Selecting a job opens a backfill+SSE stream to
// /dlc/task/<id>/log-stream (added in Task 9). Stop button calls
// /dlc/task/<id>/terminate (added in Task 11). Visibility + 20-min
// idle timeout govern the SSE lifecycle (added in Task 10).

const State = {
  selectedTaskId: null,
  eventSource:    null,
  listPollTimer:  null,
  idleTimer:      null,
  jobs:           [],   // last-rendered list (for Stop confirmation)
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

// ─── Rail rendering ──────────────────────────────────────────────────────
function _statusGlyph(status) {
  return ({
    running:  "●",
    paused:   "⏸",
    complete: "✓",
    failed:   "✗",
    dead:     "⚠",
    stopped:  "■",
    stopping: "■",
  })[status] || "·";
}

function _statusColor(status) {
  return ({
    running:  "var(--accent)",
    paused:   "#d29922",
    complete: "#3fb950",
    failed:   "#f85149",
    dead:     "#f85149",
    stopped:  "var(--text-dim)",
    stopping: "var(--text-dim)",
  })[status] || "var(--text-dim)";
}

function _formatRuntime(startedAt) {
  if (!startedAt) return "";
  const elapsed = Date.now() / 1000 - parseFloat(startedAt);
  if (elapsed < 60)   return `${Math.round(elapsed)}s`;
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)}m`;
  const h = Math.floor(elapsed / 3600);
  const m = Math.floor((elapsed - h * 3600) / 60);
  return `${h}h ${m}m`;
}

function _renderRail(jobs) {
  const rail = document.getElementById("jobs-rail");
  if (!rail) return;
  if (!jobs.length) {
    rail.innerHTML = '<p class="jobs-empty">No jobs running.</p>';
    return;
  }
  rail.innerHTML = jobs.map(j => {
    const id = j.task_id || "";
    const op = j.operation || "train";
    const status = j.status || "";
    const isSel = id === State.selectedTaskId ? "selected" : "";
    return `
      <div class="jobs-row ${isSel}" data-task-id="${_escapeHtml(id)}" data-status="${_escapeHtml(status)}">
        <div class="jobs-row-top">
          <span class="jobs-row-op">${_escapeHtml(op)}</span>
          <span class="jobs-row-id">${_escapeHtml(id.slice(0, 8))}</span>
          <span class="jobs-row-status" style="color:${_statusColor(status)}">${_statusGlyph(status)} ${_escapeHtml(status)}</span>
        </div>
        <div class="jobs-row-meta">
          <span>${_escapeHtml(j.project || "")}</span>
          <span>GPU${_escapeHtml(j.gpu_id || "?")}</span>
          <span>${_escapeHtml(_formatRuntime(j.started_at))}</span>
        </div>
      </div>`;
  }).join("");
  rail.querySelectorAll(".jobs-row").forEach(row => {
    row.addEventListener("click", () => _onRowClick(row.dataset.taskId));
  });
}

async function _fetchJobs() {
  try {
    const res = await fetch("/dlc/training/jobs");
    if (!res.ok) return;
    const data = await res.json();
    const isFirstFetch = State.jobs.length === 0;
    State.jobs = data.jobs || [];
    _renderRail(State.jobs);
    // Auto-select the most-recent running job on first load (only — don't
    // hijack the user's selection on subsequent polls).
    if (isFirstFetch && !State.selectedTaskId) {
      const firstRunning = State.jobs.find(j => j.status === "running");
      if (firstRunning && firstRunning.task_id) {
        _onRowClick(firstRunning.task_id);
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

// ─── Detail pane: backfill + SSE stream ─────────────────────────────────
function _setStatusPill(text, cls) {
  const pill = document.getElementById("jobs-status-pill");
  if (!pill) return;
  pill.textContent = text;
  pill.className = "jobs-status-pill " + (cls || "");
}

function _renderDetailHeader(job) {
  const status = job.status || "";
  const showStop = status === "running" || status === "paused";
  const startedTxt = job.started_at
    ? new Date(parseFloat(job.started_at) * 1000).toLocaleTimeString()
    : "?";
  return `
    <div class="jobs-detail-header">
      <h3>${_escapeHtml(job.operation || "train")} ${_escapeHtml(job.task_id || "")}</h3>
      <div class="jobs-detail-meta">
        <span>project: ${_escapeHtml(job.project || "?")}</span>
        <span>engine: ${_escapeHtml(job.engine || "?")}</span>
        <span>GPU${_escapeHtml(job.gpu_id || "?")}</span>
        <span>started: ${_escapeHtml(startedTxt)}</span>
        <span>status: ${_escapeHtml(status)}</span>
      </div>
      ${showStop ? `<button class="jobs-stop-btn" data-action="stop">Stop</button>` : ""}
    </div>
    <pre id="jobs-terminal" class="jobs-terminal"></pre>
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

function _openStream(taskId, terminalEl) {
  if (State.eventSource) { State.eventSource.close(); State.eventSource = null; }
  const es = new EventSource(`/dlc/task/${taskId}/log-stream`);
  es.addEventListener("message", (ev) => {
    if (taskId !== State.selectedTaskId) return;  // raced past selection change
    const wasBottom = _isAtBottom(terminalEl);
    terminalEl.textContent += ev.data + "\n";
    if (wasBottom) terminalEl.scrollTop = terminalEl.scrollHeight;
  });
  let retryArmed = true;
  es.addEventListener("error", () => {
    if (retryArmed) {
      retryArmed = false;
      _setStatusPill("reconnecting…", "paused");
      // Browsers auto-reconnect EventSource by default; give it 2s. If a
      // second 'error' fires within that window OR no 'message' arrives,
      // close and surface Reconnect.
      setTimeout(() => {
        if (es.readyState === EventSource.CLOSED || es.readyState === EventSource.CONNECTING) {
          _setStatusPill("disconnected (server unreachable)", "error");
          es.close();
          _showReconnectButton();
        } else {
          retryArmed = true;  // back to live; re-arm
          _setStatusPill("live · streaming", "live");
        }
      }, 2000);
    } else {
      _setStatusPill("disconnected (server unreachable)", "error");
      es.close();
      _showReconnectButton();
    }
  });
  State.eventSource = es;
  _setStatusPill("live · streaming", "live");
}

async function _showJob(taskId) {
  State.selectedTaskId = taskId;
  _renderRail(State.jobs);
  const detail = document.getElementById("jobs-detail");
  if (!detail) return;
  const job = State.jobs.find(j => j.task_id === taskId) || { task_id: taskId };
  detail.innerHTML = _renderDetailHeader(job);
  const terminal = detail.querySelector("#jobs-terminal");
  await _backfillLog(taskId, terminal);
  _openStream(taskId, terminal);

  const stopBtn = detail.querySelector('button[data-action="stop"]');
  if (stopBtn) {
    stopBtn.addEventListener("click", async () => {
      const ok = window.confirm(`Stop ${job.operation || "task"} ${taskId}?\n\nThis cannot be undone.`);
      if (!ok) return;
      stopBtn.disabled = true;
      try {
        const res = await fetch(`/dlc/task/${taskId}/terminate`, { method: "POST" });
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
function _onRowClick(taskId) {
  if (!taskId || taskId === State.selectedTaskId) return;
  _showJob(taskId).catch(err => console.error("[jobs] _showJob:", err));
}

// ─── Visibility + 20-min idle timeout ───────────────────────────────────
const IDLE_MS_DEFAULT = 20 * 60 * 1000;

function _idleMs() {
  // Test seam: ?_test_idle_ms=500 lets E2E tests force a fast timeout.
  // Honored only when the URL is on localhost (defensive against accidental
  // exposure in production).
  if (location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
    return IDLE_MS_DEFAULT;
  }
  const v = parseInt(new URLSearchParams(location.search).get("_test_idle_ms"), 10);
  return Number.isFinite(v) && v > 0 ? v : IDLE_MS_DEFAULT;
}

function _showReconnectButton() {
  const detail = document.getElementById("jobs-detail");
  if (!detail) return;
  if (detail.querySelector(".jobs-reconnect-btn")) return;  // already shown
  const btn = document.createElement("button");
  btn.className = "jobs-reconnect-btn";
  btn.textContent = "Reconnect";
  btn.addEventListener("click", () => {
    btn.remove();
    if (State.selectedTaskId) _showJob(State.selectedTaskId);
    _startListPoll();
  });
  detail.appendChild(btn);
}

function _onHidden() {
  if (State.eventSource) { State.eventSource.close(); State.eventSource = null; }
  _stopListPoll();
  _setStatusPill("paused (tab hidden)", "paused");
  if (State.idleTimer) clearTimeout(State.idleTimer);
  State.idleTimer = setTimeout(() => {
    State.idleTimer = null;
    _setStatusPill("closed (idle 20m — Reconnect)", "closed");
    _showReconnectButton();
  }, _idleMs());
}

function _onVisible() {
  if (State.idleTimer) { clearTimeout(State.idleTimer); State.idleTimer = null; }
  // Don't auto-resume if the idle timer already fired and the user hasn't clicked Reconnect.
  const detail = document.getElementById("jobs-detail");
  if (detail && detail.querySelector(".jobs-reconnect-btn")) return;
  _startListPoll();
  if (State.selectedTaskId) {
    const term = document.querySelector("#jobs-terminal");
    if (term) {
      _backfillLog(State.selectedTaskId, term).then(() => {
        _openStream(State.selectedTaskId, term);
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
