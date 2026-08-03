import test from "node:test";
import assert from "node:assert/strict";
import { parseRecordedAt, sortTrackedFiles, DEFAULT_DIRECTION }
  from "../../static/js/components/tracked_sort.mjs";

const REAL = "khoai-lang-2_cam0_20260507_105538_1_trig1_fps200_exposure1500_gain10.avi";

test("parseRecordedAt: pulls the recording timestamp out of a real filename", () => {
  assert.equal(parseRecordedAt(REAL), Date.UTC(2026, 4, 7, 10, 55, 38));
});

test("parseRecordedAt: returns null when there is no timestamp", () => {
  assert.equal(parseRecordedAt("session-one.avi"), null);
  assert.equal(parseRecordedAt(""), null);
  assert.equal(parseRecordedAt(null), null);
});

test("parseRecordedAt: camera settings cannot false-match the 8-then-6 shape", () => {
  assert.equal(parseRecordedAt("cam0_fps200_exposure1500_gain10.avi"), null);
  assert.equal(parseRecordedAt("rec_12345678.avi"), null);      // 8 digits, no 6-digit half
});

test("parseRecordedAt: accepts a timestamp at the very end of the stem", () => {
  assert.equal(parseRecordedAt("s_20260507_105538.avi"), Date.UTC(2026, 4, 7, 10, 55, 38));
});

const ROWS = [
  { name: "b_20260101_000000_x.avi", last_opened_at: "2026-07-02T10:00:00Z" },
  { name: "a_20260301_000000_x.avi", last_opened_at: null },
  { name: "c_20260201_000000_x.avi", last_opened_at: "2026-07-05T10:00:00Z" },
  { name: "d-no-timestamp.avi",      last_opened_at: "2026-07-01T10:00:00Z" },
];
const names = (rows) => rows.map((r) => r.name[0]);

test("sortTrackedFiles: by name, both directions", () => {
  assert.deepEqual(names(sortTrackedFiles(ROWS, "name", "asc")), ["a", "b", "c", "d"]);
  assert.deepEqual(names(sortTrackedFiles(ROWS, "name", "desc")), ["d", "c", "b", "a"]);
});

test("sortTrackedFiles: by recording date, unparseable last in BOTH directions", () => {
  assert.deepEqual(names(sortTrackedFiles(ROWS, "recorded", "asc")),  ["b", "c", "a", "d"]);
  assert.deepEqual(names(sortTrackedFiles(ROWS, "recorded", "desc")), ["a", "c", "b", "d"]);
});

test("sortTrackedFiles: by last opened, never-opened last in BOTH directions", () => {
  assert.deepEqual(names(sortTrackedFiles(ROWS, "opened", "desc")), ["c", "b", "d", "a"]);
  assert.deepEqual(names(sortTrackedFiles(ROWS, "opened", "asc")),  ["d", "b", "c", "a"]);
});

test("sortTrackedFiles: ties break on filename, case-insensitively", () => {
  const tied = [
    { name: "Zebra.avi", last_opened_at: null },
    { name: "apple.avi", last_opened_at: null },
    { name: "Mango.avi", last_opened_at: null },
  ];
  assert.deepEqual(sortTrackedFiles(tied, "opened", "desc").map((r) => r.name),
                   ["apple.avi", "Mango.avi", "Zebra.avi"]);
});

test("sortTrackedFiles: does not mutate its input", () => {
  const before = ROWS.map((r) => r.name);
  sortTrackedFiles(ROWS, "name", "desc");
  assert.deepEqual(ROWS.map((r) => r.name), before);
});

test("sortTrackedFiles: an unknown field leaves the order untouched", () => {
  assert.deepEqual(names(sortTrackedFiles(ROWS, "nonsense", "asc")), ["b", "a", "c", "d"]);
});

test("DEFAULT_DIRECTION: alphabetical for names, newest-first for dates", () => {
  assert.equal(DEFAULT_DIRECTION.name, "asc");
  assert.equal(DEFAULT_DIRECTION.recorded, "desc");
  assert.equal(DEFAULT_DIRECTION.opened, "desc");
});
