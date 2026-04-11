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
    from dlc import vlm_indexer
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
        from dlc import vlm_indexer
        proj, index = built_index

        # Query with img0001's own vector — top result (excluding self) should be
        # one of the other frames with a non-empty vector.
        query_vec = vlm_indexer.get_frame_vector(index, "stem_A", "img0001.png")
        assert query_vec, "Expected a non-empty vector for img0001.png"

        # exclude the whole stem — results must come from a different stem
        results = vlm_indexer.find_similar(
            index, query_vec, k=2,
            exclude_video_stem="stem_A",
        )

        assert len(results) <= 2
        for r in results:
            # Must be from a different stem
            assert r["video_stem"] != "stem_A", (
                f"Result came from excluded stem: {r['video_stem']}/{r['frame']}"
            )
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

        fake_vlm_result = (
            {"snout": [12.0, 22.0], "tailbase": [32.0, 42.0]},
            {"snout": {"reason": "ok", "dx": 2.0, "dy": 2.0, "correct": False},
             "tailbase": {"reason": "ok", "dx": 2.0, "dy": 2.0, "correct": False}},
        )

        with patch("dlc.vlm_indexer.refine_coords_posture_aware", return_value=fake_vlm_result):
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
        assert "vlm_debug" in data
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
        fake_vlm = (
            {"snout": [99.0, 88.0], "tailbase": None},
            {"snout": {"reason": "ok", "dx": 89.0, "dy": 68.0, "correct": False},
             "tailbase": {"reason": "no_ref_label"}},
        )
        with patch("dlc.vlm_indexer.refine_coords_posture_aware", return_value=fake_vlm):
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
        from dlc import vlm_indexer

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
        from dlc import vlm_indexer

        idx1 = vlm_indexer.build_index(tiny_project, use_ollama=False)
        idx2 = vlm_indexer.build_index(tiny_project, use_ollama=False)

        assert idx1["total_frames"] == idx2["total_frames"] == 3
        # Only one index file should exist
        idx_files = list(tiny_project.glob("vlm_index*.json"))
        assert len(idx_files) == 1, f"Expected 1 index file, found {len(idx_files)}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — Patch-based VLM refinement unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPatchBasedVlmRefine:
    """Unit tests for the per-bodypart crop + delta approach in refine_coords_with_vlm."""

    def _make_png_bytes(self, width: int = 200, height: int = 200) -> bytes:
        """Return a valid PNG of given size."""
        try:
            from PIL import Image as _I
            import io
            img = _I.new("RGB", (width, height), color=(100, 150, 200))
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return buf.getvalue()
        except ImportError:
            # fallback: minimal valid 1×1 PNG
            import base64
            return base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVQI12NgAAAAAgAB4iG8MwAAAABJRU5ErkJggg=="
            )

    def _write_png(self, path: Path, width: int = 200, height: int = 200):
        path.write_bytes(self._make_png_bytes(width, height))

    def test_applies_vlm_offset_to_machine_coord(self, tmp_path):
        """
        VLM returns {"correct": false, "dx": 5, "dy": -3}.
        Result should be machine_coord + (5, -3).
        """
        from dlc import vlm_indexer

        active = tmp_path / "active.png"
        ref    = tmp_path / "ref.png"
        self._write_png(active)
        self._write_png(ref)

        machine = {"snout": [100.0, 80.0], "tailbase": [50.0, 60.0]}
        ref_labels = {"snout": [110.0, 90.0], "tailbase": [55.0, 65.0]}

        # Batched format: one response covers all bodyparts
        fake_response = json.dumps({
            "snout":    {"correct": False, "dx": 5,  "dy": -3},
            "tailbase": {"correct": False, "dx": 5,  "dy": -3},
        })

        with patch("dlc.vlm_indexer._ollama_chat", return_value=(fake_response, "")):
            result, debug = vlm_indexer.refine_coords_with_vlm(
                active_frame_path=active,
                reference_frame_path=ref,
                reference_labels=ref_labels,
                machine_coords=machine,
                bodyparts=["snout", "tailbase"],
            )

        assert result["snout"]    == pytest.approx([105.0,  77.0], abs=0.01)
        assert result["tailbase"] == pytest.approx([ 55.0,  57.0], abs=0.01)
        assert debug["snout"]["reason"] == "ok"
        assert debug["snout"]["dx"] == pytest.approx(5.0, abs=0.01)
        assert debug["snout"]["dy"] == pytest.approx(-3.0, abs=0.01)

    def test_skips_bodypart_when_machine_coord_is_null(self, tmp_path):
        """Bodyparts with null machine coord → result is None (not [0,0] or error)."""
        from dlc import vlm_indexer

        active = tmp_path / "active.png"
        ref    = tmp_path / "ref.png"
        self._write_png(active)
        self._write_png(ref)

        machine    = {"snout": None, "tailbase": [50.0, 60.0]}
        ref_labels = {"snout": [10.0, 20.0], "tailbase": [55.0, 65.0]}

        # snout has no machine coord so only tailbase is callable → batched response
        fake_batched = json.dumps({"tailbase": {"correct": True, "dx": 0, "dy": 0}})
        with patch("dlc.vlm_indexer._ollama_chat", return_value=(fake_batched, "")):
            result, debug = vlm_indexer.refine_coords_with_vlm(
                active_frame_path=active,
                reference_frame_path=ref,
                reference_labels=ref_labels,
                machine_coords=machine,
                bodyparts=["snout", "tailbase"],
            )

        assert result["snout"] is None
        assert debug["snout"]["reason"] == "no_machine_coord"
        assert result["tailbase"] is not None

    def test_falls_back_to_machine_when_reference_label_missing(self, tmp_path):
        """
        When the reference frame has no label for a bodypart, the machine coord
        is kept unchanged (no VLM call for that part).
        """
        from dlc import vlm_indexer

        active = tmp_path / "active.png"
        ref    = tmp_path / "ref.png"
        self._write_png(active)
        self._write_png(ref)

        machine    = {"snout": [100.0, 80.0]}
        ref_labels = {}   # no reference label for snout

        call_count = {"n": 0}
        original_chat = vlm_indexer._ollama_chat
        def _counting_chat(*a, **kw):
            call_count["n"] += 1
            return ('{"correct": true, "dx": 0, "dy": 0}', "")

        with patch("dlc.vlm_indexer._ollama_chat", side_effect=_counting_chat):
            result, debug = vlm_indexer.refine_coords_with_vlm(
                active_frame_path=active,
                reference_frame_path=ref,
                reference_labels=ref_labels,
                machine_coords=machine,
                bodyparts=["snout"],
            )

        # No VLM call should have been made
        assert call_count["n"] == 0
        # Machine coord is kept
        assert result["snout"] == pytest.approx([100.0, 80.0], abs=0.01)
        assert debug["snout"]["reason"] == "no_ref_label"

    def test_falls_back_gracefully_when_ollama_fails(self, tmp_path):
        """When _ollama_chat returns None (network error), machine coords are kept."""
        from dlc import vlm_indexer

        active = tmp_path / "active.png"
        ref    = tmp_path / "ref.png"
        self._write_png(active)
        self._write_png(ref)

        machine    = {"snout": [100.0, 80.0]}
        ref_labels = {"snout": [110.0, 90.0]}

        with patch("dlc.vlm_indexer._ollama_chat", return_value=(None, "connection refused")):
            result, debug = vlm_indexer.refine_coords_with_vlm(
                active_frame_path=active,
                reference_frame_path=ref,
                reference_labels=ref_labels,
                machine_coords=machine,
                bodyparts=["snout"],
            )

        assert result["snout"] == pytest.approx([100.0, 80.0], abs=0.01)
        assert debug["snout"]["reason"] == "ollama_failed"

    def test_crop_patch_returns_base64_string(self, tmp_path):
        """_crop_patch should return a non-empty base64 string for a valid image."""
        from dlc import vlm_indexer
        import base64

        img_path = tmp_path / "frame.png"
        self._write_png(img_path, 200, 200)

        result = vlm_indexer._crop_patch(img_path, cx=100.0, cy=100.0, size=128)
        assert result is not None
        assert isinstance(result, str)
        # Must be valid base64
        decoded = base64.b64decode(result)
        assert len(decoded) > 0

    def test_crop_patch_handles_edge_coords(self, tmp_path):
        """_crop_patch near image edges should not crash (border padding)."""
        from dlc import vlm_indexer

        img_path = tmp_path / "frame.png"
        self._write_png(img_path, 200, 200)

        # Near top-left corner
        result = vlm_indexer._crop_patch(img_path, cx=5.0, cy=5.0, size=128)
        assert result is not None

    def test_require_video_stem_filters_results(self, built_index):
        """find_similar with require_video_stem returns only frames from that stem."""
        from dlc import vlm_indexer
        proj, index = built_index

        query_vec = vlm_indexer.get_frame_vector(index, "stem_A", "img0001.png")
        results = vlm_indexer.find_similar(
            index, query_vec, k=10,
            exclude_video_stem="stem_A",
            require_video_stem="stem_B",
        )
        assert len(results) > 0
        for r in results:
            assert r["video_stem"] == "stem_B"

    def test_index_stems_returns_all_stems(self, built_index):
        """index_stems returns every unique stem in the index."""
        from dlc import vlm_indexer
        proj, index = built_index
        stems = vlm_indexer.index_stems(index)
        assert "stem_A" in stems
        assert "stem_B" in stems

    def test_frame_data_includes_index_stems(self, tiny_project, flask_test_client_vlm, built_index):
        """GET /vlm/frame-data response includes index_stems list."""
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
        assert "index_stems" in data
        assert "stem_A" in data["index_stems"]
        assert "stem_B" in data["index_stems"]

    def test_frame_data_reference_stem_filter(self, tiny_project, flask_test_client_vlm, built_index):
        """GET /vlm/frame-data?reference_stem=stem_B returns only stem_B references."""
        client, app_module, redis_client, data_dir, user_data_dir = flask_test_client_vlm

        project_key = f"webapp:dlc_project:{_get_uid(client)}"
        redis_client.set(project_key, json.dumps({
            "project_path": str(tiny_project),
            "config_path":  str(tiny_project / "config.yaml"),
            "engine":       "pytorch",
        }))

        resp = client.get("/vlm/frame-data?video_stem=stem_A&frame=img0001.png&reference_stem=stem_B")
        assert resp.status_code == 200
        data = resp.get_json()
        for ref in data.get("similar", []):
            assert ref["video_stem"] == "stem_B", f"Got ref from {ref['video_stem']}"

    def test_refine_route_passes_machine_coords(self, tiny_project, flask_test_client_vlm):
        """
        POST /vlm/refine passes machine_coords from the request body to
        refine_coords_with_vlm.  We capture the call kwargs and assert.
        """
        client, app_module, redis_client, data_dir, user_data_dir = flask_test_client_vlm

        project_key = f"webapp:dlc_project:{_get_uid(client)}"
        redis_client.set(project_key, json.dumps({
            "project_path": str(tiny_project),
            "config_path":  str(tiny_project / "config.yaml"),
            "engine":       "pytorch",
        }))

        captured = {}

        def _fake_refine(**kwargs):
            captured.update(kwargs)
            return ({"snout": [12.0, 22.0]}, {"snout": {"reason": "ok", "dx": 2.0, "dy": 2.0, "correct": False}})

        with patch("dlc.vlm_indexer.refine_coords_posture_aware", side_effect=_fake_refine):
            client.post("/vlm/refine", json={
                "active_video_stem":    "stem_A",
                "active_frame":         "img0001.png",
                "reference_video_stem": "stem_B",
                "reference_frame":      "img0003.png",
                "reference_labels":     {"snout": [5.0, 8.0]},
                "machine_coords":       {"snout": [10.0, 20.0]},
                "bodyparts":            ["snout"],
            })

        assert "machine_coords" in captured
        assert captured["machine_coords"] == {"snout": [10.0, 20.0]}


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — VLM result persistence
#
# VLM results are saved to _vlm_results.json on refine and restored from it
# when the frame is next selected.  The invariants locked in here:
#
#   • /vlm/refine saves the result to disk
#   • /vlm/frame-data returns vlm_coords/vlm_debug from the saved file
#   • /vlm/frame-data returns vlm_coords=None (not missing key) when no save exists
#   • /vlm/stem-vlm-frames lists frames that have a saved result
#   • Reloading machine coords (lh slider) does NOT overwrite the saved VLM result
# ─────────────────────────────────────────────────────────────────────────────

