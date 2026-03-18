/* ─────────────────────────────────────────────────────────────
   Anipose Pipeline — Frontend Controller
   ───────────────────────────────────────────────────────────── */

(function () {
  "use strict";

  // ── Session DOM refs ────────────────────────────────────────
  const sessionDot   = document.getElementById("session-dot");
  const sessionLabel = document.getElementById("session-label");
  const sessionMeta  = document.getElementById("session-meta");
  const btnCreate             = document.getElementById("btn-create-session");
  const btnClear              = document.getElementById("btn-clear-session");
  const btnSessionFromServer  = document.getElementById("btn-session-from-server");
  const sessionInput          = document.getElementById("session-config-input");

  let sessionPollTimer = null;

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
    btnSessionFromServer.classList.toggle("hidden", !(isIdle && _userDataDir !== null));

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
      _initConfig().then(() => loadProjects(_currentRoot));
      loadConfig();
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
    // Pre-fetch /config so _userDataDir is set before applySessionState runs.
    // This ensures the "From server" button is shown even with no active session.
    try {
      const cfgRes  = await fetch("/config");
      const cfgData = await cfgRes.json();
      if (cfgData.user_data_dir) {
        _userDataDir = cfgData.user_data_dir;
        sourceBtnUserData.disabled = false;
        sourceBtnUserData.title    = `User data volume: ${cfgData.user_data_dir}`;
      }
      if (cfgData.data_dir) _dataDir = cfgData.data_dir;
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
    _refreshPickerNav(_userDataDir);
  }

  async function _refreshPickerNav(path) {

    // Breadcrumb
    const baseName = _userDataDir.split("/").filter(Boolean).pop() || "user-data";
    const rel = path.substring(_userDataDir.length).split("/").filter(Boolean);
    let crumbHTML = `<button class="picker-bc-seg" data-path="${_userDataDir}">${baseName}</button>`;
    let cumPath = _userDataDir;
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
      if (path !== _userDataDir) {
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
  const folderSelect  = document.getElementById("folder-select");
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
    if (!_currentProjectId) {
      alert("Select a project folder first.");
      return;
    }

    detectDimsBtn.disabled       = true;
    detectDimsStatus.textContent = "Detecting…";
    detectDimsStatus.className   = "detect-dims-status";

    try {
      // Browse the project to find the first video file in any folder
      const rootParam = _currentRoot ? `?root=${encodeURIComponent(_currentRoot)}` : "";
      const browseRes  = await fetch(`/projects/${_currentProjectId}/browse${rootParam}`);
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
      if (_currentRoot) body.root = _currentRoot;

      const dimRes  = await fetch(`/projects/${_currentProjectId}/detect-frame-dims`, {
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

  // ── DLC Project Manager ──────────────────────────────────────
  const dlcDot              = document.getElementById("dlc-dot");
  const dlcLabel            = document.getElementById("dlc-label");
  const dlcMeta             = document.getElementById("dlc-meta");
  const btnManageDlc        = document.getElementById("btn-manage-dlc");
  const btnDlcClear         = document.getElementById("btn-dlc-clear");

  const dlcProjectCard      = document.getElementById("dlc-project-card");
  const dlcFolderNav        = document.getElementById("dlc-folder-nav");
  const dlcFolderBreadcrumb = document.getElementById("dlc-folder-breadcrumb");
  const dlcFolderSubfolders = document.getElementById("dlc-folder-subfolders");
  const dlcBrowseBtn        = document.getElementById("dlc-browse-btn");
  const dlcBrowseInfo       = document.getElementById("dlc-browse-info");
  const dlcSelectBtn        = document.getElementById("dlc-select-btn");
  const dlcSelectStatus     = document.getElementById("dlc-select-status");
  const dlcPipelineSection  = document.getElementById("dlc-pipeline-section");
  const dlcNoConfigMsg      = document.getElementById("dlc-no-config-msg");
  const dlcFrameExtractLaunch = document.getElementById("dlc-frame-extract-launch");
  const dlcActivePath       = document.getElementById("dlc-active-path");
  const dlcPipelineFolders  = document.getElementById("dlc-pipeline-folders");
  const dlcRefreshBtn       = document.getElementById("dlc-refresh-btn");
  const dlcDownloadProjectBtn = document.getElementById("dlc-download-project-btn");

  let _dlcBrowsePath    = null;     // path currently browsed in the folder nav
  let _dlcEngine        = "pytorch"; // engine read from config.yaml when project is loaded
  let _dlcTrainingActive = false;   // true while a training job has status "running"

  // ── Apply DLC project state to bar + card ───────────────────
  function applyDlcProjectState(data) {
    if (!data || data.status === "none") {
      dlcDot.dataset.state = "none";
      dlcLabel.textContent = "No active DLC project";
      dlcMeta.textContent  = "";
      btnManageDlc.classList.remove("hidden");
      btnDlcClear.classList.add("hidden");
      dlcPipelineSection.classList.add("hidden");
      dlcNoConfigMsg.classList.add("hidden");
      dlcFrameExtractLaunch.classList.add("hidden");
    } else {
      dlcDot.dataset.state = "ready";
      dlcLabel.textContent = data.has_config ? "DLC project active" : "DLC project (no config.yaml)";
      dlcMeta.textContent  = data.project_name || "";
      _dlcEngine = (data.engine || "pytorch").toLowerCase();
      btnManageDlc.classList.add("hidden");
      btnDlcClear.classList.remove("hidden");

      // Show or hide pipeline section based on config presence
      if (data.has_config) {
        dlcActivePath.textContent = data.project_path || "";
        dlcPipelineSection.classList.remove("hidden");
        dlcNoConfigMsg.classList.add("hidden");
        dlcFrameExtractLaunch.classList.remove("hidden");
        _browseDlcPipeline();
      } else {
        dlcPipelineSection.classList.add("hidden");
        dlcNoConfigMsg.classList.remove("hidden");
        dlcFrameExtractLaunch.classList.add("hidden");
      }

      // Keep card open
      dlcProjectCard.classList.remove("hidden");
    }
  }

  // ── Open/close project manager card ─────────────────────────
  btnManageDlc.addEventListener("click", () => {
    dlcProjectCard.classList.remove("hidden");
    // Auto-open folder browser if user data is available
    if (_userDataDir && dlcFolderNav.classList.contains("hidden")) {
      dlcFolderNav.classList.remove("hidden");
      _refreshDlcFolderNav(_userDataDir);
    } else if (!_userDataDir) {
      dlcBrowseInfo.textContent = "No user data volume mounted";
      dlcBrowseInfo.className   = "dlc-browse-info err";
    }
  });

  // ── Browse user data button ──────────────────────────────────
  dlcBrowseBtn.addEventListener("click", () => {
    if (!_userDataDir) {
      dlcBrowseInfo.textContent = "No user data volume mounted";
      dlcBrowseInfo.className   = "dlc-browse-info err";
      return;
    }
    dlcFolderNav.classList.remove("hidden");
    _refreshDlcFolderNav(_dlcBrowsePath || _userDataDir);
  });

  // ── Folder navigator ──────────────────────────────────────────
  async function _refreshDlcFolderNav(path) {
    _dlcBrowsePath = path;

    // Breadcrumb
    const baseName = (_userDataDir || path).split("/").filter(Boolean).pop() || "user-data";
    const base     = _userDataDir || path;
    const rel      = path.substring(base.length).split("/").filter(Boolean);
    let crumbHTML  = `<button class="userdata-bc-seg" data-path="${base}">${baseName}</button>`;
    let cumPath    = base;
    rel.forEach((part, i) => {
      cumPath += "/" + part;
      const isLast = (i === rel.length - 1);
      crumbHTML += `<span class="userdata-bc-sep">›</span>`;
      crumbHTML += `<button class="userdata-bc-seg${isLast ? " active" : ""}" data-path="${cumPath}">${part}</button>`;
    });
    dlcFolderBreadcrumb.innerHTML = crumbHTML;
    dlcFolderBreadcrumb.querySelectorAll(".userdata-bc-seg").forEach(seg =>
      seg.addEventListener("click", () => _refreshDlcFolderNav(seg.dataset.path)));

    dlcFolderSubfolders.innerHTML = '<span class="userdata-no-folders">Loading…</span>';

    try {
      const res  = await fetch(`/fs/list?path=${encodeURIComponent(path)}`);
      const data = await res.json();

      dlcFolderSubfolders.innerHTML = "";

      // ".." chip
      if (path !== _userDataDir) {
        const upBtn = document.createElement("button");
        upBtn.className   = "userdata-subfolder-chip up";
        upBtn.textContent = "..";
        upBtn.title       = "Go up one level";
        upBtn.addEventListener("click", () => {
          const parent = path.split("/").slice(0, -1).join("/") || "/";
          _refreshDlcFolderNav(parent);
        });
        dlcFolderSubfolders.appendChild(upBtn);
      }

      const subs = res.ok ? (data.projects || []) : [];
      if (subs.length === 0 && dlcFolderSubfolders.children.length === 0) {
        const msg = document.createElement("span");
        msg.className   = "userdata-no-folders";
        msg.textContent = "No subfolders";
        dlcFolderSubfolders.appendChild(msg);
      } else {
        subs.forEach(name => {
          const chip = document.createElement("button");
          chip.className   = "userdata-subfolder-chip";
          chip.textContent = name;
          chip.title       = `Navigate into ${name}/`;
          chip.addEventListener("click", () => _refreshDlcFolderNav(path + "/" + name));
          dlcFolderSubfolders.appendChild(chip);
        });
      }
    } catch (err) {
      console.error("DLC folder nav error:", err);
      dlcFolderSubfolders.innerHTML = '<span class="userdata-no-folders">Failed to load</span>';
    }
  }

  // ── Select current folder as DLC project ────────────────────
  dlcSelectBtn.addEventListener("click", async () => {
    if (!_dlcBrowsePath) return;

    dlcSelectStatus.textContent = "Checking for config.yaml…";
    dlcSelectStatus.className   = "dlc-config-status";

    try {
      const res  = await fetch("/dlc/project", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ path: _dlcBrowsePath }),
      });
      const data = await res.json();
      if (!res.ok) {
        dlcSelectStatus.textContent = data.error || "Failed";
        dlcSelectStatus.className   = "dlc-config-status err";
      } else {
        dlcSelectStatus.textContent = data.has_config
          ? "✓ config.yaml found — pipeline ready"
          : "⚠ No config.yaml in this folder";
        dlcSelectStatus.className = data.has_config
          ? "dlc-config-status ok"
          : "dlc-config-status err";
        setTimeout(() => {
          dlcSelectStatus.textContent = "";
          dlcSelectStatus.className   = "dlc-config-status";
        }, 4000);
        applyDlcProjectState(data);
      }
    } catch (err) {
      console.error("DLC set project error:", err);
      dlcSelectStatus.textContent = "Network error";
      dlcSelectStatus.className   = "dlc-config-status err";
    }
  });

  // ── Clear DLC project ────────────────────────────────────────
  btnDlcClear.addEventListener("click", async () => {
    if (!confirm("Clear the DLC project session? The files on disk are not affected.")) return;
    try {
      await fetch("/dlc/project", { method: "DELETE" });
    } catch (err) {
      console.error("Clear DLC project error:", err);
    }
    applyDlcProjectState(null);
    dlcProjectCard.classList.add("hidden");
    dlcFolderNav.classList.add("hidden");
    _dlcBrowsePath = null;
  });

  // ── Browse DLC pipeline folders ──────────────────────────────
  async function _browseDlcPipeline() {
    dlcPipelineFolders.innerHTML = '<p class="explorer-empty" style="opacity:.5">Loading…</p>';
    try {
      const res  = await fetch("/dlc/project/browse");
      const data = await res.json();
      if (!res.ok) {
        dlcPipelineFolders.innerHTML = `<p class="explorer-empty">${data.error || "Error loading project"}</p>`;
        return;
      }
      const list = document.createElement("div");
      list.className = "folder-list";
      data.folders.forEach(entry => list.appendChild(_buildDlcFolderRow(entry)));
      dlcPipelineFolders.innerHTML = "";
      dlcPipelineFolders.appendChild(list);
    } catch (err) {
      console.error("browseDlcPipeline error:", err);
      dlcPipelineFolders.innerHTML = '<p class="explorer-empty">Failed to load project.</p>';
    }
  }

  // ── Count all files (recursively) in a children array ───────────
  function _countAllFiles(children) {
    let n = 0;
    for (const c of (children || [])) {
      if (c.type === "file") n++;
      else n += _countAllFiles(c.children);
    }
    return n;
  }

  // ── Build a tree node (file or subfolder) ─────────────────────
  function _buildDlcTreeNode(node) {
    if (node.type === "file") {
      const item = document.createElement("div");
      item.className = "file-item";
      item.innerHTML = `${_fileSvg()}<span class="file-item-name" title="${node.rel_path}">${node.name}</span><span class="file-size">${_fmtSize(node.size)}</span><button class="file-rename-btn" title="Rename">✎</button><button class="file-delete-btn" title="Delete">×</button>`;
      item.querySelector(".file-rename-btn").addEventListener("click", e => {
        e.stopPropagation();
        _activateDlcRename(item, node.name, node.rel_path);
      });
      item.querySelector(".file-delete-btn").addEventListener("click", e => {
        e.stopPropagation();
        _deleteDlcFile(node.name, node.rel_path);
      });
      return item;
    }

    // Directory node
    const subRow = document.createElement("div");
    subRow.className = "folder-row folder-subrow";

    const fileCount = _countAllFiles(node.children);
    const subHeader = document.createElement("div");
    subHeader.className = "folder-row-header";
    subHeader.innerHTML = `
      <span class="folder-chevron">▶</span>
      <span class="folder-icon">${_folderSvg("currentColor")}</span>
      <span class="folder-key" style="font-weight:500;font-style:normal">${node.name}</span>
      <span class="folder-badge ${fileCount > 0 ? "has-files" : ""}">${fileCount} file${fileCount !== 1 ? "s" : ""}</span>`;

    const subFiles = document.createElement("div");
    subFiles.className = "folder-files folder-subtree";
    if (!node.children || node.children.length === 0) {
      subFiles.innerHTML = '<p class="folder-empty-msg">Empty folder</p>';
    } else {
      node.children.forEach(child => subFiles.appendChild(_buildDlcTreeNode(child)));
    }

    subRow.appendChild(subHeader);
    subRow.appendChild(subFiles);
    subHeader.addEventListener("click", () => subRow.classList.toggle("open"));
    return subRow;
  }

  // ── Build a DLC pipeline folder row ──────────────────────────
  function _buildDlcFolderRow(entry) {
    const { key, folder, children, exists } = entry;
    const fileCount = _countAllFiles(children);

    const row = document.createElement("div");
    row.className = "folder-row";
    row.dataset.folder = folder;

    const header = document.createElement("div");
    header.className = "folder-row-header";
    header.innerHTML = `
      <span class="folder-chevron">▶</span>
      <span class="folder-icon">${_folderSvg("currentColor")}</span>
      <span class="folder-key">${key}</span>
      <span class="folder-name-chip">${folder}</span>
      <span class="folder-badge ${fileCount > 0 ? "has-files" : ""}">${fileCount} file${fileCount !== 1 ? "s" : ""}</span>
      <span class="folder-upload-status"></span>
      <label class="folder-upload-label" title="Upload files to ${folder}/">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>
        Upload
        <input type="file" multiple />
      </label>
      <button class="folder-download-btn" title="Download ${folder}/ as ZIP">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="5 12 12 19 19 12"/></svg>
      </button>`;

    const fileList = document.createElement("div");
    fileList.className = "folder-files";
    if (!children || children.length === 0) {
      fileList.innerHTML = `<p class="folder-empty-msg">${exists ? "Empty folder" : "Folder not yet created"}</p>`;
    } else {
      children.forEach(child => fileList.appendChild(_buildDlcTreeNode(child)));
    }

    row.appendChild(header);
    row.appendChild(fileList);

    header.addEventListener("click", e => {
      if (e.target.closest("label")) return;
      if (e.target.closest(".folder-download-btn")) return;
      row.classList.toggle("open");
    });

    header.querySelector(".folder-download-btn").addEventListener("click", e => {
      e.stopPropagation();
      window.location.href = `/dlc/project/download?folder=${encodeURIComponent(folder)}`;
    });

    row.addEventListener("dragover",  e => { e.preventDefault(); row.classList.add("dragover"); });
    row.addEventListener("dragleave", ()  => row.classList.remove("dragover"));
    row.addEventListener("drop", async e => {
      e.preventDefault();
      row.classList.remove("dragover");
      const dropped = Array.from(e.dataTransfer.files);
      if (dropped.length) await _uploadDlcFiles(dropped, folder, row);
    });

    const fileInput = header.querySelector("input[type='file']");
    fileInput.addEventListener("change", async () => {
      if (!fileInput.files.length) return;
      await _uploadDlcFiles(Array.from(fileInput.files), folder, row);
      fileInput.value = "";
    });

    return row;
  }

  async function _uploadDlcFiles(files, folder, row) {
    const statusEl = row.querySelector(".folder-upload-status");
    statusEl.textContent = "Uploading…";
    statusEl.className   = "folder-upload-status";

    const fd = new FormData();
    fd.append("folder", folder);
    files.forEach(f => fd.append("files[]", f));

    try {
      const res  = await fetch("/dlc/project/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) {
        statusEl.textContent = data.error || "Upload failed";
        statusEl.className   = "folder-upload-status err";
      } else {
        statusEl.textContent = `✓ ${data.saved.length} uploaded`;
        statusEl.className   = "folder-upload-status ok";
        setTimeout(() => { statusEl.textContent = ""; statusEl.className = "folder-upload-status"; }, 3000);
        _browseDlcPipeline();
      }
    } catch (err) {
      statusEl.textContent = "Network error";
      statusEl.className   = "folder-upload-status err";
    }
  }

  async function _deleteDlcFile(filename, relPath) {
    if (!confirm(`Delete "${filename}"? This cannot be undone.`)) return;
    try {
      const res  = await fetch("/dlc/project/file", {
        method:  "DELETE",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ rel_path: relPath }),
      });
      const data = await res.json();
      if (!res.ok) alert(data.error || "Delete failed.");
      else _browseDlcPipeline();
    } catch (err) {
      alert("Network error.");
    }
  }

  function _activateDlcRename(item, oldName, relPath) {
    const nameSpan  = item.querySelector(".file-item-name");
    const sizeSpan  = item.querySelector(".file-size");
    const renameBtn = item.querySelector(".file-rename-btn");
    const deleteBtn = item.querySelector(".file-delete-btn");

    const input = document.createElement("input");
    input.type      = "text";
    input.className = "file-rename-input";
    input.value     = oldName;

    const confirmBtn = document.createElement("button");
    confirmBtn.className   = "file-rename-confirm";
    confirmBtn.title       = "Confirm rename";
    confirmBtn.textContent = "✓";

    const cancelBtn = document.createElement("button");
    cancelBtn.className   = "file-rename-cancel";
    cancelBtn.title       = "Cancel";
    cancelBtn.textContent = "×";

    nameSpan.replaceWith(input);
    sizeSpan.style.display  = "none";
    renameBtn.style.display = "none";
    deleteBtn.style.display = "none";
    item.appendChild(confirmBtn);
    item.appendChild(cancelBtn);
    input.focus();
    input.select();

    async function doRename() {
      const newName = input.value.trim();
      if (!newName || newName === oldName) { _browseDlcPipeline(); return; }
      try {
        const res  = await fetch("/dlc/project/file", {
          method:  "PATCH",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ rel_path: relPath, new_name: newName }),
        });
        const data = await res.json();
        if (!res.ok) alert(data.error || "Rename failed.");
      } catch (err) {
        alert("Network error.");
      }
      _browseDlcPipeline();
    }

    confirmBtn.addEventListener("click", doRename);
    cancelBtn.addEventListener("click",  _browseDlcPipeline);
    input.addEventListener("keydown", e => {
      if (e.key === "Enter")  doRename();
      if (e.key === "Escape") _browseDlcPipeline();
    });
  }

  dlcRefreshBtn.addEventListener("click", _browseDlcPipeline);
  dlcDownloadProjectBtn.addEventListener("click", () => {
    window.location.href = "/dlc/project/download";
  });

  // ── Populate project folder dropdowns ───────────────────────
  const explorerFolderSelect = document.getElementById("explorer-folder-select");
  const sourceBtnLocal       = document.getElementById("source-btn-local");
  const sourceBtnUserData    = document.getElementById("source-btn-userdata");
  const userDataNav          = document.getElementById("userdata-nav");
  const userDataBreadcrumb   = document.getElementById("userdata-breadcrumb");
  const userDataSubfolders   = document.getElementById("userdata-subfolders");

  let _currentRoot = "";   // "" = DATA_DIR; non-empty = current browse path
  let _userDataDir = null; // base mount path, populated from /config
  let _dataDir     = null; // server DATA_DIR, populated from /config

  async function loadProjects(root) {
    try {
      const url = root
        ? `/fs/list?path=${encodeURIComponent(root)}`
        : "/projects";
      const res  = await fetch(url);
      const data = await res.json();
      if (!res.ok) return false;
      // Exclude session_ dirs — they hold config only, not project data
      const projects = (data.projects || []).filter(p => !p.startsWith("session_"));
      const opts = '<option value="">— select a project —</option>' +
        projects.map(p => `<option value="${p}">${p}</option>`).join("");
      folderSelect.innerHTML         = opts;
      explorerFolderSelect.innerHTML = opts;
      return true;
    } catch (err) {
      console.error("loadProjects error:", err);
      return false;
    }
  }

  // ── User-data folder navigator ────────────────────────────────
  async function _refreshUserDataNav(path) {
    _currentRoot = path;

    // Render breadcrumb
    const baseName = _userDataDir.split("/").filter(Boolean).pop() || "user-data";
    const rel = path.substring(_userDataDir.length).split("/").filter(Boolean);
    let crumbHTML = `<button class="userdata-bc-seg" data-path="${_userDataDir}">${baseName}</button>`;
    let cumPath = _userDataDir;
    rel.forEach((part, i) => {
      cumPath += "/" + part;
      const isLast = (i === rel.length - 1);
      crumbHTML += `<span class="userdata-bc-sep">›</span>`;
      crumbHTML += `<button class="userdata-bc-seg${isLast ? " active" : ""}" data-path="${cumPath}">${part}</button>`;
    });
    userDataBreadcrumb.innerHTML = crumbHTML;
    userDataBreadcrumb.querySelectorAll(".userdata-bc-seg").forEach(seg => {
      seg.addEventListener("click", async () => {
        if (seg.dataset.path === _currentRoot) return;
        _onProjectSelected("");
        await _refreshUserDataNav(seg.dataset.path);
        await loadProjects(_currentRoot);
      });
    });

    // Render subfolder chips
    userDataSubfolders.innerHTML = "";

    // ".." chip when not at the volume root
    if (path !== _userDataDir) {
      const upBtn = document.createElement("button");
      upBtn.className   = "userdata-subfolder-chip up";
      upBtn.textContent = "..";
      upBtn.title       = "Go up one level";
      const parent = path.split("/").slice(0, -1).join("/") || "/";
      upBtn.addEventListener("click", async () => {
        _onProjectSelected("");
        await _refreshUserDataNav(parent);
        await loadProjects(_currentRoot);
      });
      userDataSubfolders.appendChild(upBtn);
    }

    try {
      const res  = await fetch(`/fs/list?path=${encodeURIComponent(path)}`);
      const data = await res.json();
      const subs = res.ok ? (data.projects || []) : [];
      if (subs.length === 0) {
        const msg = document.createElement("span");
        msg.className   = "userdata-no-folders";
        msg.textContent = "No subfolders";
        userDataSubfolders.appendChild(msg);
      } else {
        subs.forEach(name => {
          const chip = document.createElement("button");
          chip.className   = "userdata-subfolder-chip";
          chip.textContent = name;
          chip.title       = `Navigate into ${name}/`;
          const newPath    = path + "/" + name;
          chip.addEventListener("click", async () => {
            _onProjectSelected("");
            await _refreshUserDataNav(newPath);
            await loadProjects(_currentRoot);
          });
          userDataSubfolders.appendChild(chip);
        });
      }
    } catch (err) {
      console.error("userdata nav error:", err);
      const msg = document.createElement("span");
      msg.className   = "userdata-no-folders";
      msg.textContent = "Failed to load";
      userDataSubfolders.appendChild(msg);
    }
  }

  // ── Source selector buttons ───────────────────────────────────
  async function _selectSource(root) {
    sourceBtnLocal.classList.toggle("active",    root === "");
    sourceBtnUserData.classList.toggle("active", root !== "");
    _onProjectSelected("");
    if (root === "") {
      userDataNav.classList.add("hidden");
      _currentRoot = "";
      await loadProjects("");
    } else {
      userDataNav.classList.remove("hidden");
      await _refreshUserDataNav(root);
      await loadProjects(_currentRoot);
    }
  }

  sourceBtnLocal.addEventListener("click", () => _selectSource(""));
  sourceBtnUserData.addEventListener("click", () => {
    if (_userDataDir) _selectSource(_userDataDir);
  });

  // Fetch /config to learn the user-data path and enable buttons
  async function _initConfig() {
    try {
      const res  = await fetch("/config");
      const data = await res.json();
      if (data.user_data_dir) {
        _userDataDir = data.user_data_dir;
        sourceBtnUserData.disabled = false;
        sourceBtnUserData.title    = `User data volume: ${data.user_data_dir}`;
      }
    } catch (err) {
      console.error("Config fetch error:", err);
    }
  }

  // ── Project Explorer ─────────────────────────────────────────
  const explorerFolders       = document.getElementById("explorer-folders");
  const explorerProjectActions= document.getElementById("explorer-project-actions");
  const refreshExplorerBtn    = document.getElementById("refresh-explorer-btn");
  const downloadProjectBtn    = document.getElementById("download-project-btn");
  const newProjectNameInput   = document.getElementById("new-project-name");
  const createProjectBtn    = document.getElementById("create-project-btn");
  const createProjectStatus = document.getElementById("create-project-status");

  async function createProject() {
    const name = newProjectNameInput.value.trim();
    if (!name) { newProjectNameInput.focus(); return; }

    createProjectBtn.disabled    = true;
    createProjectStatus.textContent = "Creating…";
    createProjectStatus.className   = "create-project-status";

    try {
      const body = { name };
      if (_currentRoot) body.root = _currentRoot;
      const res  = await fetch("/projects", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        createProjectStatus.textContent = data.error || "Failed";
        createProjectStatus.className   = "create-project-status err";
      } else {
        createProjectStatus.textContent = `✓ ${data.folders_created.length} folders created`;
        createProjectStatus.className   = "create-project-status ok";
        newProjectNameInput.value = "";
        // Refresh dropdowns then select + browse the new project
        await loadProjects(_currentRoot);
        _onProjectSelected(data.project_id);
        setTimeout(() => {
          createProjectStatus.textContent = "";
          createProjectStatus.className   = "create-project-status";
        }, 3000);
      }
    } catch (err) {
      createProjectStatus.textContent = "Network error";
      createProjectStatus.className   = "create-project-status err";
    } finally {
      createProjectBtn.disabled = false;
    }
  }

  createProjectBtn.addEventListener("click", createProject);
  newProjectNameInput.addEventListener("keydown", e => { if (e.key === "Enter") createProject(); });

  // Sync helpers — keep both selects identical and trigger browse
  let _currentProjectId = "";
  function _onProjectSelected(pid) {
    folderSelect.value         = pid;
    explorerFolderSelect.value = pid;
    _currentProjectId          = pid;
    explorerProjectActions.classList.toggle("hidden", !pid);
    if (pid) browseProject(pid);
    else explorerFolders.innerHTML = '<p class="explorer-empty">Select or create a project to browse its pipeline folders.</p>';
  }

  refreshExplorerBtn.addEventListener("click", () => {
    if (_currentProjectId) browseProject(_currentProjectId);
  });

  downloadProjectBtn.addEventListener("click", () => {
    if (!_currentProjectId) return;
    const rootParam = _currentRoot ? `?root=${encodeURIComponent(_currentRoot)}` : "";
    window.location.href = `/projects/${_currentProjectId}/download${rootParam}`;
  });

  explorerFolderSelect.addEventListener("change", () => _onProjectSelected(explorerFolderSelect.value));
  folderSelect.addEventListener("change",         () => _onProjectSelected(folderSelect.value));

  function _fmtSize(bytes) {
    if (bytes == null) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  }

  function _folderSvg(color) {
    return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`;
  }

  function _fileSvg() {
    return `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>`;
  }

  function _buildFolderRow(entry, projectId) {
    const { key, folder, files, exists } = entry;
    const count = files.length;

    const row = document.createElement("div");
    row.className = "folder-row";
    row.dataset.folder = folder;

    // ── header ──
    const header = document.createElement("div");
    header.className = "folder-row-header";
    header.innerHTML = `
      <span class="folder-chevron">▶</span>
      <span class="folder-icon">${_folderSvg("currentColor")}</span>
      <span class="folder-key">${key}</span>
      <span class="folder-name-chip">${folder}</span>
      <span class="folder-badge ${count > 0 ? "has-files" : ""}">${count} file${count !== 1 ? "s" : ""}</span>
      <span class="folder-upload-status"></span>
      <label class="folder-upload-label" title="Upload files to ${folder}/">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>
        Upload
        <input type="file" multiple />
      </label>
      <button class="folder-download-btn" title="Download ${folder}/ as ZIP">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="5 12 12 19 19 12"/></svg>
      </button>`;

    // ── file list ──
    const fileList = document.createElement("div");
    fileList.className = "folder-files";
    if (files.length === 0) {
      fileList.innerHTML = `<p class="folder-empty-msg">${exists ? "Empty folder" : "Folder not yet created"}</p>`;
    } else {
      files.forEach(f => {
        const item = document.createElement("div");
        item.className = "file-item";
        item.innerHTML = `${_fileSvg()}<span class="file-item-name">${f.name}</span><span class="file-size">${_fmtSize(f.size)}</span><button class="file-rename-btn" title="Rename ${f.name}">✎</button><button class="file-delete-btn" title="Delete ${f.name}">×</button>`;
        item.querySelector(".file-rename-btn").addEventListener("click", e => {
          e.stopPropagation();
          _activateRename(item, f.name, folder, projectId);
        });
        item.querySelector(".file-delete-btn").addEventListener("click", e => {
          e.stopPropagation();
          _deleteFile(f.name, folder, projectId);
        });
        fileList.appendChild(item);
      });
    }

    row.appendChild(header);
    row.appendChild(fileList);

    // Toggle expand
    header.addEventListener("click", e => {
      if (e.target.closest("label")) return;              // let upload label handle its own click
      if (e.target.closest(".folder-download-btn")) return; // handled separately
      row.classList.toggle("open");
    });

    // Folder download
    header.querySelector(".folder-download-btn").addEventListener("click", e => {
      e.stopPropagation();
      const rootParam = _currentRoot ? `&root=${encodeURIComponent(_currentRoot)}` : "";
      window.location.href = `/projects/${projectId}/download?folder=${encodeURIComponent(folder)}${rootParam}`;
    });

    // Drag-drop onto the row
    row.addEventListener("dragover", e => { e.preventDefault(); row.classList.add("dragover"); });
    row.addEventListener("dragleave", ()  => row.classList.remove("dragover"));
    row.addEventListener("drop", async e => {
      e.preventDefault();
      row.classList.remove("dragover");
      const droppedFiles = Array.from(e.dataTransfer.files);
      if (droppedFiles.length) await _uploadFiles(droppedFiles, folder, projectId, row);
    });

    // File input change → upload
    const fileInput = header.querySelector("input[type='file']");
    fileInput.addEventListener("change", async () => {
      if (!fileInput.files.length) return;
      await _uploadFiles(Array.from(fileInput.files), folder, projectId, row);
      fileInput.value = "";
    });

    return row;
  }

  async function _uploadFiles(files, folder, projectId, row) {
    const statusEl = row.querySelector(".folder-upload-status");
    statusEl.textContent = "Uploading…";
    statusEl.className   = "folder-upload-status";

    const fd = new FormData();
    fd.append("folder", folder);
    if (_currentRoot) fd.append("root", _currentRoot);
    files.forEach(f => fd.append("files[]", f));

    try {
      const res  = await fetch(`/projects/${projectId}/upload`, { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) {
        statusEl.textContent = data.error || "Upload failed";
        statusEl.className   = "folder-upload-status err";
      } else {
        statusEl.textContent = `✓ ${data.saved.length} uploaded`;
        statusEl.className   = "folder-upload-status ok";
        setTimeout(() => { statusEl.textContent = ""; statusEl.className = "folder-upload-status"; }, 3000);
        // Refresh this project's explorer
        browseProject(projectId);
      }
    } catch (err) {
      statusEl.textContent = "Network error";
      statusEl.className   = "folder-upload-status err";
    }
  }

  function _activateRename(item, oldName, folder, projectId) {
    const nameSpan  = item.querySelector(".file-item-name");
    const sizeSpan  = item.querySelector(".file-size");
    const renameBtn = item.querySelector(".file-rename-btn");
    const deleteBtn = item.querySelector(".file-delete-btn");

    const input = document.createElement("input");
    input.type      = "text";
    input.className = "file-rename-input";
    input.value     = oldName;

    const confirmBtn = document.createElement("button");
    confirmBtn.className = "file-rename-confirm";
    confirmBtn.title     = "Confirm rename";
    confirmBtn.textContent = "✓";

    const cancelBtn = document.createElement("button");
    cancelBtn.className  = "file-rename-cancel";
    cancelBtn.title      = "Cancel";
    cancelBtn.textContent = "×";

    nameSpan.replaceWith(input);
    sizeSpan.style.display  = "none";
    renameBtn.style.display = "none";
    deleteBtn.style.display = "none";
    item.appendChild(confirmBtn);
    item.appendChild(cancelBtn);
    input.focus();
    input.select();

    async function doRename() {
      const newName = input.value.trim();
      if (!newName || newName === oldName) { browseProject(projectId); return; }
      await _renameFile(oldName, newName, folder, projectId);
    }

    confirmBtn.addEventListener("click", doRename);
    cancelBtn.addEventListener("click",  () => browseProject(projectId));
    input.addEventListener("keydown", e => {
      if (e.key === "Enter")  doRename();
      if (e.key === "Escape") browseProject(projectId);
    });
  }

  async function _renameFile(oldName, newName, folder, projectId) {
    try {
      const body = { folder, old_name: oldName, new_name: newName };
      if (_currentRoot) body.root = _currentRoot;
      const res  = await fetch(`/projects/${projectId}/file`, {
        method:  "PATCH",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) alert(data.error || "Rename failed.");
    } catch (err) {
      console.error("renameFile error:", err);
      alert("Network error.");
    }
    browseProject(projectId);
  }

  async function _deleteFile(filename, folder, projectId) {
    if (!confirm(`Delete "${filename}" from ${folder}/? This cannot be undone.`)) return;
    try {
      const body = { folder, filename };
      if (_currentRoot) body.root = _currentRoot;
      const res  = await fetch(`/projects/${projectId}/file`, {
        method:  "DELETE",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        alert(data.error || "Delete failed.");
      } else {
        browseProject(projectId);
      }
    } catch (err) {
      console.error("deleteFile error:", err);
      alert("Network error.");
    }
  }

  async function browseProject(projectId) {
    explorerFolders.innerHTML = '<p class="explorer-empty" style="opacity:.5">Loading…</p>';
    try {
      const rootParam = _currentRoot ? `?root=${encodeURIComponent(_currentRoot)}` : "";
      const res  = await fetch(`/projects/${projectId}/browse${rootParam}`);
      const data = await res.json();
      if (!res.ok) {
        explorerFolders.innerHTML = `<p class="explorer-empty">${data.error || "Error loading project"}</p>`;
        return;
      }
      const list = document.createElement("div");
      list.className = "folder-list";
      data.folders.forEach(entry => list.appendChild(_buildFolderRow(entry, projectId)));
      explorerFolders.innerHTML = "";
      explorerFolders.appendChild(list);
    } catch (err) {
      console.error("browseProject error:", err);
      explorerFolders.innerHTML = '<p class="explorer-empty">Failed to load project.</p>';
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
        const runBody = { operation, project_id: projectId };
        if (_currentRoot) runBody.root = _currentRoot;
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

  // ── Operation progress DOM refs ──────────────────────────────
  const operationProgress = document.getElementById("operation-progress");
  const progressBar       = document.getElementById("progress-bar");
  const progressPct       = document.getElementById("progress-pct");
  const progressStage     = document.getElementById("progress-stage");
  const taskIdDisplay     = document.getElementById("task-id-display");
  const logOutput         = document.getElementById("log-output");
  const newJobBtn         = document.getElementById("new-job-btn");

  let pollTimer = null;

  // ── Show operation progress (inline in actions card) ────────
  function showProgress(taskId) {
    operationProgress.classList.remove("hidden");
    operationProgress.classList.remove("state-success", "state-fail");
    taskIdDisplay.textContent = taskId.slice(0, 12) + "…";
    progressBar.style.width = "0%";
    progressPct.textContent = "0 %";
    progressStage.textContent = "Queued";
    logOutput.textContent = "Waiting for output…";
    newJobBtn.classList.add("hidden");

    pollTimer = setInterval(() => pollStatus(taskId), 2000);
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
        operationProgress.classList.add("state-success");
        progressStage.textContent = "✓ Complete";
        progressBar.style.width = "100%";
        progressPct.textContent = "100 %";
        if (data.result && data.result.log) {
          logOutput.textContent = data.result.log;
        }
        newJobBtn.classList.remove("hidden");
        if (_currentProjectId) browseProject(_currentProjectId);
      }

      if (data.state === "FAILURE") {
        clearInterval(pollTimer);
        operationProgress.classList.add("state-fail");
        progressStage.textContent = "✗ " + (data.error || "Failed");
        logOutput.textContent = data.error || "An unknown error occurred.";
        newJobBtn.classList.remove("hidden");
        if (_currentProjectId) browseProject(_currentProjectId);
      }

    } catch (err) {
      console.error("Polling error:", err);
    }
  }

  // ── New job ─────────────────────────────────────────────────
  newJobBtn.addEventListener("click", () => {
    operationProgress.classList.add("hidden");
    progressTitle.textContent = "Processing";  // reset title for next run
    actionBtns.forEach(b => { b.disabled = false; });
  });

  // ── Frame Extractor ──────────────────────────────────────────
  (function () {
    const feCard          = document.getElementById("frame-extractor-card");
    const feOpenBtn       = document.getElementById("btn-open-frame-extractor");
    const feCloseBtn      = document.getElementById("btn-close-frame-extractor");
    const feBtnProject    = document.getElementById("fe-btn-from-project");
    const feBtnUpload     = document.getElementById("fe-btn-upload");
    const feBtnServer     = document.getElementById("fe-btn-server");
    const feProjectVids   = document.getElementById("fe-project-videos");
    const feVideoList     = document.getElementById("fe-video-list");
    const feUploadSec     = document.getElementById("fe-upload-section");
    const feFileInput     = document.getElementById("fe-video-file-input");
    const feUploadStatus  = document.getElementById("fe-upload-status");
    const feServerSec     = document.getElementById("fe-server-section");
    const feServerBrowser = document.getElementById("fe-server-browser");
    const feServerStatus  = document.getElementById("fe-server-status");
    const fePlayerSec     = document.getElementById("fe-player-section");
    const feCanvas        = document.getElementById("fe-canvas");
    const feBtnPlay       = document.getElementById("fe-btn-play");
    const fePlayIcon      = document.getElementById("fe-play-icon");
    const fePauseIcon     = document.getElementById("fe-pause-icon");
    const feBtnPrev       = document.getElementById("fe-btn-prev");
    const feBtnNext       = document.getElementById("fe-btn-next");
    const feFrameCounter  = document.getElementById("fe-frame-counter");
    const feFrameJump     = document.getElementById("fe-frame-jump");
    const feTimeDisplay   = document.getElementById("fe-time-display");
    const feSeek          = document.getElementById("fe-seek");
    const feBtnExtract    = document.getElementById("fe-btn-extract");
    const feBtnStopExtract = document.getElementById("fe-btn-stop-extract");
    const feBatchCountInput = document.getElementById("fe-batch-count");
    const feBatchStepInput  = document.getElementById("fe-batch-step");
    const feBtnBatchExtract = document.getElementById("fe-btn-batch-extract");
    const feExtractDialog = document.getElementById("fe-extract-dialog");
    const feDialogMsg     = document.getElementById("fe-dialog-msg");
    const feDialogConfirm = document.getElementById("fe-dialog-confirm");
    const feDialogCustomBtn = document.getElementById("fe-dialog-custom-btn");
    const feDialogCustomWrap = document.getElementById("fe-dialog-custom-wrap");
    const feDialogCustomInput = document.getElementById("fe-dialog-custom-input");
    const feDialogCancel  = document.getElementById("fe-dialog-cancel");
    const feExtractCount  = document.getElementById("fe-extract-count");
    const feExtractStatus = document.getElementById("fe-extract-status");
    const feCsvBars       = document.getElementById("fe-csv-bars");
    const feStatusBarWrap = document.getElementById("fe-status-bar-wrap");
    const feNoteBarWrap   = document.getElementById("fe-note-bar-wrap");
    const feStatusBar     = document.getElementById("fe-status-bar");
    const feNoteBar       = document.getElementById("fe-note-bar");
    const feVideoWrap     = document.getElementById("fe-video-wrap");
    const feFrameImg      = document.getElementById("fe-frame-img");
    const feFrameSpinner  = document.getElementById("fe-frame-spinner");
    const feZoomInput     = document.getElementById("fe-zoom");
    const feZoomVal       = document.getElementById("fe-zoom-val");
    const feStatusBefore   = document.getElementById("fe-status-before");
    const feStatusAfter    = document.getElementById("fe-status-after");
    const feStatusApply    = document.getElementById("fe-status-apply");
    const feStatusTags     = document.getElementById("fe-status-tags");
    const feStatusNav      = document.getElementById("fe-status-nav");
    const feStatusPrevBtn  = document.getElementById("fe-status-prev");
    const feStatusNextBtn  = document.getElementById("fe-status-next");
    const feStatusNavInfo  = document.getElementById("fe-status-nav-info");
    const feNoteBefore     = document.getElementById("fe-note-before");
    const feNoteAfter      = document.getElementById("fe-note-after");
    const feNoteApply      = document.getElementById("fe-note-apply");
    const feNoteTags       = document.getElementById("fe-note-tags");
    const feNoteNav        = document.getElementById("fe-note-nav");
    const feNotePrevBtn    = document.getElementById("fe-note-prev");
    const feNoteNextBtn    = document.getElementById("fe-note-next");
    const feNoteNavInfo    = document.getElementById("fe-note-nav-info");

    let _feZoom         = 100;
    let _feFps          = 30;
    let _feCsvRows      = [];
    let _feFrameCount   = 0;
    let _feStatusRuns          = [];
    let _feNoteRuns            = [];
    let _feStatusEffectiveRuns = [];
    let _feNoteEffectiveRuns   = [];
    let _feStatusColorMap = {};
    let _feNoteColorMap   = {};
    let _feStatusActiveTag = null;
    let _feNoteActiveTag   = null;
    let _feStatusSegIdx    = 0;
    let _feNoteSegIdx      = 0;
    let _feReRenderStatus = null;
    let _feReRenderNote   = null;
    let _feCurrentVideo    = null;
    let _feCurrentVideoExt = false;  // true when video is an external abs path
    let _feExtracted    = 0;
    let _feSeekDragging = false;
    let _feCurrentFrame = 0;
    let _feFrameBusy    = false;
    let _fePlayTimer    = null;
    let _feStopExtraction = false;

    // ── Viewer sizing (can break out of card borders) ─────────────
    function _feFitViewer() {
      if (!feFrameImg.naturalWidth) return;
      const cs      = getComputedStyle(feCard);
      const padL    = parseFloat(cs.paddingLeft)  || 0;
      const padR    = parseFloat(cs.paddingRight) || 0;
      const baseW   = feCard.clientWidth - padL - padR;
      const maxW    = Math.max(baseW, window.innerWidth - 32);
      const targetW = Math.min(Math.round(baseW * (_feZoom / 100)), Math.floor(maxW));
      const extra   = targetW - baseW;
      feVideoWrap.style.width      = targetW + "px";
      feVideoWrap.style.marginLeft = extra > 0 ? `-${extra / 2}px` : "";
    }
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => { if (feFrameImg.naturalWidth) _feFitViewer(); }).observe(feCard);
    }
    feZoomInput.addEventListener("input", () => {
      _feZoom = parseInt(feZoomInput.value, 10);
      feZoomVal.textContent = _feZoom + " %";
      _feFitViewer();
    });

    function _feFrameUrl(n) {
      if (_feCurrentVideoExt)
        return `/dlc/project/video-frame-ext/${n}?path=${encodeURIComponent(_feCurrentVideo)}`;
      return `/dlc/project/video-frame/${encodeURIComponent(_feCurrentVideo)}/${n}`;
    }

    function _fePrefetch(frames) {
      frames.forEach(n => {
        if (n >= 0 && n < _feFrameCount) new Image().src = _feFrameUrl(n);
      });
    }

    // ── Open / close ─────────────────────────────────────────────
    feOpenBtn.addEventListener("click", () => {
      feCard.classList.remove("hidden");
      feCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      _feLoadProjectVideos();
    });

    feCloseBtn.addEventListener("click", () => {
      feCard.classList.add("hidden");
      _feReset();
    });

    function _feReset() {
      _feCurrentVideo    = null;
      _feCurrentVideoExt = false;
      _feCurrentFrame = 0;
      _feFrameBusy    = false;
      _feCsvRows      = [];
      _feStatusRuns = []; _feNoteRuns = [];
      _feStatusEffectiveRuns = []; _feNoteEffectiveRuns = [];
      _feStatusColorMap = {}; _feNoteColorMap = {};
      _feStatusActiveTag = null; _feNoteActiveTag = null;
      _feStatusSegIdx = 0; _feNoteSegIdx = 0;
      _feReRenderStatus = null; _feReRenderNote = null;
      feStatusNav.classList.add("hidden");
      feNoteNav.classList.add("hidden");
      if (_fePlayTimer) { clearInterval(_fePlayTimer); _fePlayTimer = null; }
      fePlayIcon.classList.remove("hidden"); fePauseIcon.classList.add("hidden");
      feFrameImg.onload  = null;
      feFrameImg.onerror = null;
      if (feFrameImg.src.startsWith("blob:")) URL.revokeObjectURL(feFrameImg.src);
      feFrameImg.removeAttribute("src");
      feFrameSpinner.classList.add("hidden");
      fePlayerSec.classList.add("hidden");
      feCsvBars.classList.add("hidden");
      feStatusBarWrap.classList.add("hidden");
      feNoteBarWrap.classList.add("hidden");
      _feZoom = 100; feZoomInput.value = "100"; feZoomVal.textContent = "100 %";
      feVideoWrap.style.width = ""; feVideoWrap.style.marginLeft = "";
    }

    // ── Source toggle ─────────────────────────────────────────────
    function _feShowSource(active) {
      [feBtnProject, feBtnUpload, feBtnServer].forEach(b => b.classList.remove("active"));
      [feProjectVids, feUploadSec, feServerSec].forEach(s => s.classList.add("hidden"));
      active.btn.classList.add("active");
      active.sec.classList.remove("hidden");
    }

    feBtnProject.addEventListener("click", () => {
      _feShowSource({ btn: feBtnProject, sec: feProjectVids });
      _feLoadProjectVideos();
    });

    feBtnUpload.addEventListener("click", () => {
      _feShowSource({ btn: feBtnUpload, sec: feUploadSec });
    });

    feBtnServer.addEventListener("click", () => {
      _feShowSource({ btn: feBtnServer, sec: feServerSec });
      const startPath = _userDataDir || "/";
      _feBrowseServerDir(startPath);
    });

    // ── List project videos ───────────────────────────────────────
    async function _feLoadProjectVideos() {
      feVideoList.innerHTML = '<p class="explorer-empty">Loading…</p>';
      try {
        const res  = await fetch("/dlc/project/videos");
        const data = await res.json();
        if (data.error) { feVideoList.innerHTML = `<p class="explorer-empty">${data.error}</p>`; return; }
        if (!data.videos.length) { feVideoList.innerHTML = '<p class="explorer-empty">No videos in project videos/ folder.</p>'; return; }
        feVideoList.innerHTML = "";
        data.videos.forEach(v => {
          const item = document.createElement("div");
          item.className = "fe-video-item";
          item.innerHTML = `
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="2" y="2" width="20" height="20" rx="3"/>
              <polygon points="10 8 16 12 10 16 10 8" fill="currentColor" stroke="none"/>
            </svg>
            <span>${v.name}</span>`;
          item.addEventListener("click", () => _feSelectProjectVideo(v.name, item));
          feVideoList.appendChild(item);
        });
      } catch (err) {
        feVideoList.innerHTML = `<p class="explorer-empty">Error: ${err.message}</p>`;
      }
    }

    async function _feSelectVideo(filename) {
      _feReset();
      _feCurrentVideo = filename;
      feExtractCount.textContent = "0 frames saved";
      feExtractStatus.textContent = "";
      feExtractStatus.className = "fe-extract-status";
      try {
        const res  = await fetch(`/dlc/project/video-info/${encodeURIComponent(filename)}`);
        const info = await res.json();
        _feFps        = info.fps || 30;
        _feFrameCount = info.frame_count || 0;
      } catch (_) { _feFps = 30; _feFrameCount = 0; }
      fePlayerSec.classList.remove("hidden");
      _feLoadCsvData(filename);
      _feLoadFrame(0);
    }

    async function _feSelectProjectVideo(filename, itemEl) {
      feVideoList.querySelectorAll(".fe-video-item").forEach(el => el.classList.remove("active"));
      itemEl.classList.add("active");
      await _feSelectVideo(filename);
    }

    // ── External (server browse) video ────────────────────────────
    async function _feSelectExtVideo(absPath) {
      _feReset();
      _feCurrentVideo    = absPath;
      _feCurrentVideoExt = true;
      feExtractCount.textContent  = "0 frames saved";
      feExtractStatus.textContent = "";
      feExtractStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch(`/dlc/project/video-info-ext?path=${encodeURIComponent(absPath)}`);
        const info = await res.json();
        if (info.error) throw new Error(info.error);
        _feFps        = info.fps || 30;
        _feFrameCount = info.frame_count || 0;
      } catch (_) { _feFps = 30; _feFrameCount = 0; }
      fePlayerSec.classList.remove("hidden");
      _feLoadCsvData(absPath);
      _feLoadFrame(0);
    }

    // ── Server directory browser ──────────────────────────────────
    const _feVideoExts = new Set([".mp4", ".avi", ".mov", ".mkv", ".mpg", ".mpeg"]);
    function _feIsVideo(name) { return _feVideoExts.has(name.slice(name.lastIndexOf(".")).toLowerCase()); }

    async function _feBrowseServerDir(dirPath) {
      feServerBrowser.innerHTML = `<span style="font-size:.8rem;color:var(--text-dim)">Loading…</span>`;
      try {
        const res  = await fetch(`/fs/ls?path=${encodeURIComponent(dirPath)}`);
        const data = await res.json();
        if (data.error) {
          feServerBrowser.innerHTML = `<span style="font-size:.78rem;color:var(--text-dim)">${data.error}</span>`;
          return;
        }
        feServerBrowser.innerHTML = "";

        // Header row: current path + Up button
        const header = document.createElement("div");
        header.style.cssText = "display:flex;align-items:center;gap:.4rem;margin-bottom:.3rem;flex-wrap:wrap";
        const pathLabel = document.createElement("span");
        pathLabel.style.cssText = "font-size:.7rem;color:var(--text-dim);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono)";
        pathLabel.textContent = data.path;
        header.appendChild(pathLabel);
        if (data.parent) {
          const upBtn = document.createElement("button");
          upBtn.className = "btn-sm";
          upBtn.style.cssText = "padding:.12rem .45rem;font-size:.7rem;flex-shrink:0";
          upBtn.textContent = "↑ Up";
          upBtn.addEventListener("click", e => { e.stopPropagation(); _feBrowseServerDir(data.parent); });
          header.appendChild(upBtn);
        }
        feServerBrowser.appendChild(header);

        const visible = data.entries.filter(e => e.type === "dir" || (e.type === "file" && _feIsVideo(e.name)));
        if (!visible.length) {
          const empty = document.createElement("span");
          empty.style.cssText = "font-size:.75rem;color:var(--text-dim);padding:.25rem;display:block";
          empty.textContent = "(no video files here)";
          feServerBrowser.appendChild(empty);
        } else {
          visible.forEach(e => {
            const row = document.createElement("div");
            row.className = "fe-video-item";
            const fullPath = data.path.replace(/\/+$/, "") + "/" + e.name;
            if (e.type === "dir") {
              row.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${e.name}/</span>`;
              row.style.cursor = "pointer";
              row.addEventListener("click", () => _feBrowseServerDir(fullPath));
            } else {
              row.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><rect x="2" y="2" width="20" height="20" rx="3"/><polygon points="10 8 16 12 10 16 10 8" fill="currentColor" stroke="none"/></svg><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0">${e.name}</span>`;
              row.style.cursor = "pointer";
              row.addEventListener("click", async () => {
                if (!confirm(`Add this video to the project?\n\n${fullPath}\n\nThe path will be registered in config.yaml (no copy is made).`)) return;
                feServerBrowser.querySelectorAll(".fe-video-item").forEach(r => r.classList.remove("active"));
                row.classList.add("active");
                feServerStatus.textContent = "Registering video with project…";
                try {
                  const res2 = await fetch("/dlc/project/add-video", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ video_path: fullPath }),
                  });
                  const data2 = await res2.json();
                  if (data2.error) { feServerStatus.textContent = `Error: ${data2.error}`; return; }
                  feServerStatus.textContent = `Added: ${data2.name}`;
                  await _feSelectExtVideo(data2.abs_path);
                } catch (err) {
                  feServerStatus.textContent = `Error: ${err.message}`;
                }
              });
            }
            feServerBrowser.appendChild(row);
          });
        }
      } catch (err) {
        feServerBrowser.innerHTML = `<span style="font-size:.78rem;color:var(--text-dim)">Error: ${err.message}</span>`;
      }
    }

    // ── File upload ───────────────────────────────────────────────
    feFileInput.addEventListener("change", async () => {
      const file = feFileInput.files[0];
      if (!file) return;
      feUploadStatus.textContent = "Uploading…";
      const fd = new FormData();
      fd.append("video", file);
      try {
        const res  = await fetch("/dlc/project/video-upload", { method: "POST", body: fd });
        const data = await res.json();
        if (data.error) { feUploadStatus.textContent = `Error: ${data.error}`; return; }
        feUploadStatus.textContent = `Saved as ${data.saved}`;
        await _feSelectVideo(data.saved);
      } catch (err) {
        feUploadStatus.textContent = `Upload failed: ${err.message}`;
      }
      feFileInput.value = "";
    });

    // ── Frame display ─────────────────────────────────────────────
    // Text node kept separate so the hidden <input> inside the span isn't clobbered.
    // Remove any existing text nodes (the static "Frame 0 / 0" from HTML), then
    // insert a managed text node before the jump input.
    [...feFrameCounter.childNodes].forEach(n => { if (n.nodeType === Node.TEXT_NODE) n.remove(); });
    const _feCounterTextNode = document.createTextNode("");
    feFrameCounter.insertBefore(_feCounterTextNode, feFrameJump);

    function _feUpdateFrameDisplay() {
      const total = Math.max(_feFrameCount, 1);
      _feCounterTextNode.nodeValue = `Frame ${_feCurrentFrame} / ${_feFrameCount}`;
      feTimeDisplay.textContent  = `${(_feCurrentFrame / _feFps).toFixed(3)} s`;
      if (!_feSeekDragging)
        feSeek.value = Math.round((_feCurrentFrame / Math.max(total - 1, 1)) * 1000);
    }

    // Double-click on counter → inline frame-jump input
    feFrameCounter.addEventListener("dblclick", () => {
      feFrameCounter.classList.add("editing");
      feFrameJump.classList.remove("hidden");
      feFrameJump.max   = String(_feFrameCount - 1);
      feFrameJump.value = String(_feCurrentFrame);
      feFrameJump.select();
    });

    function _feCommitJump() {
      const n = parseInt(feFrameJump.value);
      feFrameJump.classList.add("hidden");
      feFrameCounter.classList.remove("editing");
      if (!isNaN(n)) _feLoadFrame(n);
    }

    let _feJumpEscaped = false;
    feFrameJump.addEventListener("keydown", e => {
      if (e.key === "Enter")  { e.preventDefault(); _feCommitJump(); }
      if (e.key === "Escape") {
        _feJumpEscaped = true;
        feFrameJump.classList.add("hidden");
        feFrameCounter.classList.remove("editing");
        feFrameJump.blur();
      }
    });
    feFrameJump.addEventListener("blur", () => {
      if (_feJumpEscaped) { _feJumpEscaped = false; return; }
      _feCommitJump();
    });

    async function _feLoadFrame(n) {
      if (_feFrameBusy) return;
      _feFrameBusy = true;
      n = Math.max(0, Math.min(n, Math.max(_feFrameCount - 1, 0)));
      _feCurrentFrame = n;
      feFrameSpinner.classList.remove("hidden");
      try {
        const resp = await fetch(_feFrameUrl(n));
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.error || `HTTP ${resp.status}`);
        }
        const blob    = await resp.blob();
        const blobUrl = URL.createObjectURL(blob);
        await new Promise((resolve, reject) => {
          feFrameImg.onload  = resolve;
          feFrameImg.onerror = reject;
          const prev = feFrameImg.src;
          feFrameImg.src = blobUrl;
          if (prev.startsWith("blob:")) URL.revokeObjectURL(prev);
        });
        _feFitViewer();
        _feUpdateFrameDisplay();
        _fePrefetch([n + 1, n + 2]);
      } catch (err) {
        feExtractStatus.textContent = `Failed to load frame: ${err.message}`;
        feExtractStatus.className = "fe-extract-status err";
      } finally {
        _feFrameBusy = false;
        feFrameSpinner.classList.add("hidden");
      }
    }

    // ── Controls ──────────────────────────────────────────────────
    feBtnPlay.addEventListener("click", () => {
      if (_fePlayTimer) {
        clearInterval(_fePlayTimer); _fePlayTimer = null;
        fePlayIcon.classList.remove("hidden"); fePauseIcon.classList.add("hidden");
      } else {
        fePlayIcon.classList.add("hidden"); fePauseIcon.classList.remove("hidden");
        _fePlayTimer = setInterval(async () => {
          if (_feCurrentFrame >= _feFrameCount - 1) {
            clearInterval(_fePlayTimer); _fePlayTimer = null;
            fePlayIcon.classList.remove("hidden"); fePauseIcon.classList.add("hidden");
            return;
          }
          await _feLoadFrame(_feCurrentFrame + 1);
        }, 1000 / _feFps);
      }
    });

    feBtnPrev.addEventListener("click", () => _feLoadFrame(_feCurrentFrame - 1));
    feBtnNext.addEventListener("click", () => _feLoadFrame(_feCurrentFrame + 1));

    feSeek.addEventListener("mousedown",  () => { _feSeekDragging = true; });
    feSeek.addEventListener("touchstart", () => { _feSeekDragging = true; });
    feSeek.addEventListener("input", () => {
      _feCurrentFrame = Math.round((feSeek.value / 1000) * Math.max(_feFrameCount - 1, 0));
      _feUpdateFrameDisplay();
    });
    feSeek.addEventListener("change", () => { _feSeekDragging = false; _feLoadFrame(_feCurrentFrame); });

    // ── Capture + save helpers ────────────────────────────────────
    async function _feCaptureCurrent() {
      feCanvas.width  = feFrameImg.naturalWidth;
      feCanvas.height = feFrameImg.naturalHeight;
      try {
        feCanvas.getContext("2d").drawImage(feFrameImg, 0, 0);
        const url = feCanvas.toDataURL("image/jpeg", 0.92);
        return url.split(",")[1] || null;
      } catch (secErr) {
        feExtractStatus.textContent = `Canvas error: ${secErr.message}`;
        feExtractStatus.className = "fe-extract-status err";
        return null;
      }
    }

    async function _feSaveFrames(count, step = 1) {
      if (!_feCurrentVideo) return;
      _feStopExtraction = false;
      feBtnExtract.disabled = true;
      feBtnBatchExtract.disabled = true;
      if (count > 1) {
        feBtnStopExtract.classList.remove("hidden");
        feExtractStatus.textContent = `Saving ${count} frames…`;
        feExtractStatus.className = "fe-extract-status";
      }
      if (_fePlayTimer) { clearInterval(_fePlayTimer); _fePlayTimer = null; fePlayIcon.classList.remove("hidden"); fePauseIcon.classList.add("hidden"); }
      let saved = 0, skipped = 0, lastData = null;
      try {
        for (let i = 0; i < count; i++) {
          if (_feStopExtraction) break;
          if (i > 0) await _feLoadFrame(_feCurrentFrame + step);
          if (_feStopExtraction) break;
          const base64 = await _feCaptureCurrent();
          if (!base64) break;
          const res  = await fetch("/dlc/project/save-frame", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ video_name: _feCurrentVideo, frame_data: base64, frame_number: _feCurrentFrame }),
          });
          const data = await res.json();
          if (data.skipped) { skipped++; continue; }
          if (data.error) { feExtractStatus.textContent = `Error on frame ${i + 1}: ${data.error}`; feExtractStatus.className = "fe-extract-status err"; break; }
          saved++; lastData = data;
          _feExtracted = data.frame_count;
          feExtractCount.textContent = `${_feExtracted} frame${_feExtracted !== 1 ? "s" : ""} saved`;
          if (count > 1) feExtractStatus.textContent = `Saving… ${i + 1}/${count} frames`;
        }
        if (_feStopExtraction && saved > 0) {
          const skipNote = skipped > 0 ? `, ${skipped} duplicate${skipped !== 1 ? "s" : ""} skipped` : "";
          feExtractStatus.textContent = `Stopped — saved ${saved} frame${saved !== 1 ? "s" : ""}${skipNote} → ${lastData.abs_path}`;
          feExtractStatus.className = "fe-extract-status";
        } else if (_feStopExtraction) {
          feExtractStatus.textContent = "Stopped — no frames saved";
          feExtractStatus.className = "fe-extract-status";
        } else if (saved > 0) {
          const skipNote = skipped > 0 ? `, ${skipped} duplicate${skipped !== 1 ? "s" : ""} skipped` : "";
          feExtractStatus.textContent = `Saved ${saved} frame${saved !== 1 ? "s" : ""}${skipNote} → ${lastData.abs_path}`;
          feExtractStatus.className = "fe-extract-status ok";
        } else if (skipped > 0) {
          feExtractStatus.textContent = `All ${skipped} frame${skipped !== 1 ? "s" : ""} already saved — skipped`;
          feExtractStatus.className = "fe-extract-status";
        }
      } catch (err) {
        feExtractStatus.textContent = `Network error: ${err.message}`;
        feExtractStatus.className = "fe-extract-status err";
      } finally {
        feBtnExtract.disabled = false;
        feBtnBatchExtract.disabled = false;
        feBtnStopExtract.classList.add("hidden");
        feBtnStopExtract.disabled = false;
        _feStopExtraction = false;
        _feUpdateFrameDisplay();
      }
    }

    // ── CSV annotation bars ───────────────────────────────────────
    const _feCsvPalette = ["#6ee7b7","#60a5fa","#f472b6","#fbbf24","#a78bfa","#34d399","#fb923c","#e879f9"];

    function _feComputeRuns(rows, field) {
      const vals = [...new Set(rows.map(r => r[field]).filter(v => v))];
      const colorMap = {};
      vals.forEach((v, i) => { colorMap[v] = _feCsvPalette[i % _feCsvPalette.length]; });
      const runs = [];
      rows.forEach(row => {
        const val = row[field];
        if (!val) return;
        const last = runs[runs.length - 1];
        if (last && last.value === val && row.frame_number === last.endFrame + 1) { last.endFrame = row.frame_number; }
        else { runs.push({ value: val, startFrame: row.frame_number, endFrame: row.frame_number }); }
      });
      return { runs, colorMap };
    }


    function _feRenderCsvBar(container, runs, colorMap, beforeInput, afterInput, activeTag, onSegClick) {
      container.innerHTML = "";
      const total = Math.max(_feFrameCount, 1);
      const bef = parseInt(beforeInput.value) || 0;
      const aft = parseInt(afterInput.value)  || 0;
      let filteredIdx = 0;
      runs.forEach(run => {
        if (activeTag !== null && run.value !== activeTag) return;
        const thisIdx  = filteredIdx++;
        const visStart = Math.max(0, run.startFrame - bef);
        const visEnd   = Math.min(_feFrameCount - 1, run.startFrame + aft);
        const startPct = (visStart / total) * 100;
        const widthPct = Math.max(((visEnd + 1) / total) * 100 - startPct, 0.3);
        const color    = colorMap[run.value];
        const seg = document.createElement("div");
        seg.className = "fe-timeline-seg";
        seg.style.cssText = `left:${startPct}%;width:${widthPct}%;background:${color}40;border-color:${color};color:${color}`;
        seg.textContent = run.value;
        seg.title = `${run.value}  (signal frames ${run.startFrame}–${run.endFrame})\nWindow: ${visStart}–${visEnd}  (${visEnd - visStart + 1} frames)\nClick → frame ${visStart}  |  Shift+click → extract window`;
        seg.addEventListener("click", async (e) => {
          const b = parseInt(beforeInput.value) || 0;
          const a = parseInt(afterInput.value)  || 0;
          const nav    = Math.max(0, run.startFrame - b);
          const winEnd = Math.min(_feFrameCount - 1, run.startFrame + a);
          if (onSegClick) onSegClick(thisIdx);
          await _feLoadFrame(nav);
          if (e.shiftKey) {
            const total = winEnd - nav + 1;
            const n = await _feConfirmWindowExtract(total, nav, winEnd);
            if (n > 0) _feSaveFrames(n);
          }
        });
        container.appendChild(seg);
      });
    }

    function _feRenderTagFilter(tagContainer, runs, colorMap, activeTag, onTagClick) {
      tagContainer.innerHTML = "";
      const vals = [...new Set(runs.map(r => r.value))];
      if (vals.length < 2) return;
      vals.forEach(val => {
        const chip = document.createElement("span");
        chip.className = "fe-tag-chip" + (activeTag === val ? " active" : "");
        chip.style.setProperty("--chip-color", colorMap[val]);
        chip.textContent = val;
        chip.addEventListener("click", () => onTagClick(val));
        tagContainer.appendChild(chip);
      });
    }

    function _feGoToSeg(runs, beforeInput, afterInput, activeTag, idx) {
      const filtered = activeTag ? runs.filter(r => r.value === activeTag) : runs;
      if (!filtered.length) return;
      const run = filtered[idx % filtered.length];
      _feLoadFrame(Math.max(0, run.startFrame - (parseInt(beforeInput.value) || 0)));
    }

    function _feUpdateSegNav(navEl, infoEl, runs, activeTag, idx, alwaysShow) {
      const filtered = activeTag ? runs.filter(r => r.value === activeTag) : (alwaysShow ? runs : null);
      if (!filtered || !filtered.length) { navEl.classList.add("hidden"); return; }
      navEl.classList.remove("hidden");
      infoEl.textContent = activeTag ? `${activeTag}: ${idx + 1} / ${filtered.length}` : `${idx + 1} / ${filtered.length}`;
    }

    async function _feLoadCsvData(filename) {
      _feCsvRows = [];
      _feStatusRuns = []; _feNoteRuns = [];
      _feStatusColorMap = {}; _feNoteColorMap = {};
      _feStatusActiveTag = null; _feNoteActiveTag = null;
      feCsvBars.classList.add("hidden");
      feStatusBarWrap.classList.add("hidden");
      feNoteBarWrap.classList.add("hidden");
      try {
        const url = _feCurrentVideoExt
          ? `/dlc/project/video-csv-ext?path=${encodeURIComponent(filename)}`
          : `/dlc/project/video-csv/${encodeURIComponent(filename)}`;
        const res  = await fetch(url);
        const data = await res.json();
        _feCsvRows = data.rows || [];
        if (!_feCsvRows.length) return;
        const hasStatus = _feCsvRows.some(r => r.frame_line_status);
        const hasNote   = _feCsvRows.some(r => r.note);
        if (!hasStatus && !hasNote) return;
        feCsvBars.classList.remove("hidden");
        if (hasStatus) {
          ({ runs: _feStatusRuns, colorMap: _feStatusColorMap } = _feComputeRuns(_feCsvRows, "frame_line_status"));
          const onStatusTag = val => {
            _feStatusActiveTag = (_feStatusActiveTag === val) ? null : val;
            _feStatusSegIdx = 0;
            _feStatusEffectiveRuns = _feStatusRuns;
            _feRenderCsvBar(feStatusBar, _feStatusEffectiveRuns, _feStatusColorMap, feStatusBefore, feStatusAfter, _feStatusActiveTag, idx => {
              _feStatusSegIdx = idx;
              _feUpdateSegNav(feStatusNav, feStatusNavInfo, _feStatusEffectiveRuns, _feStatusActiveTag, _feStatusSegIdx);
            });
            _feRenderTagFilter(feStatusTags, _feStatusRuns, _feStatusColorMap, _feStatusActiveTag, onStatusTag);
            _feUpdateSegNav(feStatusNav, feStatusNavInfo, _feStatusEffectiveRuns, _feStatusActiveTag, _feStatusSegIdx);
            if (_feStatusActiveTag) _feGoToSeg(_feStatusEffectiveRuns, feStatusBefore, feStatusAfter, _feStatusActiveTag, 0);
          };
          _feReRenderStatus = () => {
            _feStatusEffectiveRuns = _feStatusRuns;
            _feRenderCsvBar(feStatusBar, _feStatusEffectiveRuns, _feStatusColorMap, feStatusBefore, feStatusAfter, _feStatusActiveTag, idx => {
              _feStatusSegIdx = idx;
              _feUpdateSegNav(feStatusNav, feStatusNavInfo, _feStatusEffectiveRuns, _feStatusActiveTag, _feStatusSegIdx);
            });
            _feRenderTagFilter(feStatusTags, _feStatusRuns, _feStatusColorMap, _feStatusActiveTag, onStatusTag);
            _feUpdateSegNav(feStatusNav, feStatusNavInfo, _feStatusEffectiveRuns, _feStatusActiveTag, _feStatusSegIdx);
          };
          _feReRenderStatus();
          feStatusBarWrap.classList.remove("hidden");
        }
        if (hasNote) {
          ({ runs: _feNoteRuns, colorMap: _feNoteColorMap } = _feComputeRuns(_feCsvRows, "note"));
          const onNoteTag = val => {
            _feNoteActiveTag = (_feNoteActiveTag === val) ? null : val;
            _feNoteSegIdx = 0;
            _feNoteEffectiveRuns = _feNoteRuns;
            _feRenderCsvBar(feNoteBar, _feNoteEffectiveRuns, _feNoteColorMap, feNoteBefore, feNoteAfter, _feNoteActiveTag, idx => {
              _feNoteSegIdx = idx;
              _feUpdateSegNav(feNoteNav, feNoteNavInfo, _feNoteEffectiveRuns, _feNoteActiveTag, _feNoteSegIdx, true);
            });
            _feRenderTagFilter(feNoteTags, _feNoteRuns, _feNoteColorMap, _feNoteActiveTag, onNoteTag);
            _feUpdateSegNav(feNoteNav, feNoteNavInfo, _feNoteEffectiveRuns, _feNoteActiveTag, _feNoteSegIdx, true);
            if (_feNoteActiveTag) _feGoToSeg(_feNoteEffectiveRuns, feNoteBefore, feNoteAfter, _feNoteActiveTag, 0);
          };
          _feReRenderNote = () => {
            _feNoteEffectiveRuns = _feNoteRuns;
            _feRenderCsvBar(feNoteBar, _feNoteEffectiveRuns, _feNoteColorMap, feNoteBefore, feNoteAfter, _feNoteActiveTag, idx => {
              _feNoteSegIdx = idx;
              _feUpdateSegNav(feNoteNav, feNoteNavInfo, _feNoteEffectiveRuns, _feNoteActiveTag, _feNoteSegIdx, true);
            });
            _feRenderTagFilter(feNoteTags, _feNoteRuns, _feNoteColorMap, _feNoteActiveTag, onNoteTag);
            _feUpdateSegNav(feNoteNav, feNoteNavInfo, _feNoteEffectiveRuns, _feNoteActiveTag, _feNoteSegIdx, true);
          };
          _feReRenderNote();
          feNoteBarWrap.classList.remove("hidden");
        }
      } catch (_) { /* no CSV – bars stay hidden */ }
    }

    feBtnExtract.addEventListener("click", () => _feSaveFrames(1));

    feBtnBatchExtract.addEventListener("click", () => {
      if (!_feCurrentVideo) return;
      const requested = Math.max(2, parseInt(feBatchCountInput.value) || 10);
      const step      = Math.max(1, parseInt(feBatchStepInput.value)  || 1);
      // max frames reachable from current position with this step
      const maxCount  = Math.floor((_feFrameCount - 1 - _feCurrentFrame) / step) + 1;
      const count     = Math.min(requested, maxCount);
      if (count < 1) return;
      if (count < requested) {
        feExtractStatus.textContent = `Near end — extracting ${count} frame${count !== 1 ? "s" : ""} (clamped from ${requested})`;
        feExtractStatus.className = "fe-extract-status";
      }
      _feSaveFrames(count, step);
    });

    feBtnStopExtract.addEventListener("click", () => { _feStopExtraction = true; feBtnStopExtract.disabled = true; });

    // ── Window-extract confirmation dialog ───────────────────────
    function _feConfirmWindowExtract(totalFrames, winStart, winEnd) {
      return new Promise(resolve => {
        feDialogMsg.textContent = `Extract ${totalFrames} frame${totalFrames !== 1 ? "s" : ""} from window ${winStart}–${winEnd}?`;
        feDialogCustomWrap.style.display = "none";
        feDialogCustomInput.value = totalFrames;
        feDialogConfirm.textContent = `Extract all ${totalFrames}`;
        feDialogCustomBtn.classList.remove("hidden");

        function cleanup() { feExtractDialog.close(); }

        feDialogConfirm.onclick = () => { cleanup(); resolve(totalFrames); };
        feDialogCancel.onclick  = () => { cleanup(); resolve(0); };
        feDialogCustomBtn.onclick = () => {
          feDialogCustomWrap.style.display = "block";
          feDialogCustomInput.max = totalFrames;
          feDialogCustomInput.value = Math.min(totalFrames, 10);
          feDialogCustomBtn.classList.add("hidden");
          feDialogConfirm.textContent = "Extract";
          feDialogConfirm.onclick = () => {
            const n = Math.max(1, Math.min(parseInt(feDialogCustomInput.value) || 1, totalFrames));
            cleanup(); resolve(n);
          };
          feDialogCustomInput.focus();
        };
        feDialogCustomInput.onkeydown = e => {
          if (e.key === "Enter") { const n = Math.max(1, Math.min(parseInt(feDialogCustomInput.value) || 1, totalFrames)); cleanup(); resolve(n); }
          if (e.key === "Escape") { cleanup(); resolve(0); }
        };
        feExtractDialog.showModal();
      });
    }

    feStatusApply.addEventListener("click", () => { if (_feReRenderStatus) _feReRenderStatus(); });
    feNoteApply.addEventListener("click",   () => { if (_feReRenderNote)   _feReRenderNote();   });

    feStatusPrevBtn.addEventListener("click", () => {
      if (!_feStatusActiveTag) return;
      const n = _feStatusEffectiveRuns.filter(r => r.value === _feStatusActiveTag).length;
      _feStatusSegIdx = (_feStatusSegIdx - 1 + n) % n;
      _feGoToSeg(_feStatusEffectiveRuns, feStatusBefore, feStatusAfter, _feStatusActiveTag, _feStatusSegIdx);
      _feUpdateSegNav(feStatusNav, feStatusNavInfo, _feStatusEffectiveRuns, _feStatusActiveTag, _feStatusSegIdx);
    });
    feStatusNextBtn.addEventListener("click", () => {
      if (!_feStatusActiveTag) return;
      const n = _feStatusEffectiveRuns.filter(r => r.value === _feStatusActiveTag).length;
      _feStatusSegIdx = (_feStatusSegIdx + 1) % n;
      _feGoToSeg(_feStatusEffectiveRuns, feStatusBefore, feStatusAfter, _feStatusActiveTag, _feStatusSegIdx);
      _feUpdateSegNav(feStatusNav, feStatusNavInfo, _feStatusEffectiveRuns, _feStatusActiveTag, _feStatusSegIdx);
    });

    feNotePrevBtn.addEventListener("click", () => {
      const pool = _feNoteActiveTag ? _feNoteEffectiveRuns.filter(r => r.value === _feNoteActiveTag) : _feNoteEffectiveRuns;
      if (!pool.length) return;
      _feNoteSegIdx = (_feNoteSegIdx - 1 + pool.length) % pool.length;
      _feGoToSeg(_feNoteEffectiveRuns, feNoteBefore, feNoteAfter, _feNoteActiveTag, _feNoteSegIdx);
      _feUpdateSegNav(feNoteNav, feNoteNavInfo, _feNoteEffectiveRuns, _feNoteActiveTag, _feNoteSegIdx, true);
    });
    feNoteNextBtn.addEventListener("click", () => {
      const pool = _feNoteActiveTag ? _feNoteEffectiveRuns.filter(r => r.value === _feNoteActiveTag) : _feNoteEffectiveRuns;
      if (!pool.length) return;
      _feNoteSegIdx = (_feNoteSegIdx + 1) % pool.length;
      _feGoToSeg(_feNoteEffectiveRuns, feNoteBefore, feNoteAfter, _feNoteActiveTag, _feNoteSegIdx);
      _feUpdateSegNav(feNoteNav, feNoteNavInfo, _feNoteEffectiveRuns, _feNoteActiveTag, _feNoteSegIdx, true);
    });

    // ── Keyboard shortcuts (active while hovering over player) ────
    let _feHover      = false;
    let _fePending    = null;
    let _fePendingTmr = null;

    fePlayerSec.addEventListener("mouseenter", () => { _feHover = true; });
    fePlayerSec.addEventListener("mouseleave", () => { _feHover = false; _fePending = null; clearTimeout(_fePendingTmr); feExtractStatus.textContent = feExtractStatus.textContent.startsWith("Press") ? "" : feExtractStatus.textContent; });

    document.addEventListener("keydown", e => {
      if (!_feHover || fePlayerSec.classList.contains("hidden")) return;
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

      if (e.key === " ") { e.preventDefault(); feBtnPlay.click(); return; }
      if (e.key === "ArrowLeft")  { e.preventDefault(); feBtnPrev.click(); return; }
      if (e.key === "ArrowRight") { e.preventDefault(); feBtnNext.click(); return; }

      if (/^[1-9]$/.test(e.key)) {
        e.preventDefault();
        _fePending = parseInt(e.key);
        clearTimeout(_fePendingTmr);
        _fePendingTmr = setTimeout(() => { _fePending = null; }, 2000);
        feExtractStatus.textContent = `Press S to save ${_fePending} frame${_fePending !== 1 ? "s" : ""}`;
        feExtractStatus.className = "fe-extract-status";
        return;
      }

      // "s" → save N frames (N from pending digit, else 1)
      if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        const n = _fePending || 1;
        _fePending = null;
        clearTimeout(_fePendingTmr);
        _feSaveFrames(n);
        return;
      }
    });
  })();

  // ── Frame Labeler ─────────────────────────────────────────────
  (function () {
    const flCard         = document.getElementById("frame-labeler-card");
    const flOpenBtn      = document.getElementById("btn-open-frame-labeler");
    const flCloseBtn     = document.getElementById("btn-close-frame-labeler");
    const flStemSelect   = document.getElementById("fl-stem-select");
    const flRefreshBtn   = document.getElementById("fl-refresh-btn");
    const flStemStatus   = document.getElementById("fl-stem-status");
    const flPlayerSec    = document.getElementById("fl-player-section");
    const flBtnPrev      = document.getElementById("fl-btn-prev");
    const flBtnNext      = document.getElementById("fl-btn-next");
    const flFrameInfo    = document.getElementById("fl-frame-info");
    const flFrameName    = document.getElementById("fl-frame-name");
    const flCanvas       = document.getElementById("fl-canvas");
    const flCtx          = flCanvas.getContext("2d");
    const flCanvasLoading = document.getElementById("fl-canvas-loading");
    const flBodypartList = document.getElementById("fl-bodypart-list");
    const flBpHint       = document.getElementById("fl-bp-hint");
    const flBtnSave        = document.getElementById("fl-btn-save");
    const flBtnSaveH5      = document.getElementById("fl-btn-save-h5");
    const flSaveStatus     = document.getElementById("fl-save-status");
    const flLabelCount     = document.getElementById("fl-label-count");
    const flScorerFilename = document.getElementById("fl-scorer-filename");
    const flMarkerSizeInput = document.getElementById("fl-marker-size");
    const flMarkerSizeVal   = document.getElementById("fl-marker-size-val");
    const flShowNamesInput  = document.getElementById("fl-show-names");

    // ── TAPNet propagation elements ──────────────────────────────
    const flTapCheckbox      = document.getElementById("fl-tap-checkbox");
    const flTapOpts          = document.getElementById("fl-tap-opts");
    const flTapCkpt          = document.getElementById("fl-tap-ckpt");
    const flTapAnchor        = document.getElementById("fl-tap-anchor");
    const flTapCheckBtn      = document.getElementById("fl-tap-check-btn");
    const flTapCheckStatus   = document.getElementById("fl-tap-check-status");
    const flTapSeqInfo       = document.getElementById("fl-tap-seq-info");
    const flTapOverwrite     = document.getElementById("fl-tap-overwrite");
    const flTapRunBtn        = document.getElementById("fl-tap-run-btn");
    const flTapStopBtn       = document.getElementById("fl-tap-stop-btn");
    const flTapRerunBtn      = document.getElementById("fl-tap-rerun-btn");
    const flTapConfirmedCount= document.getElementById("fl-tap-confirmed-count");
    const flTapStatus        = document.getElementById("fl-tap-status");
    const flTapProgress      = document.getElementById("fl-tap-progress");
    const flTapTaskId        = document.getElementById("fl-tap-task-id");
    const flTapProgressBar   = document.getElementById("fl-tap-progress-bar");
    const flTapProgressStage = document.getElementById("fl-tap-progress-stage");
    const flTapProgressPct   = document.getElementById("fl-tap-progress-pct");
    const flTapLogOutput     = document.getElementById("fl-tap-log-output");
    // Per-frame confirm elements
    const flTapFrameBadge    = document.getElementById("fl-tap-frame-badge");
    const flTapConfirmBtn    = document.getElementById("fl-tap-confirm-btn");
    const flTapConfirmLabel  = document.getElementById("fl-tap-confirm-label");

    // ── Machine-labeling elements ────────────────────────────────
    const flMlCheckbox     = document.getElementById("fl-ml-checkbox");
    const flMlOpts         = document.getElementById("fl-ml-opts");
    const flMlSnapshot     = document.getElementById("fl-ml-snapshot");
    const flMlRefreshSnap  = document.getElementById("fl-ml-refresh-snap");
    const flMlShuffle      = document.getElementById("fl-ml-shuffle");
    const flMlRunBtn       = document.getElementById("fl-ml-run-btn");
    const flMlRunAllBtn    = document.getElementById("fl-ml-run-all-btn");
    const flMlLikelihood   = document.getElementById("fl-ml-likelihood");
    const flMlStopBtn      = document.getElementById("fl-ml-stop-btn");
    const flMlStatus       = document.getElementById("fl-ml-status");
    const flMlUpdateWrap   = document.getElementById("fl-ml-update-wrap");
    const flMlUpdateBtn    = document.getElementById("fl-ml-update-btn");
    const flMlUpdateStatus = document.getElementById("fl-ml-update-status");
    const flMlProgress     = document.getElementById("fl-ml-progress");
    const flMlTaskId       = document.getElementById("fl-ml-task-id");
    const flMlProgressBar  = document.getElementById("fl-ml-progress-bar");
    const flMlProgressStage= document.getElementById("fl-ml-progress-stage");
    const flMlProgressPct  = document.getElementById("fl-ml-progress-pct");
    const flMlLogOutput    = document.getElementById("fl-ml-log-output");

    // ── State ───────────────────────────────────────────────────
    let _flBodyparts   = [];
    let _flScorer      = "User";
    let _flStemData    = [];      // [{video_stem, frames[]}]
    let _flVideoStem   = null;
    let _flFrames      = [];      // array of filenames
    let _flFrameIdx    = 0;
    let _flLabels      = {};      // {frame_name: {bp: [x, y] | null}}
    let _flDirty       = false;  // unsaved changes since last save/load
    let _flSelectedBp  = null;
    let _flImg         = new Image();
    let _flImgLoaded   = false;
    let _flMarkerRadius   = 4;
    let _flShowNames      = true;
    let _flCursorInCanvas = false;
    let _flHoverBp        = null;   // bodypart marker the cursor is near
    let _flZoom           = 100;
    let _flHidden         = {};  // {frame_name: {bp: bool}} — visibility-toggled markers  // percent of container width (100 = fit to card)

    // Machine labeling state (persists across folder changes)
    let _flMlPollTimer   = null;
    let _flMlActiveTask  = null;
    let _flMlQueue       = [];   // stems queued for "Run All Folders"
    let _flMlQueueIdx    = -1;   // current index in queue (-1 = single-folder mode)
    let _flMlQueueTotal  = 0;

    // ── Machine labeling: toggle panel ──────────────────────────
    flMlCheckbox.addEventListener("change", () => {
      flMlOpts.classList.toggle("hidden", !flMlCheckbox.checked);
      if (flMlCheckbox.checked) {
        _flMlLoadSnapshots();
        _populateGpuSelect("fl-ml-gpu");
      }
    });

    // ── Machine labeling: load snapshots ────────────────────────
    async function _flMlLoadSnapshots() {
      try {
        // No shuffle filter — show all shuffles so models from any shuffle are visible.
        // The backend will auto-correct the shuffle when snapshot_path is provided.
        const res  = await fetch("/dlc/project/snapshots");
        const data = await res.json();
        if (data.error) return;
        flMlSnapshot.innerHTML = "";
        const latestOpt = document.createElement("option");
        // Use the actual latest snapshot path (not -1) so shuffle is auto-derived
        latestOpt.value = data.latest_rel_path || "-1";
        const latestSuffix = data.latest_label
          ? ` — ${data.latest_label}${data.latest_iteration != null ? "  ·  iter " + data.latest_iteration.toLocaleString() : ""}${data.latest_shuffle != null ? "  ·  sh" + data.latest_shuffle : ""}`
          : "";
        latestOpt.textContent = `Latest${latestSuffix}`;
        flMlSnapshot.appendChild(latestOpt);
        (data.snapshots || []).forEach(s => {
          const opt = document.createElement("option");
          opt.value = s.rel_path;
          const shuffleSuffix = s.shuffle != null ? `  ·  sh${s.shuffle}` : "";
          opt.textContent = `${s.label}${s.iteration != null ? "  ·  iter " + s.iteration.toLocaleString() : ""}${shuffleSuffix}`;
          flMlSnapshot.appendChild(opt);
        });
      } catch (err) {
        console.error("flMlLoadSnapshots:", err);
      }
    }

    flMlRefreshSnap.addEventListener("click", _flMlLoadSnapshots);
    flMlShuffle.addEventListener("change", _flMlLoadSnapshots);

    // ── Machine labeling: run / stop ────────────────────────────
    function _flMlSetRunning(running) {
      flMlRunBtn.classList.toggle("hidden",    running);
      flMlRunAllBtn.classList.toggle("hidden", running);
      flMlStopBtn.classList.toggle("hidden",  !running);
      flMlRunBtn.disabled    = running;
      flMlRunAllBtn.disabled = running;
      if (running) flMlUpdateWrap.classList.add("hidden");
    }

    function _flMlQueueLabel() {
      if (_flMlQueueIdx < 0 || _flMlQueueTotal <= 1) return "";
      return ` (${_flMlQueueIdx + 1}/${_flMlQueueTotal}: ${_flMlQueue[_flMlQueueIdx]})`;
    }

    function _flMlStartPolling(taskId) {
      flMlProgress.classList.remove("hidden", "state-success", "state-fail");
      flMlTaskId.textContent        = taskId.slice(0, 12) + "…";
      flMlProgressBar.style.width   = "0%";
      flMlProgressPct.textContent   = "0 %";
      flMlProgressStage.textContent = "Queued" + _flMlQueueLabel();
      flMlLogOutput.textContent     = "Waiting for output…";
      _flMlSetRunning(true);
      if (_flMlPollTimer) clearInterval(_flMlPollTimer);
      _flMlPollTimer = setInterval(() => _flMlPoll(taskId), 2000);
      _flMlPoll(taskId);
    }

    async function _flMlPoll(taskId) {
      try {
        const res  = await fetch(`/status/${taskId}`);
        const data = await res.json();
        const pct  = Math.min(data.progress || 0, 100);
        flMlProgressBar.style.width   = pct + "%";
        flMlProgressPct.textContent   = pct + " %";
        flMlProgressStage.textContent = (data.stage || data.state) + _flMlQueueLabel();
        if (data.log) {
          flMlLogOutput.textContent = data.log;
          flMlLogOutput.scrollTop   = flMlLogOutput.scrollHeight;
        }
        if (data.state === "SUCCESS") {
          clearInterval(_flMlPollTimer); _flMlPollTimer = null;
          if (data.result && data.result.log) flMlLogOutput.textContent = data.result.log;
          // Reload labels for the current stem so the user sees machine labels immediately
          if (_flVideoStem) {
            const found = _flStemData.find(s => s.video_stem === _flVideoStem);
            if (found) await _flSelectStem(found.video_stem, found.frames);
          }
          // Advance queue if running all folders
          if (_flMlQueueIdx >= 0 && _flMlQueueIdx < _flMlQueue.length - 1) {
            _flMlQueueIdx++;
            flMlStatus.textContent = `Folder ${_flMlQueueIdx + 1}/${_flMlQueueTotal} done — starting next…`;
            flMlStatus.className   = "fe-extract-status ok";
            await _flMlDispatch(_flMlQueue[_flMlQueueIdx]);
          } else {
            // Done (single folder or last in queue)
            _flMlQueue = []; _flMlQueueIdx = -1; _flMlQueueTotal = 0;
            flMlProgress.classList.add("state-success");
            flMlProgressStage.textContent = "✓ Machine labeling complete";
            flMlProgressBar.style.width   = "100%";
            flMlProgressPct.textContent   = "100 %";
            flMlStatus.textContent = "Labels loaded — review and correct as needed.";
            flMlStatus.className   = "fe-extract-status ok";
            _flMlSetRunning(false);
          }
        }
        if (data.state === "FAILURE" || data.state === "REVOKED") {
          clearInterval(_flMlPollTimer); _flMlPollTimer = null;
          _flMlQueue = []; _flMlQueueIdx = -1; _flMlQueueTotal = 0;
          const userStopped = data.state === "REVOKED" || (data.error || "").includes("__USER_STOPPED__");
          flMlProgress.classList.add("state-fail");
          flMlProgressStage.textContent = userStopped ? "✗ Stopped by user" : "✗ " + (data.error || "Failed").split("\n")[0];
          if (!userStopped) flMlLogOutput.textContent = data.error || "An unknown error occurred.";
          flMlStatus.textContent = userStopped ? "Machine labeling stopped." : "";
          flMlStatus.className   = "fe-extract-status";
          _flMlSetRunning(false);
        }
      } catch (err) {
        console.error("flMlPoll:", err);
      }
    }

    function _flMlBuildBody(videoStem) {
      const snapVal = flMlSnapshot.value;
      return {
        video_stem:           videoStem,
        shuffle:              parseInt(flMlShuffle.value) || 1,
        trainingsetindex:     parseInt(document.getElementById("fl-ml-tsidx").value) ?? 0,
        gputouse:             document.getElementById("fl-ml-gpu").value !== ""
                                ? parseInt(document.getElementById("fl-ml-gpu").value) : null,
        snapshot_path:        snapVal !== "-1" ? snapVal : null,
        likelihood_threshold: parseFloat(flMlLikelihood.value) || 0.9,
      };
    }

    async function _flMlDispatch(videoStem) {
      try {
        const res  = await fetch("/dlc/project/machine-label-frames", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify(_flMlBuildBody(videoStem)),
        });
        const data = await res.json();
        if (!res.ok) {
          flMlStatus.textContent = data.error || "Failed to start machine labeling.";
          flMlStatus.className   = "fe-extract-status err";
          _flMlQueue = []; _flMlQueueIdx = -1; _flMlQueueTotal = 0;
          _flMlSetRunning(false);
          return;
        }
        _flMlActiveTask = data.task_id;
        _flMlStartPolling(data.task_id);
      } catch (err) {
        flMlStatus.textContent = "Network error: " + err.message;
        flMlStatus.className   = "fe-extract-status err";
        _flMlQueue = []; _flMlQueueIdx = -1; _flMlQueueTotal = 0;
        _flMlSetRunning(false);
      }
    }

    flMlRunBtn.addEventListener("click", async () => {
      if (!_flVideoStem) {
        flMlStatus.textContent = "Select a labeled-data folder first.";
        flMlStatus.className   = "fe-extract-status err";
        return;
      }
      flMlStatus.textContent = "";
      flMlStatus.className   = "fe-extract-status";
      _flMlQueue = []; _flMlQueueIdx = -1; _flMlQueueTotal = 0;
      await _flMlDispatch(_flVideoStem);
    });

    flMlRunAllBtn.addEventListener("click", async () => {
      if (!_flStemData || _flStemData.length === 0) {
        flMlStatus.textContent = "No labeled-data folders loaded.";
        flMlStatus.className   = "fe-extract-status err";
        return;
      }
      flMlStatus.textContent = "";
      flMlStatus.className   = "fe-extract-status";
      _flMlQueue      = _flStemData.map(s => s.video_stem);
      _flMlQueueIdx   = 0;
      _flMlQueueTotal = _flMlQueue.length;
      flMlStatus.textContent = `Starting ${_flMlQueueTotal} folder(s)…`;
      flMlStatus.className   = "fe-extract-status ok";
      await _flMlDispatch(_flMlQueue[0]);
    });

    flMlStopBtn.addEventListener("click", async () => {
      if (!_flMlActiveTask) return;
      // Cancel the whole queue
      _flMlQueue = []; _flMlQueueIdx = -1; _flMlQueueTotal = 0;
      flMlStopBtn.disabled = true;
      try {
        await fetch("/dlc/project/machine-label-frames/stop", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ task_id: _flMlActiveTask }),
        });
        flMlStatus.textContent = "Stop signal sent.";
        flMlStatus.className   = "fe-extract-status";
      } catch (err) {
        flMlStatus.textContent = "Stop error: " + err.message;
        flMlStatus.className   = "fe-extract-status err";
        flMlStopBtn.disabled = false;
      }
    });

    // ══════════════════════════════════════════════════════════════
    // TAPNet propagation
    // ══════════════════════════════════════════════════════════════
    let _flTapPollTimer   = null;
    let _flTapActiveTask  = null;
    let _flTapConfirmed   = new Set();   // confirmed anchor frame names
    let _flTapnetFrames   = new Set();   // frames labeled by TAPNet

    // Load confirmed anchors + tapnet frames from server for current stem
    async function _flTapLoadSidecars() {
      if (!_flVideoStem) return;
      try {
        const res  = await fetch(`/dlc/project/tapnet-confirmed?video_stem=${encodeURIComponent(_flVideoStem)}`);
        if (!res.ok) return;
        const data = await res.json();
        _flTapConfirmed  = new Set(data.confirmed     || []);
        _flTapnetFrames  = new Set(data.tapnet_frames || []);
      } catch (_) { /* non-critical */ }
      _flTapUpdateFrameStatus();
      _flTapUpdateConfirmedCount();
    }

    // Update the per-frame badge + confirm button for the current frame
    function _flTapUpdateFrameStatus() {
      const fname = _flFrames[_flFrameIdx];
      if (!fname || !flTapCheckbox.checked) {
        flTapFrameBadge.classList.add("hidden");
        flTapConfirmBtn.classList.add("hidden");
        return;
      }
      flTapConfirmBtn.classList.remove("hidden");
      const isConfirmed = _flTapConfirmed.has(fname);
      const isTapnet    = _flTapnetFrames.has(fname);
      flTapConfirmLabel.textContent = isConfirmed ? "✓ Confirmed anchor" : "Confirm as anchor";
      flTapConfirmBtn.style.background = isConfirmed ? "var(--accent)" : "";
      flTapConfirmBtn.style.color      = isConfirmed ? "#fff" : "";
      if (isConfirmed) {
        flTapFrameBadge.textContent = "Anchor";
        flTapFrameBadge.style.color = "var(--accent)";
        flTapFrameBadge.style.borderColor = "var(--accent)";
        flTapFrameBadge.classList.remove("hidden");
      } else if (isTapnet) {
        flTapFrameBadge.textContent = "TAPNet";
        flTapFrameBadge.style.color = "var(--text-dim)";
        flTapFrameBadge.style.borderColor = "var(--border)";
        flTapFrameBadge.classList.remove("hidden");
      } else {
        flTapFrameBadge.classList.add("hidden");
      }
    }

    // Update the confirmed-count display and rerun button visibility
    function _flTapUpdateConfirmedCount() {
      const n = _flTapConfirmed.size;
      if (n > 0 && flTapCheckbox.checked) {
        flTapConfirmedCount.textContent = `${n} confirmed anchor${n === 1 ? "" : "s"}`;
        flTapConfirmedCount.classList.remove("hidden");
        flTapRerunBtn.classList.remove("hidden");
      } else {
        flTapConfirmedCount.classList.add("hidden");
        flTapRerunBtn.classList.add("hidden");
      }
    }

    flTapCheckbox.addEventListener("change", () => {
      flTapOpts.classList.toggle("hidden", !flTapCheckbox.checked);
      _flTapUpdateFrameStatus();
      _flTapUpdateConfirmedCount();
      if (flTapCheckbox.checked && _flVideoStem) _flTapLoadSidecars();
    });

    // Confirm / unconfirm current frame as anchor
    flTapConfirmBtn.addEventListener("click", async () => {
      const fname = _flFrames[_flFrameIdx];
      if (!fname || !_flVideoStem) return;
      try {
        const res  = await fetch("/dlc/project/tapnet-confirm-frame", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ video_stem: _flVideoStem, frame_name: fname }),
        });
        const data = await res.json();
        if (data.error) { console.error("confirm-frame:", data.error); return; }
        if (data.confirmed) _flTapConfirmed.add(fname);
        else                _flTapConfirmed.delete(fname);
        _flTapUpdateFrameStatus();
        _flTapUpdateConfirmedCount();
      } catch (err) {
        console.error("confirm-frame error:", err);
      }
    });

    // Re-run with confirmed anchors
    flTapRerunBtn.addEventListener("click", async () => {
      if (!_flVideoStem) {
        flTapStatus.textContent = "Select a labeled-data folder first.";
        flTapStatus.className   = "fe-extract-status err";
        return;
      }
      const ckpt = flTapCkpt.value.trim();
      if (!ckpt) {
        flTapStatus.textContent = "Enter the checkpoint path.";
        flTapStatus.className   = "fe-extract-status err";
        return;
      }
      flTapStatus.textContent = "";
      flTapStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch("/dlc/project/tapnet-propagate-multi", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            video_stem:            _flVideoStem,
            tapnet_checkpoint_path: ckpt,
            gpu_index:             0,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          flTapStatus.textContent = data.error || "Failed to start multi-anchor TAPNet.";
          flTapStatus.className   = "fe-extract-status err";
          return;
        }
        _flTapActiveTask = data.task_id;
        _flTapStartPolling(data.task_id);
      } catch (err) {
        flTapStatus.textContent = "Network error: " + err.message;
        flTapStatus.className   = "fe-extract-status err";
      }
    });

    function _flTapSetRunning(running) {
      flTapRunBtn.classList.toggle("hidden",  running);
      flTapStopBtn.classList.toggle("hidden", !running);
    }

    flTapCheckBtn.addEventListener("click", async () => {
      if (!_flVideoStem) {
        flTapCheckStatus.textContent = "Select a labeled-data folder first.";
        flTapCheckStatus.className   = "fe-extract-status err";
        return;
      }
      flTapCheckStatus.textContent = "Checking…";
      flTapCheckStatus.className   = "fe-extract-status";
      flTapSeqInfo.classList.add("hidden");
      try {
        const res  = await fetch(`/dlc/project/tapnet-check?video_stem=${encodeURIComponent(_flVideoStem)}`);
        const data = await res.json();
        if (!res.ok) {
          flTapCheckStatus.textContent = data.error || "Check failed.";
          flTapCheckStatus.className   = "fe-extract-status err";
          return;
        }
        const n = data.propagatable_count;
        flTapCheckStatus.textContent = `${n} propagatable sequence(s) found`;
        flTapCheckStatus.className   = "fe-extract-status " + (n > 0 ? "ok" : "");
        if (data.sequences && data.sequences.length > 0) {
          flTapSeqInfo.innerHTML = data.sequences.map(s => {
            const badge = s.propagatable
              ? `<span style="color:var(--accent)">✓ anchor: ${s.anchor}</span>`
              : `<span style="color:var(--text-dim)">✗ no labeled anchor</span>`;
            return `<div>${s.first_frame} → ${s.last_frame} &nbsp;(${s.frame_count} frames) &nbsp;${badge}</div>`;
          }).join("");
          flTapSeqInfo.classList.remove("hidden");
        }
      } catch (err) {
        flTapCheckStatus.textContent = "Network error: " + err.message;
        flTapCheckStatus.className   = "fe-extract-status err";
      }
    });

    flTapRunBtn.addEventListener("click", async () => {
      if (!_flVideoStem) {
        flTapStatus.textContent = "Select a labeled-data folder first.";
        flTapStatus.className   = "fe-extract-status err";
        return;
      }
      const ckpt = flTapCkpt.value.trim();
      if (!ckpt) {
        flTapStatus.textContent = "Enter the checkpoint path.";
        flTapStatus.className   = "fe-extract-status err";
        return;
      }
      flTapStatus.textContent = "";
      flTapStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch("/dlc/project/tapnet-propagate", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            video_stem:             _flVideoStem,
            tapnet_checkpoint_path: ckpt,
            anchor:                 flTapAnchor.value,
            gpu_index:              0,
            overwrite:              flTapOverwrite.checked,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          flTapStatus.textContent = data.error || "Failed to start TAPNet.";
          flTapStatus.className   = "fe-extract-status err";
          return;
        }
        _flTapActiveTask = data.task_id;
        _flTapStartPolling(data.task_id);
      } catch (err) {
        flTapStatus.textContent = "Network error: " + err.message;
        flTapStatus.className   = "fe-extract-status err";
      }
    });

    flTapStopBtn.addEventListener("click", async () => {
      if (!_flTapActiveTask) return;
      flTapStopBtn.disabled = true;
      try {
        await fetch("/dlc/project/tapnet-propagate/stop", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ task_id: _flTapActiveTask }),
        });
        flTapStatus.textContent = "Stop signal sent.";
        flTapStatus.className   = "fe-extract-status";
      } catch (err) {
        flTapStatus.textContent = "Stop error: " + err.message;
        flTapStatus.className   = "fe-extract-status err";
        flTapStopBtn.disabled = false;
      }
    });

    function _flTapStartPolling(taskId) {
      flTapProgress.classList.remove("hidden", "state-success", "state-fail");
      flTapTaskId.textContent        = taskId.slice(0, 12) + "…";
      flTapProgressBar.style.width   = "0%";
      flTapProgressPct.textContent   = "0 %";
      flTapProgressStage.textContent = "Queued";
      flTapLogOutput.textContent     = "Waiting for output…";
      _flTapSetRunning(true);
      if (_flTapPollTimer) clearInterval(_flTapPollTimer);
      _flTapPollTimer = setInterval(() => _flTapPoll(taskId), 2000);
      _flTapPoll(taskId);
    }

    async function _flTapPoll(taskId) {
      try {
        const res  = await fetch(`/status/${taskId}`);
        const data = await res.json();
        const pct  = Math.min(data.progress || 0, 100);
        flTapProgressBar.style.width   = pct + "%";
        flTapProgressPct.textContent   = pct + " %";
        flTapProgressStage.textContent = data.stage || data.state;
        if (data.log) {
          flTapLogOutput.textContent = data.log;
          flTapLogOutput.scrollTop   = flTapLogOutput.scrollHeight;
        }
        if (data.state === "SUCCESS") {
          clearInterval(_flTapPollTimer); _flTapPollTimer = null;
          const r = data.result || {};
          if (r.log) flTapLogOutput.textContent = r.log;
          flTapProgress.classList.add("state-success");
          flTapProgressStage.textContent = "✓ Propagation complete";
          flTapProgressBar.style.width   = "100%";
          flTapProgressPct.textContent   = "100 %";
          flTapStatus.textContent = `Done — ${r.frames_labeled || 0} frame(s) labeled across ${r.sequences_found || 0} sequence(s).`;
          flTapStatus.className   = "fe-extract-status ok";
          _flTapSetRunning(false);
          // Reload labels so the user sees propagated markers immediately
          if (_flVideoStem) {
            const found = _flStemData.find(s => s.video_stem === _flVideoStem);
            if (found) await _flSelectStem(found.video_stem, found.frames);
          }
        }
        if (data.state === "FAILURE" || data.state === "REVOKED") {
          clearInterval(_flTapPollTimer); _flTapPollTimer = null;
          const userStopped = data.state === "REVOKED" || (data.error || "").includes("__USER_STOPPED__");
          flTapProgress.classList.add("state-fail");
          flTapProgressStage.textContent = userStopped ? "✗ Stopped" : "✗ " + (data.error || "Failed").split("\n")[0];
          if (!userStopped) flTapLogOutput.textContent = data.error || "An unknown error occurred.";
          flTapStatus.textContent = userStopped ? "TAPNet stopped." : "";
          flTapStatus.className   = "fe-extract-status";
          _flTapSetRunning(false);
        }
      } catch (err) {
        console.error("flTapPoll:", err);
      }
    }

    function _flUpdateScorerFilename() {
      if (flScorerFilename) flScorerFilename.textContent = `CollectedData_${_flScorer}.csv`;
    }

    // ── Napari-inspired color palette ────────────────────────────
    const FL_COLORS = [
      "#f87171","#fb923c","#fbbf24","#a3e635","#34d399",
      "#22d3ee","#818cf8","#e879f9","#f43f5e","#10b981",
      "#3b82f6","#ec4899","#f59e0b","#84cc16","#06b6d4",
    ];
    function _flColor(i) { return FL_COLORS[i % FL_COLORS.length]; }

    // ── Open / close ────────────────────────────────────────────
    if (flOpenBtn) {
      flOpenBtn.addEventListener("click", () => {
        flCard.classList.remove("hidden");
        flCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
        _flLoad();
      });
    }

    flCloseBtn.addEventListener("click", () => {
      flCard.classList.add("hidden");
    });

    // ── Marker display controls ──────────────────────────────────
    const flZoomInput = document.getElementById("fl-zoom");
    const flZoomVal   = document.getElementById("fl-zoom-val");

    flZoomInput.addEventListener("input", () => {
      _flZoom = parseInt(flZoomInput.value, 10);
      flZoomVal.textContent = _flZoom + " %";
      if (_flImgLoaded) { _flFitCanvas(); _flDraw(); }
    });

    flMarkerSizeInput.addEventListener("input", () => {
      _flMarkerRadius = parseInt(flMarkerSizeInput.value, 10);
      flMarkerSizeVal.textContent = _flMarkerRadius;
      _flDraw();
    });

    flShowNamesInput.addEventListener("change", () => {
      _flShowNames = flShowNamesInput.checked;
      _flDraw();
    });

    // ── Load bodyparts + stems ───────────────────────────────────
    async function _flLoad() {
      try {
        const res  = await fetch("/dlc/project/bodyparts");
        const data = await res.json();
        _flBodyparts = data.bodyparts || [];
        _flScorer    = data.scorer    || "User";
        _flUpdateScorerFilename();
        _flRenderBodypartList();
      } catch (e) { console.error("FL bodyparts:", e); }
      await _flLoadStems();
    }

    async function _flLoadStems() {
      flStemStatus.textContent = "";
      flStemStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch("/dlc/project/labeled-frames");
        const data = await res.json();
        if (data.error) {
          flStemStatus.textContent = data.error;
          flStemStatus.className   = "fe-extract-status err";
          return;
        }
        _flStemData = data.video_stems || [];
        const prev  = flStemSelect.value;
        flStemSelect.innerHTML = '<option value="">— select video —</option>';
        _flStemData.forEach(s => {
          const opt = document.createElement("option");
          opt.value       = s.video_stem;
          opt.textContent = `${s.video_stem}  (${s.frames.length} frame${s.frames.length !== 1 ? "s" : ""})`;
          flStemSelect.appendChild(opt);
        });
        // Restore selection or auto-select if only one
        if (_flStemData.length === 1) {
          flStemSelect.value = _flStemData[0].video_stem;
          await _flSelectStem(_flStemData[0].video_stem, _flStemData[0].frames);
        } else if (prev && _flStemData.find(s => s.video_stem === prev)) {
          flStemSelect.value = prev;
          const found = _flStemData.find(s => s.video_stem === prev);
          if (found) await _flSelectStem(found.video_stem, found.frames);
        }
      } catch (e) {
        flStemStatus.textContent = `Error: ${e.message}`;
        flStemStatus.className   = "fe-extract-status err";
      }
    }

    flRefreshBtn.addEventListener("click", () => _flLoadStems());

    flStemSelect.addEventListener("change", async () => {
      const stem = flStemSelect.value;
      if (!stem) { flPlayerSec.classList.add("hidden"); return; }
      const found = _flStemData.find(s => s.video_stem === stem);
      if (found) await _flSelectStem(found.video_stem, found.frames);
    });

    async function _flSelectStem(stem, frames) {
      _flVideoStem = stem;
      _flFrames    = frames;
      _flFrameIdx  = 0;
      _flDirty     = false;

      // Fetch existing labels
      try {
        const res  = await fetch(`/dlc/project/labels/${encodeURIComponent(stem)}`);
        const data = await res.json();
        if (!data.error) {
          _flLabels = data.labels || {};
          _flScorer = data.scorer || _flScorer;
          _flUpdateScorerFilename();
        }
      } catch (_) { _flLabels = {}; }

      // Show "Update Threshold" button only when raw predictions exist
      flMlUpdateWrap.classList.add("hidden");
      flMlUpdateStatus.textContent = "";
      try {
        const r = await fetch(`/dlc/project/machine-label-raw?video_stem=${encodeURIComponent(stem)}`);
        const d = await r.json();
        if (d.exists) flMlUpdateWrap.classList.remove("hidden");
      } catch (_) {}

      flPlayerSec.classList.remove("hidden");
      _flUpdateLabelCount();
      _flShowFrame(0);

      // Load TAPNet sidecars (confirmed anchors + tapnet-labeled frames)
      _flTapConfirmed  = new Set();
      _flTapnetFrames  = new Set();
      _flTapLoadSidecars();
    }

    // ── Update Threshold ─────────────────────────────────────────
    flMlUpdateBtn.addEventListener("click", async () => {
      if (!_flVideoStem) return;
      flMlUpdateBtn.disabled       = true;
      flMlUpdateStatus.textContent = "Dispatching…";
      flMlUpdateStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch("/dlc/project/machine-label-reapply", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            video_stem:           _flVideoStem,
            likelihood_threshold: parseFloat(flMlLikelihood.value) || 0.9,
          }),
        });
        const dispatched = await res.json();
        if (!res.ok || dispatched.error) {
          flMlUpdateStatus.textContent = `Error: ${dispatched.error || "unknown"}`;
          flMlUpdateStatus.className   = "fe-extract-status err";
          flMlUpdateBtn.disabled = false;
          return;
        }

        // Poll until the worker finishes
        const taskId = dispatched.task_id;
        flMlUpdateStatus.textContent = "Applying…";
        let data = null;
        while (true) {
          await new Promise(r => setTimeout(r, 1000));
          const sr = await fetch(`/status/${taskId}`);
          const s  = await sr.json();
          if (s.state === "SUCCESS") {
            data = s.result;
            break;
          } else if (s.state === "FAILURE") {
            throw new Error(s.error || "Worker task failed");
          }
          // PENDING / PROGRESS — keep polling
        }

        flMlUpdateStatus.textContent =
          `Done — ${data.n_machine} machine label(s), ${data.n_human} human preserved`;
        flMlUpdateStatus.className = "fe-extract-status ok";
        // Reload labels so the labeler reflects the new threshold immediately
        const found = _flStemData.find(s => s.video_stem === _flVideoStem);
        if (found) await _flSelectStem(found.video_stem, found.frames);
      } catch (err) {
        flMlUpdateStatus.textContent = `Error: ${err.message}`;
        flMlUpdateStatus.className   = "fe-extract-status err";
      } finally {
        flMlUpdateBtn.disabled = false;
      }
    });

    // ── Render body-part chip list ───────────────────────────────
    function _flRenderBodypartList() {
      flBodypartList.innerHTML = "";
      if (!_flBodyparts.length) {
        flBpHint.classList.remove("hidden");
        return;
      }
      flBpHint.classList.add("hidden");
      _flBodyparts.forEach((bp, i) => {
        const chip = document.createElement("button");
        chip.className = "fl-bp-chip";
        chip.dataset.bp = bp;
        chip.style.setProperty("--fl-color", _flColor(i));
        chip.innerHTML =
          `<span class="fl-bp-dot"></span>` +
          `<span class="fl-bp-name">${bp}</span>` +
          `<svg class="fl-bp-check" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>` +
          `<svg class="fl-bp-eye-slash" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`;
        chip.addEventListener("click", () => _flSelectBp(bp));
        chip.addEventListener("dblclick", e => {
          e.preventDefault();
          _flSelectBp(bp);
          _flToggleVisibility(bp);
        });
        flBodypartList.appendChild(chip);
      });
      // Default: select first
      if (_flBodyparts.length) _flSelectBp(_flBodyparts[0]);
    }

    function _flSelectBp(bp) {
      _flSelectedBp = bp;
      flCanvas.style.cursor = "crosshair";
      flBodypartList.querySelectorAll(".fl-bp-chip").forEach(c => {
        c.classList.toggle("active", c.dataset.bp === bp);
      });
    }

    // ── Silent auto-save (fire-and-forget) ──────────────────────
    function _flAutoSave() {
      if (!_flVideoStem) return;
      fetch(`/dlc/project/labels/${encodeURIComponent(_flVideoStem)}`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ labels: _flLabels }),
      }).catch(() => {});  // silent — user can always use Save button
    }

    // ── Frame display ────────────────────────────────────────────
    function _flShowFrame(idx) {
      if (!_flFrames.length) return;
      // Auto-save unsaved changes before switching frames
      if (_flDirty) { _flDirty = false; _flAutoSave(); }
      idx = Math.max(0, Math.min(idx, _flFrames.length - 1));
      _flFrameIdx = idx;
      const fname = _flFrames[idx];
      flFrameInfo.textContent = `Frame ${idx + 1} / ${_flFrames.length}`;
      flFrameName.textContent = fname;

      _flUpdateBpChipStatus();
      _flUpdateLabelCount();
      _flTapUpdateFrameStatus();

      // Load frame image
      _flHoverBp   = null;
      _flImgLoaded = false;
      flCanvasLoading.classList.remove("hidden");
      const img   = new Image();
      img.onload  = () => {
        _flImg       = img;
        _flImgLoaded = true;
        flCanvasLoading.classList.add("hidden");
        _flFitCanvas();
        _flDraw();
      };
      img.onerror = () => {
        flCanvasLoading.textContent = "Failed to load frame.";
        flCanvasLoading.classList.remove("hidden");
      };
      img.src = `/dlc/project/frame-image/${encodeURIComponent(_flVideoStem)}/${encodeURIComponent(fname)}`;
    }

    function _flFitCanvas() {
      const wrap = flCanvas.parentElement;
      const cs   = getComputedStyle(flCard);
      const padL = parseFloat(cs.paddingLeft)  || 0;
      const padR = parseFloat(cs.paddingRight) || 0;

      // Base width = card's inner content width (canvas at 100% zoom)
      const baseW = flCard.clientWidth - padL - padR;

      // Maximum width: fill the viewport minus a small margin on each side.
      // The card is centred, so the canvas expands symmetrically beyond its border.
      const maxW    = Math.max(baseW, window.innerWidth - 32);
      const targetW = Math.min(Math.round(baseW * (_flZoom / 100)), Math.floor(maxW));

      flCanvas.width  = targetW;
      flCanvas.height = Math.round(_flImg.naturalHeight * (targetW / _flImg.naturalWidth));

      // Break out of card padding symmetrically — card has no overflow:hidden so
      // the wrapper renders beyond the card border without clipping.
      const extra = targetW - baseW;
      if (extra > 0) {
        wrap.style.width      = targetW + "px";
        wrap.style.marginLeft = `-${extra / 2}px`;
      } else {
        wrap.style.width      = "";
        wrap.style.marginLeft = "";
      }
    }

    // Re-fit whenever the card width changes (window resize, layout shifts)
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => {
        if (_flImgLoaded) { _flFitCanvas(); _flDraw(); }
      }).observe(flCard);
    }

    function _flDraw() {
      if (!_flImgLoaded) return;
      flCtx.clearRect(0, 0, flCanvas.width, flCanvas.height);
      flCtx.drawImage(_flImg, 0, 0, flCanvas.width, flCanvas.height);

      const fname       = _flFrames[_flFrameIdx];
      const frameLabels = _flLabels[fname] || {};
      const scaleX      = flCanvas.width  / _flImg.naturalWidth;
      const scaleY      = flCanvas.height / _flImg.naturalHeight;
      const r           = _flMarkerRadius;

      _flBodyparts.forEach((bp, i) => {
        const pt = frameLabels[bp];
        if (!pt) return;
        if (_flHidden[fname] && _flHidden[fname][bp]) return;
        const cx    = pt[0] * scaleX;
        const cy    = pt[1] * scaleY;
        const color = _flColor(i);

        // Selection ring for the active bodypart
        if (bp === _flSelectedBp) {
          flCtx.beginPath();
          flCtx.arc(cx, cy, r + 3.5, 0, Math.PI * 2);
          flCtx.strokeStyle = "rgba(255,255,255,0.85)";
          flCtx.lineWidth   = 2;
          flCtx.stroke();
        }

        // Filled circle with a thin dark outline for contrast
        flCtx.beginPath();
        flCtx.arc(cx, cy, r, 0, Math.PI * 2);
        flCtx.fillStyle = color;
        flCtx.fill();
        flCtx.strokeStyle = "rgba(0,0,0,0.55)";
        flCtx.lineWidth   = 1.2;
        flCtx.stroke();

        if (_flShowNames || bp === _flHoverBp) {
          flCtx.font = "bold 11px 'JetBrains Mono', monospace";
          const tw = flCtx.measureText(bp).width;
          const tx = cx + r + 4;
          const ty = cy + 4;
          flCtx.fillStyle = "rgba(12,13,16,.65)";
          flCtx.fillRect(tx - 2, ty - 11, tw + 6, 14);
          flCtx.fillStyle = color;
          flCtx.fillText(bp, tx + 1, ty);
        }
      });
    }

    // ── Canvas interaction ───────────────────────────────────────
    function _flHitTest(cx, cy, fname) {
      const frameLabels = _flLabels[fname] || {};
      const scaleX = flCanvas.width  / _flImg.naturalWidth;
      const scaleY = flCanvas.height / _flImg.naturalHeight;
      const hitR   = _flMarkerRadius + 6;
      let hit = null;
      _flBodyparts.forEach(bp => {
        const pt = frameLabels[bp];
        if (!pt) return;
        if (_flHidden[fname] && _flHidden[fname][bp]) return;
        const dx = pt[0] * scaleX - cx;
        const dy = pt[1] * scaleY - cy;
        if (Math.sqrt(dx * dx + dy * dy) <= hitR) hit = bp;
      });
      return hit;
    }

    flCanvas.addEventListener("click", e => {
      if (!_flImgLoaded || !_flVideoStem) return;
      const rect = flCanvas.getBoundingClientRect();
      const cx   = e.clientX - rect.left;
      const cy   = e.clientY - rect.top;
      const fname = _flFrames[_flFrameIdx];

      // Click near an existing marker → select it
      const hit = _flHitTest(cx, cy, fname);
      if (hit) {
        _flSelectBp(hit);
        return;
      }

      // Click on empty space → place point for selected bp
      if (!_flSelectedBp) return;
      const scaleX = flCanvas.width  / _flImg.naturalWidth;
      const scaleY = flCanvas.height / _flImg.naturalHeight;
      if (!_flLabels[fname]) _flLabels[fname] = {};
      _flLabels[fname][_flSelectedBp] = [cx / scaleX, cy / scaleY];
      _flDirty = true;
      _flDraw();
      _flUpdateBpChipStatus();
      _flUpdateLabelCount();
      _flAutoAdvanceBp();
    });

    // Right-click → remove current body-part point
    flCanvas.addEventListener("contextmenu", e => {
      e.preventDefault();
      if (!_flSelectedBp || !_flVideoStem) return;
      _flRemoveBpLabel(_flSelectedBp);
    });

    flCanvas.addEventListener("mousemove", e => {
      if (!_flImgLoaded) return;
      const rect  = flCanvas.getBoundingClientRect();
      const cx    = e.clientX - rect.left;
      const cy    = e.clientY - rect.top;
      const fname = _flFrames[_flFrameIdx];
      const found = _flHitTest(cx, cy, fname);
      if (found !== _flHoverBp) {
        _flHoverBp = found;
        _flDraw();
      }
      flCanvas.style.cursor = found ? "pointer" : (_flSelectedBp ? "crosshair" : "default");
    });

    flCanvas.addEventListener("mouseenter", () => { _flCursorInCanvas = true; });
    flCanvas.addEventListener("mouseleave", () => {
      _flCursorInCanvas = false;
      if (_flHoverBp) { _flHoverBp = null; _flDraw(); }
      flCanvas.style.cursor = _flSelectedBp ? "crosshair" : "default";
    });

    function _flRemoveBpLabel(bp) {
      const fname = _flFrames[_flFrameIdx];
      if (!fname || !_flLabels[fname]) return;
      _flLabels[fname][bp] = null;
      // Also clear hidden state when marker is deleted
      if (_flHidden[fname]) delete _flHidden[fname][bp];
      _flDirty = true;
      _flDraw();
      _flUpdateBpChipStatus();
      _flUpdateLabelCount();
    }

    function _flToggleVisibility(bp) {
      const fname = _flFrames[_flFrameIdx];
      if (!fname) return;
      if (!_flHidden[fname]) _flHidden[fname] = {};
      _flHidden[fname][bp] = !_flHidden[fname][bp];
      _flDraw();
      _flUpdateBpChipStatus();
    }

    // Clear all body-part markers on the currently displayed frame
    function _flClearFrame() {
      const fname = _flFrames[_flFrameIdx];
      if (!fname) return;
      if (!_flLabels[fname]) _flLabels[fname] = {};
      _flBodyparts.forEach(bp => { _flLabels[fname][bp] = null; });
      delete _flHidden[fname];
      _flDirty = true;
      _flDraw();
      _flUpdateBpChipStatus();
      _flUpdateLabelCount();
    }

    // Double-click on Clear Frame button erases all markers on current frame
    document.getElementById("fl-btn-clear-frame").addEventListener("dblclick", e => {
      e.preventDefault();
      if (!_flVideoStem) return;
      _flClearFrame();
    });

    document.getElementById("fl-btn-delete-frame").addEventListener("dblclick", async e => {
      e.preventDefault();
      const fname = _flFrames[_flFrameIdx];
      if (!_flVideoStem || !fname) return;

      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        const res = await fetch("/dlc/project/frame", {
          method:  "DELETE",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ video_stem: _flVideoStem, frame_name: fname }),
        });
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          flSaveStatus.textContent = `Delete failed: ${d.error || res.status}`;
          flSaveStatus.className   = "fl-save-status err";
          return;
        }
        // Remove from in-memory state
        delete _flLabels[fname];
        _flFrames.splice(_flFrameIdx, 1);
        _flUpdateLabelCount();

        if (_flFrames.length === 0) {
          // No frames left — reset canvas
          _flFrameIdx = 0;
          flFrameInfo.textContent = "Frame 0 / 0";
          flFrameName.textContent = "";
          const ctx = flCanvas.getContext("2d");
          ctx.clearRect(0, 0, flCanvas.width, flCanvas.height);
        } else {
          _flShowFrame(Math.min(_flFrameIdx, _flFrames.length - 1));
        }
        flSaveStatus.textContent = `Deleted ${fname}`;
        flSaveStatus.className   = "fl-save-status";
      } catch (err) {
        flSaveStatus.textContent = `Delete error: ${err.message}`;
        flSaveStatus.className   = "fl-save-status err";
      } finally {
        btn.disabled = false;
      }
    });

    // Auto-advance to the next unlabeled body part (napari behavior)
    function _flAutoAdvanceBp() {
      const fname       = _flFrames[_flFrameIdx];
      const frameLabels = _flLabels[fname] || {};
      const cur         = _flBodyparts.indexOf(_flSelectedBp);
      for (let i = 1; i <= _flBodyparts.length; i++) {
        const next = _flBodyparts[(cur + i) % _flBodyparts.length];
        if (!frameLabels[next]) { _flSelectBp(next); return; }
      }
      // All body parts labeled on this frame → move to next frame
      if (_flFrameIdx < _flFrames.length - 1) _flShowFrame(_flFrameIdx + 1);
    }

    // ── Chip status updates ──────────────────────────────────────
    function _flUpdateBpChipStatus() {
      const fname       = _flFrames[_flFrameIdx];
      const frameLabels = _flLabels[fname] || {};
      flBodypartList.querySelectorAll(".fl-bp-chip").forEach(c => {
        const bp = c.dataset.bp;
        const pt = frameLabels[bp];
        const isHidden = !!(_flHidden[fname] && _flHidden[fname][bp]);
        c.classList.toggle("labeled",    !!(pt && pt[0] !== null));
        c.classList.toggle("vis-hidden", !!(pt && pt[0] !== null) && isHidden);
      });
    }

    function _flUpdateLabelCount() {
      const labeled = Object.values(_flLabels).filter(fl =>
        _flBodyparts.some(bp => fl && fl[bp] && fl[bp][0] !== null)
      ).length;
      flLabelCount.textContent = `${labeled} / ${_flFrames.length} frame${_flFrames.length !== 1 ? "s" : ""} labeled`;
    }

    // ── Navigation ───────────────────────────────────────────────
    flBtnPrev.addEventListener("click", () => _flShowFrame(_flFrameIdx - 1));
    flBtnNext.addEventListener("click", () => _flShowFrame(_flFrameIdx + 1));

    document.addEventListener("keydown", e => {
      if (flCard.classList.contains("hidden")) return;
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;

      // WASD nudge: move the selected marker when cursor is inside the canvas
      // and the current frame already has a point placed for the active body part.
      const _wasdKeys = ["a", "d", "w", "s"];
      if (_wasdKeys.includes(e.key) && _flCursorInCanvas && _flSelectedBp && _flVideoStem) {
        const fname = _flFrames[_flFrameIdx];
        const pt    = fname && _flLabels[fname] && _flLabels[fname][_flSelectedBp];
        if (pt && pt[0] !== null) {
          e.preventDefault();
          const step = e.shiftKey ? 10 : 1;
          let [x, y] = pt;
          if (e.key === "a") x -= step;
          if (e.key === "d") x += step;
          if (e.key === "w") y -= step;
          if (e.key === "s") y += step;
          // Clamp to image dimensions
          x = Math.max(0, Math.min(x, _flImg.naturalWidth  - 1));
          y = Math.max(0, Math.min(y, _flImg.naturalHeight - 1));
          _flLabels[fname][_flSelectedBp] = [x, y];
          _flDirty = true;
          _flDraw();
          return;
        }
      }

      // Tab / Shift+Tab — cycle through body parts
      if (e.key === "Tab" && _flVideoStem) {
        e.preventDefault();
        const cur  = _flBodyparts.indexOf(_flSelectedBp);
        const next = e.shiftKey
          ? (_flBodyparts.length + cur - 1) % _flBodyparts.length
          : (cur + 1) % _flBodyparts.length;
        _flSelectBp(_flBodyparts[next]);
        return;
      }

      // Spacebar — toggle visibility of selected marker
      if (e.key === " " && _flSelectedBp && _flVideoStem) {
        e.preventDefault();
        _flToggleVisibility(_flSelectedBp);
        return;
      }

      // Backspace — delete selected marker (no cursor-in-canvas requirement)
      if (e.key === "Backspace" && _flSelectedBp && _flVideoStem) {
        e.preventDefault();
        _flRemoveBpLabel(_flSelectedBp);
        return;
      }

      // Frame navigation (arrow keys)
      if (e.key === "ArrowLeft")  { e.preventDefault(); _flShowFrame(_flFrameIdx - 1); }
      if (e.key === "ArrowRight") { e.preventDefault(); _flShowFrame(_flFrameIdx + 1); }

      // Delete (with cursor over canvas) — also deletes selected marker
      if (e.key === "Delete" && _flCursorInCanvas && _flSelectedBp && _flVideoStem) {
        e.preventDefault();
        _flRemoveBpLabel(_flSelectedBp);
      }
    });

    // ── Save ─────────────────────────────────────────────────────
    flBtnSave.addEventListener("click", async () => {
      if (!_flVideoStem) return;
      flBtnSave.disabled      = true;
      flSaveStatus.textContent = "Saving…";
      flSaveStatus.className   = "fl-save-status";
      try {
        const res  = await fetch(`/dlc/project/labels/${encodeURIComponent(_flVideoStem)}`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ labels: _flLabels }),
        });
        const data = await res.json();
        if (res.ok) {
          _flDirty = false;
          const h5note = data.h5_warning ? ` (H5 warning: ${data.h5_warning})` : (data.h5_path ? " + H5" : "");
          flSaveStatus.textContent = `Saved ✓${h5note}`;
          flSaveStatus.className   = "fl-save-status ok";
        } else {
          flSaveStatus.textContent = data.error || "Error saving";
          flSaveStatus.className   = "fl-save-status err";
        }
      } catch (err) {
        flSaveStatus.textContent = `Network error: ${err.message}`;
        flSaveStatus.className   = "fl-save-status err";
      }
      flBtnSave.disabled = false;
      setTimeout(() => {
        flSaveStatus.textContent = "";
        flSaveStatus.className   = "fl-save-status";
      }, 4000);
    });

    // ── Save all to H5 ───────────────────────────────────────────
    flBtnSaveH5.addEventListener("click", async () => {
      if (!_flVideoStem) return;

      // First flush current frame's CSV
      flBtnSaveH5.disabled     = true;
      flSaveStatus.textContent = "Saving CSV…";
      flSaveStatus.className   = "fl-save-status";
      try {
        const csvRes = await fetch(`/dlc/project/labels/${encodeURIComponent(_flVideoStem)}`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ labels: _flLabels }),
        });
        if (!csvRes.ok) {
          const d = await csvRes.json();
          flSaveStatus.textContent = d.error || "CSV save failed";
          flSaveStatus.className   = "fl-save-status err";
          flBtnSaveH5.disabled = false;
          return;
        }
        _flDirty = false;
      } catch (err) {
        flSaveStatus.textContent = `Network error: ${err.message}`;
        flSaveStatus.className   = "fl-save-status err";
        flBtnSaveH5.disabled = false;
        return;
      }

      // Dispatch Celery task for the full convertcsv2h5
      flSaveStatus.textContent = "Converting to H5…";
      try {
        const res  = await fetch("/dlc/project/labels/convert-to-h5", { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
          flSaveStatus.textContent = data.error || "Failed to dispatch H5 conversion";
          flSaveStatus.className   = "fl-save-status err";
          flBtnSaveH5.disabled = false;
          return;
        }

        // Poll until done
        const taskId = data.task_id;
        const poll = setInterval(async () => {
          try {
            const tr   = await fetch(`/status/${taskId}`);
            const td   = await tr.json();
            if (td.state === "SUCCESS") {
              clearInterval(poll);
              const r   = td.result || {};
              const cnt = (r.converted || []).length;
              const sk  = (r.skipped  || []).length;
              const note = sk > 0 ? `, ${sk} skipped` : "";
              flSaveStatus.textContent = `Saved ✓ CSV + H5 (${cnt} folder${cnt !== 1 ? "s" : ""}${note})`;
              flSaveStatus.className   = "fl-save-status ok";
              flBtnSaveH5.disabled = false;
              setTimeout(() => { flSaveStatus.textContent = ""; flSaveStatus.className = "fl-save-status"; }, 6000);
            } else if (td.state === "FAILURE" || td.state === "REVOKED") {
              clearInterval(poll);
              const errFull = td.error || td.state || "";
              // Show the last non-empty line (most specific part of the traceback)
              const lines   = errFull.split("\n").map(l => l.trim()).filter(Boolean);
              const errLine = lines[lines.length - 1] || errFull;
              flSaveStatus.textContent = "H5 failed: " + errLine;
              flSaveStatus.title       = errFull;   // full traceback on hover
              flSaveStatus.className   = "fl-save-status err";
              console.error("H5 conversion traceback:\n", errFull);
              flBtnSaveH5.disabled = false;
            }
          } catch (_) {}
        }, 1500);
      } catch (err) {
        flSaveStatus.textContent = `Network error: ${err.message}`;
        flSaveStatus.className   = "fl-save-status err";
        flBtnSaveH5.disabled = false;
      }
    });

    // ── Redraw on resize ─────────────────────────────────────────
    window.addEventListener("resize", () => {
      if (!flCard.classList.contains("hidden") && _flImgLoaded) {
        _flFitCanvas();
        _flDraw();
      }
    });
  })();

  // ── DLC config.yaml editor ───────────────────────────────────
  (function () {
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
  })();

  // ── Create Training Dataset ──────────────────────────────────
  (function () {
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
  })();

  // ── Train Network ────────────────────────────────────────────
  (function () {
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

    // ── Engine display (reads module-level _dlcEngine set when project loads) ──
    function _tnDetectEngine() {
      _tnEngine = _dlcEngine;
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
        _tnDetectEngine(); // sync — reads _dlcEngine set when project was loaded
        _populateGpuSelect("tn-gputouse");
        // Check if a training job is already running; if so, reconnect to it
        try {
          const res  = await fetch("/dlc/training/jobs");
          const data = await res.json();
          const activeJob = (data.jobs || []).find(
            j => (j.status === "running" || j.status === "dead") && j.operation !== "analyze"
          );
          _dlcTrainingActive = !!activeJob;
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
      if (_dlcTrainingActive) {
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
      _dlcTrainingActive     = running; // keep module-level flag in sync
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
  })();

  // ── Shared GPU select helper ──────────────────────────────────
  async function _populateGpuSelect(selectId) {
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
  (() => {
    const avCard         = document.getElementById("analyze-card");
    const avOpenBtn      = document.getElementById("btn-open-analyze");
    const avCloseBtn     = document.getElementById("btn-close-analyze");
    const avTargetPath   = document.getElementById("av-target-path");
    const avBrowseBtn    = document.getElementById("av-browse-btn");
    const avBrowser      = document.getElementById("av-browser");
    const avSnapshot     = document.getElementById("av-snapshot");
    const avRefreshSnaps = document.getElementById("av-refresh-snapshots");
    const avRunBtn       = document.getElementById("btn-run-analyze");
    const avStopBtn      = document.getElementById("btn-stop-analyze");
    const avRunStatus    = document.getElementById("av-run-status");
    const avProgress     = document.getElementById("av-progress");
    const avTaskId       = document.getElementById("av-task-id");
    const avProgressBar  = document.getElementById("av-progress-bar");
    const avProgressStage= document.getElementById("av-progress-stage");
    const avProgressPct  = document.getElementById("av-progress-pct");
    const avLogOutput    = document.getElementById("av-log-output");

    let _avPollTimer  = null;
    let _avActiveTask = null;
    let _avBrowserLoaded = false;
    let _avProjectPath   = null;   // set when browse data arrives

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
        });
      } catch (err) {
        console.error("avLoadSnapshots:", err);
      }
    }

    avRefreshSnaps.addEventListener("click", _avLoadSnapshots);

    // Reload snapshots when shuffle changes (indices are per-shuffle)
    document.getElementById("av-shuffle").addEventListener("change", _avLoadSnapshots);

    // ── Project browser ───────────────────────────────────────
    const _AV_VIDEO_EXTS = new Set(['.mp4','.avi','.mov','.mkv','.wmv','.m4v']);
    const _AV_IMAGE_EXTS = new Set(['.jpg','.jpeg','.png','.bmp','.tif','.tiff']);
    function _avSupportedFile(name) {
      const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')).toLowerCase() : '';
      return _AV_VIDEO_EXTS.has(ext) || _AV_IMAGE_EXTS.has(ext);
    }

    function _avMakeEntry(name, fullPath, isDir) {
      const wrapper = document.createElement("div");
      const row = document.createElement("div");
      row.style.cssText = "display:flex;align-items:center;gap:.3rem;padding:.18rem .4rem;cursor:pointer;border-radius:4px;user-select:none";
      row.title = fullPath;

      const arrow = document.createElement("span");
      arrow.style.cssText = "font-size:.62rem;color:var(--text-dim);flex-shrink:0;width:.8rem;text-align:center";
      arrow.textContent = isDir ? "▶" : "";

      const icon = document.createElement("span");
      icon.style.flexShrink = "0";
      icon.textContent = isDir ? "📁" : "📄";

      const label = document.createElement("span");
      label.textContent = name;
      label.style.cssText = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono);font-size:.75rem;flex:1;min-width:0";

      row.appendChild(arrow); row.appendChild(icon); row.appendChild(label);
      wrapper.appendChild(row);

      row.addEventListener("mouseenter", () => row.style.background = "var(--surface-3,#2a2a2a)");
      row.addEventListener("mouseleave", () => row.style.background = "");

      if (isDir) {
        const childContainer = document.createElement("div");
        childContainer.style.cssText = "display:none;padding-left:1rem";
        wrapper.appendChild(childContainer);
        let loaded = false, expanded = false;

        row.addEventListener("click", async () => {
          if (!expanded && !loaded) {
            childContainer.innerHTML = `<span style="font-size:.72rem;color:var(--text-dim);padding:.15rem .4rem;display:block">Loading…</span>`;
            childContainer.style.display = "block";
            try {
              const res = await fetch(`/fs/ls?path=${encodeURIComponent(fullPath)}`);
              const d   = await res.json();
              childContainer.innerHTML = "";
              if (!d.error) {
                const vis = d.entries.filter(e => (e.type === "dir" && e.has_media !== false) || (e.type === "file" && _avSupportedFile(e.name)));
                vis.forEach(e =>
                  childContainer.appendChild(_avMakeEntry(e.name, fullPath.replace(/\/+$/, "") + "/" + e.name, e.type === "dir"))
                );
                if (!vis.length)
                  childContainer.innerHTML = `<span style="font-size:.72rem;color:var(--text-dim);padding:.15rem .4rem;display:block">(no supported files)</span>`;
              } else {
                childContainer.innerHTML = `<span style="font-size:.72rem;color:var(--text-dim);padding:.15rem .4rem;display:block">${d.error}</span>`;
              }
            } catch (e) {
              childContainer.innerHTML = `<span style="font-size:.72rem;color:var(--text-dim);padding:.15rem .4rem;display:block">Error loading.</span>`;
            }
            loaded = true; expanded = true; arrow.textContent = "▼";
          } else {
            expanded = !expanded;
            childContainer.style.display = expanded ? "block" : "none";
            arrow.textContent = expanded ? "▼" : "▶";
          }
        });
      }

      // double-click selects the path and closes the browser
      row.addEventListener("dblclick", (e) => {
        e.stopPropagation();
        avTargetPath.value = fullPath;
        avBrowser.classList.add("hidden");
        _avBrowserLoaded = false;
      });

      return wrapper;
    }

    async function _avBrowseDir(dirPath) {
      _avBrowserLoaded = false;
      avBrowser.innerHTML = `<span style="font-size:.8rem;color:var(--text-dim)">Loading…</span>`;
      try {
        const res  = await fetch(`/fs/ls?path=${encodeURIComponent(dirPath)}`);
        const data = await res.json();
        if (data.error) { avBrowser.textContent = data.error; return; }
        avBrowser.innerHTML = "";

        // Header: current path + Up button
        const header = document.createElement("div");
        header.style.cssText = "display:flex;align-items:center;gap:.4rem;padding:.2rem .3rem .35rem;border-bottom:1px solid var(--border);margin-bottom:.25rem;min-width:0";
        const pathLabel = document.createElement("span");
        pathLabel.style.cssText = "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono);font-size:.72rem;color:var(--text-dim)";
        pathLabel.textContent = data.path;
        pathLabel.title = data.path;
        header.appendChild(pathLabel);
        if (data.parent) {
          const upBtn = document.createElement("button");
          upBtn.className = "btn-sm";
          upBtn.style.cssText = "padding:.15rem .5rem;font-size:.72rem;flex-shrink:0";
          upBtn.textContent = "↑ Up";
          upBtn.addEventListener("click", (e) => { e.stopPropagation(); _avBrowseDir(data.parent); });
          header.appendChild(upBtn);
        }
        avBrowser.appendChild(header);

        const visible = data.entries.filter(e => (e.type === "dir" && e.has_media !== false) || (e.type === "file" && _avSupportedFile(e.name)));
        if (!visible.length) {
          const empty = document.createElement("span");
          empty.style.cssText = "font-size:.78rem;color:var(--text-dim);padding:.3rem;display:block";
          empty.textContent = "(no supported video or image files)";
          avBrowser.appendChild(empty);
        } else {
          visible.forEach(e =>
            avBrowser.appendChild(_avMakeEntry(e.name, data.path.replace(/\/+$/, "") + "/" + e.name, e.type === "dir"))
          );
        }
        _avBrowserLoaded = true;
      } catch (err) {
        avBrowser.textContent = "Failed to load.";
        console.error("avBrowseDir:", err);
      }
    }

    avBrowseBtn.addEventListener("click", async () => {
      const isHidden = avBrowser.classList.contains("hidden");
      avBrowser.classList.toggle("hidden");
      if (!isHidden) return;  // closing
      if (_avBrowserLoaded) return;  // already showing content
      try {
        const res  = await fetch("/dlc/project/browse");
        const data = await res.json();
        if (data.error) { avBrowser.textContent = data.error; return; }
        _avProjectPath = data.project_path;
        await _avBrowseDir(data.project_path);
      } catch (err) {
        avBrowser.textContent = "Failed to load project.";
        console.error("avBrowse:", err);
      }
    });

    // ── Running state helpers ─────────────────────────────────
    function _avSetRunning(running) {
      avRunBtn.classList.toggle("hidden",  running);
      avStopBtn.classList.toggle("hidden", !running);
      avRunBtn.disabled  = running;
      const _clvBtn = document.getElementById("btn-create-labeled-video");
      if (_clvBtn) _clvBtn.disabled = running;
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
          avProgressStage.textContent = "✓ Analysis complete";
          avProgressBar.style.width   = "100%";
          avProgressPct.textContent   = "100 %";
          avRunStatus.textContent = "Analysis finished successfully.";
          avRunStatus.className   = "fe-extract-status ok";
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
          avRunStatus.textContent = userStopped ? "Analysis stopped." : "";
          avRunStatus.className   = "fe-extract-status";
          _avSetRunning(false);
        }
      } catch (err) {
        console.error("Analyze poll error:", err);
      }
    }

    // ── Open / Close ──────────────────────────────────────────
    avOpenBtn?.addEventListener("click", async () => {
      avCard.classList.remove("hidden");
      avCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      _avLoadSnapshots();
      _populateGpuSelect("av-gputouse");
      _avBrowserLoaded = false;
      avBrowser.classList.add("hidden");
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

    // ── Run ───────────────────────────────────────────────────
    avRunBtn.addEventListener("click", async () => {
      const target = avTargetPath.value.trim();
      if (!target) {
        avRunStatus.textContent = "Please enter a target path.";
        avRunStatus.className   = "fe-extract-status err";
        return;
      }

      avRunStatus.textContent = "";
      avRunStatus.className   = "fe-extract-status";

      const snapshotVal = avSnapshot.value;

      const batchSizeVal = document.getElementById("av-batch-size").value;
      const clvPcutoff   = document.getElementById("clv-pcutoff").value;
      const body = {
        target_path:      target,
        shuffle:          parseInt(document.getElementById("av-shuffle").value) || 1,
        trainingsetindex: parseInt(document.getElementById("av-trainingsetindex").value) ?? 0,
        gputouse:         document.getElementById("av-gputouse").value !== ""
                            ? parseInt(document.getElementById("av-gputouse").value)
                            : null,
        batch_size:       batchSizeVal !== "" ? parseInt(batchSizeVal) : null,
        save_as_csv:      document.getElementById("av-save-csv").checked,
        create_labeled:   document.getElementById("av-create-labeled").checked,
        snapshot_path:    snapshotVal !== "-1" ? snapshotVal : null,
        // labeled video params
        pcutoff:          clvPcutoff !== "" ? parseFloat(clvPcutoff) : null,
        dotsize:          parseInt(document.getElementById("clv-dotsize").value) || 8,
        colormap:         document.getElementById("clv-colormap").value,
        modelprefix:      (document.getElementById("clv-modelprefix").value || "").trim(),
        filtered:         document.getElementById("clv-filtered").checked,
        draw_skeleton:    document.getElementById("clv-draw-skeleton").checked,
        overwrite:        document.getElementById("clv-overwrite").checked,
        destfolder:       (document.getElementById("clv-destfolder").value || "").trim() || null,
      };

      try {
        const res  = await fetch("/dlc/project/analyze", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) {
          avRunStatus.textContent = data.error || "Failed to start analysis.";
          avRunStatus.className   = "fe-extract-status err";
          return;
        }
        _avActiveTask = data.task_id;
        _avStartPolling(data.task_id);
      } catch (err) {
        avRunStatus.textContent = "Network error: " + err.message;
        avRunStatus.className   = "fe-extract-status err";
      }
    });

    // ── Stop ──────────────────────────────────────────────────
    avStopBtn.addEventListener("click", async () => {
      if (!_avActiveTask) return;
      avStopBtn.disabled = true;
      try {
        await fetch("/dlc/project/analyze/stop", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ task_id: _avActiveTask }),
        });
        avRunStatus.textContent = "Stop signal sent — analysis will terminate shortly.";
        avRunStatus.className   = "fe-extract-status";
      } catch (err) {
        avRunStatus.textContent = "Stop error: " + err.message;
        avRunStatus.className   = "fe-extract-status err";
        avStopBtn.disabled = false;
      }
    });

    // ── Create Labeled Video (standalone) ─────────────────────
    const clvBtn        = document.getElementById("btn-create-labeled-video");
    const clvStatus     = document.getElementById("av-clv-status");
    const clvDestInput  = document.getElementById("clv-destfolder");
    const clvDestBrowse = document.getElementById("clv-dest-browse-btn");
    const clvDestClear  = document.getElementById("clv-dest-clear-btn");
    const clvDestBrowser= document.getElementById("clv-dest-browser");

    // destfolder browser — shows directories only, double-click selects
    async function _clvBrowseDir(dirPath) {
      clvDestBrowser.innerHTML = `<span style="font-size:.8rem;color:var(--text-dim)">Loading…</span>`;
      try {
        const res  = await fetch(`/fs/ls?path=${encodeURIComponent(dirPath)}`);
        const data = await res.json();
        if (data.error) { clvDestBrowser.textContent = data.error; return; }
        clvDestBrowser.innerHTML = "";

        // Header
        const hdr = document.createElement("div");
        hdr.style.cssText = "display:flex;align-items:center;gap:.4rem;padding:.2rem .3rem .35rem;border-bottom:1px solid var(--border);margin-bottom:.25rem";
        const pathLbl = document.createElement("span");
        pathLbl.style.cssText = "flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono);font-size:.72rem;color:var(--text-dim)";
        pathLbl.textContent = data.path;
        hdr.appendChild(pathLbl);
        // "Select this folder" button
        const selBtn = document.createElement("button");
        selBtn.className = "btn-sm";
        selBtn.style.cssText = "padding:.15rem .5rem;font-size:.72rem;flex-shrink:0";
        selBtn.textContent = "✓ Select";
        selBtn.addEventListener("click", () => {
          clvDestInput.value = data.path;
          clvDestBrowser.classList.add("hidden");
        });
        hdr.appendChild(selBtn);
        if (data.parent) {
          const upBtn = document.createElement("button");
          upBtn.className = "btn-sm";
          upBtn.style.cssText = "padding:.15rem .5rem;font-size:.72rem;flex-shrink:0";
          upBtn.textContent = "↑ Up";
          upBtn.addEventListener("click", (e) => { e.stopPropagation(); _clvBrowseDir(data.parent); });
          hdr.appendChild(upBtn);
        }
        clvDestBrowser.appendChild(hdr);

        const dirs = data.entries.filter(e => e.type === "dir");
        if (!dirs.length) {
          const em = document.createElement("span");
          em.style.cssText = "font-size:.78rem;color:var(--text-dim);padding:.3rem;display:block";
          em.textContent = "(no subdirectories)";
          clvDestBrowser.appendChild(em);
        }
        dirs.forEach(e => {
          const row = document.createElement("div");
          const fullPath = data.path.replace(/\/+$/, "") + "/" + e.name;
          row.style.cssText = "display:flex;align-items:center;gap:.4rem;padding:.18rem .4rem;cursor:pointer;border-radius:4px;user-select:none;font-size:.78rem";
          row.innerHTML = `<span style="flex-shrink:0">📁</span><span style="font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${e.name}</span>`;
          row.title = fullPath;
          row.addEventListener("mouseenter", () => row.style.background = "var(--surface-3,#2a2a2a)");
          row.addEventListener("mouseleave", () => row.style.background = "");
          row.addEventListener("click",    () => _clvBrowseDir(fullPath));
          row.addEventListener("dblclick", () => {
            clvDestInput.value = fullPath;
            clvDestBrowser.classList.add("hidden");
          });
          clvDestBrowser.appendChild(row);
        });
      } catch (err) {
        clvDestBrowser.textContent = "Failed to load.";
      }
    }

    clvDestBrowse?.addEventListener("click", () => {
      const isHidden = clvDestBrowser.classList.contains("hidden");
      clvDestBrowser.classList.toggle("hidden");
      if (isHidden) {
        const startPath = clvDestInput.value.trim() || _avProjectPath || "/";
        _clvBrowseDir(startPath);
      }
    });
    clvDestClear?.addEventListener("click", () => { clvDestInput.value = ""; });

    clvBtn?.addEventListener("click", async () => {
      const target = (document.getElementById("av-target-path")?.value || "").trim();
      if (!target) {
        clvStatus.textContent = "Select a video file first.";
        clvStatus.className   = "fe-extract-status err";
        return;
      }
      clvStatus.textContent = "Dispatching…";
      clvStatus.className   = "fe-extract-status";
      clvBtn.disabled = true;

      const pcutoffVal = document.getElementById("clv-pcutoff").value;
      const destVal    = (clvDestInput?.value || "").trim();
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
  })();

  // ── View Analyzed Videos / Frames ─────────────────────────────
  (() => {
    const vaCard         = document.getElementById("view-analyzed-card");
    const vaOpenBtn      = document.getElementById("btn-open-view-analyzed");
    const vaCloseBtn     = document.getElementById("btn-close-view-analyzed");
    const vaRefreshBtn   = document.getElementById("va-refresh-btn");
    const vaContentList  = document.getElementById("va-content-list");
    const vaPlayerSec    = document.getElementById("va-player-section");
    const vaSelectedName = document.getElementById("va-selected-name");
    const vaBackBtn      = document.getElementById("va-btn-back");
    const vaVideoWrap    = document.getElementById("va-video-wrap");
    const vaFrameImg     = document.getElementById("va-frame-img");
    const vaFrameSpinner = document.getElementById("va-frame-spinner");
    const vaZoomInput    = document.getElementById("va-zoom");
    const vaZoomVal      = document.getElementById("va-zoom-val");
    const vaBtnPlay      = document.getElementById("va-btn-play");
    const vaPlayIcon     = document.getElementById("va-play-icon");
    const vaPauseIcon    = document.getElementById("va-pause-icon");
    const vaBtnPrev      = document.getElementById("va-btn-prev");
    const vaBtnNext      = document.getElementById("va-btn-next");
    const vaFrameCounter = document.getElementById("va-frame-counter");
    const vaTimeDisplay  = document.getElementById("va-time-display");
    const vaSeek         = document.getElementById("va-seek");
    const vaStatus       = document.getElementById("va-status");
    // Browse-tab elements
    const vaTabProject      = document.getElementById("va-tab-project");
    const vaTabBrowse       = document.getElementById("va-tab-browse");
    const vaTabProjectPanel = document.getElementById("va-tab-project-panel");
    const vaTabBrowsePanel  = document.getElementById("va-tab-browse-panel");
    const vaBrowseBreadcrumb = document.getElementById("va-browse-breadcrumb");
    const vaBrowseUp         = document.getElementById("va-browse-up");
    const vaBrowseList       = document.getElementById("va-browse-list");

    // State
    let _vaMode         = null;   // "video" | "frames" | "browse-video"
    let _vaCurrentFrame = 0;
    let _vaFrameCount   = 0;
    let _vaFps          = 30;
    let _vaFrameBusy    = false;
    let _vaPlayTimer    = null;
    let _vaSeekDragging = false;
    let _vaZoom         = 100;
    // video mode (DLC project labeled videos)
    let _vaVideoName  = null;
    // frames mode
    let _vaFrameStem  = null;
    let _vaFrameFiles = [];   // sorted list of labeled frame filenames
    // browse-video mode (arbitrary path via /annotate endpoints)
    let _vaBrowseVideoPath = null;
    // browse tab state
    let _vaBrowsePath = null;

    // ── Kinematic overlay state ────────────────────────────────────────────
    let _vaOverlayEnabled   = false;
    let _vaH5Path           = null;       // absolute path to loaded .h5 file
    let _vaAllBodyParts     = [];         // all body parts from h5-info
    let _vaSelectedParts    = new Set();  // empty = show all
    let _vaThreshold        = 0.60;
    let _vaMarkerSize       = 6;
    // absolute path to the currently loaded original video (for annotated frames)
    let _vaCurrentVideoPath = null;

    // ── Viewer sizing (same break-out-of-card approach as frame labeler) ──
    function _vaFitViewer() {
      if (!vaFrameImg.naturalWidth) return;
      const cs    = getComputedStyle(vaCard);
      const padL  = parseFloat(cs.paddingLeft)  || 0;
      const padR  = parseFloat(cs.paddingRight) || 0;
      const baseW = vaCard.clientWidth - padL - padR;
      const maxW  = Math.max(baseW, window.innerWidth - 32);
      const targetW = Math.min(Math.round(baseW * (_vaZoom / 100)), Math.floor(maxW));
      const extra   = targetW - baseW;
      vaVideoWrap.style.width      = targetW + "px";
      vaVideoWrap.style.marginLeft = extra > 0 ? `-${extra / 2}px` : "";
    }

    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => { if (vaFrameImg.naturalWidth) _vaFitViewer(); }).observe(vaCard);
    }

    vaZoomInput.addEventListener("input", () => {
      _vaZoom = parseInt(vaZoomInput.value, 10);
      vaZoomVal.textContent = _vaZoom + " %";
      _vaFitViewer();
      _vaSyncCanvas();
    });

    function _vaReset() {
      if (_vaPlayTimer) { clearInterval(_vaPlayTimer); _vaPlayTimer = null; }
      _vaMode            = null;
      _vaCurrentFrame    = 0;
      _vaFrameCount      = 0;
      _vaFps             = 30;
      _vaFrameBusy       = false;
      _vaVideoName       = null;
      _vaFrameStem       = null;
      _vaFrameFiles      = [];
      _vaBrowseVideoPath = null;
      _vaCurrentVideoPath = null;
      _vaCurrentPoses = [];
      _vaHoverBp      = null;
      if (vaOverlayCtx) vaOverlayCtx.clearRect(0, 0, vaOverlayCanvas.width, vaOverlayCanvas.height);
      vaPlayIcon.classList.remove("hidden"); vaPauseIcon.classList.add("hidden");
      vaFrameImg.onload  = null;
      vaFrameImg.onerror = null;
      if (vaFrameImg.src && vaFrameImg.src.startsWith("blob:")) URL.revokeObjectURL(vaFrameImg.src);
      vaFrameImg.removeAttribute("src");
      vaVideoWrap.style.width      = "";
      vaVideoWrap.style.marginLeft = "";
      vaFrameSpinner.classList.add("hidden");
      vaPlayerSec.classList.add("hidden");
      vaStatus.textContent = "";
      vaStatus.className   = "fe-extract-status";
    }

    function _vaFrameUrl(n) {
      // ── Kinematic overlay takes priority when enabled and h5 is loaded ──
      if (_vaOverlayEnabled && _vaH5Path && _vaCurrentVideoPath) {
        const parts = _vaSelectedParts.size > 0
          ? [..._vaSelectedParts].join(",")
          : "";
        const p = new URLSearchParams({
          video:       _vaCurrentVideoPath,
          h5:          _vaH5Path,
          threshold:   _vaThreshold.toFixed(2),
          marker_size: _vaMarkerSize,
          scale:       1.0,
        });
        if (parts) p.set("parts", parts);
        return `/dlc/viewer/frame-annotated/${n}?${p}`;
      }

      if (_vaMode === "browse-video") {
        return `/annotate/video-frame/${n}?path=${encodeURIComponent(_vaBrowseVideoPath)}`;
      }
      if (_vaMode === "video") {
        return `/dlc/project/video-frame/${encodeURIComponent(_vaVideoName)}/${n}`;
      }
      // frames mode: index into _vaFrameFiles
      return `/dlc/project/frame-image/${encodeURIComponent(_vaFrameStem)}/${encodeURIComponent(_vaFrameFiles[n])}`;
    }

    function _vaUpdateDisplay() {
      vaFrameCounter.textContent = `Frame ${_vaCurrentFrame} / ${_vaFrameCount}`;
      if (_vaMode === "video" || _vaMode === "browse-video") {
        vaTimeDisplay.textContent = `${(_vaCurrentFrame / _vaFps).toFixed(3)} s`;
      } else {
        vaTimeDisplay.textContent = _vaFrameFiles[_vaCurrentFrame] || "";
      }
      if (!_vaSeekDragging)
        vaSeek.value = Math.round((_vaCurrentFrame / Math.max(_vaFrameCount - 1, 1)) * 1000);
    }

    async function _vaLoadFrame(n) {
      if (_vaFrameBusy) return;
      _vaFrameBusy = true;
      n = Math.max(0, Math.min(n, Math.max(_vaFrameCount - 1, 0)));
      _vaCurrentFrame = n;
      vaFrameSpinner.classList.remove("hidden");
      try {
        const resp = await fetch(_vaFrameUrl(n));
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.error || `HTTP ${resp.status}`);
        }
        const blob    = await resp.blob();
        const blobUrl = URL.createObjectURL(blob);
        await new Promise((resolve, reject) => {
          vaFrameImg.onload  = resolve;
          vaFrameImg.onerror = reject;
          const prev = vaFrameImg.src;
          vaFrameImg.src = blobUrl;
          if (prev && prev.startsWith("blob:")) URL.revokeObjectURL(prev);
        });
        _vaFitViewer();
        _vaUpdateDisplay();
        // Sync canvas size after image loads, then fetch poses for hover labels
        _vaSyncCanvas();
        if (_vaOverlayEnabled && _vaH5Path) _vaFetchPoses(n);
        else { _vaCurrentPoses = []; _vaHoverBp = null; if (vaOverlayCtx) vaOverlayCtx.clearRect(0, 0, vaOverlayCanvas.width, vaOverlayCanvas.height); }
      } catch (err) {
        vaStatus.textContent = `Failed to load frame: ${err.message}`;
        vaStatus.className   = "fe-extract-status err";
      } finally {
        _vaFrameBusy = false;
        vaFrameSpinner.classList.add("hidden");
      }
    }

    async function _vaOpenVideo(name) {
      _vaReset();
      _vaMode      = "video";
      _vaVideoName = name;
      vaSelectedName.textContent = name;
      try {
        const res  = await fetch(`/dlc/project/video-info/${encodeURIComponent(name)}`);
        const info = await res.json();
        _vaFps        = info.fps || 30;
        _vaFrameCount = info.frame_count || 0;
      } catch (_) { _vaFps = 30; _vaFrameCount = 0; }
      vaPlayerSec.classList.remove("hidden");
      _vaLoadFrame(0);
    }

    function _vaOpenFrameFolder(stem, frames) {
      _vaReset();
      _vaMode       = "frames";
      _vaFrameStem  = stem;
      _vaFrameFiles = frames;
      _vaFrameCount = frames.length;
      _vaFps        = 5;   // slow playback for sparse labeled frames
      vaSelectedName.textContent = `${stem}/ (${frames.length} labeled frames)`;
      vaPlayerSec.classList.remove("hidden");
      _vaLoadFrame(0);
    }

    async function _vaOpenBrowseVideo(absPath, name) {
      _vaReset();
      _vaMode             = "browse-video";
      _vaBrowseVideoPath  = absPath;
      _vaCurrentVideoPath = absPath;
      vaSelectedName.textContent = name;
      try {
        const res  = await fetch(`/annotate/video-info?path=${encodeURIComponent(absPath)}`);
        const info = await res.json();
        _vaFps        = info.fps || 30;
        _vaFrameCount = info.frame_count || 0;
      } catch (_) { _vaFps = 30; _vaFrameCount = 0; }
      vaPlayerSec.classList.remove("hidden");
      _vaLoadFrame(0);
      // Auto-detect companion h5 in the same directory
      _vaAutoDetectH5(absPath);
    }

    // ── Browse-tab folder navigator ────────────────────────────
    const _VA_VIDEO_EXTS = new Set([".mp4", ".avi", ".mov", ".mkv", ".mpg", ".mpeg"]);

    async function _vaRefreshBrowse(path) {
      _vaBrowsePath = path;
      vaBrowseBreadcrumb.textContent = path;
      vaBrowseList.innerHTML = '<p class="explorer-empty">Loading…</p>';
      try {
        const res  = await fetch(`/fs/ls?path=${encodeURIComponent(path)}`);
        const data = await res.json();
        if (data.error) { vaBrowseList.innerHTML = `<p class="explorer-empty">${data.error}</p>`; return; }

        const entries = data.entries || [];
        const dirs    = entries.filter(e => e.type === "dir");
        const videos  = entries.filter(e => e.type === "file" && _VA_VIDEO_EXTS.has(e.name.slice(e.name.lastIndexOf(".")).toLowerCase()));

        if (!dirs.length && !videos.length) {
          vaBrowseList.innerHTML = '<p class="explorer-empty">No folders or videos found here.</p>';
          return;
        }
        vaBrowseList.innerHTML = "";

        dirs.forEach(d => {
          const row = document.createElement("div");
          row.className = "fe-video-item";
          row.style.cursor = "pointer";
          row.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${d.name}/</span>`;
          row.addEventListener("click", () => _vaRefreshBrowse(path + "/" + d.name));
          vaBrowseList.appendChild(row);
        });

        videos.forEach(v => {
          const fullPath = path + "/" + v.name;
          const row = document.createElement("div");
          row.className = "fe-video-item";
          row.style.cursor = "pointer";
          row.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><rect x="2" y="2" width="20" height="20" rx="3"/><polygon points="10 8 16 12 10 16 10 8" fill="currentColor" stroke="none"/></svg><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0">${v.name}</span>`;
          row.addEventListener("click", () => _vaOpenBrowseVideo(fullPath, v.name));
          vaBrowseList.appendChild(row);
        });
      } catch (err) {
        vaBrowseList.innerHTML = `<p class="explorer-empty">Error: ${err.message}</p>`;
      }
    }

    // ── Tab switching ──────────────────────────────────────────
    vaTabProject?.addEventListener("click", () => {
      vaTabProject.classList.add("active");
      vaTabBrowse.classList.remove("active");
      vaTabProjectPanel.classList.remove("hidden");
      vaTabBrowsePanel.classList.add("hidden");
    });
    vaTabBrowse?.addEventListener("click", () => {
      vaTabBrowse.classList.add("active");
      vaTabProject.classList.remove("active");
      vaTabBrowsePanel.classList.remove("hidden");
      vaTabProjectPanel.classList.add("hidden");
      if (!_vaBrowsePath) {
        // Start at user-data dir or /
        const startPath = _userDataDir || _dataDir || "/";
        _vaRefreshBrowse(startPath);
      }
    });

    vaBrowseUp?.addEventListener("click", () => {
      if (!_vaBrowsePath) return;
      const parent = _vaBrowsePath.split("/").slice(0, -1).join("/") || "/";
      if (parent !== _vaBrowsePath) _vaRefreshBrowse(parent);
    });

    // ── Kinematic overlay canvas ──────────────────────────────
    const vaOverlayCanvas = document.getElementById("va-overlay-canvas");
    const vaOverlayCtx    = vaOverlayCanvas ? vaOverlayCanvas.getContext("2d") : null;

    // Current frame poses (fetched alongside each annotated frame)
    let _vaCurrentPoses = [];  // [{bp, x, y, lh, color_idx}]
    let _vaNBodyparts   = 1;   // total bodyparts count (for palette)
    let _vaHoverBp      = null;

    // Replicate the server's HSV rainbow palette in JS for label colours
    function _vaHsvToRgb(h, s, v) {
      const i = Math.floor(h * 6);
      const f = h * 6 - i;
      const p = v * (1 - s), q = v * (1 - f * s), t = v * (1 - (1 - f) * s);
      let r, g, b;
      switch (i % 6) {
        case 0: r=v; g=t; b=p; break; case 1: r=q; g=v; b=p; break;
        case 2: r=p; g=v; b=t; break; case 3: r=p; g=q; b=v; break;
        case 4: r=t; g=p; b=v; break; default: r=v; g=p; b=q;
      }
      return `rgb(${Math.round(r*255)},${Math.round(g*255)},${Math.round(b*255)})`;
    }
    function _vaPaletteColor(idx, total) {
      return _vaHsvToRgb(idx / Math.max(total, 1), 0.9, 0.95);
    }

    function _vaSyncCanvas() {
      if (!vaOverlayCanvas) return;
      // Match canvas buffer size to the *displayed* image size (not natural)
      const w = vaFrameImg.offsetWidth  || vaFrameImg.clientWidth  || 1;
      const h = vaFrameImg.offsetHeight || vaFrameImg.clientHeight || 1;
      if (vaOverlayCanvas.width !== w || vaOverlayCanvas.height !== h) {
        vaOverlayCanvas.width  = w;
        vaOverlayCanvas.height = h;
      }
    }

    function _vaDrawHoverLabel() {
      if (!vaOverlayCtx) return;
      _vaSyncCanvas();
      vaOverlayCtx.clearRect(0, 0, vaOverlayCanvas.width, vaOverlayCanvas.height);
      if (!_vaHoverBp || !_vaCurrentPoses.length) return;
      const pose = _vaCurrentPoses.find(p => p.bp === _vaHoverBp);
      if (!pose) return;

      // Map video-native coords → canvas display coords
      const natW = vaFrameImg.naturalWidth  || 1;
      const natH = vaFrameImg.naturalHeight || 1;
      const sx   = vaOverlayCanvas.width  / natW;
      const sy   = vaOverlayCanvas.height / natH;
      const cx   = pose.x * sx;
      const cy   = pose.y * sy;

      const color = _vaPaletteColor(pose.color_idx, _vaNBodyparts);
      const r     = _vaMarkerSize + 2;          // slightly larger hit ring
      const bp    = pose.bp;

      vaOverlayCtx.font      = "bold 11px 'JetBrains Mono', monospace";
      const tw = vaOverlayCtx.measureText(bp).width;
      // Flip label to the left if it would clip the right edge
      const flip = (cx + r + tw + 12) > vaOverlayCanvas.width;
      const tx   = flip ? cx - r - tw - 10 : cx + r + 4;
      const ty   = cy + 4;
      vaOverlayCtx.fillStyle = "rgba(12,13,16,.75)";
      vaOverlayCtx.fillRect(tx - 2, ty - 11, tw + 6, 14);
      vaOverlayCtx.fillStyle = color;
      vaOverlayCtx.fillText(bp, tx + 1, ty);
    }

    function _vaHitTest(cx, cy) {
      if (!_vaCurrentPoses.length) return null;
      const natW  = vaFrameImg.naturalWidth  || 1;
      const natH  = vaFrameImg.naturalHeight || 1;
      const sx    = vaOverlayCanvas.width  / natW;
      const sy    = vaOverlayCanvas.height / natH;
      const hitR  = (_vaMarkerSize + 6) * Math.max(sx, sy);
      for (const pose of _vaCurrentPoses) {
        const dx = pose.x * sx - cx;
        const dy = pose.y * sy - cy;
        if (Math.sqrt(dx * dx + dy * dy) <= hitR) return pose.bp;
      }
      return null;
    }

    if (vaOverlayCanvas) {
      // Enable pointer events on the canvas for hover detection only
      vaOverlayCanvas.style.pointerEvents = "auto";
      vaOverlayCanvas.style.cursor        = "default";

      vaOverlayCanvas.addEventListener("mousemove", e => {
        if (!_vaOverlayEnabled || !_vaCurrentPoses.length) return;
        const rect = vaOverlayCanvas.getBoundingClientRect();
        const hit  = _vaHitTest(e.clientX - rect.left, e.clientY - rect.top);
        if (hit !== _vaHoverBp) {
          _vaHoverBp = hit;
          _vaDrawHoverLabel();
        }
        vaOverlayCanvas.style.cursor = hit ? "crosshair" : "default";
      });

      vaOverlayCanvas.addEventListener("mouseleave", () => {
        if (_vaHoverBp) { _vaHoverBp = null; _vaDrawHoverLabel(); }
        vaOverlayCanvas.style.cursor = "default";
      });
    }

    // Fetch visible poses for the current frame (called after overlay frame loads)
    async function _vaFetchPoses(frameNumber) {
      if (!_vaH5Path) return;
      const parts    = _vaSelectedParts.size > 0 ? [..._vaSelectedParts].join(",") : "";
      const p        = new URLSearchParams({ h5: _vaH5Path, threshold: _vaThreshold.toFixed(2) });
      if (parts) p.set("parts", parts);
      try {
        const res  = await fetch(`/dlc/viewer/frame-poses/${frameNumber}?${p}`);
        const data = await res.json();
        _vaCurrentPoses = data.poses || [];
        _vaNBodyparts   = data.n_bodyparts || 1;
      } catch (_) {
        _vaCurrentPoses = [];
      }
      _vaHoverBp = null;
      _vaDrawHoverLabel();
    }

    // ── Kinematic overlay controls ────────────────────────────
    const vaOverlayToggle    = document.getElementById("va-overlay-toggle");
    const vaOverlayControls  = document.getElementById("va-overlay-controls");
    const vaOverlayStatus    = document.getElementById("va-overlay-status");
    const vaOverlayH5Path    = document.getElementById("va-overlay-h5-path");
    const vaOverlayH5Auto    = document.getElementById("va-overlay-h5-auto");
    const vaOverlayH5Browse  = document.getElementById("va-overlay-h5-browse");
    const vaOverlayH5Clear   = document.getElementById("va-overlay-h5-clear");
    const vaOverlayH5Browser = document.getElementById("va-overlay-h5-browser");
    const vaOverlayThreshold = document.getElementById("va-overlay-threshold");
    const vaOverlayThreshVal = document.getElementById("va-overlay-threshold-val");
    const vaOverlayMarkerSz  = document.getElementById("va-overlay-marker-size");
    const vaOverlayMarkerVal = document.getElementById("va-overlay-marker-size-val");
    const vaOverlayPartsBox  = document.getElementById("va-overlay-bodyparts");
    const vaOverlayPartsAll  = document.getElementById("va-overlay-parts-all");
    const vaOverlayPartsNone = document.getElementById("va-overlay-parts-none");

    function _vaOverlayStatus(msg, isErr = false) {
      vaOverlayStatus.textContent = msg;
      vaOverlayStatus.className   = "fe-extract-status" + (isErr ? " err" : "");
    }

    async function _vaLoadH5Info(h5Path) {
      vaOverlayPartsBox.innerHTML = '<span style="color:var(--text-dim);font-size:.73rem">Loading…</span>';
      _vaSelectedParts.clear();
      try {
        const res  = await fetch(`/dlc/viewer/h5-info?h5=${encodeURIComponent(h5Path)}`);
        const data = await res.json();
        if (data.error) { _vaOverlayStatus(data.error, true); return; }
        _vaAllBodyParts = data.bodyparts || [];
        _vaRebuildPartsChecklist();
        _vaOverlayStatus(`${data.frame_count.toLocaleString()} frames · ${_vaAllBodyParts.length} body parts`);
      } catch (e) {
        _vaOverlayStatus(`Failed to load h5 info: ${e.message}`, true);
      }
    }

    function _vaRebuildPartsChecklist() {
      vaOverlayPartsBox.innerHTML = "";
      if (!_vaAllBodyParts.length) {
        vaOverlayPartsBox.innerHTML = '<span style="color:var(--text-dim);font-size:.73rem">No body parts loaded.</span>';
        return;
      }
      _vaAllBodyParts.forEach(bp => {
        const lbl  = document.createElement("label");
        lbl.style.cssText = "display:flex;align-items:center;gap:.3rem;cursor:pointer;white-space:nowrap";
        const chk  = document.createElement("input");
        chk.type   = "checkbox";
        chk.value  = bp;
        chk.style.accentColor = "var(--accent)";
        // Empty _vaSelectedParts means ALL selected
        chk.checked = _vaSelectedParts.size === 0 || _vaSelectedParts.has(bp);
        chk.addEventListener("change", () => {
          if (chk.checked) _vaSelectedParts.delete(bp);  // empty = all
          else             _vaSelectedParts.add(bp);      // explicit exclude
          // If all checked manually, reset to empty (= all)
          if ([...vaOverlayPartsBox.querySelectorAll("input")].every(c => c.checked))
            _vaSelectedParts.clear();
          if (_vaOverlayEnabled && _vaH5Path) _vaLoadFrame(_vaCurrentFrame);
        });
        lbl.appendChild(chk);
        lbl.appendChild(document.createTextNode(bp));
        vaOverlayPartsBox.appendChild(lbl);
      });
    }

    async function _vaAutoDetectH5(videoPath) {
      const dir  = videoPath.substring(0, videoPath.lastIndexOf("/"));
      const name = videoPath.substring(videoPath.lastIndexOf("/") + 1);
      const stem = name.replace(/\.[^.]+$/, "");
      _vaOverlayStatus("Scanning for .h5…");
      try {
        const res  = await fetch(`/dlc/viewer/h5-find?dir=${encodeURIComponent(dir)}&stem=${encodeURIComponent(stem)}`);
        const data = await res.json();
        if (data.error) { _vaOverlayStatus(data.error.includes("No .h5") ? "No .h5 found — browse to select one." : data.error); return; }
        _vaH5Path = data.h5_path;
        vaOverlayH5Path.value = _vaH5Path;
        _vaOverlayStatus("h5 auto-detected");
        await _vaLoadH5Info(_vaH5Path);
      } catch (e) {
        _vaOverlayStatus("Auto-detect failed: " + e.message);
      }
    }

    vaOverlayToggle?.addEventListener("change", () => {
      _vaOverlayEnabled = vaOverlayToggle.checked;
      vaOverlayControls.classList.toggle("hidden", !_vaOverlayEnabled);
      if (_vaOverlayEnabled && !_vaH5Path && _vaCurrentVideoPath)
        _vaAutoDetectH5(_vaCurrentVideoPath);
      if (_vaCurrentFrame !== null) _vaLoadFrame(_vaCurrentFrame);
    });

    vaOverlayH5Auto?.addEventListener("click", () => {
      if (_vaCurrentVideoPath) _vaAutoDetectH5(_vaCurrentVideoPath);
    });

    vaOverlayH5Clear?.addEventListener("click", () => {
      _vaH5Path = null;
      vaOverlayH5Path.value = "";
      _vaAllBodyParts = [];
      _vaSelectedParts.clear();
      vaOverlayPartsBox.innerHTML = '<span style="color:var(--text-dim);font-size:.73rem">Load an .h5 file to see body parts.</span>';
      _vaOverlayStatus("");
      if (_vaOverlayEnabled) _vaLoadFrame(_vaCurrentFrame);
    });

    // Threshold slider
    vaOverlayThreshold?.addEventListener("input", () => {
      _vaThreshold = parseFloat(vaOverlayThreshold.value);
      vaOverlayThreshVal.textContent = _vaThreshold.toFixed(2);
    });
    vaOverlayThreshold?.addEventListener("change", () => {
      if (_vaOverlayEnabled && _vaH5Path) _vaLoadFrame(_vaCurrentFrame);
    });

    // Marker size slider
    vaOverlayMarkerSz?.addEventListener("input", () => {
      _vaMarkerSize = parseInt(vaOverlayMarkerSz.value, 10);
      vaOverlayMarkerVal.textContent = _vaMarkerSize;
    });
    vaOverlayMarkerSz?.addEventListener("change", () => {
      if (_vaOverlayEnabled && _vaH5Path) _vaLoadFrame(_vaCurrentFrame);
    });

    vaOverlayPartsAll?.addEventListener("click", () => {
      _vaSelectedParts.clear();
      vaOverlayPartsBox.querySelectorAll("input").forEach(c => { c.checked = true; });
      if (_vaOverlayEnabled && _vaH5Path) _vaLoadFrame(_vaCurrentFrame);
    });
    vaOverlayPartsNone?.addEventListener("click", () => {
      _vaAllBodyParts.forEach(bp => _vaSelectedParts.add(bp));
      vaOverlayPartsBox.querySelectorAll("input").forEach(c => { c.checked = false; });
      // "none selected" still shows all — reset to prevent empty render
      _vaSelectedParts.clear();
      vaOverlayPartsBox.querySelectorAll("input").forEach(c => { c.checked = false; });
      // keep _vaSelectedParts empty but mark first part as explicit include for "none" visual
      // Actually: show nothing when all unchecked — use a sentinel
      _vaAllBodyParts.forEach(bp => _vaSelectedParts.add("__none__"));
    });

    // h5 file browser (shows .h5 files and dirs)
    let _vaH5BrowsePath = null;

    async function _vaH5BrowseDir(path) {
      _vaH5BrowsePath = path;
      vaOverlayH5Browser.innerHTML = '<p class="explorer-empty">Loading…</p>';
      try {
        const res  = await fetch(`/fs/ls?path=${encodeURIComponent(path)}`);
        const data = await res.json();
        if (data.error) { vaOverlayH5Browser.innerHTML = `<p class="explorer-empty">${data.error}</p>`; return; }
        vaOverlayH5Browser.innerHTML = "";
        const entries = data.entries || [];
        // Up button
        if (data.parent) {
          const upRow = document.createElement("div");
          upRow.className = "fe-video-item";
          upRow.style.cursor = "pointer";
          upRow.textContent = "↑ ..";
          upRow.addEventListener("click", () => _vaH5BrowseDir(data.parent));
          vaOverlayH5Browser.appendChild(upRow);
        }
        entries.forEach(e => {
          const isH5  = e.type === "file" && e.name.toLowerCase().endsWith(".h5");
          const isDir = e.type === "dir";
          if (!isH5 && !isDir) return;
          const row = document.createElement("div");
          row.className   = "fe-video-item";
          row.style.cursor = "pointer";
          row.textContent  = isDir ? `📁 ${e.name}/` : `📊 ${e.name}`;
          row.addEventListener("click", async () => {
            if (isDir) {
              _vaH5BrowseDir(path + "/" + e.name);
            } else {
              const full = path + "/" + e.name;
              _vaH5Path = full;
              vaOverlayH5Path.value = full;
              vaOverlayH5Browser.classList.add("hidden");
              _vaOverlayStatus("h5 selected");
              await _vaLoadH5Info(full);
              if (_vaOverlayEnabled) _vaLoadFrame(_vaCurrentFrame);
            }
          });
          vaOverlayH5Browser.appendChild(row);
        });
        if (!vaOverlayH5Browser.children.length)
          vaOverlayH5Browser.innerHTML = '<p class="explorer-empty">No .h5 files found here.</p>';
      } catch (e) {
        vaOverlayH5Browser.innerHTML = `<p class="explorer-empty">Error: ${e.message}</p>`;
      }
    }

    vaOverlayH5Browse?.addEventListener("click", () => {
      const isHidden = vaOverlayH5Browser.classList.toggle("hidden");
      if (!isHidden) {
        const startDir = _vaCurrentVideoPath
          ? _vaCurrentVideoPath.substring(0, _vaCurrentVideoPath.lastIndexOf("/"))
          : (_userDataDir || _dataDir || "/");
        _vaH5BrowseDir(startDir);
      }
    });

    // ── Load content list ─────────────────────────────────────
    async function _vaLoadContent() {
      vaContentList.innerHTML = '<p class="explorer-empty">Loading…</p>';
      try {
        const res  = await fetch("/dlc/project/labeled-content");
        const data = await res.json();
        if (data.error) {
          vaContentList.innerHTML = `<p class="explorer-empty">${data.error}</p>`;
          return;
        }
        const hasVideos  = data.videos  && data.videos.length  > 0;
        const hasFolders = data.frame_folders && data.frame_folders.length > 0;
        if (!hasVideos && !hasFolders) {
          vaContentList.innerHTML = '<p class="explorer-empty">No labeled videos or frame folders found. Run "Analyze Video / Frames" with "Create labeled video / frame" enabled.</p>';
          return;
        }
        vaContentList.innerHTML = "";

        function _makeItem(svgHtml, name, subtitle, onClick) {
          const item = document.createElement("div");
          item.className = "fe-video-item";
          item.innerHTML = `${svgHtml}<div style="display:flex;flex-direction:column;min-width:0;flex:1"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${name}</span>${subtitle ? `<span style="font-size:.7rem;color:var(--text-dim)">${subtitle}</span>` : ""}</div>`;
          item.addEventListener("click", onClick);
          return item;
        }

        if (hasVideos) {
          const hdr = document.createElement("div");
          hdr.style.cssText = "font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);padding:.25rem .3rem .1rem";
          hdr.textContent   = "Labeled Videos";
          vaContentList.appendChild(hdr);
          data.videos.forEach(v => {
            const sub  = v.size ? Math.round(v.size / 1024 / 1024) + " MB" : "";
            const svg  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><rect x="2" y="2" width="20" height="20" rx="3"/><polygon points="10 8 16 12 10 16 10 8" fill="currentColor" stroke="none"/></svg>`;
            vaContentList.appendChild(_makeItem(svg, v.name, sub, () => _vaOpenVideo(v.name)));
          });
        }

        if (hasFolders) {
          const hdr = document.createElement("div");
          hdr.style.cssText = "font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);padding:.35rem .3rem .1rem";
          hdr.textContent   = "Labeled Frame Folders";
          vaContentList.appendChild(hdr);
          data.frame_folders.forEach(f => {
            const sub = f.frame_count + " labeled frames";
            const svg = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`;
            vaContentList.appendChild(_makeItem(svg, f.stem + "/", sub, () => _vaOpenFrameFolder(f.stem, f.frames)));
          });
        }
      } catch (err) {
        vaContentList.innerHTML = `<p class="explorer-empty">Error: ${err.message}</p>`;
      }
    }

    // ── Player controls ───────────────────────────────────────
    vaBtnPlay.addEventListener("click", () => {
      if (_vaPlayTimer) {
        clearInterval(_vaPlayTimer); _vaPlayTimer = null;
        vaPlayIcon.classList.remove("hidden"); vaPauseIcon.classList.add("hidden");
      } else {
        vaPlayIcon.classList.add("hidden"); vaPauseIcon.classList.remove("hidden");
        _vaPlayTimer = setInterval(async () => {
          if (_vaCurrentFrame >= _vaFrameCount - 1) {
            clearInterval(_vaPlayTimer); _vaPlayTimer = null;
            vaPlayIcon.classList.remove("hidden"); vaPauseIcon.classList.add("hidden");
            return;
          }
          await _vaLoadFrame(_vaCurrentFrame + 1);
        }, 1000 / _vaFps);
      }
    });

    vaBtnPrev.addEventListener("click", () => _vaLoadFrame(_vaCurrentFrame - 1));
    vaBtnNext.addEventListener("click", () => _vaLoadFrame(_vaCurrentFrame + 1));

    vaSeek.addEventListener("mousedown",  () => { _vaSeekDragging = true; });
    vaSeek.addEventListener("touchstart", () => { _vaSeekDragging = true; });
    vaSeek.addEventListener("input", () => {
      _vaCurrentFrame = Math.round((vaSeek.value / 1000) * Math.max(_vaFrameCount - 1, 0));
      _vaUpdateDisplay();
    });
    vaSeek.addEventListener("change", () => { _vaSeekDragging = false; _vaLoadFrame(_vaCurrentFrame); });

    vaBackBtn.addEventListener("click", _vaReset);
    vaRefreshBtn.addEventListener("click", _vaLoadContent);

    // ── Open / close ──────────────────────────────────────────
    vaOpenBtn?.addEventListener("click", () => {
      vaCard.classList.remove("hidden");
      vaCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      _vaLoadContent();
    });

    vaCloseBtn?.addEventListener("click", () => {
      vaCard.classList.add("hidden");
      _vaReset();
    });

    // Arrow key navigation when player is active
    vaCard.addEventListener("keydown", (e) => {
      if (vaPlayerSec.classList.contains("hidden")) return;
      if (e.key === "ArrowLeft")  { e.preventDefault(); _vaLoadFrame(_vaCurrentFrame - 1); }
      if (e.key === "ArrowRight") { e.preventDefault(); _vaLoadFrame(_vaCurrentFrame + 1); }
    });
  })();

  // ── Video Annotator ───────────────────────────────────────────
  (() => {
    const anvCard           = document.getElementById("annotate-video-card");
    const anvOpenBtn        = document.getElementById("btn-open-annotate-video");
    const anvCloseBtn       = document.getElementById("btn-close-annotate-video");
    const anvVideoPath      = document.getElementById("anv-video-path");
    const anvBrowseBtn      = document.getElementById("anv-browse-btn");
    const anvLoadBtn        = document.getElementById("anv-load-btn");
    const anvBrowser        = document.getElementById("anv-browser");
    const anvLoadStatus     = document.getElementById("anv-load-status");
    const anvPlayerSec      = document.getElementById("anv-player-section");
    const anvVideoWrap      = document.getElementById("anv-video-wrap");
    const anvFrameImg       = document.getElementById("anv-frame-img");
    const anvFrameSpinner   = document.getElementById("anv-frame-spinner");
    const anvBtnPlay        = document.getElementById("anv-btn-play");
    const anvPlayIcon       = document.getElementById("anv-play-icon");
    const anvPauseIcon      = document.getElementById("anv-pause-icon");
    const anvBtnPrev        = document.getElementById("anv-btn-prev");
    const anvBtnNext        = document.getElementById("anv-btn-next");
    const anvFrameCounter   = document.getElementById("anv-frame-counter");
    const anvFrameJump      = document.getElementById("anv-frame-jump");
    const anvTimeDisplay    = document.getElementById("anv-time-display");
    const anvSeek           = document.getElementById("anv-seek");
    const anvCsvBars        = document.getElementById("anv-csv-bars");
    const anvStatusBarWrap  = document.getElementById("anv-status-bar-wrap");
    const anvNoteBarWrap    = document.getElementById("anv-note-bar-wrap");
    const anvStatusBar      = document.getElementById("anv-status-bar");
    const anvNoteBar        = document.getElementById("anv-note-bar");
    const anvCsvSection     = document.getElementById("anv-csv-section");
    const anvCsvNone        = document.getElementById("anv-csv-none");
    const anvCsvLoaded      = document.getElementById("anv-csv-loaded");
    const anvCsvPathDisplay = document.getElementById("anv-csv-path-display");
    const anvCreateCsvBtn   = document.getElementById("anv-create-csv-btn");
    const anvCsvCreateStatus= document.getElementById("anv-csv-create-status");
    const anvAnnotationPanel= document.getElementById("anv-annotation-panel");
    const anvAnnotateFrameNum= document.getElementById("anv-annotate-frame-num");
    const anvNoteInput      = document.getElementById("anv-note-input");
    const anvStatusInput    = document.getElementById("anv-status-input");
    const anvSaveAnnotationBtn = document.getElementById("anv-save-annotation-btn");
    const anvSaveStatus     = document.getElementById("anv-save-status");
    const anvTagChips       = document.getElementById("anv-tag-chips");
    const anvNewTagInput    = document.getElementById("anv-new-tag-input");
    const anvAddTagBtn      = document.getElementById("anv-add-tag-btn");
    const anvZoomInput      = document.getElementById("anv-zoom");
    const anvZoomVal        = document.getElementById("anv-zoom-val");
    const anvRefreshCsvBtn  = document.getElementById("anv-refresh-csv-btn");

    // ── State ───────────────────────────────────────────────────
    let _anvZoom          = 100;
    let _anvVideoPath     = null;
    let _anvFps           = 30;
    let _anvFrameCount    = 0;
    let _anvCurrentFrame  = 0;
    let _anvFrameBusy     = false;
    let _anvSeekDragging  = false;
    let _anvPlayTimer     = null;
    let _anvCsvPath       = null;
    let _anvCsvRows       = [];       // {frame_number, timestamp, frame_line_status, note}
    let _anvUserTags      = [];       // user-defined tags (note values), populated from CSV + user input
    const _anvCsvPalette = ["#6ee7b7","#60a5fa","#f472b6","#fbbf24","#a78bfa","#34d399","#fb923c","#e879f9"];

    // ── Viewer sizing (can break out of card borders like VA card) ──
    function _anvFitViewer() {
      if (!anvFrameImg.naturalWidth) return;
      const cs      = getComputedStyle(anvCard);
      const padL    = parseFloat(cs.paddingLeft)  || 0;
      const padR    = parseFloat(cs.paddingRight) || 0;
      const baseW   = anvCard.clientWidth - padL - padR;
      const maxW    = Math.max(baseW, window.innerWidth - 32);
      const targetW = Math.min(Math.round(baseW * (_anvZoom / 100)), Math.floor(maxW));
      const extra   = targetW - baseW;
      anvVideoWrap.style.width      = targetW + "px";
      anvVideoWrap.style.marginLeft = extra > 0 ? `-${extra / 2}px` : "";
    }
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => { if (anvFrameImg.naturalWidth) _anvFitViewer(); }).observe(anvCard);
    }
    anvZoomInput.addEventListener("input", () => {
      _anvZoom = parseInt(anvZoomInput.value, 10);
      anvZoomVal.textContent = _anvZoom + " %";
      _anvFitViewer();
    });

    // ── Reset ───────────────────────────────────────────────────
    function _anvReset() {
      if (_anvPlayTimer) { clearInterval(_anvPlayTimer); _anvPlayTimer = null; }
      _anvZoom = 100; anvZoomInput.value = "100"; anvZoomVal.textContent = "100 %";
      _anvVideoPath = null; _anvFps = 30; _anvFrameCount = 0;
      _anvCurrentFrame = 0; _anvFrameBusy = false; _anvSeekDragging = false;
      _anvCsvPath = null; _anvCsvRows = []; _anvUserTags = [];
      anvPlayIcon.classList.remove("hidden"); anvPauseIcon.classList.add("hidden");
      anvFrameImg.onload = null; anvFrameImg.onerror = null;
      if (anvFrameImg.src && anvFrameImg.src.startsWith("blob:")) URL.revokeObjectURL(anvFrameImg.src);
      anvFrameImg.removeAttribute("src");
      anvVideoWrap.style.width = ""; anvVideoWrap.style.marginLeft = "";
      anvFrameSpinner.classList.add("hidden");
      anvPlayerSec.classList.add("hidden");
      anvCsvBars.classList.add("hidden");
      anvStatusBarWrap.classList.add("hidden");
      anvNoteBarWrap.classList.add("hidden");
      anvAnnotationPanel.classList.add("hidden");
      anvLoadStatus.textContent = "";
      anvLoadStatus.className = "fe-extract-status";
    }

    // ── Frame URL ───────────────────────────────────────────────
    function _anvFrameUrl(n) {
      return `/annotate/video-frame/${n}?path=${encodeURIComponent(_anvVideoPath)}`;
    }

    // ── Frame counter — text node kept separate from the jump input ──
    [...anvFrameCounter.childNodes].forEach(n => { if (n.nodeType === Node.TEXT_NODE) n.remove(); });
    const _anvCounterText = document.createTextNode("");
    anvFrameCounter.insertBefore(_anvCounterText, anvFrameJump);

    function _anvUpdateDisplay() {
      _anvCounterText.nodeValue = `Frame ${_anvCurrentFrame} / ${_anvFrameCount}`;
      anvTimeDisplay.textContent = `${(_anvCurrentFrame / _anvFps).toFixed(3)} s`;
      if (!_anvSeekDragging)
        anvSeek.value = Math.round((_anvCurrentFrame / Math.max(_anvFrameCount - 1, 1)) * 1000);
      _anvSyncAnnotationPanel();
    }

    // ── Double-click frame counter to jump ───────────────────────
    anvFrameCounter.addEventListener("dblclick", () => {
      anvFrameCounter.classList.add("editing");
      anvFrameJump.classList.remove("hidden");
      anvFrameJump.max   = String(_anvFrameCount - 1);
      anvFrameJump.value = String(_anvCurrentFrame);
      anvFrameJump.select();
    });

    function _anvCommitJump() {
      const n = parseInt(anvFrameJump.value);
      anvFrameJump.classList.add("hidden");
      anvFrameCounter.classList.remove("editing");
      if (!isNaN(n)) _anvLoadFrame(n);
    }

    let _anvJumpEscaped = false;
    anvFrameJump.addEventListener("keydown", e => {
      if (e.key === "Enter")  { e.preventDefault(); _anvCommitJump(); }
      if (e.key === "Escape") {
        _anvJumpEscaped = true;
        anvFrameJump.classList.add("hidden");
        anvFrameCounter.classList.remove("editing");
        anvFrameJump.blur();
      }
    });
    anvFrameJump.addEventListener("blur", () => {
      if (_anvJumpEscaped) { _anvJumpEscaped = false; return; }
      _anvCommitJump();
    });

    // ── Load a frame ────────────────────────────────────────────
    async function _anvLoadFrame(n) {
      if (_anvFrameBusy) return;
      _anvFrameBusy = true;
      n = Math.max(0, Math.min(n, Math.max(_anvFrameCount - 1, 0)));
      _anvCurrentFrame = n;
      anvFrameSpinner.classList.remove("hidden");
      try {
        const resp = await fetch(_anvFrameUrl(n));
        if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.error || `HTTP ${resp.status}`); }
        const blob    = await resp.blob();
        const blobUrl = URL.createObjectURL(blob);
        await new Promise((resolve, reject) => {
          anvFrameImg.onload  = resolve;
          anvFrameImg.onerror = reject;
          const prev = anvFrameImg.src;
          anvFrameImg.src = blobUrl;
          if (prev && prev.startsWith("blob:")) URL.revokeObjectURL(prev);
        });
        _anvFitViewer();
        _anvUpdateDisplay();
      } catch (err) {
        anvLoadStatus.textContent = `Frame load error: ${err.message}`;
        anvLoadStatus.className   = "fe-extract-status err";
      } finally {
        _anvFrameBusy = false;
        anvFrameSpinner.classList.add("hidden");
      }
    }

    // ── Controls ────────────────────────────────────────────────
    anvBtnPlay.addEventListener("click", () => {
      if (_anvPlayTimer) {
        clearInterval(_anvPlayTimer); _anvPlayTimer = null;
        anvPlayIcon.classList.remove("hidden"); anvPauseIcon.classList.add("hidden");
      } else {
        anvPlayIcon.classList.add("hidden"); anvPauseIcon.classList.remove("hidden");
        _anvPlayTimer = setInterval(async () => {
          if (_anvCurrentFrame >= _anvFrameCount - 1) {
            clearInterval(_anvPlayTimer); _anvPlayTimer = null;
            anvPlayIcon.classList.remove("hidden"); anvPauseIcon.classList.add("hidden");
            return;
          }
          await _anvLoadFrame(_anvCurrentFrame + 1);
        }, 1000 / _anvFps);
      }
    });
    anvBtnPrev.addEventListener("click", () => _anvLoadFrame(_anvCurrentFrame - 1));
    anvBtnNext.addEventListener("click", () => _anvLoadFrame(_anvCurrentFrame + 1));

    anvSeek.addEventListener("mousedown",  () => { _anvSeekDragging = true; });
    anvSeek.addEventListener("touchstart", () => { _anvSeekDragging = true; });
    anvSeek.addEventListener("input", () => {
      _anvCurrentFrame = Math.round((anvSeek.value / 1000) * Math.max(_anvFrameCount - 1, 0));
      _anvCounterText.nodeValue  = `Frame ${_anvCurrentFrame} / ${_anvFrameCount}`;
      anvTimeDisplay.textContent = `${(_anvCurrentFrame / _anvFps).toFixed(3)} s`;
    });
    anvSeek.addEventListener("change", () => { _anvSeekDragging = false; _anvLoadFrame(_anvCurrentFrame); });

    anvCard.addEventListener("keydown", (e) => {
      if (anvPlayerSec.classList.contains("hidden")) return;
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      if (e.key === "ArrowLeft")  { e.preventDefault(); _anvLoadFrame(_anvCurrentFrame - 1); }
      if (e.key === "ArrowRight") { e.preventDefault(); _anvLoadFrame(_anvCurrentFrame + 1); }
    });

    // ── Build CSV bars — one segment per annotated frame ─────────
    function _anvBuildCsvBars() {
      const total     = Math.max(_anvFrameCount, 1);
      const hasNote   = _anvCsvRows.some(r => r.note);
      const hasStatus = _anvCsvRows.some(r => r.frame_line_status && r.frame_line_status !== "0");

      anvCsvBars.classList.toggle("hidden", !hasNote && !hasStatus);

      // Note bar
      anvNoteBarWrap.classList.toggle("hidden", !hasNote);
      anvNoteBar.innerHTML = "";
      if (hasNote) {
        const vals = [...new Set(_anvCsvRows.filter(r => r.note).map(r => r.note))];
        const colorMap = {};
        vals.forEach((v, i) => { colorMap[v] = _anvCsvPalette[i % _anvCsvPalette.length]; });
        _anvCsvRows.forEach(row => {
          if (!row.note) return;
          const fn    = Number(row.frame_number);
          const color = colorMap[row.note];
          const seg   = document.createElement("div");
          seg.className = "fe-timeline-seg";
          seg.style.cssText = `left:${(fn / total) * 100}%;width:max(0.5%,3px);background:${color}40;border-color:${color};color:${color}`;
          seg.textContent = row.note;
          seg.title = `${row.note}  (frame ${fn})`;
          seg.addEventListener("click", () => _anvLoadFrame(fn));
          anvNoteBar.appendChild(seg);
        });
      }

      // Status bar
      anvStatusBarWrap.classList.toggle("hidden", !hasStatus);
      anvStatusBar.innerHTML = "";
      if (hasStatus) {
        const vals = [...new Set(_anvCsvRows.filter(r => r.frame_line_status && r.frame_line_status !== "0").map(r => r.frame_line_status))];
        const colorMap = {};
        vals.forEach((v, i) => { colorMap[v] = _anvCsvPalette[i % _anvCsvPalette.length]; });
        _anvCsvRows.forEach(row => {
          if (!row.frame_line_status || row.frame_line_status === "0") return;
          const fn    = Number(row.frame_number);
          const color = colorMap[row.frame_line_status];
          const seg   = document.createElement("div");
          seg.className = "fe-timeline-seg";
          seg.style.cssText = `left:${(fn / total) * 100}%;width:max(0.5%,3px);background:${color}40;border-color:${color};color:${color}`;
          seg.textContent = row.frame_line_status;
          seg.title = `${row.frame_line_status}  (frame ${fn})`;
          seg.addEventListener("click", () => _anvLoadFrame(fn));
          anvStatusBar.appendChild(seg);
        });
      }
    }

    // ── Sync annotation panel to current frame ───────────────────
    function _anvSyncAnnotationPanel() {
      if (!_anvCsvPath) return;
      anvAnnotateFrameNum.textContent = _anvCurrentFrame;
      const row = _anvCsvRows.find(r => r.frame_number === _anvCurrentFrame);
      anvNoteInput.value    = row ? (row.note || "") : "";
      anvStatusInput.value  = row ? (row.frame_line_status || "0") : "0";
    }

    // ── Apply CSV rows to state and UI ───────────────────────────
    function _anvApplyCsvRows(rows, csvPath) {
      _anvCsvPath  = csvPath;
      _anvCsvRows  = rows;

      // Collect unique note tags
      const noteVals = [...new Set(rows.map(r => r.note).filter(v => v))];
      _anvUserTags  = [...new Set([..._anvUserTags, ...noteVals])];

      // Show CSV status
      anvCsvNone.classList.add("hidden");
      anvCsvLoaded.classList.remove("hidden");
      anvCsvPathDisplay.textContent = csvPath;
      anvCsvPathDisplay.title       = csvPath;

      // Show annotation panel
      anvAnnotationPanel.classList.remove("hidden");

      // Build CSV bars
      _anvBuildCsvBars();

      // Render tag chips
      _anvRenderTagChips();

      // Sync to current frame
      _anvSyncAnnotationPanel();
    }

    // ── Render clickable note tag chips ──────────────────────────
    function _anvRenderTagChips() {
      anvTagChips.innerHTML = "";
      _anvUserTags.forEach(tag => {
        const chip = document.createElement("span");
        chip.className = "fe-tag-chip";
        chip.textContent = tag;
        chip.style.setProperty("--chip-color", "#6ee7b7");
        chip.title = `Click to annotate frame ${_anvCurrentFrame} with note "${tag}"`;
        chip.addEventListener("click", () => _anvApplyTag(tag));
        anvTagChips.appendChild(chip);
      });
    }

    // ── Apply a note tag to the current frame ────────────────────
    async function _anvApplyTag(tag) {
      if (!_anvCsvPath) return;
      anvNoteInput.value = tag;
      await _anvSaveAnnotation();
    }

    // ── Save annotation for current frame ────────────────────────
    async function _anvSaveAnnotation() {
      if (!_anvCsvPath) return;
      anvSaveStatus.textContent = "Saving…";
      anvSaveStatus.className   = "fe-extract-status";
      const note   = anvNoteInput.value.trim();
      const status = anvStatusInput.value || "0";
      try {
        const res  = await fetch("/annotate/save-row", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            csv_path:          _anvCsvPath,
            frame_number:      _anvCurrentFrame,
            note,
            frame_line_status: status,
            fps:               _anvFps,
          }),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        // Update _anvCsvRows in-place — only "interesting" rows are tracked
        // (non-empty note OR status that isn't the default "0")
        const isInteresting = note || (status && status !== "0");
        const idx = _anvCsvRows.findIndex(r => r.frame_number === _anvCurrentFrame);
        if (isInteresting) {
          const savedRow = data.row || {
            frame_number:      _anvCurrentFrame,
            timestamp:         (_anvCurrentFrame / _anvFps).toFixed(3),
            frame_line_status: status,
            note,
          };
          if (idx >= 0) _anvCsvRows[idx] = savedRow;
          else {
            _anvCsvRows.push(savedRow);
            _anvCsvRows.sort((a, b) => a.frame_number - b.frame_number);
          }
          // Add new note to user tags if needed
          if (note && !_anvUserTags.includes(note)) {
            _anvUserTags.push(note);
            _anvRenderTagChips();
          }
        } else {
          // Row is now default — remove it from the tracked list
          if (idx >= 0) _anvCsvRows.splice(idx, 1);
        }

        // Rebuild the timeline bars to reflect the change
        _anvBuildCsvBars();

        anvSaveStatus.textContent = "Saved";
        anvSaveStatus.className   = "fe-extract-status ok";
        setTimeout(() => { if (anvSaveStatus.textContent === "Saved") anvSaveStatus.textContent = ""; }, 2000);
      } catch (err) {
        anvSaveStatus.textContent = `Error: ${err.message}`;
        anvSaveStatus.className   = "fe-extract-status err";
      }
    }

    anvSaveAnnotationBtn.addEventListener("click", _anvSaveAnnotation);

    // ── Add new tag ──────────────────────────────────────────────
    anvAddTagBtn.addEventListener("click", () => {
      const tag = anvNewTagInput.value.trim();
      if (!tag) return;
      if (!_anvUserTags.includes(tag)) {
        _anvUserTags.push(tag);
        _anvRenderTagChips();
      }
      anvNewTagInput.value = "";
    });
    anvNewTagInput.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); anvAddTagBtn.click(); }
    });

    // ── Load video ───────────────────────────────────────────────
    async function _anvLoadVideo(path) {
      _anvReset();
      _anvVideoPath = path;
      anvVideoPath.value = path;
      anvLoadStatus.textContent = "Loading video info…";
      anvLoadStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch(`/annotate/video-info?path=${encodeURIComponent(path)}`);
        const info = await res.json();
        if (info.error) throw new Error(info.error);
        _anvFps        = info.fps || 30;
        _anvFrameCount = info.frame_count || 0;
      } catch (err) {
        anvLoadStatus.textContent = `Error: ${err.message}`;
        anvLoadStatus.className   = "fe-extract-status err";
        return;
      }
      anvLoadStatus.textContent = "";
      anvPlayerSec.classList.remove("hidden");
      anvCsvSection.classList.remove("hidden");
      _anvLoadFrame(0);

      // Try to load companion CSV
      try {
        const res  = await fetch(`/annotate/csv?path=${encodeURIComponent(path)}`);
        const data = await res.json();
        if (data.csv_exists) {
          _anvApplyCsvRows(data.rows, data.csv_path);
        } else {
          anvCsvNone.classList.remove("hidden");
          anvCsvLoaded.classList.add("hidden");
        }
      } catch (_) {
        anvCsvNone.classList.remove("hidden");
        anvCsvLoaded.classList.add("hidden");
      }
    }

    anvLoadBtn.addEventListener("click", () => {
      const path = anvVideoPath.value.trim();
      if (!path) { anvLoadStatus.textContent = "Enter a video path first."; anvLoadStatus.className = "fe-extract-status err"; return; }
      _anvLoadVideo(path);
    });

    // ── Create CSV ───────────────────────────────────────────────
    anvCreateCsvBtn.addEventListener("click", async () => {
      if (!_anvVideoPath) return;
      anvCsvCreateStatus.textContent = `Creating CSV for ${_anvFrameCount} frames…`;
      anvCsvCreateStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch("/annotate/create-csv", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            video_path:  _anvVideoPath,
            fps:         _anvFps,
            frame_count: _anvFrameCount,
          }),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        anvCsvCreateStatus.textContent = "";
        _anvApplyCsvRows(data.rows, data.csv_path);
      } catch (err) {
        anvCsvCreateStatus.textContent = `Error: ${err.message}`;
        anvCsvCreateStatus.className   = "fe-extract-status err";
      }
    });

    // ── Refresh CSV ──────────────────────────────────────────────
    async function _anvRefreshCsv() {
      if (!_anvVideoPath) return;
      try {
        const res  = await fetch(`/annotate/csv?path=${encodeURIComponent(_anvVideoPath)}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        if (data.csv_exists) {
          _anvApplyCsvRows(data.rows, data.csv_path);
        }
      } catch (err) {
        anvSaveStatus.textContent = `Refresh error: ${err.message}`;
        anvSaveStatus.className   = "fe-extract-status err";
      }
    }
    anvRefreshCsvBtn.addEventListener("click", _anvRefreshCsv);

    // ── File browser ─────────────────────────────────────────────
    const _anvVideoExts = new Set([".mp4", ".avi", ".mov", ".mkv", ".mpg", ".mpeg"]);
    function _anvIsVideo(name) { return _anvVideoExts.has(name.slice(name.lastIndexOf(".")).toLowerCase()); }

    async function _anvBrowseDir(dirPath) {
      anvBrowser.innerHTML = `<span style="font-size:.8rem;color:var(--text-dim)">Loading…</span>`;
      try {
        const res  = await fetch(`/fs/ls?path=${encodeURIComponent(dirPath)}`);
        const data = await res.json();
        if (data.error) { anvBrowser.innerHTML = `<span style="font-size:.78rem;color:var(--text-dim)">${data.error}</span>`; return; }
        anvBrowser.innerHTML = "";

        // Header: path + Up button
        const header = document.createElement("div");
        header.style.cssText = "display:flex;align-items:center;gap:.4rem;padding:.15rem .2rem .3rem;border-bottom:1px solid var(--border);margin-bottom:.2rem;min-width:0";
        const pathLabel = document.createElement("span");
        pathLabel.style.cssText = "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono);font-size:.7rem;color:var(--text-dim)";
        pathLabel.textContent = data.path;
        pathLabel.title = data.path;
        header.appendChild(pathLabel);
        if (data.parent) {
          const upBtn = document.createElement("button");
          upBtn.className = "btn-sm";
          upBtn.style.cssText = "padding:.12rem .45rem;font-size:.7rem;flex-shrink:0";
          upBtn.textContent = "↑ Up";
          upBtn.addEventListener("click", e => { e.stopPropagation(); _anvBrowseDir(data.parent); });
          header.appendChild(upBtn);
        }
        anvBrowser.appendChild(header);

        const visible = data.entries.filter(e => e.type === "dir" || (e.type === "file" && _anvIsVideo(e.name)));
        if (!visible.length) {
          const empty = document.createElement("span");
          empty.style.cssText = "font-size:.75rem;color:var(--text-dim);padding:.25rem;display:block";
          empty.textContent = "(no video files here)";
          anvBrowser.appendChild(empty);
        } else {
          visible.forEach(e => {
            const row = document.createElement("div");
            row.style.cssText = "display:flex;align-items:center;gap:.35rem;padding:.18rem .3rem;border-radius:4px;cursor:pointer;font-size:.77rem";
            const icon = e.type === "dir"
              ? `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="flex-shrink:0;color:var(--text-dim)"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`
              : `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="flex-shrink:0;color:var(--text-dim)"><rect x="2" y="2" width="20" height="20" rx="3"/><polygon points="10 8 16 12 10 16 10 8" fill="currentColor" stroke="none"/></svg>`;
            row.innerHTML = `${icon}<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0">${e.name}</span>`;
            row.addEventListener("mouseenter", () => { row.style.background = "var(--surface-3,#2a2a2a)"; });
            row.addEventListener("mouseleave", () => { row.style.background = ""; });
            const fullPath = data.path.replace(/\/+$/, "") + "/" + e.name;
            if (e.type === "dir") {
              row.addEventListener("click", () => _anvBrowseDir(fullPath));
            } else {
              row.addEventListener("click", () => {
                anvVideoPath.value = fullPath;
                anvBrowser.classList.add("hidden");
              });
            }
            anvBrowser.appendChild(row);
          });
        }
      } catch (err) {
        anvBrowser.innerHTML = `<span style="font-size:.78rem;color:var(--text-dim)">Error: ${err.message}</span>`;
      }
    }

    anvBrowseBtn.addEventListener("click", () => {
      if (anvBrowser.classList.contains("hidden")) {
        anvBrowser.classList.remove("hidden");
        const startPath = _userDataDir || "/";
        _anvBrowseDir(startPath);
      } else {
        anvBrowser.classList.add("hidden");
      }
    });

    // ── Open / close card ────────────────────────────────────────
    anvOpenBtn?.addEventListener("click", () => {
      anvCard.classList.remove("hidden");
      anvCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });

    anvCloseBtn?.addEventListener("click", () => {
      anvCard.classList.add("hidden");
      _anvReset();
      anvBrowser.classList.add("hidden");
    });
  })();

  // ── GPU & Training Monitor ────────────────────────────────────
  (() => {
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
      _dlcTrainingActive = (data.jobs || []).some(j => (j.status === "running" || j.status === "dead") && j.operation !== "analyze");
      const tnRunBtn = document.getElementById("btn-run-train-network");
      if (tnRunBtn && !tnRunBtn._tnPolling) {
        tnRunBtn.disabled = _dlcTrainingActive;
        tnRunBtn.title    = _dlcTrainingActive ? "A training job is already running" : "";
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
  })();

  // ── Custom Script Runner ─────────────────────────────────────
  (function () {
    const csCard           = document.getElementById("custom-script-card");
    const openBtn          = document.getElementById("btn-open-custom-script");
    const closeBtn         = document.getElementById("btn-close-custom-script");

    // Script picker
    const csScriptBrowseBtn    = document.getElementById("cs-script-browse-btn");
    const csScriptPathDisplay  = document.getElementById("cs-script-path-display");
    const csScriptNav          = document.getElementById("cs-script-nav");
    const csScriptBreadcrumb   = document.getElementById("cs-script-breadcrumb");
    const csScriptEntries      = document.getElementById("cs-script-entries");

    // Input picker
    const csInputModeFile      = document.getElementById("cs-input-mode-file");
    const csInputModeFolder    = document.getElementById("cs-input-mode-folder");
    const csInputBrowseBtn     = document.getElementById("cs-input-browse-btn");
    const csInputPathDisplay   = document.getElementById("cs-input-path-display");
    const csInputNav           = document.getElementById("cs-input-nav");
    const csInputBreadcrumb    = document.getElementById("cs-input-breadcrumb");
    const csInputEntries       = document.getElementById("cs-input-entries");
    const csInputNavHint       = document.getElementById("cs-input-nav-hint");

    // Run / output
    const csRunBtn         = document.getElementById("cs-run-btn");
    const csAbortBtn       = document.getElementById("cs-abort-btn");
    const csRunStatus      = document.getElementById("cs-run-status");
    const csOutputSection  = document.getElementById("cs-output-section");
    const csOutputDir      = document.getElementById("cs-output-dir");
    const csLogOutput      = document.getElementById("cs-log-output");

    let _csSelectedScript = null;
    let _csSelectedInput  = null;
    let _csInputMode      = "file";   // "file" | "folder"
    let _csScriptNavPath  = null;
    let _csInputNavPath   = null;
    let _csPollTimer      = null;
    let _csJobId          = null;

    // ── Open / close ─────────────────────────────────────────
    openBtn?.addEventListener("click", () => {
      csCard.classList.remove("hidden");
      csCard.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    closeBtn?.addEventListener("click", () => {
      csCard.classList.add("hidden");
    });

    // ── Input-mode toggle ─────────────────────────────────────
    function _setInputMode(mode) {
      _csInputMode = mode;
      csInputModeFile.classList.toggle("active", mode === "file");
      csInputModeFolder.classList.toggle("active", mode === "folder");
      const hint = mode === "folder"
        ? "Click folders to navigate &nbsp;·&nbsp; <strong>Double-click</strong> a folder to select all its CSVs"
        : "Click folders to navigate &nbsp;·&nbsp; <strong>Double-click</strong> a <code style=\"font-family:var(--mono);font-size:.73rem\">.csv</code> file to select it";
      csInputNavHint.innerHTML = hint;
      _csSelectedInput = null;
      csInputPathDisplay.textContent = "No input selected";
      if (!csInputNav.classList.contains("hidden") && _csInputNavPath) {
        _refreshInputNav(_csInputNavPath);
      }
    }
    csInputModeFile?.addEventListener("click",   () => _setInputMode("file"));
    csInputModeFolder?.addEventListener("click", () => _setInputMode("folder"));

    // ── Generic filesystem navigator ──────────────────────────
    function _csDefaultPath() {
      return _userDataDir || _dataDir || "/";
    }

    async function _refreshCsNav(path, breadcrumbEl, entriesEl, onFileDblClick, onFolderDblClick, showFiles, fileExt) {
      // Breadcrumb
      const base     = _csDefaultPath();
      const baseName = base.split("/").filter(Boolean).pop() || "/";
      const rel      = path.startsWith(base)
        ? path.substring(base.length).split("/").filter(Boolean)
        : path.split("/").filter(Boolean);
      let crumbHTML  = `<button class="userdata-bc-seg" data-path="${base}">${baseName}</button>`;
      let cumPath    = base;
      rel.forEach((part, i) => {
        cumPath += "/" + part;
        const isLast = (i === rel.length - 1);
        crumbHTML += `<span class="userdata-bc-sep">›</span>`;
        crumbHTML += `<button class="userdata-bc-seg${isLast ? " active" : ""}" data-path="${cumPath}">${part}</button>`;
      });
      breadcrumbEl.innerHTML = crumbHTML;
      breadcrumbEl.querySelectorAll(".userdata-bc-seg").forEach(seg =>
        seg.addEventListener("click", () => {
          if (breadcrumbEl === csScriptBreadcrumb) {
            _csScriptNavPath = seg.dataset.path;
            _refreshScriptNav(seg.dataset.path);
          } else {
            _csInputNavPath = seg.dataset.path;
            _refreshInputNav(seg.dataset.path);
          }
        })
      );

      entriesEl.innerHTML = '<span class="userdata-no-folders">Loading…</span>';

      try {
        const res  = await fetch(`/fs/ls?path=${encodeURIComponent(path)}`);
        const data = await res.json();
        entriesEl.innerHTML = "";

        // ".." chip when not at the default base
        if (path !== base && path !== "/") {
          const upBtn = document.createElement("button");
          upBtn.className   = "userdata-subfolder-chip up";
          upBtn.textContent = "..";
          upBtn.title       = "Go up one level";
          const parent = path.split("/").slice(0, -1).join("/") || "/";
          upBtn.addEventListener("click", () => {
            if (breadcrumbEl === csScriptBreadcrumb) {
              _csScriptNavPath = parent;
              _refreshScriptNav(parent);
            } else {
              _csInputNavPath = parent;
              _refreshInputNav(parent);
            }
          });
          entriesEl.appendChild(upBtn);
        }

        const entries = res.ok ? (data.entries || []) : [];
        let hasItems = entriesEl.children.length > 0;

        entries.forEach(entry => {
          if (entry.type === "dir") {
            hasItems = true;
            const chip      = document.createElement("button");
            chip.className  = "userdata-subfolder-chip";
            chip.textContent = entry.name + "/";
            chip.title      = "Click to navigate · Double-click to select folder";
            const childPath = path + "/" + entry.name;
            chip.addEventListener("click", () => {
              if (breadcrumbEl === csScriptBreadcrumb) {
                _csScriptNavPath = childPath;
                _refreshScriptNav(childPath);
              } else {
                _csInputNavPath = childPath;
                _refreshInputNav(childPath);
              }
            });
            if (onFolderDblClick) {
              chip.addEventListener("dblclick", e => {
                e.preventDefault();
                onFolderDblClick(childPath, entry.name);
              });
            }
            entriesEl.appendChild(chip);
          } else if (showFiles && entry.name.toLowerCase().endsWith(fileExt)) {
            hasItems = true;
            const chip      = document.createElement("button");
            chip.className  = "picker-config-chip";
            chip.textContent = entry.name;
            chip.title      = "Double-click to select";
            chip.addEventListener("dblclick", e => {
              e.preventDefault();
              onFileDblClick(path + "/" + entry.name, entry.name);
            });
            entriesEl.appendChild(chip);
          }
        });

        if (!hasItems) {
          const msg       = document.createElement("span");
          msg.className   = "userdata-no-folders";
          msg.textContent = "No items";
          entriesEl.appendChild(msg);
        }
      } catch (err) {
        console.error("cs nav error:", err);
        entriesEl.innerHTML = '<span class="userdata-no-folders">Failed to load</span>';
      }
    }

    function _refreshScriptNav(path) {
      _csScriptNavPath = path;
      _refreshCsNav(
        path,
        csScriptBreadcrumb, csScriptEntries,
        (filePath) => {               // file double-click → select script
          _csSelectedScript = filePath;
          csScriptPathDisplay.textContent = filePath;
          csScriptPathDisplay.style.color = "var(--text)";
          csScriptNav.classList.add("hidden");
        },
        null,                         // no folder select for script picker
        true, ".py"
      );
    }

    function _refreshInputNav(path) {
      _csInputNavPath = path;
      const isFolderMode = (_csInputMode === "folder");
      _refreshCsNav(
        path,
        csInputBreadcrumb, csInputEntries,
        isFolderMode ? null : (filePath) => {   // CSV file double-click
          _csSelectedInput = filePath;
          csInputPathDisplay.textContent = filePath;
          csInputPathDisplay.style.color = "var(--text)";
          csInputNav.classList.add("hidden");
        },
        isFolderMode ? (folderPath) => {        // folder double-click
          _csSelectedInput = folderPath;
          csInputPathDisplay.textContent = folderPath;
          csInputPathDisplay.style.color = "var(--text)";
          csInputNav.classList.add("hidden");
        } : null,
        !isFolderMode, ".csv"
      );
    }

    // ── Browse buttons ────────────────────────────────────────
    csScriptBrowseBtn?.addEventListener("click", () => {
      if (csScriptNav.classList.contains("hidden")) {
        csScriptNav.classList.remove("hidden");
        _refreshScriptNav(_csScriptNavPath || _csDefaultPath());
      } else {
        csScriptNav.classList.add("hidden");
      }
    });

    csInputBrowseBtn?.addEventListener("click", () => {
      if (csInputNav.classList.contains("hidden")) {
        csInputNav.classList.remove("hidden");
        _refreshInputNav(_csInputNavPath || _csDefaultPath());
      } else {
        csInputNav.classList.add("hidden");
      }
    });

    // ── Run script ────────────────────────────────────────────
    csRunBtn?.addEventListener("click", async () => {
      if (!_csSelectedScript) { alert("Select a Python script first."); return; }
      if (!_csSelectedInput)  { alert("Select an input CSV file or folder first."); return; }

      csRunBtn.disabled = true;
      csAbortBtn.classList.remove("hidden");
      csRunStatus.textContent = "Submitting…";
      csRunStatus.style.color = "var(--text-dim)";
      csOutputSection.classList.remove("hidden");
      csLogOutput.textContent = "Starting…";
      csOutputDir.textContent = "";

      try {
        const res  = await fetch("/custom-script/run", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            script_path: _csSelectedScript,
            input_mode:  _csInputMode,
            input_path:  _csSelectedInput,
          }),
        });
        const data = await res.json();

        if (!res.ok) {
          csRunStatus.textContent = data.error || "Error";
          csRunStatus.style.color = "var(--danger)";
          csRunBtn.disabled = false;
          csAbortBtn.classList.add("hidden");
          csLogOutput.textContent = data.error || "Server error";
          return;
        }

        _csJobId = data.job_id;
        csOutputDir.textContent = data.output_dir;
        csRunStatus.textContent = "Running…";
        csRunStatus.style.color = "var(--text-dim)";

        if (_csPollTimer) clearInterval(_csPollTimer);
        _csPollTimer = setInterval(_csPoll, 1500);
      } catch (err) {
        csRunStatus.textContent = "Network error";
        csRunStatus.style.color = "var(--danger)";
        csRunBtn.disabled = false;
        csAbortBtn.classList.add("hidden");
      }
    });

    async function _csPoll() {
      if (!_csJobId) return;
      try {
        const res  = await fetch(`/custom-script/status/${_csJobId}`);
        const data = await res.json();

        if (data.output) {
          csLogOutput.textContent = data.output;
          csLogOutput.scrollTop   = csLogOutput.scrollHeight;
        }

        if (data.status === "done") {
          clearInterval(_csPollTimer); _csPollTimer = null;
          csRunStatus.textContent = "Done";
          csRunStatus.style.color = "var(--accent)";
          csRunBtn.disabled = false;
          csAbortBtn.classList.add("hidden");
        } else if (data.status === "error") {
          clearInterval(_csPollTimer); _csPollTimer = null;
          const errMsg = data.error || "Script failed";
          csRunStatus.textContent = errMsg;
          csRunStatus.style.color = "var(--danger)";
          if (!data.output) csLogOutput.textContent = "[Error] " + errMsg;
          else csLogOutput.textContent = data.output + "\n\n[Error] " + errMsg;
          csLogOutput.scrollTop = csLogOutput.scrollHeight;
          csRunBtn.disabled = false;
          csAbortBtn.classList.add("hidden");
        }
      } catch (_) { /* ignore transient network errors */ }
    }

    csAbortBtn?.addEventListener("click", () => {
      if (_csPollTimer) { clearInterval(_csPollTimer); _csPollTimer = null; }
      csRunStatus.textContent = "Aborted (script may still finish on the server)";
      csRunStatus.style.color = "var(--danger)";
      csRunBtn.disabled = false;
      csAbortBtn.classList.add("hidden");
    });
  })();

  // ── Inspect Video ────────────────────────────────────────────
  document.getElementById("inspect-video-btn")?.addEventListener("click", () => {
    const projectId = folderSelect.value;
    if (!projectId) {
      alert("Select a project folder first.");
      return;
    }
    const overlay = document.getElementById("inspector-overlay");
    const frame   = document.getElementById("inspector-frame");
    frame.src = `/inspector#${encodeURIComponent(projectId)}`;
    overlay.classList.remove("hidden");
  });

})();

function closeInspector() {
  const overlay = document.getElementById("inspector-overlay");
  const frame   = document.getElementById("inspector-frame");
  overlay.classList.add("hidden");
  frame.src = "";
}

// Handle close message posted from inside the inspector iframe
window.addEventListener("message", e => {
  if (e.data === "closeInspector") closeInspector();
});
