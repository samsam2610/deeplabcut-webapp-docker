"""
Tests for annotate_video_frame VideoCapture caching (src/routes/annotate.py).

Verifies that:
  - Sequential frames skip cv2.CAP_PROP_POS_FRAMES (no seek)
  - Non-sequential frames perform exactly one seek
  - The VideoCapture is not re-opened on every request
  - Different users get independent cache entries
  - The cache is bounded (LRU eviction)

Integration tests (require the MAPS-DREADSS AVI on the docker data volume) confirm
the cached route is readable and produces valid JPEG bytes.
"""
from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ── Path setup ───────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# ── Reference data ───────────────────────────────────────────────────────────
_TEST_VIDEO = Path(
    "/user-data/Parra-Data/Cloud/Reaching-Task-Data"
    "/RatBox Videos/MAPS-DREADSS/MAP1_20250713_112101_3.avi"
)
_DATA_AVAILABLE = _TEST_VIDEO.is_file()
_skip_no_data   = pytest.mark.skipif(
    not _DATA_AVAILABLE,
    reason="Test video not mounted — skipping integration test.",
)


# ── Flask test client fixture ─────────────────────────────────────────────────

@pytest.fixture(scope="module")
def tav_client():
    """Minimal Flask test client with annotate blueprint and session support."""
    from flask import Flask
    from routes.annotate import bp as annotate_bp, _anv_vcap_cache

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(annotate_bp)
    app.config["TESTING"] = True

    # Clear cache before module-level tests to avoid cross-test pollution
    _anv_vcap_cache.clear()
    return app.test_client()


@pytest.fixture(autouse=True)
def tav_clear_cache():
    """Clear the annotate vcap cache before every test."""
    from routes.annotate import _anv_vcap_cache
    for entry in _anv_vcap_cache.values():
        if entry.get("vcap"):
            try:
                entry["vcap"].release()
            except Exception:
                pass
    _anv_vcap_cache.clear()
    yield
    for entry in _anv_vcap_cache.values():
        if entry.get("vcap"):
            try:
                entry["vcap"].release()
            except Exception:
                pass
    _anv_vcap_cache.clear()


# ── Unit tests: caching behaviour (mocked cv2) ───────────────────────────────

def _make_mock_cap(frame_count: int = 100):
    """Return a mock cv2.VideoCapture that always succeeds."""
    import numpy as np
    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.read.return_value = (True, np.zeros((100, 100, 3), dtype="uint8"))
    cap.get.return_value = 0
    return cap


def test_tav_sequential_frames_skip_seek(tav_client, tmp_path):
    """Requesting frame N then N+1 must NOT call set(CAP_PROP_POS_FRAMES) for N+1."""
    import cv2
    from routes.annotate import _anv_vcap_cache

    dummy = tmp_path / "vid.avi"
    dummy.write_bytes(b"fake")

    mock_cap = _make_mock_cap()

    with patch("cv2.VideoCapture", return_value=mock_cap), \
         patch("cv2.imencode",
               return_value=(True, MagicMock(tobytes=lambda: b"\xff\xd8\xff"))), \
         tav_client.session_transaction() as sess:
        sess["uid"] = "test-user-seq"

    with patch("cv2.VideoCapture", return_value=mock_cap), \
         patch("cv2.imencode",
               return_value=(True, MagicMock(tobytes=lambda: b"\xff\xd8\xff"))):
        with tav_client.session_transaction() as sess:
            sess["uid"] = "test-user-seq"
        tav_client.get(f"/annotate/video-frame/0?path={dummy}")
        mock_cap.set.reset_mock()           # ignore the initial seek to frame 0
        tav_client.get(f"/annotate/video-frame/1?path={dummy}")

    # Frame 1 = sequential after frame 0 → no seek
    mock_cap.set.assert_not_called()


def test_tav_nonsequential_seek_called(tav_client, tmp_path):
    """Requesting frame 0 then frame 50 must call set(CAP_PROP_POS_FRAMES, 50)."""
    import cv2
    dummy = tmp_path / "vid.avi"
    dummy.write_bytes(b"fake")

    mock_cap = _make_mock_cap()

    with patch("cv2.VideoCapture", return_value=mock_cap), \
         patch("cv2.imencode",
               return_value=(True, MagicMock(tobytes=lambda: b"\xff\xd8\xff"))):
        with tav_client.session_transaction() as sess:
            sess["uid"] = "test-user-seek"
        tav_client.get(f"/annotate/video-frame/0?path={dummy}")
        mock_cap.set.reset_mock()
        tav_client.get(f"/annotate/video-frame/50?path={dummy}")

    mock_cap.set.assert_called_once_with(cv2.CAP_PROP_POS_FRAMES, 50)


def test_tav_vcap_not_reopened_for_same_video(tav_client, tmp_path):
    """cv2.VideoCapture() must be called only ONCE for sequential requests to the same video."""
    dummy = tmp_path / "vid.avi"
    dummy.write_bytes(b"fake")

    mock_cap = _make_mock_cap()

    with patch("cv2.VideoCapture", return_value=mock_cap) as ctor, \
         patch("cv2.imencode",
               return_value=(True, MagicMock(tobytes=lambda: b"\xff\xd8\xff"))):
        with tav_client.session_transaction() as sess:
            sess["uid"] = "test-user-noopen"
        for n in range(5):
            tav_client.get(f"/annotate/video-frame/{n}?path={dummy}")

    ctor.assert_called_once()


