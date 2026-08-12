"""POST /annotate/save-row must not rewrite a row's timestamp.

Found 2026-08-12 on a real recording. A hand-placed `start-success` at frame
44386 left that row reading

    221.930,44386,10,start-success

while its neighbours carried `221.9147631` and `221.924762874`. The endpoint
recomputed the timestamp as ``frame / fps`` to three decimals and wrote that
over the acquisition time the camera actually recorded.

Only the edited row is affected, so it is silent: one row in 250 158 loses its
precision per saved annotation, and these files are annotated ~85 times each.

Both the note and the status buttons route here, so both did it.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

HEADER = ["timestamp", "frame_number", "frame_line_status", "note"]

# Real values from the recording that exposed this.
PRECISE = {
    44385: "221.9147631",
    44386: "221.919762",
    44387: "221.924762874",
}


@pytest.fixture
def companion(tmp_path):
    path = tmp_path / "vid.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        for frame, ts in PRECISE.items():
            w.writerow({"timestamp": ts, "frame_number": frame,
                        "frame_line_status": "10", "note": ""})
    return path


def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return {int(r["frame_number"]): r for r in csv.DictReader(fh)}


def _save(client, path, frame, note="", status="10", fps=200.0):
    return client.post("/annotate/save-row", data=json.dumps({
        "csv_path": str(path), "frame_number": frame, "note": note,
        "frame_line_status": status, "fps": fps,
    }), content_type="application/json")


def test_saving_a_note_keeps_the_rows_own_timestamp(flask_test_client, companion):
    client = flask_test_client[0]
    resp = _save(client, companion, 44386, note="start-success")
    assert resp.status_code == 200
    row = _rows(companion)[44386]
    assert row["note"] == "start-success"
    assert row["timestamp"] == PRECISE[44386], "acquisition time was overwritten"


def test_saving_a_status_keeps_the_timestamp_too(flask_test_client, companion):
    """The status button routes through the same endpoint, so it had the same
    defect — fixing only the note path would have left half of it."""
    client = flask_test_client[0]
    _save(client, companion, 44386, status="14")
    row = _rows(companion)[44386]
    assert row["frame_line_status"] == "14"
    assert row["timestamp"] == PRECISE[44386]


def test_neighbouring_rows_are_untouched(flask_test_client, companion):
    client = flask_test_client[0]
    _save(client, companion, 44386, note="x")
    got = _rows(companion)
    assert got[44385]["timestamp"] == PRECISE[44385]
    assert got[44387]["timestamp"] == PRECISE[44387]


def test_the_returned_row_reports_the_stored_timestamp(flask_test_client, companion):
    """The caller writes this straight into its in-memory table, so returning a
    recomputed value would put the wrong one on screen even once the file is
    right."""
    client = flask_test_client[0]
    resp = _save(client, companion, 44386, note="x")
    assert resp.get_json()["row"]["timestamp"] == PRECISE[44386]


def test_a_row_that_does_not_exist_yet_still_gets_a_timestamp(flask_test_client,
                                                              companion):
    """There is nothing to preserve for a new row, so frame/fps remains the only
    thing available — this path must keep working."""
    client = flask_test_client[0]
    _save(client, companion, 50000, note="new")
    row = _rows(companion)[50000]
    assert row["note"] == "new"
    assert row["timestamp"] == f"{50000 / 200.0:.3f}"


def test_a_blank_stored_timestamp_falls_back_to_computing_one(flask_test_client,
                                                              tmp_path):
    path = tmp_path / "v.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        w.writerow({"timestamp": "", "frame_number": 10,
                    "frame_line_status": "0", "note": ""})
    client = flask_test_client[0]
    _save(client, path, 10, note="x")
    assert _rows(path)[10]["timestamp"] == f"{10 / 200.0:.3f}"


def test_editing_the_same_row_twice_does_not_erode_it(flask_test_client, companion):
    """Precision must not be lost on the second save either — the first fix
    could have preserved it once and then locked in its own output."""
    client = flask_test_client[0]
    _save(client, companion, 44386, note="one")
    _save(client, companion, 44386, note="two")
    assert _rows(companion)[44386]["timestamp"] == PRECISE[44386]
