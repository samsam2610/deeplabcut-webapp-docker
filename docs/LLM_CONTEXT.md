# LLM CONTEXT
> Hyper-condensed system context for future LLM agent sessions.
> Load this file first. ~1800 tokens.

---

## System Identity

**Project**: DeepLabCut WebApp — Flask + Celery + Redis microservice stack for pose estimation, 3D triangulation, and active-learning frame curation.
**Stack**: Python 3.10 (Flask) / Python 3.11 (Worker) · Docker Compose · PyTorch 2.9.1 · CUDA 13.0
**Repo root**: `/home/sam/docker-images/deeplabcut-webapp-dlc-refactor/`
**Working branch convention**: `feature/<topic>`

---

## HARD CONSTRAINTS (never violate)

1. **GPU 0 = RTX 5090** → All DLC, TAPNet, Anipose tasks. Enforced via `CUDA_VISIBLE_DEVICES=0` in worker env and subprocess spawning.
2. **GPU 1 = Blackwell A6000** → LLM/orchestrator ONLY. This codebase must NEVER reference GPU 1 in any task.
3. **VRAM teardown** → TAPNet runs in an isolated subprocess that calls `sys.exit(0)` after saving results. DLC runs in subprocess via `deeplabcut` Python API. Neither holds VRAM after task completion.
4. **Sandbox all tests** → Reference data at `/user-data/Parra-Data/Cloud/Reaching-Task-Data/RatBox Videos/MAPS-DREADSS/`. NEVER modify originals. Use `tmp_path` fixtures from `tests/conftest.py`.
5. **H5 atomic writes** → Always write to `.tmp` then `Path.replace()`. Key must be `"df_with_missing"`. Never use `mode="a"` (append) on H5 — full rebuild from CSV only.
6. **Dockers have live-mounted `src/`** → Worker mounts `./src:/app`. Flask mounts individual files. Code changes take effect on service restart, not rebuild.

---

## Established Design Patterns

- **Shared context via `dlc/ctx.py`** — Blueprint modules never import from `app.py`. `setup()` called in `before_request`; accessor functions `data_dir()`, `redis_client()`, etc.
- **Per-session Redis state** — Keys: `webapp:session:{uid}` (Anipose config) and `webapp:dlc_project:{uid}` (DLC project path + engine).
- **Celery queue routing** — `pytorch` queue → `worker`; `tensorflow` queue → `worker-tf`; `celery` queue → `worker`. Engine derived from `config.yaml` by `dlc.utils._get_engine_queue()`.
- **LRU caches** — `dlc/videos.py` (vcap, 20 entries), `dlc/viewer.py` (vcap 10, H5 5). All `threading.Lock`-protected `OrderedDict`s. `move_to_end()` on hit, pop from left on capacity exceeded.
- **Security checks on all file routes** — `_dlc_project_security_check(path, DATA_DIR, USER_DATA_DIR)` returns False for paths outside both roots. 403 on failure.
- **Config sanitization before every DLC call** — `_sanitize_dlc_config_yaml()` in `dlc/tasks.py` fixes ruamel.yaml multi-line key bug (space-containing video paths).
- **TAPNet subprocess isolation** — Parent writes query points as `.npy` to tempdir, spawns subprocess with `CUDA_VISIBLE_DEVICES=0`, subprocess exits after saving `tracks.npy`/`visibilities.npy`, parent reads results. Hard-kill on timeout. Stubs `tensorflow_datasets` to avoid unused-dep crash.
- **CSV is source of truth** — H5 is always a derived artifact rebuilt from CSV via `rebuild_h5_from_csv()`. Never edit H5 directly.
- **Frame naming** — Primary: `img{seq:04d}-{abs:05d}.png`. `seq`=extraction order; `abs`=video frame index. TAPNet uses `seq` for consecutive sequence detection, not `abs`.
- **Frontend IIFEs (legacy)** — Original `src/static/main.js` used one `(() => { ... })()` per UI card. Superseded by ES module split (see Frontend Design Patterns below). DOM IDs remain the API surface.

---

## Frontend Design Patterns

