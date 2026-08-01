import test from "node:test";
import assert from "node:assert/strict";
import { formatRelative } from "../../static/js/components/relative_time.mjs";

const NOW = Date.parse("2026-07-31T12:00:00Z");

test("formatRelative: null/empty/garbage timestamps read as never opened", () => {
  assert.equal(formatRelative(null, NOW), "never opened");
  assert.equal(formatRelative("", NOW), "never opened");
  assert.equal(formatRelative("not-a-date", NOW), "never opened");
});

test("formatRelative: under a minute is 'just now'", () => {
  assert.equal(formatRelative("2026-07-31T12:00:00Z", NOW), "just now");
  assert.equal(formatRelative("2026-07-31T11:59:31Z", NOW), "just now");
});

test("formatRelative: minutes, hours, days", () => {
  assert.equal(formatRelative("2026-07-31T11:58:00Z", NOW), "2 min ago");
  assert.equal(formatRelative("2026-07-31T10:00:00Z", NOW), "2 h ago");
  assert.equal(formatRelative("2026-07-28T12:00:00Z", NOW), "3 d ago");
});

test("formatRelative: months and years", () => {
  assert.equal(formatRelative("2026-05-31T12:00:00Z", NOW), "2 mo ago");
  assert.equal(formatRelative("2024-07-31T12:00:00Z", NOW), "2 y ago");
});

test("formatRelative: a future timestamp clamps to 'just now', never negative", () => {
  assert.equal(formatRelative("2026-07-31T12:05:00Z", NOW), "just now");
});
