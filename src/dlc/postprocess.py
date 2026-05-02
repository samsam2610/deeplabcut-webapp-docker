"""Post-process predictions blueprint.

Exposes routes that run DeepLabCut's filterpredictions and a vendored
refineDLC toolkit on analyzed .h5/.csv files. See
docs/superpowers/specs/2026-05-01-postprocess-card-design.md.
"""
from __future__ import annotations

from flask import Blueprint, jsonify

bp = Blueprint("dlc_postprocess", __name__, url_prefix="/dlc/postprocess")


@bp.route("/recent", methods=["GET"])
def recent():
    """Return recent post-process runs for the active project (stub)."""
    return jsonify({"runs": []})
