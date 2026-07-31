"""Closing the main webapp's inline-analysis card must not discard queued work.

The /session/stop route treats an ABSENT only_if_idle as an EXPLICIT cancel,
which sets the stop key AND deletes the queue. Both close paths in
inline_analysis_player.js therefore have to send only_if_idle: true, or every
tab close destroys a running Analyze-for-tag batch.
"""
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src/static/js/inline_analysis_player.js"


@pytest.fixture(scope="module")
def js():
    return JS.read_text()


def _stop_call_bodies(src):
    """Every JSON body sent to the session/stop endpoint."""
    out = []
    for i in range(len(src)):
        i = src.find("inline-analysis/session/stop", i)
        if i == -1:
            break
        out.append(src[i:i + 400])
    return out


def test_there_are_stop_calls_to_check(js):
    """Guard the guard: if the extraction finds nothing it must not pass."""
    assert len(_stop_call_bodies(js)) >= 2, "expected the close + beforeunload calls"


def test_every_close_path_sends_only_if_idle(js):
    for frag in _stop_call_bodies(js):
        assert "only_if_idle: true" in frag, (
            "a session/stop call omits only_if_idle -> the route treats it as an "
            "explicit cancel and DELETES the queued ranges:\n" + frag[:200])