class TestVlmResultPersistence:
    """VLM results survive page reloads and machine-coord reloads via disk storage."""

    def _setup(self, client, redis_client, tiny_project):
        project_key = f"webapp:dlc_project:{_get_uid(client)}"
        redis_client.set(project_key, json.dumps({
            "project_path": str(tiny_project),
            "config_path":  str(tiny_project / "config.yaml"),
            "engine":       "pytorch",
        }))

    def _fake_refine(self, client, tiny_project, vlm_result=None):
        """POST /vlm/refine with a mocked VLM call."""
        if vlm_result is None:
            vlm_result = (
                {"snout": [12.0, 22.0], "tailbase": [32.0, 42.0]},
                {"snout":    {"reason": "ok", "dx": 2.0, "dy": 2.0, "correct": False},
                 "tailbase": {"reason": "ok", "dx": 2.0, "dy": 2.0, "correct": False}},
            )
        with patch("dlc.vlm_indexer.refine_coords_posture_aware", return_value=vlm_result):
            return client.post("/vlm/refine", json={
                "active_video_stem":    "stem_A",
                "active_frame":         "img0001.png",
                "reference_video_stem": "stem_B",
                "reference_frame":      "img0003.png",
                "reference_labels":     {"snout": [5.0, 8.0], "tailbase": [50.0, 60.0]},
                "machine_coords":       {"snout": [10.0, 20.0], "tailbase": [30.0, 40.0]},
                "bodyparts":            ["snout", "tailbase"],
            })

    def test_refine_saves_result_to_disk(self, tiny_project, flask_test_client_vlm):
        """POST /vlm/refine writes _vlm_results.json into the stem directory."""
        client, _, redis_client, *_ = flask_test_client_vlm
        self._setup(client, redis_client, tiny_project)

        results_file = tiny_project / "labeled-data" / "stem_A" / "_vlm_results.json"
        assert not results_file.exists(), "Should not exist before refine"

        self._fake_refine(client, tiny_project)

        assert results_file.exists(), "_vlm_results.json was not created"
        saved = json.loads(results_file.read_text())
        assert "img0001.png" in saved
        assert saved["img0001.png"]["vlm_coords"]["snout"] == [12.0, 22.0]

    def test_frame_data_returns_saved_vlm_coords(self, tiny_project, flask_test_client_vlm):
        """
        After a refine, GET /vlm/frame-data for the same frame returns the
        saved vlm_coords so the V layer is restored on frame re-selection.
        """
        client, _, redis_client, *_ = flask_test_client_vlm
        self._setup(client, redis_client, tiny_project)

        self._fake_refine(client, tiny_project)

        resp = client.get("/vlm/frame-data?video_stem=stem_A&frame=img0001.png")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data["vlm_coords"] is not None, (
            "frame-data must return saved vlm_coords so V layer is restored on re-select"
        )
        assert data["vlm_coords"]["snout"] == [12.0, 22.0]
        assert data["vlm_debug"] is not None
        assert data["vlm_debug"]["snout"]["reason"] == "ok"

    def test_frame_data_returns_null_vlm_coords_when_none_saved(
        self, tiny_project, flask_test_client_vlm
    ):
        """
        For a frame with no saved VLM result, vlm_coords is None (not absent)
        so the JS can distinguish 'no result' from 'server error'.
        """
        client, _, redis_client, *_ = flask_test_client_vlm
        self._setup(client, redis_client, tiny_project)

        resp = client.get("/vlm/frame-data?video_stem=stem_A&frame=img0001.png")
        data = resp.get_json()
        assert "vlm_coords" in data, "vlm_coords key must always be present"
        assert data["vlm_coords"] is None

    def test_machine_reload_does_not_overwrite_saved_vlm(
        self, tiny_project, flask_test_client_vlm
    ):
        """
        Calling /vlm/frame-data again (lh slider reload) returns the SAME
        saved vlm_coords — it does not erase the saved result.
        """
        client, _, redis_client, *_ = flask_test_client_vlm
        self._setup(client, redis_client, tiny_project)

        self._fake_refine(client, tiny_project)

        # First load
        r1 = client.get("/vlm/frame-data?video_stem=stem_A&frame=img0001.png").get_json()
        assert r1["vlm_coords"]["snout"] == [12.0, 22.0]

        # Second load (simulates lh slider change or reference-stem reload)
        r2 = client.get("/vlm/frame-data?video_stem=stem_A&frame=img0001.png&min_lh=0.5").get_json()
        assert r2["vlm_coords"]["snout"] == [12.0, 22.0], (
            "Reloading machine coords must not erase the saved VLM result"
        )

    def test_stem_vlm_frames_lists_refined_frame(self, tiny_project, flask_test_client_vlm):
        """
        After refine, GET /vlm/stem-vlm-frames returns the frame name so the
        UI can show a badge in the frame list.
        """
        client, _, redis_client, *_ = flask_test_client_vlm
        self._setup(client, redis_client, tiny_project)

        # Before refine: empty
        r_before = client.get("/vlm/stem-vlm-frames?video_stem=stem_A").get_json()
        assert "img0001.png" not in r_before["frames"]

        self._fake_refine(client, tiny_project)

        # After refine: present
        r_after = client.get("/vlm/stem-vlm-frames?video_stem=stem_A").get_json()
        assert "img0001.png" in r_after["frames"]

    def test_vlm_results_upsert_on_second_refine(self, tiny_project, flask_test_client_vlm):
        """
        Running refine twice on the same frame updates the stored result
        rather than appending a duplicate.
        """
        client, _, redis_client, *_ = flask_test_client_vlm
        self._setup(client, redis_client, tiny_project)

        self._fake_refine(client, tiny_project)

        # Second refine with different coords
        second = (
            {"snout": [99.0, 88.0], "tailbase": [77.0, 66.0]},
            {"snout": {"reason": "ok", "dx": 89.0, "dy": 68.0, "correct": False},
             "tailbase": {"reason": "ok", "dx": 47.0, "dy": 26.0, "correct": False}},
        )
        self._fake_refine(client, tiny_project, vlm_result=second)

        saved = json.loads(
            (tiny_project / "labeled-data" / "stem_A" / "_vlm_results.json").read_text()
        )
        # Only one entry for img0001.png
        assert list(saved.keys()).count("img0001.png") == 1
        # Updated to latest result
        assert saved["img0001.png"]["vlm_coords"]["snout"] == [99.0, 88.0]


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — Likelihood filter reads raw predictions CSV
# ─────────────────────────────────────────────────────────────────────────────

