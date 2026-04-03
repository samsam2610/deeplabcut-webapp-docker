# DLC Architecture Map
> Generated during Phase 2 of the DLC modular refactoring project.
> Source files: `src/app.py` (4214 lines) · `src/tasks.py` (2361 lines)
> Anipose code is intentionally excluded from this map.

---

## GPU Routing Constraint
| GPU | Device | Role |
|-----|--------|------|
| `CUDA_VISIBLE_DEVICES=0` | RTX 5090 | **All DLC processes** (train, analyze, machine-label) |
| `CUDA_VISIBLE_DEVICES=1` | RTX PRO 6000 Blackwell | Orchestrator / local LLM only |

All Celery tasks that spawn GPU subprocesses must inject `CUDA_VISIBLE_DEVICES=0` into the child process environment.

---

## Proposed Module Layout

```
src/
├── app.py                  ← keep; strip DLC routes → register blueprints
├── tasks.py                ← keep; strip DLC tasks → import from modules
├── dlc/
│   ├── __init__.py
│   ├── dlc_utils.py        ← shared helpers, Redis keys, security checks
│   ├── dlc_config.py       ← config CRUD routes + engine detection
│   ├── dlc_project.py      ← project state, file management, browsing
│   ├── dlc_videos.py       ← video listing, streaming, frame extraction
│   ├── dlc_training.py     ← training dataset creation + train_network
│   ├── dlc_inference.py    ← analyze, snapshots, labeled-video content
│   └── dlc_labeling.py     ← machine labeling, CSV↔H5 conversion
```

---

## Redis Key Catalogue

### Session / Project State
| Key Pattern | Type | TTL | Owner |
|-------------|------|-----|-------|
| `webapp:session:{uid}` | Hash | Session lifetime | Flask session |
| `webapp:dlc_project:{uid}` | Hash | Session lifetime | Flask session |

### Training Jobs
| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `dlc_train_pid:{task_id}` | String | 7200 s | Child process PID |
| `dlc_train_stop:{task_id}` | String | 120 s | Stop signal flag |
| `dlc_train_job:{task_id}` | Hash | 3600 s post-completion | Job metadata |
| `dlc_train_jobs` | Sorted set | — | Active job IDs (score = timestamp) |

### Analysis Jobs
| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `dlc_analyze_pid:{task_id}` | String | 7200 s | Child process PID |
| `dlc_analyze_stop:{task_id}` | String | 120 s | Stop signal flag |
| `dlc_analyze_job:{task_id}` | Hash | 3600 s post-completion | Job metadata |
| `dlc_analyze_jobs` | Sorted set | — | Active job IDs |

### Machine Labeling
| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `dlc_ml_pid:{task_id}` | String | — | Child process PID |
| `dlc_ml_stop:{task_id}` | String | 120 s | Stop signal flag |

### GPU Monitoring
| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `dlc_gpu_stats` | String | 60 s | Cached `nvidia-smi` CSV output |
| `dlc_gpu_stats_ts` | String | 60 s | Timestamp of last probe |

---

## Celery Task Names

| Task Name | Module | Description |
|-----------|--------|-------------|
| `tasks.dlc_create_training_dataset` | `dlc_training.py` | Run `dlc.create_training_dataset()` |
| `tasks.dlc_add_datasets_to_video_list` | `dlc_project.py` | Sync `video_sets` ↔ labeled-data |
| `tasks.dlc_convert_labels_to_h5` | `dlc_labeling.py` | CSV → HDF5 for all labeled-data folders |
| `tasks.dlc_train_network` | `dlc_training.py` | Spawn training subprocess + monitor |
| `tasks.dlc_probe_gpu_stats` | `dlc_utils.py` | `nvidia-smi` probe + cache in Redis |
| `tasks.dlc_analyze` | `dlc_inference.py` | Spawn analysis subprocess + monitor |
| `tasks.dlc_machine_label_frames` | `dlc_labeling.py` | Model inference on labeled-data frames |
| `tasks.dlc_machine_label_reapply` | `dlc_labeling.py` | Re-apply threshold to saved predictions |
| `tasks.run_processing` | `dlc_utils.py` | Legacy Anipose/DLC dispatcher |

---

## Module: `dlc_utils.py`

**Role:** Shared helpers, key builders, security, GPU utilities, monitoring.

