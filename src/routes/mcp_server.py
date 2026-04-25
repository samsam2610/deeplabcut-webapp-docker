# src/routes/mcp_server.py
"""
MCP Streamable HTTP server — Flask blueprint.

Implements the MCP 2024-11-05 protocol over plain HTTP POST (no SSE).
Exposes 9 tools for DLC/Anipose project management via the Hermes agent.

Auth: every tools/call passes session_token validated against APP_TOKEN.
"""
from __future__ import annotations
import json
import os
import secrets
import uuid
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request

bp = Blueprint("mcp_server", __name__)

_PROTOCOL_VERSION = "2024-11-05"
_SERVER_INFO = {"name": "dlc-webapp", "version": "1.0.0"}

# ── Helpers ───────────────────────────────────────────────────────


def _app_token() -> str:
    return current_app.config.get("APP_TOKEN") or os.environ.get("APP_TOKEN", "")


def _public_url() -> str:
    return current_app.config.get("WEBAPP_PUBLIC_URL") or os.environ.get("WEBAPP_PUBLIC_URL", "")


def _data_dir() -> Path:
    return Path(current_app.config.get("APP_DATA_DIR", os.environ.get("DATA_DIR", "/app/data")))


def _user_data_dir() -> Path:
    return Path(current_app.config.get("APP_USER_DATA_DIR", os.environ.get("USER_DATA_DIR", "/user-data")))


def _celery():
    return current_app.config["APP_CELERY"]


def _check_token(session_token: str) -> None:
    tok = _app_token()
    if not tok or not secrets.compare_digest(str(session_token), str(tok)):
        raise PermissionError("Invalid session token")


def _ok(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _content(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


# ── Tool definitions ──────────────────────────────────────────────

_TOOLS = [
    {
        "name": "list_dlc_projects",
        "description": "List all DeepLabCut projects available on the server.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string", "description": "App auth token"}
            },
            "required": ["session_token"],
        },
    },
    {
        "name": "list_anipose_projects",
        "description": "List all Anipose projects available on the server.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"}
            },
            "required": ["session_token"],
        },
    },
    {
        "name": "browse_project",
        "description": "List files and subdirectories within a project folder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"},
                "project_id": {"type": "string", "description": "Project folder name"},
                "subpath": {"type": "string", "description": "Optional sub-path within project", "default": ""},
            },
            "required": ["session_token", "project_id"],
        },
    },
    {
        "name": "run_dlc_analysis",
        "description": "Run DLC pose estimation analysis on a video file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"},
                "config_path": {"type": "string", "description": "Full path to DLC config.yaml"},
                "video_path": {"type": "string", "description": "Full path to video file"},
            },
            "required": ["session_token", "config_path", "video_path"],
        },
    },
    {
        "name": "run_anipose_pipeline",
        "description": (
            "Run an Anipose pipeline operation. Valid operations: calibrate, filter_2d, triangulate, "
            "filter_3d, organize_for_anipose, convert_mediapipe_csv_to_h5, convert_mediapipe_to_dlc_csv, "
            "convert_3d_csv_to_mat."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"},
                "project_id": {"type": "string"},
                "operation": {"type": "string", "description": "Pipeline operation name"},
                "config_path": {"type": "string", "description": "Full path to Anipose config.toml (required for non-mediapipe ops)", "default": ""},
                "scorer": {"type": "string", "description": "Scorer name for MediaPipe ops (default 'User')", "default": "User"},
            },
            "required": ["session_token", "project_id", "operation"],
        },
    },
    {
        "name": "extract_frames",
        "description": "Extract evenly-spaced frames from a video and save to labeled-data.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"},
                "config_path": {"type": "string", "description": "Full path to DLC config.yaml"},
                "video_path": {"type": "string", "description": "Full path to video file"},
                "count": {"type": "integer", "description": "Number of frames to extract", "default": 20},
            },
            "required": ["session_token", "config_path", "video_path"],
        },
    },
    {
        "name": "jitter_prelabel",
        "description": (
            "Detect jittery frames (large raw vs filtered displacement) in a labeled-data stem "
            "and add them back with median-filtered coordinates as initial labels for retraining."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"},
                "config_path": {"type": "string", "description": "Full path to DLC config.yaml"},
                "stem_path": {"type": "string", "description": "Full path to labeled-data/<stem>/ folder"},
                "video_path": {"type": "string", "description": "Full path to the source video"},
                "px_threshold": {"type": "number", "description": "Pixel displacement threshold (default 10)", "default": 10},
                "min_jittery_parts": {"type": "integer", "description": "Min bodyparts above threshold per frame (default 3)", "default": 3},
                "max_frames": {"type": "integer", "description": "Max frames to extract (default 200)", "default": 200},
            },
            "required": ["session_token", "config_path", "stem_path", "video_path"],
        },
    },
    {
        "name": "get_task_status",
        "description": "Poll the status of a background Celery task by task_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["session_token", "task_id"],
        },
    },
    {
        "name": "webapp_link",
        "description": "Generate a clickable URL to open the webapp (optionally at a specific stem).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_token": {"type": "string"},
                "stem": {"type": "string", "description": "Optional labeled-data stem name for VLM refiner deep-link", "default": ""},
            },
            "required": ["session_token"],
        },
    },
]


