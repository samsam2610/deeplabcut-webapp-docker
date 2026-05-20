"""
Posture-Centric VLM Refiner — Flask Blueprint.

Routes
------
GET  /vlm-posture-refiner                  Render the posture-refiner UI.
GET  /posture/index-status                 Check if posture_index.json exists.
POST /posture/index/build                  Build posture index (streams NDJSON progress).
GET  /posture/frame-data                   Frame labels + top-3 posture-similar references.
POST /posture/refine                       Posture-aware VLM refinement.
GET  /posture/reference-image/<stem>/<fn>  Serve reference frame PNG.
"""
from __future__ import annotations

import json
from pathlib import Path

from flask import (
    Blueprint, Response, jsonify, render_template,
    request, stream_with_context,
)
from werkzeug.utils import secure_filename

from . import ctx as _ctx
from .labeling import _parse_dlc_yaml, _sec_check, _dlc_key, _natural_keys
from dlc.utils import _dlc_project_security_check

bp = Blueprint("dlc_posture", __name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_project() -> tuple[dict | None, str | None]:
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return None, "No active DLC project."
    return json.loads(raw), None


def _project_path_checked() -> tuple[Path | None, str | None]:
    project_data, err = _get_project()
    if err:
        return None, err
    pp = Path(project_data.get("project_path", ""))
    if not pp.is_dir():
        return None, "Project directory not found."
    if not _sec_check(pp):
        return None, "Access denied."
    return pp, None


def _vi():
    from dlc import vlm_indexer
    return vlm_indexer


def _get_bodyparts(pp: Path) -> list[str]:
    """Read bodyparts from config.yaml; return [] on failure."""
    cfg_path = pp / "config.yaml"
    if not cfg_path.is_file():
        return []
    try:
        import yaml as _y
        cfg = _y.safe_load(cfg_path.read_text())
        return list(cfg.get("bodyparts", []))
    except Exception:
        return []


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/vlm-posture-refiner")
def posture_refiner_ui():
    """Render the posture-centric refiner UI."""
    return render_template("posture_refiner.html")


@bp.route("/posture/index-status")
def posture_index_status():
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400
    vi    = _vi()
    index = vi.load_posture_index(pp)
    if index is None:
        return jsonify({"exists": False, "total_frames": 0})
    return jsonify({
        "exists":       True,
        "total_frames": index.get("total_frames", len(index.get("frames", []))),
        "built_at":     index.get("built_at", ""),
        "bodyparts":    index.get("bodyparts", []),
    })


@bp.route("/posture/index/build", methods=["POST"])
def posture_build_index():
    """
    Build (or rebuild) the posture index for the active project.
    Streams newline-delimited JSON progress:
      {"done": int, "total": int}
      {"done": int, "total": int, "finished": true, "built_at": str}
      {"error": str}
    """
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400

    vi = _vi()
    bodyparts = _get_bodyparts(pp)

    def _generate():
        progress_events: list[str] = []

        def _cb(done: int, total: int):
            progress_events.append(json.dumps({"done": done, "total": total}) + "\n")

        try:
            index = vi.build_posture_index(pp, bodyparts=bodyparts or None, progress_cb=_cb)
            yield from progress_events
            yield json.dumps({
                "done":     index["total_frames"],
                "total":    index["total_frames"],
                "finished": True,
                "built_at": index["built_at"],
            }) + "\n"
        except Exception as exc:
            yield from progress_events
            yield json.dumps({"error": str(exc)}) + "\n"

    return Response(
        stream_with_context(_generate()),
        mimetype="application/x-ndjson",
    )


@bp.route("/posture/frame-data")
def posture_frame_data():
    """
    GET /posture/frame-data?video_stem=<stem>&frame=<filename>&min_lh=<float>

    Returns:
      - current_labels (machine or CollectedData fallback)
      - posture_signature for the query frame
      - top-3 posture-similar reference frames from the index
      - saved posture VLM result for this frame (if any)
    """
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400

    video_stem = request.args.get("video_stem", "")
    frame      = request.args.get("frame", "")
    try:
        min_lh = float(request.args.get("min_lh", 0.0))
    except ValueError:
        min_lh = 0.0

    if not video_stem or not frame:
        return jsonify({"error": "video_stem and frame are required."}), 400

    from .labeling import _get_dlc_project_and_config
    project_data, cfg, label_err = _get_dlc_project_and_config()
    scorer    = (cfg or {}).get("scorer", "User")
    bodyparts = (cfg or {}).get("bodyparts", [])

    vi       = _vi()
    stem_dir = pp / "labeled-data" / secure_filename(video_stem)

    # Machine coords (raw predictions preferred; CollectedData fallback)
    current_labels: dict = {}
    raw_labels = vi.read_raw_predictions(stem_dir, min_lh=min_lh)
    if raw_labels is not None:
        current_labels = raw_labels.get(frame, {})
    if not current_labels:
        csv_path = stem_dir / f"CollectedData_{scorer}.csv"
        if not csv_path.is_file():
            candidates = sorted(stem_dir.glob("CollectedData_*.csv"))
            csv_path   = candidates[0] if candidates else None
        if csv_path and csv_path.is_file():
            current_labels = vi._read_labels_from_csv(csv_path).get(frame, {})

    has_raw = vi._ensure_raw_pred_csv(stem_dir)

    # Compute posture signature for the query frame
    query_sig = vi.posture_signature(current_labels, list(bodyparts))

    # KNN via posture index
    posture_index = vi.load_posture_index(pp)
    similar: list[dict] = []
    if posture_index:
        # Try stored signature first; re-compute if absent
        stored_sig = vi.get_posture_signature_for_frame(posture_index, video_stem, frame)
        if stored_sig:
            query_sig = stored_sig
        similar = vi.find_similar_posture(
            posture_index, query_sig, k=3,
            exclude_video_stem=video_stem,
        )
        for r in similar:
            r.pop("signature", None)   # don't send raw vector to browser

    # Saved posture VLM result
    saved_coords, saved_debug = vi.load_posture_result(stem_dir, frame)

    return jsonify({
        "video_stem":          video_stem,
        "frame":               frame,
        "current_labels":      current_labels,
        "has_raw_predictions": has_raw,
        "posture_signature":   query_sig,
        "similar":             similar,
        "index_available":     posture_index is not None,
        "bodyparts":           bodyparts,
        "scorer":              scorer,
        "vlm_coords":          saved_coords,
        "vlm_debug":           saved_debug,
    })


@bp.route("/posture/refine", methods=["POST"])
def posture_refine():
    """
    POST /posture/refine
    Body: {
      "active_video_stem":    str,
      "active_frame":         str,
      "reference_video_stem": str,
      "reference_frame":      str,
      "reference_labels":     {bp: [x, y] or null},
      "machine_coords":       {bp: [x, y] or null},
      "bodyparts":            [str, ...]
    }
    Returns: {"vlm_coords": {bp: [x, y] or null}, "vlm_debug": {...}}
    """
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400

    body = request.get_json(force=True) or {}
    active_stem    = secure_filename(body.get("active_video_stem", ""))
    active_frame   = secure_filename(body.get("active_frame", ""))
    ref_stem       = secure_filename(body.get("reference_video_stem", ""))
    ref_frame      = secure_filename(body.get("reference_frame", ""))
    ref_labels     = body.get("reference_labels", {})
    machine_coords = body.get("machine_coords", {})
    bodyparts      = body.get("bodyparts", [])

    if not all([active_stem, active_frame, ref_stem, ref_frame]):
        return jsonify({"error": "active_video_stem, active_frame, reference_video_stem, reference_frame are required."}), 400

    active_path = pp / "labeled-data" / active_stem / active_frame
    ref_path    = pp / "labeled-data" / ref_stem    / ref_frame

    pp_resolved = pp.resolve()
    for p in (active_path, ref_path):
        try:
            p.resolve().relative_to(pp_resolved)
        except ValueError:
            return jsonify({"error": "Access denied."}), 403

    if not active_path.is_file():
        return jsonify({"error": f"Active frame not found: {active_frame}"}), 404
    if not ref_path.is_file():
        return jsonify({"error": f"Reference frame not found: {ref_frame}"}), 404

    vi = _vi()
    vlm_coords, vlm_debug = vi.refine_coords_posture_aware(
        active_frame_path=active_path,
        reference_frame_path=ref_path,
        reference_labels=ref_labels,
        machine_coords=machine_coords,
        bodyparts=bodyparts,
    )

    stem_dir = pp / "labeled-data" / active_stem
    vi.save_posture_result(stem_dir, active_frame, vlm_coords, vlm_debug)

    return jsonify({"vlm_coords": vlm_coords, "vlm_debug": vlm_debug})


@bp.route("/posture/reference-image/<path:video_stem>/<filename>")
def posture_serve_reference_image(video_stem: str, filename: str):
    """Serve a labeled-data PNG for the reference panel."""
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400

    from flask import send_file
    safe_stem = secure_filename(video_stem)
    safe_file = secure_filename(filename)
    img_path  = (pp / "labeled-data" / safe_stem / safe_file).resolve()

    try:
        img_path.relative_to(pp.resolve())
    except ValueError:
        return jsonify({"error": "Access denied."}), 403

    if not img_path.is_file():
        return jsonify({"error": "Image not found."}), 404

    return send_file(str(img_path), mimetype="image/png")