### Functions from `app.py`
| Function | Line | Signature | Notes |
|----------|------|-----------|-------|
| `_user_id` | 62 | `() -> str` | Flask session UID |
| `_session_key` | 68 | `() -> str` | `webapp:session:{uid}` |
| `_dlc_key` | 71 | `() -> str` | `webapp:dlc_project:{uid}` |
| `_engine_info` | 97 | `(engine: str) -> tuple[str, str, str]` | Returns (models_folder, model_config_file, eval_results_folder) |
| `_get_pipeline_folders` | 104 | `(engine: str) -> list` | DLC pipeline folder name list |
| `_get_engine_queue` | 110 | `(engine: str) -> str` | Maps engine → Celery queue name |
| `_resolve_project_dir` | 133 | `(project_id: str, root: str = "") -> Path` | Path-traversal-safe resolver |
| `_clear_session_data` | 695 | `() -> None` | Revoke task + delete session dir + Redis key |
| `_parse_pipeline_section` | 896 | `(config_text: str) -> dict` | Extract [pipeline] TOML section |
| `_walk_dir` | 914 | `(path, project_path, depth=0, max_depth=6) -> list` | Recursive dir listing |
| `_dlc_project_security_check` | 937 | `(p: Path) -> bool` | Allow-list check vs DATA_DIR/USER_DATA_DIR |
| `_dir_has_media` | 1066 | `(path: Path) -> bool` | Check if dir has video/image files |
| `run_operation` | 734 | `() -> (dict, int)` | `POST /run` — Anipose/MediaPipe dispatcher |
| `fs_ls` | 1081 | `() -> (dict, int)` | `GET /fs/ls` — file browser listing |

### Functions from `tasks.py`
| Function | Line | Signature | Notes |
|----------|------|-----------|-------|
| `_kill_stale_gpu_processes` | 53 | `(sender, **kwargs) -> None` | `@worker_ready` signal handler |
| `_run_cmd` | 81 | `(cmd, cwd, task, stage, progress) -> str` | Shell command runner with progress |
| `_ensure_config` | 174 | `(config_path, session_path) -> None` | Copy config.toml to session dir |
| `_session_task_wrapper` | 181 | `(self, session_path, config_path, cmd, stage, operation) -> dict` | Shared session task body |
| `_cuda_cleanup_with_timeout` | 893 | `(timeout: int = 10) -> None` | GPU teardown in daemon thread |
| `dlc_probe_gpu_stats` | 1199 | `() -> str` | Celery: nvidia-smi → Redis cache |
| `run_processing` | 2326 | `(self, project_id, task_type="anipose") -> dict` | Legacy dispatcher task |

### Flask Routes in `dlc_utils.py`
| Route | Method | Handler |
|-------|--------|---------|
| `/run` | POST | `run_operation` |
| `/fs/ls` | GET | `fs_ls` |
| `/dlc/training/jobs` | GET | `dlc_training_jobs` |
| `/dlc/training/jobs/clear` | POST | `dlc_training_jobs_clear` |
| `/dlc/gpu/status` | GET | `dlc_gpu_status` |

---

## Module: `dlc_config.py`

**Role:** Config CRUD (session-level and project-level) + engine detection.

### Functions from `app.py`
| Function | Line | HTTP | Signature | Notes |
|----------|------|------|-----------|-------|
| `upload_dlc_config` | 415 | `POST /session/dlc-config` | `() -> (dict, 201)` | Upload config.yaml to session |
| `get_dlc_config` | 449 | `GET /session/dlc-config` | `() -> (dict, 200)` | Read session config.yaml |
| `save_dlc_config` | 472 | `PATCH /session/dlc-config` | `() -> (dict, 200)` | Write session config.yaml |
| `clear_dlc_config` | 497 | `DELETE /session/dlc-config` | `() -> (dict, 200)` | Remove session config reference |
| `load_dlc_config_from_path` | 510 | `POST /session/dlc-config/from-path` | `() -> (dict, 201)` | Attach server-side config |
| `get_dlc_project_config` | 1105 | `GET /dlc/project/config` | `() -> (dict, 200)` | Read active project config.yaml |
| `save_dlc_project_config` | 1118 | `PATCH /dlc/project/config` | `() -> (dict, 200)` | Write active project config.yaml |
| `get_dlc_project_engine` | 3247 | `GET /dlc/project/engine` | `() -> (dict, 200)` | Read engine field from config |

**Redis keys used:** `webapp:session:{uid}`, `webapp:dlc_project:{uid}`

---

## Module: `dlc_project.py`

**Role:** DLC project state management, file operations, browsing, video-sets sync.

