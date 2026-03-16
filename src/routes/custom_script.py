"""
Blueprint: custom_script
Handles custom Python script execution (/custom-script/run, /custom-script/status/<job_id>).
"""
import subprocess as _subprocess
import sys as _sys
import threading as _threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("custom_script", __name__)

# In-memory job store (per-process; jobs are lost on restart, which is fine)
_script_jobs: dict = {}
_script_jobs_lock = _threading.Lock()

_CS_TIMEOUT   = 600   # 10-minute hard limit per script run
_CS_ALLOWED   = {".py"}
_CS_INPUT_EXT = {".csv"}


def _data_dir() -> Path:
    return current_app.config["APP_DATA_DIR"]

def _user_data_dir() -> Path:
    return current_app.config.get("APP_USER_DATA_DIR", Path("/user-data"))


def _cs_allowed_root(p: Path) -> bool:
    """Return True if *p* is inside USER_DATA_DIR or DATA_DIR."""
    roots = [_data_dir().resolve()]
    uddir = _user_data_dir()
    if Path(uddir).is_dir():
        roots.append(Path(uddir).resolve())
    try:
        return any(p.is_relative_to(r) for r in roots)
    except Exception:
        return False


@bp.route("/custom-script/run", methods=["POST"])
def custom_script_run():
    """
    Launch a user-supplied Python script in an isolated subprocess.

    Expected JSON body:
      script_path : absolute path to a .py file inside user-data / data dir
      input_mode  : "file" | "folder"
      input_path  : absolute path to a single CSV  OR  a folder of CSVs
    """
    body        = request.get_json(force=True) or {}
    script_path = (body.get("script_path") or "").strip()
    input_mode  = (body.get("input_mode")  or "file").strip()
    input_path  = (body.get("input_path")  or "").strip()

    # ── Validate script ───────────────────────────────────────────
    if not script_path:
        return jsonify({"error": "script_path required"}), 400
    sp = Path(script_path).resolve()
    if not sp.is_file():
        return jsonify({"error": "Script file not found"}), 404
    if sp.suffix.lower() not in _CS_ALLOWED:
        return jsonify({"error": "Script must be a .py file"}), 400
    if not _cs_allowed_root(sp):
        return jsonify({"error": "Script must be inside user-data or data directory"}), 403

    # ── Collect input CSV paths ───────────────────────────────────
    if not input_path:
        return jsonify({"error": "input_path required"}), 400
    ip = Path(input_path).resolve()
    if not _cs_allowed_root(ip):
        return jsonify({"error": "Input path must be inside user-data or data directory"}), 403

    input_csvs: list[str] = []
    if input_mode == "folder":
        if not ip.is_dir():
            return jsonify({"error": "Input folder not found"}), 404
        input_csvs = sorted(
            str(f) for f in ip.iterdir()
            if f.is_file() and f.suffix.lower() in _CS_INPUT_EXT
        )
        if not input_csvs:
            return jsonify({"error": "No CSV files found in the selected folder"}), 400
    else:
        if not ip.is_file():
            return jsonify({"error": "Input CSV file not found"}), 404
        if ip.suffix.lower() not in _CS_INPUT_EXT:
            return jsonify({"error": "Input must be a .csv file"}), 400
        input_csvs = [str(ip)]

    # ── Create timestamped output directory ───────────────────────
    ts      = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = _data_dir() / "script_outputs" / f"{ts}_{sp.stem[:24]}"
    out_dir.mkdir(parents=True, exist_ok=True)

    job_id = uuid.uuid4().hex[:12]
    with _script_jobs_lock:
        _script_jobs[job_id] = {
            "status":     "running",
            "output":     "",
            "error":      None,
            "output_dir": str(out_dir),
        }

    # ── Spawn subprocess ──────────────────────────────────────────
    wrapper = (
        "import importlib.util as _ilu\n"
        f"_spec = _ilu.spec_from_file_location('_user_script', {repr(str(sp))})\n"
        "_mod  = _ilu.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_mod)\n"
        f"_mod.run({repr(input_csvs)}, {repr(str(out_dir))})\n"
    )

    def _run_job():
        try:
            result = _subprocess.run(
                [_sys.executable, "-c", wrapper],
                capture_output=True,
                text=True,
                timeout=_CS_TIMEOUT,
            )
            combined = result.stdout
            if result.stderr:
                combined += "\n[stderr]\n" + result.stderr
            with _script_jobs_lock:
                _script_jobs[job_id]["output"] = combined
                if result.returncode == 0:
                    _script_jobs[job_id]["status"] = "done"
                else:
                    _script_jobs[job_id]["status"] = "error"
                    _script_jobs[job_id]["error"]  = f"Exit code {result.returncode}"
        except _subprocess.TimeoutExpired:
            with _script_jobs_lock:
                _script_jobs[job_id]["status"] = "error"
                _script_jobs[job_id]["error"]  = f"Script timed out ({_CS_TIMEOUT}s limit)"
        except Exception as exc:
            with _script_jobs_lock:
                _script_jobs[job_id]["status"] = "error"
                _script_jobs[job_id]["error"]  = str(exc)

    _threading.Thread(target=_run_job, daemon=True).start()

    return jsonify({"job_id": job_id, "output_dir": str(out_dir)})


@bp.route("/custom-script/status/<job_id>")
def custom_script_status(job_id: str):
    """Poll the status of a running custom script job."""
    with _script_jobs_lock:
        job = _script_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)
