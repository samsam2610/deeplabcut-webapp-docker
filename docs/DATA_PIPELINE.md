# DATA PIPELINE
> Frame lifecycle: from raw video to annotated HDF5. Token-optimized reference.

---

## 1. CollectedData Format (DLC Standard)

### CSV Layout (3 header rows + N data rows)

```
scorer,    ,       , DLC_resnet50_...  , DLC_resnet50_...  , ...
bodyparts, ,       , forepaw_L         , forepaw_L         , ...
coords,    ,       , x                 , y                 , ...
labeled-data, videoStem, img0001-00042.png, 312.4 , 198.7, ...
```

- **Index**: 3-column MultiIndex — `("labeled-data", video_stem, frame_name)`
- **Columns**: 3-level MultiIndex — `(scorer, bodypart, "x"|"y")`
- Index `names = ["", "", ""]` (empty strings, not None)
- Column `names = ["scorer", "bodyparts", "coords"]`
- No `likelihood` column in `CollectedData` (only in `*DLC_*.h5` analysis output)

### HDF5 Key

```python
pd.read_hdf(path, key="df_with_missing")
data.to_hdf(path, key="df_with_missing", mode="w")
```

---

## 2. Frame Naming Convention

```
img{seq_index:04d}-{abs_frame_number:05d}.png
```

- `seq_index`: extraction order (0, 1, 2, …), increments by 1 per extracted frame
- `abs_frame_number`: actual 0-based frame index in the source video

| Pattern | Example | abs frame |
|---------|---------|-----------|
| `img{seq}-{abs}.png` | `img0003-01582.png` | 1582 |
| `frame{N}.png` | `frame0050.png` | 50 |
| `img{N}.png` | `img0042.png` | 42 |

Parser: `dlc_tapnet_tracker.parse_frame_number(filename)`

---

## 3. Frame Extraction → PNG

**Entry point**: `dlc_dataset_curator.extract_frame_as_png()`

```
video_path + frame_number
        │
        ▼
cv2.VideoCapture.set(CAP_PROP_POS_FRAMES, frame_number)
        │
        ▼  Duplicate guard: scan existing img????-NNNNN.png
        │  → if abs_frame_number already present: return (existing_path, True)
        │
        ▼
cv2.imencode(".png", frame)  ← lossless
        │
        ▼
output_dir / f"img{seq_index:04d}-{frame_number:05d}.png"
```

**Side-effects**: Creates PNG file. No GPU. No pandas.

---

## 4. Append Frame to Dataset

**Entry point**: `dlc_dataset_curator.append_frame_to_dataset()`

```
stem_dir / CollectedData_{scorer}.csv
        │
        ▼  _read_labels_csv()  →  {frame_name: {bp: [x,y]|None}}
        │
        ▼  if frame_name not in labels: insert with coords (or NaN)
        │
        ▼  _write_labels_csv()  →  overwrites CSV (3 headers + all rows)
        │
        ▼  rebuild_h5_from_csv()
              │
              ▼  pd.read_csv(index_col=[0,1,2], header=[0,1,2])
              │  ← auto-detects 4-level (multi-individual) if "individuals" in row 1
              │
              ▼  data.to_hdf(tmp_path, key="df_with_missing", mode="w")
              │
              ▼  tmp_path.replace(h5_path)  ← atomic rename
```

**Fragile points**:
- CSV must have exactly 3 header rows; 4-level format uses `individuals` sentinel
- `index_col` detection: first data cell must equal `"labeled-data"` for 3-column index
- H5 key must be `"df_with_missing"` — DLC hardcodes this string

---

## 5. Update Annotation (Curator)

**Entry point**: `dlc_dataset_curator.update_frame_annotation()`

```
existing labels = _read_labels_csv(csv)
        │
        ▼  merged = {bp: existing.get(bp) for bp in bodyparts}
           for bp, pt in coords.items():
               if bp in merged: merged[bp] = pt
           labels[frame_name] = merged
        │
        ▼  _write_labels_csv()  →  rebuild_h5_from_csv()
```

**Key behavior**: Only listed `bodyparts` survive; bodyparts absent from the project's `config.yaml` list are silently dropped.

---

## 6. TAPNet Label Propagation Pipeline

**Entry point**: `dlc_tapnet_tracker.propagate_labels()` (single-anchor) or `propagate_labels_multi_anchor()`

