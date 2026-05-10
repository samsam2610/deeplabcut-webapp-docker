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
      <div class="jobs-row ${isSel}" data-task-id="${id}" data-status="${status}">
        <div class="jobs-row-top">
          <span class="jobs-row-op">${op}</span>
          <span class="jobs-row-id">${id.slice(0, 8)}</span>
          <span class="jobs-row-status" style="color:${_statusColor(status)}">${_statusGlyph(status)} ${status}</span>
        </div>
        <div class="jobs-row-meta">
          <span>${j.project || ""}</span>
          <span>GPU${j.gpu_id || "?"}</span>
          <span>${_formatRuntime(j.started_at)}</span>
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
    State.jobs = data.jobs || [];
    _renderRail(State.jobs);
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

// ─── Row click — placeholder; real impl lands in Task 9 ─────────────────
function _onRowClick(taskId) {
  State.selectedTaskId = taskId;
  _renderRail(State.jobs);
  const detail = document.getElementById("jobs-detail");
  if (detail) detail.innerHTML = `<p class="jobs-empty">Selected ${taskId} — log streaming lands in Task 9.</p>`;
}

// ─── Bootstrap ──────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  _startListPoll();
});

// Test seam — exposed for cross-session E2E tests to wait on the first poll.
window.__jobsState = State;