# ── Tool implementations ──────────────────────────────────────────

def _tool_list_dlc_projects(args: dict) -> str:
    _check_token(args["session_token"])
    data_dir = _data_dir()
    if not data_dir.is_dir():
        return json.dumps([])
    projects = sorted(
        [{"id": d.name, "name": d.name, "path": str(d)}
         for d in data_dir.iterdir() if d.is_dir() and (d / "config.yaml").is_file()],
        key=lambda p: p["name"],
    )
    return json.dumps(projects)


def _tool_list_anipose_projects(args: dict) -> str:
    _check_token(args["session_token"])
    data_dir = _data_dir()
    if not data_dir.is_dir():
        return json.dumps([])
    projects = sorted(
        [{"id": d.name, "name": d.name, "path": str(d)}
         for d in data_dir.iterdir() if d.is_dir() and (d / "config.toml").is_file()],
        key=lambda p: p["name"],
    )
    return json.dumps(projects)


def _tool_browse_project(args: dict) -> str:
    _check_token(args["session_token"])
    project_id = args["project_id"]
    subpath = args.get("subpath", "")
    data_dir = _data_dir()
    project_dir = (data_dir / project_id).resolve()
    if not project_dir.is_relative_to(data_dir):
        raise ValueError("Access denied")
    target = (project_dir / subpath).resolve() if subpath else project_dir
    if not target.is_relative_to(project_dir):
        raise ValueError("Access denied")
    if not target.is_dir():
        raise FileNotFoundError(f"Directory not found: {target}")
    entries = []
    for child in sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if child.name.startswith(".") or child.name.startswith("@"):
            continue
        entries.append({
            "name": child.name,
            "type": "dir" if child.is_dir() else "file",
            "path": str(child),
        })
    return json.dumps({"path": str(target), "entries": entries})


def _tool_run_dlc_analysis(args: dict) -> str:
    _check_token(args["session_token"])
    config_path = args["config_path"]
    video_path = args["video_path"]
    task = _celery().send_task(
        "tasks.dlc_analyze",
        kwargs={"config_path": config_path, "target_path": video_path, "params": {}},
        queue="pytorch",
    )
    return json.dumps({"task_id": task.id, "operation": "dlc_analyze"})


_ANIPOSE_OPERATION_TASKS = {
    "calibrate":                    "tasks.process_calibrate",
    "filter_2d":                    "tasks.process_filter_2d",
    "triangulate":                  "tasks.process_triangulate",
    "filter_3d":                    "tasks.process_filter_3d",
    "organize_for_anipose":         "tasks.process_organize_for_anipose",
    "convert_mediapipe_csv_to_h5":  "tasks.process_convert_mediapipe_csv_to_h5",
    "convert_mediapipe_to_dlc_csv": "tasks.process_convert_mediapipe_to_dlc_csv",
    "convert_3d_csv_to_mat":        "tasks.process_convert_3d_csv_to_mat",
}
_ANIPOSE_MEDIAPIPE_OPS = {
    "organize_for_anipose", "convert_mediapipe_csv_to_h5",
    "convert_mediapipe_to_dlc_csv", "convert_3d_csv_to_mat",
}


def _tool_run_anipose_pipeline(args: dict) -> str:
    _check_token(args["session_token"])
    project_id = args["project_id"]
    operation  = args["operation"].lower()
    config_path = args.get("config_path", "")
    scorer      = args.get("scorer", "User") or "User"
    if operation not in _ANIPOSE_OPERATION_TASKS:
        raise ValueError(f"Unknown operation '{operation}'. Valid: {sorted(_ANIPOSE_OPERATION_TASKS)}")
    data_dir = _data_dir()
    project_dir = data_dir / project_id
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Project not found: {project_id}")
    if operation in _ANIPOSE_MEDIAPIPE_OPS:
        task_kwargs = {"session_path": str(project_dir), "scorer": scorer}
    else:
        task_kwargs = {"session_path": str(project_dir), "config_path": config_path}
    task = _celery().send_task(_ANIPOSE_OPERATION_TASKS[operation], kwargs=task_kwargs, queue="celery")
    return json.dumps({"task_id": task.id, "operation": operation})


