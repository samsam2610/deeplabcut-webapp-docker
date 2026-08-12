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


def test_a_frame_with_no_row_is_refused_not_invented(flask_test_client, companion):
    """Every frame of a real recording already has a row — create-csv seeds
    1..frame_count and the acquisition system writes them all. So a missing row
    means the frame is out of range or the file is truncated, and computing a
    timestamp for it would paper over exactly that."""
    client = flask_test_client[0]
    resp = _save(client, companion, 50000, note="new")
    assert resp.status_code == 404
    assert "50000" in resp.get_json()["error"]
    assert 50000 not in _rows(companion), "no row may be invented"


def test_the_refusal_says_what_range_the_file_covers(flask_test_client, companion):
    client = flask_test_client[0]
    err = _save(client, companion, 50000).get_json()["error"]
    assert "44385" in err and "44387" in err


def test_an_existing_row_is_never_refused(flask_test_client, companion):
    assert _save(flask_test_client[0], companion, 44385, note="x").status_code == 200


def test_a_blank_stored_timestamp_is_preserved_not_filled_in(flask_test_client,
                                                             tmp_path):
    """Still no invention. A blank is what the file says, and writing frame/fps
    over it would be the same fabrication in a quieter place."""
    path = tmp_path / "v.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        w.writerow({"timestamp": "", "frame_number": 10,
                    "frame_line_status": "0", "note": ""})
    client = flask_test_client[0]
    assert _save(client, path, 10, note="x").status_code == 200
    got = _rows(path)[10]
    assert got["note"] == "x"
    assert got["timestamp"] == ""


def test_a_missing_csv_is_refused_rather_than_conjured(flask_test_client, tmp_path):
    """create-csv exists for that. save-row inventing a one-row file would
    produce a companion the acquisition system never wrote."""
    resp = _save(flask_test_client[0], tmp_path / "nope.csv", 10, note="x")
    assert resp.status_code == 404
    assert not (tmp_path / "nope.csv").exists()


def test_editing_the_same_row_twice_does_not_erode_it(flask_test_client, companion):
    """Precision must not be lost on the second save either — the first fix
    could have preserved it once and then locked in its own output."""
    client = flask_test_client[0]
    _save(client, companion, 44386, note="one")
    _save(client, companion, 44386, note="two")
    assert _rows(companion)[44386]["timestamp"] == PRECISE[44386]
