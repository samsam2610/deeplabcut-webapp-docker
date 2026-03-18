# ARCHITECTURE MAP
> Machine-readable module reference. Token-optimized for LLM context loading.

---

## Directory Tree

```
src/
├── app.py                      # Flask factory: blueprint registration, Redis/Celery init
├── celery_app.py               # Celery instance; worker startup (stale PID cleanup)
├── tasks.py                    # Worker entry: re-exports dlc.tasks + anipose.tasks
├── dlc_tapnet_tracker.py       # TAPNet/TAPIR adapter (GPU-isolated subprocess, label propagation)
├── dlc_dataset_curator.py      # Pure-Python: frame→PNG, CollectedData CSV/H5 I/O
│
├── dlc/
│   ├── __init__.py
│   ├── ctx.py                  # Shared runtime context (avoids circular imports)
│   ├── utils.py                # Engine routing, dir walking, security checks
│   ├── project.py              # Blueprint: DLC project CRUD, config detection
│   ├── config_routes.py        # Blueprint: config upload, PyTorch config management
│   ├── videos.py               # Blueprint: video list/stream/frame + LRU vcap cache
│   ├── labeling.py             # Blueprint: frame label R/W (CSV), bodypart list
│   ├── training.py             # Blueprint: create training dataset, train network
│   ├── inference.py            # Blueprint: dispatch analyze task, labeled-content
│   ├── monitoring.py           # Blueprint: machine-label, GPU status, job list
│   ├── curator.py              # Blueprint: extract-frame, add-to-dataset, save-annotation
│   ├── viewer.py               # Blueprint: kinematic overlay rendering (cv2, H5 cache)
│   └── tasks.py                # Celery tasks: analyze, train, create-dataset, machine-label
│
├── anipose/
│   ├── __init__.py
│   ├── projects.py             # Blueprint: project CRUD, upload/download
│   ├── session.py              # Blueprint: session lifecycle, config upload
│   ├── pipeline.py             # Blueprint: pipeline dispatcher (calibrate→filter→triangulate)
│   ├── visualization.py        # Blueprint: behavior/pose3d data routes
│   ├── inspector.py            # Blueprint: behavior inspector web UI
│   └── tasks.py                # Celery tasks: Anipose pipeline + MediaPipe conversion
│
├── anipose_src/                # Anipose algorithm library (forked/vendored)
│   ├── utils.py                # Transform matrices, clustering
│   ├── boards.py               # Charuco calibration boards
│   ├── cameras.py              # Camera calibration + geometry
│   ├── calibration_funcs.py
│   ├── filter_2d_funcs.py      # Median/Viterbi 2D filter
│   ├── filter_3d_funcs.py
│   ├── load_config_funcs.py
│   ├── preprocessing_funcs.py  # MediaPipe → Anipose format
│   └── triangulate_funcs.py    # Multi-camera triangulation
│
├── routes/
│   ├── annotate.py             # Blueprint: generic video annotation R/W
│   └── custom_script.py        # Blueprint: arbitrary user script subprocess
│
├── config_templates/
│   ├── config.toml             # Anipose project template
│   └── config.yaml             # DLC project template
│
├── templates/
│   ├── index.html              # SPA shell (all cards)
│   └── inspector.html          # Behavior inspector page
│
└── static/
    ├── main.js                 # All frontend logic (~6500 lines, IIFEs per card)
    └── style.css

tests/
├── conftest.py                 # Fixtures: sandbox dirs, GPU env, DLC project paths
├── test_dlc_celery_tasks.py
├── test_dlc_config_routes.py
├── test_dlc_project_routes.py
├── test_dlc_training_routes.py
├── test_dlc_utils.py
├── test_dlc_video_routes.py
├── test_tapnet_adapter.py
├── test_video_viewer_backend.py
└── test_viewer_dataset_curation.py

scripts/
└── training_heartbeat.py       # Cron daemon: Redis→Telegram training notifications

docs/
├── ARCHITECTURE_MAP.md         # This file
├── DATA_PIPELINE.md
└── LLM_CONTEXT.md
```

---

## Module Responsibility Table

