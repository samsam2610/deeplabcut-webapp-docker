# Claude Code — Project Guide

## Project Overview

Dockerised web app for DeepLabCut pose-estimation workflows.
Three services: **flask** (web UI + API), **worker** (PyTorch Celery), **worker-tf** (TensorFlow Celery).
Redis is the broker and the project-state store.

```
src/
├── app.py                  ← Flask app factory; registers all blueprints
├── tasks.py                ← Celery worker entry-point; imports dlc/tasks.py
├── dlc/                    ← All DLC blueprints (see dlc/README.md for route map)
│   ├── vlm_indexer.py      ← VLM / KNN logic (no Flask)
│   ├── vlm_routes.py       ← /vlm/* Flask blueprint
│   └── tasks.py            ← Celery task implementations
├── static/js/vlm_refiner.js
└── templates/vlm_refiner.html
tests/
└── test_vlm_verification.py   ← 37 tests; always run before declaring a VLM fix done
```

**Run tests:**
```bash
python -m pytest tests/test_vlm_verification.py -q   # fast, no GPU needed
python -m pytest -q                                   # full suite
```

---

## GPU Routing

| GPU | CUDA index | Role |
|-----|-----------|------|
| RTX 5090 | `CUDA_VISIBLE_DEVICES=0` | All DLC processes |
| RTX PRO 6000 Blackwell | `CUDA_VISIBLE_DEVICES=1` | Orchestrator / local LLM |

Celery tasks that spawn GPU subprocesses must inject `CUDA_VISIBLE_DEVICES=0` into the child env.

---

## VLM Label Verification System

`GET /vlm/refiner` → three-panel UI (`vlm_refiner.html` / `vlm_refiner.js`).

### Three label layers

| Layer | Key | Colour | Source |
|-------|-----|--------|--------|
| M — Machine | `state.machineCoords` | green | `_machine_predictions_raw.csv` (or CollectedData fallback) |
| V — VLM | `state.vlmCoords` | yellow | `/vlm/refine` → Ollama qwen3-vl:32b |
| H — Human | `state.humanCoords` | indigo | User edits; saved to CollectedData CSV |

Toggle buttons switch which layer is drawn on canvas and shown in coord list.

### Data flow

```
/vlm/frame-data  →  current_labels (M)  +  vlm_coords (saved V, if any)
/vlm/refine      →  vlm_coords (new V)  +  saved to _vlm_results.json
/vlm/similar     →  KNN top-k from vlm_index.json
```

### Key files on disk (per labeled-data stem)

| File | Written by | Purpose |
|------|-----------|---------|
| `CollectedData_<scorer>.csv` | Human labeling | Ground truth human labels |
| `_machine_predictions_raw.h5` | `dlc_machine_label_frames` Celery task | All predictions + likelihood |
| `_machine_predictions_raw.csv` | `tasks.py` (at write time) OR `_ensure_raw_pred_csv()` (lazy) | Flat CSV for lh filtering without HDF5 in Flask |
| `_vlm_results.json` | `/vlm/refine` route | Persisted VLM coords per frame (upsert) |
| `vlm_index.json` | `/vlm/index/build` route | KNN pixel-vector index |

### `_machine_predictions_raw.csv` generation

The Flask container does **not** have HDF5 support. `vlm_indexer._ensure_raw_pred_csv(stem_dir)`:

1. CSV already exists → return `True` immediately.
2. `.h5` exists → reads with `pandas.read_hdf` + `tables` (available in Flask container) → writes CSV → returns `True`.
3. Neither → returns `False`.

**Host Python lacks `tables`** (numpy ABI mismatch). Always test `_ensure_raw_pred_csv` inside the Docker container:

```bash
docker exec $(docker ps --filter "name=flask" -q) python3 -c "
import sys; sys.path.insert(0, '/app')
from pathlib import Path; from dlc import vlm_indexer as vi
stem = Path('/user-data/Parra-Data/Disk/DLC-Projects/DREADD-Ali-2026-01-07/labeled-data/MAP2_20250715_120050_0')
print(vi._ensure_raw_pred_csv(stem))
"
```

### Critical: machine-coord fallback in `/vlm/frame-data`

`read_raw_predictions` returns `None` when no CSV/H5 exists, OR a dict when it does.
If it returns a dict but the requested frame is absent (frame was manually labeled before
machine labeling ran), the raw dict has no entry for that frame.

**Route logic** (`vlm_routes.py`):

```python
raw_labels = vi.read_raw_predictions(stem_dir, min_lh=min_lh)
if raw_labels is not None:
    current_labels = raw_labels.get(frame, {})
# ALWAYS fall back to CollectedData when current_labels is empty
if not current_labels:
    csv_path = ...  # CollectedData_<scorer>.csv
    if csv_path.is_file():
        current_labels = vi._read_labels_from_csv(csv_path).get(frame, {})
```

**Do not revert to `else` here.** If `current_labels = {}`, VLM gets no machine coords → all
bodyparts return `no_machine_coord` → all `vlm_coords = None` → V mode shows nothing ("empty VLM" bug).

### VLM refine — batch calls

`refine_coords_with_vlm` sends bodyparts to Ollama in chunks of `MAX_BATCH=5` (≤10 images per call).
Format: `{"role": "user", "content": <prompt_str>, "images": [<base64>, ...]}` — **not** OpenAI content-array format.
Ollama rejects array content with HTTP 400.

On Ollama failure, machine coords are used as fallback and `debug[bp]["reason"] = "ollama_failed"`.

### VLM result persistence

