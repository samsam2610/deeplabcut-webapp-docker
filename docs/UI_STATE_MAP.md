# UI STATE MAP
> DOM ID → JS module → event → backend endpoint reference.
> Token-optimized for LLM context loading.

---

## Viewer Card (VA) — `viewer.js`

### Video Player Controls

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `va-btn-play` | `viewer.js` | `click` → toggle play/pause interval | (frame fetch loop) |
| `va-btn-prev` | `viewer.js` | `click` → `_vaLoadFrame(n-1)` | `GET /dlc/project/video-frame/<name>/<n>` |
| `va-btn-next` | `viewer.js` | `click` → `_vaLoadFrame(n+1)` | `GET /dlc/project/video-frame/<name>/<n>` |
| `va-btn-skip-back` | `viewer.js` | `click` → `_vaLoadFrame(n - skipN)` | (same frame endpoint) |
| `va-btn-skip-fwd` | `viewer.js` | `click` → `_vaLoadFrame(n + skipN)` | (same frame endpoint) |
| `va-skip-n` | `viewer.js` | `keydown` (stopPropagation) | — |
| `va-seek` | `viewer.js` | `input` (update display) / `change` (load frame) | (same frame endpoint) |
| `va-btn-back` | `viewer.js` | `click` → `_vaReset()` | — |
| `va-refresh-btn` | `viewer.js` | `click` → `_vaLoadContent()` | `GET /dlc/project/labeled-content` |
| `va-frame-counter` | `viewer.js` | (display only) | — |
| `va-time-display` | `viewer.js` | (display only) | — |

Keyboard: `Space` = play/pause; `ArrowLeft`/`ArrowRight` = ±1 frame; `Ctrl+Arrow` = ±skipN. Scoped to `view-analyzed-card` (`tabindex="-1"`).

### Overlay Controls & Marker Drag-to-Edit

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `va-overlay-canvas` | `viewer.js` | `mousemove` (hover hit-test) / `mousedown` (start drag) / `mouseup` (end drag, flush edit) / `mouseleave` (cancel drag) | `POST /dlc/viewer/marker-edit` (on mouseup) |
| `va-overlay-toggle` | `viewer.js` | `change` → enable/disable overlay | `GET /dlc/viewer/h5-find` (auto-detect) |
| `va-overlay-threshold` | `viewer.js` | `input` (update label) / `change` (reload frame) | `GET /dlc/viewer/frame-poses/<n>` |
| `va-overlay-threshold-val` | `viewer.js` | (display only) | — |
| `va-overlay-marker-size` | `viewer.js` | `input` → redraw canvas | — |
| `va-overlay-marker-size-val` | `viewer.js` | (display only) | — |
| `va-overlay-h5-path` | `viewer.js` | (value source for h5 path) | — |
| `va-overlay-h5-auto` | `viewer.js` | `click` → `_vaAutoDetectH5()` | `GET /dlc/viewer/h5-find` |
| `va-overlay-h5-browse` | `viewer.js` | `click` → toggle `va-overlay-h5-browser` | `GET /fs/ls` |
| `va-overlay-h5-clear` | `viewer.js` | `click` → clear h5 state + canvas | — |
| `va-overlay-parts-all` | `viewer.js` | `click` → select all bodyparts | `GET /dlc/viewer/frame-poses/<n>` |
| `va-overlay-parts-none` | `viewer.js` | `click` → deselect all bodyparts | — |
| `va-overlay-bodyparts` | `viewer.js` | checkbox `change` (per bodypart) | `GET /dlc/viewer/frame-poses/<n>` |

Pose prefetch: `GET /dlc/viewer/frame-poses-batch` (AbortController-managed window of 30 frames).

**Marker drag-to-edit state machine:**

```
idle
  │  mousedown + hit-test finds bodypart
  ▼
dragging  (_vaDragBp = bp, _vaDragging = true)
  │  mousemove → update _vaLocalEdits[frame][bp] = {x, y}
  │            → _vaDrawPoseMarkers() (zero-latency redraw, edited marker has white ring)
  │
  │  mouseup / mouseleave
  ▼
flush  → POST /dlc/viewer/marker-edit {h5, frame, bp, x, y}
  │       save_edit_cache() on server (H5 unchanged)
  ▼
idle  → _vaUpdateEditBanner() (shows "N frames edited" + Save/Discard buttons)
```

**Edit-state JS variables (module scope, `viewer.js`):**

| Variable | Type | Purpose |
|----------|------|---------|
| `_vaLocalEdits` | `Map<int, {bp: {x,y}}>` | Client-side overrides; populated from server cache on H5 load |
| `_vaDragBp` | `string \| null` | Bodypart currently being dragged |
| `_vaDragging` | `boolean` | True while mouse button is held on a marker |

### Marker Adjustment Buttons

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `va-marker-edit-banner` | `viewer.js` | (shown/hidden by `_vaUpdateEditBanner`) | — |
| `va-save-adjustments-btn` | `viewer.js` | `click` → apply cache → clear → reload poses | `POST /dlc/viewer/save-marker-edits` |
| `va-discard-adjustments-btn` | `viewer.js` | `click` → clear `_vaLocalEdits` + redraw | — |

