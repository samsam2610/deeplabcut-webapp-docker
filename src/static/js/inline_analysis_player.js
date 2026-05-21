"use strict";
import { state } from './state.js';
import { makeFileBrowser } from './components/file_browser.js';

    const iaCard         = document.getElementById("inline-analysis-card");
    const iaOpenBtn      = document.getElementById("btn-open-inline-analysis");
    const iaCloseBtn     = document.getElementById("btn-close-inline-analysis");
    const iaRefreshBtn   = document.getElementById("ia-refresh-btn");
    const iaContentList  = document.getElementById("ia-content-list");
    const iaPlayerSec    = document.getElementById("ia-player-section");
    const iaSelectedName = document.getElementById("ia-selected-name");
    const iaBackBtn      = document.getElementById("ia-btn-back");
    const iaVideoWrap    = document.getElementById("ia-video-wrap");
    const iaFrameImg     = document.getElementById("ia-frame-img");
    const iaFrameSpinner = document.getElementById("ia-frame-spinner");
    const iaZoomInput    = document.getElementById("ia-zoom");
    const iaZoomVal      = document.getElementById("ia-zoom-val");
    const iaBtnPlay      = document.getElementById("ia-btn-play");
    const iaPlayIcon     = document.getElementById("ia-play-icon");
    const iaPauseIcon    = document.getElementById("ia-pause-icon");
    const iaBtnPrev      = document.getElementById("ia-btn-prev");
    const iaBtnNext      = document.getElementById("ia-btn-next");
    const iaBtnSkipBack  = document.getElementById("ia-btn-skip-back");
    const iaBtnSkipFwd   = document.getElementById("ia-btn-skip-fwd");
    const iaSkipN        = document.getElementById("ia-skip-n");
    const iaFrameCounter = document.getElementById("ia-frame-counter");
    const iaTimeDisplay  = document.getElementById("ia-time-display");
    const iaSeek         = document.getElementById("ia-seek");
    const iaStatus       = document.getElementById("ia-status");
    // Browse-tab elements
    const iaTabProject      = document.getElementById("ia-tab-project");
    const iaTabBrowse       = document.getElementById("ia-tab-browse");
    const iaTabProjectPanel = document.getElementById("ia-tab-project-panel");
    const iaTabBrowsePanel  = document.getElementById("ia-tab-browse-panel");
    const iaBrowseBreadcrumb = document.getElementById("ia-browse-breadcrumb");
    const iaBrowseUp         = document.getElementById("ia-browse-up");
    const iaBrowseList       = document.getElementById("ia-browse-list");

    // State
    let _iaMode         = null;   // "video" | "frames" | "browse-video"
    let _iaCurrentFrame = 0;
    let _iaFrameCount   = 0;
    let _iaFps          = 30;
    let _iaFrameBusy    = false;
    let _iaPlayTimer    = null;
    let _iaSeekDragging = false;
    let _iaZoom         = 100;
    // video mode (DLC project labeled videos)
    let _iaVideoName  = null;
    // frames mode
    let _iaFrameStem  = null;
    let _iaFrameFiles = [];   // sorted list of labeled frame filenames
    // browse-video mode (arbitrary path via /annotate endpoints)
    let _iaBrowseVideoPath = null;
    // browse tab state
    let _iaBrowsePath = null;

    // ── Kinematic overlay state ────────────────────────────────────────────
    let _iaOverlayEnabled   = false;
    let _iaAllBodyParts     = [];         // all body parts from h5-info
    let _iaSelectedBp       = null;       // currently active/selected bodypart
    const _iaHiddenParts    = new Set();  // client-side per-bodypart visibility toggle
    let _iaMarkerSize       = 6;
    // absolute path to the currently loaded original video (for annotated frames + companion CSV)
    let _iaCurrentVideoPath = null;
    // Hook called by _iaLoadFrame so the nested curation IIFE can sync its annotation panel
    let _iaCurationFrameHook = null;
    let _iaMetadataFrameHook = null;

    // ── Pose cache (prefetch window) ───────────────────────────────────────
    const _POSE_WINDOW    = 30;
    let   _iaPrefetchCtrl = null;     // AbortController for in-flight batch prefetch

    function _iaClearPoseCache() {
      // Clear per-layer caches. Legacy single-cache fully removed in T6.
      _iaLayers.forEach(l => l.posesCache.clear());
      if (_iaPrefetchCtrl) { _iaPrefetchCtrl.abort(); _iaPrefetchCtrl = null; }
    }

    // ── Kinematic overlay LAYER state ───────────────────────────────────
    // Element 0 = primary (editable). Elements 1+ = comparison layers (read-only).
    // Each layer:
    //   { id, path, label, type, shape, visible, threshold, posesCache,
    //     bodyparts, errored }
    const _iaLayers = [];
    let   _iaGlobalThreshold    = 0.60;
    let   _iaPerLayerThresholds = false;

    function _iaPrimary()     { return _iaLayers[0] || null; }
    function _iaCompare()     { return _iaLayers.slice(1); }
    function _iaIsEditable()  { return _iaLayers.length === 1; }
    function _iaLayerThreshold(layer) {
      return _iaPerLayerThresholds && layer.threshold != null
        ? layer.threshold
        : _iaGlobalThreshold;
    }

    const _SHAPE_ORDER = ["circle-filled", "diamond", "square", "triangle"];
    function _iaAssignShapes() {
      _iaLayers.forEach((l, i) => {
        l.shape = _SHAPE_ORDER[Math.min(i, _SHAPE_ORDER.length - 1)];
      });
    }

    let _iaLayerIdCounter = 0;
    function _iaMakeLayer({path, label, type}) {
      return {
        id:         "layer_" + (_iaLayerIdCounter++),
        path,
        label,
        type:       type || "raw",
        shape:      "circle-filled",
        visible:    true,
        threshold:  null,            // null → use _iaGlobalThreshold
        posesCache: new Map(),
        bodyparts:  [],
        editsCache: null,
        errored:    false,
      };
    }

    // Replace the entire layer set with [primary], clear caches, reassign shapes.
    function _iaSetPrimaryLayer(layer) {
      _iaLayers.length = 0;
      if (layer) _iaLayers.push(layer);
      _iaAssignShapes();
      _iaClearPoseCache();
    }

    // ── Viewer sizing (same break-out-of-card approach as frame labeler) ──
    function _iaFitViewer() {
      if (!iaFrameImg.naturalWidth) return;
      const cs    = getComputedStyle(iaCard);
      const padL  = parseFloat(cs.paddingLeft)  || 0;
      const padR  = parseFloat(cs.paddingRight) || 0;
      const baseW = iaCard.clientWidth - padL - padR;
      const maxW  = Math.max(baseW, window.innerWidth - 32);
      const targetW = Math.min(Math.round(baseW * (_iaZoom / 100)), Math.floor(maxW));
      const extra   = targetW - baseW;
      iaVideoWrap.style.width      = targetW + "px";
      iaVideoWrap.style.marginLeft = extra > 0 ? `-${extra / 2}px` : "";
    }

    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => { if (iaFrameImg.naturalWidth) _iaFitViewer(); }).observe(iaCard);
    }

    iaZoomInput.addEventListener("input", () => {
      _iaZoom = parseInt(iaZoomInput.value, 10);
      iaZoomVal.textContent = _iaZoom + " %";
      _iaFitViewer();
      _iaSyncCanvas();
    });

    function _iaReset() {
      if (_iaPlayTimer) { _iaStopPlayback(); }
      _iaMode            = null;
      _iaCurrentFrame    = 0;
      _iaFrameCount      = 0;
      _iaFps             = 30;
      _iaFrameBusy       = false;
      _iaVideoName       = null;
      _iaFrameStem       = null;
      _iaFrameFiles      = [];
      _iaBrowseVideoPath = null;
      _iaCurrentVideoPath = null;
      _iaCurrentPoses = [];
      _iaHoverBp      = null;
      _iaSelectedBp   = null;
      _iaHiddenParts.clear();
      _iaDragBp       = null;
      _iaDragging     = false;
      if (iaBpListWrap) iaBpListWrap.classList.add("hidden");
      if (iaBpChips)    iaBpChips.innerHTML = "";
      if (iaOverlayCanvas) iaOverlayCanvas.style.cursor = "default";
      if (typeof _iaLocalEdits !== "undefined") _iaLocalEdits.clear();
      _iaUpdateEditBanner();
      _iaClearPoseCache();
      if (iaOverlayCtx) iaOverlayCtx.clearRect(0, 0, iaOverlayCanvas.width, iaOverlayCanvas.height);
      iaPlayIcon.classList.remove("hidden"); iaPauseIcon.classList.add("hidden");
      iaFrameImg.onload  = null;
      iaFrameImg.onerror = null;
      if (iaFrameImg.src && iaFrameImg.src.startsWith("blob:")) URL.revokeObjectURL(iaFrameImg.src);
      iaFrameImg.removeAttribute("src");
      iaVideoWrap.style.width      = "";
      iaVideoWrap.style.marginLeft = "";
      iaFrameSpinner.classList.add("hidden");
      iaPlayerSec.classList.add("hidden");
      iaStatus.textContent = "";
      iaStatus.className   = "fe-extract-status";
    }

    function _iaFrameUrl(n) {
      if (_iaMode === "browse-video") {
        // Use the same cached VideoCapture endpoint as Frame Extractor
        return `/dlc/project/video-frame-ext/${n}?path=${encodeURIComponent(_iaBrowseVideoPath)}`;
      }
      if (_iaMode === "video") {
        return `/dlc/project/video-frame/${encodeURIComponent(_iaVideoName)}/${n}`;
      }
      // frames mode: index into _iaFrameFiles
      return `/dlc/project/frame-image/${encodeURIComponent(_iaFrameStem)}/${encodeURIComponent(_iaFrameFiles[n])}`;
    }

    function _iaPrefetchFrames(frames) {
      frames.forEach(n => {
        if (n >= 0 && n < _iaFrameCount) new Image().src = _iaFrameUrl(n);
      });
    }

    function _iaUpdateDisplay() {
      iaFrameCounter.textContent = `Frame ${_iaCurrentFrame} / ${_iaFrameCount}`;
      if (_iaMode === "video" || _iaMode === "browse-video") {
        iaTimeDisplay.textContent = `${(_iaCurrentFrame / _iaFps).toFixed(3)} s`;
      } else {
        iaTimeDisplay.textContent = _iaFrameFiles[_iaCurrentFrame] || "";
      }
      if (!_iaSeekDragging)
        iaSeek.value = Math.round((_iaCurrentFrame / Math.max(_iaFrameCount - 1, 1)) * 1000);
    }

    async function _iaLoadFrame(n) {
      if (_iaFrameBusy) return;
      _iaFrameBusy = true;
      n = Math.max(0, Math.min(n, Math.max(_iaFrameCount - 1, 0)));
      _iaCurrentFrame = n;
      iaFrameSpinner.classList.remove("hidden");

      const newUrl = _iaFrameUrl(n);

      // Preload the image off-DOM in parallel with all visible-layer pose
      // fetches so the visible image NEVER lands on screen before its markers.
      const imgReady = new Promise((resolve, reject) => {
        const im = new Image();
        im.onload  = () => resolve(im);
        im.onerror = (e) => reject(e || new Error("image preload failed"));
        im.src = newUrl;
      });

      const posesReady = _iaOverlayEnabled
        ? Promise.all(
            _iaLayers
              .filter(l => l.visible && !l.errored)
              .map(l => _iaFetchPosesForFrame(l, n).catch(() => null))
          )
        : Promise.resolve();

      try {
        const [preloadedImg] = await Promise.all([imgReady, posesReady]);

        // Atomic swap: image + markers go to screen together.
        const prev = iaFrameImg.src;
        iaFrameImg.src = preloadedImg.src;
        if (prev && prev.startsWith("blob:")) URL.revokeObjectURL(prev);

        _iaFitViewer();
        _iaUpdateDisplay();
        _iaPrefetchFrames([n + 1, n + 2]);
        if (_iaCurationFrameHook) _iaCurationFrameHook(n);
        if (_iaMetadataFrameHook) _iaMetadataFrameHook(n);

        // Sync primary cache into legacy _iaCurrentPoses for hit-testing.
        const primary = _iaPrimary();
        if (primary) {
          const c = primary.posesCache.get(n);
          if (c) { _iaCurrentPoses = c.poses; _iaNBodyparts = c.n_bodyparts; }
        }

        _iaUpdateOverlay(n);

        // Paint barrier — guarantees image + canvas have landed before the
        // play loop schedules the next tick.
        await new Promise(requestAnimationFrame);
      } catch (err) {
        iaStatus.textContent = `Failed to load frame: ${err && err.message ? err.message : err}`;
        iaStatus.className   = "fe-extract-status err";
      } finally {
        _iaFrameBusy = false;
        iaFrameSpinner.classList.add("hidden");
      }
    }

    // Draw overlay for frame n: show cached poses immediately; fetch from server only when paused.
    function _iaUpdateOverlay(n) {
      if (!iaOverlayCtx || !_iaOverlayEnabled) return;
      _iaSyncCanvas();
      iaOverlayCtx.clearRect(0, 0, iaOverlayCanvas.width, iaOverlayCanvas.height);
      const primary = _iaPrimary();
      if (!primary) return;
      // Sync primary's cache into the legacy _iaCurrentPoses (consumed by
      // hit-testing, hover labels, bp-chip status, and the edit overlays).
      const pKey    = _iaPoseCacheKey(primary);
      const pCached = primary.posesCache.get(n);
      let primaryReady = false;
      if (pCached && pCached.key === pKey) {
        _iaCurrentPoses = pCached.poses;
        _iaNBodyparts   = pCached.n_bodyparts;
        primaryReady    = true;
      }
      _iaDrawCurrentFrame();
      if (primaryReady) _iaUpdateBpChipStatus();
      // Only hit the server when paused
      if (!_iaPlayTimer && (!pCached || pCached.key !== pKey)) _iaFetchPoses(n);
    }

    // Multi-layer draw orchestration: clears canvas, then for each visible/non-errored
    // layer renders its cached poses with the appropriate shape primitive. The primary
    // layer goes through _iaDrawPoseMarkers so its edits/select/hover overlays appear.
    function _iaDrawCurrentFrame() {
      if (!iaOverlayCtx || !_iaOverlayEnabled) return;
      _iaSyncCanvas();
      iaOverlayCtx.clearRect(0, 0, iaOverlayCanvas.width, iaOverlayCanvas.height);
      const natW = iaFrameImg.naturalWidth  || 1;
      const natH = iaFrameImg.naturalHeight || 1;
      const sx   = iaOverlayCanvas.width  / natW;
      const sy   = iaOverlayCanvas.height / natH;
      const r    = Math.max(1, Math.round(_iaMarkerSize * Math.min(sx, sy)));
      const visibleLayers = _iaLayers.filter(l => l.visible && !l.errored);
      // Draw comparison layers underneath the primary (primary draws last so its
      // selection/edit rings sit on top).
      for (const layer of visibleLayers) {
        if (layer === _iaPrimary()) continue;
        const cached = layer.posesCache.get(_iaCurrentFrame);
        if (!cached) continue;
        const drawFn = _SHAPE_FN[layer.shape] || _drawCircleFilled;
        const total  = cached.n_bodyparts || layer.bodyparts.length || 1;
        for (const pose of cached.poses) {
          if (_iaHiddenParts.has(pose.bp)) continue;
          const cx = Math.round(pose.x * sx);
          const cy = Math.round(pose.y * sy);
          const color = _iaPaletteColor(pose.color_idx, total);
          drawFn(iaOverlayCtx, cx, cy, r, color);
        }
      }
      // Primary layer: handles edits/selection/hover via existing _iaDrawPoseMarkers.
      if (_iaPrimary() && _iaPrimary().visible && !_iaPrimary().errored) {
        _iaDrawPoseMarkers();
      }
    }

    async function _iaOpenVideo(name) {
      _iaReset();
      _iaMode      = "video";
      _iaVideoName = name;
      iaSelectedName.textContent = name;
      try {
        const res  = await fetch(`/dlc/project/video-info/${encodeURIComponent(name)}`);
        const info = await res.json();
        _iaFps             = info.fps || 30;
        _iaFrameCount      = info.frame_count || 0;
        _iaCurrentVideoPath = info.abs_path || null;
      } catch (_) { _iaFps = 30; _iaFrameCount = 0; }
      iaPlayerSec.classList.remove("hidden");
      _iaLoadFrame(0);
    }

    function _iaOpenFrameFolder(stem, frames) {
      _iaReset();
      _iaMode       = "frames";
      _iaFrameStem  = stem;
      _iaFrameFiles = frames;
      _iaFrameCount = frames.length;
      _iaFps        = 5;   // slow playback for sparse labeled frames
      iaSelectedName.textContent = `${stem}/ (${frames.length} labeled frames)`;
      iaPlayerSec.classList.remove("hidden");
      _iaLoadFrame(0);
    }

    async function _iaOpenBrowseVideo(absPath, name) {
      _iaReset();
      _iaMode             = "browse-video";
      _iaBrowseVideoPath  = absPath;
      _iaCurrentVideoPath = absPath;
      iaSelectedName.textContent = name;
      try {
        const res  = await fetch(`/annotate/video-info?path=${encodeURIComponent(absPath)}`);
        const info = await res.json();
        _iaFps        = info.fps || 30;
        _iaFrameCount = info.frame_count || 0;
      } catch (_) { _iaFps = 30; _iaFrameCount = 0; }
      iaPlayerSec.classList.remove("hidden");
      _iaLoadFrame(0);
      // Discover companion h5 variants in the same directory
      _iaDiscoverVariants(absPath);
    }

    // ── Browse-tab folder navigator ────────────────────────────
    const _VA_VIDEO_EXTS = new Set([".mp4", ".avi", ".mov", ".mkv", ".mpg", ".mpeg"]);

    async function _iaRefreshBrowse(path) {
      _iaBrowsePath = path;
      iaBrowseBreadcrumb.value = path;
      iaBrowseList.innerHTML = '<p class="explorer-empty">Loading…</p>';

      // Try the new dir-with-h5 endpoint; fall back to /fs/ls on failure.
      let data;
      try {
        const res = await fetch(`/dlc/viewer/dir-with-h5?path=${encodeURIComponent(path)}`);
        if (!res.ok) throw new Error(`status ${res.status}`);
        data = await res.json();
        if (data.error) throw new Error(data.error);
      } catch (newRouteErr) {
        // Fallback: legacy /fs/ls. Treat every video as has_h5=false (we can't tell).
        try {
          const res2 = await fetch(`/fs/ls?path=${encodeURIComponent(path)}`);
          const d2   = await res2.json();
          if (d2.error) { iaBrowseList.innerHTML = `<p class="explorer-empty">${d2.error}</p>`; return; }
          const entries = d2.entries || [];
          data = {
            path,
            dirs:   entries.filter(e => e.type === "dir").map(e => ({name: e.name})),
            videos: entries
              .filter(e => e.type === "file" && _VA_VIDEO_EXTS.has(e.name.slice(e.name.lastIndexOf(".")).toLowerCase()))
              .map(e => ({name: e.name, has_h5: false, h5_count: 0})),
          };
        } catch (fbErr) {
          iaBrowseList.innerHTML = `<p class="explorer-empty">Error: ${fbErr.message}</p>`;
          return;
        }
      }

      const dirs = data.dirs || [];
      const videos = data.videos || [];
      const hideNoH5 = !!state.iaBrowseHideNoH5;
      const visibleVideos = hideNoH5 ? videos.filter(v => v.has_h5) : videos;

      if (!dirs.length && !visibleVideos.length) {
        iaBrowseList.innerHTML = hideNoH5
          ? '<p class="explorer-empty">No videos with analyzed h5 here. Untick "Hide videos without h5" to show all.</p>'
          : '<p class="explorer-empty">No folders or videos found here.</p>';
        return;
      }

      iaBrowseList.innerHTML = "";

      dirs.forEach(d => {
        const row = document.createElement("div");
        row.className = "fe-video-item";
        row.style.cursor = "pointer";
        row.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>`;
        row.querySelector("span").textContent = d.name + "/";
        row.addEventListener("click", () => _iaRefreshBrowse(path + "/" + d.name));
        iaBrowseList.appendChild(row);
      });

      visibleVideos.forEach(v => {
        const fullPath = path + "/" + v.name;
        const row = document.createElement("div");
        row.className = "fe-video-item";
        row.style.cursor = "pointer";
        row.dataset.hasH5 = v.has_h5 ? "true" : "false";
        const iconOpacity = v.has_h5 ? "1" : "0.45";
        const badge = v.has_h5
          ? `<span style="font-size:.68rem;color:var(--text-dim);margin-left:auto;padding:.05rem .35rem;background:var(--surface);border:1px solid var(--border);border-radius:8px">${v.h5_count} h5</span>`
          : `<span style="font-size:.68rem;color:var(--text-dim);margin-left:auto;font-style:italic">no h5</span>`;
        row.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:${iconOpacity}"><rect x="2" y="2" width="20" height="20" rx="3"/><polygon points="10 8 16 12 10 16 10 8" fill="currentColor" stroke="none"/></svg><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;opacity:${iconOpacity === "1" ? "1" : "0.7"}"></span>${badge}`;
        row.querySelector("span").textContent = v.name;
        row.addEventListener("click", () => _iaOpenBrowseVideo(fullPath, v.name));
        iaBrowseList.appendChild(row);
      });
    }

    // ── Tab switching ──────────────────────────────────────────
    iaTabProject?.addEventListener("click", () => {
      iaTabProject.classList.add("active");
      iaTabBrowse.classList.remove("active");
      iaTabProjectPanel.classList.remove("hidden");
      iaTabBrowsePanel.classList.add("hidden");
    });
    iaTabBrowse?.addEventListener("click", () => {
      iaTabBrowse.classList.add("active");
      iaTabProject.classList.remove("active");
      iaTabBrowsePanel.classList.remove("hidden");
      iaTabProjectPanel.classList.add("hidden");
      if (!_iaBrowsePath) {
        // Start at user-data dir or /
        const startPath = state.userDataDir || state.dataDir || "/";
        _iaRefreshBrowse(startPath);
      }
    });

    iaBrowseUp?.addEventListener("click", () => {
      if (!_iaBrowsePath) return;
      const parent = _iaBrowsePath.split("/").slice(0, -1).join("/") || "/";
      if (parent !== _iaBrowsePath) _iaRefreshBrowse(parent);
    });

    const iaBrowseHideNoH5 = document.getElementById("ia-browse-hide-no-h5");
    iaBrowseHideNoH5?.addEventListener("change", () => {
      state.iaBrowseHideNoH5 = !!iaBrowseHideNoH5.checked;
      if (_iaBrowsePath) _iaRefreshBrowse(_iaBrowsePath);
    });
    // On startup, sync the checkbox to state (state.iaBrowseHideNoH5 defaults true).
    if (iaBrowseHideNoH5) iaBrowseHideNoH5.checked = !!state.iaBrowseHideNoH5;

    // ── Editable address bar ───────────────────────────────────
    async function _iaNavigateTo(raw) {
      const p = raw.trim();
      if (!p) return;
      // Check if the path looks like a video file
      const ext = p.slice(p.lastIndexOf(".")).toLowerCase();
      if (_VA_VIDEO_EXTS.has(ext)) {
        // Navigate the browser to the parent folder first, then open the video
        const dir  = p.substring(0, p.lastIndexOf("/")) || "/";
        const name = p.substring(p.lastIndexOf("/") + 1);
        await _iaRefreshBrowse(dir);
        _iaOpenBrowseVideo(p, name);
      } else {
        _iaRefreshBrowse(p);
      }
    }

    iaBrowseBreadcrumb?.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); _iaNavigateTo(iaBrowseBreadcrumb.value); }
      if (e.key === "Escape") { iaBrowseBreadcrumb.value = _iaBrowsePath || ""; iaBrowseBreadcrumb.blur(); }
    });
    // Also handle paste: navigate immediately after the clipboard text lands
    iaBrowseBreadcrumb?.addEventListener("paste", e => {
      // Let the paste complete, then navigate
      setTimeout(() => _iaNavigateTo(iaBrowseBreadcrumb.value), 0);
    });

    // ── Kinematic overlay canvas ──────────────────────────────
    const iaOverlayCanvas = document.getElementById("ia-overlay-canvas");
    const iaOverlayCtx    = iaOverlayCanvas ? iaOverlayCanvas.getContext("2d") : null;

    // Current frame poses (fetched alongside each annotated frame)
    let _iaCurrentPoses = [];  // [{bp, x, y, lh, color_idx}]
    let _iaNBodyparts   = 1;   // total bodyparts count (for palette)
    let _iaHoverBp      = null;

    // Replicate the server's HSV rainbow palette in JS for label colours
    function _iaHsvToRgb(h, s, v) {
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
    function _iaPaletteColor(idx, total) {
      return _iaHsvToRgb(idx / Math.max(total, 1), 0.9, 0.95);
    }

    // ── Shape-aware draw primitives for multi-layer overlay rendering ──
    function _drawCircleFilled(ctx, x, y, r, color) {
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fill();
    }
    function _drawDiamond(ctx, x, y, r, color) {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(x,     y - r);
      ctx.lineTo(x + r, y    );
      ctx.lineTo(x,     y + r);
      ctx.lineTo(x - r, y    );
      ctx.closePath();
      ctx.fill();
    }
    function _drawSquare(ctx, x, y, r, color) {
      ctx.strokeStyle = color; ctx.lineWidth = 2;
      ctx.strokeRect(x - r, y - r, 2 * r, 2 * r);
    }
    function _drawTriangle(ctx, x, y, r, color) {
      ctx.strokeStyle = color; ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x,        y - r);
      ctx.lineTo(x + r,    y + r);
      ctx.lineTo(x - r,    y + r);
      ctx.closePath();
      ctx.stroke();
    }
    const _SHAPE_FN = {
      "circle-filled": _drawCircleFilled,
      "diamond":       _drawDiamond,
      "square":        _drawSquare,
      "triangle":      _drawTriangle,
    };

    function _iaSyncCanvas() {
      if (!iaOverlayCanvas) return;
      // Match canvas buffer size to the *displayed* image size (not natural)
      const w = iaFrameImg.offsetWidth  || iaFrameImg.clientWidth  || 1;
      const h = iaFrameImg.offsetHeight || iaFrameImg.clientHeight || 1;
      if (iaOverlayCanvas.width !== w || iaOverlayCanvas.height !== h) {
        iaOverlayCanvas.width  = w;
        iaOverlayCanvas.height = h;
      }
    }

    // Draw all pose marker circles onto the overlay canvas.
    // Skips bodyparts in _iaHiddenParts (client-side visibility).
    // If _iaLocalEdits contains an override for the current frame+bodypart,
    // the edited position is used and the marker is drawn with an extra white ring.
    // The currently selected bodypart (_iaSelectedBp) gets a gold ring.
    // An edit with x=null/y=null means the marker was deleted — not drawn.
    function _iaDrawPoseMarkers() {
      if (!iaOverlayCtx || !_iaOverlayEnabled || !_iaCurrentPoses.length) return;
      const natW = iaFrameImg.naturalWidth  || 1;
      const natH = iaFrameImg.naturalHeight || 1;
      const sx   = iaOverlayCanvas.width  / natW;
      const sy   = iaOverlayCanvas.height / natH;
      const r    = Math.max(1, Math.round(_iaMarkerSize * Math.min(sx, sy)));
      // _iaLocalEdits may not exist yet (declared later in the module); guard with typeof
      const frameEdits = (typeof _iaLocalEdits !== "undefined")
        ? (_iaLocalEdits.get(_iaCurrentFrame) || {})
        : {};
      for (const pose of _iaCurrentPoses) {
        // Skip bodyparts hidden by per-bodypart visibility toggle
        if (_iaHiddenParts.has(pose.bp)) continue;
        const edited   = pose.bp in frameEdits;
        const editData = edited ? frameEdits[pose.bp] : null;
        // NaN/null edit means the marker was deleted — don't render it
        if (edited && (editData.x == null || editData.y == null)) continue;
        const cx = Math.round((edited ? editData.x : pose.x) * sx);
        const cy = Math.round((edited ? editData.y : pose.y) * sy);
        const color = _iaPaletteColor(pose.color_idx, _iaNBodyparts);
        iaOverlayCtx.beginPath();
        iaOverlayCtx.arc(cx, cy, r, 0, Math.PI * 2);
        iaOverlayCtx.fillStyle = color;
        iaOverlayCtx.fill();
        if (edited) {
          // White ring indicates an unsaved positional edit
          iaOverlayCtx.beginPath();
          iaOverlayCtx.arc(cx, cy, r + 3, 0, Math.PI * 2);
          iaOverlayCtx.strokeStyle = "#fff";
          iaOverlayCtx.lineWidth   = 1.5;
          iaOverlayCtx.stroke();
        }
        if (pose.bp === _iaSelectedBp) {
          // Gold ring indicates the currently selected bodypart
          iaOverlayCtx.beginPath();
          iaOverlayCtx.arc(cx, cy, r + (edited ? 6 : 3), 0, Math.PI * 2);
          iaOverlayCtx.strokeStyle = "#facc15";
          iaOverlayCtx.lineWidth   = 2;
          iaOverlayCtx.stroke();
        }
      }
    }

    function _iaDrawHoverLabel() {
      if (!iaOverlayCtx) return;
      // Re-paint via the multi-layer renderer so comparison-layer shapes are
      // preserved and the primary layer's visibility flag is honored. The old
      // path (clearRect + _iaDrawPoseMarkers) wiped comparison layers off the
      // canvas and re-drew the primary's poses as hardcoded filled circles
      // even when the primary layer was toggled hidden — so any mousemove,
      // marker-size change or chip-toggle after hiding the primary made the
      // comparison layer's shape disappear and replaced it with circles at
      // the primary's coordinates.
      _iaDrawCurrentFrame();
      // Hover label only makes sense when the primary layer is visible (its
      // poses drive _iaCurrentPoses + hit-testing).
      const primary = _iaPrimary();
      if (!primary || !primary.visible) return;
      if (!_iaHoverBp || !_iaCurrentPoses.length) return;
      const pose = _iaCurrentPoses.find(p => p.bp === _iaHoverBp);
      if (!pose) return;

      // Map video-native coords → canvas display coords
      const natW = iaFrameImg.naturalWidth  || 1;
      const natH = iaFrameImg.naturalHeight || 1;
      const sx   = iaOverlayCanvas.width  / natW;
      const sy   = iaOverlayCanvas.height / natH;
      const cx   = pose.x * sx;
      const cy   = pose.y * sy;

      const color = _iaPaletteColor(pose.color_idx, _iaNBodyparts);
      const r     = _iaMarkerSize + 2;          // slightly larger hit ring
      const bp    = pose.bp;

      iaOverlayCtx.font      = "bold 11px 'JetBrains Mono', monospace";
      const tw = iaOverlayCtx.measureText(bp).width;
      // Flip label to the left if it would clip the right edge
      const flip = (cx + r + tw + 12) > iaOverlayCanvas.width;
      const tx   = flip ? cx - r - tw - 10 : cx + r + 4;
      const ty   = cy + 4;
      iaOverlayCtx.fillStyle = "rgba(12,13,16,.75)";
      iaOverlayCtx.fillRect(tx - 2, ty - 11, tw + 6, 14);
      iaOverlayCtx.fillStyle = color;
      iaOverlayCtx.fillText(bp, tx + 1, ty);
    }

    function _iaHitTest(cx, cy) {
      if (!_iaCurrentPoses.length) return null;
      const natW  = iaFrameImg.naturalWidth  || 1;
      const natH  = iaFrameImg.naturalHeight || 1;
      const sx    = iaOverlayCanvas.width  / natW;
      const sy    = iaOverlayCanvas.height / natH;
      const hitR  = (_iaMarkerSize + 6) * Math.max(sx, sy);
      for (const pose of _iaCurrentPoses) {
        const dx = pose.x * sx - cx;
        const dy = pose.y * sy - cy;
        if (Math.sqrt(dx * dx + dy * dy) <= hitR) return pose.bp;
      }
      return null;
    }

    // ── Marker drag-and-edit state ────────────────────────────
    // _iaLocalEdits: Map<string, Map<bp, {x, y}>> — client-side overrides
    // keyed by frame number.  Mirrors the server-side JSON cache and provides
    // zero-latency feedback while dragging.
    const _iaLocalEdits = new Map();  // frameNumber (int) → {bp: {x, y}}

    let _iaDragBp      = null;   // body-part being dragged
    let _iaDragging    = false;

    // Marker-edit UI elements (may be null if not yet in DOM)
    const iaMarkerEditBanner  = document.getElementById("ia-marker-edit-banner");
    const iaMarkerEditCount   = document.getElementById("ia-marker-edit-count");
    const iaSaveAdjBtn        = document.getElementById("ia-save-adjustments-btn");
    const iaDiscardAdjBtn     = document.getElementById("ia-discard-adjustments-btn");

    function _iaEditCount() {
      return _iaLocalEdits.size;
    }

    function _iaUpdateEditBanner() {
      if (!iaMarkerEditBanner) return;
      // Force-hide while comparison layers are active — editing is disabled.
      if (!_iaIsEditable()) {
        iaMarkerEditBanner.classList.add("hidden");
        return;
      }
      const n = _iaEditCount();
      if (n === 0) {
        iaMarkerEditBanner.classList.add("hidden");
      } else {
        iaMarkerEditBanner.classList.remove("hidden");
        if (iaMarkerEditCount) iaMarkerEditCount.textContent = `${n} frame${n !== 1 ? "s" : ""} edited`;
      }
    }

    // Convert canvas-display coords back to video-native coords
    function _iaCanvasToVideo(cx, cy) {
      const natW = iaFrameImg.naturalWidth  || 1;
      const natH = iaFrameImg.naturalHeight || 1;
      const sx   = iaOverlayCanvas.width  / natW;
      const sy   = iaOverlayCanvas.height / natH;
      return { x: cx / sx, y: cy / sy };
    }

    // Hit-test that accounts for local-edit positions
    function _iaHitTestWithEdits(cx, cy) {
      if (!_iaCurrentPoses.length) return null;
      const natW  = iaFrameImg.naturalWidth  || 1;
      const natH  = iaFrameImg.naturalHeight || 1;
      const sx    = iaOverlayCanvas.width  / natW;
      const sy    = iaOverlayCanvas.height / natH;
      const hitR  = (_iaMarkerSize + 8) * Math.max(sx, sy);
      const frameEdits = _iaLocalEdits.get(_iaCurrentFrame) || {};

      for (const pose of _iaCurrentPoses) {
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
    async function _iaFlushMarkerEdit(frame, bp, x, y) {
      if (!_iaIsEditable()) return;     // edit disabled while compare layers active
      const layer = _iaPrimary();
      if (!layer) return;
      try {
        await fetch("/dlc/viewer/marker-edit", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ h5: layer.path, frame, bp, x, y }),
        });
      } catch (_) { /* non-critical; edit lives in local state */ }
    }

    // Delete a marker (set to NaN) in the server cache (fire-and-forget)
    async function _iaFlushMarkerDelete(frame, bp) {
      if (!_iaIsEditable()) return;     // edit disabled while compare layers active
      const layer = _iaPrimary();
      if (!layer) return;
      try {
        await fetch("/dlc/viewer/marker-edit", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ h5: layer.path, frame, bp, x: null, y: null }),
        });
      } catch (_) {}
    }

    // Sync local edits from the server's edit-cache on H5 load / page refresh
    async function _iaLoadEditCacheFromServer(h5Path) {
      try {
        const res  = await fetch(`/dlc/viewer/edit-cache?h5=${encodeURIComponent(h5Path)}`);
        if (!res.ok) return;
        const data = await res.json();
        _iaLocalEdits.clear();
        for (const [frameKey, bpEdits] of Object.entries(data.cache || {})) {
          const fn = parseInt(frameKey.split("_")[1], 10);
          if (!isNaN(fn)) _iaLocalEdits.set(fn, bpEdits);
        }
        _iaUpdateEditBanner();
      } catch (_) {}
    }

    if (iaOverlayCanvas) {
      iaOverlayCanvas.style.pointerEvents = "auto";

      // Click: select marker near cursor OR place selected bodypart
      iaOverlayCanvas.addEventListener("click", e => {
        if (!_iaOverlayEnabled || !_iaCurrentPoses.length) return;
        const rect = iaOverlayCanvas.getBoundingClientRect();
        const cx   = e.clientX - rect.left;
        const cy   = e.clientY - rect.top;
        const hit  = _iaHitTestWithEdits(cx, cy);
        if (hit) {
          _iaSelectBp(hit);
          return;
        }
        if (!_iaIsEditable()) return;     // edit disabled while compare layers active
        if (!_iaSelectedBp) return;
        const { x, y } = _iaCanvasToVideo(cx, cy);
        if (!_iaLocalEdits.has(_iaCurrentFrame)) _iaLocalEdits.set(_iaCurrentFrame, {});
        _iaLocalEdits.get(_iaCurrentFrame)[_iaSelectedBp] = { x, y };
        _iaSyncCanvas();
        iaOverlayCtx.clearRect(0, 0, iaOverlayCanvas.width, iaOverlayCanvas.height);
        _iaDrawPoseMarkers();
        _iaFlushMarkerEdit(_iaCurrentFrame, _iaSelectedBp, x, y);
        _iaUpdateEditBanner();
        _iaUpdateBpChipStatus();
      });

      // Mousedown on a marker → begin drag; otherwise ignored
      iaOverlayCanvas.addEventListener("mousedown", e => {
        if (!_iaIsEditable()) return;     // edit disabled while compare layers active
        if (!_iaOverlayEnabled || !_iaCurrentPoses.length || e.button !== 0) return;
        const rect = iaOverlayCanvas.getBoundingClientRect();
        const hit  = _iaHitTestWithEdits(e.clientX - rect.left, e.clientY - rect.top);
        if (!hit) return;
        e.preventDefault();
        _iaDragBp   = hit;
        _iaDragging = true;
        iaOverlayCanvas.style.cursor = "grabbing";
      });

      iaOverlayCanvas.addEventListener("mousemove", e => {
        if (!_iaOverlayEnabled) return;
        const rect = iaOverlayCanvas.getBoundingClientRect();
        const cx   = e.clientX - rect.left;
        const cy   = e.clientY - rect.top;

        if (_iaDragging && _iaDragBp) {
          if (!_iaIsEditable()) return;     // edit disabled while compare layers active
          const { x, y } = _iaCanvasToVideo(cx, cy);
          if (!_iaLocalEdits.has(_iaCurrentFrame)) _iaLocalEdits.set(_iaCurrentFrame, {});
          _iaLocalEdits.get(_iaCurrentFrame)[_iaDragBp] = { x, y };
          _iaSyncCanvas();
          iaOverlayCtx.clearRect(0, 0, iaOverlayCanvas.width, iaOverlayCanvas.height);
          _iaDrawPoseMarkers();
          return;
        }

        const hit = _iaHitTestWithEdits(cx, cy);
        if (hit !== _iaHoverBp) { _iaHoverBp = hit; _iaDrawHoverLabel(); }
        iaOverlayCanvas.style.cursor = hit ? "pointer" : (_iaSelectedBp && _iaOverlayEnabled ? "crosshair" : "default");
      });

      iaOverlayCanvas.addEventListener("mouseup", async e => {
        if (!_iaIsEditable()) return;     // edit disabled while compare layers active
        if (!_iaDragging || !_iaDragBp) return;
        _iaDragging = false;
        const rect  = iaOverlayCanvas.getBoundingClientRect();
        const { x, y } = _iaCanvasToVideo(e.clientX - rect.left, e.clientY - rect.top);
        await _iaFlushMarkerEdit(_iaCurrentFrame, _iaDragBp, x, y);
        _iaUpdateEditBanner();
        _iaUpdateBpChipStatus();
        _iaDragBp = null;
        iaOverlayCanvas.style.cursor = _iaSelectedBp ? "crosshair" : "default";
        _iaDrawHoverLabel();
      });

      iaOverlayCanvas.addEventListener("mouseleave", () => {
        if (_iaDragging && _iaDragBp) {
          const edits = _iaLocalEdits.get(_iaCurrentFrame);
          if (edits && _iaDragBp in edits) {
            const { x, y } = edits[_iaDragBp];
            _iaFlushMarkerEdit(_iaCurrentFrame, _iaDragBp, x, y);
            _iaUpdateEditBanner();
          }
          _iaDragging = false;
          _iaDragBp   = null;
        }
        if (_iaHoverBp) { _iaHoverBp = null; _iaDrawHoverLabel(); }
        iaOverlayCanvas.style.cursor = _iaSelectedBp ? "crosshair" : "default";
      });

      iaOverlayCanvas.addEventListener("mouseenter", () => {
        iaOverlayCanvas.style.cursor = _iaSelectedBp && _iaOverlayEnabled ? "crosshair" : "default";
      });

      // Right-click → delete (NaN) the currently selected marker
      iaOverlayCanvas.addEventListener("contextmenu", e => {
        e.preventDefault();
        if (!_iaIsEditable()) return;     // edit disabled while compare layers active
        if (!_iaOverlayEnabled || !_iaSelectedBp || !_iaPrimary()) return;
        if (!_iaLocalEdits.has(_iaCurrentFrame)) _iaLocalEdits.set(_iaCurrentFrame, {});
        _iaLocalEdits.get(_iaCurrentFrame)[_iaSelectedBp] = { x: null, y: null };
        _iaSyncCanvas();
        iaOverlayCtx.clearRect(0, 0, iaOverlayCanvas.width, iaOverlayCanvas.height);
        _iaDrawPoseMarkers();
        _iaFlushMarkerDelete(_iaCurrentFrame, _iaSelectedBp);
        _iaUpdateEditBanner();
        _iaUpdateBpChipStatus();
      });
    }

    // Save Adjustments button
    if (iaSaveAdjBtn) {
      iaSaveAdjBtn.addEventListener("click", async () => {
        if (!_iaIsEditable()) return;     // edit disabled while compare layers active
        const layer = _iaPrimary();
        if (!layer) return;
        iaSaveAdjBtn.disabled = true;
        iaSaveAdjBtn.textContent = "Saving…";
        try {
          const res  = await fetch("/dlc/viewer/save-marker-edits", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ h5: layer.path }),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
          _iaLocalEdits.clear();
          _iaClearPoseCache();
          _iaUpdateEditBanner();
          iaStatus.textContent = `Saved: ${data.frames_edited} frame(s), ${data.bodyparts_edited} keypoint(s) updated.`;
          iaStatus.className   = "fe-extract-status ok";
          // Reload poses for current frame from updated H5
          if (_iaOverlayEnabled) await _iaFetchPoses(_iaCurrentFrame);
        } catch (err) {
          iaStatus.textContent = `Save failed: ${err.message}`;
          iaStatus.className   = "fe-extract-status err";
        } finally {
          iaSaveAdjBtn.disabled    = false;
          iaSaveAdjBtn.innerHTML   =
            '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" style="margin-right:.3rem"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>Save Adjustments';
        }
      });
    }

    // Discard Adjustments button
    if (iaDiscardAdjBtn) {
      iaDiscardAdjBtn.addEventListener("click", async () => {
        if (!_iaIsEditable()) return;     // edit disabled while compare layers active
        const layer = _iaPrimary();
        if (!layer) return;
        _iaLocalEdits.clear();
        _iaClearPoseCache();
        _iaUpdateEditBanner();
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
        if (_iaOverlayEnabled) {
          _iaSyncCanvas();
          if (iaOverlayCtx) iaOverlayCtx.clearRect(0, 0, iaOverlayCanvas.width, iaOverlayCanvas.height);
          _iaFetchPoses(_iaCurrentFrame);
        }
      });
    }

    // Clear Frame button — double-click sets ALL markers on current frame to NaN
    const iaClearFrameBtn = document.getElementById("ia-clear-frame-btn");
    if (iaClearFrameBtn) {
      iaClearFrameBtn.addEventListener("dblclick", async () => {
        if (!_iaIsEditable()) return;     // edit disabled while compare layers active
        if (!_iaOverlayEnabled || !_iaPrimary() || !_iaCurrentPoses.length) return;
        const frameMap = {};
        for (const pose of _iaCurrentPoses) {
          frameMap[pose.bp] = { x: null, y: null };
          _iaFlushMarkerDelete(_iaCurrentFrame, pose.bp);
        }
        _iaLocalEdits.set(_iaCurrentFrame, frameMap);
        _iaSyncCanvas();
        iaOverlayCtx.clearRect(0, 0, iaOverlayCanvas.width, iaOverlayCanvas.height);
        _iaDrawPoseMarkers();
        _iaUpdateEditBanner();
      });
    }

    // Pose cache key (per layer) — encodes everything that affects pose data.
    function _iaPoseCacheKey(layer) {
      return `${layer.path}:${_iaLayerThreshold(layer).toFixed(2)}`;
    }

    // Fetch poses for one (layer, frame) pair into layer.posesCache.
    // Returns the cache entry {key, poses, n_bodyparts} or null on error.
    async function _iaFetchPosesForFrame(layer, frame) {
      const key    = _iaPoseCacheKey(layer);
      const cached = layer.posesCache.get(frame);
      if (cached && cached.key === key) return cached;
      const params = new URLSearchParams({
        h5:        layer.path,
        threshold: _iaLayerThreshold(layer).toFixed(2),
      });
      try {
        const r    = await fetch(`/dlc/viewer/frame-poses/${frame}?${params}`);
        const data = await r.json();
        if (!r.ok || data.error) { layer.errored = true; return null; }
        const entry = { key, poses: data.poses || [], n_bodyparts: data.n_bodyparts || 1 };
        layer.posesCache.set(frame, entry);
        return entry;
      } catch (e) { layer.errored = true; return null; }
    }

    // Fetch poses for the primary layer (drives current-frame state used by hit-testing).
    // Only called when paused — never during playback.
    async function _iaFetchPoses(frameNumber) {
      const primary = _iaPrimary();
      if (!primary || !_iaOverlayEnabled) return;
      const entry = await _iaFetchPosesForFrame(primary, frameNumber);
      if (entry) {
        _iaCurrentPoses = entry.poses;
        _iaNBodyparts   = entry.n_bodyparts;
      } else {
        _iaCurrentPoses = [];
      }
      // Also kick off comparison-layer fetches for the same frame so V/H markers appear.
      await Promise.all(
        _iaCompare()
          .filter(l => l.visible && !l.errored)
          .map(l => _iaFetchPosesForFrame(l, frameNumber))
      );
      _iaHoverBp = null;
      _iaDrawHoverLabel();
      _iaUpdateBpChipStatus();
      if (!_iaPrefetchCtrl) _iaPrefetchPoseWindow(frameNumber);
    }

    // Prefetch the next _POSE_WINDOW frames in the background, per visible layer.
    async function _iaPrefetchPoseWindow(fromFrame) {
      if (!_iaPrimary()) return;
      if (_iaPrefetchCtrl) return;
      _iaPrefetchCtrl = new AbortController();
      const ctrl = _iaPrefetchCtrl;
      try {
        await Promise.all(
          _iaLayers
            .filter(l => l.visible && !l.errored)
            .map(layer => _iaPrefetchOne(layer, fromFrame, ctrl.signal))
        );
      } finally {
        if (_iaPrefetchCtrl === ctrl) _iaPrefetchCtrl = null;
      }
    }

    async function _iaPrefetchOne(layer, fromFrame, signal) {
      const key = _iaPoseCacheKey(layer);
      // Skip if the next _POSE_WINDOW frames for this layer are already cached.
      let allCached = true;
      for (let i = fromFrame; i < fromFrame + _POSE_WINDOW && i < _iaFrameCount; i++) {
        const c = layer.posesCache.get(i);
        if (!c || c.key !== key) { allCached = false; break; }
      }
      if (allCached) return;
      const params = new URLSearchParams({
        h5:        layer.path,
        start:     String(fromFrame),
        count:     String(_POSE_WINDOW),
        threshold: _iaLayerThreshold(layer).toFixed(2),
      });
      try {
        const r = await fetch(`/dlc/viewer/frame-poses-batch?${params}`, { signal });
        if (!r.ok) return;
        const data = await r.json();
        for (const [fnStr, fd] of Object.entries(data.frames || {})) {
          const fn = parseInt(fnStr, 10);
          layer.posesCache.set(fn, {
            key,
            poses:       fd.poses || [],
            n_bodyparts: fd.n_bodyparts || 1,
          });
        }
      } catch (e) {
        if (e.name !== "AbortError") console.warn("pose prefetch failed:", e);
      }
    }

    // Backwards-compat wrapper for callers that still reference the old name.
    function _iaFetchPosesWindow(fromFrame) { return _iaPrefetchPoseWindow(fromFrame); }

    // ── Dataset Curation master toggle ────────────────────────
    const iaCurationToggle   = document.getElementById("ia-curation-toggle");
    const iaCurationControls = document.getElementById("ia-curation-controls");
    iaCurationToggle?.addEventListener("change", () => {
      iaCurationControls?.classList.toggle("hidden", !iaCurationToggle.checked);
    });

    // ── Kinematic overlay controls ────────────────────────────
    const iaOverlayToggle    = document.getElementById("ia-overlay-toggle");
    const iaOverlayControls  = document.getElementById("ia-overlay-controls");
    const iaOverlayStatus    = document.getElementById("ia-overlay-status");
    const iaOverlayH5Path    = document.getElementById("ia-overlay-h5-path");
    const iaOverlayH5Browse  = document.getElementById("ia-overlay-h5-browse");
    const iaOverlayH5Clear   = document.getElementById("ia-overlay-h5-clear");
    const iaOverlayH5Browser = document.getElementById("ia-overlay-h5-browser");
    const iaOverlayThreshold = document.getElementById("ia-overlay-threshold");
    const iaOverlayThreshVal = document.getElementById("ia-overlay-threshold-val");
    const iaOverlayMarkerSz  = document.getElementById("ia-overlay-marker-size");
    const iaOverlayMarkerVal = document.getElementById("ia-overlay-marker-size-val");
    const iaOverlayPartsAll  = document.getElementById("ia-overlay-parts-all");
    const iaOverlayPartsNone = document.getElementById("ia-overlay-parts-none");
    // Body-part chip list (below the canvas)
    const iaBpChips     = document.getElementById("ia-bp-chips");
    const iaBpListWrap  = document.getElementById("ia-bp-list-wrap");

    function _iaOverlayStatus(msg, isErr = false) {
      iaOverlayStatus.textContent = msg;
      iaOverlayStatus.className   = "fe-extract-status" + (isErr ? " err" : "");
    }

    async function _iaLoadH5Info(h5Path) {
      _iaSelectedBp = null;
      if (iaBpChips) iaBpChips.innerHTML = '<span style="color:var(--text-dim);font-size:.73rem">Loading…</span>';
      try {
        const res  = await fetch(`/dlc/viewer/h5-info?h5=${encodeURIComponent(h5Path)}`);
        const data = await res.json();
        if (data.error) { _iaOverlayStatus(data.error, true); return; }
        _iaAllBodyParts = data.bodyparts || [];
        _iaRebuildPartsChecklist();
        _iaOverlayStatus(`${data.frame_count.toLocaleString()} frames · ${_iaAllBodyParts.length} body parts`);
      } catch (e) {
        _iaOverlayStatus(`Failed to load h5 info: ${e.message}`, true);
      }
    }

    async function _iaLoadLayerInfo(layer) {
      // Replaces _iaLoadH5Info; populates layer.bodyparts in place.
      try {
        const r    = await fetch(`/dlc/viewer/h5-info?h5=${encodeURIComponent(layer.path)}`);
        const data = await r.json();
        if (!r.ok || data.error) { layer.errored = true; return; }
        layer.bodyparts = data.bodyparts || [];
        if (layer === _iaPrimary()) {
          // Keep the legacy globals in sync for any code path not yet migrated.
          _iaAllBodyParts = layer.bodyparts.slice();
          _iaNBodyparts   = _iaAllBodyParts.length;
        }
      } catch (e) { layer.errored = true; }
    }

    async function _iaLoadEditCacheForPrimary() {
      const layer = _iaPrimary();
      if (!layer) return;
      // Reuse the existing _iaLoadEditCacheFromServer path so the marker-edit
      // banner / edits map keep working unchanged.
      await _iaLoadEditCacheFromServer(layer.path);
    }

    // Select a bodypart: set active chip, update canvas cursor.
    function _iaSelectBp(bp) {
      _iaSelectedBp = bp;
      if (iaOverlayCanvas) iaOverlayCanvas.style.cursor = bp ? "crosshair" : "default";
      _iaUpdateBpChipStatus();
      iaCard.focus();
    }

    // Update chip .active / .labeled / .vis-hidden states.
    function _iaUpdateBpChipStatus() {
      if (!iaBpChips) return;
      const posedBps = new Set(_iaCurrentPoses.map(p => p.bp));
      iaBpChips.querySelectorAll(".fl-bp-chip").forEach(c => {
        const bp = c.dataset.bp;
        const hasEdit = (typeof _iaLocalEdits !== "undefined") &&
          _iaLocalEdits.has(_iaCurrentFrame) &&
          bp in _iaLocalEdits.get(_iaCurrentFrame) &&
          _iaLocalEdits.get(_iaCurrentFrame)[bp].x != null;
        const isLabeled  = posedBps.has(bp) || hasEdit;
        const isHidden   = _iaHiddenParts.has(bp);
        c.classList.toggle("active",     c.dataset.bp === _iaSelectedBp);
        c.classList.toggle("labeled",    isLabeled);
        c.classList.toggle("vis-hidden", isLabeled && isHidden);
      });
    }

    // Build the fl-bp-chip list in the panel below the canvas.
    function _iaRebuildPartsChecklist() {
      if (!iaBpChips) return;
      iaBpChips.innerHTML = "";
      if (!_iaAllBodyParts.length) {
        if (iaBpListWrap) iaBpListWrap.classList.add("hidden");
        return;
      }
      _iaAllBodyParts.forEach((bp, idx) => {
        const chip = document.createElement("button");
        chip.className  = "fl-bp-chip";
        chip.dataset.bp = bp;
        chip.style.setProperty("--fl-color", _iaPaletteColor(idx, _iaAllBodyParts.length));
        chip.innerHTML =
          `<span class="fl-bp-dot"></span>` +
          `<span class="fl-bp-name">${bp}</span>` +
          `<svg class="fl-bp-check" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>` +
          `<svg class="fl-bp-eye-slash" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`;
        chip.title = "Click: select  •  Dbl-click: toggle visibility";
        chip.addEventListener("click", () => _iaSelectBp(bp));
        chip.addEventListener("dblclick", e => {
          e.preventDefault();
          if (_iaHiddenParts.has(bp)) _iaHiddenParts.delete(bp);
          else _iaHiddenParts.add(bp);
          _iaDrawHoverLabel();
          _iaUpdateBpChipStatus();
        });
        iaBpChips.appendChild(chip);
      });
      if (iaBpListWrap) iaBpListWrap.classList.toggle("hidden", !_iaOverlayEnabled);
      _iaUpdateBpChipStatus();
      // Auto-select first bodypart (like the labeler does)
      if (_iaAllBodyParts.length && !_iaSelectedBp) _iaSelectBp(_iaAllBodyParts[0]);
    }

    // Cached on the page for re-populating after add/remove.
    let _iaLastVariants = [];

    function _iaPickBestPrimary(variants) {
      // Newest variant by ts wins. Raw companion has ts=null and is the
      // fallback when no dated variants exist.
      const dated = (variants || []).filter(v => !v.disabled && v.ts);
      if (dated.length) {
        return dated.reduce((a, b) => (a.ts > b.ts ? a : b));
      }
      return (variants || []).find(v => !v.disabled) || null;
    }

    function _iaPlayStep() {
      const v = parseInt(document.getElementById("ia-play-step")?.value || "1", 10);
      return Math.max(1, Math.min(100, isNaN(v) ? 1 : v));
    }

    function _iaPlaybackFps() {
      const v = parseInt(document.getElementById("ia-play-fps")?.value || "5", 10);
      return Math.max(1, Math.min(120, isNaN(v) ? 5 : v));
    }

    function _iaPlayDelayMs() {
      return Math.round(1000 / _iaPlaybackFps());
    }

    async function _iaDiscoverVariants(videoPath) {
      // Fetch every analyzable h5 near `videoPath` and populate the Primary <select>.
      // Default the primary to the first 'raw' entry, or the first variant otherwise.
      const select = document.getElementById("ia-overlay-primary-select");
      const addCmp = document.getElementById("ia-overlay-add-compare");
      if (!select || !addCmp) return;

      // Reset both controls to their empty states.
      select.innerHTML = '<option value="">(no h5 detected — use Browse)</option>';
      addCmp.innerHTML = '<option value="">+ add comparison…</option>';

      let data;
      try {
        const r = await fetch(`/dlc/viewer/h5-variants?video=${encodeURIComponent(videoPath)}`);
        data = await r.json();
        if (!r.ok || !Array.isArray(data.variants)) return;
      } catch (e) { return; }

      _iaLastVariants = data.variants;
      if (!data.variants.length) return;

      // Populate primary select.
      data.variants.forEach((v) => {
        const opt = document.createElement("option");
        opt.value = v.path;
        opt.textContent = v.label;
        if (v.disabled) opt.disabled = true;
        opt.dataset.type  = v.type;
        opt.dataset.label = v.label;
        select.appendChild(opt);
      });

      // Default selection.
      const defaultEntry = _iaPickBestPrimary(data.variants);
      if (!defaultEntry) return;
      select.value = defaultEntry.path;
      await _iaApplyPrimaryFromSelect();
      _iaSyncPrimaryRow();
      _iaRefreshAddComparisonOptions(data.variants);
    }

    function _iaRefreshAddComparisonOptions(variants) {
      const addCmp = document.getElementById("ia-overlay-add-compare");
      const hint   = document.getElementById("ia-overlay-add-compare-empty-hint");
      if (!addCmp) return;
      addCmp.innerHTML = '<option value="">+ add comparison…</option>';
      const taken = new Set(_iaLayers.map(l => l.path));
      const available = (variants || []).filter(v => !v.disabled && !taken.has(v.path));
      available.forEach((v) => {
        const opt = document.createElement("option");
        opt.value = v.path;
        opt.textContent = v.label;
        opt.dataset.type  = v.type;
        opt.dataset.label = v.label;
        addCmp.appendChild(opt);
      });
      // Show the dropdown only when at least one non-taken option exists;
      // otherwise show the inline "(no other variants)" hint.
      addCmp.classList.toggle("hidden", available.length === 0);
      if (hint) hint.classList.toggle("hidden", available.length > 0);
    }

    async function _iaApplyPrimaryFromSelect() {
      const select = document.getElementById("ia-overlay-primary-select");
      if (!select) return;
      const path  = select.value;
      if (!path) return;
      const opt   = select.options[select.selectedIndex];
      const label = opt?.dataset.label || path.split("/").pop();
      const type  = opt?.dataset.type  || "raw";

      // Primary swap = fresh slate. Drop every comparison layer.
      _iaLayers.length = 0;
      const layer = _iaMakeLayer({ path, label, type });
      _iaSetPrimaryLayer(layer);
      document.getElementById("ia-overlay-h5-path").value = path;
      await _iaLoadLayerInfo(layer);
      await _iaLoadEditCacheForPrimary();
      _iaRenderCompareRows();
      _iaRefreshAddComparisonOptions(_iaLastVariants);
      _iaRenderPrimaryThresholdInline();
      if (_iaOverlayEnabled) _iaLoadFrame(_iaCurrentFrame);
      _iaSyncPrimaryRow();
    }

    function _iaRenderPrimaryThresholdInline() {
      const host = document.getElementById("ia-overlay-primary-select");
      if (!host) return;
      let slot = document.getElementById("ia-overlay-primary-threshold-slot");
      if (!_iaPerLayerThresholds) {
        if (slot) slot.remove();
        return;
      }
      if (!slot) {
        slot = document.createElement("span");
        slot.id = "ia-overlay-primary-threshold-slot";
        slot.style.cssText = "display:flex;align-items:center;gap:.25rem;margin-left:.4rem";
        host.parentElement?.appendChild(slot);
      }
      slot.innerHTML = "";
      const layer = _iaPrimary();
      if (!layer) return;
      const slider = document.createElement("input");
      slider.type = "range"; slider.min = "0"; slider.max = "1"; slider.step = "0.05";
      slider.value = String(layer.threshold ?? _iaGlobalThreshold);
      slider.style.cssText = "width:60px;accent-color:var(--accent)";
      const lbl = document.createElement("span");
      lbl.style.cssText = "font-family:var(--mono);font-size:.7rem;min-width:2.2rem";
      lbl.textContent = Number(slider.value).toFixed(2);
      slider.addEventListener("input", () => {
        layer.threshold = Number(slider.value);
        lbl.textContent = layer.threshold.toFixed(2);
        if (_iaOverlayEnabled) {
          _iaFetchPosesForFrame(layer, _iaCurrentFrame).then(_iaDrawCurrentFrame);
        }
      });
      slot.appendChild(slider);
      slot.appendChild(lbl);
    }

    // ── Comparison-row UI ──────────────────────────────────────────
    function _shapeGlyph(shape) {
      switch (shape) {
        case "circle-filled": return "●";
        case "diamond":       return "◆";
        case "square":        return "□";
        case "triangle":      return "△";
        default:              return "?";
      }
    }

    function _iaRenderCompareRows() {
      const list = document.getElementById("ia-overlay-compare-list");
      if (!list) return;
      list.innerHTML = "";
      _iaCompare().forEach((layer) => {
        const row = document.createElement("div");
        row.id = `va-layer-row-${layer.id}`;
        row.style.cssText = "display:flex;align-items:center;gap:.35rem;font-size:.74rem;padding:.15rem .25rem;background:var(--surface);border:1px solid var(--border);border-radius:5px";
        // visibility checkbox
        const vis = document.createElement("input");
        vis.type = "checkbox";
        vis.checked = layer.visible;
        vis.style.cssText = "accent-color:var(--accent);width:12px;height:12px;flex-shrink:0";
        vis.addEventListener("change", () => {
          layer.visible = vis.checked;
          _iaDrawCurrentFrame();
        });
        row.appendChild(vis);
        // shape badge
        const badge = document.createElement("span");
        badge.textContent = _shapeGlyph(layer.shape);
        badge.style.cssText = "font-family:var(--mono);width:1.1rem;text-align:center;flex-shrink:0";
        row.appendChild(badge);
        // label
        const lbl = document.createElement("span");
        lbl.textContent = layer.label;
        lbl.style.cssText = "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
        row.appendChild(lbl);
        // per-layer threshold (rendered conditionally when Customize is on)
        const thrSlot = document.createElement("span");
        thrSlot.dataset.role = "threshold";
        thrSlot.style.cssText = "display:flex;align-items:center;gap:.25rem;flex-shrink:0";
        if (_iaPerLayerThresholds) {
          const slider = document.createElement("input");
          slider.type = "range"; slider.min = "0"; slider.max = "1"; slider.step = "0.05";
          slider.value = String(layer.threshold ?? _iaGlobalThreshold);
          slider.style.cssText = "width:60px;accent-color:var(--accent)";
          const lbl = document.createElement("span");
          lbl.style.cssText = "font-family:var(--mono);font-size:.7rem;min-width:2.2rem";
          lbl.textContent = Number(slider.value).toFixed(2);
          slider.addEventListener("input", () => {
            layer.threshold = Number(slider.value);
            lbl.textContent = layer.threshold.toFixed(2);
            if (_iaOverlayEnabled) {
              _iaFetchPosesForFrame(layer, _iaCurrentFrame).then(_iaDrawCurrentFrame);
            }
          });
          thrSlot.appendChild(slider);
          thrSlot.appendChild(lbl);
        }
        row.appendChild(thrSlot);
        // remove button
        const rm = document.createElement("button");
        rm.className = "btn-sm";
        rm.style.cssText = "padding:.05rem .35rem;font-size:.7rem;flex-shrink:0";
        rm.textContent = "×";
        rm.title = "Remove this comparison layer";
        rm.addEventListener("click", () => _iaRemoveCompare(layer.id));
        row.appendChild(rm);
        list.appendChild(row);
      });
      _iaUpdateEditDisabledBanner();
    }

    async function _iaAddCompare(path, label, type) {
      if (_iaLayers.some(l => l.path === path)) return;
      const layer = _iaMakeLayer({ path, label, type });
      _iaLayers.push(layer);
      _iaAssignShapes();
      await _iaLoadLayerInfo(layer);
      // Pre-fetch poses for the current frame so the new layer paints immediately.
      if (_iaOverlayEnabled) await _iaFetchPosesForFrame(layer, _iaCurrentFrame);
      _iaRenderCompareRows();
      _iaRefreshAddComparisonOptions(_iaLastVariants);
      _iaDrawCurrentFrame();
    }

    function _iaRemoveCompare(id) {
      const idx = _iaLayers.findIndex(l => l.id === id);
      if (idx < 1) return;  // never remove primary
      _iaLayers.splice(idx, 1);
      _iaAssignShapes();
      _iaRenderCompareRows();
      _iaRefreshAddComparisonOptions(_iaLastVariants);
      _iaDrawCurrentFrame();
    }

    function _iaUpdateEditDisabledBanner() {
      const banner = document.getElementById("ia-overlay-edit-disabled-banner");
      if (banner) banner.classList.toggle("hidden", _iaIsEditable());
      // Re-evaluate the marker-edit banner: when compare layers are active it
      // must be force-hidden regardless of unsaved-edit count.
      _iaUpdateEditBanner();
    }

    iaOverlayToggle?.addEventListener("change", () => {
      _iaOverlayEnabled = iaOverlayToggle.checked;
      iaOverlayControls.classList.toggle("hidden", !_iaOverlayEnabled);
      if (!_iaOverlayEnabled) {
        if (iaOverlayCtx) iaOverlayCtx.clearRect(0, 0, iaOverlayCanvas.width, iaOverlayCanvas.height);
        if (iaBpListWrap) iaBpListWrap.classList.add("hidden");
        if (iaOverlayCanvas) iaOverlayCanvas.style.cursor = "default";
        return;
      }
      if (_iaAllBodyParts.length && iaBpListWrap) iaBpListWrap.classList.remove("hidden");
      if (!_iaPrimary() && _iaCurrentVideoPath) _iaDiscoverVariants(_iaCurrentVideoPath);
      if (_iaPrimary() && !_iaPlayTimer) _iaFetchPoses(_iaCurrentFrame);
    });

    const iaOverlayPrimarySelect = document.getElementById("ia-overlay-primary-select");
    iaOverlayPrimarySelect?.addEventListener("change", _iaApplyPrimaryFromSelect);

    const iaOverlayAddCompare = document.getElementById("ia-overlay-add-compare");
    iaOverlayAddCompare?.addEventListener("change", async (e) => {
      const path = e.target.value;
      if (!path) return;
      const opt  = e.target.options[e.target.selectedIndex];
      await _iaAddCompare(path, opt.dataset.label, opt.dataset.type);
      e.target.value = "";  // reset to placeholder
    });

    const iaOverlayPrimaryVisible = document.getElementById("ia-overlay-primary-visible");
    const iaOverlayPrimaryShape   = document.getElementById("ia-overlay-primary-shape");
    const iaOverlayPrimaryLabel   = document.getElementById("ia-overlay-primary-label");

    iaOverlayPrimaryVisible?.addEventListener("change", () => {
      const layer = _iaPrimary();
      if (!layer) return;
      layer.visible = !!iaOverlayPrimaryVisible.checked;
      _iaDrawCurrentFrame();
    });

    function _iaSyncPrimaryRow() {
      const layer = _iaPrimary();
      if (!layer) {
        if (iaOverlayPrimaryShape) iaOverlayPrimaryShape.textContent = "—";
        if (iaOverlayPrimaryLabel) iaOverlayPrimaryLabel.textContent = "(no primary)";
        if (iaOverlayPrimaryVisible) iaOverlayPrimaryVisible.checked = false;
        return;
      }
      if (iaOverlayPrimaryShape) iaOverlayPrimaryShape.textContent = _shapeGlyph(layer.shape);
      if (iaOverlayPrimaryLabel) iaOverlayPrimaryLabel.textContent = layer.label || "";
      if (iaOverlayPrimaryVisible) iaOverlayPrimaryVisible.checked = !!layer.visible;
    }

    iaOverlayH5Clear?.addEventListener("click", () => {
      _iaSetPrimaryLayer(null);
      iaOverlayH5Path.value = "";
      _iaAllBodyParts = [];
      _iaHiddenParts.clear();
      _iaSelectedBp = null;
      _iaClearPoseCache();
      if (iaBpChips)    iaBpChips.innerHTML = "";
      if (iaBpListWrap) iaBpListWrap.classList.add("hidden");
      if (iaOverlayCanvas) iaOverlayCanvas.style.cursor = "default";
      _iaOverlayStatus("");
      _iaCurrentPoses = [];
      if (iaOverlayCtx) iaOverlayCtx.clearRect(0, 0, iaOverlayCanvas.width, iaOverlayCanvas.height);
    });

    // Threshold slider
    iaOverlayThreshold?.addEventListener("input", () => {
      _iaGlobalThreshold = Number(iaOverlayThreshold.value);
      iaOverlayThreshVal.textContent = _iaGlobalThreshold.toFixed(2);
      // Stale per-layer cache entries are auto-skipped by _iaFetchPosesForFrame
      // (key mismatch on threshold), so we just trigger a re-fetch of the
      // current frame.
      if (_iaOverlayEnabled) _iaLoadFrame(_iaCurrentFrame);
    });

    // Customize per-layer thresholds toggle
    const iaCustomizeThr = document.getElementById("ia-overlay-customize-thresholds");
    iaCustomizeThr?.addEventListener("change", () => {
      _iaPerLayerThresholds = iaCustomizeThr.checked;
      if (!_iaPerLayerThresholds) {
        // Forget per-layer overrides; revert to global.
        _iaLayers.forEach(l => l.threshold = null);
      } else {
        // Seed each layer's override with the current global so toggling on
        // produces no immediate visual change.
        _iaLayers.forEach(l => l.threshold = _iaGlobalThreshold);
      }
      _iaRenderCompareRows();
      _iaRenderPrimaryThresholdInline();
      if (_iaOverlayEnabled) _iaLoadFrame(_iaCurrentFrame);
    });

    // Marker size slider — redraw canvas immediately, no frame reload needed
    iaOverlayMarkerSz?.addEventListener("input", () => {
      _iaMarkerSize = parseInt(iaOverlayMarkerSz.value, 10);
      iaOverlayMarkerVal.textContent = _iaMarkerSize;
      _iaDrawHoverLabel();
    });

    iaOverlayPartsAll?.addEventListener("click", () => {
      _iaHiddenParts.clear();
      _iaDrawHoverLabel();
      _iaUpdateBpChipStatus();
    });
    iaOverlayPartsNone?.addEventListener("click", () => {
      _iaAllBodyParts.forEach(bp => _iaHiddenParts.add(bp));
      _iaDrawHoverLabel();
      _iaUpdateBpChipStatus();
    });

    // h5 file browser — canonical file-browser component, .h5 files only.
    // dblclick a .h5 row to select it (component leaves the browser open;
    // user closes via Browse-toggle when done).
    async function _iaPickH5(full) {
      const layer = _iaMakeLayer({
        path:  full,
        label: `Custom — ${full.split("/").pop()}`,
        type:  "raw",
      });
      _iaSetPrimaryLayer(layer);
      iaOverlayH5Path.value = full;
      _iaClearPoseCache();
      _iaOverlayStatus("h5 selected");
      await _iaLoadH5Info(full);
      await _iaLoadLayerInfo(layer);
      await _iaLoadEditCacheForPrimary();
      if (_iaOverlayEnabled) _iaLoadFrame(_iaCurrentFrame);
    }

    const iaH5Picker = iaOverlayH5Path && iaOverlayH5Browser ? makeFileBrowser({
      inputEl: iaOverlayH5Path,
      paneEl:  iaOverlayH5Browser,
      fileFilter: (name) => name.toLowerCase().endsWith(".h5"),
      onPick:  _iaPickH5,
    }) : null;

    iaOverlayH5Browse?.addEventListener("click", () => {
      const startDir = _iaCurrentVideoPath
        ? _iaCurrentVideoPath.substring(0, _iaCurrentVideoPath.lastIndexOf("/"))
        : (state.userDataDir || state.dataDir || "/");
      iaH5Picker?.openAt(startDir);
    });

    // ── Load content list ─────────────────────────────────────
    async function _iaLoadContent() {
      iaContentList.innerHTML = '<p class="explorer-empty">Loading…</p>';
      try {
        const res  = await fetch("/dlc/project/labeled-content");
        const data = await res.json();
        if (data.error) {
          iaContentList.innerHTML = `<p class="explorer-empty">${data.error}</p>`;
          return;
        }
        const hasVideos  = data.videos  && data.videos.length  > 0;
        const hasFolders = data.frame_folders && data.frame_folders.length > 0;
        if (!hasVideos && !hasFolders) {
          iaContentList.innerHTML = '<p class="explorer-empty">No labeled videos or frame folders found. Run "Analyze Video / Frames" with "Create labeled video / frame" enabled.</p>';
          return;
        }
        iaContentList.innerHTML = "";

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
          iaContentList.appendChild(hdr);
          data.videos.forEach(v => {
            const sub  = v.size ? Math.round(v.size / 1024 / 1024) + " MB" : "";
            const svg  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><rect x="2" y="2" width="20" height="20" rx="3"/><polygon points="10 8 16 12 10 16 10 8" fill="currentColor" stroke="none"/></svg>`;
            iaContentList.appendChild(_makeItem(svg, v.name, sub, () => _iaOpenVideo(v.name)));
          });
        }

        if (hasFolders) {
          const hdr = document.createElement("div");
          hdr.style.cssText = "font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);padding:.35rem .3rem .1rem";
          hdr.textContent   = "Labeled Frame Folders";
          iaContentList.appendChild(hdr);
          data.frame_folders.forEach(f => {
            const sub = f.frame_count + " labeled frames";
            const svg = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`;
            iaContentList.appendChild(_makeItem(svg, f.stem + "/", sub, () => _iaOpenFrameFolder(f.stem, f.frames)));
          });
        }
      } catch (err) {
        iaContentList.innerHTML = `<p class="explorer-empty">Error: ${err.message}</p>`;
      }
    }

    // ── Player controls ───────────────────────────────────────
    //
    // Strict sequential playback loop — replaces setInterval.
    //
    // setInterval would fire at a fixed wall-clock rate regardless of whether
    // the previous frame finished rendering.  When _iaFrameBusy is true the
    // tick is silently dropped, causing frame-skips and marker desync.
    //
    // Instead: each iteration awaits _iaLoadFrame() (which itself awaits the
    // image-load AND a requestAnimationFrame paint barrier) before scheduling
    // the next tick with setTimeout.  This guarantees both the raw image and
    // its overlay markers are fully composited before the engine advances.
    //
    // _iaPlayTimer is used as a boolean sentinel: truthy = playing.
    // _iaPlayTimeoutId holds the setTimeout handle for cancellation.
    let _iaPlayTimeoutId = null;

    function _iaStopPlayback() {
      if (_iaPlayTimeoutId !== null) { clearTimeout(_iaPlayTimeoutId); _iaPlayTimeoutId = null; }
      _iaPlayTimer = null;
      iaPlayIcon.classList.remove("hidden");
      iaPauseIcon.classList.add("hidden");
    }

    async function _iaPlayLoop() {
      // Guard: stop if externally cancelled between ticks
      if (!_iaPlayTimer) return;

      const next = _iaCurrentFrame + _iaPlayStep();
      if (next >= _iaFrameCount) {
        _iaStopPlayback();
        if (_iaOverlayEnabled && _iaPrimary()) _iaFetchPoses(_iaCurrentFrame);
        return;
      }

      const t0 = performance.now();
      await _iaLoadFrame(next);
      // If play was stopped while we were awaiting the frame, exit cleanly
      if (!_iaPlayTimer) return;

      // Pace the next tick: subtract actual render time from the target interval.
      // Clamped to 0 so a slow frame never makes us "owe" future ticks.
      const elapsed = performance.now() - t0;
      const delay   = Math.max(0, _iaPlayDelayMs() - elapsed);
      _iaPlayTimeoutId = setTimeout(_iaPlayLoop, delay);
    }

    iaBtnPlay.addEventListener("click", () => {
      if (_iaPlayTimer) {
        _iaStopPlayback();
        // Just paused: fetch and display poses for current frame
        if (_iaOverlayEnabled && _iaPrimary()) _iaFetchPoses(_iaCurrentFrame);
      } else {
        iaPlayIcon.classList.add("hidden");
        iaPauseIcon.classList.remove("hidden");
        _iaPlayTimer = true;   // sentinel: truthy = playing
        // Pre-warm pose cache before playback so the first N frames render with markers
        if (_iaOverlayEnabled && _iaPrimary()) _iaPrefetchPoseWindow(_iaCurrentFrame);
        _iaPlayLoop();
      }
    });

    iaBtnPrev.addEventListener("click", () => _iaLoadFrame(_iaCurrentFrame - 1));
    iaBtnNext.addEventListener("click", () => _iaLoadFrame(_iaCurrentFrame + 1));

    function _iaSkipN() { return Math.max(1, parseInt(iaSkipN?.value, 10) || 10); }
    iaBtnSkipBack?.addEventListener("click", () => _iaLoadFrame(_iaCurrentFrame - _iaSkipN()));
    iaBtnSkipFwd?.addEventListener("click",  () => _iaLoadFrame(_iaCurrentFrame + _iaSkipN()));
    // Prevent arrow keys from changing the skip-N field from triggering frame nav
    iaSkipN?.addEventListener("keydown", e => e.stopPropagation());

    iaSeek.addEventListener("mousedown",  () => { _iaSeekDragging = true; });
    iaSeek.addEventListener("touchstart", () => { _iaSeekDragging = true; });
    iaSeek.addEventListener("input", () => {
      _iaCurrentFrame = Math.round((iaSeek.value / 1000) * Math.max(_iaFrameCount - 1, 0));
      _iaUpdateDisplay();
    });
    iaSeek.addEventListener("change", () => { _iaSeekDragging = false; _iaLoadFrame(_iaCurrentFrame); });

    iaBackBtn.addEventListener("click", _iaReset);
    iaRefreshBtn.addEventListener("click", _iaLoadContent);

    // ── Open / close ──────────────────────────────────────────
    iaOpenBtn?.addEventListener("click", () => {
      iaCard.classList.remove("hidden");
      iaCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      _iaLoadContent();
    });

    iaCloseBtn?.addEventListener("click", () => {
      iaCard.classList.add("hidden");
      _iaReset();
    });

    // ── Keyboard navigation ───────────────────────────────────
    // Scoped to iaCard so it doesn't fire when another card has focus.
    iaCard.addEventListener("keydown", (e) => {
      if (iaPlayerSec.classList.contains("hidden")) return;
      // Don't intercept when typing in any input except the skip-N field
      if (e.target.tagName === "INPUT" && e.target !== iaSkipN) return;
      if (e.target.tagName === "TEXTAREA") return;

      const overlayActive = _iaOverlayEnabled && _iaPrimary() && _iaCurrentPoses.length > 0;

      // ── Spacebar: visibility toggle when overlay+bp selected, else play/pause ──
      if (e.key === " ") {
        e.preventDefault();
        if (overlayActive && _iaSelectedBp) {
          if (_iaHiddenParts.has(_iaSelectedBp)) _iaHiddenParts.delete(_iaSelectedBp);
          else _iaHiddenParts.add(_iaSelectedBp);
          _iaDrawHoverLabel();
          _iaUpdateBpChipStatus();
        } else {
          iaBtnPlay.click();
        }
        return;
      }

      // ── Frame navigation ──────────────────────────────────────────────────────
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        e.ctrlKey ? _iaLoadFrame(_iaCurrentFrame - _iaSkipN())
                  : _iaLoadFrame(_iaCurrentFrame - 1);
        return;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        e.ctrlKey ? _iaLoadFrame(_iaCurrentFrame + _iaSkipN())
                  : _iaLoadFrame(_iaCurrentFrame + 1);
        return;
      }

      // ── Tab: cycle bodyparts (only when overlay is active) ────────────────────
      if (e.key === "Tab" && overlayActive) {
        e.preventDefault();
        if (!_iaAllBodyParts.length) return;
        const idx = _iaAllBodyParts.indexOf(_iaSelectedBp);
        const next = e.shiftKey
          ? (_iaAllBodyParts.length + idx - 1) % _iaAllBodyParts.length
          : (idx + 1) % _iaAllBodyParts.length;
        _iaSelectBp(_iaAllBodyParts[next]);
        _iaDrawHoverLabel();
        return;
      }

      // ── Backspace/Delete: delete (NaN) selected marker ────────────────────────
      if ((e.key === "Backspace" || e.key === "Delete") && overlayActive && _iaSelectedBp) {
        e.preventDefault();
        if (!_iaLocalEdits.has(_iaCurrentFrame)) _iaLocalEdits.set(_iaCurrentFrame, {});
        _iaLocalEdits.get(_iaCurrentFrame)[_iaSelectedBp] = { x: null, y: null };
        _iaSyncCanvas();
        iaOverlayCtx.clearRect(0, 0, iaOverlayCanvas.width, iaOverlayCanvas.height);
        _iaDrawPoseMarkers();
        _iaFlushMarkerDelete(_iaCurrentFrame, _iaSelectedBp);
        _iaUpdateEditBanner();
        _iaUpdateBpChipStatus();
        return;
      }

      // ── WASD nudge: ±1px (±10px with Shift) when overlay+bp selected ─────────
      if (overlayActive && _iaSelectedBp) {
        const step = e.shiftKey ? 10 : 1;
        let dx = 0, dy = 0;
        if      (e.key === "a" || e.key === "A") dx = -step;
        else if (e.key === "d" || e.key === "D") dx =  step;
        else if (e.key === "w" || e.key === "W") dy = -step;
        else if (e.key === "s" || e.key === "S") dy =  step;
        if (dx !== 0 || dy !== 0) {
          e.preventDefault();
          const frameEdits = _iaLocalEdits.get(_iaCurrentFrame) || {};
          const pose = _iaCurrentPoses.find(p => p.bp === _iaSelectedBp);
          const base = frameEdits[_iaSelectedBp] || (pose ? { x: pose.x, y: pose.y } : null);
          if (base && base.x != null && base.y != null) {
            const nx = base.x + dx;
            const ny = base.y + dy;
            if (!_iaLocalEdits.has(_iaCurrentFrame)) _iaLocalEdits.set(_iaCurrentFrame, {});
            _iaLocalEdits.get(_iaCurrentFrame)[_iaSelectedBp] = { x: nx, y: ny };
            _iaSyncCanvas();
            iaOverlayCtx.clearRect(0, 0, iaOverlayCanvas.width, iaOverlayCanvas.height);
            _iaDrawPoseMarkers();
            _iaFlushMarkerEdit(_iaCurrentFrame, _iaSelectedBp, nx, ny);
            _iaUpdateEditBanner();
          }
          return;
        }
      }
    });
    // Make the card focusable so keydown fires when clicked inside it
    if (!iaCard.hasAttribute("tabindex")) iaCard.setAttribute("tabindex", "-1");

    // ── Dataset Curation ──────────────────────────────────────────
    (() => {
      const iaCurationStatus  = document.getElementById("ia-curation-status");
      const iaExtractFrameBtn = document.getElementById("ia-extract-frame-btn");
      const iaAddToDatasetBtn = document.getElementById("ia-add-to-dataset-btn");
      const iaBatchAddBtn     = document.getElementById("ia-batch-add-btn");
      const iaBatchCount      = document.getElementById("ia-batch-count");
      const iaBatchStep       = document.getElementById("ia-batch-step");
      const iaCsvNone         = document.getElementById("ia-csv-none");
      const iaCsvLoaded       = document.getElementById("ia-csv-loaded");
      const iaCsvPathDisplay  = document.getElementById("ia-csv-path-display");
      const iaCreateCsvBtn    = document.getElementById("ia-create-csv-btn");
      const iaCsvCreateStatus = document.getElementById("ia-csv-create-status");
      const iaCsvBars         = document.getElementById("ia-csv-bars");
      const iaStatusBarWrap   = document.getElementById("ia-status-bar-wrap");
      const iaNoteBarWrap     = document.getElementById("ia-note-bar-wrap");
      const iaStatusCanvas    = document.getElementById("ia-status-canvas");
      const iaNoteCanvas      = document.getElementById("ia-note-canvas");
      const iaStatusChips     = document.getElementById("ia-status-chips");
      const iaNoteChips       = document.getElementById("ia-note-chips");
      const iaAnnotPanel      = document.getElementById("ia-annot-panel");
      const iaAnnotFrameNum   = document.getElementById("ia-annot-frame-num");
      const iaNoteInput       = document.getElementById("ia-note-input");
      const iaStatusInput     = document.getElementById("ia-status-input");
      const iaSaveStatusBtn   = document.getElementById("ia-save-status-btn");
      const iaSaveNoteBtn     = document.getElementById("ia-save-note-btn");
      const iaAnnotSaveStatus = document.getElementById("ia-annot-save-status");
      const iaStatusPrevBtn   = document.getElementById("ia-status-prev-btn");
      const iaStatusNextBtn   = document.getElementById("ia-status-next-btn");
      const iaNoteStepPrevBtn = document.getElementById("ia-note-prev-btn");
      const iaNoteStepNextBtn = document.getElementById("ia-note-next-btn");
      const iaNewTagInput     = document.getElementById("ia-new-tag-input");
      const iaAddTagBtn       = document.getElementById("ia-add-tag-btn");

      // Companion CSV state
      let _iaCsvPath          = null;
      let _iaCsvRows          = [];     // {frame_number, timestamp, frame_line_status, note}
      let _iaUserTags         = [];
      let _iaUserStatuses     = [];
      let _iaActiveNoteFilter = null;

      // Per-chip active sets and color maps (populated when chips are rendered)
      let _iaActiveNoteChips   = new Set();
      let _iaActiveStatusChips = new Set();
      let _iaNoteColorMap      = {};
      let _iaStatusColorMap    = {};

      // Color palettes — status uses warm/green tones, notes use cool/blue tones
      const _VA_STATUS_COLORS = ["#34d399","#f97316","#e879f9","#facc15","#f87171","#22d3ee","#a78bfa","#fb923c"];
      const _VA_NOTE_COLORS   = ["#60a5fa","#f472b6","#4ade80","#38bdf8","#e879f9","#a78bfa","#facc15","#fb7185"];

      // ── Status helpers ──────────────────────────────────────────
      let _curationMsgTimer = null;
      function _curStatus(msg, isErr) {
        if (!iaCurationStatus) return;
        iaCurationStatus.textContent = msg;
        iaCurationStatus.className   = "fe-extract-status" + (isErr ? " err" : "");
        if (_curationMsgTimer) clearTimeout(_curationMsgTimer);
        if (msg && !isErr) {
          _curationMsgTimer = setTimeout(() => {
            iaCurationStatus.textContent = "";
          }, 4000);
        }
      }

      // ── Build request body helper ───────────────────────────────
      function _videoRequestBody(frameNum) {
        const n = (frameNum !== undefined) ? frameNum : _iaCurrentFrame;
        const body = { frame_number: n };
        if (_iaMode === "browse-video" && _iaCurrentVideoPath) {
          body.video_path = _iaCurrentVideoPath;
        } else if (_iaMode === "video" && _iaVideoName) {
          body.video_name = _iaVideoName;
        }
        return body;
      }

      // ── Extract Frame ────────────────────────────────────────────
      if (iaExtractFrameBtn) {
        iaExtractFrameBtn.addEventListener("click", async () => {
          if (!_iaMode || _iaMode === "frames") {
            _curStatus("No video loaded — open a video first.", true); return;
          }
          iaExtractFrameBtn.disabled = true;
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
            iaExtractFrameBtn.disabled = false;
          }
        });
      }

      // ── Add to Dataset ────────────────────────────────────────────
      if (iaAddToDatasetBtn) {
        iaAddToDatasetBtn.addEventListener("click", async () => {
          if (!_iaMode || _iaMode === "frames") {
            _curStatus("No video loaded — open a video first.", true); return;
          }
          iaAddToDatasetBtn.disabled = true;
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
            iaAddToDatasetBtn.disabled = false;
          }
        });
      }

      // ── Batch Add ─────────────────────────────────────────────────
      if (iaBatchAddBtn) {
        iaBatchAddBtn.addEventListener("click", async () => {
          if (!_iaMode || _iaMode === "frames") {
            _curStatus("No video loaded — open a video first.", true); return;
          }
          const count = Math.max(1, parseInt(iaBatchCount?.value) || 10);
          const step  = Math.max(1, parseInt(iaBatchStep?.value)  || 30);
          iaBatchAddBtn.disabled = true;
          let added = 0, dupes = 0, errors = 0;
          const start = _iaCurrentFrame;
          let lastFrame = start;
          for (let i = 0; i < count; i++) {
            const frameNum = start + i * step;
            if (frameNum >= _iaFrameCount) break;
            lastFrame = frameNum;
            _curStatus(`Batch adding… ${i + 1}/${count} (frame ${frameNum})`);
            // Navigate player to the frame being extracted
            await _iaLoadFrame(frameNum);
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
          if (lastFrame !== _iaCurrentFrame) await _iaLoadFrame(lastFrame);
          iaBatchAddBtn.disabled = false;
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
      function _iaDrawCanvas(canvas, rows, field, activeSet, colorMap) {
        if (!canvas) return;
        const total = Math.max(_iaFrameCount, 1);
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

      function _iaRedrawNoteCanvas()   { _iaDrawCanvas(iaNoteCanvas,   _iaCsvRows, "note",              _iaActiveNoteChips,   _iaNoteColorMap);   }
      function _iaRedrawStatusCanvas() { _iaDrawCanvas(iaStatusCanvas, _iaCsvRows, "frame_line_status", _iaActiveStatusChips, _iaStatusColorMap); }

      function _iaBuildCsvBars() {
        if (!iaCsvBars) return;
        const hasNote   = _iaCsvRows.some(r => r.note);
        const hasStatus = _iaCsvRows.some(r => r.frame_line_status && r.frame_line_status !== "0");
        iaCsvBars.classList.toggle("hidden", !hasNote && !hasStatus);
        iaNoteBarWrap?.classList.toggle("hidden", !hasNote);
        iaStatusBarWrap?.classList.toggle("hidden", !hasStatus);
        // Canvases start empty; chips toggle individual values onto them.
        _iaRedrawNoteCanvas();
        _iaRedrawStatusCanvas();
      }

      // Click on either canvas — map x position to frame number and jump.
      [iaNoteCanvas, iaStatusCanvas].forEach(canvas => {
        if (!canvas) return;
        canvas.addEventListener("click", e => {
          const rect = canvas.getBoundingClientRect();
          const fn = Math.round((e.clientX - rect.left) / rect.width * Math.max(_iaFrameCount - 1, 0));
          _iaLoadFrame(fn);
        });
      });

      // Prev/next navigation within the active chip set for a given field.
      function _iaNavAnnot(field, activeSet, dir) {
        if (!activeSet.size) return;
        const frames = _iaCsvRows
          .filter(r => { const v = r[field]; return v && (field !== "frame_line_status" || v !== "0") && activeSet.has(v); })
          .map(r => r.frame_number)
          .sort((a, b) => a - b);
        if (!frames.length) return;
        if (dir < 0) {
          const prev = [...frames].reverse().find(f => f < _iaCurrentFrame);
          if (prev != null) _iaLoadFrame(prev);
        } else {
          const next = frames.find(f => f > _iaCurrentFrame);
          if (next != null) _iaLoadFrame(next);
        }
      }

      if (iaStatusPrevBtn) iaStatusPrevBtn.addEventListener("click", () => _iaNavAnnot("frame_line_status", _iaActiveStatusChips, -1));
      if (iaStatusNextBtn) iaStatusNextBtn.addEventListener("click", () => _iaNavAnnot("frame_line_status", _iaActiveStatusChips,  1));
      if (iaNoteStepPrevBtn) iaNoteStepPrevBtn.addEventListener("click", () => _iaNavAnnot("note", _iaActiveNoteChips, -1));
      if (iaNoteStepNextBtn) iaNoteStepNextBtn.addEventListener("click", () => _iaNavAnnot("note", _iaActiveNoteChips,  1));

      // ── Companion CSV helpers ────────────────────────────────────

      function _iaCsvSyncPanel() {
        if (!_iaCsvPath) return;
        if (iaAnnotFrameNum) iaAnnotFrameNum.textContent = _iaCurrentFrame;
        const row = _iaCsvRows.find(r => r.frame_number === _iaCurrentFrame);
        if (iaNoteInput)   iaNoteInput.value   = row ? (row.note || "") : "";
        if (iaStatusInput) iaStatusInput.value = row ? (row.frame_line_status ?? "0") : "0";
      }

      function _iaCsvApplyRows(rows, csvPath) {
        _iaCsvPath  = csvPath;
        _iaCsvRows  = rows;
        const noteVals   = [...new Set(rows.map(r => r.note).filter(v => v))];
        const statusVals = [...new Set(rows.map(r => r.frame_line_status).filter(v => v && v !== "0"))];
        _iaUserTags     = [...new Set([..._iaUserTags,     ...noteVals])];
        _iaUserStatuses = [...new Set([..._iaUserStatuses, ...statusVals])];

        if (iaCsvNone)        iaCsvNone.classList.add("hidden");
        if (iaCsvLoaded)      iaCsvLoaded.classList.remove("hidden");
        if (iaCsvPathDisplay) { iaCsvPathDisplay.textContent = csvPath; iaCsvPathDisplay.title = csvPath; }
        if (iaAnnotPanel)     iaAnnotPanel.classList.remove("hidden");

        _iaBuildCsvBars();
        _iaCsvRenderStatusChips();
        _iaCsvRenderTags();
        _iaCsvSyncPanel();
      }

      function _iaCsvRenderStatusChips() {
        if (!iaStatusChips) return;
        iaStatusChips.innerHTML = "";
        _iaStatusColorMap = {};
        _iaUserStatuses.forEach((val, i) => {
          const color = _VA_STATUS_COLORS[i % _VA_STATUS_COLORS.length];
          _iaStatusColorMap[val] = color;
          const chip = document.createElement("span");
          chip.className = "fe-tag-chip" + (_iaActiveStatusChips.has(val) ? " active" : "");
          chip.textContent = val;
          chip.style.setProperty("--chip-color", color);
          chip.title = `Click to show/hide "${val}" on timeline`;
          chip.addEventListener("click", () => {
            if (_iaActiveStatusChips.has(val)) _iaActiveStatusChips.delete(val);
            else _iaActiveStatusChips.add(val);
            _iaCsvRenderStatusChips();
            _iaRedrawStatusCanvas();
          });
          iaStatusChips.appendChild(chip);
        });
        const hasActive = _iaActiveStatusChips.size > 0;
        if (iaStatusPrevBtn) iaStatusPrevBtn.disabled = !hasActive;
        if (iaStatusNextBtn) iaStatusNextBtn.disabled = !hasActive;
      }

      function _iaCsvRenderTags() {
        if (!iaNoteChips) return;
        iaNoteChips.innerHTML = "";
        _iaNoteColorMap = {};
        _iaUserTags.forEach((tag, i) => {
          const color = _VA_NOTE_COLORS[i % _VA_NOTE_COLORS.length];
          _iaNoteColorMap[tag] = color;
          const chip = document.createElement("span");
          chip.className = "fe-tag-chip" + (_iaActiveNoteChips.has(tag) ? " active" : "");
          chip.textContent = tag;
          chip.style.setProperty("--chip-color", color);
          chip.title = `Click to show/hide "${tag}" on timeline`;
          chip.addEventListener("click", () => {
            if (_iaActiveNoteChips.has(tag)) _iaActiveNoteChips.delete(tag);
            else _iaActiveNoteChips.add(tag);
            _iaCsvRenderTags();
            _iaRedrawNoteCanvas();
          });
          iaNoteChips.appendChild(chip);
        });
        const hasActive = _iaActiveNoteChips.size > 0;
        if (iaNoteStepPrevBtn) iaNoteStepPrevBtn.disabled = !hasActive;
        if (iaNoteStepNextBtn) iaNoteStepNextBtn.disabled = !hasActive;
      }

      async function _iaCsvSaveStatus() {
        if (!_iaCsvPath) return;
        // Read existing note for this frame so saving status doesn't wipe it
        const existingRow = _iaCsvRows.find(r => r.frame_number === _iaCurrentFrame);
        const note   = iaNoteInput ? iaNoteInput.value.trim() : (existingRow?.note || "");
        const status = iaStatusInput ? (iaStatusInput.value || "0") : "0";
        await _iaCsvDoSave(note, status);
      }

      async function _iaCsvSaveNote() {
        if (!_iaCsvPath) return;
        // Read existing status for this frame so saving note doesn't wipe it
        const existingRow = _iaCsvRows.find(r => r.frame_number === _iaCurrentFrame);
        const note   = iaNoteInput ? iaNoteInput.value.trim() : "";
        const status = iaStatusInput ? (iaStatusInput.value || "0") : (existingRow?.frame_line_status || "0");
        await _iaCsvDoSave(note, status);
      }

      async function _iaCsvDoSave(note, status) {
        if (iaAnnotSaveStatus) { iaAnnotSaveStatus.textContent = "Saving…"; iaAnnotSaveStatus.className = "fe-extract-status"; }
        try {
          const res  = await fetch("/annotate/save-row", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({
              csv_path:          _iaCsvPath,
              frame_number:      _iaCurrentFrame,
              note,
              frame_line_status: status,
              fps:               _iaFps,
            }),
          });
          const data = await res.json();
          if (data.error) throw new Error(data.error);

          const isInteresting = note || (status && status !== "0");
          const idx = _iaCsvRows.findIndex(r => r.frame_number === _iaCurrentFrame);
          if (isInteresting) {
            const savedRow = data.row || { frame_number: _iaCurrentFrame, timestamp: (_iaCurrentFrame / _iaFps).toFixed(3), frame_line_status: status, note };
            if (idx >= 0) _iaCsvRows[idx] = savedRow;
            else { _iaCsvRows.push(savedRow); _iaCsvRows.sort((a, b) => a.frame_number - b.frame_number); }
            if (note && !_iaUserTags.includes(note)) { _iaUserTags.push(note); _iaCsvRenderTags(); }
            if (status && status !== "0" && !_iaUserStatuses.includes(status)) { _iaUserStatuses.push(status); _iaCsvRenderStatusChips(); }
          } else {
            if (idx >= 0) _iaCsvRows.splice(idx, 1);
          }

          _iaBuildCsvBars();

          if (iaAnnotSaveStatus) {
            iaAnnotSaveStatus.textContent = "Saved";
            iaAnnotSaveStatus.className   = "fe-extract-status ok";
            setTimeout(() => { if (iaAnnotSaveStatus?.textContent === "Saved") iaAnnotSaveStatus.textContent = ""; }, 2000);
          }
        } catch (err) {
          if (iaAnnotSaveStatus) { iaAnnotSaveStatus.textContent = `Error: ${err.message}`; iaAnnotSaveStatus.className = "fe-extract-status err"; }
        }
      }

      async function _iaCsvLoad(videoPath) {
        // Reset CSV state
        _iaCsvPath = null; _iaCsvRows = []; _iaUserTags = []; _iaUserStatuses = []; _iaActiveNoteFilter = null;
        _iaActiveNoteChips = new Set(); _iaActiveStatusChips = new Set();
        _iaNoteColorMap = {}; _iaStatusColorMap = {};
        if (iaCsvNone)        iaCsvNone.classList.remove("hidden");
        if (iaCsvLoaded)      iaCsvLoaded.classList.add("hidden");
        if (iaCsvBars)        iaCsvBars.classList.add("hidden");
        if (iaAnnotPanel)     iaAnnotPanel.classList.add("hidden");
        if (iaCsvCreateStatus) iaCsvCreateStatus.textContent = "";

        if (!videoPath) return;
        try {
          const res  = await fetch(`/annotate/csv?path=${encodeURIComponent(videoPath)}`);
          const data = await res.json();
          if (data.csv_exists) {
            _iaCsvApplyRows(data.rows, data.csv_path);
          }
        } catch (_) {}
      }

      // Hook into frame navigation
      _iaCurationFrameHook = () => {
        _iaCsvSyncPanel();
      };

      // Load companion CSV when player section becomes visible (video opened)
      if (typeof MutationObserver !== "undefined" && iaPlayerSec) {
        new MutationObserver(async () => {
          if (!iaPlayerSec.classList.contains("hidden") && _iaCurrentVideoPath) {
            await _iaCsvLoad(_iaCurrentVideoPath);
          } else if (iaPlayerSec.classList.contains("hidden")) {
            _iaCsvPath = null; _iaCsvRows = []; _iaUserTags = []; _iaUserStatuses = []; _iaActiveNoteFilter = null;
            _iaActiveNoteChips = new Set(); _iaActiveStatusChips = new Set();
            _iaNoteColorMap = {}; _iaStatusColorMap = {};
            if (iaCsvNone)    iaCsvNone.classList.remove("hidden");
            if (iaCsvLoaded)  iaCsvLoaded.classList.add("hidden");
            if (iaCsvBars)    iaCsvBars.classList.add("hidden");
            if (iaAnnotPanel) iaAnnotPanel.classList.add("hidden");
          }
        }).observe(iaPlayerSec, { attributes: true, attributeFilter: ["class"] });
      }

      // Create CSV
      if (iaCreateCsvBtn) {
        iaCreateCsvBtn.addEventListener("click", async () => {
          if (!_iaCurrentVideoPath) return;
          if (iaCsvCreateStatus) { iaCsvCreateStatus.textContent = `Creating CSV for ${_iaFrameCount} frames…`; iaCsvCreateStatus.className = "fe-extract-status"; }
          try {
            const res  = await fetch("/annotate/create-csv", {
              method:  "POST",
              headers: { "Content-Type": "application/json" },
              body:    JSON.stringify({ video_path: _iaCurrentVideoPath, fps: _iaFps, frame_count: _iaFrameCount }),
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);
            if (iaCsvCreateStatus) iaCsvCreateStatus.textContent = "";
            _iaCsvApplyRows(data.rows, data.csv_path);
          } catch (err) {
            if (iaCsvCreateStatus) { iaCsvCreateStatus.textContent = `Error: ${err.message}`; iaCsvCreateStatus.className = "fe-extract-status err"; }
          }
        });
      }

      // Save annotation
      if (iaSaveStatusBtn) {
        iaSaveStatusBtn.addEventListener("click", _iaCsvSaveStatus);
      }
      if (iaSaveNoteBtn) {
        iaSaveNoteBtn.addEventListener("click", _iaCsvSaveNote);
      }

      // Add new tag
      if (iaAddTagBtn) {
        iaAddTagBtn.addEventListener("click", () => {
          const tag = iaNewTagInput ? iaNewTagInput.value.trim() : "";
          if (!tag) return;
          if (!_iaUserTags.includes(tag)) { _iaUserTags.push(tag); _iaCsvRenderTags(); }
          if (iaNewTagInput) iaNewTagInput.value = "";
        });
      }
      if (iaNewTagInput) {
        iaNewTagInput.addEventListener("keydown", e => {
          if (e.key === "Enter") { e.preventDefault(); iaAddTagBtn?.click(); }
        });
      }

    })(); // end Dataset Curation

    // ── Video Metadata Panel (companion CSV viewer) ────────────────────────
    (() => {
      const iaMetaCsvInfo   = document.getElementById("ia-meta-csv-info");
      const iaMetaFrameRow  = document.getElementById("ia-meta-frame-row");
      const iaMetaFrameNote = document.getElementById("ia-meta-frame-note");
      const iaMetaFrameStat = document.getElementById("ia-meta-frame-status");
      if (!iaMetaCsvInfo) return;

      let _metaCsvRows = [];

      function _metaClear() {
        _metaCsvRows = [];
        iaMetaCsvInfo.textContent = "No companion CSV";
        iaMetaFrameRow.style.display = "none";
      }

      function _metaShowFrame(n) {
        if (!_metaCsvRows.length) { iaMetaFrameRow.style.display = "none"; return; }
        const row = _metaCsvRows.find(r => r.frame_number === n);
        const hasContent = row && (row.note || (row.frame_line_status && row.frame_line_status !== "0"));
        if (!hasContent) { iaMetaFrameRow.style.display = "none"; return; }
        iaMetaFrameRow.style.display = "flex";
        iaMetaFrameNote.textContent = row.note || "—";
        iaMetaFrameStat.textContent = row.frame_line_status || "0";
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
          iaMetaCsvInfo.textContent = _metaCsvRows.length
            ? `${fname} · ${_metaCsvRows.length.toLocaleString()} annotated frames`
            : `${fname} · no annotations yet`;
        } catch (_) {}
      }

      // Hook into frame navigation (outer scope variable set above)
      _iaMetadataFrameHook = n => _metaShowFrame(n);

      // Watch iaPlayerSec visibility to auto-load the companion CSV
      if (typeof MutationObserver !== "undefined" && iaPlayerSec) {
        new MutationObserver(async () => {
          if (!iaPlayerSec.classList.contains("hidden") && _iaCurrentVideoPath) {
            await _metaLoad(_iaCurrentVideoPath);
            _metaShowFrame(_iaCurrentFrame);
          } else if (iaPlayerSec.classList.contains("hidden")) {
            _metaClear();
          }
        }).observe(iaPlayerSec, { attributes: true, attributeFilter: ["class"] });
      }
    })(); // end Video Metadata Panel


    // ════════════════════════════════════════════════════════════════════
    //  ANALYSIS DISPATCH — submit a range to the warm worker and, when it
    //  finishes, programmatically refresh the h5-variant dropdown the
    //  cloned viewer code already uses. That triggers the same proven
    //  marker-render path that View-Analyzed runs.
    // ════════════════════════════════════════════════════════════════════
    (function () {
      const iaSnapshotSel  = document.getElementById("ia-snapshot");
      const iaShuffle      = document.getElementById("ia-shuffle");
      const iaTSI          = document.getElementById("ia-trainingsetindex");
      const iaBatchSize    = document.getElementById("ia-batch-size");
      const iaFramesPerCk  = document.getElementById("ia-frames-per-click");
      const iaKeepWarm     = document.getElementById("ia-keep-warm-seconds");
      const iaSaveCsv      = document.getElementById("ia-save-csv");
      const iaBtnAnalyze   = document.getElementById("ia-btn-analyze-range");
      const iaLastRun      = document.getElementById("ia-last-run-status");
      const iaWarmInd      = document.getElementById("ia-warm-indicator");
      const iaRefreshSnaps = document.getElementById("ia-refresh-snapshots");

      if (!iaBtnAnalyze) return;   // markup missing — bail silently

      let _iaSnapKey       = null;
      let _iaActiveReqId   = null;
      let _iaActiveReqPoll = null;
      let _iaStatusPoll    = null;

      // ── Snapshot loader: mirrors analyze.js's _avLoadSnapshots ──────
      async function _iaLoadSnapshots() {
        try {
          const r = await fetch("/dlc/project/snapshots");
          const data = await r.json();
          if (data.error || !iaSnapshotSel) return;
          iaSnapshotSel.innerHTML = "";
          const latestOpt = document.createElement("option");
          latestOpt.value = data.latest_rel_path || "-1";
          if (data.latest_label) {
            const iterStr = data.latest_iteration != null
              ? `  ·  iter ${data.latest_iteration.toLocaleString()}` : "";
            const shStr   = data.latest_shuffle   != null
              ? `  ·  sh${data.latest_shuffle}`   : "";
            latestOpt.textContent = `Latest — ${data.latest_label}${iterStr}${shStr}`;
          } else {
            latestOpt.textContent = "Latest (from config)";
          }
          iaSnapshotSel.appendChild(latestOpt);
          (data.snapshots || []).forEach((s) => {
            const opt = document.createElement("option");
            opt.value = s.rel_path;
            const iterStr = s.iteration != null
              ? `  ·  iter ${s.iteration.toLocaleString()}` : "";
            const shStr   = s.shuffle   != null
              ? `  ·  sh${s.shuffle}`   : "";
            opt.textContent = `${s.label}${iterStr}${shStr}`;
            iaSnapshotSel.appendChild(opt);
          });
        } catch (e) { /* silent */ }
      }
      iaRefreshSnaps?.addEventListener("click", _iaLoadSnapshots);
      iaShuffle?.addEventListener("change", _iaLoadSnapshots);
      iaOpenBtn?.addEventListener("click", _iaLoadSnapshots);

      // ── Live label on the Analyze button ────────────────────────────
      function _iaSyncLabel() {
        const n = parseInt(iaFramesPerCk?.value, 10) || 500;
        const k = _iaCurrentFrame || 0;
        iaBtnAnalyze.textContent = `▶ Analyze ${n} frames from frame ${k}`;
      }
      iaFramesPerCk?.addEventListener("input", _iaSyncLabel);
      // Keep in sync with the player's frame counter via MutationObserver.
      if (iaFrameCounter) {
        new MutationObserver(_iaSyncLabel)
          .observe(iaFrameCounter, { childList: true, characterData: true, subtree: true });
      }

      // ── Warm-worker session start + status poll ─────────────────────
      async function _iaEnsureSession() {
        const snapshot = iaSnapshotSel?.value;
        if (!snapshot) { iaLastRun.textContent = "Pick a snapshot first."; return null; }
        const r = await fetch("/dlc/project/inline-analysis/session/start", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            snapshot_path: snapshot,
            shuffle:       parseInt(iaShuffle?.value, 10) || 1,
            ttl_seconds:   parseInt(iaKeepWarm?.value,  10) || 300,
            batch_size:    parseInt(iaBatchSize?.value, 10) || 8,
          }),
        });
        if (!r.ok) {
          const data = await r.json().catch(() => ({}));
          iaLastRun.textContent = data.error || `Could not start session (HTTP ${r.status})`;
          iaLastRun.className   = "fe-extract-status err";
          return null;
        }
        const data = await r.json();
        _iaSnapKey = data.snap_key;
        _iaStartStatusPoll();
        return _iaSnapKey;
      }

      function _iaStartStatusPoll() {
        if (_iaStatusPoll) return;
        _iaStatusPoll = setInterval(async () => {
          if (!_iaSnapKey) return;
          try {
            const r = await fetch(
              `/dlc/project/inline-analysis/session/status?snap_key=${_iaSnapKey}`);
            const data = await r.json();
            const s = data.status || "absent";
            const remain = data.idle_remaining_s || 0;
            const mm = Math.floor(remain / 60);
            const ss = String(remain % 60).padStart(2, "0");
            if (iaWarmInd) {
              if (s === "ready")       iaWarmInd.textContent = `● warm · ${mm}:${ss}`;
              else if (s === "warming") iaWarmInd.textContent = `… warming`;
              else                       iaWarmInd.textContent = `○ ${s}`;
            }
          } catch (e) { /* keep polling */ }
        }, 2000);
      }
      function _iaStopStatusPoll() {
        if (_iaStatusPoll) { clearInterval(_iaStatusPoll); _iaStatusPoll = null; }
      }

      // ── Analyze-button click ────────────────────────────────────────
      iaBtnAnalyze.addEventListener("click", async () => {
        if (!_iaCurrentVideoPath && !_iaBrowseVideoPath) {
          iaLastRun.textContent = "Pick a video first.";
          return;
        }
        const videoPath = _iaCurrentVideoPath || _iaBrowseVideoPath;
        const sk = await _iaEnsureSession();
        if (!sk) return;
        const startFrame = _iaCurrentFrame || 0;
        const nFrames    = parseInt(iaFramesPerCk?.value, 10) || 500;
        const r = await fetch("/dlc/project/inline-analysis/range", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            snap_key:         sk,
            video_path:       videoPath,
            start_frame:      startFrame,
            n_frames:         nFrames,
            batch_size:       parseInt(iaBatchSize?.value, 10) || 8,
            save_as_csv:      !!(iaSaveCsv && iaSaveCsv.checked),
            snapshot_path:    iaSnapshotSel?.value || "",
            shuffle:          parseInt(iaShuffle?.value, 10) || 1,
            trainingsetindex: parseInt(iaTSI?.value, 10)      || 0,
          }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          iaLastRun.textContent = `Error: ${data.error || r.status}`;
          iaLastRun.className   = "fe-extract-status err";
          return;
        }
        _iaActiveReqId = data.req_id;
        iaLastRun.textContent = `Running (${nFrames} frames from ${startFrame})…`;
        iaLastRun.className   = "fe-extract-status";
        _iaStartRangePoll();
      });

      function _iaStartRangePoll() {
        _iaStopRangePoll();
        _iaActiveReqPoll = setInterval(async () => {
          if (!_iaActiveReqId) { _iaStopRangePoll(); return; }
          try {
            const r = await fetch(
              `/dlc/project/inline-analysis/range/status?req_id=${_iaActiveReqId}`);
            if (!r.ok) return;
            const d = await r.json();
            if (d.status === "done") {
              iaLastRun.textContent =
                `Last run: ${d.n_analyzed} analyzed, ${d.n_skipped} skipped`;
              const videoPath = _iaCurrentVideoPath || _iaBrowseVideoPath;
              if (videoPath && typeof _iaDiscoverVariants === "function") {
                // Re-scan for h5 variants near the video. The just-produced
                // h5 will appear and _vaPickBestPrimary's defaulting picks
                // it up. After discovery, force overlay on so markers draw.
                await _iaDiscoverVariants(videoPath);
                if (iaOverlayToggle && !iaOverlayToggle.checked) {
                  iaOverlayToggle.checked = true;
                  iaOverlayToggle.dispatchEvent(new Event("change", { bubbles: true }));
                }
              }
              _iaActiveReqId = null;
              _iaStopRangePoll();
            } else if (d.status === "error") {
              iaLastRun.textContent = `Error: ${d.error || "unknown"}`;
              iaLastRun.className   = "fe-extract-status err";
              _iaActiveReqId = null;
              _iaStopRangePoll();
            }
          } catch (e) { /* keep polling */ }
        }, 500);
      }
      function _iaStopRangePoll() {
        if (_iaActiveReqPoll) {
          clearInterval(_iaActiveReqPoll); _iaActiveReqPoll = null;
        }
      }

      // Best-effort cleanup on card close + page unload.
      iaCloseBtn?.addEventListener("click", () => {
        _iaStopRangePoll(); _iaStopStatusPoll();
        if (_iaSnapKey) {
          try {
            fetch("/dlc/project/inline-analysis/session/stop", {
              method:  "POST",
              headers: { "Content-Type": "application/json" },
              body:    JSON.stringify({ snap_key: _iaSnapKey }),
            });
          } catch (e) { /* ignore */ }
          _iaSnapKey = null;
        }
      });
      window.addEventListener("beforeunload", () => {
        if (_iaSnapKey) {
          navigator.sendBeacon?.(
            "/dlc/project/inline-analysis/session/stop",
            new Blob([JSON.stringify({ snap_key: _iaSnapKey })],
                     { type: "application/json" }),
          );
        }
      });
    })(); // end ANALYSIS DISPATCH
