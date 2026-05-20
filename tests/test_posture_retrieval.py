"""
Tests for Posture-Centric VLM Refiner — Component A (posture engine) and
Component B (routes + persistence).

Key scenarios:
  1. posture_signature produces geometrically correct normalized vectors.
  2. Cosine similarity correctly retrieves 'rearing' references for a 'rearing' query
     and NOT 'huddled' or 'walking' ones.
  3. VLM refine output is persisted to _posture_vlm_results.json.
  4. /posture/frame-data route returns posture-similar references.
  5. build_posture_index + load_posture_index round-trip.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Ensure src/ is importable ─────────────────────────────────────────────────
_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ─────────────────────────────────────────────────────────────────────────────
# Canonical posture definitions  (image coordinates: y increases downward)
# ─────────────────────────────────────────────────────────────────────────────
BODYPARTS = ["Snout", "Neck", "Back1", "Back2", "TailBase"]

# Rearing: animal standing upright — Snout high (low y), TailBase low (high y)
_REARING = {
    "Snout":    [50.0,  10.0],
    "Neck":     [50.0,  30.0],
    "Back1":    [50.0,  55.0],
    "Back2":    [50.0,  75.0],
    "TailBase": [50.0, 100.0],
}

# Huddled: all keypoints tightly clustered near the centre
_HUDDLED = {
    "Snout":    [100.0, 100.0],
    "Neck":     [102.0, 101.0],
    "Back1":    [104.0, 103.0],
    "Back2":    [103.0, 105.0],
    "TailBase": [101.0, 106.0],
}

# Walking: animal horizontal — all keypoints at similar y, spread along x
_WALKING = {
    "Snout":    [ 20.0, 50.0],
    "Neck":     [ 50.0, 50.0],
    "Back1":    [ 80.0, 52.0],
    "Back2":    [110.0, 51.0],
    "TailBase": [140.0, 50.0],
}

# A second rearing variant (slightly noisy) — should score close to _REARING
_REARING2 = {
    "Snout":    [52.0,  12.0],
    "Neck":     [49.0,  33.0],
    "Back1":    [51.0,  57.0],
    "Back2":    [50.0,  77.0],
    "TailBase": [48.0, 103.0],
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x / na * y / nb for x, y in zip(a, b))


def _minimal_png(width: int = 64, height: int = 64) -> bytes:
    """
    Return a valid grayscale PNG using PIL (always available in test env).
    64×64 ensures crop_patch can extract a 128×128 region (with padding).
    """
    try:
        from PIL import Image as _I
        import io
        img = _I.new("RGB", (width, height), color=(128, 100, 80))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except ImportError:
        import base64
        # Known-good 1×1 white PNG fallback
        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVQI12NgAAAAAgAB4iG8MwAAAABJRU5ErkJggg=="
        )


def _write_csv(path: Path, scorer: str, frames: list[str],
               bodyparts: list[str], coords: dict) -> None:
    """Write a minimal DLC MultiIndex CSV."""
    import csv as _csv
    rows = [
        ["scorer",    "", ""] + [scorer] * (len(bodyparts) * 2),
        ["bodyparts", "", ""] + [bp for bp in bodyparts for _ in range(2)],
        ["coords",    "", ""] + ["x", "y"] * len(bodyparts),
    ]
    for frame in frames:
        row = ["labeled-data", path.parent.name, frame]
        for bp in bodyparts:
            xy = coords.get(frame, {}).get(bp)
            row += [str(xy[0]), str(xy[1])] if xy else ["", ""]
        rows.append(row)
    with open(str(path), "w", newline="") as fh:
        _csv.writer(fh).writerows(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def posture_project(tmp_path):
    """
    Minimal DLC project with four labeled stems inside tmp_path/user-data/
    (so the security check passes when USER_DATA_DIR = tmp_path/user-data).

      stem_rearing1   — 2 rearing frames
      stem_rearing2   — 1 rearing (noisy) frame
      stem_huddled    — 2 huddled frames
      stem_walking    — 2 walking frames
    """
    png = _minimal_png()

    stems = {
        "stem_rearing1": [("img001.png", _REARING),  ("img002.png", _REARING)],
        "stem_rearing2": [("img001.png", _REARING2)],
        "stem_huddled":  [("img001.png", _HUDDLED),  ("img002.png", _HUDDLED)],
        "stem_walking":  [("img001.png", _WALKING),  ("img002.png", _WALKING)],
    }

    # Place inside user-data/ so _sec_check passes in route tests
    user_data = tmp_path / "user-data"
    user_data.mkdir(exist_ok=True)
    proj = user_data / "posture-test-project"
    proj.mkdir()

    labeled_base = proj / "labeled-data"
    labeled_base.mkdir()
    (proj / "config.yaml").write_text(
        "project_path: /fake\nscorer: TestScorer\n"
        f"bodyparts:\n" + "".join(f"- {bp}\n" for bp in BODYPARTS)
    )

    for stem_name, frame_list in stems.items():
        stem_dir = labeled_base / stem_name
        stem_dir.mkdir()
        frame_names = [f for f, _ in frame_list]
        coords_by_frame = {f: c for f, c in frame_list}
        for fname, _ in frame_list:
            (stem_dir / fname).write_bytes(png)
        _write_csv(stem_dir / "CollectedData_TestScorer.csv",
                   "TestScorer", frame_names, BODYPARTS, coords_by_frame)

    return proj


# ─────────────────────────────────────────────────────────────────────────────
# 1. Posture signature geometry
# ─────────────────────────────────────────────────────────────────────────────

class TestPostureSignature:
    def _sig(self, coords):
        from dlc.vlm_indexer import posture_signature
        return posture_signature(coords, BODYPARTS)

    def test_rearing_is_vertical(self):
        """Rearing signature: x-variance ≈ 0, y-variance dominates."""
        sig = self._sig(_REARING)
        assert len(sig) == 2 * len(BODYPARTS)
        # All x components should be ~0 (animal is centred vertically)
        x_vals = [sig[2 * i] for i in range(len(BODYPARTS))]
        y_vals = [sig[2 * i + 1] for i in range(len(BODYPARTS))]
        assert max(abs(x) for x in x_vals) < 0.05, "Rearing should have near-zero x spread"
        # y spread should be large (whole range is normalized to ≈ 1)
        assert (max(y_vals) - min(y_vals)) > 0.8

    def test_walking_is_horizontal(self):
        """Walking signature: y-variance ≈ 0, x-variance dominates."""
        sig = self._sig(_WALKING)
        y_vals = [sig[2 * i + 1] for i in range(len(BODYPARTS))]
        x_vals = [sig[2 * i]     for i in range(len(BODYPARTS))]
        assert max(abs(y) for y in y_vals) < 0.1,  "Walking should have near-zero y spread"
        assert (max(x_vals) - min(x_vals)) > 0.8

    def test_max_distance_normalized_to_one(self):
        """After normalization the maximum pairwise distance should be ≈ 1.0."""
        sig = self._sig(_REARING)
        n = len(BODYPARTS)
        coords = [(sig[2 * i], sig[2 * i + 1]) for i in range(n)]
        max_dist = max(
            math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)
            for j, a in enumerate(coords)
            for b in coords[j + 1:]
        )
        assert abs(max_dist - 1.0) < 1e-9

    def test_huddled_has_small_range(self):
        """Huddled signature should have very small spread after normalization."""
        from dlc.vlm_indexer import posture_signature
        # Huddled coords are tight; the normalized sig is well-defined but
        # all components should be small (< 0.6 in absolute value)
        sig = posture_signature(_HUDDLED, BODYPARTS)
        assert all(abs(v) <= 0.6 for v in sig)

    def test_empty_labels_returns_empty(self):
        from dlc.vlm_indexer import posture_signature
        assert posture_signature({}, BODYPARTS) == []

    def test_single_valid_point_returns_empty(self):
        from dlc.vlm_indexer import posture_signature
        labels = {"Snout": [10.0, 20.0]}
        assert posture_signature(labels, BODYPARTS) == []

    def test_centroid_is_zero(self):
        """Mean of valid x-components and y-components should both be ≈ 0."""
        from dlc.vlm_indexer import posture_signature
        sig = posture_signature(_WALKING, BODYPARTS)
        n = len(BODYPARTS)
        # All 5 bodyparts are valid in _WALKING
        mx = sum(sig[2 * i]     for i in range(n)) / n
        my = sum(sig[2 * i + 1] for i in range(n)) / n
        assert abs(mx) < 1e-9
        assert abs(my) < 1e-9


# ─────────────────────────────────────────────────────────────────────────────
# 2. Posture retrieval — rearing query retrieves rearing references
# ─────────────────────────────────────────────────────────────────────────────

class TestPostureRetrieval:

    def _build(self, project_path):
        from dlc.vlm_indexer import build_posture_index
        return build_posture_index(project_path, bodyparts=BODYPARTS)

    def test_rearing_query_retrieves_rearing_references(self, posture_project):
        """
        A 'rearing' query should rank rearing library frames above huddled/walking.
        """
        from dlc.vlm_indexer import posture_signature, find_similar_posture

        index = self._build(posture_project)
        query_sig = posture_signature(_REARING, BODYPARTS)

        # Exclude stem_rearing1 (the one the query conceptually comes from)
        results = find_similar_posture(index, query_sig, k=5, exclude_video_stem="stem_rearing1")

        assert results, "Expected at least one result"
        top = results[0]
        assert top["video_stem"] in ("stem_rearing2",), \
            f"Top result should be a rearing stem, got {top['video_stem']}"
        assert top["score"] > 0.95, \
            f"Rearing-vs-rearing cosine should be near 1.0, got {top['score']}"

    def test_walking_query_not_top_for_rearing(self, posture_project):
        """A walking query should score much lower against rearing frames."""
        from dlc.vlm_indexer import posture_signature, find_similar_posture

        index = self._build(posture_project)
        rearing_sig = posture_signature(_REARING,  BODYPARTS)
        walking_sig = posture_signature(_WALKING,  BODYPARTS)

        rearing_score = _cosine(rearing_sig, rearing_sig)    # self-similarity = 1
        cross_score   = _cosine(rearing_sig, walking_sig)    # should be much lower
        assert cross_score < rearing_score - 0.3, \
            f"Rearing vs walking cosine ({cross_score:.3f}) should be well below self ({rearing_score:.3f})"

    def test_huddled_query_not_top_for_rearing(self, posture_project):
        from dlc.vlm_indexer import posture_signature

        r_sig = posture_signature(_REARING, BODYPARTS)
        h_sig = posture_signature(_HUDDLED, BODYPARTS)
        w_sig = posture_signature(_WALKING, BODYPARTS)

        r_vs_r = _cosine(r_sig, r_sig)
        r_vs_h = _cosine(r_sig, h_sig)
        r_vs_w = _cosine(r_sig, w_sig)

        assert r_vs_r > r_vs_h, "Rearing-vs-rearing should beat rearing-vs-huddled"
        assert r_vs_r > r_vs_w, "Rearing-vs-rearing should beat rearing-vs-walking"

    def test_index_contains_all_frames(self, posture_project):
        index = self._build(posture_project)
        assert index["total_frames"] == 7   # 2+1+2+2

    def test_index_round_trip(self, posture_project):
        from dlc.vlm_indexer import load_posture_index
        self._build(posture_project)
        loaded = load_posture_index(posture_project)
        assert loaded is not None
        assert loaded["total_frames"] == 7

    def test_frame_not_in_index_returns_empty_sig(self, posture_project):
        from dlc.vlm_indexer import get_posture_signature_for_frame
        index = self._build(posture_project)
        sig = get_posture_signature_for_frame(index, "stem_rearing1", "nonexistent.png")
        assert sig == []

    def test_known_frame_sig_in_index(self, posture_project):
        from dlc.vlm_indexer import get_posture_signature_for_frame
        index = self._build(posture_project)
        sig = get_posture_signature_for_frame(index, "stem_rearing1", "img001.png")
        assert len(sig) == 2 * len(BODYPARTS)


# ─────────────────────────────────────────────────────────────────────────────
# 3. VLM output persistence
# ─────────────────────────────────────────────────────────────────────────────

class TestPosturePersistence:

    def test_save_and_load_posture_result(self, tmp_path):
        from dlc.vlm_indexer import save_posture_result, load_posture_result

        stem_dir  = tmp_path / "stem_A"
        stem_dir.mkdir()
        frame     = "img001.png"
        coords    = {"Snout": [55.0, 25.0], "TailBase": [55.0, 90.0]}
        debug     = {"Snout": {"reason": "ok", "dx": 2.0, "dy": -1.0}}

        save_posture_result(stem_dir, frame, coords, debug)

        loaded_coords, loaded_debug = load_posture_result(stem_dir, frame)
        assert loaded_coords == coords
        assert loaded_debug["Snout"]["reason"] == "ok"

    def test_save_upserts(self, tmp_path):
        from dlc.vlm_indexer import save_posture_result, load_posture_result

        stem_dir = tmp_path / "stem_B"
        stem_dir.mkdir()
        save_posture_result(stem_dir, "img001.png", {"Snout": [10.0, 10.0]}, {})
        save_posture_result(stem_dir, "img001.png", {"Snout": [20.0, 20.0]}, {})

        coords, _ = load_posture_result(stem_dir, "img001.png")
        assert coords["Snout"] == [20.0, 20.0], "Second save should overwrite first"

    def test_missing_frame_returns_none(self, tmp_path):
        from dlc.vlm_indexer import load_posture_result
        coords, debug = load_posture_result(tmp_path, "nonexistent.png")
        assert coords is None
        assert debug  is None

    def test_results_file_uses_separate_filename(self, tmp_path):
        """_posture_vlm_results.json must not clobber _vlm_results.json."""
        from dlc.vlm_indexer import save_posture_result, POSTURE_RESULTS_FILENAME, VLM_RESULTS_FILENAME

        stem_dir = tmp_path / "stem_C"
        stem_dir.mkdir()
        save_posture_result(stem_dir, "img001.png", {}, {})

        assert (stem_dir / POSTURE_RESULTS_FILENAME).is_file()
        assert not (stem_dir / VLM_RESULTS_FILENAME).is_file(), \
            "_posture_vlm_results.json must be a separate file from _vlm_results.json"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Posture-aware VLM refine (mocked Ollama)
# ─────────────────────────────────────────────────────────────────────────────

class TestPostureAwareRefine:

    def _make_frame(self, tmp_path: Path) -> Path:
        """Write a minimal 64×64 PNG (using the known-good 8×8 PNG bytes)."""
        p = tmp_path / "frame.png"
        p.write_bytes(_minimal_png())
        return p

    def _mock_ollama_response(self, bodyparts: list[str]) -> str:
        return json.dumps({bp: {"correct": True, "dx": 0, "dy": 0} for bp in bodyparts})

    def test_refine_with_mocked_ollama_returns_coords(self, tmp_path):
        from dlc.vlm_indexer import refine_coords_posture_aware

        frame    = self._make_frame(tmp_path)
        bps      = ["Snout", "TailBase"]
        machine  = {"Snout": [32.0, 32.0], "TailBase": [32.0, 32.0]}
        ref_lbls = {"Snout": [32.0, 32.0], "TailBase": [32.0, 32.0]}

        mock_resp = self._mock_ollama_response(bps)
        with patch("dlc.vlm_indexer._ollama_chat",
                   return_value=(mock_resp, "")) as mock_chat:
            coords, debug = refine_coords_posture_aware(
                active_frame_path=frame,
                reference_frame_path=frame,
                reference_labels=ref_lbls,
                machine_coords=machine,
                bodyparts=bps,
            )

        assert mock_chat.called
        for bp in bps:
            assert coords[bp] is not None
            assert debug[bp]["reason"] == "ok"

    def test_refine_uses_anatomical_prompt(self, tmp_path):
        """The posture-aware prompt should reference 'anatomical' context."""
        from dlc.vlm_indexer import refine_coords_posture_aware

        frame    = self._make_frame(tmp_path)
        machine  = {"Snout": [32.0, 32.0]}
        ref_lbls = {"Snout": [32.0, 32.0]}
        mock_resp = json.dumps({"Snout": {"correct": True, "dx": 0, "dy": 0}})

        captured_prompts = []

        def _capture(messages, model, timeout=120, fmt=None):
            captured_prompts.append(messages[0]["content"])
            return (mock_resp, "")

        with patch("dlc.vlm_indexer._ollama_chat", side_effect=_capture):
            refine_coords_posture_aware(
                active_frame_path=frame,
                reference_frame_path=frame,
                reference_labels=ref_lbls,
                machine_coords=machine,
                bodyparts=["Snout"],
            )

        assert captured_prompts, "Expected at least one Ollama call"
        prompt_text = captured_prompts[0].lower()
        assert "anatomical" in prompt_text, \
            "Posture-aware prompt should mention 'anatomical'"
        assert "reference" in prompt_text, \
            "Posture-aware prompt should mention 'reference'"

    def test_ollama_failure_falls_back_to_machine_coords(self, tmp_path):
        from dlc.vlm_indexer import refine_coords_posture_aware

        frame    = self._make_frame(tmp_path)
        machine  = {"Snout": [40.0, 50.0]}
        ref_lbls = {"Snout": [40.0, 50.0]}

        with patch("dlc.vlm_indexer._ollama_chat", return_value=(None, "timeout")):
            coords, debug = refine_coords_posture_aware(
                active_frame_path=frame,
                reference_frame_path=frame,
                reference_labels=ref_lbls,
                machine_coords=machine,
                bodyparts=["Snout"],
            )

        assert coords["Snout"] == [40.0, 50.0], "Should fall back to machine coords on Ollama failure"
        assert debug["Snout"]["reason"] == "ollama_failed"

    def test_save_roundtrip_after_refine(self, tmp_path):
        from dlc.vlm_indexer import refine_coords_posture_aware, save_posture_result, load_posture_result

        frame    = self._make_frame(tmp_path)
        machine  = {"Snout": [32.0, 32.0], "Neck": [32.0, 32.0]}
        ref_lbls = {"Snout": [32.0, 32.0], "Neck": [32.0, 32.0]}
        bps      = ["Snout", "Neck"]
        mock_resp = json.dumps({"Snout": {"correct": True, "dx": 3, "dy": -2},
                                "Neck":  {"correct": False, "dx": 1, "dy": 1}})

        with patch("dlc.vlm_indexer._ollama_chat", return_value=(mock_resp, "")):
            coords, debug = refine_coords_posture_aware(
                active_frame_path=frame,
                reference_frame_path=frame,
                reference_labels=ref_lbls,
                machine_coords=machine,
                bodyparts=bps,
            )

        save_posture_result(tmp_path, "frame.png", coords, debug)
        loaded_coords, loaded_debug = load_posture_result(tmp_path, "frame.png")

        assert loaded_coords["Snout"][0] == pytest.approx(35.0)   # 32 + dx=3
        assert loaded_coords["Snout"][1] == pytest.approx(30.0)   # 32 + dy=-2
        assert loaded_debug["Snout"]["reason"] == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Flask route — /posture/frame-data
# ─────────────────────────────────────────────────────────────────────────────

def _get_uid(client) -> str:
    """Extract or create a session uid from the test client."""
    with client.session_transaction() as sess:
        if "uid" not in sess:
            sess["uid"] = "test-posture-uid"
        return sess["uid"]


@pytest.fixture()
def flask_posture_client(fake_redis, tmp_path):
    """
    Minimal Flask test client with posture blueprint, auth disabled.
    Matches the pattern used by flask_test_client_vlm in test_vlm_verification.py.

    USER_DATA_DIR = tmp_path/user-data, so posture_project (which lives in
    tmp_path/user-data/posture-test-project) passes the security check.
    """
    data_dir      = tmp_path / "data"
    user_data_dir = tmp_path / "user-data"
    data_dir.mkdir(exist_ok=True)
    user_data_dir.mkdir(exist_ok=True)

    env_vars = {
        "DATA_DIR":          str(data_dir),
        "USER_DATA_DIR":     str(user_data_dir),
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "FLASK_SECRET_KEY":  "test-secret-key-32-chars-minimum!",
        "AUTH_DISABLED":     "true",
    }

    with patch.dict(os.environ, env_vars):
        with patch("redis.Redis.from_url", return_value=fake_redis):
            import app as flask_app_module
            flask_app_module.DATA_DIR      = data_dir
            flask_app_module.USER_DATA_DIR = user_data_dir
            flask_app_module._redis_client = fake_redis

            flask_app_module.app.config["TESTING"]          = True
            flask_app_module.app.config["SECRET_KEY"]       = "test-secret"
            flask_app_module.app.config["WTF_CSRF_ENABLED"] = False

            with flask_app_module.app.test_client() as client:
                yield client, flask_app_module, fake_redis, data_dir, user_data_dir


class TestPostureFrameDataRoute:

    def _register_project(self, client, fake_redis, project_path):
        """Set the active DLC project in fake Redis for a test client session."""
        uid = _get_uid(client)
        project_key = f"webapp:dlc_project:{uid}"
        fake_redis.set(project_key, json.dumps({
            "project_path": str(project_path),
            "config_path":  str(project_path / "config.yaml"),
            "engine":       "pytorch",
        }))

    def test_frame_data_returns_similar(self, flask_posture_client, posture_project):
        """
        /posture/frame-data should return posture-similar references when a
        posture index is present.
        """
        client, app_module, fake_redis, data_dir, user_data_dir = flask_posture_client
        self._register_project(client, fake_redis, posture_project)

        from dlc.vlm_indexer import build_posture_index
        build_posture_index(posture_project, bodyparts=BODYPARTS)

        resp = client.get(
            "/posture/frame-data?video_stem=stem_rearing1&frame=img001.png"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "similar" in data
        assert "current_labels" in data
        assert data["index_available"] is True

    def test_frame_data_no_index(self, flask_posture_client, posture_project):
        """Without a posture index, similar should be empty but request succeeds."""
        client, app_module, fake_redis, data_dir, user_data_dir = flask_posture_client
        self._register_project(client, fake_redis, posture_project)

        resp = client.get(
            "/posture/frame-data?video_stem=stem_walking&frame=img001.png"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["similar"] == []
        assert data["index_available"] is False

    def test_frame_data_missing_params(self, flask_posture_client, posture_project):
        client, app_module, fake_redis, data_dir, user_data_dir = flask_posture_client
        self._register_project(client, fake_redis, posture_project)

        resp = client.get("/posture/frame-data?video_stem=stem_walking")
        assert resp.status_code == 400