### Functions from `app.py`
| Function | Line | HTTP | Signature | Notes |
|----------|------|------|-----------|-------|
| `get_dlc_project` | 944 | `GET /dlc/project` | `() -> (dict, 200)` | Read project state from Redis |
| `set_dlc_project` | 953 | `POST /dlc/project` | `() -> (dict, 200)` | Set active project + read config |
| `clear_dlc_project` | 1020 | `DELETE /dlc/project` | `() -> (dict, 200)` | Clear project session |
| `browse_dlc_project` | 1027 | `GET /dlc/project/browse` | `() -> (dict, 200)` | List pipeline folder contents |
| `dlc_project_upload` | 1137 | `POST /dlc/project/upload` | `() -> (dict, 201)` | Upload files to pipeline folder |
| `dlc_project_delete_file` | 1175 | `DELETE /dlc/project/file` | `() -> (dict, 200)` | Delete file in project |
| `dlc_project_rename_file` | 1206 | `PATCH /dlc/project/file` | `() -> (dict, 200)` | Rename file in project |
| `dlc_project_download` | 1243 | `GET /dlc/project/download` | `() -> Response` | Download project folder as ZIP |

### Functions from `tasks.py`
| Function | Line | Task Name | Signature | Notes |
|----------|------|-----------|-----------|-------|
| `dlc_add_datasets_to_video_list` | 735 | `tasks.dlc_add_datasets_to_video_list` | `(self, config_path) -> dict` | Sync video_sets ↔ labeled-data; create dummy video files |

**Redis keys used:** `webapp:dlc_project:{uid}`
**DLC APIs:** `dlc.auxiliaryfunctions.read_config()` (indirect via config reading)

---

## Module: `dlc_videos.py`

**Role:** Video listing, upload, streaming (byte-range), frame extraction, CSV annotation serving.

### Functions from `app.py`
| Function | Line | HTTP | Signature | Notes |
|----------|------|------|-----------|-------|
| `detect_frame_dims` | 652 | `POST /projects/<project_id>/detect-frame-dims` | `(project_id) -> (dict, 200)` | OpenCV frame dimensions |
| `dlc_list_videos` | 1283 | `GET /dlc/project/videos` | `() -> (dict, 200)` | List videos/ folder |
| `dlc_video_info` | 1307 | `GET /dlc/project/video-info/<filename>` | `(filename) -> (dict, 200)` | FPS, frame count, dims |
| `dlc_video_csv` | 1340 | `GET /dlc/project/video-csv/<filename>` | `(filename) -> (dict, 200)` | CSV annotation rows |
| `dlc_video_csv_ext` | 1391 | `GET /dlc/project/video-csv-ext` | `() -> (dict, 200)` | CSV for external video path |
| `dlc_video_stream` | 1436 | `GET /dlc/project/video-stream/<filename>` | `(filename) -> Response` | HTTP byte-range video stream |
| `dlc_video_frame` | 1515 | `GET /dlc/project/video-frame/<filename>/<frame_number>` | `(filename, frame_number) -> Response` | JPEG frame with LRU cache |
| `dlc_video_upload` | 1586 | `POST /dlc/project/video-upload` | `() -> (dict, 201)` | Upload to videos/ |
| `dlc_add_video` | 1615 | `POST /dlc/project/add-video` | `() -> (dict, 200)` | Register external video in video_sets |
| `dlc_video_info_ext` | 1680 | `GET /dlc/project/video-info-ext` | `() -> (dict, 200)` | FPS/dims for external path |
| `dlc_video_frame_ext` | 1712 | `GET /dlc/project/video-frame-ext/<frame_number>` | `(frame_number) -> Response` | JPEG frame for external path |

**Per-session LRU cache:** `_fe_vcap_cache` (dict, max 20 concurrent `VideoCapture` objects)
**Redis keys used:** `webapp:dlc_project:{uid}`

---

## Module: `dlc_training.py`

**Role:** Training dataset creation, PyTorch config management, train_network dispatch.

### Functions from `app.py`
| Function | Line | HTTP | Signature | Notes |
|----------|------|------|-----------|-------|
| `dlc_create_training_dataset` (route) | 3086 | `POST /dlc/project/create-training-dataset` | `() -> (dict, 202)` | Dispatch `tasks.dlc_create_training_dataset` |
| `dlc_add_datasets_to_video_list` (route) | 3116 | `POST /dlc/project/add-datasets-to-video-list` | `() -> (dict, 202)` | Dispatch `tasks.dlc_add_datasets_to_video_list` |
| `list_dlc_pytorch_configs` | 3137 | `GET /dlc/project/pytorch-configs` | `() -> (dict, 200)` | List pytorch_config.yaml files |
| `get_dlc_pytorch_config` | 3164 | `GET /dlc/project/pytorch-config` | `() -> (dict, 200)` | Read pytorch_config.yaml content |
| `save_dlc_pytorch_config` | 3203 | `PATCH /dlc/project/pytorch-config` | `() -> (dict, 200)` | Write pytorch_config.yaml |
| `dlc_train_network` (route) | 3275 | `POST /dlc/project/train-network` | `() -> (dict, 202)` | Dispatch `tasks.dlc_train_network` |
| `dlc_train_network_stop` | 3354 | `POST /dlc/project/train-network/stop` | `() -> (dict, 200)` | Set stop flag; revoke Celery task |

