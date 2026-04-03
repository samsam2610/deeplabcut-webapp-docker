"use strict";
import { state } from './state.js';

  // ── Frame Extractor ──────────────────────────────────────────
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
    const feServerSec        = document.getElementById("fe-server-section");
    const feServerBreadcrumb = document.getElementById("fe-server-breadcrumb");
    const feServerUp         = document.getElementById("fe-server-up");
    const feServerBrowser    = document.getElementById("fe-server-browser");
    const feServerStatus     = document.getElementById("fe-server-status");
    const fePlayerSec     = document.getElementById("fe-player-section");
    const feCanvas        = document.getElementById("fe-canvas");
    const feBtnPlay       = document.getElementById("fe-btn-play");
    const fePlayIcon      = document.getElementById("fe-play-icon");
    const fePauseIcon     = document.getElementById("fe-pause-icon");
    const feBtnPrev       = document.getElementById("fe-btn-prev");
    const feBtnNext       = document.getElementById("fe-btn-next");
    const feBtnSkipBack   = document.getElementById("fe-btn-skip-back");
    const feBtnSkipFwd    = document.getElementById("fe-btn-skip-fwd");
    const feSkipN         = document.getElementById("fe-skip-n");
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
    let _feBrowsePath   = null;    // current directory in the server browser
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
      const startPath = state.userDataDir || "/";
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
      _feBrowsePath = dirPath;
      if (feServerBreadcrumb) feServerBreadcrumb.value = dirPath;
      feServerBrowser.innerHTML = `<span style="font-size:.8rem;color:var(--text-dim)">Loading…</span>`;
      try {
        const res  = await fetch(`/fs/ls?path=${encodeURIComponent(dirPath)}`);
        const data = await res.json();
        if (data.error) {
          feServerBrowser.innerHTML = `<span style="font-size:.78rem;color:var(--text-dim)">${data.error}</span>`;
          return;
        }
        feServerBrowser.innerHTML = "";

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

    feServerUp?.addEventListener("click", () => {
      if (!_feBrowsePath) return;
      const parent = _feBrowsePath.split("/").slice(0, -1).join("/") || "/";
      if (parent !== _feBrowsePath) _feBrowseServerDir(parent);
    });

    feServerBreadcrumb?.addEventListener("keydown", e => {
      if (e.key === "Enter")  { e.preventDefault(); _feBrowseServerDir(feServerBreadcrumb.value.trim()); }
      if (e.key === "Escape") { feServerBreadcrumb.value = _feBrowsePath || ""; feServerBreadcrumb.blur(); }
    });
    feServerBreadcrumb?.addEventListener("paste", e => {
      setTimeout(() => _feBrowseServerDir(feServerBreadcrumb.value.trim()), 0);
    });

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
    const _feSkipN = () => Math.max(1, parseInt(feSkipN?.value || "10", 10));
    if (feBtnSkipBack) feBtnSkipBack.addEventListener("click", () => _feLoadFrame(_feCurrentFrame - _feSkipN()));
    if (feBtnSkipFwd)  feBtnSkipFwd.addEventListener("click",  () => _feLoadFrame(_feCurrentFrame + _feSkipN()));

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
      if (e.ctrlKey && e.key === "ArrowLeft")  { e.preventDefault(); _feLoadFrame(_feCurrentFrame - _feSkipN()); return; }
      if (e.ctrlKey && e.key === "ArrowRight") { e.preventDefault(); _feLoadFrame(_feCurrentFrame + _feSkipN()); return; }
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