- **CSS custom properties** — All theme tokens defined in `src/static/css/variables.css` under `:root` (`--bg`, `--surface`, `--accent`, `--border`, `--text-dim`, etc.). Never hardcode colors; always reference variables.
- **DLC theme override** — Sections using DLC-specific UI apply `.dlc-theme` class, which overrides `--accent` to indigo. Defined in `components.css`.
- **No BEM** — Flat, descriptive class names (`fe-video-item`, `fe-extract-status`, `fe-tag-chip`). Mirrors original IIFE card structure. Do not introduce BEM.
- **ES modules** — `src/static/js/` uses `<script type="module">` (deferred; DOM is ready at run time). Each card file is its own module scope — no IIFE wrapper needed.
- **`state.js` singleton** — Cross-module mutable state lives in `export const state = { ... }` in `state.js`. Replaces shared IIFE-scope `let` variables. Import with `import { state } from './state.js'`.
- **Canvas-based timelines** — Annotation timelines (`anv-status-canvas`, `anv-note-canvas`, `va-status-canvas`, `va-note-canvas`) draw one `fillRect` per annotated frame. NEVER create a DOM node per frame (see `feedback_timeline_dom_antipattern.md`). Regression test: `tests/test_timeline_canvas_rendering.py`.
- **Template hierarchy** — `base.html` is the shell (5 CSS links, `<script type="module" src="/static/js/main.js">`). `index.html` extends `base.html` with `{% block content %}` and 17 `{% include "partials/<name>.html" %}` calls. Each partial is one UI card.
- **Responsive breakpoint** — `@media (max-width: 600px)` in `base.css`. Single breakpoint; no CSS grid framework.
- **No `window.*` globals** — Legacy refs (`window._vaLoadH5Info`, `window._vaReset`) removed. Use module imports for cross-module calls.

---

## Known Fragile Areas

| Area | Risk | Mitigation |
|------|------|-----------|
| DLC MultiIndex CSV 3-vs-4 header rows | 4-level format ("individuals") needs `header=[0,1,2,3]` | `rebuild_h5_from_csv` auto-detects via `"individuals" in row[1]` |
| `ruamel.yaml` multi-line keys | Paths with spaces break on round-trip | `_sanitize_dlc_config_yaml()` before every DLC API call |
| H5 append mode | `mode="a"` on HDF5 can corrupt MultiIndex under concurrent writes | Forbidden — full rewrite via `mode="w"` only |
| TAPNet TAPIR checkpoint | `.npy` format, not `.ckpt`. URL hardcoded in `dlc_tapnet_tracker.py` | Auto-download on first use |
| JAX GPU memory | JAX claims all GPU VRAM on first import | Subprocess exits immediately after inference; never import JAX in Flask/worker scope |
| pandas MultiIndex column names | Must be `["scorer","bodyparts","coords"]`; index names `["","",""]` | Verified in `tests/test_viewer_dataset_curation.py` |
| VideoCapture cache leak | OpenCV `VideoCapture` not released → fd leak | LRU eviction calls `vcap.release()` on pop |
| `dlc_dataset_curator.py` seq_index | Race condition if two Flask threads extract frame simultaneously | Acceptable; seq_index collision just appends one extra PNG |

---

## Module Quick-Ref

| File | One-line purpose |
|------|-----------------|
| `app.py` | Flask factory, blueprint wiring |
| `celery_app.py` | Celery instance, worker init hook |
| `tasks.py` | Worker entry (re-exports tasks) |
| `dlc_tapnet_tracker.py` | TAPNet label propagation, GPU subprocess |
| `dlc_dataset_curator.py` | Frame→PNG, CSV/H5 R/W (no Flask) |
| `dlc/ctx.py` | Shared runtime state |
| `dlc/utils.py` | Engine routing, security, dir walk |
| `dlc/viewer.py` | Kinematic overlay, H5+vcap cache |
| `dlc/curator.py` | Flask routes wrapping `dlc_dataset_curator` |
| `dlc/tasks.py` | DLC Celery tasks, config sanitization |
| `anipose/tasks.py` | Anipose pipeline + MediaPipe Celery tasks |

---

## API Surface (key endpoints)

```
POST /dlc/project                    Set active DLC project
POST /dlc/curator/extract-frame      Extract PNG from video frame
POST /dlc/curator/add-to-dataset     Extract + append to CollectedData
POST /dlc/curator/save-annotation    Write corrected coords to CSV/H5
GET  /dlc/viewer/h5-find             Locate H5 for a video stem
GET  /dlc/viewer/frame-annotated/:n  Render pose overlay JPEG
POST /dlc/project/analyze            Dispatch DLC inference task
POST /dlc/project/train-network      Dispatch DLC training task
POST /run                            Dispatch Anipose pipeline step
GET  /status/:task_id                Poll Celery task state
```

---

## Test Strategy

- Tests run inside Docker worker (`python:3.11` + pandas + cv2)
- `conftest.py` fixtures: `tmp_path` for all file I/O, skip markers for GPU/video-dependent tests
- Video-dependent tests require: `/user-data/Parra-Data/Cloud/Reaching-Task-Data/RatBox Videos/MAPS-DREADSS/*.avi`
- Never use real project paths in assertions — always fixture-generated paths
- H5 integrity verified by `pd.read_hdf(h5, key="df_with_missing")` round-trip check

---

## Session Bootstrapping for New LLM Session

1. Read `docs/LLM_CONTEXT.md` (this file) — ~1800 tokens
2. Read `docs/ARCHITECTURE_MAP.md` for module map — ~2000 tokens
3. Read `docs/DATA_PIPELINE.md` if touching H5/CSV/TAPNet code — ~1500 tokens
4. Read the specific module(s) being modified
5. Check `tests/conftest.py` for fixture conventions before writing tests