### Functions from `tasks.py`
| Function | Line | Task Name | Signature | Notes |
|----------|------|-----------|-----------|-------|
| `dlc_create_training_dataset` (task) | 662 | `tasks.dlc_create_training_dataset` | `(self, config_path, num_shuffles=1, freeze_split=True) -> dict` | Calls `dlc.mergeandsplit` + `dlc.create_training_dataset` |
| `_dlc_train_subprocess` | 914 | — | `(config_path, kwargs, log_path) -> None` | Child process: `dlc.train_network()` + `_cuda_cleanup_with_timeout()` |
| `dlc_train_network` (task) | 959 | `tasks.dlc_train_network` | `(self, config_path, engine="pytorch", params=None) -> dict` | Spawn child, stream logs, manage stop signal |

**DLC APIs called:**
- `dlc.mergeandsplit(config_path, trainindex=0, uniform=True)`
- `dlc.create_training_dataset(config_path, num_shuffles, Shuffles, trainIndices, testIndices, userfeedback=False)`
- `dlc.train_network(config_path, **kwargs)` — called in subprocess

**Redis keys:** `dlc_train_pid:*`, `dlc_train_stop:*`, `dlc_train_job:*`, `dlc_train_jobs`

---

## Module: `dlc_inference.py`

**Role:** Video/image analysis, snapshot management, labeled video listing.

### Functions from `app.py`
| Function | Line | HTTP | Signature | Notes |
|----------|------|------|-----------|-------|
| `dlc_project_snapshots` | 3391 | `GET /dlc/project/snapshots` | `() -> (dict, 200)` | List .pt/.index snapshot files |
| `dlc_project_analyze` | 3477 | `POST /dlc/project/analyze` | `() -> (dict, 202)` | Dispatch `tasks.dlc_analyze` |
| `dlc_project_analyze_stop` | 3531 | `POST /dlc/project/analyze/stop` | `() -> (dict, 200)` | Set stop flag; revoke Celery task |
| `dlc_labeled_content` | 3557 | `GET /dlc/project/labeled-content` | `() -> (dict, 200)` | List labeled videos + frame folders |

### Functions from `tasks.py`
| Function | Line | Task Name | Signature | Notes |
|----------|------|-----------|-----------|-------|
| `_dlc_analyze_subprocess` | 1233 | — | `(config_path, target_path, params, log_path) -> None` | Detect target type → call `analyze_videos` / `analyze_time_lapse_frames` / `create_labeled_video` |
| `dlc_analyze` (task) | 1413 | `tasks.dlc_analyze` | `(self, config_path, target_path, params=None) -> dict` | Spawn child, stream logs, manage stop signal |

**DLC APIs called (in subprocess):**
- `dlc.analyze_videos(config_path, [video_paths], **params)`
- `dlc.analyze_time_lapse_frames(config_path, folder, **params)`
- `dlc.create_labeled_video(config_path, paths, **params)`

**Redis keys:** `dlc_analyze_pid:*`, `dlc_analyze_stop:*`, `dlc_analyze_job:*`, `dlc_analyze_jobs`

---

## Module: `dlc_labeling.py`

**Role:** Machine labeling, CSV↔H5 conversion, threshold re-application.

### Functions from `app.py`
| Function | Line | HTTP | Signature | Notes |
|----------|------|------|-----------|-------|
| `dlc_project_machine_label_frames` | 3604 | `POST /dlc/project/machine-label-frames` | `() -> (dict, 202)` | Dispatch `tasks.dlc_machine_label_frames` |
| `dlc_project_machine_label_frames_stop` | 3666 | `POST /dlc/project/machine-label-frames/stop` | `() -> (dict, 200)` | Set stop flag |
| `dlc_machine_label_raw_exists` | 3683 | `GET /dlc/project/machine-label-raw` | `() -> (dict, 200)` | Check for `_machine_predictions_raw.h5` |
| `dlc_machine_label_reapply` (route) | 3706 | `POST /dlc/project/machine-label-reapply` | `() -> (dict, 202)` | Dispatch `tasks.dlc_machine_label_reapply` |

