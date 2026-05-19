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

| `vlm_routes.py` | `dlc_vlm` | `GET /vlm/refiner`, `/vlm/index-status`, `/vlm/index-stems`, `POST /vlm/index/build`, `GET /vlm/similar`, `POST /vlm/refine`, `GET /vlm/frame-data`, `/vlm/reference-image`, `/vlm/stem-vlm-frames`, `/vlm/stem-likelihoods` |

## Support files

| File | Purpose |
|------|---------|
| `utils.py` | Pure utility functions: `_engine_info`, `_get_pipeline_folders`, `_get_engine_queue`, `_walk_dir`, `_dir_has_media`, `_dlc_project_security_check`, `_resolve_project_dir` |
| `vlm_indexer.py` | VLM + KNN logic (no Flask): `build_index`, `find_similar`, `_ensure_raw_pred_csv`, `read_raw_predictions`, `frame_min_likelihoods`, `refine_coords_with_vlm`, `save_vlm_result`, `load_vlm_result`, `list_vlm_frames` |
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

## Post-Process Predictions

Module: `src/dlc/postprocess.py` (Flask), `src/dlc/postprocess_dlc.py` (DLC wrapper),
`src/dlc/postprocess_refine.py` (refineDLC drivers), `src/dlc/_refinedlc/` (vendored).

UI: card `postprocess-card` (button between View Analyzed and Annotate Video).

| Route | Method | Purpose |
|---|---|---|
| `/dlc/postprocess/scan` | POST | List analyzable files in a path. Body `{path, mode: "file" \| "folder"}`. |
| `/dlc/postprocess/run` | POST | Dispatch a Celery task. Returns `{task_id}`. |
| `/dlc/postprocess/status/<id>` | GET | Celery state + progress meta. |
| `/dlc/postprocess/logs/<id>` | GET | Tail of run log. |
| `/dlc/postprocess/cancel/<id>` | POST | Revoke the running task. |
| `/dlc/postprocess/recent` | GET | List recent runs from sidecar `run.json` files under the active project. |

Outputs land at `<input-parent>/postproc/<YYYYMMDD-HHMMSS>_<tool-tag>/`.
Source files are never modified.

**Known TODOs:**
- `_active_project_root()` in `postprocess.py` returns `None` until wired to the per-request session-uid + redis lookup used elsewhere (see `tests/test_dlc_labeling_routes.py::_set_project`). Until wired, `/recent` returns `[]` in production.
- The vendored `smoothing.py` is a local stub (upstream refineDLC has no smoothing module). The actual Savitzky-Golay implementation lives in `step_smoothing` in `postprocess_refine.py`.