```
labeled-data/{video_stem}/
    CollectedData_{scorer}.csv   ← source of truth
    img????.png files            ← input frames

        │
        ▼  find_consecutive_sequences(frame_names)
           → groups frames by extraction-order seq index (not abs frame number)

        │  For each sequence:
        ▼  dlc_to_tapnet_points(df, anchor_frame)
           → query_points: np.ndarray shape (1, N_bodyparts, 3)  [t=0, y, x]

        │
        ▼  run_tapnet_inference(frame_paths, query_points, checkpoint_path, gpu_index=0)
           │
           ▼  Subprocess: CUDA_VISIBLE_DEVICES=0
              Lazy-import: jax, tapir_model, cv2
              Load TAPIR checkpoint (.npy)
              Resize frames to 256×256
              Run TAPIR inference → tracks, visibilities
              Save to temp .npy files
              sys.exit(0)   ← VRAM freed immediately
           │
           ▼  Parent reads .npy files from temp dir

        │
        ▼  tapnet_to_dlc_labels(tracks, visibilities, bodyparts, frame_names, scorer, video_stem)
           → pd.DataFrame with same MultiIndex structure as CollectedData

        │
        ▼  Merge: skip frames already labeled by human (overwrite_existing=False)
           pd.DataFrame.combine_first() or row-wise merge

        │
        ▼  Write updated CollectedData_{scorer}.csv
           (rebuild_h5_from_csv called by caller or labeling.py)

Sidecar files:
  _tapnet_frames.json   ← set of frames generated by TAPNet (not human-labeled)
  _confirmed_anchors.json  ← anchor frames confirmed by human review
```

---

## 7. Multi-Anchor Propagation

```
Anchor frames: [A0, A1, A2, ...]  (sorted by abs frame number)

Segments processed in order:
  A0 → A1 : anchor=A0, frames=[A0, ..., A1-1]
  A1 → A2 : anchor=A1, frames=[A1, ..., A2-1]
  ...
  AN → end: anchor=AN, frames=[AN, ..., last]

Rules:
  - Anchor frames NEVER overwritten
  - Frames already in _confirmed_anchors.json always preserved
  - Each segment runs independent TAPNet subprocess (VRAM freed between segments)
```

---

## 8. Viewer H5 Read Path

**Entry point**: `dlc/viewer.py → viewer_load_h5(h5_path)`

```
h5_path
    │
    ▼  LRU cache check (_viewer_h5_cache, max 5 entries)
    │
    ▼  pd.read_hdf(h5_path, key="df_with_missing")
    │
    ▼  Extract: scorer = df.columns.get_level_values("scorer")[0]
               bodyparts = df.columns.get_level_values("bodyparts").unique().tolist()
    │
    ▼  Cache entry: {df, scorer, bodyparts}

Per-frame render:
    df.loc[("labeled-data", video_stem, frame_name)]
    → Series indexed by (scorer, bodypart, coord)
    → cv2.circle on decoded video frame
    → Return JPEG bytes
```

**Threshold filter**: likelihood column (if present in analysis H5) gates marker visibility.

---

## 9. Atomic Write Pattern

All H5 writes use this pattern to prevent corrupt files on crash:

```python
tmp_path = Path(str(h5_path) + ".tmp")
data.to_hdf(str(tmp_path), key="df_with_missing", mode="w")
tmp_path.replace(h5_path)   # atomic on POSIX
```

If `.tmp` file is present on startup → write was interrupted, H5 is safe (old version intact).

---

## 10. JSON Delta Edit-Cache Lifecycle

Interactive marker adjustments are stored in a lightweight hidden JSON file **co-located with the analysis H5**, never modifying the H5 until the user explicitly saves.

### Cache file naming

```
{h5_dir}/.{h5_stem}_edits.json
```

**Critical:** The cache filename is dynamically derived from the **H5 stem**, not the directory. This prevents collisions when multiple video H5 files share the same folder (e.g., `MAP1_…DLC.h5` and `MAP2_…DLC.h5` each get their own cache).

| H5 path | Edit cache path |
|---------|----------------|
| `/data/MAP1_20250713_DLC_resnet50.h5` | `/data/.MAP1_20250713_DLC_resnet50_edits.json` |
| `/data/MAP2_20250713_DLC_resnet50.h5` | `/data/.MAP2_20250713_DLC_resnet50_edits.json` |

### Cache format

```json
{
  "frame_0":  {"Snout": {"x": 123.4, "y": 456.7}},
  "frame_42": {"forepaw_L": {"x": 300.5, "y": 410.2}}
}
```

### Lifecycle

```
User drags marker on canvas
        │
        ▼  POST /dlc/viewer/marker-edit  {h5, frame, bp, x, y}
           save_edit_cache(h5_path, cache)  ← atomic .tmp → rename
           H5 and CSV are NOT modified
        │
        ▼  frame-poses/<n> and frame-poses-batch
           _get_effective_poses()  ← edit-cache overrides win
           Edited keypoints returned with likelihood=1.0 to client
        │
        ▼  "Save Adjustments" button clicked
           POST /dlc/viewer/save-marker-edits  {h5}
           _apply_marker_edits_to_h5(h5, cache)
              ├─ Load H5 with pd.read_hdf(key="df_with_missing")
              ├─ Patch x, y, likelihood=1.0 for each edited keypoint
              ├─ Write H5 atomically (.tmp → rename)
              ├─ Regenerate companion CSV with df.to_csv()
              └─ Evict H5 from LRU cache (_viewer_h5_cache)
           clear_edit_cache(h5)  ← delete hidden JSON file
```

