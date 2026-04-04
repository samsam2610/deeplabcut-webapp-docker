"""
Frontend asset integration tests — Phase 2 safety net.

Verifies:
  1. Static file endpoints return 200 OK.
  2. The main HTML template renders successfully.
  3. All critical DOM element IDs are present in the rendered HTML.
  4. Timeline/overlay canvas elements use <canvas> (not per-frame DOM nodes).
  5. After the CSS/JS split, the new modular static files are also served correctly.

These tests run on BOTH the monolithic code (baseline) and the refactored code
(regression guard).  AUTH_DISABLED is forced to bypass the Jupyter-style token gate.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

# Force auth bypass for all tests in this module.
os.environ["AUTH_DISABLED"] = "true"

SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))


@pytest.fixture(scope="module")
def app_client():
    """
    Flask test client with:
    - AUTH_DISABLED=true so we can fetch the main page without a token.
    - Redis replaced with an in-memory fake.
    - DATA_DIR / USER_DATA_DIR isolated in a temp directory.
    """
    import tempfile

    tmp = tempfile.mkdtemp(prefix="fe_test_")
    data_dir = os.path.join(tmp, "data")
    user_data_dir = os.path.join(tmp, "user-data")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(user_data_dir, exist_ok=True)

    env_patch = {
        "DATA_DIR": data_dir,
        "USER_DATA_DIR": user_data_dir,
        "AUTH_DISABLED": "true",
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "FLASK_SECRET_KEY": "test-frontend-assets-key-32chars!!",
    }

    fake_redis = _build_fake_redis()

    with patch.dict(os.environ, env_patch):
        with patch("redis.Redis.from_url", return_value=fake_redis):
            import importlib
            import app as flask_app

            importlib.reload(flask_app)  # pick up new env vars

            flask_app.app.config["TESTING"] = True
            flask_app.app.config["SECRET_KEY"] = "test-frontend-assets-key-32chars!!"

            with flask_app.app.test_client() as client:
                yield client


def _build_fake_redis():
    """Minimal in-memory Redis stub."""

    class FakeRedis:
        def __init__(self):
            self._s: dict = {}
            self._h: dict = {}

        def get(self, k):
            return self._s.get(k)

        def set(self, k, v, ex=None):
            self._s[k] = v

        def setex(self, k, seconds, v):
            self._s[k] = v

        def delete(self, *keys):
            for k in keys:
                self._s.pop(k, None)
                self._h.pop(k, None)

        def hset(self, name, key=None, value=None, mapping=None, **kw):
            self._h.setdefault(name, {})
            if key is not None:
                self._h[name][key] = value
            if mapping:
                self._h[name].update(mapping)
            self._h[name].update(kw)

        def hgetall(self, name):
            return self._h.get(name, {})

        def hget(self, name, key):
            return self._h.get(name, {}).get(key)

        def expire(self, k, s):
            pass

        def zadd(self, name, mapping, **kw):
            pass

        def zrange(self, name, start, stop, withscores=False, rev=False):
            return []

        def zrevrange(self, name, start, stop, withscores=False):
            return []

        def zrem(self, name, *members):
            pass

        def scan_iter(self, pattern):
            return iter([])

        def from_url(self, url, decode_responses=True):
            return self

    return FakeRedis()


# ---------------------------------------------------------------------------
# Critical DOM IDs that MUST survive the refactor.
# Grouped by card so regressions are easy to diagnose.
# ---------------------------------------------------------------------------

CRITICAL_IDS: dict[str, list[str]] = {
    # Anipose / session bar
    "session": [
        "session-dot",
        "session-label",
        "session-bar",
        "actions-card",
        "config-card",
    ],
    # DLC project manager
    "dlc_project": [
        "dlc-project-card",
        "dlc-pipeline-section",
        "dlc-folder-nav",
        "dlc-browse-btn",
        "dlc-select-btn",
    ],
    # Frame extractor
    "frame_extractor": [
        "frame-extractor-card",
        "fe-server-section",
        "fe-player-section",
    ],
    # Frame labeler
    "frame_labeler": [
        "frame-labeler-card",
        "fl-player-section",
        "fl-canvas",
        "fl-btn-next",
    ],
    # Training
    "training": [
        "create-training-dataset-card",
        "train-network-card",
        "tn-epochs",
        "tn-progress-bar",
    ],
    # Analyze
    "analyze": [
        "analyze-card",
        "av-progress-bar",
        "av-log-output",
    ],
    # Viewer (VA card) — canvas-based elements critical for memory-leak regression
    "viewer": [
        "view-analyzed-card",
        "va-video-wrap",
        "va-frame-img",
        "va-seek",
        "va-overlay-canvas",   # kinematic overlay — must be <canvas>
        "va-status-canvas",    # timeline status — must be <canvas> NOT per-frame divs
        "va-note-canvas",      # timeline notes   — must be <canvas>
        "va-curation-panel",
        "va-overlay-panel",
        "va-metadata-panel",
    ],
    # Video annotator
    "annotator": [
        "annotate-video-card",
        "anv-frame-img",
        "anv-seek",
        "anv-status-canvas",  # must be <canvas>
        "anv-note-canvas",    # must be <canvas>
        "anv-clip-section",
    ],
    # GPU monitor
    "gpu_monitor": [
        "gpu-monitor-card",
        "gpu-monitor-badge",
        "gm-jobs-list",
    ],
    # Custom script
    "custom_script": [
        "custom-script-card",
    ],
}

# Flat list for parametrize
ALL_CRITICAL_IDS = [
    pytest.param(elem_id, id=f"{card}_{elem_id}")
    for card, ids in CRITICAL_IDS.items()
    for elem_id in ids
]


# ---------------------------------------------------------------------------
# 1. Monolithic static file endpoints
# ---------------------------------------------------------------------------

MONOLITHIC_STATIC_FILES = [
    "main.js",
    "style.css",
]


@pytest.mark.parametrize("filename", MONOLITHIC_STATIC_FILES)
def test_monolithic_static_file_200(app_client, filename):
    """Original monolithic static files must return 200."""
    resp = app_client.get(f"/static/{filename}")
    assert resp.status_code == 200, (
        f"/static/{filename} returned {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# 2. Modular static file endpoints (populated after Phase 3 split)
# ---------------------------------------------------------------------------

CSS_MODULE_FILES = [
    "css/variables.css",
    "css/base.css",
    "css/layout.css",
    "css/components.css",
    "css/viewer.css",
]

JS_MODULE_FILES = [
    "js/state.js",
    "js/api.js",
    "js/anipose.js",
    "js/dlc_project.js",
    "js/frame_extractor.js",
    "js/frame_labeler.js",
    "js/training.js",
    "js/analyze.js",
    "js/viewer.js",
    "js/annotator.js",
    "js/gpu_monitor.js",
    "js/custom_script.js",
    "js/main.js",
]

STATIC_DIR = SRC_DIR / "static"


@pytest.mark.parametrize("path", CSS_MODULE_FILES)
def test_css_module_file_exists(path):
    """Each modular CSS file must exist on disk after the split."""
    full = STATIC_DIR / path
    assert full.exists(), f"Missing modular CSS: {full}"


@pytest.mark.parametrize("path", CSS_MODULE_FILES)
def test_css_module_file_200(app_client, path):
    """Each modular CSS file must be served as 200."""
    resp = app_client.get(f"/static/{path}")
    assert resp.status_code == 200, (
        f"/static/{path} returned {resp.status_code}"
    )


@pytest.mark.parametrize("path", JS_MODULE_FILES)
def test_js_module_file_exists(path):
    """Each modular JS file must exist on disk after the split."""
    full = STATIC_DIR / path
    assert full.exists(), f"Missing modular JS: {full}"


@pytest.mark.parametrize("path", JS_MODULE_FILES)
def test_js_module_file_200(app_client, path):
    """Each modular JS file must be served as 200."""
    resp = app_client.get(f"/static/{path}")
    assert resp.status_code == 200, (
        f"/static/{path} returned {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# 3. Main HTML template renders successfully
# ---------------------------------------------------------------------------

def test_index_renders_200(app_client):
    """GET / must return 200 with AUTH_DISABLED=true."""
    resp = app_client.get("/")
    assert resp.status_code == 200, f"/ returned {resp.status_code}"


def test_index_content_type_html(app_client):
    """/ must return text/html."""
    resp = app_client.get("/")
    assert "text/html" in resp.content_type


# ---------------------------------------------------------------------------
# 4. Critical DOM element IDs present in rendered HTML
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rendered_html(app_client) -> str:
    resp = app_client.get("/")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


@pytest.mark.parametrize("elem_id", ALL_CRITICAL_IDS)
def test_critical_dom_id_present(rendered_html, elem_id):
    """Every critical DOM ID must appear in the rendered index.html."""
    assert f'id="{elem_id}"' in rendered_html, (
        f'DOM element id="{elem_id}" is missing from rendered index.html'
    )


# ---------------------------------------------------------------------------
# 5. Canvas regression: timeline/overlay elements must use <canvas>
#    (never bare <div>/<span> per annotated frame — see feedback_timeline_dom_antipattern.md)
# ---------------------------------------------------------------------------

CANVAS_IDS = [
    "va-status-canvas",
    "va-note-canvas",
    "va-overlay-canvas",
    "anv-status-canvas",
    "anv-note-canvas",
]


@pytest.mark.parametrize("canvas_id", CANVAS_IDS)
def test_timeline_uses_canvas_element(rendered_html, canvas_id):
    """Timeline/overlay containers must be <canvas> tags, not <div> or <span>."""
    # The element must appear as a <canvas id="..."> tag
    assert f'<canvas' in rendered_html and f'id="{canvas_id}"' in rendered_html, (
        f'id="{canvas_id}" must exist'
    )
    # Must NOT be declared as a <div id="..."> or <span id="...">
    for bad_tag in ("div", "span"):
        assert f'<{bad_tag} id="{canvas_id}"' not in rendered_html, (
            f'id="{canvas_id}" is a <{bad_tag}> — must be <canvas> to prevent '
            f"per-frame DOM node memory leak (see feedback_timeline_dom_antipattern.md)"
        )


# ---------------------------------------------------------------------------
# 6. After refactor: index.html must NOT inline the old monolithic main.js
#    (it must load the modular JS entry point instead)
# ---------------------------------------------------------------------------

def test_index_loads_modular_js_entry(rendered_html):
    """After refactor, index.html must load static/js/main.js as a module."""
    assert 'src="/static/js/main.js"' in rendered_html or \
           "static/js/main.js" in rendered_html, (
        "index.html must reference the modular JS entry point (static/js/main.js)"
    )


def test_index_loads_modular_css(rendered_html):
    """After refactor, index.html must load at least one modular CSS file."""
    assert "static/css/" in rendered_html, (
        "index.html must reference at least one modular CSS file from static/css/"
    )