def _tool_extract_frames(args: dict) -> str:
    """Extract evenly-spaced frames from video, save to labeled-data/<video_stem>/."""
    _check_token(args["session_token"])
    import re as _re
    import cv2 as _cv2
    import yaml as _yaml
    video_path  = Path(args["video_path"])
    config_path = Path(args["config_path"])
    count       = int(args.get("count", 20))
    if not video_path.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not config_path.is_file():
        raise FileNotFoundError(f"config.yaml not found: {config_path}")
    with open(str(config_path)) as _f:
        cfg = _yaml.safe_load(_f)
    project_dir = config_path.parent
    stem_dir = project_dir / "labeled-data" / video_path.stem
    stem_dir.mkdir(parents=True, exist_ok=True)
    existing = {
        int(m.group(1))
        for p in stem_dir.glob("img*-*.png")
        if (m := _re.search(r"img\d+-(\d+)\.png$", p.name))
    }
    # Determine next_nnnn from max existing NNNN
    existing_files = list(stem_dir.glob("img*-*.png"))
    if existing_files:
        nnnn_vals = []
        for p in existing_files:
            m = _re.match(r"img(\d+)-", p.name)
            if m:
                nnnn_vals.append(int(m.group(1)))
        next_nnnn = (max(nnnn_vals) + 1) if nnnn_vals else 0
    else:
        next_nnnn = 0
    cap = _cv2.VideoCapture(str(video_path))
    total = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    if total <= 0:
        raise ValueError(f"Cannot read frame count from {video_path}")
    step = max(1, total // count)
    frame_nums = [i * step for i in range(count) if i * step < total]
    added = 0
    cap = _cv2.VideoCapture(str(video_path))
    for fn in frame_nums:
        if fn in existing:
            continue
        cap.set(_cv2.CAP_PROP_POS_FRAMES, fn)
        ret, frame = cap.read()
        if not ret:
            continue
        filename = f"img{next_nnnn:04d}-{fn:05d}.png"
        _cv2.imwrite(str(stem_dir / filename), frame)
        next_nnnn += 1
        added += 1
    cap.release()
    return json.dumps({"added": added, "stem": stem_dir.name, "video": video_path.name})


def _tool_jitter_prelabel(args: dict) -> str:
    _check_token(args["session_token"])
    task = _celery().send_task(
        "tasks.dlc_jitter_prelabel",
        kwargs={
            "config_path":       args["config_path"],
            "stem_path":         args["stem_path"],
            "video_path":        args["video_path"],
            "px_threshold":      float(args.get("px_threshold", 10)),
            "min_jittery_parts": int(args.get("min_jittery_parts", 3)),
            "max_frames":        int(args.get("max_frames", 200)),
            "webapp_public_url": _public_url(),
        },
        queue="pytorch",
    )
    return json.dumps({"task_id": task.id, "operation": "jitter_prelabel"})


def _tool_get_task_status(args: dict) -> str:
    _check_token(args["session_token"])
    task_id = args["task_id"]
    result = _celery().AsyncResult(task_id)
    state = result.state
    info = result.info
    if isinstance(info, Exception):
        info = str(info)
    return json.dumps({"state": state, "result": info, "task_id": task_id}, default=str)


def _tool_webapp_link(args: dict) -> str:
    _check_token(args["session_token"])
    token = _app_token()
    base = _public_url() or "http://localhost:5000"
    stem = args.get("stem", "")
    if stem:
        url = f"{base}/vlm/refiner?token={token}&stem={stem}"
    else:
        url = f"{base}/?token={token}"
    return json.dumps({"url": url})


_TOOL_DISPATCH = {
    "list_dlc_projects":     _tool_list_dlc_projects,
    "list_anipose_projects": _tool_list_anipose_projects,
    "browse_project":        _tool_browse_project,
    "run_dlc_analysis":      _tool_run_dlc_analysis,
    "run_anipose_pipeline":  _tool_run_anipose_pipeline,
    "extract_frames":        _tool_extract_frames,
    "jitter_prelabel":       _tool_jitter_prelabel,
    "get_task_status":       _tool_get_task_status,
    "webapp_link":           _tool_webapp_link,
}


# ── Route ─────────────────────────────────────────────────────────

@bp.route("/mcp", methods=["GET", "POST", "DELETE"])
def mcp_endpoint():
    if request.method == "GET":
        return Response(status=405)

    if request.method == "DELETE":
        return Response(status=204)

    msg = request.get_json(force=True, silent=True) or {}
    method = msg.get("method", "")
    params = msg.get("params") or {}
    req_id = msg.get("id")

    session_id = request.headers.get("Mcp-Session-Id") or str(uuid.uuid4())

    try:
        if method == "initialize":
            result = {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": _SERVER_INFO,
            }
        elif method in ("notifications/initialized", "notifications/cancelled"):
            return Response(status=204)
        elif method == "tools/list":
            result = {"tools": _TOOLS}
        elif method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            if name not in _TOOL_DISPATCH:
                raise ValueError(f"Unknown tool: {name}")
            text = _TOOL_DISPATCH[name](arguments)
            result = _content(text)
        else:
            resp = jsonify(_err(req_id, -32601, f"Method not found: {method}"))
            resp.headers["Mcp-Session-Id"] = session_id
            return resp

        resp = jsonify(_ok(req_id, result))

    except PermissionError as exc:
        resp = jsonify(_err(req_id, -32000, str(exc)))
    except (FileNotFoundError, ValueError) as exc:
        resp = jsonify(_err(req_id, -32602, str(exc)))
    except Exception as exc:
        resp = jsonify(_err(req_id, -32603, f"Internal error: {exc}"))

    resp.headers["Mcp-Session-Id"] = session_id
    return resp