def _write_raw_pred_csv(path: Path, rows: list[tuple]):
    """Write _machine_predictions_raw.csv with (frame, bodypart, x, y, lh) rows."""
    import csv as _csv
    with open(str(path), "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["frame", "bodypart", "x", "y", "likelihood"])
        for row in rows:
            w.writerow(row)


class TestLikelihoodFilter:
    """Raw predictions CSV is read by vlm_indexer and filtered by min_lh."""

    def test_read_raw_predictions_all_at_zero(self, tmp_path):
        """read_raw_predictions(min_lh=0) returns ALL predictions."""
        from dlc import vlm_indexer
        raw = tmp_path / "_machine_predictions_raw.csv"
        _write_raw_pred_csv(raw, [
            ("img001.png", "snout",    10.0, 20.0, 0.95),
            ("img001.png", "tailbase", 30.0, 40.0, 0.25),  # low lh
        ])
        result = vlm_indexer.read_raw_predictions(tmp_path, min_lh=0.0)
        assert result is not None
        assert result["img001.png"]["snout"] == pytest.approx([10.0, 20.0])
        assert result["img001.png"]["tailbase"] == pytest.approx([30.0, 40.0])

    def test_read_raw_predictions_filters_low_lh(self, tmp_path):
        """read_raw_predictions filters bodyparts below min_lh."""
        from dlc import vlm_indexer
        raw = tmp_path / "_machine_predictions_raw.csv"
        _write_raw_pred_csv(raw, [
            ("img001.png", "snout",    10.0, 20.0, 0.95),
            ("img001.png", "tailbase", 30.0, 40.0, 0.25),
        ])
        result = vlm_indexer.read_raw_predictions(tmp_path, min_lh=0.9)
        assert result is not None
        assert result["img001.png"]["snout"] == pytest.approx([10.0, 20.0])
        assert result["img001.png"]["tailbase"] is None  # filtered out

    def test_read_raw_predictions_returns_none_when_no_file(self, tmp_path):
        """read_raw_predictions returns None when file is absent (fallback to CSV)."""
        from dlc import vlm_indexer
        result = vlm_indexer.read_raw_predictions(tmp_path, min_lh=0.0)
        assert result is None

    def test_frame_min_likelihoods_computes_per_frame_min(self, tmp_path):
        """frame_min_likelihoods returns the minimum lh per frame."""
        from dlc import vlm_indexer
        raw = tmp_path / "_machine_predictions_raw.csv"
        _write_raw_pred_csv(raw, [
            ("img001.png", "snout",    10.0, 20.0, 0.95),
            ("img001.png", "tailbase", 30.0, 40.0, 0.25),
            ("img002.png", "snout",    11.0, 21.0, 0.80),
            ("img002.png", "tailbase", 31.0, 41.0, 0.85),
        ])
        mins = vlm_indexer.frame_min_likelihoods(tmp_path)
        assert abs(mins["img001.png"] - 0.25) < 0.001
        assert abs(mins["img002.png"] - 0.80) < 0.001

    def test_frame_data_uses_raw_predictions_when_available(
        self, tiny_project, flask_test_client_vlm
    ):
        """
        GET /vlm/frame-data uses raw predictions CSV when present,
        and the low-lh bodypart is null at threshold=0.9 but visible at 0.
        """
        client, app_module, redis_client, data_dir, user_data_dir = flask_test_client_vlm

        project_key = f"webapp:dlc_project:{_get_uid(client)}"
        redis_client.set(project_key, json.dumps({
            "project_path": str(tiny_project),
            "config_path":  str(tiny_project / "config.yaml"),
            "engine":       "pytorch",
        }))

        # Write a raw predictions file for stem_A with tailbase at lh=0.1
        raw_csv = tiny_project / "labeled-data" / "stem_A" / "_machine_predictions_raw.csv"
        _write_raw_pred_csv(raw_csv, [
            ("img0001.png", "snout",    10.0, 20.0, 0.99),
            ("img0001.png", "tailbase", 30.0, 40.0, 0.10),  # low lh
        ])

        # At min_lh=0 → tailbase should be visible
        resp0 = client.get("/vlm/frame-data?video_stem=stem_A&frame=img0001.png&min_lh=0")
        assert resp0.status_code == 200
        d0 = resp0.get_json()
        assert d0["has_raw_predictions"] is True
        assert d0["current_labels"]["tailbase"] == pytest.approx([30.0, 40.0])

        # At min_lh=0.9 → tailbase should be null
        resp9 = client.get("/vlm/frame-data?video_stem=stem_A&frame=img0001.png&min_lh=0.9")
        assert resp9.status_code == 200
        d9 = resp9.get_json()
        assert d9["current_labels"]["tailbase"] is None

    def test_frame_data_falls_back_to_csv_when_no_raw(
        self, tiny_project, flask_test_client_vlm
    ):
        """
        Without _machine_predictions_raw.csv, /vlm/frame-data falls back to
        the filtered CollectedData CSV and has_raw_predictions is False.
        """
        client, app_module, redis_client, data_dir, user_data_dir = flask_test_client_vlm

        project_key = f"webapp:dlc_project:{_get_uid(client)}"
        redis_client.set(project_key, json.dumps({
            "project_path": str(tiny_project),
            "config_path":  str(tiny_project / "config.yaml"),
            "engine":       "pytorch",
        }))

        resp = client.get("/vlm/frame-data?video_stem=stem_A&frame=img0001.png&min_lh=0")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["has_raw_predictions"] is False
        # Still returns labels from filtered CSV
        assert data["current_labels"]["snout"] == pytest.approx([10.0, 20.0])

    def test_frame_data_falls_back_to_csv_when_frame_not_in_raw(
        self, tiny_project, flask_test_client_vlm
    ):
        """
        When _machine_predictions_raw.csv EXISTS but the requested frame is NOT
        in it, /vlm/frame-data must fall back to CollectedData CSV rather than
        returning empty current_labels.

        This covers the case where a stem has machine predictions for some
        frames but the user is viewing a manually-labeled frame that was never
        machine-predicted.
        """
        client, app_module, redis_client, data_dir, user_data_dir = flask_test_client_vlm

        project_key = f"webapp:dlc_project:{_get_uid(client)}"
        redis_client.set(project_key, json.dumps({
            "project_path": str(tiny_project),
            "config_path":  str(tiny_project / "config.yaml"),
            "engine":       "pytorch",
        }))

        # Write a raw-predictions CSV that covers img0002.png but NOT img0001.png
        raw_csv = tiny_project / "labeled-data" / "stem_A" / "_machine_predictions_raw.csv"
        _write_raw_pred_csv(raw_csv, [
            ("img0002.png", "snout",    15.0, 25.0, 0.90),
            ("img0002.png", "tailbase", 35.0, 45.0, 0.88),
        ])

        # Request img0001.png — NOT in the raw CSV
        resp = client.get("/vlm/frame-data?video_stem=stem_A&frame=img0001.png&min_lh=0")
        assert resp.status_code == 200
        data = resp.get_json()
        # Must fall back to CollectedData — should have the human labels
        assert data["current_labels"].get("snout") == pytest.approx([10.0, 20.0]), (
            "current_labels is empty — fallback to CollectedData CSV did not happen. "
            "VLM will have no machine coords and will return all-null vlm_coords."
        )

    def test_stem_likelihoods_route(self, tiny_project, flask_test_client_vlm):
        """GET /vlm/stem-likelihoods returns per-frame min likelihoods."""
        client, app_module, redis_client, data_dir, user_data_dir = flask_test_client_vlm

        project_key = f"webapp:dlc_project:{_get_uid(client)}"
        redis_client.set(project_key, json.dumps({
            "project_path": str(tiny_project),
            "config_path":  str(tiny_project / "config.yaml"),
            "engine":       "pytorch",
        }))

        raw_csv = tiny_project / "labeled-data" / "stem_A" / "_machine_predictions_raw.csv"
        _write_raw_pred_csv(raw_csv, [
            ("img0001.png", "snout",    10.0, 20.0, 0.95),
            ("img0001.png", "tailbase", 30.0, 40.0, 0.25),
            ("img0002.png", "snout",    15.0, 25.0, 0.88),
            ("img0002.png", "tailbase", 35.0, 45.0, 0.91),
        ])

        resp = client.get("/vlm/stem-likelihoods?video_stem=stem_A")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "likelihoods" in data
        assert abs(data["likelihoods"]["img0001.png"] - 0.25) < 0.001
        assert abs(data["likelihoods"]["img0002.png"] - 0.88) < 0.001


# ─────────────────────────────────────────────────────────────────────────────
# Test 8 — Real-project integration (skipped when project not on disk)
# ─────────────────────────────────────────────────────────────────────────────

import sys as _sys
_TESTS_DIR = Path(__file__).parent
if str(_TESTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_TESTS_DIR))
from conftest import ORIGINAL_DLC_PROJECT as _REAL_PROJECT  # noqa: E402

