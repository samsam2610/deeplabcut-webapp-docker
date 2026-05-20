// src/static/js/components/analyzed_frame_player.js
//
// ⚠ DUPLICATION NOTICE
//   This file currently maintains a copy of player/overlay/marker-adjustment/
//   dataset-curation logic that ALSO lives in ../viewer.js. Bug fixes in one
//   must be manually mirrored to the other until viewer.js is migrated to
//   this factory.
//
//   See docs/superpowers/specs/2026-05-20-inline-analysis-design.md
//   (§4 "Player Code Reuse" and "Known tech debt") for the planned migration.
//   Follow-up PR title prefix: `refactor(viewer): migrate to analyzed_frame_player factory`.
//
// USAGE:
//   import { makeAnalyzedFramePlayer } from "./components/analyzed_frame_player.js";
//   const player = makeAnalyzedFramePlayer({
//     prefix: "ia",                            // DOM id prefix (ia-frame-img, ia-overlay-canvas, …)
//     frameUrlFn: (n) => `/annotate/video-frame/${n}?path=${path}`,
//     poseUrlFn:  (layer, n) => `/dlc/viewer/h5-pose-window?h5=${layer.path}&start=${n}&n=1`,
//     onCsvSaved: () => { /* card refresh hook */ },
//   });
//   player.loadVideo(videoPath, fps, nFrames);
//   player.reloadH5();        // after each inline range completes
//   player.destroy();         // on card close