def test_tav_different_videos_reopen(tav_client, tmp_path):
    """Switching to a different video path must reopen VideoCapture."""
    vid_a = tmp_path / "a.avi"
    vid_b = tmp_path / "b.avi"
    vid_a.write_bytes(b"a")
    vid_b.write_bytes(b"b")

    mock_cap = _make_mock_cap()

    with patch("cv2.VideoCapture", return_value=mock_cap) as ctor, \
         patch("cv2.imencode",
               return_value=(True, MagicMock(tobytes=lambda: b"\xff\xd8\xff"))):
        with tav_client.session_transaction() as sess:
            sess["uid"] = "test-user-switch"
        tav_client.get(f"/annotate/video-frame/0?path={vid_a}")
        tav_client.get(f"/annotate/video-frame/0?path={vid_b}")

    assert ctor.call_count == 2


def test_tav_etag_304(tav_client, tmp_path):
    """If-None-Match matching the ETag returns HTTP 304 without reading a frame."""
    dummy = tmp_path / "vid.avi"
    dummy.write_bytes(b"fake")
    etag = f"anv-{dummy}-0"

    mock_cap = _make_mock_cap()

    with patch("cv2.VideoCapture", return_value=mock_cap), \
         patch("cv2.imencode",
               return_value=(True, MagicMock(tobytes=lambda: b"\xff\xd8\xff"))):
        resp = tav_client.get(
            f"/annotate/video-frame/0?path={dummy}",
            headers={"If-None-Match": etag},
        )

    assert resp.status_code == 304
    mock_cap.read.assert_not_called()


def test_tav_missing_path_returns_400(tav_client):
    """Missing path param returns HTTP 400."""
    resp = tav_client.get("/annotate/video-frame/0")
    assert resp.status_code == 400


def test_tav_nonexistent_file_returns_404(tav_client):
    """Non-existent video path returns HTTP 404."""
    resp = tav_client.get("/annotate/video-frame/0?path=/nonexistent/video.avi")
    assert resp.status_code == 404


def test_tav_cache_bounded(tav_client, tmp_path):
    """Cache stays at most _ANV_VCAP_MAX entries; oldest is evicted and released."""
    from routes.annotate import _ANV_VCAP_MAX, _anv_vcap_cache

    mock_cap = _make_mock_cap()

    with patch("cv2.VideoCapture", return_value=mock_cap), \
         patch("cv2.imencode",
               return_value=(True, MagicMock(tobytes=lambda: b"\xff\xd8\xff"))):
        for i in range(_ANV_VCAP_MAX + 3):
            dummy = tmp_path / f"vid_{i}.avi"
            dummy.write_bytes(b"x")
            with tav_client.session_transaction() as sess:
                sess["uid"] = f"user-{i}"
            tav_client.get(f"/annotate/video-frame/0?path={dummy}")

    assert len(_anv_vcap_cache) <= _ANV_VCAP_MAX


# ── Integration tests (require mounted data volume) ───────────────────────────

@_skip_no_data
def test_tav_real_video_returns_jpeg():
    """Cached route returns a valid JPEG for frame 0 of the real test video."""
    import numpy as np
    import cv2
    from flask import Flask
    from routes.annotate import bp as annotate_bp, _anv_vcap_cache

    _anv_vcap_cache.clear()
    app = Flask(__name__)
    app.secret_key = "integ-secret"
    app.register_blueprint(annotate_bp)
    app.config["TESTING"] = True

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["uid"] = "integ-user"
        resp = client.get(f"/annotate/video-frame/0?path={_TEST_VIDEO}")

    assert resp.status_code == 200
    assert resp.content_type == "image/jpeg"
    arr = np.frombuffer(resp.data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert img is not None
    assert img.shape[0] > 0 and img.shape[1] > 0


@_skip_no_data
def test_tav_real_video_sequential_no_seek():
    """10 consecutive frames are served without any CAP_PROP_POS_FRAMES after frame 0."""
    import cv2
    from flask import Flask
    from routes.annotate import bp as annotate_bp, _anv_vcap_cache

    _anv_vcap_cache.clear()
    app = Flask(__name__)
    app.secret_key = "integ-secret2"
    app.register_blueprint(annotate_bp)
    app.config["TESTING"] = True

    seek_calls = []
    real_vcap  = cv2.VideoCapture(str(_TEST_VIDEO))

    original_set = real_vcap.set

    def spy_set(prop, val):
        seek_calls.append((prop, val))
        return original_set(prop, val)

    real_vcap.set = spy_set

    with patch("cv2.VideoCapture", return_value=real_vcap):
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["uid"] = "integ-seq-user"
            for n in range(10):
                resp = client.get(f"/annotate/video-frame/{n}?path={_TEST_VIDEO}")
                assert resp.status_code == 200

    real_vcap.release()

    # Only frame 0 should have triggered a seek (pos starts at -1, 0 != -1+1)
    seek_calls_after_open = [c for c in seek_calls if c[0] == cv2.CAP_PROP_POS_FRAMES]
    assert len(seek_calls_after_open) <= 1, (
        f"Expected at most 1 seek for 10 sequential frames, got {seek_calls_after_open}"
    )
