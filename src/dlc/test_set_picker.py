"""
DLC Test-set Picker Blueprint.

Routes:
  GET  /dlc/project/test-set/marks
  POST /dlc/project/test-set/marks/<video_stem>/<image_name>
  POST /dlc/project/test-set/marks/bulk
  POST /dlc/project/test-set/marks/clean-stale
  POST /dlc/project/test-set/mode

All routes operate on the active DLC project (Redis key
webapp:dlc_project:<uid>) and use the per-project SQLite at
<project_path>/test_set_marks.sqlite.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, session as flask_session
from werkzeug.utils import secure_filename

from dlc import marks_store
from . import ctx as _ctx

bp = Blueprint("dlc_test_set_picker", __name__)


def _user_id() -> str:
    if "uid" not in flask_session:
        flask_session["uid"] = uuid.uuid4().hex
    return flask_session["uid"]


def _dlc_key() -> str:
    return f"webapp:dlc_project:{_user_id()}"


def _active_project() -> tuple[Path | None, tuple | None]:
    """Return (project_path, error_response). Error response is None on success."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return None, (jsonify({"error": "No active DLC project."}), 400)
    try:
        data = json.loads(raw)
    except Exception:
        return None, (jsonify({"error": "Corrupt project state."}), 500)
    project_path = Path(data.get("project_path", "") or "")
    if not project_path.is_dir():
        return None, (jsonify({"error": "Project directory not found."}), 404)
    return project_path, None


def _safe_stem(value: str) -> str | None:
    """Return a secure_filename'd stem, or None if the input is suspicious."""
    if not value or value != value.strip():
        return None
    cleaned = secure_filename(value)
    if not cleaned or cleaned != value:
        # secure_filename mutates anything traversal-ish; reject mutation
        return None
    return cleaned


def _safe_image_name(value: str) -> str | None:
    if not value or "/" in value or "\\" in value or ".." in value:
        return None
    cleaned = secure_filename(value)
    if not cleaned or cleaned != value:
        return None
    return cleaned


def _frame_belongs_to_project(project_path: Path, stem: str, image: str) -> bool:
    labeled_root = (project_path / "labeled-data").resolve()
    candidate = (labeled_root / stem / image).resolve()
    try:
        candidate.relative_to(labeled_root)
    except ValueError:
        return False
    return True


def _count_labeled_frames(project_path: Path) -> tuple[int, dict[str, int]]:
    labeled_root = project_path / "labeled-data"
    per_folder: dict[str, int] = {}
    total = 0
    if not labeled_root.is_dir():
        return 0, {}
    for stem_dir in sorted(labeled_root.iterdir()):
        if not stem_dir.is_dir():
            continue
        n = sum(1 for p in stem_dir.iterdir() if p.suffix.lower() == ".png")
        per_folder[stem_dir.name] = n
        total += n
    return total, per_folder


@bp.route("/dlc/project/test-set/marks", methods=["GET"])
def get_marks():
    project_path, err = _active_project()
    if err:
        return err
    grouped = marks_store.get_marks_grouped(project_path)
    mode = marks_store.get_mode(project_path)
    total_labeled, per_folder_total = _count_labeled_frames(project_path)
    per_folder = {
        stem: {"marked": len(grouped.get(stem, [])), "total": per_folder_total.get(stem, 0)}
        for stem in set(grouped) | set(per_folder_total)
    }
    return jsonify({
        "mode": mode,
        "marks": grouped,
        "counts": {
            "marked": sum(len(v) for v in grouped.values()),
            "total_labeled": total_labeled,
            "per_folder": per_folder,
        },
    })


@bp.route("/dlc/project/test-set/marks/<path:video_stem>/<image_name>", methods=["POST"])
def post_mark(video_stem: str, image_name: str):
    project_path, err = _active_project()
    if err:
        return err
    stem = _safe_stem(video_stem)
    image = _safe_image_name(image_name)
    if stem is None or image is None:
        return jsonify({"error": "Invalid path component."}), 400
    if not _frame_belongs_to_project(project_path, stem, image):
        return jsonify({"error": "Path escapes labeled-data root."}), 403

    body = request.get_json(force=True, silent=True) or {}
    marked = bool(body.get("marked", True))
    note = body.get("note")
    marks_store.set_mark(project_path, stem, image, marked, note)
    return jsonify({"ok": True, "marked": marked})


