"""
TDD tests for Analyze Card UX improvements.

Covers:
  1. New DOM elements (batch list, add/clear buttons, labeled-params section ID).
  2. JS source assertions — create-labeled checkbox toggles params section;
     double-click adds to batch list; run button sends target_paths array.
  3. Backend endpoint accepts target_paths (array), validates each path,
     rejects empty arrays.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ["AUTH_DISABLED"] = "true"

SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

ANALYZE_JS = SRC_DIR / "static" / "js" / "analyze.js"
ANALYZE_HTML = SRC_DIR / "templates" / "partials" / "card_analyze.html"


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _build_fake_redis():
    class FakeRedis:
        def __init__(self):
            self._s: dict = {}
            self._h: dict = {}

        def get(self, k):           return self._s.get(k)
        def set(self, k, v, ex=None): self._s[k] = v
        def setex(self, k, s, v):   self._s[k] = v
        def delete(self, *keys):
            for k in keys:
                self._s.pop(k, None); self._h.pop(k, None)
        def hset(self, name, key=None, value=None, mapping=None, **kw):
            self._h.setdefault(name, {})
            if key is not None:         self._h[name][key] = value
            if mapping:                 self._h[name].update(mapping)
            self._h[name].update(kw)
        def hgetall(self, name):    return self._h.get(name, {})
        def hget(self, name, key):  return self._h.get(name, {}).get(key)
        def exists(self, k):        return k in self._s or k in self._h
        def expire(self, k, s):     pass
        def zadd(self, n, m, **kw): pass
        def zrange(self, *a, **kw): return []
        def zrevrange(self, *a, **kw): return []
        def zrem(self, *a):         pass
        def scan_iter(self, pat):   return iter([])
        def sadd(self, k, *v):      pass
        def smembers(self, k):      return set()
        def spop(self, k):          return None
        def rpush(self, k, *v):     pass
        def lrange(self, k, s, e):  return []
        def from_url(self, url, decode_responses=True): return self

    return FakeRedis()


@pytest.fixture(scope="module")
def app_client():
    tmp = tempfile.mkdtemp(prefix="analyze_ux_test_")
    data_dir = os.path.join(tmp, "data")
    user_data_dir = os.path.join(tmp, "user-data")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(user_data_dir, exist_ok=True)

    env_patch = {
        "DATA_DIR": data_dir,
        "USER_DATA_DIR": user_data_dir,
        "AUTH_DISABLED": "true",
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "FLASK_SECRET_KEY": "test-analyze-ux-key-32chars!!!!!",
    }
    fake_redis = _build_fake_redis()

    with patch.dict(os.environ, env_patch):
        with patch("redis.Redis.from_url", return_value=fake_redis):
            import importlib
            import app as flask_app
            importlib.reload(flask_app)
            flask_app.app.config["TESTING"] = True
            flask_app.app.config["SECRET_KEY"] = "test-analyze-ux-key-32chars!!!!!"
            with flask_app.app.test_client() as client:
                yield client


@pytest.fixture(scope="module")
def rendered_html(app_client) -> str:
    resp = app_client.get("/")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


@pytest.fixture(scope="module")
def analyze_js_src() -> str:
    return ANALYZE_JS.read_text()


@pytest.fixture(scope="module")
def analyze_html_src() -> str:
    return ANALYZE_HTML.read_text()


# ── 1. DOM structure: new element IDs must be present ────────────────────────

def test_av_labeled_params_section_id_in_html(rendered_html):
    """The labeled video params block must have id='av-labeled-params-section'."""
    assert 'id="av-labeled-params-section"' in rendered_html, (
        "id='av-labeled-params-section' missing — needed for checkbox-driven show/hide"
    )


def test_av_batch_list_id_in_html(rendered_html):
    """A batch selection list container with id='av-batch-list' must exist."""
    assert 'id="av-batch-list"' in rendered_html, (
        "id='av-batch-list' missing — needed to render the selected-paths queue"
    )


def test_av_batch_add_btn_id_in_html(rendered_html):
    """An 'Add to list' button with id='av-batch-add-btn' must be present."""
    assert 'id="av-batch-add-btn"' in rendered_html, (
        "id='av-batch-add-btn' missing — single-click-then-add workflow"
    )


def test_av_batch_clear_btn_id_in_html(rendered_html):
    """A global 'Clear list' button with id='av-batch-clear-btn' must be present."""
    assert 'id="av-batch-clear-btn"' in rendered_html, (
        "id='av-batch-clear-btn' missing — bulk-clear the selection queue"
    )


# ── 2. JS behaviour assertions (source-level) ────────────────────────────────

def test_analyze_js_create_labeled_change_listener(analyze_js_src):
    """analyze.js must attach a 'change' listener to 'av-create-labeled'."""
    assert "av-create-labeled" in analyze_js_src
    assert "change" in analyze_js_src, (
        "No 'change' event handler found — checkbox must toggle the params section"
    )


def test_analyze_js_labeled_params_section_toggled(analyze_js_src):
    """analyze.js must reference av-labeled-params-section to show/hide it."""
    assert "av-labeled-params-section" in analyze_js_src, (
        "analyze.js must reference 'av-labeled-params-section' to toggle its visibility"
    )


def test_analyze_js_batch_list_referenced(analyze_js_src):
    """analyze.js must reference 'av-batch-list' to render the selection queue."""
    assert "av-batch-list" in analyze_js_src, (
        "analyze.js must manipulate 'av-batch-list'"
    )


def test_analyze_js_sends_target_paths_array(analyze_js_src):
    """Run handler must send 'target_paths' (array) not just 'target_path' string."""
    assert "target_paths" in analyze_js_src, (
        "analyze.js run handler must send target_paths (array) to the backend"
    )


def test_analyze_js_dblclick_adds_to_batch(analyze_js_src):
    """Double-click on a browser entry must trigger add-to-list logic."""
    # The JS must call something like _avAddToList or push to _avBatchList on dblclick
    assert "dblclick" in analyze_js_src
    assert "av-batch-list" in analyze_js_src or "_avBatchList" in analyze_js_src, (
        "Double-click handler must add the path to the batch list"
    )


# ── 3. Backend endpoint: target_paths array ───────────────────────────────────

import contextlib

@contextlib.contextmanager
def _make_analyze_client_with_project(tmp_dir: str):
    """
    Return (client, video_path) with:
      - a real temp file to satisfy Path.exists() checks
      - Redis primed with an active DLC project
      - Celery send_task mocked out
    """
    import importlib
    import app as flask_app

    video_path = os.path.join(tmp_dir, "test_video.mp4")
    Path(video_path).touch()

    config_path = os.path.join(tmp_dir, "config.yaml")
    Path(config_path).write_text("project: test\n")

    fake_redis = _build_fake_redis()
    project_json = json.dumps({
        "config_path": config_path,
        "project_path": tmp_dir,
        "engine": "pytorch",
    }).encode()
    # Prime the session-keyed redis entry — key is webapp:dlc_project:<uid>
    # We'll patch the _dlc_key helper to return a fixed key.
    fake_redis._s["webapp:dlc_project:testuid"] = project_json

    mock_celery = MagicMock()
    mock_task = MagicMock()
    mock_task.id = "test-task-id-123"
    mock_celery.send_task.return_value = mock_task

    env_patch = {
        "DATA_DIR": tmp_dir,
        "USER_DATA_DIR": tmp_dir,
        "AUTH_DISABLED": "true",
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "FLASK_SECRET_KEY": "test-analyze-backend-key-32ch!!!",
    }

    with patch.dict(os.environ, env_patch):
        with patch("redis.Redis.from_url", return_value=fake_redis):
            importlib.reload(flask_app)
            flask_app.app.config["TESTING"] = True
            flask_app.app.config["SECRET_KEY"] = "test-analyze-backend-key-32ch!!!"

            with flask_app.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["uid"] = "testuid"

                with patch("dlc.ctx.celery", return_value=mock_celery):
                    yield client, video_path, mock_celery


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="analyze_backend_test_") as d:
        yield d


def test_analyze_endpoint_accepts_target_paths_array(tmp_dir):
    """POST /dlc/project/analyze with target_paths array dispatches one task per path."""
    with _make_analyze_client_with_project(tmp_dir) as (client, video_path, mock_celery):
        resp = client.post(
            "/dlc/project/analyze",
            json={"target_paths": [video_path]},
            content_type="application/json",
        )
        assert resp.status_code == 202, (
            f"Expected 202, got {resp.status_code}: {resp.get_data(as_text=True)}"
        )
        data = resp.get_json()
        assert "task_ids" in data or "task_id" in data, (
            "Response must include task_id(s)"
        )
        assert mock_celery.send_task.called, "Celery send_task must be called"


def test_analyze_endpoint_rejects_empty_target_paths(tmp_dir):
    """POST with target_paths=[] must return 400."""
    with _make_analyze_client_with_project(tmp_dir) as (client, video_path, mock_celery):
        resp = client.post(
            "/dlc/project/analyze",
            json={"target_paths": []},
            content_type="application/json",
        )
        assert resp.status_code == 400, (
            f"Empty target_paths should be rejected: got {resp.status_code}"
        )


def test_analyze_endpoint_rejects_nonexistent_paths(tmp_dir):
    """POST with a path that doesn't exist must return 400."""
    with _make_analyze_client_with_project(tmp_dir) as (client, video_path, mock_celery):
        resp = client.post(
            "/dlc/project/analyze",
            json={"target_paths": ["/this/path/does/not/exist/video.mp4"]},
            content_type="application/json",
        )
        assert resp.status_code == 400, (
            f"Non-existent path should be rejected: got {resp.status_code}"
        )