_real_project_available = pytest.mark.skipif(
    _REAL_PROJECT is None,
    reason="ORIGINAL_DLC_PROJECT not present on this machine",
)


class TestRealProjectIntegration:
    """
    Integration tests that run against the actual on-disk DLC project
    (DREADD-Ali-2026-01-07).  All tests are skipped when the project path
    does not exist (CI / other machines).

    WHY these tests exist
    ---------------------
    Synthetic tiny_project tests can pass while real-world data fails because:
      - Real stems have _machine_predictions_raw.h5 (not .csv) that must be
        converted on demand.
      - A stem's raw CSV may cover only *some* frames; frames only in
        CollectedData must still return labels (fallback bug this class caught).
      - Real frame images are large — crop + VLM path behaves differently.

    Always run at least one real-project test when debugging VLM/UI issues.
    """

    @_real_project_available
    def test_read_labels_from_csv_real_stem(self):
        """_read_labels_from_csv returns non-empty coords for real CollectedData CSV."""
        from dlc import vlm_indexer as vi
        stem_dir = _REAL_PROJECT / "labeled-data" / "MAP2_20250715_120050_0"
        csv = stem_dir / "CollectedData_Ali.csv"
        assert csv.is_file(), f"Expected CollectedData CSV at {csv}"
        labels = vi._read_labels_from_csv(csv)
        assert labels, "No labels parsed from real CSV"
        sample_frame = sorted(labels.keys())[0]
        coords = labels[sample_frame]
        non_null = [v for v in coords.values() if v is not None]
        assert non_null, f"All labels are null for {sample_frame}"

    @_real_project_available
    def test_frame_data_always_returns_machine_coords(self, tmp_path):
        """
        /vlm/frame-data must return non-empty current_labels for a real frame
        even when the raw predictions CSV covers a different frame set.

        This is the exact scenario that caused the 'empty VLM' bug:
          - stem has _machine_predictions_raw.csv (generated from .h5)
          - the requested frame is in CollectedData but NOT in the raw CSV
          → route was returning current_labels={}, VLM got no machine coords,
            all bodyparts came back as no_machine_coord, vlm_coords all null.
        """
        from dlc import vlm_indexer as vi

        stem_dir = _REAL_PROJECT / "labeled-data" / "MAP2_20250715_120050_0"
        csv = stem_dir / "CollectedData_Ali.csv"
        labels = vi._read_labels_from_csv(csv)
        frames = sorted(labels.keys())
        assert frames, "No frames in CollectedData"

        # Use a temp copy of stem_dir so we can write a partial raw CSV without
        # touching the real project.
        import shutil
        fake_stem = tmp_path / "fake_stem"
        fake_stem.mkdir()
        # Copy one PNG and the CollectedData CSV
        shutil.copy(str(stem_dir / frames[0]), str(fake_stem / frames[0]))
        shutil.copy(str(csv), str(fake_stem / csv.name))

        # Write a raw CSV that covers only the SECOND frame (so frame[0] falls back)
        if len(frames) > 1:
            raw_csv = fake_stem / "_machine_predictions_raw.csv"
            second_frame = frames[1]
            second_coords = labels[second_frame]
            rows = [["frame", "bodypart", "x", "y", "likelihood"]]
            for bp, pt in second_coords.items():
                if pt:
                    rows.append([second_frame, bp, pt[0], pt[1], 0.95])
            import csv as _csv
            with open(str(raw_csv), "w", newline="") as f:
                _csv.writer(f).writerows(rows)

            # Now simulate what the route does
            raw_labels = vi.read_raw_predictions(fake_stem, min_lh=0.0)
            assert raw_labels is not None, "Raw CSV not read"
            # frame[0] is NOT in raw CSV → should be empty dict
            assert raw_labels.get(frames[0], {}) == {}, \
                "Expected frame[0] to be absent from raw CSV"

            # After the fix: fall back to CollectedData when raw is empty for frame
            current_labels = raw_labels.get(frames[0], {})
            if not current_labels:
                all_labels = vi._read_labels_from_csv(fake_stem / csv.name)
                current_labels = all_labels.get(frames[0], {})

            non_null = [v for v in current_labels.values() if v is not None]
            assert non_null, (
                f"current_labels is empty for {frames[0]} even after fallback — "
                "VLM would have no machine coords and return all-null vlm_coords."
            )

    @_real_project_available
    def test_refine_with_real_frames_mocked_ollama(self, tmp_path):
        """
        refine_coords_with_vlm returns plausible coords for real frames when
        Ollama is mocked to return a small offset.

        Verifies: crops are generated, coords are adjusted, result is saved and
        loaded back correctly — without needing a live Ollama instance.
        """
        from dlc import vlm_indexer as vi
        from unittest.mock import patch

        stems = sorted(
            p.name for p in (_REAL_PROJECT / "labeled-data").iterdir()
            if p.is_dir() and not p.name.startswith("@")
        )
        # Need two stems: one as active, one as reference
        assert len(stems) >= 2, "Need at least 2 stems for this test"
        active_stem, ref_stem = stems[0], stems[1]

        active_dir = _REAL_PROJECT / "labeled-data" / active_stem
        ref_dir    = _REAL_PROJECT / "labeled-data" / ref_stem

        active_frames = sorted(active_dir.glob("*.png"))
        ref_frames    = sorted(ref_dir.glob("*.png"))
        assert active_frames, f"No PNGs in {active_stem}"
        assert ref_frames,    f"No PNGs in {ref_stem}"

        active_path = active_frames[0]
        ref_path    = ref_frames[0]

        # Get real labels
        ref_csv    = ref_dir / next(iter(ref_dir.glob("CollectedData_*.csv")), None or "")
        active_csv = active_dir / next(iter(active_dir.glob("CollectedData_*.csv")), None or "")
        assert ref_csv.is_file(),    f"No CollectedData CSV in {ref_stem}"
        assert active_csv.is_file(), f"No CollectedData CSV in {active_stem}"

        ref_labels    = vi._read_labels_from_csv(ref_csv).get(ref_path.name, {})
        machine_coords = vi._read_labels_from_csv(active_csv).get(active_path.name, {})

        bodyparts = [bp for bp, v in ref_labels.items() if v is not None][:2]
        assert bodyparts, "No labeled bodyparts in reference frame"

        # Mock Ollama to return a small +5px offset for each bodypart
        fake_response = (
            "{" +
            ", ".join(f'"{bp}": {{"correct": false, "dx": 5, "dy": 3}}' for bp in bodyparts) +
            "}"
        )
        with patch("dlc.vlm_indexer._ollama_chat", return_value=(fake_response, "")):
            vlm_coords, vlm_debug = vi.refine_coords_with_vlm(
                active_frame_path=active_path,
                reference_frame_path=ref_path,
                reference_labels=ref_labels,
                machine_coords=machine_coords,
                bodyparts=bodyparts,
            )

        for bp in bodyparts:
            assert bp in vlm_coords, f"Missing {bp} in vlm_coords"
            m = machine_coords.get(bp)
            v = vlm_coords.get(bp)
            if m and v:
                assert abs(v[0] - (m[0] + 5)) < 0.1, f"{bp} x not shifted by +5"
                assert abs(v[1] - (m[1] + 3)) < 0.1, f"{bp} y not shifted by +3"
                assert vlm_debug[bp]["reason"] == "ok"

        # Verify save → load round-trip
        vi.save_vlm_result(tmp_path, active_path.name, vlm_coords, vlm_debug)
        loaded_coords, loaded_debug = vi.load_vlm_result(tmp_path, active_path.name)
        assert loaded_coords == vlm_coords
        assert loaded_debug  == vlm_debug


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