@bp.route("/dlc/project/test-set/marks/bulk", methods=["POST"])
def post_bulk():
    project_path, err = _active_project()
    if err:
        return err
    body = request.get_json(force=True, silent=True) or {}
    ops_in = body.get("ops") or []
    if not isinstance(ops_in, list):
        return jsonify({"error": "ops must be a list."}), 400
    cleaned: list[dict] = []
    for op in ops_in:
        if not isinstance(op, dict):
            continue
        stem = _safe_stem(op.get("video_stem", ""))
        image = _safe_image_name(op.get("image_name", ""))
        if stem is None or image is None:
            continue
        if not _frame_belongs_to_project(project_path, stem, image):
            continue
        cleaned.append({
            "video_stem": stem,
            "image_name": image,
            "marked": bool(op.get("marked", True)),
            "note": op.get("note"),
        })
    applied = marks_store.bulk_set(project_path, cleaned)
    return jsonify({"ok": True, "applied": applied})


@bp.route("/dlc/project/test-set/marks/clean-stale", methods=["POST"])
def post_clean_stale():
    project_path, err = _active_project()
    if err:
        return err
    removed = marks_store.clean_stale(project_path)
    return jsonify({"ok": True, "removed": removed})


@bp.route("/dlc/project/test-set/mode", methods=["POST"])
def post_mode():
    project_path, err = _active_project()
    if err:
        return err
    body = request.get_json(force=True, silent=True) or {}
    mode = (body.get("mode") or "").strip()
    if mode not in marks_store.VALID_MODES:
        return jsonify({"error": f"mode must be one of {list(marks_store.VALID_MODES)}"}), 400
    marks_store.set_mode(project_path, mode)
    return jsonify({"ok": True, "mode": mode})


# ── Inspect frozen splits ─────────────────────────────────────────────────────

import pickle
import re as _re


def _parse_iteration_from_config(project_path: Path) -> int:
    cfg_path = project_path / "config.yaml"
    if not cfg_path.is_file():
        return 0
    text = cfg_path.read_text()
    m = _re.search(r'^iteration\s*:\s*(\d+)', text, _re.MULTILINE)
    return int(m.group(1)) if m else 0


_DOC_PICKLE_RE = _re.compile(
    r"^Documentation_data-(?P<task>.+)_(?P<frac>\d+)shuffle(?P<shuffle>\d+)\.pickle$"
)


def _read_collected_data_csv_rows(csv_path: Path) -> list[tuple[str, str]] | None:
    """Read the row MultiIndex (video_stem, image_name) from the sibling
    CollectedData_<scorer>.csv that DLC writes alongside the H5.

    DLC's CSV layout: 3 header rows (scorer / bodyparts / coords) where the
    first 2-3 cells are blank placeholders for the row-index, followed by
    data rows whose first 3 cells are `labeled-data, <video_stem>, <image_name>`.

    Using the CSV (stdlib `csv` module) instead of the H5 keeps the inspect
    endpoint working in the flask container even when h5py/PyTables are not
    installed. Returns None if the CSV can't be parsed.
    """
    import csv as _csv
    try:
        with open(csv_path, "r", newline="") as f:
            reader = _csv.reader(f)
            rows_out: list[tuple[str, str]] = []
            for i, row in enumerate(reader):
                # Skip the 3 column-header rows (scorer / bodyparts / coords).
                if i < 3:
                    continue
                if len(row) < 3:
                    continue
                # First 3 cells = the row MultiIndex tuple.
                _root, stem, image = row[0], row[1], row[2]
                if not stem or not image:
                    continue
                rows_out.append((stem, image))
        return rows_out
    except Exception:
        return None


class _LenientUnpickler(pickle.Unpickler):
    """Pickle loader that substitutes a placeholder when a referenced class's
    module is unavailable in this container.

    DLC's Documentation_data-*.pickle stores ruamel.yaml ScalarFloat objects
    (plus other DLC-config types) alongside the numpy arrays we actually need.
    The flask container doesn't have ruamel.yaml installed; standard
    `pickle.load` raises ModuleNotFoundError on those references. This stub
    lets payload[1] (train indices) and payload[2] (test indices) be recovered
    — both are plain numpy arrays with no special class dependencies.
    """

    def find_class(self, module: str, name: str):  # type: ignore[override]
        try:
            return super().find_class(module, name)
        except (ModuleNotFoundError, ImportError, AttributeError):
            class _MissingClassStub:
                def __init__(self, *args, **kwargs):
                    pass

                def __setstate__(self, state):
                    pass

            _MissingClassStub.__module__ = module
            _MissingClassStub.__name__ = name
            return _MissingClassStub


