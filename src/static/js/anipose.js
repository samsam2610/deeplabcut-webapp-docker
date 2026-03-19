"use strict";
import { state } from './state.js';
import { applyDlcProjectState, browseProject, showProgress } from './dlc_project.js';

  // ── Session DOM refs ────────────────────────────────────────
  const sessionDot   = document.getElementById("session-dot");
  const sessionLabel = document.getElementById("session-label");
  const sessionMeta  = document.getElementById("session-meta");
  const btnCreate             = document.getElementById("btn-create-session");
  const btnClear              = document.getElementById("btn-clear-session");
  const btnSessionFromServer  = document.getElementById("btn-session-from-server");
  const sessionInput          = document.getElementById("session-config-input");

  // ── Session state helpers ────────────────────────────────────
  function applySessionState(data) {
    const s = data.status || "none";
    sessionDot.dataset.state = s;

    const isIdle = (s === "none" || s === "error");

    if (s === "none") {
      sessionLabel.textContent = "No active Anipose session";
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

    // "From server" button: only when idle AND user-data volume is mounted
    btnSessionFromServer.classList.toggle("hidden", !(isIdle && state.userDataDir !== null));

    // Close picker if session becomes active
    if (!isIdle) _closeServerPicker();

    // Show the pipeline actions card only when the session is ready.
    // actionsCard is declared later but is always initialized before
    // this function is called (all call sites are behind an async await).
    const actionsCard  = document.getElementById("actions-card");
    const configCard   = document.getElementById("config-card");
    const explorerCard = document.getElementById("explorer-card");

    if (actionsCard)  actionsCard.classList.toggle("hidden",  s !== "ready");
    if (configCard)   configCard.classList.toggle("hidden",   s !== "ready");
    if (explorerCard) explorerCard.classList.toggle("hidden", s !== "ready");

    if (s === "ready") {
      _initConfig().then(() => loadProjects(state.currentRoot));
      loadConfig();
    }
  }

  function startSessionPoll() {
    if (state.sessionPollTimer) clearInterval(state.sessionPollTimer);
    state.sessionPollTimer = setInterval(async () => {
      try {
        const res  = await fetch("/session");
        const data = await res.json();
        applySessionState(data);
        if (data.status !== "initializing") {
          clearInterval(state.sessionPollTimer);
          state.sessionPollTimer = null;
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
    if (state.sessionPollTimer) { clearInterval(state.sessionPollTimer); state.sessionPollTimer = null; }
    try {
      await fetch("/session", { method: "DELETE" });
    } catch (err) {
      console.error("Clear session error:", err);
    }
    applySessionState({ status: "none" });
  });

  // ── Flush Redis cache ─────────────────────────────────────────
  const btnFlushCache      = document.getElementById("btn-flush-cache");
  const flushCacheStatus   = document.getElementById("flush-cache-status");

  btnFlushCache.addEventListener("click", async () => {
    if (!confirm("Delete all Celery task results from Redis and clear the task queue?\n\nThis will break any in-progress jobs but fixes a crashed/looping worker.")) return;
    btnFlushCache.disabled = true;
    try {
      const res  = await fetch("/admin/flush-task-cache", { method: "POST" });
      const data = await res.json();
      if (res.ok) {
        flushCacheStatus.textContent = `Cleared ${data.deleted} task result(s) + queue.`;
        flushCacheStatus.className   = "flush-cache-status ok";
      } else {
        flushCacheStatus.textContent = data.error || "Error";
        flushCacheStatus.className   = "flush-cache-status err";
      }
    } catch {
      flushCacheStatus.textContent = "Network error";
      flushCacheStatus.className   = "flush-cache-status err";
    }
    btnFlushCache.disabled = false;
    setTimeout(() => {
      flushCacheStatus.textContent = "";
      flushCacheStatus.className   = "flush-cache-status";
    }, 5000);
  });

  // ── Restore session state on page load ──────────────────────
  (async () => {
    // Pre-fetch /config so state.userDataDir is set before applySessionState runs.
    // This ensures the "From server" button is shown even with no active session.
    try {
      const cfgRes  = await fetch("/config");
      const cfgData = await cfgRes.json();
      if (cfgData.user_data_dir) {
        state.userDataDir = cfgData.user_data_dir;
        sourceBtnUserData.disabled = false;
        sourceBtnUserData.title    = `User data volume: ${cfgData.user_data_dir}`;
      }
      if (cfgData.data_dir) state.dataDir = cfgData.data_dir;
    } catch (err) {
      console.error("Config pre-fetch error:", err);
    }

    try {
      const res  = await fetch("/session");
      const data = await res.json();
      applySessionState(data);
      if (data.status === "initializing") startSessionPoll();
    } catch (err) {
      console.error("Session load error:", err);
    }

    // Restore DLC project state
    try {
      const dlcRes  = await fetch("/dlc/project");
      const dlcData = await dlcRes.json();
      if (dlcData.status !== "none") applyDlcProjectState(dlcData);
    } catch (err) {
      console.error("DLC project restore error:", err);
    }
  })();

  // ── Server-side config picker ─────────────────────────────────
  const sessionServerPicker = document.getElementById("session-server-picker");
  const pickerBreadcrumb    = document.getElementById("picker-breadcrumb");
  const pickerSubdirs       = document.getElementById("picker-subdirs");
  const pickerConfigs       = document.getElementById("picker-configs");
  const pickerCloseBtn      = document.getElementById("picker-close-btn");

  function _closeServerPicker() {
    sessionServerPicker.classList.add("hidden");
  }

  function _openServerPicker() {
    sessionServerPicker.classList.remove("hidden");
    _refreshPickerNav(state.userDataDir);
  }

  async function _refreshPickerNav(path) {

    // Breadcrumb
    const baseName = state.userDataDir.split("/").filter(Boolean).pop() || "user-data";
    const rel = path.substring(state.userDataDir.length).split("/").filter(Boolean);
    let crumbHTML = `<button class="picker-bc-seg" data-path="${state.userDataDir}">${baseName}</button>`;
    let cumPath = state.userDataDir;
    rel.forEach((part, i) => {
      cumPath += "/" + part;
      const isLast = (i === rel.length - 1);
      crumbHTML += `<span class="picker-bc-sep">›</span>`;
      crumbHTML += `<button class="picker-bc-seg${isLast ? " active" : ""}" data-path="${cumPath}">${part}</button>`;
    });
    pickerBreadcrumb.innerHTML = crumbHTML;
    pickerBreadcrumb.querySelectorAll(".picker-bc-seg").forEach(seg => {
      seg.addEventListener("click", () => _refreshPickerNav(seg.dataset.path));
    });

    pickerSubdirs.innerHTML = '<span class="picker-loading">Loading…</span>';
    pickerConfigs.innerHTML = "";

    try {
      const res  = await fetch(`/fs/list-configs?path=${encodeURIComponent(path)}`);
      const data = await res.json();

      // Subdirs
      pickerSubdirs.innerHTML = "";
      if (path !== state.userDataDir) {
        const upBtn = document.createElement("button");
        upBtn.className   = "picker-subfolder-chip up";
        upBtn.textContent = "..";
        upBtn.title       = "Go up one level";
        const parent = path.split("/").slice(0, -1).join("/") || "/";
        upBtn.addEventListener("click", () => _refreshPickerNav(parent));
        pickerSubdirs.appendChild(upBtn);
      }

      const subs = res.ok ? (data.subdirs || []) : [];
      if (subs.length === 0 && pickerSubdirs.children.length === 0) {
        const msg = document.createElement("span");
        msg.className   = "picker-no-items";
        msg.textContent = "No subfolders";
        pickerSubdirs.appendChild(msg);
      } else {
        subs.forEach(name => {
          const chip = document.createElement("button");
          chip.className   = "picker-subfolder-chip";
          chip.textContent = name;
          chip.title       = `Navigate into ${name}/`;
          chip.addEventListener("click", () => _refreshPickerNav(path + "/" + name));
          pickerSubdirs.appendChild(chip);
        });
      }

      // .toml files
      pickerConfigs.innerHTML = "";
      const configs = res.ok ? (data.configs || []) : [];
      if (configs.length === 0) {
        const msg = document.createElement("span");
        msg.className   = "picker-no-items";
        msg.textContent = "No .toml files here";
        pickerConfigs.appendChild(msg);
      } else {
        configs.forEach(name => {
          const chip = document.createElement("button");
          chip.className   = "picker-config-chip";
          chip.textContent = name;
          chip.title       = `Load ${name} as session config`;
          chip.addEventListener("click", () => _createSessionFromPath(path + "/" + name));
          pickerConfigs.appendChild(chip);
        });
      }
    } catch (err) {
      console.error("Picker nav error:", err);
      pickerSubdirs.innerHTML = '<span class="picker-no-items">Failed to load</span>';
    }
  }

  async function _createSessionFromPath(configPath) {
    _closeServerPicker();

    sessionDot.dataset.state  = "initializing";
    sessionLabel.textContent  = "Creating session…";
    sessionMeta.textContent   = configPath.split("/").pop();
    btnCreate.classList.add("hidden");
    btnSessionFromServer.classList.add("hidden");

    try {
      const res  = await fetch("/session/from-path", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ config_path: configPath }),
      });
      const text = await res.text();
      let data;
      try { data = JSON.parse(text); }
      catch {
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
      console.error("Create session from path error:", err);
      sessionDot.dataset.state = "error";
      sessionLabel.textContent = "Could not reach server";
      sessionMeta.textContent  = err.message || "";
      btnCreate.classList.remove("hidden");
    }
  }

  btnSessionFromServer.addEventListener("click", _openServerPicker);
  pickerCloseBtn.addEventListener("click", _closeServerPicker);

  // ── Actions card DOM refs ────────────────────────────────────
  export const folderSelect  = document.getElementById("folder-select");
  const progressTitle = document.getElementById("progress-title");
  const actionBtns    = document.querySelectorAll(".btn-action");

  const OPERATION_LABELS = {
    calibrate:                      "Calibrating cameras",
    filter_2d:                      "Filtering 2D predictions",
    triangulate:                    "Triangulating 3D poses",
    filter_3d:                      "Filtering 3D trajectories",
    organize_for_anipose:           "Organizing folders for Anipose",
    convert_mediapipe_to_dlc_csv:   "Converting MediaPipe → DLC CSV",
    convert_mediapipe_csv_to_h5:    "Converting CSV → H5",
    convert_3d_csv_to_mat:          "Converting 3D CSV → .mat",
  };

  const MEDIAPIPE_OPS = new Set([
    "organize_for_anipose",
    "convert_mediapipe_csv_to_h5",
    "convert_mediapipe_to_dlc_csv",
    "convert_3d_csv_to_mat",
  ]);

  // Ops that need frame_w / frame_h (but not necessarily scorer)
  const FRAME_DIMS_OPS = new Set([
    "convert_mediapipe_to_dlc_csv",
    "convert_3d_csv_to_mat",
  ]);

  // ── Pipeline mode toggle ─────────────────────────────────────
  const pipelineBtnMediapipe  = document.getElementById("pipeline-btn-mediapipe");
  const pipelineBtnDeeplabcut = document.getElementById("pipeline-btn-deeplabcut");
  const mediapipeExtras       = document.getElementById("mediapipe-extras");
  const scorerInput           = document.getElementById("scorer-input");
  const frameWInput           = document.getElementById("frame-w-input");
  const frameHInput           = document.getElementById("frame-h-input");
  const detectDimsBtn         = document.getElementById("detect-dims-btn");
  const detectDimsStatus      = document.getElementById("detect-dims-status");

  function _setPipelineMode(mode) {
    const isMediapipe = (mode === "mediapipe");
    pipelineBtnMediapipe.classList.toggle("active",  isMediapipe);
    pipelineBtnDeeplabcut.classList.toggle("active", !isMediapipe);
    mediapipeExtras.classList.toggle("hidden", !isMediapipe);
  }

  pipelineBtnMediapipe.addEventListener("click",  () => _setPipelineMode("mediapipe"));
  pipelineBtnDeeplabcut.addEventListener("click", () => _setPipelineMode("deeplabcut"));

  // ── Detect frame dimensions ──────────────────────────────────
  detectDimsBtn.addEventListener("click", async () => {
    if (!state.currentProjectId) {
      alert("Select a project folder first.");
      return;
    }

    detectDimsBtn.disabled       = true;
    detectDimsStatus.textContent = "Detecting…";
    detectDimsStatus.className   = "detect-dims-status";

    try {
      // Browse the project to find the first video file in any folder
      const rootParam = state.currentRoot ? `?root=${encodeURIComponent(state.currentRoot)}` : "";
      const browseRes  = await fetch(`/projects/${state.currentProjectId}/browse${rootParam}`);
      const browseData = await browseRes.json();

      let videoFile   = null;
      let videoFolder = null;
      if (browseData.folders) {
        for (const f of browseData.folders) {
          const vf = f.files.find(file => /\.(mp4|avi|mov|mkv|mpg|mpeg)$/i.test(file.name));
          if (vf) { videoFile = vf.name; videoFolder = f.folder; break; }
        }
      }

      if (!videoFile) {
        detectDimsStatus.textContent = "No video found in project";
        detectDimsStatus.className   = "detect-dims-status err";
        return;
      }

      const body = { folder: videoFolder, filename: videoFile };
      if (state.currentRoot) body.root = state.currentRoot;

      const dimRes  = await fetch(`/projects/${state.currentProjectId}/detect-frame-dims`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(body),
      });
      const dimData = await dimRes.json();

      if (!dimRes.ok) {
        detectDimsStatus.textContent = dimData.error || "Error";
        detectDimsStatus.className   = "detect-dims-status err";
      } else {
        frameWInput.value            = dimData.width;
        frameHInput.value            = dimData.height;
        detectDimsStatus.textContent = `✓ ${videoFile}`;
        detectDimsStatus.className   = "detect-dims-status ok";
        setTimeout(() => {
          detectDimsStatus.textContent = "";
          detectDimsStatus.className   = "detect-dims-status";
        }, 4000);
      }
    } catch (err) {
      console.error("detect-frame-dims error:", err);
      detectDimsStatus.textContent = "Network error";
      detectDimsStatus.className   = "detect-dims-status err";
    } finally {
      detectDimsBtn.disabled = false;
    }
  });

  // ── Config editor ────────────────────────────────────────────
  const configEditor      = document.getElementById("config-editor");
  const configPathDisplay = document.getElementById("config-path-display");
  const saveConfigBtn     = document.getElementById("save-config-btn");
  const configSaveStatus  = document.getElementById("config-save-status");

  async function loadConfig() {
    try {
      const res  = await fetch("/session/config");
      const data = await res.json();
      if (!res.ok) { console.error("loadConfig error:", data.error); return; }
      configEditor.value       = data.content;
      configPathDisplay.textContent = data.config_path;
      configSaveStatus.textContent  = "";
      configSaveStatus.className    = "config-save-status";
    } catch (err) {
      console.error("loadConfig fetch error:", err);
    }
  }

  saveConfigBtn.addEventListener("click", async () => {
    saveConfigBtn.disabled       = true;
    configSaveStatus.textContent = "Saving…";
    configSaveStatus.className   = "config-save-status";
    try {
      const res  = await fetch("/session/config", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ content: configEditor.value }),
      });
      const data = await res.json();
      if (!res.ok) {
        configSaveStatus.textContent = data.error || "Save failed";
        configSaveStatus.className   = "config-save-status err";
      } else {
        configSaveStatus.textContent = "Saved";
        configSaveStatus.className   = "config-save-status ok";
        setTimeout(() => {
          configSaveStatus.textContent = "";
          configSaveStatus.className   = "config-save-status";
        }, 3000);
      }
    } catch (err) {
      configSaveStatus.textContent = "Network error";
      configSaveStatus.className   = "config-save-status err";
    } finally {
      saveConfigBtn.disabled = false;
    }
  });

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
        const runBody = { operation, project_id: projectId };
        if (state.currentRoot) runBody.root = state.currentRoot;
        if (MEDIAPIPE_OPS.has(operation) && operation !== "convert_3d_csv_to_mat") {
          runBody.scorer = scorerInput.value.trim() || "User";
        }
        if (FRAME_DIMS_OPS.has(operation)) {
          const fw = parseInt(frameWInput.value, 10);
          const fh = parseInt(frameHInput.value, 10);
          if (!fw || !fh || fw <= 0 || fh <= 0) {
            alert("Enter valid frame width and height before converting.");
            actionBtns.forEach(b => { b.disabled = false; });
            return;
          }
          runBody.frame_w = fw;
          runBody.frame_h = fh;
        }
        const res  = await fetch("/run", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify(runBody),
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

  // ── New job ─────────────────────────────────────────────────
  // showProgress / pollStatus / operation-progress DOM refs live in dlc_project.js
  // (exported as showProgress, imported above).
  const newJobBtn = document.getElementById("new-job-btn");
  newJobBtn.addEventListener("click", () => {
    document.getElementById("operation-progress").classList.add("hidden");
    progressTitle.textContent = "Processing";  // reset title for next run
    actionBtns.forEach(b => { b.disabled = false; });
  });