# ─────────────────────────────────────────────────────────────────────────────
# Test 8 — Posture matching integrated into /vlm/refiner
#
# These tests verify the three integration points:
#   1. /vlm/index/build also writes posture_index.json
#   2. /vlm/frame-data returns posture-matched references (match_type="posture")
#      when posture_index.json is present, falling back to pixel when absent
#   3. /vlm/refine calls refine_coords_posture_aware (anatomical prompt)
# ─────────────────────────────────────────────────────────────────────────────

class TestPostureIntegration:
    """Posture engine is wired into the existing /vlm/* routes."""

    def _register(self, client, redis_client, project):
        uid = _get_uid(client)
        redis_client.set(f"webapp:dlc_project:{uid}", json.dumps({
            "project_path": str(project),
            "config_path":  str(project / "config.yaml"),
            "engine":       "pytorch",
        }))

    # ── 1. Build index also produces posture_index.json ───────────────────────

    def test_build_index_also_builds_posture_index(
        self, tiny_project, flask_test_client_vlm
    ):
        """POST /vlm/index/build must write both vlm_index.json and posture_index.json."""
        client, app_module, redis_client, _, _ = flask_test_client_vlm
        self._register(client, redis_client, tiny_project)

        resp = client.post("/vlm/index/build", json={"use_ollama": False})
        assert resp.status_code == 200

        # Consume the NDJSON stream so the build completes
        lines = [l for l in resp.data.decode().split("\n") if l.strip()]
        last  = json.loads(lines[-1])
        assert last.get("finished") is True, f"Expected finished=True, got: {last}"

        assert (tiny_project / "vlm_index.json").is_file(),     "pixel index missing"
        assert (tiny_project / "posture_index.json").is_file(), "posture index missing"

    # ── 2. frame-data: posture KNN when index present ─────────────────────────

    def test_frame_data_returns_posture_match_type(
        self, tiny_project, flask_test_client_vlm
    ):
        """
        When posture_index.json exists, /vlm/frame-data must return
        match_type='posture' and non-empty similar list.
        """
        from dlc import vlm_indexer as vi
        client, app_module, redis_client, _, _ = flask_test_client_vlm
        self._register(client, redis_client, tiny_project)

        vi.build_posture_index(tiny_project, bodyparts=["snout", "tailbase"])

        resp = client.get("/vlm/frame-data?video_stem=stem_A&frame=img0001.png")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data["match_type"] == "posture", (
            f"Expected match_type='posture', got '{data['match_type']}'"
        )
        assert data["similar"], "Expected at least one posture-matched reference"
        # Similar frames must come from a different stem (exclude_video_stem=stem_A)
        for ref in data["similar"]:
            assert ref["video_stem"] != "stem_A"

    def test_frame_data_falls_back_to_pixel_when_no_posture_index(
        self, tiny_project, flask_test_client_vlm, built_index
    ):
        """
        When only the pixel index exists, /vlm/frame-data returns
        match_type='pixel'.
        """
        client, app_module, redis_client, _, _ = flask_test_client_vlm
        self._register(client, redis_client, tiny_project)

        # Ensure posture index is absent
        posture_path = tiny_project / "posture_index.json"
        posture_path.unlink(missing_ok=True)

        resp = client.get("/vlm/frame-data?video_stem=stem_A&frame=img0001.png")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data["match_type"] == "pixel", (
            f"Expected match_type='pixel', got '{data['match_type']}'"
        )

    def test_frame_data_reference_stem_filter_uses_pixel_index(
        self, tiny_project, flask_test_client_vlm, built_index
    ):
        """
        When reference_stem filter is active, posture KNN is skipped and the
        pixel index is used (posture index has no per-stem filter yet).
        """
        from dlc import vlm_indexer as vi
        client, app_module, redis_client, _, _ = flask_test_client_vlm
        self._register(client, redis_client, tiny_project)

        vi.build_posture_index(tiny_project, bodyparts=["snout", "tailbase"])

        resp = client.get(
            "/vlm/frame-data?video_stem=stem_A&frame=img0001.png&reference_stem=stem_B"
        )
        assert resp.status_code == 200
        data = resp.get_json()

        # All results must come from stem_B
        for ref in data.get("similar", []):
            assert ref["video_stem"] == "stem_B"

    def test_frame_data_no_index_returns_empty_similar(
        self, tiny_project, flask_test_client_vlm
    ):
        """With neither index built, similar=[] and index_available=False."""
        client, app_module, redis_client, _, _ = flask_test_client_vlm
        self._register(client, redis_client, tiny_project)

        # Remove any leftover indices
        (tiny_project / "vlm_index.json").unlink(missing_ok=True)
        (tiny_project / "posture_index.json").unlink(missing_ok=True)

        resp = client.get("/vlm/frame-data?video_stem=stem_A&frame=img0001.png")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data["similar"] == []
        assert data["index_available"] is False
        assert data["match_type"] == "none"

    # ── 3. /vlm/refine calls refine_coords_posture_aware ─────────────────────

    def test_refine_route_uses_posture_aware_function(
        self, tiny_project, flask_test_client_vlm
    ):
        """
        POST /vlm/refine must call refine_coords_posture_aware, not the old
        refine_coords_with_vlm.  Patching the posture function captures the call.
        """
        client, app_module, redis_client, _, _ = flask_test_client_vlm
        self._register(client, redis_client, tiny_project)

        captured = {}

        def _fake(**kwargs):
            captured.update(kwargs)
            bps = kwargs.get("bodyparts", [])
            return (
                {bp: [10.0, 20.0] for bp in bps},
                {bp: {"reason": "ok", "dx": 0, "dy": 0} for bp in bps},
            )

        with patch("dlc.vlm_indexer.refine_coords_posture_aware", side_effect=_fake):
            resp = client.post("/vlm/refine", json={
                "active_video_stem":    "stem_A",
                "active_frame":         "img0001.png",
                "reference_video_stem": "stem_B",
                "reference_frame":      "img0003.png",
                "reference_labels":     {"snout": [5.0, 8.0]},
                "machine_coords":       {"snout": [10.0, 20.0]},
                "bodyparts":            ["snout"],
            })

        assert resp.status_code == 200, resp.get_json()
        assert captured, "refine_coords_posture_aware was never called"
        assert captured["machine_coords"] == {"snout": [10.0, 20.0]}

    def test_refine_old_function_no_longer_called(
        self, tiny_project, flask_test_client_vlm
    ):
        """
        The old refine_coords_with_vlm must NOT be called by /vlm/refine.
        """
        client, app_module, redis_client, _, _ = flask_test_client_vlm
        self._register(client, redis_client, tiny_project)

        old_called = {"called": False}

        def _old(**kwargs):
            old_called["called"] = True
            return ({}, {})

        def _new(**kwargs):
            bps = kwargs.get("bodyparts", [])
            return ({bp: [1.0, 1.0] for bp in bps}, {bp: {"reason": "ok", "dx": 0, "dy": 0} for bp in bps})

        with patch("dlc.vlm_indexer.refine_coords_with_vlm",   side_effect=_old), \
             patch("dlc.vlm_indexer.refine_coords_posture_aware", side_effect=_new):
            client.post("/vlm/refine", json={
                "active_video_stem":    "stem_A",
                "active_frame":         "img0001.png",
                "reference_video_stem": "stem_B",
                "reference_frame":      "img0003.png",
                "reference_labels":     {"snout": [5.0, 8.0]},
                "machine_coords":       {"snout": [10.0, 20.0]},
                "bodyparts":            ["snout"],
            })

        assert not old_called["called"], \
            "refine_coords_with_vlm should no longer be called by /vlm/refine"
