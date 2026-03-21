# ARCHITECTURE MAP
> Machine-readable module reference. Token-optimized for LLM context loading.

---

## Directory Tree

```
src/
├── app.py                      # Flask factory: blueprint registration, Redis/Celery init
├── celery_app.py               # Celery instance; worker startup (stale PID cleanup, GPU pool init, Reaper thread)
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
│   ├── task_control.py         # Blueprint: pause/resume/terminate running tasks; SSE log streaming
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
│   ├── base.html               # Shell: head (5 CSS links), header, footer, <script type="module">
│   ├── index.html              # Extends base.html; {% block content %} with 17 {% include %} calls
│   ├── inspector.html          # Behavior inspector page
│   ├── login.html              # Token auth login page
│   └── partials/               # 17 Jinja2 partial files (one per UI card/section)
│       ├── session_dlc_bar.html
│       ├── session_anipose_bar.html
│       ├── card_dlc_project.html
│       ├── card_frame_extractor.html
│       ├── card_frame_labeler.html
│       ├── card_training_dataset.html
│       ├── card_train_network.html
│       ├── card_analyze.html
│       ├── card_viewer.html
│       ├── card_annotator.html
│       ├── card_gpu_monitor.html
│       ├── card_dlc_config.html
│       ├── card_custom_script.html
│       ├── card_project_explorer.html
│       ├── card_session_actions.html
│       ├── card_config_editor.html
│       └── card_admin.html
│
└── static/
    ├── main.js                 # Legacy monolith (kept; new entry point is js/main.js)
    ├── style.css               # Legacy monolith (kept; new entry point is css/variables.css)
    ├── css/                    # Modular CSS (5 files)
    │   ├── variables.css       # CSS custom properties (:root)
    │   ├── base.css            # Reset, html/body, grain, header, footer, responsive
    │   ├── layout.css          # Session bar, cards, form fields
    │   ├── components.css      # Buttons, progress, log, explorer, config editors, DLC theme, inspector
    │   └── viewer.css          # Frame extractor + frame labeler player UI
    └── js/                     # ES modules (13 files)
        ├── main.js             # Entry point: imports all card modules
        ├── state.js            # Exported singleton state object
        ├── api.js              # Placeholder for shared HTTP helpers
        ├── dlc_project.js      # DLC project CRUD, folder nav, project explorer
        ├── anipose.js          # Anipose session management + pipeline actions
        ├── frame_extractor.js  # Frame extractor card
        ├── frame_labeler.js    # Frame labeler card + TAPNet integration
        ├── training.js         # DLC config editor + create-dataset + train-network
        ├── analyze.js          # Analyze video/frames card
        ├── viewer.js           # View analyzed videos, kinematic overlay, timeline
        ├── annotator.js        # Video annotator (ANV card), annotation timelines
        ├── gpu_monitor.js      # GPU & training monitor card
        └── custom_script.js    # Custom script runner + inspector iframe handler

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
| `celery_app.py` | Celery instance; worker start hook; stale PID cleanup; GPU pool init (`dlc_available_gpus={0}`); Reaper daemon | `celery`, `redis` |
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
| `dlc/tasks.py` | DLC Celery tasks; config sanitization; subprocess DLC calls; GPU pool SPOP/SADD; Redis log streaming (RPUSH to `dlc_task:{id}:log`) | `deeplabcut`, `celery_app`, `cv2`, `pandas` |
| `dlc/task_control.py` | Task lifecycle control: SIGSTOP/SIGCONT pause-resume, SIGTERM→SIGKILL terminate, SSE log-stream | `flask`, `dlc.ctx`, `signal`, `os` |
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
  │  HTTP / SSE
  ▼
Flask :5000 ──────────── Redis :6379 ──── Celery Worker (PyTorch, GPU 0)
  │                          │                  ├─ dlc.tasks.*  (SPOP/SADD dlc_available_gpus)
  │ blueprints                │                  ├─ anipose.tasks.*
  │ /dlc/*                   │                  ├─ TAPNet subprocess (GPU 0)
  │ /dlc/task/<id>/pause     │                  └─ Reaper thread (30 s interval)
  │ /dlc/task/<id>/resume    │
  │ /dlc/task/<id>/terminate │            Celery Worker-TF (TF 2.13, GPU 0)
  │ /dlc/task/<id>/log-stream│                  └─ dlc.tasks.* (tensorflow queue)
  │ /session, /run, /projects│
  │ /annotate, /custom-script│
  └─ per-session state ──────┘
     webapp:session:{uid}
     webapp:dlc_project:{uid}
```

## Task Control Redis Keys

