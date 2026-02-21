# Anipose Processing Pipeline

A Dockerized web application for 3-D motion capture using **Anipose** with architectural hooks for **DeepLabCut**.

## Architecture

```
┌──────────────┐      ┌───────────┐      ┌──────────────────────────┐
│  Browser UI  │◄────►│  Flask    │─────►│  Celery Worker           │
│  (HTML/JS)   │      │  :5000    │      │  pytorch 2.9.1           │
└──────────────┘      └─────┬─────┘      │  CUDA 13.0 + cuDNN 9    │
                            │            │  DLC + Anipose (source)  │
                       ┌────▼─────┐      └────────┬───────────────--┘
                       │  Redis   │               │
                       │  Broker  │          ┌────▼─────┐
                       └──────────┘          │  Shared  │
                                             │  Volume  │
                                             │ /app/data│
                                             └──────────┘
```

The worker image is adapted from your existing Dockerfile:
- **Base**: `pytorch/pytorch:2.9.1-cuda13.0-cudnn9-runtime`
- **User**: Non-root `dlcuser` (UID/GID 1000)
- **DLC**: Cloned from source, editable install
- **Anipose**: Cloned from source, editable install
- **OpenCV**: Headless build (`4.7.0.68`) — avoids GUI deps
- **NumPy**: Pinned to `1.26.4`

## Prerequisites

- Docker & Docker Compose v2+
- NVIDIA GPU with drivers installed
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)

## Quick Start

```bash
# 1. Create the shared data directory
mkdir -p data

# 2. Build and launch
docker compose up --build -d

# 3. Open the UI
open http://localhost:5000
```

### Optional environment variables

```bash
# Custom host path for the shared volume (default: ./data)
export HOST_DATA_DIR=/path/to/your/data

# Match the UID/GID to your host user (default: 1000)
export WORKER_UID=$(id -u)
export WORKER_GID=$(id -g)
```

## Project Structure

```
anipose-app/
├── docker-compose.yml        # Service orchestration (3 containers)
├── Dockerfile.flask          # Lightweight Flask image (UID 1000)
├── Dockerfile.worker         # GPU worker — adapted from your Dockerfile
├── requirements-flask.txt    # Flask/Gunicorn/Celery client deps
├── app.py                    # Flask routes (/upload, /status)
├── tasks.py                  # Celery task definitions
├── templates/
│   └── index.html            # Upload & progress UI
├── static/
│   ├── style.css
│   └── main.js
└── data/                     # Shared volume (bind mount)
    └── <project_id>/
        ├── config.toml
        └── videos-raw/
            ├── cam1.mp4
            └── cam2.mp4
```

## Usage

1. **Select a pipeline** — Anipose (default) or DeepLabCut (placeholder).
2. **Upload** your `config.toml` and one or more camera video files.
3. **Monitor** the progress bar and live worker logs in the UI.
4. Results are written back into `/app/data/<project_id>/` on the shared volume.

## API Endpoints

| Method | Path               | Description                        |
|--------|--------------------|------------------------------------|
| GET    | `/`                | Serve the web UI                   |
| POST   | `/upload`          | Upload config + videos, start task |
| GET    | `/status/<task_id>`| Poll task progress                 |
| GET    | `/projects`        | List all project IDs               |

## Adding DeepLabCut

The `tasks.py` file contains a `_run_deeplabcut()` placeholder. To activate:

1. Uncomment the DLC imports and API calls in `_run_deeplabcut()`.
2. Ensure your DLC model weights are accessible on the shared volume.
3. The frontend dropdown already includes the DLC option.

## Permissions

Both containers run as UID 1000 so the shared bind-mount is readable
and writable by both Flask (upload) and the Worker (processing). If your
host user has a different UID, set `WORKER_UID` / `WORKER_GID` before
building.
