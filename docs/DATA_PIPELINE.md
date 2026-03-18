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

## 10. config.yaml Sanitization

DLC's `config.yaml` stores video paths as YAML mapping keys. When paths contain spaces, `ruamel.yaml` writes broken multi-line plain scalars on round-trip. `dlc/tasks.py:_sanitize_dlc_config_yaml()` normalizes two patterns before every DLC API call:

| Pattern | Input | Normalized |
|---------|-------|-----------|
| A (explicit key `?`) | `? /data/foo bar\n  : crop: …` | `"/data/foo bar":\n  crop: …` |
| B (split scalar) | `/data/foo\n  bar.avi:\n  crop:` | `"/data/foo bar.avi":\n  crop:` |
