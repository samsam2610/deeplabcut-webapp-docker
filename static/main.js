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
    const actionsCard  = document.getElementById("actions-card");
    const configCard   = document.getElementById("config-card");
    const explorerCard = document.getElementById("explorer-card");

    if (actionsCard)  actionsCard.classList.toggle("hidden",  s !== "ready");
    if (configCard)   configCard.classList.toggle("hidden",   s !== "ready");
    if (explorerCard) explorerCard.classList.toggle("hidden", s !== "ready");

    if (s === "ready") {
      loadProjects();
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

  // ── Populate project folder dropdowns ───────────────────────
  const explorerFolderSelect = document.getElementById("explorer-folder-select");

  async function loadProjects() {
    try {
      const res  = await fetch("/projects");
      const data = await res.json();
      // Exclude session_ dirs — they hold config only, not project data
      const projects = (data.projects || []).filter(p => !p.startsWith("session_"));
      const opts = '<option value="">— select a project —</option>' +
        projects.map(p => `<option value="${p}">${p}</option>`).join("");
      folderSelect.innerHTML         = opts;
      explorerFolderSelect.innerHTML = opts;
    } catch (err) {
      console.error("loadProjects error:", err);
    }
  }

  // ── Project Explorer ─────────────────────────────────────────
  const explorerFolders       = document.getElementById("explorer-folders");
  const explorerProjectActions= document.getElementById("explorer-project-actions");
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
      const res  = await fetch("/projects", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ name }),
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
        await loadProjects();
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

  downloadProjectBtn.addEventListener("click", () => {
    if (_currentProjectId) window.location.href = `/projects/${_currentProjectId}/download`;
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
      window.location.href = `/projects/${projectId}/download?folder=${encodeURIComponent(folder)}`;
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
      const res  = await fetch(`/projects/${projectId}/file`, {
        method:  "PATCH",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ folder, old_name: oldName, new_name: newName }),
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
      const res  = await fetch(`/projects/${projectId}/file`, {
        method:  "DELETE",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ folder, filename }),
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
      const res  = await fetch(`/projects/${projectId}/browse`);
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

    pollTimer = setInterval(() => pollStatus(taskId), 5000);
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
      }

      if (data.state === "FAILURE") {
        clearInterval(pollTimer);
        operationProgress.classList.add("state-fail");
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
    operationProgress.classList.add("hidden");
    progressTitle.textContent = "Processing";  // reset title for next run
    actionBtns.forEach(b => { b.disabled = false; });
  });

})();
