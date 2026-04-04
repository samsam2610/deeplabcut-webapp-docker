"use strict";
import { state } from './state.js';

  // ── DLC Project Manager ──────────────────────────────────────
  const dlcDot              = document.getElementById("dlc-dot");
  const dlcLabel            = document.getElementById("dlc-label");
  const dlcMeta             = document.getElementById("dlc-meta");
  const btnManageDlc        = document.getElementById("btn-manage-dlc");
  const btnDlcClear         = document.getElementById("btn-dlc-clear");

  const dlcProjectCard      = document.getElementById("dlc-project-card");
  const dlcFolderNav        = document.getElementById("dlc-folder-nav");
  const dlcBrowseBreadcrumb = document.getElementById("dlc-browse-breadcrumb");
  const dlcBrowseUp         = document.getElementById("dlc-browse-up");
  const dlcFolderList       = document.getElementById("dlc-folder-list");
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

  // ── Apply DLC project state to bar + card ───────────────────
  export function applyDlcProjectState(data) {
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
      state.dlcEngine = (data.engine || "pytorch").toLowerCase();
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
    dlcProjectCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
    // Auto-open folder browser if user data is available
    if (state.userDataDir && dlcFolderNav.classList.contains("hidden")) {
      dlcFolderNav.classList.remove("hidden");
      _refreshDlcFolderNav(state.userDataDir);
    } else if (!state.userDataDir) {
      dlcBrowseInfo.textContent = "No user data volume mounted";
      dlcBrowseInfo.className   = "dlc-browse-info err";
    }
  });

  // ── Browse user data button ──────────────────────────────────
  dlcBrowseBtn.addEventListener("click", () => {
    if (!state.userDataDir) {
      dlcBrowseInfo.textContent = "No user data volume mounted";
      dlcBrowseInfo.className   = "dlc-browse-info err";
      return;
    }
    dlcFolderNav.classList.remove("hidden");
    _refreshDlcFolderNav(state.dlcBrowsePath || state.userDataDir);
  });

  // ── Folder navigator ──────────────────────────────────────────
  async function _refreshDlcFolderNav(path) {
    state.dlcBrowsePath = path;
    dlcBrowseBreadcrumb.value = path;
    dlcFolderList.innerHTML = '<p class="explorer-empty">Loading…</p>';
    try {
      const res  = await fetch(`/fs/ls?path=${encodeURIComponent(path)}`);
      const data = await res.json();
      if (data.error) {
        dlcFolderList.innerHTML = `<span class="userdata-no-folders">${data.error}</span>`;
        return;
      }
      dlcFolderList.innerHTML = "";
      const dirs = data.entries.filter(e => e.type === "dir");
      if (!dirs.length) {
        const msg = document.createElement("span");
        msg.className   = "userdata-no-folders";
        msg.textContent = "No subfolders";
        dlcFolderList.appendChild(msg);
      } else {
        dirs.forEach(entry => {
          const row = document.createElement("div");
          row.className = "fe-video-item";
          row.style.cursor = "pointer";
          const fullPath = data.path.replace(/\/+$/, "") + "/" + entry.name;
          row.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${entry.name}/</span>`;
          row.addEventListener("click", () => _refreshDlcFolderNav(fullPath));
          dlcFolderList.appendChild(row);
        });
      }
    } catch (err) {
      console.error("DLC folder nav error:", err);
      dlcFolderList.innerHTML = '<span class="userdata-no-folders">Failed to load</span>';
    }
  }

  dlcBrowseUp?.addEventListener("click", () => {
    if (!state.dlcBrowsePath) return;
    const parent = state.dlcBrowsePath.split("/").slice(0, -1).join("/") || "/";
    if (parent !== state.dlcBrowsePath) _refreshDlcFolderNav(parent);
  });

  dlcBrowseBreadcrumb?.addEventListener("keydown", e => {
    if (e.key === "Enter")  { e.preventDefault(); _refreshDlcFolderNav(dlcBrowseBreadcrumb.value.trim()); }
    if (e.key === "Escape") { dlcBrowseBreadcrumb.value = state.dlcBrowsePath || ""; dlcBrowseBreadcrumb.blur(); }
  });
  dlcBrowseBreadcrumb?.addEventListener("paste", e => {
    setTimeout(() => _refreshDlcFolderNav(dlcBrowseBreadcrumb.value.trim()), 0);
  });

  // ── Select current folder as DLC project ────────────────────
  dlcSelectBtn.addEventListener("click", async () => {
    if (!state.dlcBrowsePath) return;

    dlcSelectStatus.textContent = "Checking for config.yaml…";
    dlcSelectStatus.className   = "dlc-config-status";

    try {
      const res  = await fetch("/dlc/project", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ path: state.dlcBrowsePath }),
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
    state.dlcBrowsePath = null;
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
  export const sourceBtnUserData    = document.getElementById("source-btn-userdata");
  const sourceBtnLocal       = document.getElementById("source-btn-local");
  const userDataNav          = document.getElementById("userdata-nav");
  const userDataBreadcrumb   = document.getElementById("userdata-breadcrumb");
  const userDataSubfolders   = document.getElementById("userdata-subfolders");

  export async function loadProjects(root) {
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
      document.getElementById("folder-select").innerHTML = opts;
      explorerFolderSelect.innerHTML = opts;
      return true;
    } catch (err) {
      console.error("loadProjects error:", err);
      return false;
    }
  }

  // ── User-data folder navigator ────────────────────────────────
  async function _refreshUserDataNav(path) {
    state.currentRoot = path;

    // Render breadcrumb
    const baseName = state.userDataDir.split("/").filter(Boolean).pop() || "user-data";
    const rel = path.substring(state.userDataDir.length).split("/").filter(Boolean);
    let crumbHTML = `<button class="userdata-bc-seg" data-path="${state.userDataDir}">${baseName}</button>`;
    let cumPath = state.userDataDir;
    rel.forEach((part, i) => {
      cumPath += "/" + part;
      const isLast = (i === rel.length - 1);
      crumbHTML += `<span class="userdata-bc-sep">›</span>`;
      crumbHTML += `<button class="userdata-bc-seg${isLast ? " active" : ""}" data-path="${cumPath}">${part}</button>`;
    });
    userDataBreadcrumb.innerHTML = crumbHTML;
    userDataBreadcrumb.querySelectorAll(".userdata-bc-seg").forEach(seg => {
      seg.addEventListener("click", async () => {
        if (seg.dataset.path === state.currentRoot) return;
        _onProjectSelected("");
        await _refreshUserDataNav(seg.dataset.path);
        await loadProjects(state.currentRoot);
      });
    });

    // Render subfolder chips
    userDataSubfolders.innerHTML = "";

    // ".." chip when not at the volume root
    if (path !== state.userDataDir) {
      const upBtn = document.createElement("button");
      upBtn.className   = "userdata-subfolder-chip up";
      upBtn.textContent = "..";
      upBtn.title       = "Go up one level";
      const parent = path.split("/").slice(0, -1).join("/") || "/";
      upBtn.addEventListener("click", async () => {
        _onProjectSelected("");
        await _refreshUserDataNav(parent);
        await loadProjects(state.currentRoot);
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
            await loadProjects(state.currentRoot);
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
      state.currentRoot = "";
      await loadProjects("");
    } else {
      userDataNav.classList.remove("hidden");
      await _refreshUserDataNav(root);
      await loadProjects(state.currentRoot);
    }
  }

  sourceBtnLocal.addEventListener("click", () => _selectSource(""));
  sourceBtnUserData.addEventListener("click", () => {
    if (state.userDataDir) _selectSource(state.userDataDir);
  });

  // Fetch /config to learn the user-data path and enable buttons
  export async function _initConfig() {
    try {
      const res  = await fetch("/config");
      const data = await res.json();
      if (data.user_data_dir) {
        state.userDataDir = data.user_data_dir;
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
      if (state.currentRoot) body.root = state.currentRoot;
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
        await loadProjects(state.currentRoot);
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
  function _onProjectSelected(pid) {
    document.getElementById("folder-select").value = pid;
    explorerFolderSelect.value = pid;
    state.currentProjectId          = pid;
    explorerProjectActions.classList.toggle("hidden", !pid);
    if (pid) browseProject(pid);
    else explorerFolders.innerHTML = '<p class="explorer-empty">Select or create a project to browse its pipeline folders.</p>';
  }

  refreshExplorerBtn.addEventListener("click", () => {
    if (state.currentProjectId) browseProject(state.currentProjectId);
  });

  downloadProjectBtn.addEventListener("click", () => {
    if (!state.currentProjectId) return;
    const rootParam = state.currentRoot ? `?root=${encodeURIComponent(state.currentRoot)}` : "";
    window.location.href = `/projects/${state.currentProjectId}/download${rootParam}`;
  });

  explorerFolderSelect.addEventListener("change", () => _onProjectSelected(explorerFolderSelect.value));
  document.getElementById("folder-select").addEventListener("change",         () => _onProjectSelected(document.getElementById("folder-select").value));

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
      const rootParam = state.currentRoot ? `&root=${encodeURIComponent(state.currentRoot)}` : "";
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
    if (state.currentRoot) fd.append("root", state.currentRoot);
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
      if (state.currentRoot) body.root = state.currentRoot;
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
      if (state.currentRoot) body.root = state.currentRoot;
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

  export async function browseProject(projectId) {
    explorerFolders.innerHTML = '<p class="explorer-empty" style="opacity:.5">Loading…</p>';
    try {
      const rootParam = state.currentRoot ? `?root=${encodeURIComponent(state.currentRoot)}` : "";
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

  // ── Operation progress DOM refs ──────────────────────────────
  const operationProgress = document.getElementById("operation-progress");
  const progressBar       = document.getElementById("progress-bar");
  const progressPct       = document.getElementById("progress-pct");
  const progressStage     = document.getElementById("progress-stage");
  const taskIdDisplay     = document.getElementById("task-id-display");
  const logOutput         = document.getElementById("log-output");
  const newJobBtn         = document.getElementById("new-job-btn");

  // ── Show operation progress (inline in actions card) ────────
  export function showProgress(taskId) {
    operationProgress.classList.remove("hidden");
    operationProgress.classList.remove("state-success", "state-fail");
    taskIdDisplay.textContent = taskId.slice(0, 12) + "…";
    progressBar.style.width = "0%";
    progressPct.textContent = "0 %";
    progressStage.textContent = "Queued";
    logOutput.textContent = "Waiting for output…";
    newJobBtn.classList.add("hidden");

    state.pollTimer = setInterval(() => pollStatus(taskId), 2000);
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
        clearInterval(state.pollTimer);
        operationProgress.classList.add("state-success");
        progressStage.textContent = "✓ Complete";
        progressBar.style.width = "100%";
        progressPct.textContent = "100 %";
        if (data.result && data.result.log) {
          logOutput.textContent = data.result.log;
        }
        newJobBtn.classList.remove("hidden");
        if (state.currentProjectId) browseProject(state.currentProjectId);
      }

      if (data.state === "FAILURE") {
        clearInterval(state.pollTimer);
        operationProgress.classList.add("state-fail");
        progressStage.textContent = "✗ " + (data.error || "Failed");
        logOutput.textContent = data.error || "An unknown error occurred.";
        newJobBtn.classList.remove("hidden");
        if (state.currentProjectId) browseProject(state.currentProjectId);
      }

    } catch (err) {
      console.error("Polling error:", err);
    }
  }