| Module | Responsibility | Key Dependencies |
|--------|---------------|-----------------|
| `app.py` | Flask factory; blueprint wiring; before_request ctx sync | `flask`, `redis`, `celery`, `dlc/*`, `anipose/*`, `routes/*` |
| `celery_app.py` | Celery instance; worker start hook; stale PID cleanup | `celery`, `redis` |
| `tasks.py` | Worker entry point; re-exports all task modules | `dlc.tasks`, `anipose.tasks` |
| `dlc_tapnet_tracker.py` | TAPNet label propagation; GPU-isolated subprocess; CSV merge | `numpy`, `pandas`, `jax` (subprocess), `tapir_model` (subprocess), `cv2` (subprocess) |
| `dlc_dataset_curator.py` | Frame PNG extraction; CollectedData CSV/H5 read-write | `cv2`, `csv`, `pandas` (lazy) |
| `dlc/ctx.py` | Shared runtime context; breaks circular imports | stdlib only |
| `dlc/utils.py` | Engine routing (`pytorch`/`tensorflow`); dir walk; security check | `pathlib` |
| `dlc/project.py` | DLC project CRUD; Redis session; config.yaml detection | `flask`, `dlc.ctx`, `dlc.utils` |
| `dlc/config_routes.py` | Config upload; PyTorch config R/W | `flask`, `dlc.ctx` |
| `dlc/videos.py` | Video list/stream; frame extraction; LRU vcap cache (20 sessions) | `flask`, `cv2`, `dlc.ctx` |
| `dlc/labeling.py` | Frame label CSV R/W; bodypart list; CSV→H5 conversion | `flask`, `pandas`, `dlc.ctx` |
| `dlc/training.py` | Training dataset creation; network training dispatch | `flask`, `dlc.ctx`, `celery` |
| `dlc/inference.py` | Analyze video/frames dispatch; labeled-content listing | `flask`, `dlc.ctx`, `celery` |
| `dlc/monitoring.py` | Machine label propagation; GPU status; job list | `flask`, `dlc.ctx`, `celery` |
| `dlc/curator.py` | extract-frame / add-to-dataset / save-annotation routes | `flask`, `dlc_dataset_curator`, `dlc.ctx` |
| `dlc/viewer.py` | Kinematic overlay; H5 cache (5 files); vcap cache (10 sessions) | `flask`, `cv2`, `pandas`, `dlc.ctx` |
| `dlc/tasks.py` | DLC Celery tasks; config sanitization; subprocess DLC calls | `deeplabcut`, `celery_app`, `cv2`, `pandas` |
| `anipose/projects.py` | Anipose project CRUD, file ops | `flask`, `anipose_src` |
| `anipose/session.py` | Session lifecycle; config upload | `flask` |
| `anipose/pipeline.py` | Pipeline step dispatcher | `flask`, `celery`, `anipose.tasks` |
| `anipose/tasks.py` | Anipose Celery tasks; MediaPipe conversion | `celery_app`, `anipose_src.*`, `cv2`, `pandas` |
| `routes/annotate.py` | Generic video annotation JSON R/W | `flask`, `csv` |
| `routes/custom_script.py` | User script subprocess execution | `flask`, `subprocess` |
| `scripts/training_heartbeat.py` | Redis→Telegram training progress notifications | `redis`, `subprocess` (docker exec openclaw) |

---

## Service Topology

```
Browser
  │  HTTP
  ▼
Flask :5000 ──────────── Redis :6379 ──── Celery Worker (PyTorch, GPU 0)
  │                          │                  ├─ dlc.tasks.*
  │ blueprints                │                  ├─ anipose.tasks.*
  │ /dlc/*                   │                  └─ TAPNet subprocess (GPU 0)
  │ /session, /run, /projects │
  │ /annotate, /custom-script │            Celery Worker-TF (TF 2.13, GPU 0)
  │                          │                  └─ dlc.tasks.* (tensorflow queue)
  └─ per-session state ──────┘
     webapp:session:{uid}
     webapp:dlc_project:{uid}
```

---

## Queue Routing

| Queue | Worker | Task Types |
|-------|--------|-----------|
| `celery` | `worker` (PyTorch) | Anipose pipeline, MediaPipe, generic |
| `pytorch` | `worker` (PyTorch) | DLC train/analyze/machine-label (PyTorch engine) |
| `tensorflow` | `worker-tf` (TF) | DLC train/analyze/machine-label (TF engine) |

Engine routing function: `dlc.utils._get_engine_queue(engine: str) → str`

---

## Caching Summary

| Cache | Module | Eviction | Purpose |
|-------|--------|----------|---------|
| vcap (video) | `dlc/videos.py` | LRU, max 20 | Per-session VideoCapture objects |
| vcap (viewer) | `dlc/viewer.py` | LRU, max 10 | Viewer-specific VideoCapture |
| H5 DataFrame | `dlc/viewer.py` | LRU, max 5 | Loaded pose DataFrames |

---

## Security Constraints

- `_dlc_project_security_check(path, DATA_DIR, USER_DATA_DIR)` — all file-serving routes call this
- Path traversal blocked at `_resolve_project_dir()` in `app.py` / `dlc/utils.py`
- No shell=True in subprocess calls
- File uploads use `werkzeug.utils.secure_filename`
