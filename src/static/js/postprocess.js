/* Post-Process Predictions card controller. */
(function () {
  "use strict";

  const ppCard       = document.getElementById("postprocess-card");
  const ppOpenBtn    = document.getElementById("btn-open-postprocess");
  const ppCloseBtn   = document.getElementById("btn-close-postprocess");
  const ppTool       = document.getElementById("pp-tool");
  const ppDlcBlock   = document.getElementById("pp-params-deeplabcut");
  const ppRefBlock   = document.getElementById("pp-params-refine");
  const ppModeFile   = document.getElementById("pp-input-mode-file");
  const ppModeFolder = document.getElementById("pp-input-mode-folder");
  const ppInputPath  = document.getElementById("pp-input-path");
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

  function hideAllOtherCards() {
    document.querySelectorAll("section.card").forEach((c) => {
      if (c !== ppCard) c.classList.add("hidden");
    });
  }
  function openCard() {
    hideAllOtherCards();
    ppCard.classList.remove("hidden");
    refreshRecent();
  }
  function closeCard() { ppCard.classList.add("hidden"); }

  ppOpenBtn.addEventListener("click", openCard);
  if (ppCloseBtn) ppCloseBtn.addEventListener("click", closeCard);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !ppCard.classList.contains("hidden")) closeCard();
  });

  // Tool swap.
  function syncToolBlocks() {
    if (!ppTool || !ppDlcBlock || !ppRefBlock) return;
    const isDLC = ppTool.value === "deeplabcut";
    ppDlcBlock.classList.toggle("hidden", !isDLC);
    ppRefBlock.classList.toggle("hidden", isDLC);
  }
  if (ppTool) ppTool.addEventListener("change", syncToolBlocks);
  syncToolBlocks();

  // Input mode toggle.
  let inputMode = "file";
  function setInputMode(mode) {
    inputMode = mode;
    if (ppModeFile)   ppModeFile.classList.toggle("active", mode === "file");
    if (ppModeFolder) ppModeFolder.classList.toggle("active", mode === "folder");
    if (ppInputPath) {
      ppInputPath.placeholder = mode === "file"
        ? "/paste/an/analyzed/.h5" : "/paste/a/folder/of/analyzed/files";
    }
  }
  if (ppModeFile)   ppModeFile.addEventListener("click", () => setInputMode("file"));
  if (ppModeFolder) ppModeFolder.addEventListener("click", () => setInputMode("folder"));

  // refineDLC mode toggle.
  let refineMode = "pipeline";
  function setRefineMode(mode) {
    refineMode = mode;
    if (ppRefinePipelineBtn) ppRefinePipelineBtn.classList.toggle("active", mode === "pipeline");
    if (ppRefineSingleBtn)   ppRefineSingleBtn.classList.toggle("active", mode === "single");
    if (ppRefinePipeline)    ppRefinePipeline.classList.toggle("hidden", mode !== "pipeline");
    if (ppRefineSingle)      ppRefineSingle.classList.toggle("hidden", mode !== "single");
  }
  if (ppRefinePipelineBtn) ppRefinePipelineBtn.addEventListener("click", () => setRefineMode("pipeline"));
  if (ppRefineSingleBtn)   ppRefineSingleBtn.addEventListener("click", () => setRefineMode("single"));

  // Build params payload.
  function collectParams() {
    if (ppTool && ppTool.value === "deeplabcut") {
      return {
        filtertype: (document.getElementById("pp-dlc-filtertype") || {}).value,
        windowlength: Number((document.getElementById("pp-dlc-windowlength") || {}).value),
        p_bound: Number((document.getElementById("pp-dlc-pbound") || {}).value),
        save_as_csv: !!(document.getElementById("pp-dlc-savecsv") || {}).checked,
      };
    }
    if (refineMode === "pipeline") {
      const out = {};
      if (ppRefinePipeline) {
        ppRefinePipeline.querySelectorAll("input[type=checkbox][data-step]").forEach((cb) => {
          const step = cb.dataset.step;
          const cfg = { enabled: cb.checked };
          cb.parentElement.querySelectorAll("[data-param]").forEach((el) => {
            cfg[el.dataset.param] = el.type === "number" ? Number(el.value) : el.value;
          });
          out[step] = cfg;
        });
      }
      return out;
    }
    const single = {};
    if (ppRefineSingle) {
      ppRefineSingle.querySelectorAll("[data-param]").forEach((el) => {
        single[el.dataset.param] = el.type === "number" ? Number(el.value) : el.value;
      });
    }
    return single;
  }

  function actionForRequest() {
    if (ppTool && ppTool.value === "deeplabcut") return "filterpredictions";
    if (refineMode === "pipeline") return "pipeline";
    const sel = document.getElementById("pp-refine-single-step");
    return sel ? sel.value : "pipeline";
  }

  let activeTaskId = null;
  let pollHandle = null;

  async function refreshRecent() {
    if (!ppRecent) return;
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
        row.style.display = "flex";
        row.style.justifyContent = "space-between";
        row.style.gap = ".4rem";
        row.style.padding = ".15rem 0";
        row.innerHTML = `<span style="font-family:var(--mono)">${run.run_id}</span><span>${run.tool}/${run.action}</span><span>${run.status}</span>`;
        ppRecent.appendChild(row);
      });
    } catch (e) { /* silent */ }
  }

  async function runPostprocess() {
    if (!ppInputPath || !ppStatus) return;
    const path = ppInputPath.value.trim();
    if (!path) { ppStatus.textContent = "input path is empty"; return; }
    ppStatus.textContent = "scanning…";

    let scanRes;
    try {
      const r = await fetch("/dlc/postprocess/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, mode: inputMode }),
      });
      scanRes = await r.json();
      if (!r.ok) { ppStatus.textContent = "error: " + (scanRes.error || r.status); return; }
    } catch (e) { ppStatus.textContent = "scan failed"; return; }

    if (!scanRes.files || !scanRes.files.length) { ppStatus.textContent = "no analyzable files"; return; }

    ppStatus.textContent = "queued…";
    if (ppLog) {
      ppLog.classList.remove("hidden");
      ppLog.textContent = `Found ${scanRes.files.length} file(s).\n`;
    }

    const body = {
      tool: ppTool ? ppTool.value : "deeplabcut",
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
      if (ppCancelBtn) ppCancelBtn.classList.remove("hidden");
      pollStatus();
    } catch (e) { ppStatus.textContent = "dispatch failed"; }
  }

  async function pollStatus() {
    if (!activeTaskId) return;
    try {
      const r = await fetch(`/dlc/postprocess/status/${activeTaskId}`);
      const data = await r.json();
      const p = data.progress || {};
      if (ppStatus) {
        ppStatus.textContent = `${data.state} ${p.current ?? ""}/${p.total ?? ""} ${p.file ?? ""}`;
      }
      if (data.state === "SUCCESS" || data.state === "FAILURE" || data.state === "REVOKED") {
        activeTaskId = null;
        if (ppCancelBtn) ppCancelBtn.classList.add("hidden");
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

  if (ppRunBtn)    ppRunBtn.addEventListener("click", runPostprocess);
  if (ppCancelBtn) ppCancelBtn.addEventListener("click", cancelRun);
})();
