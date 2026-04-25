# MCP Server for DLC/Anipose Webapp — Design Spec

**Date:** 2026-04-24  
**Branch:** feat/posture-match-refiner  
**Status:** Approved

---

## Overview

Add an MCP (Model Context Protocol) HTTP server to the Flask webapp so the Hermes agent can interact with DeepLabCut and Anipose projects via Telegram. Users tell Hermes their app token once; Hermes stores it and passes it on every tool call. The webapp exposes 9 tools covering project browsing, analysis, frame extraction, and an active-learning jitter-prelabel pipeline.

No existing routes, tests, or Celery tasks are modified. All new code is additive.

---

## Architecture

```
Telegram user
     │  (chat)
     ▼
Hermes agent (hermes-agent container, llm-net)
     │  MCP HTTP  http://dlc-webapp:5000/mcp
     ▼
Flask webapp (deeplabcut-webapp-docker-flask-1)
  ├── /mcp  ← new MCP blueprint (src/routes/mcp_server.py)
  │     └── validates session_token, dispatches to existing logic
  ├── Redis  ← Celery task status
  └── Celery workers  ← GPU tasks
```

Both the Flask container and Hermes container join `llm-net`. Flask is reachable on that network as `dlc-webapp` (alias). Hermes is already on `llm-net`.

---

## New Files

| File | Purpose |
|------|---------|
| `src/routes/mcp_server.py` | Flask blueprint; MCP HTTP endpoint at `/mcp`; 9 tools |
| `src/dlc/jitter_prelabel.py` | Pure logic for jitter detection + frame upsert (no Flask, no Celery) |

### Modified Files

| File | Change |
|------|--------|
| `src/app.py` | Register `mcp_server` blueprint |
| `src/dlc/tasks.py` | Add `dlc_jitter_prelabel` Celery task |
| `docker-compose.yml` | Add `llm-net` + alias + `WEBAPP_PUBLIC_URL` to flask service |
| `Dockerfile.flask` | Add `mcp` Python package |
| `/home/sam/docker-images/hermes-agent/hermes-data/config.yaml` | Add `mcp_servers.dlc_webapp` entry |

---

## MCP Server Blueprint (`src/routes/mcp_server.py`)

Uses the `mcp` Python package's `FastMCP` class. Mounted in Flask via the WSGI adapter at `/mcp`. Supports Streamable HTTP transport (POST-based request/response — no persistent SSE connection required).

### Auth

Every tool takes `session_token: str` as its first argument. Validated with:

```python
if not secrets.compare_digest(session_token, current_app.config["APP_TOKEN"]):
    raise ValueError("Invalid session token")
```

`APP_TOKEN` is already set in `docker-compose.yml` (`APP_TOKEN=deeplabcut`). No new env vars.

### Tool Catalogue

| Tool name | Args (beyond session_token) | Returns |
|-----------|----------------------------|---------|
| `list_dlc_projects` | — | List of `{id, name, path}` |
| `list_anipose_projects` | — | List of `{id, name, path}` |
| `browse_project` | `project_id`, `subpath?` | List of files/dirs at path |
| `run_dlc_analysis` | `project_id`, `video_path` | `{task_id}` |
| `run_anipose_pipeline` | `project_id`, `operation` | `{task_id}` |
| `extract_frames` | `project_id`, `video_path`, `count?` | `{task_id}` |
| `jitter_prelabel` | `project_id`, `video_path`, `px_threshold?=10`, `min_jittery_parts?=3`, `max_frames?=200` | `{task_id}` |
| `get_task_status` | `task_id` | `{state, result?, progress?}` |
| `webapp_link` | `project_id?`, `stem?` | Clickable URL string |

Tool implementations call existing blueprint helper functions directly (not via HTTP) — same process, shared app context.

---

## Jitter Prelabel Pipeline

### Logic module: `src/dlc/jitter_prelabel.py`

Pure functions, no Flask dependencies. Importable by both the MCP blueprint (for input validation) and the Celery task (for execution).

```
detect_jitter_frames(raw_h5, filtered_h5, px_threshold, min_jittery_parts, max_frames)
    → list of (frame_index, {bodypart: (raw_xy, filtered_xy)})

upsert_frames(stem_dir, video_path, jitter_frames, scorer, filtered_predictions)
    → {added: int, updated: int, stem: str}
```

### Celery task: `dlc_jitter_prelabel` (pytorch queue)

```
1. Load _machine_predictions_raw.h5  (raw predictions)
2. deeplabcut.filterpredictions(config_path, [video_path])  → *_filtered.h5
3. detect_jitter_frames(raw_h5, filtered_h5, px_threshold, min_jittery_parts, max_frames)
4. upsert_frames(stem_dir, video_path, jitter_frames, scorer, filtered_predictions)
5. Return {flagged_frames, added, updated, stem, webapp_link}
```

