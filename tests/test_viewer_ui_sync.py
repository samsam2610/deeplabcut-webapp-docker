"""
Tests for the Viewer card UI/sync adjustments (feature/viewer-ui-sync-adjustments).

Covers:
  1. DOM order: controls row (.fe-controls) must appear BEFORE the chip list
     (#va-bp-list-wrap) in the rendered HTML — chips must not push controls upward.
  2. Controls row has min-height and flex:none applied inline (jitter guard).
  3. JS source: _vaLoadFrame awaits a requestAnimationFrame before drawing the
     overlay — ensures markers are painted in the same compositor tick as the
     new video frame.
  4. JS source: playback loop uses setTimeout self-scheduling (not setInterval)
     for strict sequential frame rendering.
  5. JS source: _vaStopPlayback() helper replaces raw clearInterval calls.
  6. Chip list (va-bp-list-wrap) appears AFTER the seek slider in DOM order.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

os.environ["AUTH_DISABLED"] = "true"

SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

VIEWER_JS   = SRC_DIR / "static" / "js" / "viewer.js"
VIEWER_HTML = SRC_DIR / "templates" / "partials" / "card_viewer.html"


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def viewer_js_src() -> str:
    return VIEWER_JS.read_text()


@pytest.fixture(scope="module")
def viewer_html_src() -> str:
    return VIEWER_HTML.read_text()


@pytest.fixture(scope="module")
def rendered_html() -> str:
    import tempfile
    from unittest.mock import patch, MagicMock
    import importlib

    tmp = tempfile.mkdtemp(prefix="viewer_sync_test_")
    data_dir = os.path.join(tmp, "data")
    user_data_dir = os.path.join(tmp, "user-data")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(user_data_dir, exist_ok=True)

    class FakeRedis:
        def __init__(self):
            self._s: dict = {}
            self._h: dict = {}
        def get(self, k):          return self._s.get(k)
        def set(self, k, v, ex=None): self._s[k] = v
        def setex(self, k, s, v):  self._s[k] = v
        def delete(self, *keys):
            for k in keys:
                self._s.pop(k, None); self._h.pop(k, None)
        def hset(self, name, key=None, value=None, mapping=None, **kw):
            self._h.setdefault(name, {})
            if key is not None:    self._h[name][key] = value
            if mapping:            self._h[name].update(mapping)
            self._h[name].update(kw)
        def hgetall(self, name):   return self._h.get(name, {})
        def hget(self, name, key): return self._h.get(name, {}).get(key)
        def exists(self, k):       return k in self._s or k in self._h
        def expire(self, k, s):    pass
        def zadd(self, n, m, **kw): pass
        def zrange(self, *a, **kw): return []
        def zrevrange(self, *a, **kw): return []
        def zrem(self, *a):        pass
        def scan_iter(self, pat):  return iter([])
        def sadd(self, k, *v):     pass
        def smembers(self, k):     return set()
        def spop(self, k):         return None
        def rpush(self, k, *v):    pass
        def lrange(self, k, s, e): return []
        def from_url(self, url, decode_responses=True): return self

    fake_redis = FakeRedis()
    env_patch = {
        "DATA_DIR": data_dir,
        "USER_DATA_DIR": user_data_dir,
        "AUTH_DISABLED": "true",
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "FLASK_SECRET_KEY": "test-viewer-sync-key-32chars!!!!!",
    }
    with patch.dict(os.environ, env_patch):
        with patch("redis.Redis.from_url", return_value=fake_redis):
            import app as flask_app
            importlib.reload(flask_app)
            flask_app.app.config["TESTING"] = True
            flask_app.app.config["SECRET_KEY"] = "test-viewer-sync-key-32chars!!!!!"
            with flask_app.app.test_client() as client:
                resp = client.get("/")
                assert resp.status_code == 200
                return resp.get_data(as_text=True)


# ── 1. DOM order: controls before chip list ───────────────────────────────────

def test_controls_row_before_chip_list_in_html(viewer_html_src):
    """
    The .fe-controls div must appear BEFORE va-bp-list-wrap in the HTML source.
    If the chip list is above the controls, adding/removing chips causes the
    control buttons to physically shift on screen (jitter).
    """
    pos_controls = viewer_html_src.find('class="fe-controls"')
    pos_chips    = viewer_html_src.find('id="va-bp-list-wrap"')
    assert pos_controls != -1, ".fe-controls not found in card_viewer.html"
    assert pos_chips    != -1, "va-bp-list-wrap not found in card_viewer.html"
    assert pos_controls < pos_chips, (
        ".fe-controls must appear BEFORE va-bp-list-wrap in HTML source.\n"
        f"  .fe-controls  at char {pos_controls}\n"
        f"  va-bp-list-wrap at char {pos_chips}"
    )


def test_controls_row_before_chip_list_in_rendered_html(rendered_html):
    """Same DOM-order check against the fully rendered page."""
    pos_controls = rendered_html.find('class="fe-controls"')
    pos_chips    = rendered_html.find('id="va-bp-list-wrap"')
    assert pos_controls != -1, ".fe-controls not found in rendered HTML"
    assert pos_chips    != -1, "va-bp-list-wrap not found in rendered HTML"
    assert pos_controls < pos_chips, (
        "In rendered HTML, .fe-controls must appear before va-bp-list-wrap"
    )


def test_seek_before_chip_list_in_html(viewer_html_src):
    """va-seek slider must appear before va-bp-list-wrap (chip list is below seek)."""
    pos_seek  = viewer_html_src.find('id="va-seek"')
    pos_chips = viewer_html_src.find('id="va-bp-list-wrap"')
    assert pos_seek  != -1, "va-seek not found"
    assert pos_chips != -1, "va-bp-list-wrap not found"
    assert pos_seek < pos_chips, (
        "va-seek must appear before va-bp-list-wrap in HTML source"
    )


# ── 2. Controls row has jitter-prevention CSS ─────────────────────────────────

def test_controls_row_has_flex_none(viewer_html_src):
    """
    The .fe-controls row must have flex:none inline to prevent it from
    being pushed around when the chip list below it changes height.
    """
    # Find the controls div and check the style attribute on the same element
    # Regex: class="fe-controls" ... style="...flex:none..."
    match = re.search(
        r'class="fe-controls"[^>]*style="[^"]*flex\s*:\s*none[^"]*"',
        viewer_html_src,
    )
    assert match is not None, (
        '.fe-controls must have style containing flex:none to prevent jitter'
    )


def test_controls_row_has_min_height(viewer_html_src):
    """The .fe-controls row must declare a min-height so it never collapses."""
    match = re.search(
        r'class="fe-controls"[^>]*style="[^"]*min-height[^"]*"',
        viewer_html_src,
    )
    assert match is not None, (
        '.fe-controls must have a min-height in its inline style'
    )


# ── 3. JS: requestAnimationFrame paint barrier in _vaLoadFrame ────────────────

def test_load_frame_awaits_raf_before_overlay(viewer_js_src):
    """
    _vaLoadFrame must await a requestAnimationFrame BEFORE calling _vaUpdateOverlay.
    This ensures the new video image is composited before markers are painted.

    We check that requestAnimationFrame appears between the image onload promise
    and the _vaUpdateOverlay call in the source.
    """
    # Find the region between image-load await and _vaUpdateOverlay
    # Key pattern: the rAF await must appear before _vaUpdateOverlay(n)
    raf_pos     = viewer_js_src.find("requestAnimationFrame")
    overlay_pos = viewer_js_src.find("_vaUpdateOverlay(n)")
    assert raf_pos     != -1, "requestAnimationFrame not found in viewer.js"
    assert overlay_pos != -1, "_vaUpdateOverlay(n) not found in viewer.js"
    assert raf_pos < overlay_pos, (
        "requestAnimationFrame must appear before _vaUpdateOverlay(n) in viewer.js "
        "so the paint barrier is set up before the overlay is drawn"
    )


def test_load_frame_raf_is_awaited(viewer_js_src):
    """The requestAnimationFrame call must be inside an await expression."""
    assert "await new Promise(resolve => requestAnimationFrame(resolve))" in viewer_js_src, (
        "_vaLoadFrame must contain: "
        "await new Promise(resolve => requestAnimationFrame(resolve))"
    )


# ── 4. JS: self-scheduling async loop (not setInterval) ──────────────────────

def test_playback_uses_settimeout_not_setinterval(viewer_js_src):
    """
    The playback loop must use setTimeout (self-scheduling) instead of setInterval.
    setInterval fires at wall-clock rate regardless of render completion,
    causing frame drops and marker desync when the server is slow.
    """
    # The play loop must use setTimeout
    assert "_vaPlayTimeoutId = setTimeout" in viewer_js_src or \
           "setTimeout(_vaPlayLoop" in viewer_js_src, (
        "Playback loop must schedule next tick with setTimeout, not setInterval"
    )


def test_playback_no_set_interval_for_play(viewer_js_src):
    """
    setInterval must NOT be used to drive the playback loop.
    (setInterval for other purposes, e.g. polling, is acceptable but
    the play loop specifically must be self-scheduling.)
    """
    # Check that setInterval is not called with _vaLoadFrame as the callback
    assert "setInterval(async" not in viewer_js_src, (
        "Playback loop must not use setInterval — use self-scheduling setTimeout "
        "so each frame awaits full render before scheduling the next tick"
    )


# ── 5. JS: _vaStopPlayback helper exists ─────────────────────────────────────

def test_stop_playback_helper_exists(viewer_js_src):
    """_vaStopPlayback() must be defined to unify stop logic."""
    assert "function _vaStopPlayback" in viewer_js_src, (
        "_vaStopPlayback() helper function must be defined in viewer.js"
    )


def test_stop_playback_uses_clear_timeout(viewer_js_src):
    """_vaStopPlayback must cancel the pending setTimeout, not clearInterval."""
    assert "clearTimeout" in viewer_js_src, (
        "_vaStopPlayback must call clearTimeout to cancel the pending play tick"
    )


# ── 6. JS: pose cache pre-warm on playback start ─────────────────────────────

def test_playback_starts_pose_prefetch(viewer_js_src):
    """
    When playback begins, the pose cache should be pre-warmed via
    _vaFetchPosesWindow so the first N frames render with markers immediately.
    """
    # Find the play start section (where _vaPlayTimer is set to true)
    play_start_idx = viewer_js_src.find("_vaPlayTimer = true")
    assert play_start_idx != -1, "_vaPlayTimer = true not found in viewer.js"

    # _vaFetchPosesWindow must be called soon after (within 300 chars)
    region = viewer_js_src[play_start_idx: play_start_idx + 300]
    assert "_vaFetchPosesWindow" in region, (
        "_vaFetchPosesWindow must be called when playback starts to pre-warm "
        "the pose cache — otherwise the first N frames show no markers"
    )
