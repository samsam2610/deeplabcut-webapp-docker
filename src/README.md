# src/

Flask + Celery application source.

## Entry points

| File | Purpose |
|------|---------|
| `app.py` | Flask application bootstrap. Defines globals (`DATA_DIR`, Redis, Celery client), registers all Blueprints, and handles the 6 core routes (`/`, `/upload`, `/status`, `/config`, `/fs/list`, `/admin/flush-task-cache`). Run with `gunicorn app:app`. |
| `tasks.py` | Celery worker entry point. Imports the shared `celery` instance and all task functions so the worker can discover them. Run with `celery -A tasks worker`. |
| `celery_app.py` | Single shared `celery` Celery instance used by both `anipose/tasks.py` and `dlc/tasks.py`. Also registers the `worker_init` signal that cleans up stale GPU processes on startup. |

## Packages

| Folder | Contents |
|--------|---------|
| `dlc/` | All DeepLabCut Flask routes (7 Blueprints) + Celery tasks + shared utils + context module. See `dlc/README.md`. |
| `anipose/` | Anipose pipeline Flask routes (5 Blueprints) + Celery tasks. See `anipose/README.md`. |
| `routes/` | Standalone Blueprints: video annotator (`annotate.py`) and custom script runner (`custom_script.py`). See `routes/README.md`. |
| `templates/` | Jinja2 HTML templates. |
| `static/` | CSS, JS, and other static assets. |

## File layout

```
src/
├── app.py              Flask bootstrap (~350 lines)
├── tasks.py            Celery entry point (~35 lines)
├── celery_app.py       Shared Celery instance (~55 lines)
├── dlc/
│   ├── ctx.py          Shared mutable state (DATA_DIR, Redis, Celery)
│   ├── utils.py        Pure helper functions
│   ├── tasks.py        DLC Celery tasks (~1720 lines)
│   ├── project.py      Blueprint: project CRUD
│   ├── config_routes.py Blueprint: session/project config
│   ├── videos.py       Blueprint: video extraction
│   ├── labeling.py     Blueprint: frame labeling
│   ├── training.py     Blueprint: model training
│   ├── inference.py    Blueprint: analysis/inference
│   └── monitoring.py   Blueprint: jobs + GPU status
├── anipose/
│   ├── tasks.py        Anipose + MediaPipe Celery tasks (~595 lines)
│   ├── session.py      Blueprint: session management
│   ├── pipeline.py     Blueprint: pipeline operations
│   ├── projects.py     Blueprint: project management
│   ├── visualization.py Blueprint: data visualization
│   └── inspector.py    Blueprint: Behavior Inspector
└── routes/
    ├── annotate.py     Blueprint: video annotation
    └── custom_script.py Blueprint: custom script runner
```

## GPU routing

- **GPU 0 (RTX 5090)** → DLC processes (`CUDA_VISIBLE_DEVICES=0`)
- **GPU 1 (Blackwell A6000)** → orchestrator / LLM

DLC training and analysis always run in subprocesses with `CUDA_VISIBLE_DEVICES=0` set explicitly. See `dlc/tasks.py`.
