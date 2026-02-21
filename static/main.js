/* ─────────────────────────────────────────────────────────────
   Anipose Pipeline — Frontend Controller
   ───────────────────────────────────────────────────────────── */

(function () {
  "use strict";

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
    uploadCard.classList.remove("hidden");
    form.reset();
    configName.textContent = "";
    videoNames.textContent = "";
    configDrop.classList.remove("has-file");
    videoDrop.classList.remove("has-file");
    resetButton();
  });

  function resetButton() {
    submitBtn.disabled = false;
    submitBtn.querySelector(".btn-text").textContent = "Launch Processing";
  }

})();
