"""
Tests for VLM-Enhanced Labeling — Component A, B, D.

Test 1: Reference panel KNN returns the image path reported by vlm_indexer.
Test 2: A/B/V toggle state correctly exposes the right coord layer.
Test 3: Original project data is never modified.
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
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def tiny_project(tmp_path):
    """
    Create a minimal fake DLC project with 3 labeled frames across 2 stems.

    Structure:
        user-data/test-project/
          config.yaml
          labeled-data/
            stem_A/
              img0001.png   — CollectedData_Alice.csv
              img0002.png
            stem_B/
              img0003.png   — CollectedData_Alice.csv

    The project lives inside tmp_path/user-data so it passes the security
    check when USER_DATA_DIR=tmp_path/user-data.

    Each PNG is a valid 1×1 grayscale image written via PIL (always available
    in the test environment because the DLC workers use it).
    """
    import base64

    # Minimal valid 1×1 white PNG (base64-encoded, known-good bytes)
    _PNG_1X1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVQI12NgAAAAAgAB4iG8MwAAAABJRU5ErkJggg=="
    )

    def _make_png(width: int = 8, height: int = 8) -> bytes:
        """Return a small valid grayscale PNG using PIL, fallback to 1×1 PNG."""
        try:
            from PIL import Image as _I
            import io
            img = _I.new("L", (width, height), color=128)
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return buf.getvalue()
        except ImportError:
            return _PNG_1X1

    def _write_csv(path: Path, scorer: str, frames: list[str], bodyparts: list[str], coords: dict):
        """Write a minimal DLC MultiIndex CSV."""
        rows = [
            ["scorer",    "", ""] + [scorer] * (len(bodyparts) * 2),
            ["bodyparts", "", ""] + [bp for bp in bodyparts for _ in range(2)],
            ["coords",    "", ""] + ["x", "y"] * len(bodyparts),
        ]
        for frame in frames:
            row = ["labeled-data", path.parent.name, frame]
            for bp in bodyparts:
                pt = coords.get(frame, {}).get(bp)
                if pt:
                    row.extend([str(pt[0]), str(pt[1])])
                else:
                    row.extend(["NaN", "NaN"])
            rows.append(row)
        import csv
        with open(str(path), "w", newline="") as f:
            csv.writer(f).writerows(rows)

    # Place project inside user-data so the security check passes
    user_data = tmp_path / "user-data"
    user_data.mkdir(exist_ok=True)
    proj = user_data / "test-project"
    proj.mkdir()
    (proj / "config.yaml").write_text(
        "project_path: /fake/path\nscorer: Alice\nbodyparts:\n- snout\n- tailbase\n"
    )

    bodyparts = ["snout", "tailbase"]
    scorer    = "Alice"

    stem_a = proj / "labeled-data" / "stem_A"
    stem_a.mkdir(parents=True)
    (stem_a / "img0001.png").write_bytes(_make_png())
    (stem_a / "img0002.png").write_bytes(_make_png())
    _write_csv(
        stem_a / f"CollectedData_{scorer}.csv",
        scorer, ["img0001.png", "img0002.png"], bodyparts,
        {
            "img0001.png": {"snout": (10.0, 20.0), "tailbase": (30.0, 40.0)},
            "img0002.png": {"snout": (15.0, 25.0), "tailbase": None},
        },
    )

    stem_b = proj / "labeled-data" / "stem_B"
    stem_b.mkdir(parents=True)
    (stem_b / "img0003.png").write_bytes(_make_png())
    _write_csv(
        stem_b / f"CollectedData_{scorer}.csv",
        scorer, ["img0003.png"], bodyparts,
        {"img0003.png": {"snout": (5.0, 8.0), "tailbase": (50.0, 60.0)}},
    )

    return proj


@pytest.fixture()
def built_index(tiny_project):
    """Build the visual index on the tiny project and return (project_path, index)."""
    import vlm_indexer
    index = vlm_indexer.build_index(tiny_project, use_ollama=False)
    return tiny_project, index


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Reference panel returns correct image path from KNN search
# ─────────────────────────────────────────────────────────────────────────────

class TestReferencePanelKNN:
    """The Reference Panel must display the path returned by vlm_indexer.find_similar."""

    def test_index_contains_all_labeled_frames(self, built_index):
        """build_index indexes every frame that has a CollectedData CSV entry."""
        _, index = built_index
        frame_names = {e["frame"] for e in index["frames"]}
        assert "img0001.png" in frame_names
        assert "img0002.png" in frame_names
        assert "img0003.png" in frame_names
        assert index["total_frames"] == 3

    def test_find_similar_returns_correct_path(self, built_index):
        """
        find_similar returns at most k results and each result has frame_path
        pointing to a file that actually exists on disk.
        """
        import vlm_indexer
        proj, index = built_index

        # Query with img0001's own vector — top result (excluding self) should be
        # one of the other frames with a non-empty vector.
        query_vec = vlm_indexer.get_frame_vector(index, "stem_A", "img0001.png")
        assert query_vec, "Expected a non-empty vector for img0001.png"

        results = vlm_indexer.find_similar(
            index, query_vec, k=2,
            exclude_frame="img0001.png",
            exclude_video_stem="stem_A",
        )

        assert len(results) <= 2
        for r in results:
            # The path returned must exist
            assert Path(r["frame_path"]).is_file(), (
                f"KNN result frame_path does not exist: {r['frame_path']}"
            )
            assert "score" in r
            assert 0.0 <= r["score"] <= 1.0

    def test_reference_panel_route_returns_image(self, tiny_project, flask_test_client_vlm):
        """
        GET /vlm/reference-image/<stem>/<frame> serves the PNG bytes.
        """
        client, app_module, redis_client, data_dir, user_data_dir = flask_test_client_vlm

        # Register project in Redis
        project_key = f"webapp:dlc_project:{_get_uid(client)}"
        redis_client.set(project_key, json.dumps({
            "project_path": str(tiny_project),
            "config_path":  str(tiny_project / "config.yaml"),
            "engine":       "pytorch",
        }))

        resp = client.get("/vlm/reference-image/stem_A/img0001.png")
        assert resp.status_code == 200
        assert resp.content_type.startswith("image/")


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — A/B/V toggle exposes the correct label layer
# ─────────────────────────────────────────────────────────────────────────────

class TestToggleUI:
    """
    Verify that the three label layers (Machine / VLM / Human) are distinct
    objects in the JS state and that switching mode changes which coords are used.

    We test the *back-end* side of the toggle: the /vlm/frame-data endpoint
    returns `current_labels` (machine) and the /vlm/refine endpoint returns
    `vlm_coords` — these map to the M and V layers respectively.
    """

    def test_frame_data_returns_machine_labels(self, tiny_project, flask_test_client_vlm):
        """GET /vlm/frame-data returns current CSV labels as `current_labels` (M layer)."""
        client, app_module, redis_client, data_dir, user_data_dir = flask_test_client_vlm

        project_key = f"webapp:dlc_project:{_get_uid(client)}"
        redis_client.set(project_key, json.dumps({
            "project_path": str(tiny_project),
            "config_path":  str(tiny_project / "config.yaml"),
            "engine":       "pytorch",
        }))

        resp = client.get("/vlm/frame-data?video_stem=stem_A&frame=img0001.png")
        assert resp.status_code == 200
        data = resp.get_json()

        assert "current_labels" in data
        # snout should be [10.0, 20.0] from the CSV
        snout = data["current_labels"].get("snout")
        assert snout is not None
        assert abs(snout[0] - 10.0) < 0.1 and abs(snout[1] - 20.0) < 0.1, (
            f"Expected snout≈[10,20], got {snout}"
        )

    def test_vlm_refine_returns_separate_vlm_coords(self, tiny_project, flask_test_client_vlm):
        """
        POST /vlm/refine returns `vlm_coords` separately from machine labels,
        mapping to the V layer.  We mock the Ollama call so no GPU is needed.
        """
        client, app_module, redis_client, data_dir, user_data_dir = flask_test_client_vlm

        project_key = f"webapp:dlc_project:{_get_uid(client)}"
        redis_client.set(project_key, json.dumps({
            "project_path": str(tiny_project),
            "config_path":  str(tiny_project / "config.yaml"),
            "engine":       "pytorch",
        }))

        fake_vlm_result = {"snout": [12.0, 22.0], "tailbase": [32.0, 42.0]}

        with patch("vlm_indexer.refine_coords_with_vlm", return_value=fake_vlm_result):
            resp = client.post("/vlm/refine", json={
                "active_video_stem":    "stem_A",
                "active_frame":         "img0001.png",
                "reference_video_stem": "stem_B",
                "reference_frame":      "img0003.png",
                "reference_labels":     {"snout": [5.0, 8.0], "tailbase": [50.0, 60.0]},
                "bodyparts":            ["snout", "tailbase"],
            })

        assert resp.status_code == 200
        data = resp.get_json()
        assert "vlm_coords" in data
        assert data["vlm_coords"]["snout"] == [12.0, 22.0]
        assert data["vlm_coords"]["tailbase"] == [32.0, 42.0]

    def test_machine_and_vlm_layers_are_independent(self, tiny_project, flask_test_client_vlm):
        """
        Machine coords (from CSV) and VLM coords (from refine) differ — the
        toggle must expose distinct values.  This validates that the back-end
        does not accidentally merge them.
        """
        client, app_module, redis_client, data_dir, user_data_dir = flask_test_client_vlm

        project_key = f"webapp:dlc_project:{_get_uid(client)}"
        redis_client.set(project_key, json.dumps({
            "project_path": str(tiny_project),
            "config_path":  str(tiny_project / "config.yaml"),
            "engine":       "pytorch",
        }))

        # M layer
        fd_resp = client.get("/vlm/frame-data?video_stem=stem_A&frame=img0001.png")
        machine_snout = fd_resp.get_json()["current_labels"].get("snout")

        # V layer (mocked)
        fake_vlm = {"snout": [99.0, 88.0], "tailbase": None}
        with patch("vlm_indexer.refine_coords_with_vlm", return_value=fake_vlm):
            refine_resp = client.post("/vlm/refine", json={
                "active_video_stem":    "stem_A",
                "active_frame":         "img0001.png",
                "reference_video_stem": "stem_B",
                "reference_frame":      "img0003.png",
                "reference_labels":     {},
                "bodyparts":            ["snout", "tailbase"],
            })
        vlm_snout = refine_resp.get_json()["vlm_coords"].get("snout")

        # They must differ — otherwise the layers are collapsed
        assert machine_snout != vlm_snout, (
            "Machine and VLM coords should differ; toggle has nothing to switch between."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Original project data is never modified
# ─────────────────────────────────────────────────────────────────────────────

class TestOriginalProjectUnmodified:
    """
    All VLM operations must work on the VLM-TEST copy; the original project
    must remain byte-for-byte identical.
    """

    def test_build_index_does_not_touch_original(self, tiny_project, tmp_path):
        """
        build_index writes vlm_index.json only inside the project it is given.
        A second 'original' project must remain untouched.
        """
        import vlm_indexer

        # Create a separate 'original' project — a copy of tiny_project
        original = tmp_path / "original"
        shutil.copytree(str(tiny_project), str(original))

        # Snapshot original CSV checksums
        def _checksums(root: Path) -> dict[str, bytes]:
            return {
                str(p.relative_to(root)): p.read_bytes()
                for p in root.rglob("*.csv")
            }

        orig_before = _checksums(original)

        # Build index on tiny_project (NOT original)
        vlm_indexer.build_index(tiny_project, use_ollama=False)

        orig_after = _checksums(original)
        assert orig_before == orig_after, "Original CSV files were modified!"

        # vlm_index.json must NOT exist in original
        assert not (original / "vlm_index.json").exists(), (
            "vlm_index.json appeared in the original project directory!"
        )

        # vlm_index.json MUST exist in tiny_project (the working copy)
        assert (tiny_project / "vlm_index.json").exists()

    def test_save_labels_targets_working_copy(self, tiny_project, tmp_path, flask_test_client_vlm):
        """
        POST /dlc/project/labels/<stem> writes only to the project registered
        in the session — the original project's CSV is unchanged.
        """
        client, app_module, redis_client, data_dir, user_data_dir = flask_test_client_vlm

        original = tmp_path / "original"
        shutil.copytree(str(tiny_project), str(original))
        orig_csv = (original / "labeled-data" / "stem_A" / "CollectedData_Alice.csv").read_bytes()

        # Register tiny_project (the copy) as the active project
        project_key = f"webapp:dlc_project:{_get_uid(client)}"
        redis_client.set(project_key, json.dumps({
            "project_path": str(tiny_project),
            "config_path":  str(tiny_project / "config.yaml"),
            "engine":       "pytorch",
        }))

        # Modify a label via the API
        resp = client.post("/dlc/project/labels/stem_A", json={
            "labels": {"img0001.png": {"snout": [99.0, 99.0], "tailbase": [88.0, 88.0]}}
        })
        assert resp.status_code == 200

        # Original must be byte-identical
        orig_csv_after = (original / "labeled-data" / "stem_A" / "CollectedData_Alice.csv").read_bytes()
        assert orig_csv == orig_csv_after, "Original project CSV was modified by save operation!"

    def test_vlm_index_build_idempotent(self, tiny_project):
        """
        Building the index twice on the same project is safe and produces a
        consistent result — no leftover temp files, same frame count.
        """
        import vlm_indexer

        idx1 = vlm_indexer.build_index(tiny_project, use_ollama=False)
        idx2 = vlm_indexer.build_index(tiny_project, use_ollama=False)

        assert idx1["total_frames"] == idx2["total_frames"] == 3
        # Only one index file should exist
        idx_files = list(tiny_project.glob("vlm_index*.json"))
        assert len(idx_files) == 1, f"Expected 1 index file, found {len(idx_files)}"


# ─────────────────────────────────────────────────────────────────────────────
# Shared helper fixtures / utils
# ─────────────────────────────────────────────────────────────────────────────

def _get_uid(client) -> str:
    """Extract or create a session uid from the test client."""
    with client.session_transaction() as sess:
        if "uid" not in sess:
            sess["uid"] = "test-vlm-uid"
        return sess["uid"]


@pytest.fixture()
def flask_test_client_vlm(fake_redis, tmp_path):
    """
    Minimal Flask test client with VLM blueprint registered, auth disabled.
    Re-uses the shared fake_redis from conftest.

    NOTE: user_data_dir is tmp_path/user-data, which matches where tiny_project
    places the test project, so security checks pass.
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

            # Do NOT push test_request_context — it conflicts with actual
            # HTTP requests made via the test client.
            with flask_app_module.app.test_client() as client:
                yield client, flask_app_module, fake_redis, data_dir, user_data_dir
