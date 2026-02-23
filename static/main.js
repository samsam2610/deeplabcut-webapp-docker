/* ─────────────────────────────────────────────────────────────
   Anipose Pipeline — Frontend Controller
   ───────────────────────────────────────────────────────────── */

(function () {
  "use strict";

  // ── Session DOM refs ────────────────────────────────────────
  const sessionDot   = document.getElementById("session-dot");
  const sessionLabel = document.getElementById("session-label");
  const sessionMeta  = document.getElementById("session-meta");
  const btnCreate    = document.getElementById("btn-create-session");
  const btnClear     = document.getElementById("btn-clear-session");
  const sessionInput = document.getElementById("session-config-input");

  let sessionPollTimer = null;

  // ── Session state helpers ────────────────────────────────────
  function applySessionState(data) {
    const s = data.status || "none";
    sessionDot.dataset.state = s;

    if (s === "none") {
      sessionLabel.textContent = "No active session";
      sessionMeta.textContent  = "";
      btnCreate.classList.remove("hidden");
      btnClear.classList.add("hidden");
    } else if (s === "initializing") {
      sessionLabel.textContent = "Initializing session…";
      sessionMeta.textContent  = data.config_name || "";
      btnCreate.classList.add("hidden");
      btnClear.classList.remove("hidden");
    } else if (s === "ready") {
      const ver = data.anipose_version ? `Anipose ${data.anipose_version}` : "Session ready";
      sessionLabel.textContent = ver;
      sessionMeta.textContent  = data.config_name || "";
      btnCreate.classList.add("hidden");
      btnClear.classList.remove("hidden");
    } else if (s === "error") {
      sessionLabel.textContent = "Session error";
      sessionMeta.textContent  = data.error || "";
      btnCreate.classList.remove("hidden");
      btnClear.classList.remove("hidden");
    }

    // Show the pipeline actions card only when the session is ready.
    // actionsCard is declared later but is always initialized before
    // this function is called (all call sites are behind an async await).
    const card = document.getElementById("actions-card");
    if (card) {
      card.classList.toggle("hidden", s !== "ready");
      if (s === "ready") loadProjects();
    }
  }

  function startSessionPoll() {
    if (sessionPollTimer) clearInterval(sessionPollTimer);
    sessionPollTimer = setInterval(async () => {
      try {
        const res  = await fetch("/session");
        const data = await res.json();
        applySessionState(data);
        if (data.status !== "initializing") {
          clearInterval(sessionPollTimer);
          sessionPollTimer = null;
        }
      } catch (err) {
        console.error("Session poll error:", err);
      }
    }, 2000);
  }

  // ── Create session (file-input change) ──────────────────────
  sessionInput.addEventListener("change", async () => {
    const file = sessionInput.files[0];
    if (!file) return;

    // Optimistically show loading state
    sessionDot.dataset.state   = "initializing";
    sessionLabel.textContent   = "Creating session…";
    sessionMeta.textContent    = file.name;
    btnCreate.classList.add("hidden");

    const fd = new FormData();
    fd.append("config", file);
    sessionInput.value = "";  // allow re-selection of same file

    try {
      const res  = await fetch("/session", { method: "POST", body: fd });
      const text = await res.text();
      let data;
      try { data = JSON.parse(text); }
      catch {
        // Server returned non-JSON (HTML traceback, etc.) — show raw snippet
        console.error("Non-JSON response from /session POST:", text);
        sessionDot.dataset.state = "error";
        sessionLabel.textContent = `Server error (HTTP ${res.status})`;
        sessionMeta.textContent  = text.replace(/<[^>]*>/g, "").trim().slice(0, 120);
        btnCreate.classList.remove("hidden");
        return;
      }
      if (!res.ok) {
        sessionDot.dataset.state = "error";
        sessionLabel.textContent = data.error || "Failed to create session";
        sessionMeta.textContent  = "";
        btnCreate.classList.remove("hidden");
        return;
      }
      applySessionState(data);
      startSessionPoll();
    } catch (err) {
      console.error("Create session fetch error:", err);
      sessionDot.dataset.state = "error";
      sessionLabel.textContent = "Could not reach server";
      sessionMeta.textContent  = err.message || "";
      btnCreate.classList.remove("hidden");
    }
  });

  // ── Clear session ────────────────────────────────────────────
  btnClear.addEventListener("click", async () => {
    if (!confirm("Clear the active session? The stored config will be removed.")) return;
    if (sessionPollTimer) { clearInterval(sessionPollTimer); sessionPollTimer = null; }
    try {
      await fetch("/session", { method: "DELETE" });
    } catch (err) {
      console.error("Clear session error:", err);
    }
    applySessionState({ status: "none" });
  });

  // ── Restore session state on page load ──────────────────────
  (async () => {
    try {
      const res  = await fetch("/session");
      const data = await res.json();
      applySessionState(data);
      if (data.status === "initializing") startSessionPoll();
    } catch (err) {
      console.error("Session load error:", err);
    }
  })();

  // ── Actions card DOM refs ────────────────────────────────────
  const folderSelect  = document.getElementById("folder-select");
  const progressTitle = document.getElementById("progress-title");
  const actionBtns    = document.querySelectorAll(".btn-action");

  const OPERATION_LABELS = {
    calibrate:   "Calibrating cameras",
    filter_2d:   "Filtering 2D predictions",
    triangulate: "Triangulating 3D poses",
    filter_3d:   "Filtering 3D trajectories",
  };

  // ── Populate project folder dropdown ────────────────────────
  async function loadProjects() {
    try {
      const res  = await fetch("/projects");
      const data = await res.json();
      // Exclude session_ dirs — they hold config only, not project data
      const projects = (data.projects || []).filter(p => !p.startsWith("session_"));
      folderSelect.innerHTML =
        '<option value="">— select a project folder —</option>' +
        projects.map(p => `<option value="${p}">${p}</option>`).join("");
    } catch (err) {
      console.error("loadProjects error:", err);
    }
  }

  // ── Action button clicks ─────────────────────────────────────
  actionBtns.forEach(btn => {
    btn.addEventListener("click", async () => {
      const operation = btn.dataset.op;
      const projectId = folderSelect.value;

      if (!projectId) {
        alert("Select a project folder first.");
        return;
      }

      // Disable all buttons while the task is running
      actionBtns.forEach(b => { b.disabled = true; });

      try {
        const res  = await fetch("/run", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ operation, project_id: projectId }),
        });
        const data = await res.json();

        if (!res.ok) {
          alert(data.error || "Failed to start operation.");
          actionBtns.forEach(b => { b.disabled = false; });
          return;
        }

        // Show progress card with operation-specific title
        progressTitle.textContent = OPERATION_LABELS[operation] || "Processing";
        showProgress(data.task_id);

      } catch (err) {
        console.error("run operation error:", err);
        alert("Network error. Is the server running?");
        actionBtns.forEach(b => { b.disabled = false; });
      }
    });
  });

  // Re-enable action buttons when "New Job" is clicked
  // (delegated below after newJobBtn is defined)

  // ── DOM refs ────────────────────────────────────────────────
  const form        = document.getElementById("upload-form");
  const submitBtn   = document.getElementById("submit-btn");
  const configInput = document.getElementById("config-input");
  const videoInput  = document.getElementById("video-input");
  const configName  = document.getElementById("config-name");
  const videoNames  = document.getElementById("video-names");
  const configDrop  = document.getElementById("config-drop");
  const videoDrop   = document.getElementById("video-drop");

  const uploadCard   = document.getElementById("upload-card");
  const progressCard = document.getElementById("progress-card");
  const progressBar  = document.getElementById("progress-bar");
  const progressPct  = document.getElementById("progress-pct");
  const progressStage= document.getElementById("progress-stage");
  const taskIdDisplay= document.getElementById("task-id-display");
  const logOutput    = document.getElementById("log-output");
  const newJobBtn    = document.getElementById("new-job-btn");

  let pollTimer = null;

  // ── File-input label updates ────────────────────────────────
  configInput.addEventListener("change", () => {
    const file = configInput.files[0];
    configName.textContent = file ? file.name : "";
    configDrop.classList.toggle("has-file", !!file);
  });

  videoInput.addEventListener("change", () => {
    const files = videoInput.files;
    if (files.length) {
      const names = Array.from(files).map(f => f.name);
      videoNames.textContent = names.length <= 3
        ? names.join(", ")
        : `${names.slice(0, 2).join(", ")} + ${names.length - 2} more`;
      videoDrop.classList.add("has-file");
    } else {
      videoNames.textContent = "";
      videoDrop.classList.remove("has-file");
    }
  });

  // ── Drag-and-drop highlighting ──────────────────────────────
  [configDrop, videoDrop].forEach(zone => {
    zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("dragover"); });
    zone.addEventListener("dragleave", ()  => zone.classList.remove("dragover"));
    zone.addEventListener("drop", ()       => zone.classList.remove("dragover"));
  });

  // ── Form submission ─────────────────────────────────────────
  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    // Basic validation
    if (!configInput.files.length) {
      return alert("Please select a config.toml file.");
    }
    if (!videoInput.files.length) {
      return alert("Please select at least one video file.");
    }

    // Build FormData
    const fd = new FormData();
    fd.append("config", configInput.files[0]);
    Array.from(videoInput.files).forEach(f => fd.append("videos[]", f));
    fd.append("task_type", document.getElementById("task-type").value);

    // Disable button
    submitBtn.disabled = true;
    submitBtn.querySelector(".btn-text").textContent = "Uploading…";

    try {
      const res = await fetch("/upload", { method: "POST", body: fd });
      const data = await res.json();

      if (!res.ok) {
        alert(data.error || "Upload failed.");
        resetButton();
        return;
      }

      // Switch to progress view
      showProgress(data.task_id);

    } catch (err) {
      console.error(err);
      alert("Network error. Is the server running?");
      resetButton();
    }
  });

  // ── Show progress card & start polling ──────────────────────
  function showProgress(taskId) {
    uploadCard.classList.add("hidden");
    progressCard.classList.remove("hidden");
    taskIdDisplay.textContent = taskId.slice(0, 12) + "…";
    progressBar.style.width = "0%";
    progressPct.textContent = "0 %";
    progressStage.textContent = "Queued";
    logOutput.textContent = "Waiting for output…";
    newJobBtn.classList.add("hidden");
    progressCard.classList.remove("state-success", "state-fail");

    pollTimer = setInterval(() => pollStatus(taskId), 5000);
    // First poll immediately
    pollStatus(taskId);
  }

  // ── Poll /status/<task_id> ──────────────────────────────────
  async function pollStatus(taskId) {
    try {
      const res  = await fetch(`/status/${taskId}`);
      const data = await res.json();

      // Update bar
      const pct = Math.min(data.progress || 0, 100);
      progressBar.style.width = pct + "%";
      progressPct.textContent = pct + " %";
      progressStage.textContent = data.stage || data.state;

      // Update log
      if (data.log) {
        logOutput.textContent = data.log;
        logOutput.scrollTop = logOutput.scrollHeight;
      }

      // Terminal states
      if (data.state === "SUCCESS") {
        clearInterval(pollTimer);
        progressCard.classList.add("state-success");
        progressStage.textContent = "✓ Complete";
        progressBar.style.width = "100%";
        progressPct.textContent = "100 %";
        if (data.result && data.result.log) {
          logOutput.textContent = data.result.log;
        }
        newJobBtn.classList.remove("hidden");
      }

      if (data.state === "FAILURE") {
        clearInterval(pollTimer);
        progressCard.classList.add("state-fail");
        progressStage.textContent = "✗ " + (data.error || "Failed");
        logOutput.textContent = data.error || "An unknown error occurred.";
        newJobBtn.classList.remove("hidden");
      }

    } catch (err) {
      console.error("Polling error:", err);
    }
  }

  // ── New job ─────────────────────────────────────────────────
  newJobBtn.addEventListener("click", () => {
    progressCard.classList.add("hidden");
    progressTitle.textContent = "Processing";  // reset title for next run
    uploadCard.classList.remove("hidden");
    form.reset();
    configName.textContent = "";
    videoNames.textContent = "";
    configDrop.classList.remove("has-file");
    videoDrop.classList.remove("has-file");
    resetButton();
    // Re-enable action buttons after returning from progress view
    actionBtns.forEach(b => { b.disabled = false; });
  });

  function resetButton() {
    submitBtn.disabled = false;
    submitBtn.querySelector(".btn-text").textContent = "Launch Processing";
  }

})();
