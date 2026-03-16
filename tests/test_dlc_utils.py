"""
Tests for DLC utility/helper functions extracted from app.py.
All tests run against the UNMODIFIED monolithic code.

Note: conftest.py sets DATA_DIR env var before any import so app.py's
module-level DATA_DIR.mkdir() succeeds.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# conftest.py already set DATA_DIR before this import runs
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Patch redis before importing app so the module-level Redis.from_url call is mocked
with patch("redis.Redis.from_url", return_value=MagicMock()):
    import app as _app_module


class TestEngineInfo:
    """_engine_info() returns correct tuple for each engine."""

    def test_pytorch_default(self):
        result = _app_module._engine_info("pytorch")
        assert result == ("dlc-models-pytorch", "pytorch_config.yaml", "evaluation-results-pytorch")

    def test_pytorch_uppercase(self):
        result = _app_module._engine_info("PyTorch")
        assert result == ("dlc-models-pytorch", "pytorch_config.yaml", "evaluation-results-pytorch")

    def test_tensorflow(self):
        result = _app_module._engine_info("tensorflow")
        assert result == ("dlc-models", "pose_cfg.yaml", "evaluation-results")

    def test_tf_alias(self):
        result = _app_module._engine_info("tf")
        assert result == ("dlc-models", "pose_cfg.yaml", "evaluation-results")

    def test_unknown_engine_defaults_to_pytorch(self):
        result = _app_module._engine_info("unknown_engine")
        # Unknown engine should map to pytorch (not tensorflow)
        assert result[0] == "dlc-models-pytorch"


class TestGetEngineQueue:
    """_get_engine_queue() maps engine → Celery queue name."""

    def test_pytorch_queue(self):
        q = _app_module._get_engine_queue("pytorch")
        assert q == "pytorch"

    def test_tensorflow_queue(self):
        q = _app_module._get_engine_queue("tensorflow")
        assert q == "tensorflow"

    def test_tf_alias_queue(self):
        q = _app_module._get_engine_queue("tf")
        assert q == "tensorflow"


class TestGetPipelineFolders:
    """_get_pipeline_folders() returns correct DLC folder list."""

    def test_pytorch_includes_models_folder(self):
        folders = _app_module._get_pipeline_folders("pytorch")
        folder_names = [f[1] for f in folders]
        assert "dlc-models-pytorch" in folder_names

    def test_tf_includes_models_folder(self):
        folders = _app_module._get_pipeline_folders("tf")
        folder_names = [f[1] for f in folders]
        assert "dlc-models" in folder_names

    def test_common_folders_present(self):
        folders = _app_module._get_pipeline_folders("pytorch")
        folder_names = [f[1] for f in folders]
        assert "labeled-data" in folder_names
        assert "training-datasets" in folder_names
        assert "videos" in folder_names


class TestResolveProjectDir:
    """_resolve_project_dir() safely resolves project paths."""

    def test_resolves_within_data_dir(self, tmp_path):
        orig_data_dir = _app_module.DATA_DIR
        _app_module.DATA_DIR = tmp_path
        try:
            subdir = tmp_path / "my_project"
            subdir.mkdir()
            result = _app_module._resolve_project_dir("my_project")
            assert result == subdir.resolve()
        finally:
            _app_module.DATA_DIR = orig_data_dir

    def test_traversal_prevention(self, tmp_path):
        """Path traversal must not escape DATA_DIR."""
        orig_data_dir = _app_module.DATA_DIR
        _app_module.DATA_DIR = tmp_path
        try:
            with pytest.raises(Exception):
                _app_module._resolve_project_dir("../../etc/passwd")
        finally:
            _app_module.DATA_DIR = orig_data_dir


class TestDlcProjectSecurityCheck:
    """_dlc_project_security_check() enforces allowed-root policy."""

    def test_path_inside_data_dir_allowed(self, tmp_path):
        orig_data = _app_module.DATA_DIR
        orig_user = _app_module.USER_DATA_DIR
        _app_module.DATA_DIR = tmp_path
        _app_module.USER_DATA_DIR = tmp_path / "user-data"
        try:
            inside = tmp_path / "some_project"
            inside.mkdir()
            assert _app_module._dlc_project_security_check(inside) is True
        finally:
            _app_module.DATA_DIR = orig_data
            _app_module.USER_DATA_DIR = orig_user

    def test_path_outside_denied(self, tmp_path):
        orig_data = _app_module.DATA_DIR
        orig_user = _app_module.USER_DATA_DIR
        _app_module.DATA_DIR = tmp_path / "data"
        _app_module.USER_DATA_DIR = tmp_path / "user-data"
        try:
            outside = tmp_path / "outside"
            outside.mkdir()
            assert _app_module._dlc_project_security_check(outside) is False
        finally:
            _app_module.DATA_DIR = orig_data
            _app_module.USER_DATA_DIR = orig_user

    def test_path_inside_user_data_dir_allowed(self, tmp_path):
        orig_data = _app_module.DATA_DIR
        orig_user = _app_module.USER_DATA_DIR
        _app_module.DATA_DIR = tmp_path / "data"
        _app_module.USER_DATA_DIR = tmp_path / "user-data"
        (tmp_path / "user-data").mkdir(parents=True)
        try:
            inside = tmp_path / "user-data" / "project"
            inside.mkdir()
            assert _app_module._dlc_project_security_check(inside) is True
        finally:
            _app_module.DATA_DIR = orig_data
            _app_module.USER_DATA_DIR = orig_user


class TestWalkDir:
    """_walk_dir() returns correct recursive structure."""

    def test_lists_files_and_dirs(self, tmp_path):
        (tmp_path / "dir_a").mkdir()
        (tmp_path / "dir_a" / "file.txt").write_text("x")
        (tmp_path / "file_b.txt").write_text("y")

        items = _app_module._walk_dir(tmp_path, tmp_path)
        names = [i["name"] for i in items]
        assert "dir_a" in names
        assert "file_b.txt" in names

    def test_dirs_before_files(self, tmp_path):
        (tmp_path / "z_dir").mkdir()
        (tmp_path / "a_file.txt").write_text("x")

        items = _app_module._walk_dir(tmp_path, tmp_path)
        types = [i["type"] for i in items]
        # Find first file index; all dirs should come before it
        file_indices = [i for i, t in enumerate(types) if t == "file"]
        dir_indices  = [i for i, t in enumerate(types) if t == "dir"]
        if file_indices and dir_indices:
            assert max(dir_indices) < min(file_indices)

    def test_max_depth_respected(self, tmp_path):
        deep = tmp_path
        for i in range(8):
            deep = deep / f"level_{i}"
            deep.mkdir()
        (deep / "deep_file.txt").write_text("x")

        items = _app_module._walk_dir(tmp_path, tmp_path, max_depth=3)
        all_names = _flatten_names(items)
        assert "deep_file.txt" not in all_names


def _flatten_names(items):
    names = []
    for item in items:
        names.append(item.get("name", ""))
        if "children" in item:
            names.extend(_flatten_names(item["children"]))
    return names


class TestDirHasMedia:
    """_dir_has_media() correctly detects media files."""

    def test_detects_avi(self, tmp_path):
        (tmp_path / "video.avi").write_bytes(b"\x00")
        assert _app_module._dir_has_media(tmp_path) is True

    def test_detects_mp4(self, tmp_path):
        (tmp_path / "clip.mp4").write_bytes(b"\x00")
        assert _app_module._dir_has_media(tmp_path) is True

    def test_empty_dir_returns_false(self, tmp_path):
        assert _app_module._dir_has_media(tmp_path) is False

    def test_non_media_file_returns_false(self, tmp_path):
        (tmp_path / "notes.txt").write_text("x")
        assert _app_module._dir_has_media(tmp_path) is False
