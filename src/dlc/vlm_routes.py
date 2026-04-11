"""
VLM-Enhanced Labeling — Flask Blueprint.

Routes
------
GET  /vlm/refiner              Render the triple-panel UI page.
GET  /vlm/index-status         Check if an index exists for the active project.
POST /vlm/index/build          Kick off index construction (synchronous, streams progress).
GET  /vlm/similar              KNN search: returns top-k similar labeled frames.
POST /vlm/refine               Call qwen3-vl to suggest corrected keypoint coords.
GET  /vlm/frame-data           Fetch a frame's labels + top-3 similar references.
GET  /vlm/reference-image      Serve a reference frame PNG (from another stem).
"""
from __future__ import annotations

import json
from pathlib import Path

from flask import (
    Blueprint, Response, jsonify, render_template,
    request, session as flask_session, stream_with_context,
)

from . import ctx as _ctx
from .labeling import _parse_dlc_yaml, _sec_check, _dlc_key, _natural_keys
from dlc.utils import _dlc_project_security_check
from werkzeug.utils import secure_filename

bp = Blueprint("dlc_vlm", __name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_project() -> tuple[dict | None, str | None]:
    """Return (project_data, error_message)."""
    raw = _ctx.redis_client().get(_dlc_key())
    if not raw:
        return None, "No active DLC project."
    return json.loads(raw), None


def _project_path_checked() -> tuple[Path | None, str | None]:
    """Return (project_path, error_message), verifying the path exists and is allowed."""
    project_data, err = _get_project()
    if err:
        return None, err
    pp = Path(project_data.get("project_path", ""))
    if not pp.is_dir():
        return None, "Project directory not found."
    if not _sec_check(pp):
        return None, "Access denied."
    return pp, None


def _import_indexer():
    """Late import to avoid loading cv2 at startup when it's not needed."""
    from dlc import vlm_indexer
    return vlm_indexer


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/vlm/refiner")
def vlm_refiner_ui():
    """Render the triple-panel VLM verification dashboard."""
    return render_template("vlm_refiner.html")


@bp.route("/vlm/index-stems")
def vlm_index_stems():
    """GET /vlm/index-stems — list all video_stem values present in the index."""
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400
    vi    = _import_indexer()
    index = vi.load_index(pp)
    if index is None:
        return jsonify({"stems": []})
    return jsonify({"stems": vi.index_stems(index)})


@bp.route("/vlm/index-status")
def vlm_index_status():
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400
    vi = _import_indexer()
    index = vi.load_index(pp)
    if index is None:
        return jsonify({"exists": False, "total_frames": 0})
    return jsonify({
        "exists":       True,
        "total_frames": index.get("total_frames", len(index.get("frames", []))),
        "built_at":     index.get("built_at", ""),
    })


@bp.route("/vlm/index/build", methods=["POST"])
def vlm_build_index():
    """
    Build (or rebuild) the visual index for the active project.
    Streams newline-delimited JSON progress events:
      {"done": int, "total": int}   (progress)
      {"done": int, "total": int, "finished": true, "built_at": str}  (final)
      {"error": str}
    """
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400

    body = request.get_json(force=True) or {}
    use_ollama = bool(body.get("use_ollama", False))

    vi = _import_indexer()

    def _generate():
        state = {"done": 0, "total": 0}

        def _progress(done: int, total: int):
            state["done"]  = done
            state["total"] = total
            yield json.dumps({"done": done, "total": total}) + "\n"

        # We can't use yield inside the callback directly (generator protocol),
        # so we use a queue pattern via a list to batch progress updates.
        progress_events: list[str] = []

        def _cb(done: int, total: int):
            progress_events.append(json.dumps({"done": done, "total": total}) + "\n")

        try:
            index = vi.build_index(pp, use_ollama=use_ollama, progress_cb=_cb)
            # Also build the posture index (best-effort — never fails the stream)
            try:
                vi.build_posture_index(pp)
            except Exception:
                pass
            # Flush any accumulated progress events, then send the final event
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


@bp.route("/vlm/similar")
def vlm_similar():
    """
    GET /vlm/similar?video_stem=<stem>&frame=<filename>&k=3

    Returns top-k similar labeled frames from the index (excludes the query frame itself).
    """
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400

    video_stem    = request.args.get("video_stem", "")
    frame         = request.args.get("frame", "")
    k             = int(request.args.get("k", 5))
    reference_stem = request.args.get("reference_stem", "") or None

    if not video_stem or not frame:
        return jsonify({"error": "video_stem and frame are required."}), 400

    vi    = _import_indexer()
    index = vi.load_index(pp)
    if index is None:
        return jsonify({"error": "Index not built yet. Call /vlm/index/build first."}), 404

    query_vec = vi.get_frame_vector(index, video_stem, frame)
    if not query_vec:
        # Frame not in index — compute on-the-fly
        frame_path = pp / "labeled-data" / secure_filename(video_stem) / secure_filename(frame)
        query_vec  = vi._pixel_vector(frame_path) or []

    results = vi.find_similar(
        index, query_vec, k=k,
        exclude_video_stem=video_stem,
        require_video_stem=reference_stem,
    )

    # Strip heavy vector data from response
    for r in results:
        r.pop("vector", None)

    return jsonify({"similar": results, "query_frame": frame})


@bp.route("/vlm/refine", methods=["POST"])
def vlm_refine():
    """
    POST /vlm/refine
    Body: {
      "active_video_stem": str,
      "active_frame":      str,
      "reference_video_stem": str,
      "reference_frame":   str,
      "reference_labels":  {bp: [x, y] or null},
      "bodyparts":         [str, ...]
    }
    Returns: {"vlm_coords": {bp: [x, y] or null}}
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

    # Security: both paths must stay inside the project
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

    vi = _import_indexer()
    vlm_coords, vlm_debug = vi.refine_coords_posture_aware(
        active_frame_path=active_path,
        reference_frame_path=ref_path,
        reference_labels=ref_labels,
        machine_coords=machine_coords,
        bodyparts=bodyparts,
    )

    # Persist so the result survives page reloads and frame re-selections
    stem_dir = pp / "labeled-data" / active_stem
    vi.save_vlm_result(stem_dir, active_frame, vlm_coords, vlm_debug)

    return jsonify({"vlm_coords": vlm_coords, "vlm_debug": vlm_debug})


@bp.route("/vlm/stem-vlm-frames")
def vlm_stem_vlm_frames():
    """
    GET /vlm/stem-vlm-frames?video_stem=<stem>

    Returns the list of frame names that have a saved VLM result for this stem,
    so the UI can show a badge next to each frame in the frame list.
    """
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400

    video_stem = request.args.get("video_stem", "")
    if not video_stem:
        return jsonify({"error": "video_stem is required."}), 400

    vi       = _import_indexer()
    stem_dir = pp / "labeled-data" / secure_filename(video_stem)
    frames   = vi.list_vlm_frames(stem_dir)
    return jsonify({"frames": frames})


@bp.route("/vlm/stem-likelihoods")
def vlm_stem_likelihoods():
    """
    GET /vlm/stem-likelihoods?video_stem=<stem>

    Returns {frame: min_likelihood} from _machine_predictions_raw.csv so the
    UI can populate the likelihood filter badges and per-frame lh values.
    """
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400

    video_stem = request.args.get("video_stem", "")
    if not video_stem:
        return jsonify({"error": "video_stem is required."}), 400

    vi       = _import_indexer()
    stem_dir = pp / "labeled-data" / secure_filename(video_stem)
    mins     = vi.frame_min_likelihoods(stem_dir)
    return jsonify({"likelihoods": mins})


@bp.route("/vlm/frame-data")
def vlm_frame_data():
    """
    GET /vlm/frame-data?video_stem=<stem>&frame=<filename>&min_lh=<float>

    Returns the frame's current labels (filtered by min_lh when raw predictions
    are available) plus the top-5 similar reference frames.
    """
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400

    video_stem     = request.args.get("video_stem", "")
    frame          = request.args.get("frame", "")
    reference_stem = request.args.get("reference_stem", "") or None
    try:
        min_lh = float(request.args.get("min_lh", 0.0))
    except ValueError:
        min_lh = 0.0
    if not video_stem or not frame:
        return jsonify({"error": "video_stem and frame are required."}), 400

    # Load current labels — prefer raw predictions CSV (supports live lh filter)
    from .labeling import _get_dlc_project_and_config
    project_data, cfg, label_err = _get_dlc_project_and_config()
    if label_err:
        cfg = {}
    scorer    = (cfg or {}).get("scorer", "User")
    bodyparts = (cfg or {}).get("bodyparts", [])

    vi       = _import_indexer()
    stem_dir = pp / "labeled-data" / secure_filename(video_stem)

    current_labels: dict = {}
    raw_labels = vi.read_raw_predictions(stem_dir, min_lh=min_lh)
    if raw_labels is not None:
        current_labels = raw_labels.get(frame, {})

    # Fallback to CollectedData CSV when raw predictions don't cover this frame.
    # This handles: (a) no raw predictions file at all, or (b) raw predictions
    # exist but this particular frame wasn't machine-labeled (e.g. manually
    # labeled frames added after machine labeling was run).
    if not current_labels:
        csv_path = stem_dir / f"CollectedData_{scorer}.csv"
        if not csv_path.is_file():
            candidates = sorted(stem_dir.glob("CollectedData_*.csv"))
            csv_path   = candidates[0] if candidates else None
        if csv_path and csv_path.is_file():
            all_labels = vi._read_labels_from_csv(csv_path)
            current_labels = all_labels.get(frame, {})

    has_raw = vi._ensure_raw_pred_csv(stem_dir)

    # Load saved VLM result for this frame (if any)
    saved_vlm_coords, saved_vlm_debug = vi.load_vlm_result(stem_dir, frame)

    # KNN — prefer posture index; fall back to pixel index
    similar: list[dict] = []
    index_stems: list[str] = []
    match_type = "none"

    posture_index = vi.load_posture_index(pp)
    pixel_index   = vi.load_index(pp)

    # Collect index_stems from whichever index exists
    if posture_index:
        index_stems = sorted({e["video_stem"] for e in posture_index.get("frames", []) if e.get("video_stem")})
    elif pixel_index:
        index_stems = vi.index_stems(pixel_index)

    # Posture KNN (skip when reference_stem filter is requested — pixel index handles that)
    if posture_index and not reference_stem:
        bp_list   = posture_index.get("bodyparts") or list(bodyparts)
        query_sig = vi.get_posture_signature_for_frame(posture_index, video_stem, frame)
        if not query_sig:
            query_sig = vi.posture_signature(current_labels, bp_list)
        if query_sig:
            similar = vi.find_similar_posture(
                posture_index, query_sig, k=5,
                exclude_video_stem=video_stem,
            )
            for r in similar:
                r.pop("signature", None)
            match_type = "posture"

    # Pixel KNN fallback (also used when reference_stem filter is active)
    if not similar and pixel_index:
        query_vec = vi.get_frame_vector(pixel_index, video_stem, frame)
        if not query_vec:
            frame_path = pp / "labeled-data" / secure_filename(video_stem) / secure_filename(frame)
            query_vec  = vi._pixel_vector(frame_path) or []
        similar = vi.find_similar(
            pixel_index, query_vec, k=5,
            exclude_video_stem=video_stem,
            require_video_stem=reference_stem,
        )
        for r in similar:
            r.pop("vector", None)
        match_type = "pixel"

    index_available = posture_index is not None or pixel_index is not None

    return jsonify({
        "video_stem":          video_stem,
        "frame":               frame,
        "current_labels":      current_labels,
        "has_raw_predictions": has_raw,
        "vlm_coords":          saved_vlm_coords,
        "vlm_debug":           saved_vlm_debug,
        "bodyparts":           bodyparts,
        "scorer":              scorer,
        "similar":             similar,
        "match_type":          match_type,
        "index_available":     index_available,
        "index_stems":         index_stems,
    })


@bp.route("/vlm/reference-image/<path:video_stem>/<filename>")
def vlm_serve_reference_image(video_stem: str, filename: str):
    """Serve a PNG from any labeled-data sub-folder (for reference panel)."""
    pp, err = _project_path_checked()
    if err:
        return jsonify({"error": err}), 400

    from flask import send_file
    safe_stem = secure_filename(video_stem)
    safe_file = secure_filename(filename)
    img_path  = (pp / "labeled-data" / safe_stem / safe_file).resolve()

    # Must stay within project root
    try:
        img_path.relative_to(pp.resolve())
    except ValueError:
        return jsonify({"error": "Access denied."}), 403

    if not img_path.is_file():
        return jsonify({"error": "Image not found."}), 404

    return send_file(str(img_path), mimetype="image/png")