### Curation Buttons

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `va-extract-frame-btn` | `viewer.js` | `click` → extract PNG | `POST /dlc/curator/extract-frame` |
| `va-add-to-dataset-btn` | `viewer.js` | `click` → add frame to CollectedData | `POST /dlc/curator/add-to-dataset` |
| `va-batch-add-btn` | `viewer.js` | `click` → batch add N frames at step S | `POST /dlc/curator/add-to-dataset` (loop) |
| `va-create-csv-btn` | `viewer.js` | `click` → create companion CSV | `POST /annotate/create-csv` |
| `va-save-status-btn` | `viewer.js` | `click` → save frame status annotation | `POST /annotate/save-row` |
| `va-save-note-btn` | `viewer.js` | `click` → save frame note annotation | `POST /annotate/save-row` |

### Timeline Canvas Elements

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `va-status-canvas` | `viewer.js` | `click` → seek to frame at x position | — |
| `va-note-canvas` | `viewer.js` | `click` → seek to frame at x position | — |
| `va-status-chips` | `viewer.js` | chip `click` → toggle status value on timeline | — |
| `va-note-chips` | `viewer.js` | chip `click` → toggle note value on timeline | — |
| `va-status-prev-btn` | `viewer.js` | `click` → jump to previous status-annotated frame | — |
| `va-status-next-btn` | `viewer.js` | `click` → jump to next status-annotated frame | — |
| `va-note-prev-btn` | `viewer.js` | `click` → jump to previous note-annotated frame | — |
| `va-note-next-btn` | `viewer.js` | `click` → jump to next note-annotated frame | — |

### File Browser (Browse Tab)

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `va-tab-browse` | `viewer.js` | `click` → show browse panel | `GET /fs/ls` (initial load) |
| `va-tab-project` | `viewer.js` | `click` → show project panel | — |
| `va-browse-breadcrumb` | `viewer.js` | `keydown Enter` → navigate to typed path; `paste` → navigate | `GET /fs/ls` |
| `va-browse-up` | `viewer.js` | `click` → navigate to parent directory | `GET /fs/ls` |

---

## Annotator Card (ANV) — `annotator.js`

### Video Player Controls

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `anv-btn-play` | `annotator.js` | `click` → toggle play/pause | `GET /dlc/project/video-frame-ext/<n>` |
| `anv-btn-prev` | `annotator.js` | `click` → `_anvLoadFrame(n-1)` | same |
| `anv-btn-next` | `annotator.js` | `click` → `_anvLoadFrame(n+1)` | same |
| `anv-btn-skip-back` | `annotator.js` | `click` → ±skipN | same |
| `anv-btn-skip-fwd` | `annotator.js` | `click` → ±skipN | same |
| `anv-seek` | `annotator.js` | `input` / `change` → load frame | same |
| `anv-frame-counter` | `annotator.js` | `dblclick` → show jump input | — |
| `anv-frame-jump` | `annotator.js` | `keydown Enter` → jump to frame; `blur` → commit | same |

Keyboard: `ArrowLeft`/`ArrowRight` (card-scoped) = ±1; `Ctrl+Arrow` (document-scoped) = ±skipN.

### File Browser Buttons

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `anv-browse-btn` | `annotator.js` | `click` → toggle `anv-browser` dir listing | `GET /fs/ls` |
| `anv-load-btn` | `annotator.js` | `click` → load video from `anv-video-path` | `GET /annotate/video-info` |
| `anv-refresh-csv-btn` | `annotator.js` | `click` → reload companion CSV | `GET /annotate/csv` |
| `anv-create-csv-btn` | `annotator.js` | `click` → create companion CSV | `POST /annotate/create-csv` |

### Timeline Canvas Elements

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `anv-status-canvas` | `annotator.js` | `click` → snap to nearest annotated frame | — |
| `anv-note-canvas` | `annotator.js` | `click` → snap to nearest annotated frame | — |
| `anv-status-chips` | `annotator.js` | chip `click` → toggle status value on timeline | — |
| `anv-note-chips` | `annotator.js` | chip `click` → toggle note value on timeline | — |
| `anv-status-prev-btn` | `annotator.js` | `click` → previous status frame | — |
| `anv-status-next-btn` | `annotator.js` | `click` → next status frame | — |
| `anv-note-prev-btn` | `annotator.js` | `click` → previous note frame | — |
| `anv-note-next-btn` | `annotator.js` | `click` → next note frame | — |

### Annotation Save Buttons

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `anv-save-status-btn` | `annotator.js` | `click` → save frame status | `POST /annotate/save-row` |
| `anv-save-note-btn` | `annotator.js` | `click` → save frame note | `POST /annotate/save-row` |
| `anv-clip-btn` | `annotator.js` | `click` → extract video clip | `POST /annotate/crop-video` |
| `anv-clip-browse-btn` | `annotator.js` | `click` → toggle clip output dir browser | `GET /fs/ls` |

