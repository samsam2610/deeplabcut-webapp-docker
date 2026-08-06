import test from "node:test";
import assert from "node:assert/strict";
import {
  addTag, removeTag, toggleSelected, submittedTags, canRunForTag, parseStored,
} from "../../static/js/internal/batch_tags.mjs";

// Real tags from the DREADD-Ali companion CSVs.
const TAGS = ["start-success", "not-good", "start-failure"];

test("adding appends and reports success", () => {
  const r = addTag(TAGS, "new-tag");
  assert.deepEqual(r.tags, [...TAGS, "new-tag"]);
  assert.equal(r.added, true);
  assert.equal(r.reason, "");
});

test("a duplicate is silently ignored", () => {
  // Silently, per the spec: two identical chips would be indistinguishable in
  // the UI and would submit the same tag twice.
  const r = addTag(TAGS, "not-good");
  assert.deepEqual(r.tags, TAGS, "list must be untouched");
  assert.equal(r.added, false);
  assert.equal(r.reason, "duplicate");
});

test("a duplicate is detected after trimming", () => {
  assert.equal(addTag(TAGS, "  not-good  ").reason, "duplicate");
});

test("duplicate detection is case-sensitive", () => {
  // Tags are matched EXACTLY against the CSV note column, so "Not-Good" is a
  // different tag from "not-good" — refusing it would block a legitimate one.
  assert.equal(addTag(TAGS, "Not-Good").added, true);
});

test("empty or whitespace-only input is ignored", () => {
  for (const bad of ["", "   ", null, undefined]) {
    const r = addTag(TAGS, bad);
    assert.equal(r.added, false, String(bad));
    assert.equal(r.reason, "empty");
    assert.deepEqual(r.tags, TAGS);
  }
});

test("a value containing a comma becomes ONE tag", () => {
  // Not split: exact match is the rule, so splitting would mangle it.
  const r = addTag([], "a,b");
  assert.deepEqual(r.tags, ["a,b"]);
});

test("the input list is never mutated", () => {
  const original = [...TAGS];
  addTag(TAGS, "x");
  assert.deepEqual(TAGS, original);
});

test("toggling selects and deselects", () => {
  let sel = toggleSelected(TAGS, [], "not-good");
  assert.deepEqual(sel, ["not-good"]);
  sel = toggleSelected(TAGS, sel, "not-good");
  assert.deepEqual(sel, []);
});

test("several tags can be selected at once", () => {
  let sel = toggleSelected(TAGS, [], "start-failure");
  sel = toggleSelected(TAGS, sel, "start-success");
  assert.deepEqual(sel, ["start-success", "start-failure"],
    "selection follows LIST order, not click order");
});

test("selection order is stable regardless of click order", () => {
  const a = toggleSelected(TAGS, toggleSelected(TAGS, [], "start-failure"), "start-success");
  const b = toggleSelected(TAGS, toggleSelected(TAGS, [], "start-success"), "start-failure");
  assert.deepEqual(a, b, "the confirm dialog names these; it must read the same");
});

test("toggling an unknown tag changes nothing", () => {
  assert.deepEqual(toggleSelected(TAGS, ["not-good"], "ghost"), ["not-good"]);
});

test("removing a tag drops it from the selection too", () => {
  const r = removeTag(TAGS, ["not-good", "start-failure"], "not-good");
  assert.deepEqual(r.tags, ["start-success", "start-failure"]);
  assert.deepEqual(r.selected, ["start-failure"]);
});

test("a deleted tag can never reach the payload", () => {
  // Defence in depth: even if the selection array still names it.
  assert.deepEqual(submittedTags(["a", "b"], ["a", "gone"]), ["a"]);
});

test("submitted tags come from the selection, in list order", () => {
  assert.deepEqual(submittedTags(TAGS, ["start-failure", "start-success"]),
                   ["start-success", "start-failure"]);
});

test("analyze-for-tag is blocked until something is selected", () => {
  assert.equal(canRunForTag(TAGS, []), false);
  assert.equal(canRunForTag(TAGS, ["ghost"]), false, "a stale name must not enable it");
  assert.equal(canRunForTag(TAGS, ["not-good"]), true);
});

test("stored values parse, and earlier duplicates are healed", () => {
  assert.deepEqual(parseStored(JSON.stringify(["a", "a", " b ", ""])), ["a", "b"]);
});

test("malformed storage yields an empty list rather than throwing", () => {
  for (const bad of ["", null, "not json", '{"a":1}', "[1,2]"]) {
    assert.doesNotThrow(() => parseStored(bad), String(bad));
  }
  assert.deepEqual(parseStored("not json"), []);
  assert.deepEqual(parseStored('{"a":1}'), []);
  assert.deepEqual(parseStored("[1,2]"), ["1", "2"]);
});
