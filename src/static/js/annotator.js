"use strict";
import { state } from './state.js';
import { makeFileBrowser } from './components/file_browser.js';

    const anvCard           = document.getElementById("annotate-video-card");
    const anvOpenBtn        = document.getElementById("btn-open-annotate-video");
    const anvCloseBtn       = document.getElementById("btn-close-annotate-video");
    const anvVideoPath      = document.getElementById("anv-video-path");
    const anvBrowseBtn      = document.getElementById("anv-browse-btn");
    const anvLoadBtn        = document.getElementById("anv-load-btn");
    const anvBrowser        = document.getElementById("anv-browser");
    const anvLoadStatus     = document.getElementById("anv-load-status");
    const anvPlayerSec      = document.getElementById("anv-player-section");
    const anvVideoWrap      = document.getElementById("anv-video-wrap");
    const anvFrameImg       = document.getElementById("anv-frame-img");
    const anvFrameSpinner   = document.getElementById("anv-frame-spinner");
    const anvBtnPlay        = document.getElementById("anv-btn-play");
    const anvPlayIcon       = document.getElementById("anv-play-icon");
    const anvPauseIcon      = document.getElementById("anv-pause-icon");
    const anvBtnPrev        = document.getElementById("anv-btn-prev");
    const anvBtnNext        = document.getElementById("anv-btn-next");
    const anvBtnSkipBack    = document.getElementById("anv-btn-skip-back");
    const anvBtnSkipFwd     = document.getElementById("anv-btn-skip-fwd");
    const anvSkipN          = document.getElementById("anv-skip-n");
    const anvFrameCounter   = document.getElementById("anv-frame-counter");
    const anvFrameJump      = document.getElementById("anv-frame-jump");
    const anvTimeDisplay    = document.getElementById("anv-time-display");
    const anvSeek           = document.getElementById("anv-seek");
    const anvCsvBars        = document.getElementById("anv-csv-bars");
    const anvStatusBarWrap  = document.getElementById("anv-status-bar-wrap");
    const anvNoteBarWrap    = document.getElementById("anv-note-bar-wrap");
    const anvStatusCanvas   = document.getElementById("anv-status-canvas");
    const anvNoteCanvas     = document.getElementById("anv-note-canvas");
    const anvCsvSection     = document.getElementById("anv-csv-section");
    const anvCsvNone        = document.getElementById("anv-csv-none");
    const anvCsvLoaded      = document.getElementById("anv-csv-loaded");
    const anvCsvPathDisplay = document.getElementById("anv-csv-path-display");
    const anvCreateCsvBtn   = document.getElementById("anv-create-csv-btn");
    const anvCsvCreateStatus= document.getElementById("anv-csv-create-status");
    const anvAnnotationPanel= document.getElementById("anv-annotation-panel");
    const anvAnnotateFrameNum= document.getElementById("anv-annotate-frame-num");
    const anvNoteInput      = document.getElementById("anv-note-input");
    const anvStatusInput    = document.getElementById("anv-status-input");
    const anvSaveAnnotationBtn = document.getElementById("anv-save-annotation-btn");
    const anvSaveStatusBtn  = document.getElementById("anv-save-status-btn");
    const anvSaveNoteBtn    = document.getElementById("anv-save-note-btn");
    const anvSaveStatus     = document.getElementById("anv-save-status");
    const anvNoteChips      = document.getElementById("anv-note-chips");
    const anvStatusChips    = document.getElementById("anv-status-chips");
    const anvStatusPrevBtn  = document.getElementById("anv-status-prev-btn");
    const anvStatusNextBtn  = document.getElementById("anv-status-next-btn");
    const anvNotePrevBtn    = document.getElementById("anv-note-prev-btn");
    const anvNoteNextBtn    = document.getElementById("anv-note-next-btn");
    const anvNewTagInput    = document.getElementById("anv-new-tag-input");
    const anvAddTagBtn      = document.getElementById("anv-add-tag-btn");
    const anvZoomInput      = document.getElementById("anv-zoom");
    const anvZoomVal        = document.getElementById("anv-zoom-val");
    const anvRefreshCsvBtn  = document.getElementById("anv-refresh-csv-btn");
    const anvClipSection    = document.getElementById("anv-clip-section");
    const anvClipStart      = document.getElementById("anv-clip-start");
    const anvClipFrames     = document.getElementById("anv-clip-frames");
    const anvClipPostfix    = document.getElementById("anv-clip-postfix");
    const anvClipOutdir     = document.getElementById("anv-clip-outdir");
    const anvClipBrowseBtn  = document.getElementById("anv-clip-browse-btn");
    const anvClipBrowser    = document.getElementById("anv-clip-browser");
    const anvClipBtn        = document.getElementById("anv-clip-btn");
    const anvClipStatus     = document.getElementById("anv-clip-status");
    const anvClipLockStart  = document.getElementById("anv-clip-lock-start");

    // ── State ───────────────────────────────────────────────────
    let _anvZoom          = 100;
    let _anvVideoPath     = null;
    let _anvFps           = 30;
    let _anvFrameCount    = 0;
    let _anvCurrentFrame  = 0;
    let _anvFrameBusy     = false;
    let _anvSeekDragging  = false;
    let _anvPlayTimer     = null;
    let _anvCsvPath           = null;
    let _anvCsvRows           = [];   // {frame_number, timestamp, frame_line_status, note}
    let _anvUserTags          = [];   // unique note values seen in CSV + user-added labels
    let _anvUserStatuses      = [];   // unique status values seen in CSV

    // Per-chip active sets and color maps (populated when chips are rendered)
    let _anvActiveNoteChips   = new Set();
    let _anvActiveStatusChips = new Set();
    let _anvNoteColorMap      = {};
    let _anvStatusColorMap    = {};

    const _ANV_STATUS_COLORS = ["#34d399","#f97316","#e879f9","#facc15","#f87171","#22d3ee","#a78bfa","#fb923c"];
    const _ANV_NOTE_COLORS   = ["#60a5fa","#f472b6","#4ade80","#38bdf8","#e879f9","#a78bfa","#facc15","#fb7185"];

    // ── Viewer sizing (can break out of card borders like VA card) ──
    function _anvFitViewer() {
      if (!anvFrameImg.naturalWidth) return;
      const cs      = getComputedStyle(anvCard);
      const padL    = parseFloat(cs.paddingLeft)  || 0;
      const padR    = parseFloat(cs.paddingRight) || 0;
      const baseW   = anvCard.clientWidth - padL - padR;
      const maxW    = Math.max(baseW, window.innerWidth - 32);
      const targetW = Math.min(Math.round(baseW * (_anvZoom / 100)), Math.floor(maxW));
      const extra   = targetW - baseW;
      anvVideoWrap.style.width      = targetW + "px";
      anvVideoWrap.style.marginLeft = extra > 0 ? `-${extra / 2}px` : "";
    }
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => { if (anvFrameImg.naturalWidth) _anvFitViewer(); }).observe(anvCard);
    }
    anvZoomInput.addEventListener("input", () => {
      _anvZoom = parseInt(anvZoomInput.value, 10);
      anvZoomVal.textContent = _anvZoom + " %";
      _anvFitViewer();
    });

    // ── Reset ───────────────────────────────────────────────────
    function _anvReset() {
      if (_anvPlayTimer) { clearInterval(_anvPlayTimer); _anvPlayTimer = null; }
      _anvZoom = 100; anvZoomInput.value = "100"; anvZoomVal.textContent = "100 %";
      _anvVideoPath = null; _anvFps = 30; _anvFrameCount = 0;
      _anvCurrentFrame = 0; _anvFrameBusy = false; _anvSeekDragging = false;
      _anvCsvPath = null; _anvCsvRows = []; _anvUserTags = []; _anvUserStatuses = [];
      _anvActiveNoteChips = new Set(); _anvActiveStatusChips = new Set();
      _anvNoteColorMap = {}; _anvStatusColorMap = {};
      anvPlayIcon.classList.remove("hidden"); anvPauseIcon.classList.add("hidden");
      anvFrameImg.onload = null; anvFrameImg.onerror = null;
      if (anvFrameImg.src && anvFrameImg.src.startsWith("blob:")) URL.revokeObjectURL(anvFrameImg.src);
      anvFrameImg.removeAttribute("src");
      anvVideoWrap.style.width = ""; anvVideoWrap.style.marginLeft = "";
      anvFrameSpinner.classList.add("hidden");
      anvPlayerSec.classList.add("hidden");
      anvCsvBars.classList.add("hidden");
      anvStatusBarWrap.classList.add("hidden");
      anvNoteBarWrap.classList.add("hidden");
      [anvStatusCanvas, anvNoteCanvas].forEach(c => { if (c) { const ctx = c.getContext("2d"); ctx.clearRect(0, 0, c.width, c.height); } });
      anvAnnotationPanel.classList.add("hidden");
      anvClipSection.classList.add("hidden");
      anvClipBrowser.classList.add("hidden");
      anvClipStatus.textContent = ""; anvClipStatus.className = "fe-extract-status";
      anvLoadStatus.textContent = "";
      anvLoadStatus.className = "fe-extract-status";
    }

    // ── Frame URL ───────────────────────────────────────────────
    function _anvFrameUrl(n) {
      return `/dlc/project/video-frame-ext/${n}?path=${encodeURIComponent(_anvVideoPath)}`;
    }

    function _anvPrefetch(frames) {
      frames.forEach(n => {
        if (n >= 0 && n < _anvFrameCount) new Image().src = _anvFrameUrl(n);
      });
    }

    // ── Frame counter — text node kept separate from the jump input ──
    [...anvFrameCounter.childNodes].forEach(n => { if (n.nodeType === Node.TEXT_NODE) n.remove(); });
    const _anvCounterText = document.createTextNode("");
    anvFrameCounter.insertBefore(_anvCounterText, anvFrameJump);

    function _anvUpdateDisplay() {
      _anvCounterText.nodeValue = `Frame ${_anvCurrentFrame} / ${_anvFrameCount}`;
      anvTimeDisplay.textContent = `${(_anvCurrentFrame / _anvFps).toFixed(3)} s`;
      if (!_anvSeekDragging)
        anvSeek.value = Math.round((_anvCurrentFrame / Math.max(_anvFrameCount - 1, 1)) * 1000);
      _anvSyncAnnotationPanel();
      // Keep clip-start in sync with the current frame position (unless locked)
      if (!anvClipLockStart.checked)
        anvClipStart.value = String(_anvCurrentFrame);
      anvClipStart.max   = String(Math.max(_anvFrameCount - 1, 0));
    }

    // ── Double-click frame counter to jump ───────────────────────
    anvFrameCounter.addEventListener("dblclick", () => {
      anvFrameCounter.classList.add("editing");
      anvFrameJump.classList.remove("hidden");
      anvFrameJump.max   = String(_anvFrameCount - 1);
      anvFrameJump.value = String(_anvCurrentFrame);
      anvFrameJump.select();
    });

    function _anvCommitJump() {
      const n = parseInt(anvFrameJump.value);
      anvFrameJump.classList.add("hidden");
      anvFrameCounter.classList.remove("editing");
      if (!isNaN(n)) _anvLoadFrame(n);
    }

    let _anvJumpEscaped = false;
    anvFrameJump.addEventListener("keydown", e => {
      if (e.key === "Enter")  { e.preventDefault(); _anvCommitJump(); }
      if (e.key === "Escape") {
        _anvJumpEscaped = true;
        anvFrameJump.classList.add("hidden");
        anvFrameCounter.classList.remove("editing");
        anvFrameJump.blur();
      }
    });
    anvFrameJump.addEventListener("blur", () => {
      if (_anvJumpEscaped) { _anvJumpEscaped = false; return; }
      _anvCommitJump();
    });

    // ── Load a frame ────────────────────────────────────────────
    async function _anvLoadFrame(n) {
      if (_anvFrameBusy) return;
      _anvFrameBusy = true;
      n = Math.max(0, Math.min(n, Math.max(_anvFrameCount - 1, 0)));
      _anvCurrentFrame = n;
      anvFrameSpinner.classList.remove("hidden");
      try {
        const resp = await fetch(_anvFrameUrl(n));
        if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.error || `HTTP ${resp.status}`); }
        const blob    = await resp.blob();
        const blobUrl = URL.createObjectURL(blob);
        await new Promise((resolve, reject) => {
          anvFrameImg.onload  = resolve;
          anvFrameImg.onerror = reject;
          const prev = anvFrameImg.src;
          anvFrameImg.src = blobUrl;
          if (prev && prev.startsWith("blob:")) URL.revokeObjectURL(prev);
        });
        _anvFitViewer();
        _anvUpdateDisplay();
        _anvPrefetch([n + 1, n + 2]);
      } catch (err) {
        anvLoadStatus.textContent = `Frame load error: ${err.message}`;
        anvLoadStatus.className   = "fe-extract-status err";
      } finally {
        _anvFrameBusy = false;
        anvFrameSpinner.classList.add("hidden");
      }
    }

    // ── Controls ────────────────────────────────────────────────
    anvBtnPlay.addEventListener("click", () => {
      if (_anvPlayTimer) {
        clearInterval(_anvPlayTimer); _anvPlayTimer = null;
        anvPlayIcon.classList.remove("hidden"); anvPauseIcon.classList.add("hidden");
      } else {
        anvPlayIcon.classList.add("hidden"); anvPauseIcon.classList.remove("hidden");
        _anvPlayTimer = setInterval(async () => {
          if (_anvCurrentFrame >= _anvFrameCount - 1) {
            clearInterval(_anvPlayTimer); _anvPlayTimer = null;
            anvPlayIcon.classList.remove("hidden"); anvPauseIcon.classList.add("hidden");
            return;
          }
          await _anvLoadFrame(_anvCurrentFrame + 1);
        }, 1000 / _anvFps);
      }
    });
    anvBtnPrev.addEventListener("click", () => _anvLoadFrame(_anvCurrentFrame - 1));
    anvBtnNext.addEventListener("click", () => _anvLoadFrame(_anvCurrentFrame + 1));
    const _anvSkipN = () => Math.max(1, parseInt(anvSkipN?.value || "10", 10));
    if (anvBtnSkipBack) anvBtnSkipBack.addEventListener("click", () => _anvLoadFrame(_anvCurrentFrame - _anvSkipN()));
    if (anvBtnSkipFwd)  anvBtnSkipFwd.addEventListener("click",  () => _anvLoadFrame(_anvCurrentFrame + _anvSkipN()));
    document.addEventListener("keydown", e => {
      if (!anvCard || anvCard.classList.contains("hidden")) return;
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      if (e.ctrlKey && e.key === "ArrowLeft")  { e.preventDefault(); _anvLoadFrame(_anvCurrentFrame - _anvSkipN()); }
      if (e.ctrlKey && e.key === "ArrowRight") { e.preventDefault(); _anvLoadFrame(_anvCurrentFrame + _anvSkipN()); }
    });

    anvSeek.addEventListener("mousedown",  () => { _anvSeekDragging = true; });
    anvSeek.addEventListener("touchstart", () => { _anvSeekDragging = true; });
    anvSeek.addEventListener("input", () => {
      _anvCurrentFrame = Math.round((anvSeek.value / 1000) * Math.max(_anvFrameCount - 1, 0));
      _anvCounterText.nodeValue  = `Frame ${_anvCurrentFrame} / ${_anvFrameCount}`;
      anvTimeDisplay.textContent = `${(_anvCurrentFrame / _anvFps).toFixed(3)} s`;
    });
    anvSeek.addEventListener("change", () => { _anvSeekDragging = false; _anvLoadFrame(_anvCurrentFrame); });

    anvCard.addEventListener("keydown", (e) => {
      if (anvPlayerSec.classList.contains("hidden")) return;
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      if (e.key === "ArrowLeft")  { e.preventDefault(); _anvLoadFrame(_anvCurrentFrame - 1); }
      if (e.key === "ArrowRight") { e.preventDefault(); _anvLoadFrame(_anvCurrentFrame + 1); }
    });

    // ── Build CSV bars — canvas, one fillRect per annotated frame ──
    // Only frames whose value is in activeSet are drawn; each value uses colorMap.
    function _anvDrawCanvas(canvas, rows, field, activeSet, colorMap) {
      if (!canvas) return;
      const total = Math.max(_anvFrameCount, 1);
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

    function _anvRedrawNoteCanvas()   { _anvDrawCanvas(anvNoteCanvas,   _anvCsvRows, "note",              _anvActiveNoteChips,   _anvNoteColorMap);   }
    function _anvRedrawStatusCanvas() { _anvDrawCanvas(anvStatusCanvas, _anvCsvRows, "frame_line_status", _anvActiveStatusChips, _anvStatusColorMap); }

    function _anvBuildCsvBars() {
      const hasNote   = _anvCsvRows.some(r => r.note);
      const hasStatus = _anvCsvRows.some(r => r.frame_line_status && r.frame_line_status !== "0");
      anvCsvBars.classList.toggle("hidden", !hasNote && !hasStatus);
      anvNoteBarWrap.classList.toggle("hidden", !hasNote);
      anvStatusBarWrap.classList.toggle("hidden", !hasStatus);
      // Canvases start empty; chips toggle individual values onto them.
      _anvRedrawNoteCanvas();
      _anvRedrawStatusCanvas();
    }

    // Click on canvas — snap to nearest annotated frame for that field and jump.
    [anvNoteCanvas, anvStatusCanvas].forEach((canvas, ci) => {
      if (!canvas) return;
      const field = ci === 0 ? "note" : "frame_line_status";
      canvas.addEventListener("click", e => {
        const rect   = canvas.getBoundingClientRect();
        const target = Math.round((e.clientX - rect.left) / rect.width * Math.max(_anvFrameCount - 1, 0));
        const annotated = _anvCsvRows
          .filter(r => { const v = r[field]; return v && (field !== "frame_line_status" || v !== "0"); })
          .map(r => r.frame_number);
        if (!annotated.length) return;
        _anvLoadFrame(annotated.reduce((a, b) => Math.abs(b - target) < Math.abs(a - target) ? b : a));
      });
    });

    // ── Sync annotation panel to current frame ───────────────────
    function _anvSyncAnnotationPanel() {
      if (!_anvCsvPath) return;
      anvAnnotateFrameNum.textContent = _anvCurrentFrame;
      const row = _anvCsvRows.find(r => r.frame_number === _anvCurrentFrame);
      anvNoteInput.value    = row ? (row.note || "") : "";
      anvStatusInput.value  = row ? (row.frame_line_status || "0") : "0";
    }

    // ── Apply CSV rows to state and UI ───────────────────────────
    function _anvApplyCsvRows(rows, csvPath) {
      _anvCsvPath  = csvPath;
      _anvCsvRows  = rows;

      const noteVals   = [...new Set(rows.map(r => r.note).filter(v => v))];
      const statusVals = [...new Set(rows.map(r => r.frame_line_status).filter(v => v && v !== "0"))];
      _anvUserTags     = [...new Set([..._anvUserTags,     ...noteVals])];
      _anvUserStatuses = [...new Set([..._anvUserStatuses, ...statusVals])];

      anvCsvNone.classList.add("hidden");
      anvCsvLoaded.classList.remove("hidden");
      anvCsvPathDisplay.textContent = csvPath;
      anvCsvPathDisplay.title       = csvPath;
      anvAnnotationPanel.classList.remove("hidden");

      _anvBuildCsvBars();
      _anvRenderStatusChips();
      _anvRenderTagChips();
      _anvSyncAnnotationPanel();
    }

    // ── Render status chips — each unique value, unique color, toggle timeline ─
    function _anvRenderStatusChips() {
      if (!anvStatusChips) return;
      anvStatusChips.innerHTML = "";
      _anvStatusColorMap = {};
      _anvUserStatuses.forEach((val, i) => {
        const color = _ANV_STATUS_COLORS[i % _ANV_STATUS_COLORS.length];
        _anvStatusColorMap[val] = color;
        const chip = document.createElement("span");
        chip.className = "fe-tag-chip" + (_anvActiveStatusChips.has(val) ? " active" : "");
        chip.textContent = val;
        chip.style.setProperty("--chip-color", color);
        chip.title = `Click to show/hide "${val}" on timeline`;
        chip.addEventListener("click", () => {
          if (_anvActiveStatusChips.has(val)) _anvActiveStatusChips.delete(val);
          else _anvActiveStatusChips.add(val);
          _anvRenderStatusChips();
          _anvRedrawStatusCanvas();
        });
        anvStatusChips.appendChild(chip);
      });
      const hasActive = _anvActiveStatusChips.size > 0;
      if (anvStatusPrevBtn) anvStatusPrevBtn.disabled = !hasActive;
      if (anvStatusNextBtn) anvStatusNextBtn.disabled = !hasActive;
    }

    // ── Render note chips — each unique value, unique color, toggle timeline ──
    function _anvRenderTagChips() {
      if (!anvNoteChips) return;
      anvNoteChips.innerHTML = "";
      _anvNoteColorMap = {};
      _anvUserTags.forEach((tag, i) => {
        const color = _ANV_NOTE_COLORS[i % _ANV_NOTE_COLORS.length];
        _anvNoteColorMap[tag] = color;
        const chip = document.createElement("span");
        chip.className = "fe-tag-chip" + (_anvActiveNoteChips.has(tag) ? " active" : "");
        chip.textContent = tag;
        chip.style.setProperty("--chip-color", color);
        chip.title = `Click to show/hide "${tag}" on timeline`;
        chip.addEventListener("click", () => {
          if (_anvActiveNoteChips.has(tag)) _anvActiveNoteChips.delete(tag);
          else _anvActiveNoteChips.add(tag);
          _anvRenderTagChips();
          _anvRedrawNoteCanvas();
        });
        anvNoteChips.appendChild(chip);
      });
      const hasActive = _anvActiveNoteChips.size > 0;
      if (anvNotePrevBtn) anvNotePrevBtn.disabled = !hasActive;
      if (anvNoteNextBtn) anvNoteNextBtn.disabled = !hasActive;
    }

    // ── Core save — writes note+status for current frame ─────────
    async function _anvDoSave(note, status) {
      if (!_anvCsvPath) return;
      anvSaveStatus.textContent = "Saving…";
      anvSaveStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch("/annotate/save-row", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            csv_path:          _anvCsvPath,
            frame_number:      _anvCurrentFrame,
            note,
            frame_line_status: status,
            fps:               _anvFps,
          }),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        const isInteresting = note || (status && status !== "0");
        const idx = _anvCsvRows.findIndex(r => r.frame_number === _anvCurrentFrame);
        if (isInteresting) {
          const savedRow = data.row || {
            frame_number:      _anvCurrentFrame,
            timestamp:         (_anvCurrentFrame / _anvFps).toFixed(3),
            frame_line_status: status,
            note,
          };
          if (idx >= 0) _anvCsvRows[idx] = savedRow;
          else { _anvCsvRows.push(savedRow); _anvCsvRows.sort((a, b) => a.frame_number - b.frame_number); }
          if (note && !_anvUserTags.includes(note)) { _anvUserTags.push(note); _anvRenderTagChips(); }
          if (status && status !== "0" && !_anvUserStatuses.includes(status)) { _anvUserStatuses.push(status); _anvRenderStatusChips(); }
        } else {
          if (idx >= 0) _anvCsvRows.splice(idx, 1);
        }
        _anvBuildCsvBars();
        anvSaveStatus.textContent = "Saved";
        anvSaveStatus.className   = "fe-extract-status ok";
        setTimeout(() => { if (anvSaveStatus.textContent === "Saved") anvSaveStatus.textContent = ""; }, 2000);
      } catch (err) {
        anvSaveStatus.textContent = `Error: ${err.message}`;
        anvSaveStatus.className   = "fe-extract-status err";
      }
    }

    async function _anvSaveStatus() {
      const existingRow = _anvCsvRows.find(r => r.frame_number === _anvCurrentFrame);
      const note   = anvNoteInput   ? anvNoteInput.value.trim()       : (existingRow?.note || "");
      const status = anvStatusInput ? (anvStatusInput.value || "0")   : "0";
      await _anvDoSave(note, status);
    }

    async function _anvSaveNote() {
      const existingRow = _anvCsvRows.find(r => r.frame_number === _anvCurrentFrame);
      const note   = anvNoteInput   ? anvNoteInput.value.trim()                        : "";
      const status = anvStatusInput ? (anvStatusInput.value || "0") : (existingRow?.frame_line_status || "0");
      await _anvDoSave(note, status);
    }

    if (anvSaveStatusBtn) anvSaveStatusBtn.addEventListener("click", _anvSaveStatus);
    if (anvSaveNoteBtn)   anvSaveNoteBtn.addEventListener("click",   _anvSaveNote);
    // Keep old single-button ref working if present (graceful fallback)
    if (anvSaveAnnotationBtn) anvSaveAnnotationBtn.addEventListener("click", () => _anvDoSave(
      anvNoteInput ? anvNoteInput.value.trim() : "",
      anvStatusInput ? (anvStatusInput.value || "0") : "0",
    ));

    // ── Prev / next navigation within active chip set ─────────────
    function _anvNavAnnot(field, activeSet, dir) {
      if (!activeSet.size) return;
      const frames = _anvCsvRows
        .filter(r => { const v = r[field]; return v && (field !== "frame_line_status" || v !== "0") && activeSet.has(v); })
        .map(r => r.frame_number)
        .sort((a, b) => a - b);
      if (!frames.length) return;
      if (dir < 0) { const prev = [...frames].reverse().find(f => f < _anvCurrentFrame); if (prev != null) _anvLoadFrame(prev); }
      else         { const next = frames.find(f => f > _anvCurrentFrame);                if (next != null) _anvLoadFrame(next); }
    }

    if (anvStatusPrevBtn) anvStatusPrevBtn.addEventListener("click", () => _anvNavAnnot("frame_line_status", _anvActiveStatusChips, -1));
    if (anvStatusNextBtn) anvStatusNextBtn.addEventListener("click", () => _anvNavAnnot("frame_line_status", _anvActiveStatusChips,  1));
    if (anvNotePrevBtn)   anvNotePrevBtn.addEventListener("click",   () => _anvNavAnnot("note", _anvActiveNoteChips, -1));
    if (anvNoteNextBtn)   anvNoteNextBtn.addEventListener("click",   () => _anvNavAnnot("note", _anvActiveNoteChips,  1));

    // ── Add new tag ──────────────────────────────────────────────
    anvAddTagBtn.addEventListener("click", () => {
      const tag = anvNewTagInput.value.trim();
      if (!tag) return;
      if (!_anvUserTags.includes(tag)) {
        _anvUserTags.push(tag);
        _anvRenderTagChips();
      }
      anvNewTagInput.value = "";
    });
    anvNewTagInput.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); anvAddTagBtn.click(); }
    });

    // ── Load video ───────────────────────────────────────────────
    async function _anvLoadVideo(path) {
      _anvReset();
      _anvVideoPath = path;
      anvVideoPath.value = path;
      anvLoadStatus.textContent = "Loading video info…";
      anvLoadStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch(`/annotate/video-info?path=${encodeURIComponent(path)}`);
        const info = await res.json();
        if (info.error) throw new Error(info.error);
        _anvFps        = info.fps || 30;
        _anvFrameCount = info.frame_count || 0;
      } catch (err) {
        anvLoadStatus.textContent = `Error: ${err.message}`;
        anvLoadStatus.className   = "fe-extract-status err";
        return;
      }
      anvLoadStatus.textContent = "";
      anvPlayerSec.classList.remove("hidden");
      anvCsvSection.classList.remove("hidden");
      anvClipSection.classList.remove("hidden");
      anvClipStart.value  = "0";
      anvClipStart.max    = String(Math.max(_anvFrameCount - 1, 0));
      anvClipFrames.value = "100";
      anvClipOutdir.value = "";
      anvClipPostfix.value = "";
      anvClipStatus.textContent = "";
      _anvLoadFrame(0);

      // Try to load companion CSV
      try {
        const res  = await fetch(`/annotate/csv?path=${encodeURIComponent(path)}`);
        const data = await res.json();
        if (data.csv_exists) {
          _anvApplyCsvRows(data.rows, data.csv_path);
        } else {
          anvCsvNone.classList.remove("hidden");
          anvCsvLoaded.classList.add("hidden");
        }
      } catch (_) {
        anvCsvNone.classList.remove("hidden");
        anvCsvLoaded.classList.add("hidden");
      }
    }

    anvLoadBtn.addEventListener("click", () => {
      const path = anvVideoPath.value.trim();
      if (!path) { anvLoadStatus.textContent = "Enter a video path first."; anvLoadStatus.className = "fe-extract-status err"; return; }
      _anvLoadVideo(path);
    });

    // ── Create CSV ───────────────────────────────────────────────
    anvCreateCsvBtn.addEventListener("click", async () => {
      if (!_anvVideoPath) return;
      anvCsvCreateStatus.textContent = `Creating CSV for ${_anvFrameCount} frames…`;
      anvCsvCreateStatus.className   = "fe-extract-status";
      try {
        const res  = await fetch("/annotate/create-csv", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            video_path:  _anvVideoPath,
            fps:         _anvFps,
            frame_count: _anvFrameCount,
          }),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        anvCsvCreateStatus.textContent = "";
        _anvApplyCsvRows(data.rows, data.csv_path);
      } catch (err) {
        anvCsvCreateStatus.textContent = `Error: ${err.message}`;
        anvCsvCreateStatus.className   = "fe-extract-status err";
      }
    });

    // ── Refresh CSV ──────────────────────────────────────────────
    async function _anvRefreshCsv() {
      if (!_anvVideoPath) return;
      try {
        const res  = await fetch(`/annotate/csv?path=${encodeURIComponent(_anvVideoPath)}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        if (data.csv_exists) {
          _anvApplyCsvRows(data.rows, data.csv_path);
        }
      } catch (err) {
        anvSaveStatus.textContent = `Refresh error: ${err.message}`;
        anvSaveStatus.className   = "fe-extract-status err";
      }
    }
    anvRefreshCsvBtn.addEventListener("click", _anvRefreshCsv);

    // ── File browser (canonical file-browser component) ──────────
    // Video picker. Default fileFilter accepts video+image extensions;
    // override here to include .mpg/.mpeg (the annotator's original set).
    const _anvVideoExts = new Set([".mp4", ".avi", ".mov", ".mkv", ".mpg", ".mpeg"]);
    const anvPicker = makeFileBrowser({
      inputEl: anvVideoPath,
      paneEl:  anvBrowser,
      fileFilter: (name) => {
        const i = name.lastIndexOf(".");
        if (i < 0) return false;
        return _anvVideoExts.has(name.slice(i).toLowerCase());
      },
      onPick:  (path) => { _anvVideoPath = path; anvVideoPath.value = path; },
    });

    anvBrowseBtn.addEventListener("click", () => {
      const startPath = state.userDataDir || "/";
      anvPicker.openAt(startPath);
    });

    // ── Open / close card ────────────────────────────────────────
    anvOpenBtn?.addEventListener("click", () => {
      anvCard.classList.remove("hidden");
      anvCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });

    anvCloseBtn?.addEventListener("click", () => {
      anvCard.classList.add("hidden");
      _anvReset();
      anvBrowser.classList.add("hidden");
    });

    // ── Clip extractor dir browser (canonical file-browser, dir-only)
    // The component's single-click on a directory writes its path to
    // inputEl AND expands it inline — so "use this folder" is implicit:
    // whichever folder the user last clicked is what anvClipOutdir holds.
    const anvClipPicker = makeFileBrowser({
      inputEl: anvClipOutdir,
      paneEl:  anvClipBrowser,
      dirOnly: true,
    });

    anvClipBrowseBtn.addEventListener("click", () => {
      // Start browser at current video's directory if possible, else user data dir
      const startPath = _anvVideoPath
        ? _anvVideoPath.substring(0, _anvVideoPath.lastIndexOf("/")) || "/"
        : (state.userDataDir || "/");
      anvClipPicker.openAt(startPath);
    });

    // ── Crop video ───────────────────────────────────────────────
    async function _anvCropVideo() {
      if (!_anvVideoPath) return;
      const startFrame = parseInt(anvClipStart.value, 10) || 0;
      const numFrames  = parseInt(anvClipFrames.value, 10) || 0;
      if (numFrames <= 0) {
        anvClipStatus.textContent = "Frames must be > 0.";
        anvClipStatus.className   = "fe-extract-status err";
        return;
      }

      anvClipStatus.textContent = "Cropping…";
      anvClipStatus.className   = "fe-extract-status";
      anvClipBtn.disabled       = true;

      try {
        const res  = await fetch("/annotate/crop-video", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            video_path:  _anvVideoPath,
            start_frame: startFrame,
            num_frames:  numFrames,
            output_dir:  anvClipOutdir.value.trim(),
            postfix:     anvClipPostfix.value.trim(),
          }),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        const fname = data.output_path.split("/").pop();
        let msg = `Saved: ${fname}`;
        if (data.csv_path) {
          msg += ` + CSV`;
        }
        anvClipStatus.textContent  = msg;
        anvClipStatus.className    = "fe-extract-status ok";
        anvClipStatus.title        = data.output_path;
        anvClipLockStart.checked   = false;
        anvClipPostfix.value       = "";
      } catch (err) {
        anvClipStatus.textContent = `Error: ${err.message}`;
        anvClipStatus.className   = "fe-extract-status err";
      } finally {
        anvClipBtn.disabled = false;
      }
    }

    anvClipBtn.addEventListener("click", _anvCropVideo);
