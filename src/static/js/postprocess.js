/* Post-Process Predictions card controller. */
import { state } from "./state.js";
import { makeFileBrowser } from "./components/file_browser.js";

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

  // ── File browser (canonical file-browser component) ─────────
  // Filter accepts analyzable predictions: .h5 or .csv, excluding *_filtered.*
  // Folders are usable too — the component's single-click on a directory
  // writes that path to ppInputPath, which is exactly the prior
  // "Use this folder" semantics. Selecting an individual .h5/.csv on
  // dblclick sets the input to that file path.
  const _ppPredExts = new Set([".h5", ".csv"]);
  const ppPicker = makeFileBrowser({
    inputEl: ppInputPath,
    paneEl:  ppBrowser,
    fileFilter: (name) => {
      const lower = name.toLowerCase();
      if (lower.includes("_filtered")) return false;
      const dot = lower.lastIndexOf(".");
      if (dot < 0) return false;
      return _ppPredExts.has(lower.slice(dot));
    },
  });

  ppBrowseBtn.addEventListener("click", () => {
    const seed = ppInputPath.value.trim();
    const startPath = seed || state.userDataDir || "/";
    ppPicker.openAt(startPath);
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
        row.style.cssText = "display:flex;justify-content:space-between;gap:.4rem;padding:.15rem 0;cursor:pointer";
        row.title = "Click to view full run.json";
        const id = document.createElement("span");
        id.style.fontFamily = "var(--mono)";
        id.textContent = run.run_id || "";
        const tool = document.createElement("span");
        tool.textContent = `${run.tool || ""}/${run.action || ""}`;
        const status = document.createElement("span");
        const s = run.status || "";
        status.textContent = s;
        if (s === "success") status.style.color = "#67c267";
        else if (s === "partial") status.style.color = "#e8b339";
        else if (s === "failed") status.style.color = "#e36464";
        row.appendChild(id); row.appendChild(tool); row.appendChild(status);
        row.addEventListener("click", () => {
          const copy = { ...run };
          delete copy._sidecar;
          renderRunResult({ result: copy });
        });
        ppRecent.appendChild(row);
      });
    } catch (e) { /* silent */ }
  }

  async function runPostprocess() {
    const path = ppInputPath.value.trim();
    if (!path) { ppStatus.textContent = "input path is empty"; return; }

    // Client-side guard: median filter requires odd windowlength ≥ 3.
    // The HTML <input step=2 min=3> doesn't prevent typed even numbers.
    if (ppTool.value === "deeplabcut") {
      const wl = Number(document.getElementById("pp-dlc-windowlength").value);
      if (!Number.isInteger(wl) || wl < 3 || wl % 2 === 0) {
        ppStatus.textContent = `error: windowlength must be odd and ≥ 3 (got ${wl})`;
        return;
      }
    }

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
        renderRunResult(data);
        refreshRecent();
        return;
      }
    } catch (e) { /* keep polling */ }
    pollHandle = setTimeout(pollStatus, 1500);
  }

  // Surface run.json payload after a task lands. Celery reports SUCCESS even
  // when every input failed inside the task; the actual run-status lives in
  // the returned payload. Show a clear pass/partial/fail line plus a per-input
  // failure list, and dump the full JSON into the log panel.
  function renderRunResult(data) {
    const result = data && data.result;
    if (!result || typeof result !== "object") {
      // No payload: fall back to celery state only.
      if (data && data.state === "FAILURE") {
        ppStatus.textContent = "✗ Task crashed (no run.json)";
      }
      return;
    }
    const inputs = Array.isArray(result.inputs) ? result.inputs : [];
    const succeeded = inputs.filter(i => i && i.status === "success");
    const failed    = inputs.filter(i => i && i.status === "failed");
    const status = result.status || "unknown";

    let label;
    if (status === "success") {
      label = `✓ Success — ${succeeded.length}/${inputs.length} file(s) processed`;
    } else if (status === "partial") {
      label = `⚠ Partial — ${succeeded.length} ok, ${failed.length} failed`;
    } else {
      label = `✗ Failed — ${failed.length}/${inputs.length} file(s) failed`;
    }
    ppStatus.textContent = label;

    ppLog.classList.remove("hidden");
    let log = `Run: ${result.run_id || "(no run_id)"}\nStatus: ${status}\n`;
    if (failed.length) {
      log += "\nFailures:\n";
      failed.forEach(f => {
        const name = (f.path || "").split("/").pop();
        log += `  ${name}: ${f.error || "(no error message)"}\n`;
      });
    }
    log += "\nFull run.json:\n" + JSON.stringify(result, null, 2);
    ppLog.textContent = log;
  }

  async function cancelRun() {
    if (!activeTaskId) return;
    try { await fetch(`/dlc/postprocess/cancel/${activeTaskId}`, { method: "POST" }); }
    catch (e) { /* ignore */ }
  }

  ppRunBtn.addEventListener("click", runPostprocess);
  ppCancelBtn.addEventListener("click", cancelRun);
})();