export function makeAnalyzedFramePlayer(options) {
  const {
    prefix,
    frameUrlFn,
    poseUrlFn,
    onCsvSaved,
  } = options || {};

  // ── DOM-id helper ─────────────────────────────────────────────────────
  const $id = (suffix) => document.getElementById(`${prefix}-${suffix}`);

  // ── DOM grabs (parameterised by prefix) ───────────────────────────────
  const frameImg          = $id("frame-img");
  const frameSpinner      = $id("frame-spinner");
  const overlayCanvas     = $id("overlay-canvas");
  const btnPlay           = $id("btn-play");
  const btnPrev           = $id("btn-prev");
  const btnNext           = $id("btn-next");
  const skipN             = $id("skip-n");
  const frameCounter      = $id("frame-counter");
  const seek              = $id("seek");
  const zoomInput         = $id("zoom");
  const zoomVal           = $id("zoom-val");
  const overlayToggle     = $id("overlay-toggle");
  const overlayPrimarySel = $id("overlay-primary-select");
  const overlayAddCompare = $id("overlay-add-compare");
  const overlayCompareList = $id("overlay-compare-list");
  const overlayThreshold  = $id("overlay-threshold");
  const overlayMarkerSize = $id("overlay-marker-size");
  const markerEditBanner  = $id("marker-edit-banner");

  // ── Module-private state ──────────────────────────────────────────────
  let _currentFrame = 0;
  let _frameCount   = 0;
  let _fps          = 30;
  let _videoPath    = null;
  let _frameBusy    = false;
  let _playTimer    = null;
  let _seekDragging = false;
  let _zoom         = 100;

  // overlay
  let _overlayEnabled = false;
  let _markerSize     = 6;
  let _globalThreshold = 0.6;
  const _layers       = [];   // [0] = primary, [1+] = comparisons

  // pose cache window
  const _POSE_WINDOW = 30;
  let   _prefetchCtrl = null;

  // hooks set by attach methods
  let _curationFrameHook = null;
  let _metadataFrameHook = null;

  // edited-but-unsaved marker bookkeeping (skeleton — no rendering in v1 factory)
  const _editedFrames = new Set();

  // listener teardown registry
  const _teardown = [];
  const _on = (el, ev, fn, opts) => {
    if (!el) return;
    el.addEventListener(ev, fn, opts);
    _teardown.push(() => el.removeEventListener(ev, fn, opts));
  };

  const overlayCtx = overlayCanvas ? overlayCanvas.getContext("2d") : null;

  // ── Frame loading ─────────────────────────────────────────────────────
  function _syncCanvas() {
    if (!overlayCanvas || !frameImg) return;
    const w = frameImg.naturalWidth  || frameImg.width  || 0;
    const h = frameImg.naturalHeight || frameImg.height || 0;
    if (w && h && (overlayCanvas.width !== w || overlayCanvas.height !== h)) {
      overlayCanvas.width  = w;
      overlayCanvas.height = h;
    }
  }

  async function _loadFrame(n) {
    if (_frameBusy) return;
    _frameBusy = true;
    n = Math.max(0, Math.min(n, Math.max(_frameCount - 1, 0)));
    _currentFrame = n;
    if (frameSpinner) frameSpinner.classList.remove("hidden");

    const url = frameUrlFn(n);

    try {
      // Preload off-DOM so image and overlay land atomically.
      const im = await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload  = () => resolve(img);
        img.onerror = (e) => reject(e || new Error("image preload failed"));
        img.src = url;
      });
      const prev = frameImg ? frameImg.src : null;
      if (frameImg) frameImg.src = im.src;
      if (prev && prev.startsWith("blob:")) URL.revokeObjectURL(prev);

      _syncCanvas();
      _updateDisplay();
      _prefetchFrames([n + 1, n + 2]);
      _prefetchPoseWindow(n + 1);
      if (_curationFrameHook) _curationFrameHook(n);
      if (_metadataFrameHook) _metadataFrameHook(n);
      _drawCurrentFrame();
      // Paint barrier so play loop doesn't outrun the render.
      await new Promise(requestAnimationFrame);
    } catch (err) {
      // best-effort; keep UI alive
    } finally {
      _frameBusy = false;
      if (frameSpinner) frameSpinner.classList.add("hidden");
    }
  }

  function _updateDisplay() {
    if (frameCounter) {
      frameCounter.textContent = `Frame ${_currentFrame} / ${_frameCount}`;
    }
    if (seek && !_seekDragging) {
      // Inline-analysis card uses absolute frame indices on the slider.
      seek.value = String(_currentFrame);
    }
  }

  function _prefetchFrames(frames) {
    frames.forEach((n) => {
      if (n >= 0 && n < _frameCount) new Image().src = frameUrlFn(n);
    });
  }

  function _prefetchPoseWindow(startFrame) {
    if (_prefetchCtrl) {
      try { _prefetchCtrl.abort(); } catch (e) {}
    }
    _prefetchCtrl = new AbortController();
    const signal = _prefetchCtrl.signal;
    _layers.forEach((layer) => {
      if (!poseUrlFn || !layer || layer.errored) return;
      const url = poseUrlFn(layer, startFrame);
      fetch(url, { signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (!data || !data.poses) return;
          // Spec-compatible shape: list of per-frame entries keyed by frame index.
          if (!layer.posesCache) layer.posesCache = new Map();
          if (Array.isArray(data.poses)) {
            data.poses.forEach((entry, i) => {
              const frame = (typeof entry.frame === "number") ? entry.frame : (startFrame + i);
              layer.posesCache.set(frame, entry);
            });
          } else if (typeof data.poses === "object") {
            Object.entries(data.poses).forEach(([k, v]) => {
              layer.posesCache.set(Number(k), v);
            });
          }
          _drawCurrentFrame();
        })
        .catch(() => {});
    });
  }

  function _drawCurrentFrame() {
    if (!overlayCtx || !_overlayEnabled || !overlayCanvas) return;
    _syncCanvas();
    overlayCtx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
    const natW = frameImg ? (frameImg.naturalWidth  || 1) : 1;
    const natH = frameImg ? (frameImg.naturalHeight || 1) : 1;
    const sx   = overlayCanvas.width  / natW;
    const sy   = overlayCanvas.height / natH;
    const r    = Math.max(1, Math.round(_markerSize * Math.min(sx, sy)));
    const thr  = _globalThreshold;

    _layers.forEach((layer) => {
      if (!layer || layer.errored || layer.visible === false) return;
      const cached = layer.posesCache ? layer.posesCache.get(_currentFrame) : null;
      if (!cached || !Array.isArray(cached.poses)) return;
      cached.poses.forEach((p) => {
        if (typeof p.likelihood === "number" && p.likelihood < thr) return;
        const cx = Math.round((p.x || 0) * sx);
        const cy = Math.round((p.y || 0) * sy);
        overlayCtx.beginPath();
        overlayCtx.arc(cx, cy, r, 0, Math.PI * 2);
        overlayCtx.fillStyle = p.color || layer.color || "#22d3ee";
        overlayCtx.fill();
      });
    });
  }

  // ── Public API ────────────────────────────────────────────────────────
  function loadVideo(videoPath, fps, nFrames) {
    _videoPath   = videoPath;
    _fps         = fps || 30;
    _frameCount  = nFrames || 0;
    _currentFrame = 0;
    if (seek) {
      seek.min  = "0";
      seek.max  = String(Math.max(0, _frameCount - 1));
      seek.step = "1";
      seek.value = "0";
    }
    _loadFrame(0);
  }

  function reloadH5() {
    // Drop per-layer pose caches and re-prefetch around the visible frame.
    _layers.forEach((l) => {
      if (l && l.posesCache) l.posesCache.clear();
    });
    _prefetchPoseWindow(_currentFrame);
    _drawCurrentFrame();
  }

  function getCurrentFrame() { return _currentFrame; }
  function setCurrentFrame(n) {
    const target = Math.max(0, Math.min(n, Math.max(_frameCount - 1, 0)));
    _loadFrame(target);
  }

  function destroy() {
    if (_playTimer) { clearTimeout(_playTimer); _playTimer = null; }
    if (_prefetchCtrl) { try { _prefetchCtrl.abort(); } catch (e) {} _prefetchCtrl = null; }
    _teardown.forEach((fn) => { try { fn(); } catch (e) {} });
    _teardown.length = 0;
    _layers.length = 0;
    _editedFrames.clear();
  }

  function setCurationFrameHook(fn) { _curationFrameHook = fn; }
  function setMetadataFrameHook(fn) { _metadataFrameHook = fn; }

  function setPrimaryLayer(layerDescriptor) {
    // layerDescriptor: { path, label, color? }
    if (!layerDescriptor) return;
    const layer = {
      path: layerDescriptor.path,
      label: layerDescriptor.label || layerDescriptor.path,
      color: layerDescriptor.color || "#22d3ee",
      visible: true,
      errored: false,
      posesCache: new Map(),
    };
    if (_layers.length === 0) _layers.push(layer);
    else _layers[0] = layer;
    _prefetchPoseWindow(_currentFrame);
    _drawCurrentFrame();
  }

  // ── Wire DOM listeners (use _on so destroy() cleans them up) ──────────
  function _playStep() {
    if (!_frameCount) return;
    if (_currentFrame + 1 >= _frameCount) { _stopPlay(); return; }
    _loadFrame(_currentFrame + 1).then(() => {
      if (_playTimer !== null) {
        _playTimer = setTimeout(_playStep, Math.max(1000 / Math.max(_fps, 1), 16));
      }
    });
  }
  function _startPlay() {
    if (_playTimer !== null) return;
    _playTimer = setTimeout(_playStep, 0);
  }
  function _stopPlay() {
    if (_playTimer !== null) { clearTimeout(_playTimer); _playTimer = null; }
  }

  _on(btnPlay, "click", () => {
    if (_playTimer !== null) _stopPlay();
    else _startPlay();
  });
  _on(btnPrev, "click", () => {
    const step = (skipN && parseInt(skipN.value, 10)) || 1;
    setCurrentFrame(_currentFrame - step);
  });
  _on(btnNext, "click", () => {
    const step = (skipN && parseInt(skipN.value, 10)) || 1;
    setCurrentFrame(_currentFrame + step);
  });
  _on(seek, "input", () => { _seekDragging = true; });
  _on(seek, "change", () => {
    _seekDragging = false;
    if (seek) setCurrentFrame(parseInt(seek.value, 10) || 0);
  });
  _on(zoomInput, "input", () => {
    _zoom = parseInt(zoomInput.value, 10) || 100;
    if (zoomVal) zoomVal.textContent = `${_zoom} %`;
    if (frameImg) frameImg.style.width = `${_zoom}%`;
  });
  _on(overlayToggle, "change", () => {
    _overlayEnabled = !!(overlayToggle && overlayToggle.checked);
    _drawCurrentFrame();
  });
  _on(overlayThreshold, "input", () => {
    if (overlayThreshold) _globalThreshold = parseFloat(overlayThreshold.value) || 0.6;
    _drawCurrentFrame();
  });
  _on(overlayMarkerSize, "input", () => {
    if (overlayMarkerSize) _markerSize = parseInt(overlayMarkerSize.value, 10) || 6;
    _drawCurrentFrame();
  });

  // Silence unused-variable linters for slots reserved for the deferred port:
  void overlayPrimarySel; void overlayAddCompare; void overlayCompareList;
  void markerEditBanner; void onCsvSaved;

  return {
    loadVideo,
    reloadH5,
    getCurrentFrame,
    setCurrentFrame,
    destroy,
    setCurationFrameHook,
    setMetadataFrameHook,
    setPrimaryLayer,
  };
}
