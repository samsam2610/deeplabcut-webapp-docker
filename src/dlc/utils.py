"""
DLC utility functions and constants shared across Blueprint modules.

Pure functions (no app/request state) and functions that accept
data_dir/user_data_dir as explicit arguments.
"""
from __future__ import annotations
from pathlib import Path

# Engine aliases
_TF_ENGINE_ALIASES = {"tensorflow", "tf"}

# Engine-specific pipeline constants
#   (models_folder, model_config_file, eval_results_folder)
_ENGINE_PYTORCH = ("dlc-models-pytorch", "pytorch_config.yaml", "evaluation-results-pytorch")
_ENGINE_TF      = ("dlc-models",         "pose_cfg.yaml",        "evaluation-results")

_PIPELINE_BASE_FOLDERS = [
    ("Labeled Data",      "labeled-data"),
    ("Training Datasets", "training-datasets"),
    ("Videos",            "videos"),
]


def _engine_info(engine: str) -> tuple[str, str, str]:
    """Return (models_folder, model_config_file, eval_results_folder) for engine."""
    if (engine or "pytorch").lower() in _TF_ENGINE_ALIASES:
        return _ENGINE_TF
    return _ENGINE_PYTORCH


def _get_pipeline_folders(engine: str) -> list:
    """Return pipeline folder list with the correct models folder for the engine."""
    models_folder = _engine_info(engine)[0]
    return [("Models", models_folder)] + _PIPELINE_BASE_FOLDERS


def _get_engine_queue(engine: str) -> str:
    """Return the Celery queue name for the given engine."""
    if (engine or "pytorch").lower() in _TF_ENGINE_ALIASES:
        return "tensorflow"
    return "pytorch"


_FS_LS_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".m4v"}
_FS_LS_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
_FS_LS_MEDIA_EXTS = _FS_LS_VIDEO_EXTS | _FS_LS_IMAGE_EXTS


def _dir_has_media(path: Path) -> bool:
    """Return True if path contains at least one supported media file or subdirectory."""
    try:
        for child in path.iterdir():
            if child.name.startswith("."):
                continue
            if child.is_dir():
                return True
            if child.suffix.lower() in _FS_LS_MEDIA_EXTS:
                return True
    except PermissionError:
        pass
    return False


def _walk_dir(path: Path, project_path: Path, depth: int = 0, max_depth: int = 6) -> list:
    """
    Recursively list a directory relative to project_path.
    Each item: { name, type, rel_path, size? (files), children? (dirs) }
    Dirs are sorted before files; hidden entries are skipped.
    """
    items = []
    try:
        entries = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        for item in entries:
            if item.name.startswith(".") or item.name.startswith("@"):
                continue
            rel = str(item.relative_to(project_path))
            if item.is_dir():
                children = _walk_dir(item, project_path, depth + 1, max_depth) if depth < max_depth else []
                items.append({"name": item.name, "type": "dir", "rel_path": rel, "children": children})
            else:
                items.append({"name": item.name, "type": "file", "size": item.stat().st_size, "rel_path": rel})
    except PermissionError:
        pass
    return items


def _dlc_project_security_check(p: Path, data_dir: Path, user_data_dir: Path) -> bool:
    """Return True if p is inside an allowed data root."""
    allowed_roots = [data_dir.resolve(), user_data_dir.resolve()]
    pr = p.resolve()
    return any(pr == r or str(pr).startswith(str(r) + "/") for r in allowed_roots)


def _resolve_project_dir(project_id: str, data_dir: Path, root: str = "") -> Path:
    base = Path(root) if root else data_dir
    project_dir = (base / project_id).resolve()
    if not project_dir.is_relative_to(base.resolve()):
        raise ValueError("Invalid project path.")
    return project_dir


def _parse_dlc_yaml(config_path: Path, yaml_lib) -> dict:
    """Parse a DLC config.yaml. Returns {} on failure."""
    if yaml_lib is None:
        return {}
    try:
        with open(config_path) as f:
            return yaml_lib.safe_load(f) or {}
    except Exception:
        return {}
