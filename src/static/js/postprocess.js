/* Post-Process Predictions card controller. */
import { state } from "./state.js";

(function () {
  "use strict";

  const ppCard       = document.getElementById("postprocess-card");
  const ppOpenBtn    = document.getElementById("btn-open-postprocess");
  const ppCloseBtn   = document.getElementById("btn-close-postprocess");
  const ppTool       = document.getElementById("pp-tool");
  const ppDlcBlock   = document.getElementById("pp-params-deeplabcut");
  const ppRefBlock   = document.getElementById("pp-params-refine");
  const ppInputPath  = document.getElementById("pp-input-path");
  const ppBrowseBtn  = document.getElementById("pp-browse-btn");
  const ppBrowser    = document.getElementById("pp-browser");
  const ppRefinePipelineBtn = document.getElementById("pp-refine-mode-pipeline");
  const ppRefineSingleBtn   = document.getElementById("pp-refine-mode-single");
  const ppRefinePipeline    = document.getElementById("pp-refine-pipeline");
  const ppRefineSingle      = document.getElementById("pp-refine-single");
  const ppRunBtn     = document.getElementById("pp-run");
  const ppCancelBtn  = document.getElementById("pp-cancel");
  const ppStatus     = document.getElementById("pp-status");
  const ppLog        = document.getElementById("pp-log");
  const ppRecent     = document.getElementById("pp-recent");

  if (!ppCard || !ppOpenBtn) return;

  // ── Open / close ─────────────────────────────────────────────
  // Cards in this app are independent; opening this one MUST NOT hide any
  // other open card, and closing it MUST NOT touch other cards.
  ppOpenBtn.addEventListener("click", () => {
    ppCard.classList.remove("hidden");
    ppCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
    refreshRecent();
  });
  ppCloseBtn.addEventListener("click", () => {
    ppCard.classList.add("hidden");
    ppBrowser.classList.add("hidden");
  });

  // ── Tool dropdown swap ───────────────────────────────────────
  function syncToolBlocks() {
    const isDLC = ppTool.value === "deeplabcut";
    ppDlcBlock.classList.toggle("hidden", !isDLC);
    ppRefBlock.classList.toggle("hidden", isDLC);
  }
  ppTool.addEventListener("change", syncToolBlocks);
  syncToolBlocks();

  // ── refineDLC mode toggle ────────────────────────────────────
  let refineMode = "pipeline";
  function setRefineMode(mode) {
    refineMode = mode;
    ppRefinePipelineBtn.classList.toggle("active", mode === "pipeline");
    ppRefineSingleBtn.classList.toggle("active", mode === "single");
    ppRefinePipeline.classList.toggle("hidden", mode !== "pipeline");
    ppRefineSingle.classList.toggle("hidden", mode !== "single");
  }
  ppRefinePipelineBtn.addEventListener("click", () => setRefineMode("pipeline"));
  ppRefineSingleBtn.addEventListener("click", () => setRefineMode("single"));

  // ── File browser (mimics annotator.js, isolated state) ──────
  // Filter accepts analyzable predictions: .h5 or .csv, excluding *_filtered.*
  const _ppPredExts = new Set([".h5", ".csv"]);
  function _ppIsAnalyzable(name) {
    const lower = name.toLowerCase();
    if (lower.includes("_filtered")) return false;
    const dot = lower.lastIndexOf(".");
    if (dot < 0) return false;
    return _ppPredExts.has(lower.slice(dot));
  }

  async function _ppBrowseDir(dirPath) {
    ppBrowser.innerHTML = `<span style="font-size:.8rem;color:var(--text-dim)">Loading…</span>`;
    try {
      const res  = await fetch(`/fs/ls?path=${encodeURIComponent(dirPath)}`);
      const data = await res.json();
      if (data.error) { ppBrowser.innerHTML = `<span style="font-size:.78rem;color:var(--text-dim)">${data.error}</span>`; return; }
      ppBrowser.innerHTML = "";

      // Header: path + "Use this folder" + Up
      const header = document.createElement("div");
      header.style.cssText = "display:flex;align-items:center;gap:.4rem;padding:.15rem .2rem .3rem;border-bottom:1px solid var(--border);margin-bottom:.2rem;min-width:0";
      const pathLabel = document.createElement("span");
      pathLabel.style.cssText = "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono);font-size:.7rem;color:var(--text-dim)";
      pathLabel.textContent = data.path;
      pathLabel.title = data.path;
      header.appendChild(pathLabel);

      const useFolderBtn = document.createElement("button");
      useFolderBtn.className = "btn-sm";
      useFolderBtn.style.cssText = "padding:.12rem .45rem;font-size:.7rem;flex-shrink:0";
      useFolderBtn.textContent = "Use this folder";
      useFolderBtn.title = "Batch-process every analyzable file in this folder (and subfolders)";
      useFolderBtn.addEventListener("click", e => {
        e.stopPropagation();
        ppInputPath.value = data.path;
        ppBrowser.classList.add("hidden");
      });
      header.appendChild(useFolderBtn);

      if (data.parent) {
        const upBtn = document.createElement("button");
        upBtn.className = "btn-sm";
        upBtn.style.cssText = "padding:.12rem .45rem;font-size:.7rem;flex-shrink:0";
        upBtn.textContent = "↑ Up";
        upBtn.addEventListener("click", e => { e.stopPropagation(); _ppBrowseDir(data.parent); });
        header.appendChild(upBtn);
      }
      ppBrowser.appendChild(header);

      const visible = data.entries.filter(e => e.type === "dir" || (e.type === "file" && _ppIsAnalyzable(e.name)));
      if (!visible.length) {
        const empty = document.createElement("span");
        empty.style.cssText = "font-size:.75rem;color:var(--text-dim);padding:.25rem;display:block";
        empty.textContent = "(no .h5/.csv files here — use 'Use this folder' to batch-process the directory)";
        ppBrowser.appendChild(empty);
      } else {
        visible.forEach(e => {
          const row = document.createElement("div");
          row.style.cssText = "display:flex;align-items:center;gap:.35rem;padding:.18rem .3rem;border-radius:4px;cursor:pointer;font-size:.77rem";
          const icon = e.type === "dir"
            ? `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="flex-shrink:0;color:var(--text-dim)"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`
            : `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="flex-shrink:0;color:var(--text-dim)"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
          row.innerHTML = `${icon}<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0"></span>`;
          row.querySelector("span").textContent = e.name;
          row.addEventListener("mouseenter", () => { row.style.background = "var(--surface-3,#2a2a2a)"; });
          row.addEventListener("mouseleave", () => { row.style.background = ""; });
          const fullPath = data.path.replace(/\/+$/, "") + "/" + e.name;
          if (e.type === "dir") {
            row.addEventListener("click", () => _ppBrowseDir(fullPath));
          } else {
            row.addEventListener("click", () => {
              ppInputPath.value = fullPath;
              ppBrowser.classList.add("hidden");
            });
          }
          ppBrowser.appendChild(row);
        });
      }
    } catch (err) {
      ppBrowser.innerHTML = `<span style="font-size:.78rem;color:var(--text-dim)">Error: ${err.message}</span>`;
    }
  }

  ppBrowseBtn.addEventListener("click", () => {
    if (ppBrowser.classList.contains("hidden")) {
      ppBrowser.classList.remove("hidden");
      const seed = ppInputPath.value.trim();
      const startPath = seed || state.userDataDir || "/";
      _ppBrowseDir(startPath);
    } else {
      ppBrowser.classList.add("hidden");
    }
  });

  // ── Build params payload ────────────────────────────────────
  function collectParams() {
    if (ppTool.value === "deeplabcut") {
      return {
        filtertype: document.getElementById("pp-dlc-filtertype").value,
        windowlength: Number(document.getElementById("pp-dlc-windowlength").value),
        p_bound: Number(document.getElementById("pp-dlc-pbound").value),
        save_as_csv: document.getElementById("pp-dlc-savecsv").checked,
      };
    }
    if (refineMode === "pipeline") {
      const out = {};
      ppRefinePipeline.querySelectorAll("input[type=checkbox][data-step]").forEach((cb) => {
        const step = cb.dataset.step;
        const cfg = { enabled: cb.checked };
        cb.parentElement.querySelectorAll("[data-param]").forEach((el) => {
          cfg[el.dataset.param] = el.type === "number" ? Number(el.value) : el.value;
        });
        out[step] = cfg;
      });
      return out;
    }
    const single = {};
    ppRefineSingle.querySelectorAll("[data-param]").forEach((el) => {
      single[el.dataset.param] = el.type === "number" ? Number(el.value) : el.value;
    });
    return single;
  }

  function actionForRequest() {
    if (ppTool.value === "deeplabcut") return "filterpredictions";
    if (refineMode === "pipeline") return "pipeline";
    return document.getElementById("pp-refine-single-step").value;
  }

  // Auto-detect: paths ending in .h5 or .csv → file mode; otherwise folder.
  function detectMode(path) {
    const lower = path.toLowerCase();
    return (lower.endsWith(".h5") || lower.endsWith(".csv")) ? "file" : "folder";
  }

  let activeTaskId = null;
  let pollHandle = null;

  async function refreshRecent() {
    try {
      const r = await fetch("/dlc/postprocess/recent");
      const data = await r.json();
      ppRecent.innerHTML = "";
      if (!data.runs || !data.runs.length) {
        ppRecent.innerHTML = '<p class="explorer-empty">No runs yet.</p>';
        return;
      }
      data.runs.forEach((run) => {
        const row = document.createElement("div");
        row.style.cssText = "display:flex;justify-content:space-between;gap:.4rem;padding:.15rem 0";
        const id = document.createElement("span");
        id.style.fontFamily = "var(--mono)";
        id.textContent = run.run_id || "";
        const tool = document.createElement("span");
        tool.textContent = `${run.tool || ""}/${run.action || ""}`;
        const status = document.createElement("span");
        status.textContent = run.status || "";
        row.appendChild(id); row.appendChild(tool); row.appendChild(status);
        ppRecent.appendChild(row);
      });
    } catch (e) { /* silent */ }
  }

  async function runPostprocess() {
    const path = ppInputPath.value.trim();
    if (!path) { ppStatus.textContent = "input path is empty"; return; }
    const mode = detectMode(path);
    ppStatus.textContent = `scanning (${mode})…`;

    let scanRes;
    try {
      const r = await fetch("/dlc/postprocess/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, mode }),
      });
      scanRes = await r.json();
      if (!r.ok) { ppStatus.textContent = "error: " + (scanRes.error || r.status); return; }
    } catch (e) { ppStatus.textContent = "scan failed"; return; }

    if (!scanRes.files || !scanRes.files.length) { ppStatus.textContent = "no analyzable files"; return; }

    ppStatus.textContent = "queued…";
    ppLog.classList.remove("hidden");
    ppLog.textContent = `Found ${scanRes.files.length} file(s).\n`;

    const body = {
      tool: ppTool.value,
      action: actionForRequest(),
      params: collectParams(),
      inputs: scanRes.files,
      config_path: window.__DLC_CONFIG_PATH__ || "",
    };

    try {
      const r = await fetch("/dlc/postprocess/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) { ppStatus.textContent = "error: " + (data.error || r.status); return; }
      activeTaskId = data.task_id;
      ppCancelBtn.classList.remove("hidden");
      pollStatus();
    } catch (e) { ppStatus.textContent = "dispatch failed"; }
  }

  async function pollStatus() {
    if (!activeTaskId) return;
    try {
      const r = await fetch(`/dlc/postprocess/status/${activeTaskId}`);
      const data = await r.json();
      const p = data.progress || {};
      ppStatus.textContent = `${data.state} ${p.current ?? ""}/${p.total ?? ""} ${p.file ?? ""}`;
      if (data.state === "SUCCESS" || data.state === "FAILURE" || data.state === "REVOKED") {
        activeTaskId = null;
        ppCancelBtn.classList.add("hidden");
        refreshRecent();
        return;
      }
    } catch (e) { /* keep polling */ }
    pollHandle = setTimeout(pollStatus, 1500);
  }

  async function cancelRun() {
    if (!activeTaskId) return;
    try { await fetch(`/dlc/postprocess/cancel/${activeTaskId}`, { method: "POST" }); }
    catch (e) { /* ignore */ }
  }

  ppRunBtn.addEventListener("click", runPostprocess);
  ppCancelBtn.addEventListener("click", cancelRun);
})();