def test_analyze_endpoint_backward_compat_single_target_path(tmp_dir):
    """POST with legacy target_path (string) must still work → 202."""
    with _make_analyze_client_with_project(tmp_dir) as (client, video_path, mock_celery):
        resp = client.post(
            "/dlc/project/analyze",
            json={"target_path": video_path},
            content_type="application/json",
        )
        assert resp.status_code == 202, (
            f"Backward-compat target_path must still return 202: got {resp.status_code}"
        )


def test_analyze_endpoint_accepts_multiple_paths(tmp_dir):
    """POST with multiple paths dispatches one task per path."""
    with _make_analyze_client_with_project(tmp_dir) as (client, video_path, mock_celery):
        video2 = os.path.join(tmp_dir, "test_video2.mp4")
        Path(video2).touch()

        resp = client.post(
            "/dlc/project/analyze",
            json={"target_paths": [video_path, video2]},
            content_type="application/json",
        )
        assert resp.status_code == 202, (
            f"Multiple paths should return 202: got {resp.status_code}"
        )
        data = resp.get_json()
        # Expect either task_ids list or a single task_id
        task_ids = data.get("task_ids") or ([data.get("task_id")] if data.get("task_id") else [])
        assert len(task_ids) >= 1, "At least one task must be dispatched"
        assert mock_celery.send_task.call_count >= 2, (
            "send_task must be called once per path"
        )