---

## DLC Project Card — `dlc_project.js`

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `btn-manage-dlc` | `dlc_project.js` | `click` → open project card | — |
| `dlc-browse-btn` | `dlc_project.js` | `click` → open folder browser | `GET /fs/ls` |
| `dlc-browse-up` | `dlc_project.js` | `click` → navigate parent | `GET /fs/ls` |
| `dlc-browse-breadcrumb` | `dlc_project.js` | `keydown Enter` → navigate | `GET /fs/ls` |
| `dlc-select-btn` | `dlc_project.js` | `click` → set active project | `POST /dlc/project` |
| `dlc-refresh-btn` | `dlc_project.js` | `click` → reload project state | `GET /dlc/project` |

---

## Training Cards — `training.js`

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `save-dlc-config-btn` | `training.js` | `click` → save config.yaml | `PATCH /dlc/project/config` |
| `btn-run-create-training-dataset` | `training.js` | `click` → dispatch create-dataset task | `POST /dlc/project/create-training-dataset` |
| `btn-run-train-network` | `training.js` | `click` → dispatch train-network task | `POST /dlc/project/train-network` |
| `btn-stop-train-network` | `training.js` | `click` → revoke training task | `POST /dlc/training/stop` |
| `ctd-pytorch-config-select` | `training.js` | `change` → load selected PyTorch config | `GET /dlc/config/pytorch` |
| `ctd-save-pytorch-btn` | `training.js` | `click` → save PyTorch config | `POST /dlc/config/pytorch` |

---

## Analyze Card — `analyze.js`

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `btn-run-analyze` | `analyze.js` | `click` → dispatch analyze task | `POST /dlc/project/analyze` |
| `btn-stop-analyze` | `analyze.js` | `click` → revoke analyze task | `POST /dlc/inference/stop` |
| `av-browse-btn` | `analyze.js` | `click` → open file browser | `GET /fs/ls` |
| `av-refresh-snapshots` | `analyze.js` | `click` → reload snapshot list | `GET /dlc/project/snapshots` |

---

## GPU Monitor Card — `gpu_monitor.js`

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `btn-open-gpu-monitor` | `gpu_monitor.js` | `click` → open card + start 5 s poll | `GET /dlc/monitoring/gpu-status` |
| `gm-refresh-btn` | `gpu_monitor.js` | `click` → manual refresh | `GET /dlc/monitoring/gpu-status`, `GET /dlc/monitoring/jobs` |
| `gm-clear-btn` | `gpu_monitor.js` | `click` → clear completed jobs | `POST /dlc/training/jobs/clear` |
| `gm-cancel-all-btn` | `gpu_monitor.js` | `click` → revoke all active tasks | `POST /dlc/training/jobs/cancel-all` |

---

## Anipose / Session Bar — `anipose.js`

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `btn-create-session` | `anipose.js` | `click` → create Anipose session | `POST /session` |
| `btn-clear-session` | `anipose.js` | `click` → delete session | `DELETE /session` |
| `btn-session-from-server` | `anipose.js` | `click` → load session from server path | `POST /session/from-path` |
| `save-config-btn` | `anipose.js` | `click` → save session config | `POST /session/config` |
| `pipeline-btn-mediapipe` | `anipose.js` | `click` → run MediaPipe step | `POST /run` |
| `pipeline-btn-deeplabcut` | `anipose.js` | `click` → run DLC step | `POST /run` |
| `new-job-btn` | `anipose.js` | `click` → dispatch new Anipose pipeline job | `POST /run` |

---

## Custom Script Card — `custom_script.js`

| DOM ID | JS Module | Event | Backend Endpoint |
|--------|-----------|-------|-----------------|
| `cs-run-btn` | `custom_script.js` | `click` → run user script | `POST /custom-script/run` |
| `cs-abort-btn` | `custom_script.js` | `click` → abort running job | `POST /custom-script/abort` |
| `cs-script-browse-btn` | `custom_script.js` | `click` → open script file browser | `GET /fs/ls` |
| `cs-input-browse-btn` | `custom_script.js` | `click` → open input path browser | `GET /fs/ls` |
| `cs-input-mode-file` | `custom_script.js` | `click` → set input mode = file | — |
| `cs-input-mode-folder` | `custom_script.js` | `click` → set input mode = folder | — |
| `inspect-video-btn` | `custom_script.js` | `click` → open inspector overlay | — |

---

## Notes

- All task-dispatching buttons poll `GET /status/<task_id>` after dispatch to stream progress.
- Canvas timelines (`*-status-canvas`, `*-note-canvas`) use `fillRect` — zero DOM nodes per frame. Never replace with per-frame DOM nodes.
- File browsers (`anv-browser`, `va-browse-list`, `va-overlay-h5-browser`, `cs-script-nav`, `cs-input-nav`) all call `GET /fs/ls?path=<encoded>` and rebuild their contents dynamically.
