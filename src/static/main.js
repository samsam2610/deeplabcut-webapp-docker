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

  let _dlcBrowsePath = null; // path currently browsed in the folder nav

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
    const feCard        = document.getElementById("frame-extractor-card");
    const feOpenBtn     = document.getElementById("btn-open-frame-extractor");
    const feCloseBtn    = document.getElementById("btn-close-frame-extractor");
    const feBtnProject  = document.getElementById("fe-btn-from-project");
    const feBtnUpload   = document.getElementById("fe-btn-upload");
    const feProjectVids = document.getElementById("fe-project-videos");
    const feVideoList   = document.getElementById("fe-video-list");
    const feUploadSec   = document.getElementById("fe-upload-section");
    const feFileInput   = document.getElementById("fe-video-file-input");
    const feUploadStatus= document.getElementById("fe-upload-status");
    const fePlayerSec   = document.getElementById("fe-player-section");
    const feVideo       = document.getElementById("fe-video");
    const feCanvas      = document.getElementById("fe-canvas");
    const feBtnPlay     = document.getElementById("fe-btn-play");
    const fePlayIcon    = document.getElementById("fe-play-icon");
    const fePauseIcon   = document.getElementById("fe-pause-icon");
    const feBtnPrev     = document.getElementById("fe-btn-prev");
    const feBtnNext     = document.getElementById("fe-btn-next");
    const feFrameCounter= document.getElementById("fe-frame-counter");
    const feTimeDisplay = document.getElementById("fe-time-display");
    const feSeek        = document.getElementById("fe-seek");
    const feBtnExtract  = document.getElementById("fe-btn-extract");
    const feExtractCount= document.getElementById("fe-extract-count");
    const feExtractStatus = document.getElementById("fe-extract-status");

    let _feFps         = 30;
    let _feFrameCount  = 0;
    let _feCurrentVideo= null; // filename string
    let _feExtracted   = 0;
    let _feSeekDragging= false;

    // ── Open / close ────────────────────────────────────────────
    feOpenBtn.addEventListener("click", () => {
      feCard.classList.remove("hidden");
      feCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      _feLoadProjectVideos();
    });

    feCloseBtn.addEventListener("click", () => {
      feCard.classList.add("hidden");
      feVideo.pause();
      feVideo.src = "";
      fePlayerSec.classList.add("hidden");
      _feCurrentVideo = null;
    });

    // ── Source toggle ────────────────────────────────────────────
    feBtnProject.addEventListener("click", () => {
      feBtnProject.classList.add("active");
      feBtnUpload.classList.remove("active");
      feProjectVids.classList.remove("hidden");
      feUploadSec.classList.add("hidden");
      _feLoadProjectVideos();
    });

    feBtnUpload.addEventListener("click", () => {
      feBtnUpload.classList.add("active");
      feBtnProject.classList.remove("active");
      feUploadSec.classList.remove("hidden");
      feProjectVids.classList.add("hidden");
    });

    // ── List project videos ──────────────────────────────────────
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

    async function _feSelectProjectVideo(filename, itemEl) {
      // Update active state
      feVideoList.querySelectorAll(".fe-video-item").forEach(el => el.classList.remove("active"));
      itemEl.classList.add("active");

      _feCurrentVideo = filename;
      _feExtracted = 0;
      feExtractCount.textContent = "0 frames saved";
      feExtractStatus.textContent = "";
      feExtractStatus.className = "fe-extract-status";

      // Fetch video metadata (fps, frame count)
      try {
        const res  = await fetch(`/dlc/project/video-info/${encodeURIComponent(filename)}`);
        const info = await res.json();
        _feFps        = info.fps || 30;
        _feFrameCount = info.frame_count || 0;
      } catch (_) {
        _feFps = 30; _feFrameCount = 0;
      }

      feVideo.src = `/dlc/project/video-stream/${encodeURIComponent(filename)}`;
      feVideo.load();
      fePlayerSec.classList.remove("hidden");
      _feUpdateFrameDisplay();
    }

    // ── File upload ──────────────────────────────────────────────
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
        _feCurrentVideo = data.saved;
        _feExtracted = 0;
        feExtractCount.textContent = "0 frames saved";
        feExtractStatus.textContent = "";
        feExtractStatus.className = "fe-extract-status";

        try {
          const res2  = await fetch(`/dlc/project/video-info/${encodeURIComponent(data.saved)}`);
          const info  = await res2.json();
          _feFps        = info.fps || 30;
          _feFrameCount = info.frame_count || 0;
        } catch (_) { _feFps = 30; _feFrameCount = 0; }

        feVideo.src = `/dlc/project/video-stream/${encodeURIComponent(data.saved)}`;
        feVideo.load();
        fePlayerSec.classList.remove("hidden");
        _feUpdateFrameDisplay();
      } catch (err) {
        feUploadStatus.textContent = `Upload failed: ${err.message}`;
      }
      feFileInput.value = "";
    });

    // ── Video event listeners ────────────────────────────────────
    feVideo.addEventListener("timeupdate", () => {
      if (!_feSeekDragging) _feUpdateFrameDisplay();
    });

    feVideo.addEventListener("play",  () => { fePlayIcon.classList.add("hidden"); fePauseIcon.classList.remove("hidden"); });
    feVideo.addEventListener("pause", () => { fePlayIcon.classList.remove("hidden"); fePauseIcon.classList.add("hidden"); });
    feVideo.addEventListener("ended", () => { fePlayIcon.classList.remove("hidden"); fePauseIcon.classList.add("hidden"); });

    function _feUpdateFrameDisplay() {
      const t     = feVideo.currentTime;
      const frame = Math.round(t * _feFps);
      const total = _feFrameCount || Math.round((feVideo.duration || 0) * _feFps);
      feFrameCounter.textContent = `Frame ${frame} / ${total}`;
      feTimeDisplay.textContent  = `${t.toFixed(3)} s`;
      if (feVideo.duration > 0 && !_feSeekDragging) {
        feSeek.value = Math.round((t / feVideo.duration) * 1000);
      }
    }

    // ── Controls ─────────────────────────────────────────────────
    feBtnPlay.addEventListener("click", () => {
      if (feVideo.paused) feVideo.play(); else feVideo.pause();
    });

    feBtnPrev.addEventListener("click", () => {
      feVideo.pause();
      feVideo.currentTime = Math.max(0, feVideo.currentTime - 1 / _feFps);
    });

    feBtnNext.addEventListener("click", () => {
      feVideo.pause();
      feVideo.currentTime = Math.min(feVideo.duration || 0, feVideo.currentTime + 1 / _feFps);
    });

    // Seek slider
    feSeek.addEventListener("mousedown", () => { _feSeekDragging = true; });
    feSeek.addEventListener("touchstart", () => { _feSeekDragging = true; });
    feSeek.addEventListener("input", () => {
      if (feVideo.duration) feVideo.currentTime = (feSeek.value / 1000) * feVideo.duration;
      _feUpdateFrameDisplay();
    });
    feSeek.addEventListener("change", () => { _feSeekDragging = false; });

    // ── Capture + save helpers ───────────────────────────────────
    async function _feCaptureCurrent() {
      feCanvas.width  = feVideo.videoWidth;
      feCanvas.height = feVideo.videoHeight;
      try {
        feCanvas.getContext("2d").drawImage(feVideo, 0, 0);
        const url = feCanvas.toDataURL("image/jpeg", 0.92);
        return url.split(",")[1] || null;
      } catch (secErr) {
        feExtractStatus.textContent = `Canvas error: ${secErr.message}`;
        feExtractStatus.className = "fe-extract-status err";
        return null;
      }
    }

    async function _feSaveFrames(count) {
      if (!_feCurrentVideo) return;
      if (feVideo.readyState < 2) { feExtractStatus.textContent = "Video not ready."; return; }

      feBtnExtract.disabled = true;
      feVideo.pause();
      if (count > 1) {
        feExtractStatus.textContent = `Saving ${count} frames…`;
        feExtractStatus.className = "fe-extract-status";
      }

      let saved = 0, skipped = 0, lastData = null;
      try {
        for (let i = 0; i < count; i++) {
          if (i > 0) {
            const next = Math.min(feVideo.duration || 0, feVideo.currentTime + 1 / _feFps);
            feVideo.currentTime = next;
            await new Promise(resolve => feVideo.addEventListener("seeked", resolve, { once: true }));
          }
          const frameNumber = Math.round(feVideo.currentTime * _feFps);
          const base64 = await _feCaptureCurrent();
          if (!base64) break;
          const res  = await fetch("/dlc/project/save-frame", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ video_name: _feCurrentVideo, frame_data: base64, frame_number: frameNumber }),
          });
          const data = await res.json();
          if (data.skipped) { skipped++; continue; }
          if (data.error) {
            feExtractStatus.textContent = `Error on frame ${i + 1}: ${data.error}`;
            feExtractStatus.className = "fe-extract-status err";
            break;
          }
          saved++;
          lastData = data;
          _feExtracted = data.frame_count;
          feExtractCount.textContent = `${_feExtracted} frame${_feExtracted !== 1 ? "s" : ""} saved`;
        }
        if (saved > 0) {
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
        _feUpdateFrameDisplay();
      }
    }

    feBtnExtract.addEventListener("click", () => _feSaveFrames(1));

    // ── Keyboard shortcuts (active while mouse hovers over player) ──
    let _feHover      = false;
    let _fePending    = null;  // digit waiting for "s"
    let _fePendingTmr = null;

    fePlayerSec.addEventListener("mouseenter", () => { _feHover = true; });
    fePlayerSec.addEventListener("mouseleave", () => { _feHover = false; _fePending = null; clearTimeout(_fePendingTmr); feExtractStatus.textContent = feExtractStatus.textContent.startsWith("Press") ? "" : feExtractStatus.textContent; });

    document.addEventListener("keydown", e => {
      if (!_feHover || fePlayerSec.classList.contains("hidden")) return;
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

      // Spacebar → play / pause
      if (e.key === " ") { e.preventDefault(); feBtnPlay.click(); return; }

      // Arrows → step one frame
      if (e.key === "ArrowLeft")  { e.preventDefault(); feBtnPrev.click(); return; }
      if (e.key === "ArrowRight") { e.preventDefault(); feBtnNext.click(); return; }

      // Digit 1-9 → start combo
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
