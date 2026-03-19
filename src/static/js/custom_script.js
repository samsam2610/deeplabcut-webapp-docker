"use strict";
import { state } from './state.js';
import { folderSelect } from './anipose.js';

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
      return state.userDataDir || state.dataDir || "/";
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
