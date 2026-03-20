"use strict";
import { state } from './state.js';

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
    const vaBtnSkipBack  = document.getElementById("va-btn-skip-back");
    const vaBtnSkipFwd   = document.getElementById("va-btn-skip-fwd");
    const vaSkipN        = document.getElementById("va-skip-n");
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
    // absolute path to the currently loaded original video (for annotated frames + companion CSV)
    let _vaCurrentVideoPath = null;
    // Hook called by _vaLoadFrame so the nested curation IIFE can sync its annotation panel
    let _vaCurationFrameHook = null;
    let _vaMetadataFrameHook = null;

    // ── Pose cache (prefetch window) ───────────────────────────────────────
    const _POSE_WINDOW  = 30;
    const _vaPoseCache  = new Map();  // frameNumber → {key, poses, n_bodyparts}
    let   _vaPrefetchCtrl = null;     // AbortController for in-flight batch prefetch

    function _vaClearPoseCache() {
      _vaPoseCache.clear();
      if (_vaPrefetchCtrl) { _vaPrefetchCtrl.abort(); _vaPrefetchCtrl = null; }
    }

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
      _vaDragBp       = null;
      _vaDragging     = false;
      if (typeof _vaLocalEdits !== "undefined") _vaLocalEdits.clear();
      _vaUpdateEditBanner();
      _vaClearPoseCache();
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
      if (_vaMode === "browse-video") {
        // Use the same cached VideoCapture endpoint as Frame Extractor
        return `/dlc/project/video-frame-ext/${n}?path=${encodeURIComponent(_vaBrowseVideoPath)}`;
      }
      if (_vaMode === "video") {
        return `/dlc/project/video-frame/${encodeURIComponent(_vaVideoName)}/${n}`;
      }
      // frames mode: index into _vaFrameFiles
      return `/dlc/project/frame-image/${encodeURIComponent(_vaFrameStem)}/${encodeURIComponent(_vaFrameFiles[n])}`;
    }

    function _vaPrefetchFrames(frames) {
      frames.forEach(n => {
        if (n >= 0 && n < _vaFrameCount) new Image().src = _vaFrameUrl(n);
      });
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
        _vaPrefetchFrames([n + 1, n + 2]);
        if (_vaCurationFrameHook) _vaCurationFrameHook(n);
        if (_vaMetadataFrameHook) _vaMetadataFrameHook(n);
        _vaUpdateOverlay(n);
      } catch (err) {
        vaStatus.textContent = `Failed to load frame: ${err.message}`;
        vaStatus.className   = "fe-extract-status err";
      } finally {
        _vaFrameBusy = false;
        vaFrameSpinner.classList.add("hidden");
      }
    }

    // Draw overlay for frame n: show cached poses immediately; fetch from server only when paused.
    function _vaUpdateOverlay(n) {
      if (!vaOverlayCtx || !_vaOverlayEnabled) return;
      _vaSyncCanvas();
      vaOverlayCtx.clearRect(0, 0, vaOverlayCanvas.width, vaOverlayCanvas.height);
      if (!_vaH5Path) return;
      const key    = _vaPoseCacheKey();
      const cached = _vaPoseCache.get(n);
      if (cached && cached.key === key) {
        _vaCurrentPoses = cached.poses;
        _vaNBodyparts   = cached.n_bodyparts;
        _vaDrawPoseMarkers();
      }
      // Only hit the server when paused
      if (!_vaPlayTimer && (!cached || cached.key !== key)) _vaFetchPoses(n);
    }

    async function _vaOpenVideo(name) {
      _vaReset();
      _vaMode      = "video";
      _vaVideoName = name;
      vaSelectedName.textContent = name;
      try {
        const res  = await fetch(`/dlc/project/video-info/${encodeURIComponent(name)}`);
        const info = await res.json();
        _vaFps             = info.fps || 30;
        _vaFrameCount      = info.frame_count || 0;
        _vaCurrentVideoPath = info.abs_path || null;
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
      vaBrowseBreadcrumb.value = path;
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
        const startPath = state.userDataDir || state.dataDir || "/";
        _vaRefreshBrowse(startPath);
      }
    });

    vaBrowseUp?.addEventListener("click", () => {
      if (!_vaBrowsePath) return;
      const parent = _vaBrowsePath.split("/").slice(0, -1).join("/") || "/";
      if (parent !== _vaBrowsePath) _vaRefreshBrowse(parent);
    });

    // ── Editable address bar ───────────────────────────────────
    async function _vaNavigateTo(raw) {
      const p = raw.trim();
      if (!p) return;
      // Check if the path looks like a video file
      const ext = p.slice(p.lastIndexOf(".")).toLowerCase();
      if (_VA_VIDEO_EXTS.has(ext)) {
        // Navigate the browser to the parent folder first, then open the video
        const dir  = p.substring(0, p.lastIndexOf("/")) || "/";
        const name = p.substring(p.lastIndexOf("/") + 1);
        await _vaRefreshBrowse(dir);
        _vaOpenBrowseVideo(p, name);
      } else {
        _vaRefreshBrowse(p);
      }
    }

    vaBrowseBreadcrumb?.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); _vaNavigateTo(vaBrowseBreadcrumb.value); }
      if (e.key === "Escape") { vaBrowseBreadcrumb.value = _vaBrowsePath || ""; vaBrowseBreadcrumb.blur(); }
    });
    // Also handle paste: navigate immediately after the clipboard text lands
    vaBrowseBreadcrumb?.addEventListener("paste", e => {
      // Let the paste complete, then navigate
      setTimeout(() => _vaNavigateTo(vaBrowseBreadcrumb.value), 0);
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

    // Draw all pose marker circles onto the overlay canvas.
    // If _vaLocalEdits contains an override for the current frame+bodypart,
    // the edited position is used and the marker is drawn with an extra ring.
    function _vaDrawPoseMarkers() {
      if (!vaOverlayCtx || !_vaOverlayEnabled || !_vaCurrentPoses.length) return;
      const natW = vaFrameImg.naturalWidth  || 1;
      const natH = vaFrameImg.naturalHeight || 1;
      const sx   = vaOverlayCanvas.width  / natW;
      const sy   = vaOverlayCanvas.height / natH;
      const r    = Math.max(1, Math.round(_vaMarkerSize * Math.min(sx, sy)));
      // _vaLocalEdits may not exist yet (declared later in the module); guard with typeof
      const frameEdits = (typeof _vaLocalEdits !== "undefined")
        ? (_vaLocalEdits.get(_vaCurrentFrame) || {})
        : {};
      for (const pose of _vaCurrentPoses) {
        const edited = pose.bp in frameEdits;
        const cx = Math.round((edited ? frameEdits[pose.bp].x : pose.x) * sx);
        const cy = Math.round((edited ? frameEdits[pose.bp].y : pose.y) * sy);
        const color = _vaPaletteColor(pose.color_idx, _vaNBodyparts);
        vaOverlayCtx.beginPath();
        vaOverlayCtx.arc(cx, cy, r, 0, Math.PI * 2);
        vaOverlayCtx.fillStyle = color;
        vaOverlayCtx.fill();
        if (edited) {
          // White ring indicates an unsaved edit
          vaOverlayCtx.beginPath();
          vaOverlayCtx.arc(cx, cy, r + 3, 0, Math.PI * 2);
          vaOverlayCtx.strokeStyle = "#fff";
          vaOverlayCtx.lineWidth   = 1.5;
          vaOverlayCtx.stroke();
        }
      }
    }

    function _vaDrawHoverLabel() {
      if (!vaOverlayCtx) return;
      _vaSyncCanvas();
      vaOverlayCtx.clearRect(0, 0, vaOverlayCanvas.width, vaOverlayCanvas.height);
      _vaDrawPoseMarkers();
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

    // ── Marker drag-and-edit state ────────────────────────────
    // _vaLocalEdits: Map<string, Map<bp, {x, y}>> — client-side overrides
    // keyed by frame number.  Mirrors the server-side JSON cache and provides
    // zero-latency feedback while dragging.
    const _vaLocalEdits = new Map();  // frameNumber (int) → {bp: {x, y}}

    let _vaDragBp      = null;   // body-part being dragged
    let _vaDragging    = false;

    // Marker-edit UI elements (may be null if not yet in DOM)
    const vaMarkerEditBanner  = document.getElementById("va-marker-edit-banner");
    const vaMarkerEditCount   = document.getElementById("va-marker-edit-count");
    const vaSaveAdjBtn        = document.getElementById("va-save-adjustments-btn");
    const vaDiscardAdjBtn     = document.getElementById("va-discard-adjustments-btn");

    function _vaEditCount() {
      return _vaLocalEdits.size;
    }

    function _vaUpdateEditBanner() {
      if (!vaMarkerEditBanner) return;
      const n = _vaEditCount();
      if (n === 0) {
        vaMarkerEditBanner.classList.add("hidden");
      } else {
        vaMarkerEditBanner.classList.remove("hidden");
        if (vaMarkerEditCount) vaMarkerEditCount.textContent = `${n} frame${n !== 1 ? "s" : ""} edited`;
      }
    }

    // Convert canvas-display coords back to video-native coords
    function _vaCanvasToVideo(cx, cy) {
      const natW = vaFrameImg.naturalWidth  || 1;
      const natH = vaFrameImg.naturalHeight || 1;
      const sx   = vaOverlayCanvas.width  / natW;
      const sy   = vaOverlayCanvas.height / natH;
      return { x: cx / sx, y: cy / sy };
    }

    // Hit-test that accounts for local-edit positions
    function _vaHitTestWithEdits(cx, cy) {
      if (!_vaCurrentPoses.length) return null;
      const natW  = vaFrameImg.naturalWidth  || 1;
      const natH  = vaFrameImg.naturalHeight || 1;
      const sx    = vaOverlayCanvas.width  / natW;
      const sy    = vaOverlayCanvas.height / natH;
      const hitR  = (_vaMarkerSize + 8) * Math.max(sx, sy);
      const frameEdits = _vaLocalEdits.get(_vaCurrentFrame) || {};

      for (const pose of _vaCurrentPoses) {
        const edited = pose.bp in frameEdits;
        const px = edited ? frameEdits[pose.bp].x * sx : pose.x * sx;
        const py = edited ? frameEdits[pose.bp].y * sy : pose.y * sy;
        const dx = px - cx;
        const dy = py - cy;
        if (Math.sqrt(dx * dx + dy * dy) <= hitR) return pose.bp;
      }
      return null;
    }

    // Flush a single marker edit to the server (fire-and-forget)
    async function _vaFlushMarkerEdit(frame, bp, x, y) {
      if (!_vaH5Path) return;
      try {
        await fetch("/dlc/viewer/marker-edit", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ h5: _vaH5Path, frame, bp, x, y }),
        });
      } catch (_) { /* non-critical; edit lives in local state */ }
    }

    // Sync local edits from the server's edit-cache on H5 load / page refresh
    async function _vaLoadEditCacheFromServer(h5Path) {
      try {
        const res  = await fetch(`/dlc/viewer/edit-cache?h5=${encodeURIComponent(h5Path)}`);
        if (!res.ok) return;
        const data = await res.json();
        _vaLocalEdits.clear();
        for (const [frameKey, bpEdits] of Object.entries(data.cache || {})) {
          const fn = parseInt(frameKey.split("_")[1], 10);
          if (!isNaN(fn)) _vaLocalEdits.set(fn, bpEdits);
        }
        _vaUpdateEditBanner();
      } catch (_) {}
    }

    if (vaOverlayCanvas) {
      // Enable pointer events on the canvas for hover + drag
      vaOverlayCanvas.style.pointerEvents = "auto";
      vaOverlayCanvas.style.cursor        = "default";

      vaOverlayCanvas.addEventListener("mousedown", e => {
        if (!_vaOverlayEnabled || !_vaCurrentPoses.length) return;
        const rect = vaOverlayCanvas.getBoundingClientRect();
        const hit  = _vaHitTestWithEdits(e.clientX - rect.left, e.clientY - rect.top);
        if (!hit) return;
        e.preventDefault();
        _vaDragBp   = hit;
        _vaDragging = true;
        vaOverlayCanvas.style.cursor = "grabbing";
      });

      vaOverlayCanvas.addEventListener("mousemove", e => {
        if (!_vaOverlayEnabled) return;
        const rect = vaOverlayCanvas.getBoundingClientRect();
        const cx   = e.clientX - rect.left;
        const cy   = e.clientY - rect.top;

        if (_vaDragging && _vaDragBp) {
          // Update local edit position in real-time (no server round-trip during drag)
          const { x, y } = _vaCanvasToVideo(cx, cy);
          if (!_vaLocalEdits.has(_vaCurrentFrame)) _vaLocalEdits.set(_vaCurrentFrame, {});
          _vaLocalEdits.get(_vaCurrentFrame)[_vaDragBp] = { x, y };
          // Redraw canvas immediately for zero-latency feedback
          _vaSyncCanvas();
          vaOverlayCtx.clearRect(0, 0, vaOverlayCanvas.width, vaOverlayCanvas.height);
          _vaDrawPoseMarkers();
          return;
        }

        // Hover detection (only when not dragging)
        if (!_vaCurrentPoses.length) return;
        const hit = _vaHitTestWithEdits(cx, cy);
        if (hit !== _vaHoverBp) {
          _vaHoverBp = hit;
          _vaDrawHoverLabel();
        }
        vaOverlayCanvas.style.cursor = hit ? "grab" : "default";
      });

      vaOverlayCanvas.addEventListener("mouseup", async e => {
        if (!_vaDragging || !_vaDragBp) return;
        _vaDragging = false;
        const rect  = vaOverlayCanvas.getBoundingClientRect();
        const { x, y } = _vaCanvasToVideo(
          e.clientX - rect.left,
          e.clientY - rect.top,
        );
        // Final position already in _vaLocalEdits; flush to server
        await _vaFlushMarkerEdit(_vaCurrentFrame, _vaDragBp, x, y);
        _vaUpdateEditBanner();
        _vaDragBp = null;
        vaOverlayCanvas.style.cursor = "default";
        _vaDrawHoverLabel();
      });

      // Cancel drag if mouse leaves the canvas
      vaOverlayCanvas.addEventListener("mouseleave", () => {
        if (_vaDragging && _vaDragBp) {
          // Persist whatever position was last recorded
          const edits = _vaLocalEdits.get(_vaCurrentFrame);
          if (edits && _vaDragBp in edits) {
            const { x, y } = edits[_vaDragBp];
            _vaFlushMarkerEdit(_vaCurrentFrame, _vaDragBp, x, y);
            _vaUpdateEditBanner();
          }
          _vaDragging = false;
          _vaDragBp   = null;
        }
        if (_vaHoverBp) { _vaHoverBp = null; _vaDrawHoverLabel(); }
        vaOverlayCanvas.style.cursor = "default";
      });
    }

    // Save Adjustments button
    if (vaSaveAdjBtn) {
      vaSaveAdjBtn.addEventListener("click", async () => {
        if (!_vaH5Path) return;
        vaSaveAdjBtn.disabled = true;
        vaSaveAdjBtn.textContent = "Saving…";
        try {
          const res  = await fetch("/dlc/viewer/save-marker-edits", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ h5: _vaH5Path }),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
          _vaLocalEdits.clear();
          _vaClearPoseCache();
          _vaUpdateEditBanner();
          vaStatus.textContent = `Saved: ${data.frames_edited} frame(s), ${data.bodyparts_edited} keypoint(s) updated.`;
          vaStatus.className   = "fe-extract-status ok";
          // Reload poses for current frame from updated H5
          if (_vaOverlayEnabled) await _vaFetchPoses(_vaCurrentFrame);
        } catch (err) {
          vaStatus.textContent = `Save failed: ${err.message}`;
          vaStatus.className   = "fe-extract-status err";
        } finally {
          vaSaveAdjBtn.disabled    = false;
          vaSaveAdjBtn.innerHTML   =
            '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" style="margin-right:.3rem"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>Save Adjustments';
        }
      });
    }

    // Discard Adjustments button
    if (vaDiscardAdjBtn) {
      vaDiscardAdjBtn.addEventListener("click", async () => {
        if (!_vaH5Path) return;
        _vaLocalEdits.clear();
        _vaClearPoseCache();
        _vaUpdateEditBanner();
        // Delete server-side cache too
        try {
          await fetch("/dlc/viewer/save-marker-edits", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            // Send empty cache by patching via a discard endpoint alias.
            // Since the route applies the *current* server cache, we need to
            // clear it first using marker-edit with a sentinel, or simply call
            // save on an empty cache.  The simplest approach: reload poses and
            // the banner will disappear.
          });
        } catch (_) {}
        // Reload current frame poses from H5
        if (_vaOverlayEnabled && _vaH5Path) {
          _vaSyncCanvas();
          if (vaOverlayCtx) vaOverlayCtx.clearRect(0, 0, vaOverlayCanvas.width, vaOverlayCanvas.height);
          _vaFetchPoses(_vaCurrentFrame);
        }
      });
    }

    // Pose cache key encodes everything that affects pose data
    function _vaPoseCacheKey() {
      return `${_vaH5Path}:${_vaThreshold.toFixed(2)}:${[..._vaSelectedParts].sort().join(",")}`;
    }

    // Fetch poses for frameNumber from cache or server; draw overlay; start background prefetch.
    // Only called when paused — never during playback.
    async function _vaFetchPoses(frameNumber) {
      if (!_vaH5Path || !_vaOverlayEnabled) return;
      const key    = _vaPoseCacheKey();
      const cached = _vaPoseCache.get(frameNumber);
      if (cached && cached.key === key) {
        _vaCurrentPoses = cached.poses;
        _vaNBodyparts   = cached.n_bodyparts;
      } else {
        const parts = _vaSelectedParts.size > 0 ? [..._vaSelectedParts].join(",") : "";
        const p     = new URLSearchParams({ h5: _vaH5Path, threshold: _vaThreshold.toFixed(2) });
        if (parts) p.set("parts", parts);
        try {
          const res  = await fetch(`/dlc/viewer/frame-poses/${frameNumber}?${p}`);
          const data = await res.json();
          _vaCurrentPoses = data.poses || [];
          _vaNBodyparts   = data.n_bodyparts || 1;
          _vaPoseCache.set(frameNumber, { key, poses: _vaCurrentPoses, n_bodyparts: _vaNBodyparts });
        } catch (_) { _vaCurrentPoses = []; }
      }
      _vaHoverBp = null;
      _vaDrawHoverLabel();
      if (!_vaPrefetchCtrl) _vaFetchPosesWindow(frameNumber);
    }

    // Prefetch the next _POSE_WINDOW frames in the background (runs only when paused).
    async function _vaFetchPosesWindow(fromFrame) {
      if (!_vaH5Path) return;
      const key = _vaPoseCacheKey();
      let missing = 0;
      for (let i = fromFrame; i < fromFrame + _POSE_WINDOW && i < _vaFrameCount; i++) {
        const c = _vaPoseCache.get(i);
        if (!c || c.key !== key) missing++;
      }
      if (missing === 0) return;
      if (_vaPrefetchCtrl) return;  // let the current batch finish
      _vaPrefetchCtrl = new AbortController();
      const ctrl  = _vaPrefetchCtrl;
      const parts = _vaSelectedParts.size > 0 ? [..._vaSelectedParts].join(",") : "";
      const p = new URLSearchParams({
        h5: _vaH5Path, start: fromFrame, count: _POSE_WINDOW, threshold: _vaThreshold.toFixed(2),
      });
      if (parts) p.set("parts", parts);
      try {
        const res  = await fetch(`/dlc/viewer/frame-poses-batch?${p}`, { signal: ctrl.signal });
        if (!res.ok) return;
        const data = await res.json();
        for (const [fnStr, fd] of Object.entries(data.frames || {})) {
          const fn = parseInt(fnStr, 10);
          _vaPoseCache.set(fn, { key, poses: fd.poses || [], n_bodyparts: fd.n_bodyparts || 1 });
        }
      } catch (e) {
        if (e.name !== "AbortError") console.warn("pose prefetch failed:", e);
      } finally {
        if (_vaPrefetchCtrl === ctrl) _vaPrefetchCtrl = null;
      }
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
          _vaClearPoseCache();
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
        _vaClearPoseCache();
        _vaOverlayStatus("h5 auto-detected");
        await _vaLoadH5Info(_vaH5Path);
        // Load any pending edits from the server-side JSON cache
        await _vaLoadEditCacheFromServer(_vaH5Path);
      } catch (e) {
        _vaOverlayStatus("Auto-detect failed: " + e.message);
      }
    }

    vaOverlayToggle?.addEventListener("change", () => {
      _vaOverlayEnabled = vaOverlayToggle.checked;
      vaOverlayControls.classList.toggle("hidden", !_vaOverlayEnabled);
      if (!_vaOverlayEnabled) {
        if (vaOverlayCtx) vaOverlayCtx.clearRect(0, 0, vaOverlayCanvas.width, vaOverlayCanvas.height);
        return;
      }
      if (!_vaH5Path && _vaCurrentVideoPath) _vaAutoDetectH5(_vaCurrentVideoPath);
      if (_vaH5Path && !_vaPlayTimer) _vaFetchPoses(_vaCurrentFrame);
    });

    vaOverlayH5Auto?.addEventListener("click", () => {
      if (_vaCurrentVideoPath) _vaAutoDetectH5(_vaCurrentVideoPath);
    });

    vaOverlayH5Clear?.addEventListener("click", () => {
      _vaH5Path = null;
      vaOverlayH5Path.value = "";
      _vaAllBodyParts = [];
      _vaSelectedParts.clear();
      _vaClearPoseCache();
      vaOverlayPartsBox.innerHTML = '<span style="color:var(--text-dim);font-size:.73rem">Load an .h5 file to see body parts.</span>';
      _vaOverlayStatus("");
      _vaCurrentPoses = [];
      if (vaOverlayCtx) vaOverlayCtx.clearRect(0, 0, vaOverlayCanvas.width, vaOverlayCanvas.height);
    });

    // Threshold slider
    vaOverlayThreshold?.addEventListener("input", () => {
      _vaThreshold = parseFloat(vaOverlayThreshold.value);
      vaOverlayThreshVal.textContent = _vaThreshold.toFixed(2);
    });
    vaOverlayThreshold?.addEventListener("change", () => {
      _vaClearPoseCache();
      if (_vaOverlayEnabled && _vaH5Path) _vaLoadFrame(_vaCurrentFrame);
    });

    // Marker size slider — redraw canvas immediately, no frame reload needed
    vaOverlayMarkerSz?.addEventListener("input", () => {
      _vaMarkerSize = parseInt(vaOverlayMarkerSz.value, 10);
      vaOverlayMarkerVal.textContent = _vaMarkerSize;
      _vaDrawHoverLabel();
    });

    vaOverlayPartsAll?.addEventListener("click", () => {
      _vaSelectedParts.clear();
      vaOverlayPartsBox.querySelectorAll("input").forEach(c => { c.checked = true; });
      _vaClearPoseCache();
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
              _vaClearPoseCache();
              vaOverlayH5Browser.classList.add("hidden");
              _vaOverlayStatus("h5 selected");
              await _vaLoadH5Info(full);
              await _vaLoadEditCacheFromServer(full);
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
          : (state.userDataDir || state.dataDir || "/");
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
        // Just paused: show poses for current frame
        if (_vaOverlayEnabled && _vaH5Path) _vaFetchPoses(_vaCurrentFrame);
      } else {
        vaPlayIcon.classList.add("hidden"); vaPauseIcon.classList.remove("hidden");
        _vaPlayTimer = setInterval(async () => {
          if (_vaCurrentFrame >= _vaFrameCount - 1) {
            clearInterval(_vaPlayTimer); _vaPlayTimer = null;
            vaPlayIcon.classList.remove("hidden"); vaPauseIcon.classList.add("hidden");
            if (_vaOverlayEnabled && _vaH5Path) _vaFetchPoses(_vaCurrentFrame);
            return;
          }
          await _vaLoadFrame(_vaCurrentFrame + 1);
        }, 1000 / _vaFps);
      }
    });

    vaBtnPrev.addEventListener("click", () => _vaLoadFrame(_vaCurrentFrame - 1));
    vaBtnNext.addEventListener("click", () => _vaLoadFrame(_vaCurrentFrame + 1));

    function _vaSkipN() { return Math.max(1, parseInt(vaSkipN?.value, 10) || 10); }
    vaBtnSkipBack?.addEventListener("click", () => _vaLoadFrame(_vaCurrentFrame - _vaSkipN()));
    vaBtnSkipFwd?.addEventListener("click",  () => _vaLoadFrame(_vaCurrentFrame + _vaSkipN()));
    // Prevent arrow keys from changing the skip-N field from triggering frame nav
    vaSkipN?.addEventListener("keydown", e => e.stopPropagation());

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

    // ── Keyboard navigation ───────────────────────────────────
    // Scoped to vaCard so it doesn't fire when another card has focus.
    vaCard.addEventListener("keydown", (e) => {
      if (vaPlayerSec.classList.contains("hidden")) return;
      // Don't intercept when typing in any input except the skip-N field
      if (e.target.tagName === "INPUT" && e.target !== vaSkipN) return;
      if (e.target.tagName === "TEXTAREA") return;

      if (e.key === " ") {
        e.preventDefault();
        vaBtnPlay.click();
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        e.ctrlKey ? _vaLoadFrame(_vaCurrentFrame - _vaSkipN())
                  : _vaLoadFrame(_vaCurrentFrame - 1);
        return;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        e.ctrlKey ? _vaLoadFrame(_vaCurrentFrame + _vaSkipN())
                  : _vaLoadFrame(_vaCurrentFrame + 1);
        return;
      }
    });
    // Make the card focusable so keydown fires when clicked inside it
    if (!vaCard.hasAttribute("tabindex")) vaCard.setAttribute("tabindex", "-1");

    // ── Dataset Curation ──────────────────────────────────────────
    (() => {
      const vaCurationStatus  = document.getElementById("va-curation-status");
      const vaExtractFrameBtn = document.getElementById("va-extract-frame-btn");
      const vaAddToDatasetBtn = document.getElementById("va-add-to-dataset-btn");
      const vaBatchAddBtn     = document.getElementById("va-batch-add-btn");
      const vaBatchCount      = document.getElementById("va-batch-count");
      const vaBatchStep       = document.getElementById("va-batch-step");
      const vaCsvNone         = document.getElementById("va-csv-none");
      const vaCsvLoaded       = document.getElementById("va-csv-loaded");
      const vaCsvPathDisplay  = document.getElementById("va-csv-path-display");
      const vaCreateCsvBtn    = document.getElementById("va-create-csv-btn");
      const vaCsvCreateStatus = document.getElementById("va-csv-create-status");
      const vaCsvBars         = document.getElementById("va-csv-bars");
      const vaStatusBarWrap   = document.getElementById("va-status-bar-wrap");
      const vaNoteBarWrap     = document.getElementById("va-note-bar-wrap");
      const vaStatusCanvas    = document.getElementById("va-status-canvas");
      const vaNoteCanvas      = document.getElementById("va-note-canvas");
      const vaStatusChips     = document.getElementById("va-status-chips");
      const vaNoteChips       = document.getElementById("va-note-chips");
      const vaAnnotPanel      = document.getElementById("va-annot-panel");
      const vaAnnotFrameNum   = document.getElementById("va-annot-frame-num");
      const vaNoteInput       = document.getElementById("va-note-input");
      const vaStatusInput     = document.getElementById("va-status-input");
      const vaSaveStatusBtn   = document.getElementById("va-save-status-btn");
      const vaSaveNoteBtn     = document.getElementById("va-save-note-btn");
      const vaAnnotSaveStatus = document.getElementById("va-annot-save-status");
      const vaStatusPrevBtn   = document.getElementById("va-status-prev-btn");
      const vaStatusNextBtn   = document.getElementById("va-status-next-btn");
      const vaNoteStepPrevBtn = document.getElementById("va-note-prev-btn");
      const vaNoteStepNextBtn = document.getElementById("va-note-next-btn");
      const vaNewTagInput     = document.getElementById("va-new-tag-input");
      const vaAddTagBtn       = document.getElementById("va-add-tag-btn");

      // Companion CSV state
      let _vaCsvPath          = null;
      let _vaCsvRows          = [];     // {frame_number, timestamp, frame_line_status, note}
      let _vaUserTags         = [];
      let _vaUserStatuses     = [];
      let _vaActiveNoteFilter = null;

      // Per-chip active sets and color maps (populated when chips are rendered)
      let _vaActiveNoteChips   = new Set();
      let _vaActiveStatusChips = new Set();
      let _vaNoteColorMap      = {};
      let _vaStatusColorMap    = {};

      // Color palettes — status uses warm/green tones, notes use cool/blue tones
      const _VA_STATUS_COLORS = ["#34d399","#f97316","#e879f9","#facc15","#f87171","#22d3ee","#a78bfa","#fb923c"];
      const _VA_NOTE_COLORS   = ["#60a5fa","#f472b6","#4ade80","#38bdf8","#e879f9","#a78bfa","#facc15","#fb7185"];

      // ── Status helpers ──────────────────────────────────────────
      let _curationMsgTimer = null;
      function _curStatus(msg, isErr) {
        if (!vaCurationStatus) return;
        vaCurationStatus.textContent = msg;
        vaCurationStatus.className   = "fe-extract-status" + (isErr ? " err" : "");
        if (_curationMsgTimer) clearTimeout(_curationMsgTimer);
        if (msg && !isErr) {
          _curationMsgTimer = setTimeout(() => {
            vaCurationStatus.textContent = "";
          }, 4000);
        }
      }

      // ── Build request body helper ───────────────────────────────
      function _videoRequestBody(frameNum) {
        const n = (frameNum !== undefined) ? frameNum : _vaCurrentFrame;
        const body = { frame_number: n };
        if (_vaMode === "browse-video" && _vaCurrentVideoPath) {
          body.video_path = _vaCurrentVideoPath;
        } else if (_vaMode === "video" && _vaVideoName) {
          body.video_name = _vaVideoName;
        }
        return body;
      }

      // ── Extract Frame ────────────────────────────────────────────
      if (vaExtractFrameBtn) {
        vaExtractFrameBtn.addEventListener("click", async () => {
          if (!_vaMode || _vaMode === "frames") {
            _curStatus("No video loaded — open a video first.", true); return;
          }
          vaExtractFrameBtn.disabled = true;
          _curStatus("Extracting…");
          try {
            const res  = await fetch("/dlc/curator/extract-frame", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(_videoRequestBody()),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
            _curStatus(
              data.duplicate
                ? `Already extracted: ${data.saved}`
                : `Saved ${data.saved} (${data.folder}, #${data.frame_count})`
            );
          } catch (err) {
            _curStatus(`Extract failed: ${err.message}`, true);
          } finally {
            vaExtractFrameBtn.disabled = false;
          }
        });
      }

      // ── Add to Dataset ────────────────────────────────────────────
      if (vaAddToDatasetBtn) {
        vaAddToDatasetBtn.addEventListener("click", async () => {
          if (!_vaMode || _vaMode === "frames") {
            _curStatus("No video loaded — open a video first.", true); return;
          }
          vaAddToDatasetBtn.disabled = true;
          _curStatus("Adding to dataset…");
          try {
            const res  = await fetch("/dlc/curator/add-to-dataset", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(_videoRequestBody()),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
            const h5note = data.h5_updated ? " + H5" : "";
            _curStatus(
              data.duplicate
                ? `Already in dataset: ${data.saved}`
                : `Added ${data.saved} to CSV${h5note} (${data.frame_count} frames)`
            );
          } catch (err) {
            _curStatus(`Failed: ${err.message}`, true);
          } finally {
            vaAddToDatasetBtn.disabled = false;
          }
        });
      }

      // ── Batch Add ─────────────────────────────────────────────────
      if (vaBatchAddBtn) {
        vaBatchAddBtn.addEventListener("click", async () => {
          if (!_vaMode || _vaMode === "frames") {
            _curStatus("No video loaded — open a video first.", true); return;
          }
          const count = Math.max(1, parseInt(vaBatchCount?.value) || 10);
          const step  = Math.max(1, parseInt(vaBatchStep?.value)  || 30);
          vaBatchAddBtn.disabled = true;
          let added = 0, dupes = 0, errors = 0;
          const start = _vaCurrentFrame;
          let lastFrame = start;
          for (let i = 0; i < count; i++) {
            const frameNum = start + i * step;
            if (frameNum >= _vaFrameCount) break;
            lastFrame = frameNum;
            _curStatus(`Batch adding… ${i + 1}/${count} (frame ${frameNum})`);
            // Navigate player to the frame being extracted
            await _vaLoadFrame(frameNum);
            try {
              const res  = await fetch("/dlc/curator/add-to-dataset", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(_videoRequestBody(frameNum)),
              });
              const data = await res.json();
              if (!res.ok) { errors++; continue; }
              if (data.duplicate) dupes++; else added++;
            } catch (_) { errors++; }
          }
          // Ensure player is on the last frame processed
          if (lastFrame !== _vaCurrentFrame) await _vaLoadFrame(lastFrame);
          vaBatchAddBtn.disabled = false;
          const parts = [];
          if (added) parts.push(`${added} added`);
          if (dupes) parts.push(`${dupes} duplicate${dupes !== 1 ? "s" : ""}`);
          if (errors) parts.push(`${errors} error${errors !== 1 ? "s" : ""}`);
          _curStatus(`Batch done: ${parts.join(", ") || "nothing to add"}.`, errors > 0 && added === 0);
        });
      }

      // ── Timeline bars ────────────────────────────────────────────

      // Canvas-based timeline: one fillRect per annotated frame, zero DOM nodes per frame.
      // Only frames whose field value is in activeSet are drawn; each value uses its own color from colorMap.
      function _vaDrawCanvas(canvas, rows, field, activeSet, colorMap) {
        if (!canvas) return;
        const total = Math.max(_vaFrameCount, 1);
        const W = Math.round(canvas.getBoundingClientRect().width) || canvas.clientWidth || 600;
        canvas.width = W;
        const H    = canvas.height || 12;
        const ctx  = canvas.getContext("2d");
        const minW = Math.max(1, Math.round(W / total));
        ctx.clearRect(0, 0, W, H);
        if (!activeSet || activeSet.size === 0) return;
        rows.forEach(row => {
          const val = row[field];
          if (!val || (field === "frame_line_status" && val === "0")) return;
          if (!activeSet.has(val)) return;
          ctx.fillStyle = colorMap[val] || "#888";
          const x = Math.round((Number(row.frame_number) / total) * W);
          ctx.fillRect(x, 0, minW, H);
        });
      }

      function _vaRedrawNoteCanvas()   { _vaDrawCanvas(vaNoteCanvas,   _vaCsvRows, "note",              _vaActiveNoteChips,   _vaNoteColorMap);   }
      function _vaRedrawStatusCanvas() { _vaDrawCanvas(vaStatusCanvas, _vaCsvRows, "frame_line_status", _vaActiveStatusChips, _vaStatusColorMap); }

      function _vaBuildCsvBars() {
        if (!vaCsvBars) return;
        const hasNote   = _vaCsvRows.some(r => r.note);
        const hasStatus = _vaCsvRows.some(r => r.frame_line_status && r.frame_line_status !== "0");
        vaCsvBars.classList.toggle("hidden", !hasNote && !hasStatus);
        vaNoteBarWrap?.classList.toggle("hidden", !hasNote);
        vaStatusBarWrap?.classList.toggle("hidden", !hasStatus);
        // Canvases start empty; chips toggle individual values onto them.
        _vaRedrawNoteCanvas();
        _vaRedrawStatusCanvas();
      }

      // Click on either canvas — map x position to frame number and jump.
      [vaNoteCanvas, vaStatusCanvas].forEach(canvas => {
        if (!canvas) return;
        canvas.addEventListener("click", e => {
          const rect = canvas.getBoundingClientRect();
          const fn = Math.round((e.clientX - rect.left) / rect.width * Math.max(_vaFrameCount - 1, 0));
          _vaLoadFrame(fn);
        });
      });

      // Prev/next navigation within the active chip set for a given field.
      function _vaNavAnnot(field, activeSet, dir) {
        if (!activeSet.size) return;
        const frames = _vaCsvRows
          .filter(r => { const v = r[field]; return v && (field !== "frame_line_status" || v !== "0") && activeSet.has(v); })
          .map(r => r.frame_number)
          .sort((a, b) => a - b);
        if (!frames.length) return;
        if (dir < 0) {
          const prev = [...frames].reverse().find(f => f < _vaCurrentFrame);
          if (prev != null) _vaLoadFrame(prev);
        } else {
          const next = frames.find(f => f > _vaCurrentFrame);
          if (next != null) _vaLoadFrame(next);
        }
      }

      if (vaStatusPrevBtn) vaStatusPrevBtn.addEventListener("click", () => _vaNavAnnot("frame_line_status", _vaActiveStatusChips, -1));
      if (vaStatusNextBtn) vaStatusNextBtn.addEventListener("click", () => _vaNavAnnot("frame_line_status", _vaActiveStatusChips,  1));
      if (vaNoteStepPrevBtn) vaNoteStepPrevBtn.addEventListener("click", () => _vaNavAnnot("note", _vaActiveNoteChips, -1));
      if (vaNoteStepNextBtn) vaNoteStepNextBtn.addEventListener("click", () => _vaNavAnnot("note", _vaActiveNoteChips,  1));

      // ── Companion CSV helpers ────────────────────────────────────

      function _vaCsvSyncPanel() {
        if (!_vaCsvPath) return;
        if (vaAnnotFrameNum) vaAnnotFrameNum.textContent = _vaCurrentFrame;
        const row = _vaCsvRows.find(r => r.frame_number === _vaCurrentFrame);
        if (vaNoteInput)   vaNoteInput.value   = row ? (row.note || "") : "";
        if (vaStatusInput) vaStatusInput.value = row ? (row.frame_line_status ?? "0") : "0";
      }

      function _vaCsvApplyRows(rows, csvPath) {
        _vaCsvPath  = csvPath;
        _vaCsvRows  = rows;
        const noteVals   = [...new Set(rows.map(r => r.note).filter(v => v))];
        const statusVals = [...new Set(rows.map(r => r.frame_line_status).filter(v => v && v !== "0"))];
        _vaUserTags     = [...new Set([..._vaUserTags,     ...noteVals])];
        _vaUserStatuses = [...new Set([..._vaUserStatuses, ...statusVals])];

        if (vaCsvNone)        vaCsvNone.classList.add("hidden");
        if (vaCsvLoaded)      vaCsvLoaded.classList.remove("hidden");
        if (vaCsvPathDisplay) { vaCsvPathDisplay.textContent = csvPath; vaCsvPathDisplay.title = csvPath; }
        if (vaAnnotPanel)     vaAnnotPanel.classList.remove("hidden");

        _vaBuildCsvBars();
        _vaCsvRenderStatusChips();
        _vaCsvRenderTags();
        _vaCsvSyncPanel();
      }

      function _vaCsvRenderStatusChips() {
        if (!vaStatusChips) return;
        vaStatusChips.innerHTML = "";
        _vaStatusColorMap = {};
        _vaUserStatuses.forEach((val, i) => {
          const color = _VA_STATUS_COLORS[i % _VA_STATUS_COLORS.length];
          _vaStatusColorMap[val] = color;
          const chip = document.createElement("span");
          chip.className = "fe-tag-chip" + (_vaActiveStatusChips.has(val) ? " active" : "");
          chip.textContent = val;
          chip.style.setProperty("--chip-color", color);
          chip.title = `Click to show/hide "${val}" on timeline`;
          chip.addEventListener("click", () => {
            if (_vaActiveStatusChips.has(val)) _vaActiveStatusChips.delete(val);
            else _vaActiveStatusChips.add(val);
            _vaCsvRenderStatusChips();
            _vaRedrawStatusCanvas();
          });
          vaStatusChips.appendChild(chip);
        });
        const hasActive = _vaActiveStatusChips.size > 0;
        if (vaStatusPrevBtn) vaStatusPrevBtn.disabled = !hasActive;
        if (vaStatusNextBtn) vaStatusNextBtn.disabled = !hasActive;
      }

      function _vaCsvRenderTags() {
        if (!vaNoteChips) return;
        vaNoteChips.innerHTML = "";
        _vaNoteColorMap = {};
        _vaUserTags.forEach((tag, i) => {
          const color = _VA_NOTE_COLORS[i % _VA_NOTE_COLORS.length];
          _vaNoteColorMap[tag] = color;
          const chip = document.createElement("span");
          chip.className = "fe-tag-chip" + (_vaActiveNoteChips.has(tag) ? " active" : "");
          chip.textContent = tag;
          chip.style.setProperty("--chip-color", color);
          chip.title = `Click to show/hide "${tag}" on timeline`;
          chip.addEventListener("click", () => {
            if (_vaActiveNoteChips.has(tag)) _vaActiveNoteChips.delete(tag);
            else _vaActiveNoteChips.add(tag);
            _vaCsvRenderTags();
            _vaRedrawNoteCanvas();
          });
          vaNoteChips.appendChild(chip);
        });
        const hasActive = _vaActiveNoteChips.size > 0;
        if (vaNoteStepPrevBtn) vaNoteStepPrevBtn.disabled = !hasActive;
        if (vaNoteStepNextBtn) vaNoteStepNextBtn.disabled = !hasActive;
      }

      async function _vaCsvSaveStatus() {
        if (!_vaCsvPath) return;
        // Read existing note for this frame so saving status doesn't wipe it
        const existingRow = _vaCsvRows.find(r => r.frame_number === _vaCurrentFrame);
        const note   = vaNoteInput ? vaNoteInput.value.trim() : (existingRow?.note || "");
        const status = vaStatusInput ? (vaStatusInput.value || "0") : "0";
        await _vaCsvDoSave(note, status);
      }

      async function _vaCsvSaveNote() {
        if (!_vaCsvPath) return;
        // Read existing status for this frame so saving note doesn't wipe it
        const existingRow = _vaCsvRows.find(r => r.frame_number === _vaCurrentFrame);
        const note   = vaNoteInput ? vaNoteInput.value.trim() : "";
        const status = vaStatusInput ? (vaStatusInput.value || "0") : (existingRow?.frame_line_status || "0");
        await _vaCsvDoSave(note, status);
      }

      async function _vaCsvDoSave(note, status) {
        if (vaAnnotSaveStatus) { vaAnnotSaveStatus.textContent = "Saving…"; vaAnnotSaveStatus.className = "fe-extract-status"; }
        try {
          const res  = await fetch("/annotate/save-row", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({
              csv_path:          _vaCsvPath,
              frame_number:      _vaCurrentFrame,
              note,
              frame_line_status: status,
              fps:               _vaFps,
            }),
          });
          const data = await res.json();
          if (data.error) throw new Error(data.error);

          const isInteresting = note || (status && status !== "0");
          const idx = _vaCsvRows.findIndex(r => r.frame_number === _vaCurrentFrame);
          if (isInteresting) {
            const savedRow = data.row || { frame_number: _vaCurrentFrame, timestamp: (_vaCurrentFrame / _vaFps).toFixed(3), frame_line_status: status, note };
            if (idx >= 0) _vaCsvRows[idx] = savedRow;
            else { _vaCsvRows.push(savedRow); _vaCsvRows.sort((a, b) => a.frame_number - b.frame_number); }
            if (note && !_vaUserTags.includes(note)) { _vaUserTags.push(note); _vaCsvRenderTags(); }
            if (status && status !== "0" && !_vaUserStatuses.includes(status)) { _vaUserStatuses.push(status); _vaCsvRenderStatusChips(); }
          } else {
            if (idx >= 0) _vaCsvRows.splice(idx, 1);
          }

          _vaBuildCsvBars();

          if (vaAnnotSaveStatus) {
            vaAnnotSaveStatus.textContent = "Saved";
            vaAnnotSaveStatus.className   = "fe-extract-status ok";
            setTimeout(() => { if (vaAnnotSaveStatus?.textContent === "Saved") vaAnnotSaveStatus.textContent = ""; }, 2000);
          }
        } catch (err) {
          if (vaAnnotSaveStatus) { vaAnnotSaveStatus.textContent = `Error: ${err.message}`; vaAnnotSaveStatus.className = "fe-extract-status err"; }
        }
      }

      async function _vaCsvLoad(videoPath) {
        // Reset CSV state
        _vaCsvPath = null; _vaCsvRows = []; _vaUserTags = []; _vaUserStatuses = []; _vaActiveNoteFilter = null;
        _vaActiveNoteChips = new Set(); _vaActiveStatusChips = new Set();
        _vaNoteColorMap = {}; _vaStatusColorMap = {};
        if (vaCsvNone)        vaCsvNone.classList.remove("hidden");
        if (vaCsvLoaded)      vaCsvLoaded.classList.add("hidden");
        if (vaCsvBars)        vaCsvBars.classList.add("hidden");
        if (vaAnnotPanel)     vaAnnotPanel.classList.add("hidden");
        if (vaCsvCreateStatus) vaCsvCreateStatus.textContent = "";

        if (!videoPath) return;
        try {
          const res  = await fetch(`/annotate/csv?path=${encodeURIComponent(videoPath)}`);
          const data = await res.json();
          if (data.csv_exists) {
            _vaCsvApplyRows(data.rows, data.csv_path);
          }
        } catch (_) {}
      }

      // Hook into frame navigation
      _vaCurationFrameHook = () => {
        _vaCsvSyncPanel();
      };

      // Load companion CSV when player section becomes visible (video opened)
      if (typeof MutationObserver !== "undefined" && vaPlayerSec) {
        new MutationObserver(async () => {
          if (!vaPlayerSec.classList.contains("hidden") && _vaCurrentVideoPath) {
            await _vaCsvLoad(_vaCurrentVideoPath);
          } else if (vaPlayerSec.classList.contains("hidden")) {
            _vaCsvPath = null; _vaCsvRows = []; _vaUserTags = []; _vaUserStatuses = []; _vaActiveNoteFilter = null;
            _vaActiveNoteChips = new Set(); _vaActiveStatusChips = new Set();
            _vaNoteColorMap = {}; _vaStatusColorMap = {};
            if (vaCsvNone)    vaCsvNone.classList.remove("hidden");
            if (vaCsvLoaded)  vaCsvLoaded.classList.add("hidden");
            if (vaCsvBars)    vaCsvBars.classList.add("hidden");
            if (vaAnnotPanel) vaAnnotPanel.classList.add("hidden");
          }
        }).observe(vaPlayerSec, { attributes: true, attributeFilter: ["class"] });
      }

      // Create CSV
      if (vaCreateCsvBtn) {
        vaCreateCsvBtn.addEventListener("click", async () => {
          if (!_vaCurrentVideoPath) return;
          if (vaCsvCreateStatus) { vaCsvCreateStatus.textContent = `Creating CSV for ${_vaFrameCount} frames…`; vaCsvCreateStatus.className = "fe-extract-status"; }
          try {
            const res  = await fetch("/annotate/create-csv", {
              method:  "POST",
              headers: { "Content-Type": "application/json" },
              body:    JSON.stringify({ video_path: _vaCurrentVideoPath, fps: _vaFps, frame_count: _vaFrameCount }),
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);
            if (vaCsvCreateStatus) vaCsvCreateStatus.textContent = "";
            _vaCsvApplyRows(data.rows, data.csv_path);
          } catch (err) {
            if (vaCsvCreateStatus) { vaCsvCreateStatus.textContent = `Error: ${err.message}`; vaCsvCreateStatus.className = "fe-extract-status err"; }
          }
        });
      }

      // Save annotation
      if (vaSaveStatusBtn) {
        vaSaveStatusBtn.addEventListener("click", _vaCsvSaveStatus);
      }
      if (vaSaveNoteBtn) {
        vaSaveNoteBtn.addEventListener("click", _vaCsvSaveNote);
      }

      // Add new tag
      if (vaAddTagBtn) {
        vaAddTagBtn.addEventListener("click", () => {
          const tag = vaNewTagInput ? vaNewTagInput.value.trim() : "";
          if (!tag) return;
          if (!_vaUserTags.includes(tag)) { _vaUserTags.push(tag); _vaCsvRenderTags(); }
          if (vaNewTagInput) vaNewTagInput.value = "";
        });
      }
      if (vaNewTagInput) {
        vaNewTagInput.addEventListener("keydown", e => {
          if (e.key === "Enter") { e.preventDefault(); vaAddTagBtn?.click(); }
        });
      }

    })(); // end Dataset Curation

    // ── Video Metadata Panel (companion CSV viewer) ────────────────────────
    (() => {
      const vaMetaCsvInfo   = document.getElementById("va-meta-csv-info");
      const vaMetaFrameRow  = document.getElementById("va-meta-frame-row");
      const vaMetaFrameNote = document.getElementById("va-meta-frame-note");
      const vaMetaFrameStat = document.getElementById("va-meta-frame-status");
      if (!vaMetaCsvInfo) return;

      let _metaCsvRows = [];

      function _metaClear() {
        _metaCsvRows = [];
        vaMetaCsvInfo.textContent = "No companion CSV";
        vaMetaFrameRow.style.display = "none";
      }

      function _metaShowFrame(n) {
        if (!_metaCsvRows.length) { vaMetaFrameRow.style.display = "none"; return; }
        const row = _metaCsvRows.find(r => r.frame_number === n);
        const hasContent = row && (row.note || (row.frame_line_status && row.frame_line_status !== "0"));
        if (!hasContent) { vaMetaFrameRow.style.display = "none"; return; }
        vaMetaFrameRow.style.display = "flex";
        vaMetaFrameNote.textContent = row.note || "—";
        vaMetaFrameStat.textContent = row.frame_line_status || "0";
      }

      async function _metaLoad(videoPath) {
        _metaClear();
        if (!videoPath) return;
        try {
          const res  = await fetch(`/annotate/csv?path=${encodeURIComponent(videoPath)}`);
          const data = await res.json();
          if (!data.csv_exists) return;
          // Store only rows with actual annotations for the frame tooltip
          _metaCsvRows = (data.rows || []).filter(
            r => r.note || (r.frame_line_status && r.frame_line_status !== "0"),
          );
          const fname = (data.csv_path || "").split("/").pop();
          vaMetaCsvInfo.textContent = _metaCsvRows.length
            ? `${fname} · ${_metaCsvRows.length.toLocaleString()} annotated frames`
            : `${fname} · no annotations yet`;
        } catch (_) {}
      }

      // Hook into frame navigation (outer scope variable set above)
      _vaMetadataFrameHook = n => _metaShowFrame(n);

      // Watch vaPlayerSec visibility to auto-load the companion CSV
      if (typeof MutationObserver !== "undefined" && vaPlayerSec) {
        new MutationObserver(async () => {
          if (!vaPlayerSec.classList.contains("hidden") && _vaCurrentVideoPath) {
            await _metaLoad(_vaCurrentVideoPath);
            _metaShowFrame(_vaCurrentFrame);
          } else if (vaPlayerSec.classList.contains("hidden")) {
            _metaClear();
          }
        }).observe(vaPlayerSec, { attributes: true, attributeFilter: ["class"] });
      }
    })(); // end Video Metadata Panel