### Constraints

- The H5 LRU cache (`_viewer_h5_cache`) is **evicted** after every save so the next request re-reads from disk.
- Edits for frame indices ≥ `len(df)` are silently dropped (out-of-range guard).
- Unknown bodypart names are silently skipped.
- The `.tmp` cache file (`.{stem}_edits.json.tmp`) is cleaned up on successful atomic write.

---

## 11. config.yaml Sanitization

DLC's `config.yaml` stores video paths as YAML mapping keys. When paths contain spaces, `ruamel.yaml` writes broken multi-line plain scalars on round-trip. `dlc/tasks.py:_sanitize_dlc_config_yaml()` normalizes two patterns before every DLC API call:

| Pattern | Input | Normalized |
|---------|-------|-----------|
| A (explicit key `?`) | `? /data/foo bar\n  : crop: …` | `"/data/foo bar":\n  crop: …` |
| B (split scalar) | `/data/foo\n  bar.avi:\n  crop:` | `"/data/foo bar.avi":\n  crop:` |

---

## 12. Analyze Batch Submission Pipeline

**Frontend → Backend → Worker**:

```
_avBatchList (JS array of absolute paths)
        │  POST /dlc/project/analyze
        │  body: { target_paths: [...], shuffle, create_labeled, … }
        ▼
dlc_project_analyze() — inference.py
  • resolves target_paths (or legacy target_path scalar → [scalar])
  • validates each path exists
  • for each path: celery.send_task("tasks.dlc_analyze", kwargs={target_path, params})
  • returns { task_ids: [...], task_id: task_ids[0] }
        │
        ▼  (one Celery task per path, queued in dlc-pytorch or dlc-tf)
_dlc_analyze_subprocess(config_path, target_path, params, log_path)
  • file (.mp4/.avi/…) → analyze_videos(config, [path], **kw)
  • image file (.jpg/…) → analyze_time_lapse_frames(config, parent_dir, **kw)
  • directory         → iterdir() non-recursive, dispatches videos + images separately
  • if create_labeled: create_labeled_video(config, paths, **label_kw)
```

**Key constraints**:
- Each path is an independent Celery task; GPU pool serializes them (one at a time on GPU 0).
- `target_paths` is validated server-side — missing paths return 400 before any task is dispatched.
- Legacy `target_path` (single string) is auto-promoted to `[target_path]` for backward compat.

---

## 13. Viewer Playback Rendering Pipeline (Frame–Marker Synchronization)

**Problem solved:** The old `setInterval`-based loop fired at fixed wall-clock intervals regardless of render completion. When the server was slow, `_vaFrameBusy` silently dropped ticks (frame skips). The overlay markers were also drawn synchronously after `vaFrameImg.onload`, which runs in the same compositor tick as the old frame — markers appeared one frame behind the video (desync flicker).

**Enforced pipeline (viewer.js `_vaLoadFrame` + `_vaPlayLoop`):**

```
_vaPlayLoop() — self-scheduling async loop (NOT setInterval)
        │
        ▼  fetch next frame blob
        fetch(_vaFrameUrl(n))  →  blob → createObjectURL → vaFrameImg.src
        │
        ▼  wait for image decode (browser paints new frame)
        await new Promise(onload)
        │
        ▼  *** PAINT BARRIER ***
        await new Promise(resolve => requestAnimationFrame(resolve))
        │  The browser compositor has now committed the new video frame.
        │  Only AFTER this rAF do we draw markers — guaranteeing both
        │  the image and its markers are painted in the same visual frame.
        ▼
        _vaUpdateOverlay(n)   — draw pose markers from pose cache
        │
        ▼  schedule next tick
        _vaPlayTimeoutId = setTimeout(_vaPlayLoop, max(0, interval - elapsed))
        │  Elapsed render time is subtracted so fast frames stay on pace;
        │  slow frames clamp to 0 (next tick fires immediately, no debt).
```

**Pose cache pre-warm on play start:**
When `va-btn-play` starts playback, `_vaFetchPosesWindow(currentFrame)` is called immediately to populate the next `_POSE_WINDOW` (30) frames in the background. `_vaUpdateOverlay` then draws from this cache with zero server round-trips per frame.

**Stop path (`_vaStopPlayback`):**
Cancels `_vaPlayTimeoutId` via `clearTimeout`, resets `_vaPlayTimer` sentinel to `null`, and restores play/pause icons atomically.

**Jitter-free layout guarantee:**
`.fe-controls` has `flex:none; min-height:2.4rem` and sits immediately below the canvas. The bodypart chip list (`va-bp-list-wrap`) is positioned below the seek slider — chip additions/removals cannot shift the control buttons.
