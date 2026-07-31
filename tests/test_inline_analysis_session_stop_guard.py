"""Route-level tests for the /session/stop `only_if_idle` guard.

Bug being fixed: `beforeunload` / card-close send an unconditional stop,
which races a warm Celery session's queue-drain loop — closing ANY tab
sharing the (deterministic) snap_key kills a batch analysis started from
ANY card, stranding the rest of the queue forever. See
docs/superpowers/session-stop-guard-report.md (dlc-3D repo) for the full
diagnosis.

Follows the fixture style of tests/test_inline_analysis_peaks_route.py: a
minimal Flask app registering only `dlc.inline_analysis.bp`, `dlc.ctx`
populated directly, and the repo's existing session-scoped `fake_redis`
fixture (tests/conftest.py) — no `dlc_sandbox_project` (multi-GB project
copy) and no `flask_test_client`/`ia_client` fixtures, per the disk-fill
hazard noted in docs/superpowers/feedback_test_disk_fill.md.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dlc import ctx as _ctx  # noqa: E402
import dlc.inline_analysis as inline_analysis  # noqa: E402


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def user_data_dir(tmp_path):
    d = tmp_path / "user-data"
    d.mkdir()
    return d


@pytest.fixture
def client(data_dir, user_data_dir, fake_redis):
    from flask import Flask

    # fake_redis is session-scoped — clear it so state doesn't leak between
    # tests in this file or from other test modules.
    fake_redis._store.clear()
    fake_redis._hstore.clear()
    fake_redis._zsets.clear()
    fake_redis._sets.clear()
    fake_redis._lists.clear()

    _ctx.setup(data_dir, user_data_dir, fake_redis, None, None, None)

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(inline_analysis.bp)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


@pytest.fixture
def uid(client):
    with client.session_transaction() as sess:
        sess["uid"] = "stop-guard-uid"
    return "stop-guard-uid"


def _stop(client, **body):
    return client.post("/dlc/project/inline-analysis/session/stop", json=body)


def test_only_if_idle_with_nonempty_queue_declines_the_stop(client, fake_redis, uid):
    """A tab-close arriving mid-batch must NOT kill the session — the queue
    still has ranges other tabs/cards may be relying on."""
    snap_key = "sk-busy"
    queue_key = f"inline:queue:{uid}:{snap_key}"
    fake_redis.rpush(queue_key, "range-1", "range-2", "range-3")

    resp = _stop(client, snap_key=snap_key, only_if_idle=True)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["stopped"] is False
    assert body["pending"] == 3
    assert fake_redis.get(f"inline:control:{uid}:{snap_key}") is None
    assert fake_redis.llen(queue_key) == 3, "queue must be left untouched"


def test_only_if_idle_with_empty_queue_stops(client, fake_redis, uid):
    """No pending work behind this session — safe to actually stop it."""
    snap_key = "sk-idle"

    resp = _stop(client, snap_key=snap_key, only_if_idle=True)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["stopped"] is True
    assert body["pending"] == 0
    assert fake_redis.get(f"inline:control:{uid}:{snap_key}") == "stop"


def test_explicit_stop_sets_control_key_and_deletes_the_queue(client, fake_redis, uid):
    """A deliberate cancel (only_if_idle absent) must not orphan the queue
    either — that's the OTHER half of the original bug (522 stranded
    requests came from explicit stops too)."""
    snap_key = "sk-cancel"
    queue_key = f"inline:queue:{uid}:{snap_key}"
    fake_redis.rpush(queue_key, "range-1", "range-2")

    resp = _stop(client, snap_key=snap_key)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["stopped"] is True
    assert body["cleared"] == 2
    assert fake_redis.get(f"inline:control:{uid}:{snap_key}") == "stop"
    assert fake_redis.llen(queue_key) == 0


def test_explicit_stop_default_false_matches_absent_flag(client, fake_redis, uid):
    """only_if_idle: false behaves identically to the flag being absent."""
    snap_key = "sk-cancel-explicit-false"
    queue_key = f"inline:queue:{uid}:{snap_key}"
    fake_redis.rpush(queue_key, "range-1")

    resp = _stop(client, snap_key=snap_key, only_if_idle=False)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["stopped"] is True
    assert body["cleared"] == 1
    assert fake_redis.llen(queue_key) == 0


def test_missing_snap_key_is_still_a_400(client):
    resp = _stop(client, only_if_idle=True)
    assert resp.status_code == 400

    resp2 = _stop(client)
    assert resp2.status_code == 400