### Frame naming convention

Matches the existing DLC labeled-data convention confirmed from real project data:

- Filename: `img{NNNN:04d}-{MMMMM:05d}.png`
  - `NNNN` — sequential order within the folder (0-padded to 4 digits)
  - `MMMMM` — actual video frame number (0-padded to 5 digits minimum; naturally longer if frame > 99999)
- Images are PNG, extracted via OpenCV

### Frame upsert logic

```
existing_frames = {parse_mmmmm(f) for f in stem_dir.glob("img*-*.png")}
next_nnnn = len(list(stem_dir.glob("img*-*.png")))

for frame_num, coords in jitter_frames:
    if frame_num in existing_frames:
        # update row in CollectedData_<scorer>.csv only
    else:
        # extract image from video at frame_num
        # save as img{next_nnnn:04d}-{frame_num:05d}.png
        # add row to CollectedData_<scorer>.csv
        next_nnnn += 1
```

Only bodyparts with likelihood above `min_lh=0.6` (default) are written to the CSV. Empty string for below-threshold bodyparts — consistent with existing CSV format.

### CSV format

Multi-level header (3 rows: scorer / bodyparts / coords), then data rows:

```
labeled-data, <stem_name>, <filename>, x1, y1, x2, y2, ...
```

If `CollectedData_<scorer>.csv` does not exist yet, it is created with the full header. If it exists, rows are appended or updated in place.

---

## Deep Links

`webapp_link` reads `WEBAPP_PUBLIC_URL` from Flask config (set via env var, e.g. `http://192.168.1.13:5000`).

- Project view: `{WEBAPP_PUBLIC_URL}/?token={APP_TOKEN}`
- Specific stem: `{WEBAPP_PUBLIC_URL}/vlm/refiner?token={APP_TOKEN}&stem={stem}`

After `jitter_prelabel` completes, Hermes automatically receives the `webapp_link` in the task result and includes it in its Telegram reply.

---

## Infrastructure Changes

### `docker-compose.yml`

```yaml
# flask service
flask:
  environment:
    - WEBAPP_PUBLIC_URL=http://192.168.1.13:5000
  networks:
    default:
    llm-net:
      aliases:
        - dlc-webapp

# top-level networks declaration
networks:
  llm-net:
    external: true
```

### `Dockerfile.flask`

Add to pip install step:

```dockerfile
RUN pip install mcp
```

### Hermes `config.yaml`

```yaml
mcp_servers:
  dlc_webapp:
    url: "http://dlc-webapp:5000/mcp"
    timeout: 300
    connect_timeout: 30
```

---

## Testing

### Unit tests (`tests/test_mcp_server.py`)

| Test | Coverage |
|------|---------|
| `test_auth_rejected_bad_token` | Invalid token returns MCP error |
| `test_list_dlc_projects` | Returns expected project list |
| `test_list_anipose_projects` | Returns expected project list |
| `test_browse_project` | Returns file listing |
| `test_webapp_link_format` | URL includes token and correct host |
| `test_get_task_status_pending` | Polling a pending task |
| `test_get_task_status_success` | Polling a completed task |

### Unit tests (`tests/test_jitter_prelabel.py`)

| Test | Coverage |
|------|---------|
| `test_detect_jitter_frames_basic` | Frames with large displacement flagged |
| `test_detect_jitter_frames_threshold` | px_threshold param respected |
| `test_detect_jitter_frames_min_parts` | min_jittery_parts param respected |
| `test_detect_jitter_frames_max_frames` | max_frames cap respected |
| `test_upsert_new_frame` | New frame added with correct filename |
| `test_upsert_existing_frame` | Existing MMMMM updates CSV, no new image |
| `test_upsert_csv_created_when_missing` | CSV created with correct 3-row header |
| `test_upsert_low_likelihood_skipped` | Below-threshold bodyparts left empty |
| `test_frame_naming_large_frame_number` | Frame > 99999 uses 6+ digit MMMMM |

### Existing tests

All 66 existing tests must continue to pass after changes. The MCP blueprint and jitter prelabel logic are fully additive — no existing routes, task signatures, or data files are touched.

---

## Out of Scope

- Streaming progress updates over SSE (task polling is sufficient)
- Multi-user separate APP_TOKENs (all users share the single `APP_TOKEN`)
- Hermes skill document (can be added later as a markdown file in hermes-data/skills/)
- VLM-based prelabeling (this spec covers filter/interpolation only)