| Key pattern | Type | Purpose |
|-------------|------|---------|
| `dlc_available_gpus` | Set | GPU pool — contains only `"0"` (RTX 5090); SPOP on task start, SADD in finally |
| `dlc_train_pid:<task_id>` | String | PID of training subprocess (for SIGSTOP/SIGCONT/SIGTERM/SIGKILL) |
| `dlc_analyze_pid:<task_id>` | String | PID of analyze subprocess |
| `dlc_train_pause:<task_id>` | String | Set to `"1"` when task is paused; cleared on resume/terminate |
| `dlc_analyze_pause:<task_id>` | String | Set to `"1"` when task is paused |
| `dlc_train_stop:<task_id>` | String | Set to `"1"` to trigger SIGTERM→SIGKILL in emit_loop |
| `dlc_analyze_stop:<task_id>` | String | Same as above for analyze tasks |
| `dlc_train_job:<task_id>` | Hash | Job metadata: `status`, `gpu_id`, `project`, `engine`, `started_at` |
| `dlc_analyze_job:<task_id>` | Hash | Job metadata for analyze tasks |
| `dlc_task:<task_id>:log` | List | Live log lines RPUSH'd by emit_loop; consumed by SSE endpoint |

## Task Status State Machine

```
dispatched → running ──pause──→ paused
                │                  │
                │               resume
                │                  │
                └──terminate──→  stopping → [stopped/failed/complete]
                      ↑              │
                   [Reaper]      [dead] ← (orphan detected by Reaper)
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

## Frontend Modules

### CSS Files (`src/static/css/`)

| File | Primary Responsibility |
|------|----------------------|
| `variables.css` | CSS custom properties (`:root`): `--bg`, `--surface`, `--accent`, `--border`, `--text-dim`, etc. |
| `base.css` | Reset, `html`/`body`, grain overlay, header, footer, `@media (max-width: 600px)` breakpoint |
| `layout.css` | Session bar, card grid, form field groups (original lines 77–379) |
| `components.css` | Buttons, progress bars, log output, file explorer, config editors, `.dlc-theme` accent override, inspector |
| `viewer.css` | Frame extractor player UI and frame labeler player UI |

### JS Modules (`src/static/js/`)

| File | Primary Responsibility | Backend APIs |
|------|----------------------|-------------|
| `state.js` | Exported singleton: `{ sessionPollTimer, dlcBrowsePath, dlcEngine, dlcTrainingActive, currentRoot, userDataDir, dataDir, currentProjectId, pollTimer }` | — |
| `api.js` | Placeholder for shared HTTP helpers | — |
| `dlc_project.js` | DLC project CRUD, folder nav, project explorer; exports `applyDlcProjectState`, `browseProject`, `showProgress` | `POST/GET /dlc/project`, `GET /projects/<id>/browse` |
| `anipose.js` | Anipose session lifecycle, pipeline action cards; imports from `dlc_project.js` | `POST /session/create`, `GET /session/status`, `POST /run`, `GET /status/<task_id>` |
| `frame_extractor.js` | Frame extraction card | `GET /dlc/videos/list`, `GET /dlc/videos/frame`, `GET /dlc/videos/stream` |
| `frame_labeler.js` | Frame labeler + TAPNet label propagation | `GET /dlc/labeling/frames`, `POST /dlc/labeling/save`, `GET /dlc/labeling/bodyparts` |
| `training.js` | DLC config editor, create-training-dataset card, train-network card | `POST /dlc/project/create-training-dataset`, `POST /dlc/project/train-network` |
| `analyze.js` | Analyze video/frames card, snapshot selector | `POST /dlc/project/analyze`, `GET /dlc/inference/labeled-content` |
| `viewer.js` | Analyzed video viewer (VA card), kinematic overlay canvas, canvas-based annotation timeline, dataset curation sub-panel | `GET /dlc/viewer/h5-find`, `GET /dlc/viewer/frame-annotated/<n>`, `POST /dlc/curator/add-to-dataset`, `POST /dlc/curator/save-annotation`, `GET /annotate/csv`, `POST /annotate/save-row` |
| `annotator.js` | Video annotator (ANV card), canvas-based annotation timelines, clip extractor | `GET /annotate/load`, `POST /annotate/save`, `GET /dlc/videos/stream`, `POST /annotate/crop-video` |
| `gpu_monitor.js` | GPU status + job list card, auto-poll every 5 s | `GET /dlc/monitoring/gpu-status`, `GET /dlc/monitoring/jobs` |
| `custom_script.js` | Custom script runner, file/folder browser, inspector iframe message handler | `POST /custom-script/run` |
| `main.js` | Entry point: imports all card modules; loaded via `<script type="module">` in `base.html` | — |

---

## Security Constraints

- `_dlc_project_security_check(path, DATA_DIR, USER_DATA_DIR)` — all file-serving routes call this
- Path traversal blocked at `_resolve_project_dir()` in `app.py` / `dlc/utils.py`
- No shell=True in subprocess calls
- File uploads use `werkzeug.utils.secure_filename`
