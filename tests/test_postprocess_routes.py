"""Tests for the post-process predictions blueprint."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dlc import postprocess as pp  # noqa: E402


def test_blueprint_registered(flask_test_client):
    """The /dlc/postprocess/recent route must be registered on the app.

    Note: this app's auth middleware returns 302 (login redirect) for ANY
    unknown URL, so a status-code check alone cannot distinguish a real
    route from a missing one. Instead, assert the rule is present in the
    URL map, then confirm the response is not a 404.
    """
    client, app_module, _redis_client, _data_dir, _user_data_dir = flask_test_client

    rules = {str(r) for r in app_module.app.url_map.iter_rules()}
    assert "/dlc/postprocess/recent" in rules, (
        f"/dlc/postprocess/recent not registered. Rules sample: "
        f"{sorted(r for r in rules if r.startswith('/dlc/'))[:5]}"
    )

    resp = client.get("/dlc/postprocess/recent")
    # Route exists; either 200 (empty list), 302 (auth redirect), or 400
    # (no active project), but never 404.
    assert resp.status_code != 404


def test_make_run_subfolder_uses_timestamp_and_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "_now_stamp", lambda: "20260501-120000")
    result = pp.make_run_subfolder(tmp_path, "filterpredictions")
    assert result.name == "20260501-120000_filterpredictions"
    assert result.parent == tmp_path / "postproc"
    assert result.is_dir()


def test_make_run_subfolder_refuses_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "_now_stamp", lambda: "20260501-120000")
    pp.make_run_subfolder(tmp_path, "filterpredictions")
    with pytest.raises(FileExistsError):
        pp.make_run_subfolder(tmp_path, "filterpredictions")


def test_write_sidecar(tmp_path):
    pp.write_sidecar(tmp_path, {
        "run_id": "x",
        "tool": "deeplabcut",
        "action": "filterpredictions",
        "status": "success",
        "params": {},
        "inputs": [],
    })
    data = json.loads((tmp_path / "run.json").read_text())
    assert data["run_id"] == "x"


def test_scan_inputs_file_mode_returns_single(tmp_path):
    src = tmp_path / "videoDLC_resnet50_shuffle1.h5"
    src.write_bytes(b"")
    assert pp.scan_inputs(src, "file") == [src]


def test_scan_inputs_folder_skips_postproc(tmp_path):
    a = tmp_path / "videoDLC_resnet50.h5"
    b = tmp_path / "postproc" / "20260101-000000_x" / "videoBDLC_resnet50.h5"
    a.write_bytes(b"")
    b.parent.mkdir(parents=True)
    b.write_bytes(b"")
    files = pp.scan_inputs(tmp_path, "folder")
    assert a in files
    assert b not in files


def test_scan_inputs_folder_excludes_filtered(tmp_path):
    a = tmp_path / "videoDLC_resnet50_shuffle1.h5"
    b = tmp_path / "videoDLC_resnet50_shuffle1_filtered.h5"
    a.write_bytes(b""); b.write_bytes(b"")
    files = pp.scan_inputs(tmp_path, "folder")
    assert a in files
    assert b not in files


def test_scan_inputs_unknown_mode_raises(tmp_path):
    with pytest.raises(ValueError):
        pp.scan_inputs(tmp_path, "weird")