### Functions from `tasks.py`
| Function | Line | Task Name | Signature | Notes |
|----------|------|-----------|-----------|-------|
| `dlc_convert_labels_to_h5` (task) | 819 | `tasks.dlc_convert_labels_to_h5` | `(self, config_path, scorer=None) -> dict` | CSV → HDF5 for all labeled-data folders; uses pandas |
| `_dlc_machine_label_subprocess` | 1604 | — | `(config_path, labeled_data_path, params, log_path) -> None` | Model inference on frame dir; merge with human labels; write CollectedData CSVs |
| `dlc_machine_label_frames` (task) | 1976 | `tasks.dlc_machine_label_frames` | `(self, config_path, labeled_data_path, params=None) -> dict` | Spawn child, stream logs, manage stop signal |
| `dlc_machine_label_reapply` (task) | 2133 | `tasks.dlc_machine_label_reapply` | `(self, stem_dir, video_stem, scorer, bodyparts, threshold) -> dict` | Re-apply threshold to `_machine_predictions_raw.h5`; no model re-run |

**DLC APIs called (in subprocess):**
- `dlc.analyze_time_lapse_frames(config_path, frame_dir, frametype, **params)`
- `dlc.auxiliaryfunctions.read_config(config_path)`

**Redis keys:** `dlc_ml_pid:*`, `dlc_ml_stop:*`

---

## Cross-Module Dependency Graph

```
dlc_utils.py  ←────────────────────  all other modules
     ↑
dlc_config.py  ←──── dlc_training.py, dlc_inference.py, dlc_labeling.py
                           ↓
dlc_project.py ←──── dlc_training.py, dlc_labeling.py
dlc_videos.py  ←──── dlc_inference.py (labeled-content)
```

**Shared global state that must be accessible to all modules:**
- `app` (Flask instance)
- `celery` (Celery instance)
- `_redis_client` (Redis client)
- `DATA_DIR`, `USER_DATA_DIR` (Path constants)
- `_fe_vcap_cache`, `_fe_vcap_cache_lock` (VideoCapture LRU cache)
- `_TF_ENGINE_ALIASES`, `_ENGINE_PYTORCH`, `_ENGINE_TF` (engine constants)

---

## Path Integrity Notes (Constraint #6)

1. **`config_path`** always points to an absolute `config.yaml` inside the DLC project. After extraction, all new modules must accept it as a parameter — never reconstruct it from CWD.
2. **`project_path`** is read from `config.yaml["project_path"]` and may contain stale absolute paths (mitigated in `set_dlc_project` which patches the value). Any extracted function that reads `project_path` must include the same stale-path-fix logic.
3. **`labeled-data/` and `videos/`** are always resolved as `Path(config_path).parent / "labeled-data"` etc. — not from `project_path` alone — to handle projects mounted at non-standard paths.
4. **Dummy video files** (`dlc_add_datasets_to_video_list`) are created under `project_path / "videos"`. This path must resolve to the actual project directory, not the Docker container's `/app/data`.

---

## Zero Name Collision Register

To prevent the collision issue from the previous failed attempt, renamed identifiers for the new modules:

| Original Name | New Unique Name | Location |
|---------------|-----------------|----------|
| `_run_cmd` | `dlc_run_cmd` | `dlc_utils.py` |
| `_ensure_config` | `dlc_ensure_config` | `dlc_utils.py` |
| `_walk_dir` | `dlc_walk_dir` | `dlc_utils.py` |
| `process_data` (if it exists) | `dlc_process_kinematic_data` | N/A |
| `dlc_create_training_dataset` (Flask route) | Keep original name in Blueprint | `dlc_training.py` |
| `dlc_add_datasets_to_video_list` (Flask route) | Keep original name in Blueprint | `dlc_project.py` |
| `dlc_machine_label_reapply` (Flask route) | Keep original name in Blueprint | `dlc_labeling.py` |

> **Note:** The Flask route function names and Celery task names that share the same Python identifier (e.g., `dlc_create_training_dataset`) are disambiguated by living in separate modules. Celery tasks are registered with their explicit `name=` string; Flask routes are registered via Blueprint. No collision at import time.

---

## Sandbox Test Project
```
Original (READ-ONLY): /home/sam/data-disk/Parra-Data/Disk/DLC-Projects/DREADD-Ali-2026-01-07
Temp copy pattern:    /tmp/dlc_test_{uuid}/DREADD-Ali-2026-01-07
```
All tests must duplicate, test, then `shutil.rmtree` the temp copy in a pytest fixture.
