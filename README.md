# DeepLabCut WebApp

Browser-based pipeline orchestrator for [DeepLabCut](https://github.com/DeepLabCut/DeepLabCut) pose estimation and [Anipose](https://github.com/lambdaloop/anipose) 3D triangulation, with integrated TAPNet/TAPIR label propagation and an active-learning frame curation UI.

---

## Hardware Requirements

| Role | Device | Notes |
|------|--------|-------|
| DLC inference / TAPNet | RTX 5090 (GPU 0) | `CUDA_VISIBLE_DEVICES=0` enforced in all workers |
| LLM orchestrator / Claude | Blackwell A6000 (GPU 1) | Never touched by this stack |
| CPU fallback | Any | TensorFlow worker uses CUDA 11.8 |

> **Critical:** The A6000 is reserved for the orchestrator. No DLC or Anipose task may
> set `CUDA_VISIBLE_DEVICES=1`. See `src/dlc_tapnet_tracker.py` and `src/dlc/tasks.py`
> for enforcement points.

---

## Quick Start

### Prerequisites
- Docker + NVIDIA Container Toolkit
- `docker compose` v2+
- Host paths (edit `docker-compose.yml` if needed):
  - `/home/sam/data-disk/Parra-Data` → `/user-data/Parra-Data/Disk`
  - `/home/sam/synology/Parra-Lab-Data` → `/user-data/Parra-Data/Cloud`

### Build & Run

```bash
# First run — build all images
docker compose up --build -d

# Subsequent runs
docker compose up -d

# Tail logs
docker compose logs -f flask worker
```

UI available at **http://localhost:5000**

### Rebuild a single service

```bash
docker compose build worker && docker compose up -d --no-deps worker
```

---

## Project Structure

```
.
├── docker-compose.yml          # Orchestration: flask + worker + worker-tf + redis
├── Dockerfile.flask            # python:3.10-slim, gunicorn, no GPU
├── Dockerfile.worker           # pytorch/pytorch:2.9.1-cuda13.0, DLC + TAPNet
├── Dockerfile.worker-tf        # tensorflow:2.13.0-gpu (CUDA 11.8), legacy TF DLC
├── requirements-flask.txt
├── requirements-worker.txt
├── pytest.ini
├── scripts/
│   └── training_heartbeat.py   # Telegram training progress daemon
├── docs/
│   ├── ARCHITECTURE_MAP.md
│   ├── DATA_PIPELINE.md
│   └── LLM_CONTEXT.md
├── src/
│   ├── app.py                  # Flask entry point, blueprint registration
│   ├── celery_app.py           # Celery instance + worker startup hooks
│   ├── tasks.py                # Worker entry point (imports dlc + anipose tasks)
│   ├── dlc_tapnet_tracker.py   # TAPNet/TAPIR label propagation adapter
│   ├── dlc_dataset_curator.py  # Frame extraction + CollectedData CSV/H5 I/O
│   ├── dlc/                    # DLC Flask blueprints + Celery tasks
│   ├── anipose/                # Anipose Flask blueprints + Celery tasks
│   ├── anipose_src/            # Anipose algorithm implementations
│   ├── routes/                 # Legacy route modules (annotate, custom_script)
│   ├── config_templates/       # Example config.toml / config.yaml
│   ├── templates/              # Jinja2 HTML templates
│   └── static/                 # JS / CSS frontend
└── tests/
    ├── conftest.py
    └── test_*.py               # pytest test modules
```

---

## Running Tests

Tests run inside the PyTorch worker container (host Python lacks DLC/pandas):

```bash
# Copy tests into the mounted src volume, run inside worker
docker compose exec worker bash -c "cd /app && pip install pytest -q && pytest tests/ -v"
```

GPU-dependent tests (TAPNet, video fixture) auto-skip if hardware/data is absent.

---

## Services

| Service | Image | Port | Queue |
|---------|-------|------|-------|
| `flask` | `python:3.10-slim` (custom) | 5000 | — |
| `redis` | `redis:7-alpine` | 6379 | — |
| `worker` | `pytorch:2.9.1-cuda13.0` (custom) | — | `celery,pytorch` |
| `worker-tf` | `tensorflow:2.13.0-gpu` (custom) | — | `tensorflow` |

---

## Key Environment Variables

```
DATA_DIR=/app/data                    # Project storage root
USER_DATA_DIR=/user-data              # External data mounts
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
FLASK_SECRET_KEY=<set in .env>
WORKER_UID / WORKER_GID=1000          # dlcuser inside containers
```

---

## Contributing

- All DLC/TAPNet code must enforce `CUDA_VISIBLE_DEVICES=0`
- Never modify files under `/user-data/Parra-Data/` directly — use sandbox copies
- H5 writes use atomic rename via `.tmp` — do not bypass this
- See `docs/LLM_CONTEXT.md` for design constraints before editing
