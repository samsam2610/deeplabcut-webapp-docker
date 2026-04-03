# dlc/

Flask Blueprint package for all DeepLabCut functionality.

## Modules

| File | Blueprint | Routes |
|------|-----------|--------|
| `project.py` | `dlc_project` | `GET/POST/DELETE /dlc/project`, `/dlc/project/browse`, `/dlc/project/config`, `/dlc/project/upload`, `/dlc/project/file`, `/dlc/project/download` |
| `config_routes.py` | `dlc_config` | `POST/GET/PATCH/DELETE /session/dlc-config`, `/session/dlc-config/from-path`, `/dlc/project/engine`, `/dlc/project/pytorch-config(s)` |
| `videos.py` | `dlc_videos` | `/dlc/project/videos`, `/dlc/project/video-info`, `/dlc/project/video-stream`, `/dlc/project/video-frame`, `/dlc/project/video-upload`, `/dlc/project/add-video`, `/dlc/project/save-frame` |
| `labeling.py` | `dlc_labeling` | `/dlc/project/bodyparts`, `/dlc/project/labeled-frames`, `/dlc/project/frame-image`, `/dlc/project/labels`, `/dlc/project/labels/convert-to-h5` |
| `training.py` | `dlc_training` | `/dlc/project/create-training-dataset`, `/dlc/project/add-datasets-to-video-list`, `/dlc/project/snapshots`, `/dlc/project/train-network`, `/dlc/project/train-network/stop` |
| `inference.py` | `dlc_inference` | `/dlc/project/analyze`, `/dlc/project/analyze/stop`, `/dlc/project/labeled-content` |
| `monitoring.py` | `dlc_monitoring` | `/dlc/project/machine-label-frames`, `/dlc/project/machine-label-raw`, `/dlc/project/machine-label-reapply`, `/dlc/training/jobs`, `/dlc/gpu/status` |

## Support files

| File | Purpose |
|------|---------|
| `utils.py` | Pure utility functions: `_engine_info`, `_get_pipeline_folders`, `_get_engine_queue`, `_walk_dir`, `_dir_has_media`, `_dlc_project_security_check`, `_resolve_project_dir` |
| `ctx.py` | Shared mutable context (DATA_DIR, Redis, Celery). Populated by `app.py`'s `before_request` hook; read by Blueprint route handlers via `_ctx.*()` accessors. Avoids circular imports. |
| `tasks.py` | Celery task implementations: `dlc_create_training_dataset`, `dlc_train_network`, `dlc_analyze`, `dlc_machine_label_frames`, `dlc_machine_label_reapply`, etc. Imported by `tasks.py` for worker discovery. |

## Shared state pattern

Route handlers access `DATA_DIR`, `_redis_client`, and `celery` via `ctx.py`:

```python
import dlc.ctx as _ctx

def my_route():
    redis = _ctx.redis_client()
    data_dir = _ctx.data_dir()
```

`app.py` keeps module-level globals and syncs them to `ctx` on every request.
