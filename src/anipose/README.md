# anipose/

Flask Blueprint package for Anipose pipeline functionality, plus Celery tasks.

## Route modules

| File | Blueprint | Routes |
|------|-----------|--------|
| `session.py` | `anipose_session` | `POST/GET/DELETE /session`, `GET/POST /session/config`, `POST /session/from-path`, `GET /fs/list-configs` |
| `pipeline.py` | `anipose_pipeline` | `POST /run` (task dispatcher), `GET /session/pipeline`, `POST /projects/<id>/detect-frame-dims` |
| `projects.py` | `anipose_projects` | `POST/GET /projects`, `PATCH/DELETE /projects/<id>/file`, `GET /projects/<id>/download`, `GET /projects/<id>/browse`, `POST /projects/<id>/upload` |
| `visualization.py` | `anipose_visualization` | `GET /get-sessions`, behavior data endpoints, pose/trial data routes |
| `inspector.py` | `anipose_inspector` | `GET /inspector`, `GET /metadata/<session>`, `GET /get-trials/<session>`, `GET /pose3d/...`, `GET /video/...`, `POST /update-behavior`, etc. |

## Task module

| File | Purpose |
|------|---------|
| `tasks.py` | Celery tasks: `process_calibrate`, `process_filter_2d`, `process_triangulate`, `process_filter_3d`, `init_anipose_session`, plus MediaPipe conversion tasks (`process_organize_for_anipose`, `process_convert_mediapipe_csv_to_h5`, `process_convert_3d_csv_to_mat`, `process_convert_mediapipe_to_dlc_csv`). Imported by root `tasks.py` for worker discovery. |

## Shared state

Blueprints read shared state from `current_app.config` keys set by `app.py`'s `before_request` hook:

```python
from flask import current_app

def _data_dir(): return current_app.config['APP_DATA_DIR']
def _redis():    return current_app.config['APP_REDIS']
def _celery():   return current_app.config['APP_CELERY']
```