def _read_pickle_dataset(pickle_path: Path) -> dict | None:
    """Parse a Documentation_data-*.pickle and return train/test frame tuples.

    DLC's pickle layout is [Documentation_data, trainIndices, testIndices,
    trainFraction] where Documentation_data is the *train-only filtered*
    list (NOT all labeled frames). The indices reference positions in the
    sibling CollectedData_<scorer> merged DataFrame. We read the sibling
    CSV (stdlib only) to recover the (stem, image) for each positional index.

    Returns None on parse failure or missing sibling CSV.
    """
    m = _DOC_PICKLE_RE.match(pickle_path.name)
    if not m:
        return None
    train_pct = int(m.group("frac"))
    shuffle = int(m.group("shuffle"))
    try:
        with open(pickle_path, "rb") as f:
            payload = _LenientUnpickler(f).load()
    except Exception:
        return None
    if not isinstance(payload, (list, tuple)) or len(payload) < 3:
        return None
    train_idx, test_idx = payload[1], payload[2]

    # Find the sibling CollectedData_*.csv (always written next to the H5 by DLC).
    csv_candidates = sorted(pickle_path.parent.glob("CollectedData_*.csv"))
    if not csv_candidates:
        return None
    rows = _read_collected_data_csv_rows(csv_candidates[0])
    if rows is None:
        return None

    def _resolve(indices) -> list[dict]:
        out: list[dict] = []
        for i in indices:
            i = int(i)
            if i < 0 or i >= len(rows):
                continue  # strips -1 padding and out-of-range
            stem, image = rows[i]
            out.append({"video_stem": stem, "image_name": image})
        return out

    return {
        "shuffle": shuffle,
        "train_fraction": train_pct / 100.0,
        "train": _resolve(train_idx),
        "test":  _resolve(test_idx),
        "documentation_pickle": str(pickle_path.name),
    }


@bp.route("/dlc/project/training-dataset/inspect", methods=["GET"])
def get_inspect():
    project_path, err = _active_project()
    if err:
        return err

    requested_iter = request.args.get("iteration", type=int)
    if requested_iter is None:
        requested_iter = _parse_iteration_from_config(project_path)

    iter_root = project_path / "training-datasets" / f"iteration-{requested_iter}"
    if not iter_root.is_dir():
        return jsonify({"iteration": requested_iter, "datasets": []})

    requested_shuffle = request.args.get("shuffle", type=int)
    datasets: list[dict] = []
    for pickle_path in sorted(iter_root.glob("UnaugmentedDataSet_*/Documentation_data-*.pickle")):
        parsed = _read_pickle_dataset(pickle_path)
        if parsed is None:
            continue
        if requested_shuffle is not None and parsed["shuffle"] != requested_shuffle:
            continue
        datasets.append(parsed)

    return jsonify({"iteration": requested_iter, "datasets": datasets})


# ── List available frozen splits (for inspect dropdown) ───────────────────────

_ITER_FOLDER_RE = _re.compile(r"^iteration-(\d+)$")


@bp.route("/dlc/project/training-dataset/splits", methods=["GET"])
def get_splits():
    """List every Documentation_data-*.pickle on disk so the inspect UI can
    populate its dropdown without users guessing iteration/shuffle numbers.

    Returns: {"splits": [{iteration, shuffle, train_fraction, pickle, label}, ...]}
    sorted by (iteration DESC, shuffle ASC).
    """
    project_path, err = _active_project()
    if err:
        return err

    ts_root = project_path / "training-datasets"
    if not ts_root.is_dir():
        return jsonify({"splits": []})

    splits: list[dict] = []
    for iter_dir in ts_root.iterdir():
        if not iter_dir.is_dir():
            continue
        iter_match = _ITER_FOLDER_RE.match(iter_dir.name)
        if not iter_match:
            continue
        iteration = int(iter_match.group(1))
        for pickle_path in iter_dir.glob("UnaugmentedDataSet_*/Documentation_data-*.pickle"):
            m = _DOC_PICKLE_RE.match(pickle_path.name)
            if not m:
                continue
            try:
                train_pct = int(m.group("frac"))
                shuffle = int(m.group("shuffle"))
            except (TypeError, ValueError):
                continue
            train_fraction = train_pct / 100.0
            splits.append({
                "iteration": iteration,
                "shuffle": shuffle,
                "train_fraction": train_fraction,
                "pickle": pickle_path.name,
                "label": f"iteration-{iteration} • shuffle-{shuffle} • trainset {train_pct}%",
            })

    splits.sort(key=lambda s: (-s["iteration"], s["shuffle"]))
    return jsonify({"splits": splits})