- **Save:** `/vlm/refine` calls `vi.save_vlm_result(stem_dir, frame, vlm_coords, vlm_debug)` → upserts `_vlm_results.json`.
- **Load:** `/vlm/frame-data` calls `vi.load_vlm_result(stem_dir, frame)` → returned as `vlm_coords` in response.
- **JS restore:** `selectFrame()` checks `data.vlm_coords` and restores `state.vlmCoords` if non-empty.
- **Invariant:** `reloadMachineCoords()` (called by lh slider + ref-stem picker) **must not** reset `state.vlmCoords`. It only updates `state.machineCoords`.

### Likelihood slider

- Reads `_machine_predictions_raw.csv` (or h5→csv on demand).
- Passes `min_lh` as query param to `/vlm/frame-data`.
- Filters **bodypart coords per frame** (hides low-likelihood bodyparts), NOT which frames appear in the list.
- Slider is disabled when no raw predictions file exists for the stem.

---

## Tests

### Running

```bash
python -m pytest tests/test_vlm_verification.py -q    # 37 tests, < 1s
```

### Key test classes

| Class | Coverage |
|-------|---------|
| `TestReferencePanelKNN` | Index build, find_similar, reference-image route |
| `TestToggleUI` | M/V/H layer separation via routes |
| `TestOriginalProjectUnmodified` | No side-effects on original project |
| `TestPatchBasedVlmRefine` | Crop + offset logic, batch chunks, key normalisation |
| `TestVlmResultPersistence` | save/load _vlm_results.json, upsert, badge |
| `TestLikelihoodFilter` | CSV read, lh filtering, `_ensure_raw_pred_csv`, fallback |
| `TestRealProjectIntegration` | **Real on-disk data** — skipped on machines without the project |

### Real-project integration tests

`TestRealProjectIntegration` uses `ORIGINAL_DLC_PROJECT` (defined in `tests/conftest.py`).
These tests are **required** when debugging VLM/UI bugs because synthetic tests cannot catch
the "frame absent from raw CSV" class of failure.

Always run these before closing a VLM bug fix:
1. `test_read_labels_from_csv_real_stem` — confirms CSV parsing works on actual data.
2. `test_frame_data_always_returns_machine_coords` — directly reproduces the "frame not in raw CSV" bug.
3. `test_refine_with_real_frames_mocked_ollama` — end-to-end with real images, mocked Ollama, save/load round-trip.

---

## Ollama

- URL: `http://172.26.0.1:11434` (set via `OLLAMA_URL` env in docker-compose).
- Model: `qwen3-vl:32b` (vision). Text-only fallback: any qwen3 variant.
- Reachable from inside Docker containers (verified).
- `_ollama_chat` returns `(content: str | None, error: str)` tuple — callers must unpack both.

---

## Real Project on Disk

```
/home/sam/data-disk/Parra-Data/DLC-Projects/DREADD-Ali-2026-01-07/
  config.yaml           (scorer: Ali, 16 bodyparts including MCP-1, PIP-1, DIP-1, ...)
  labeled-data/
    MAP2_20250715_120050_0/     ← 148 frames, has h5 + csv (after first VLM run)
    output_20250711_173455_2/   ← 20 frames, no h5/csv (human-only labels)
    output_20250715_162448_2/   ← 20 frames, no h5/csv
    ...                         ← ~14 stems total
  vlm_index.json        (if built)
```

Inside Docker, this mounts as `/user-data/Parra-Data/Disk/DLC-Projects/DREADD-Ali-2026-01-07/`.

---

## Common Debugging Commands

```bash
# Check what's in a stem
ls /home/sam/data-disk/Parra-Data/DLC-Projects/DREADD-Ali-2026-01-07/labeled-data/MAP2_20250715_120050_0/

# Test CSV generation in Docker
docker exec $(docker ps --filter "name=flask" -q) python3 -c "
import sys; sys.path.insert(0, '/app')
from pathlib import Path; from dlc import vlm_indexer as vi
PROJECT = Path('/user-data/Parra-Data/Disk/DLC-Projects/DREADD-Ali-2026-01-07')
stem_dir = PROJECT / 'labeled-data' / 'MAP2_20250715_120050_0'
print('ensure CSV:', vi._ensure_raw_pred_csv(stem_dir))
raw = vi.read_raw_predictions(stem_dir)
if raw:
    frame = sorted(raw.keys())[0]
    print('sample frame:', frame, {k: v for k,v in list(raw[frame].items())[:3]})
"

# Check VLM results saved for a stem
ls /home/sam/data-disk/Parra-Data/DLC-Projects/DREADD-Ali-2026-01-07/labeled-data/MAP2_20250715_120050_0/_vlm_results.json

# Flask logs
docker logs $(docker ps --filter "name=flask" -q) --tail 50

# Full VLM refine test with real data (mocked Ollama, host Python)
python -m pytest tests/test_vlm_verification.py::TestRealProjectIntegration -v
```

---

## Adding Support Modules

Support modules live entirely in `../deeplabcut-webapp-docker-supports/<module-name>/` and run as independent Docker containers. They are reachable at `http://localhost:5000/<module-name>/` — this app reverse-proxies that prefix to the module's container on the internal Docker network.

When a new module is added, make exactly these changes to this project:

1. **`docker-compose.yml`** — add the module as a new service on the internal network with no exposed host port.
2. **`base.html`** — add one nav button linking to `/<module-name>/`.
3. **One proxy route** in the Flask app forwarding `/<module-name>/*` to `http://<module-name>:<internal-port>/*`.

Do not add blueprints, business logic, templates, or static assets for support modules here. All of that lives in the module's own subdirectory.

---

## Branch

Current feature branch: `feat/vlm-reference-verify`
