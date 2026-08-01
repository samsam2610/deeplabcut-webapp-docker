import test from "node:test";
import assert from "node:assert/strict";
import { readableTextColor, contrastRatio } from "../../static/js/components/hex_color.mjs";

test("contrastRatio: identical colours have ratio 1, black vs white is 21", () => {
  assert.equal(Math.round(contrastRatio("#ffffff", "#ffffff")), 1);
  assert.equal(Math.round(contrastRatio("#000000", "#ffffff")), 21);
  // Symmetric — order must not matter.
  assert.equal(contrastRatio("#2ea043", "#ffffff"), contrastRatio("#ffffff", "#2ea043"));
});

test("readableTextColor: unambiguous extremes", () => {
  assert.equal(readableTextColor("#ffffff"), "#000000");
  assert.equal(readableTextColor("#000000"), "#ffffff");
});

test("readableTextColor: light backgrounds get black text", () => {
  // Yellow is the classic case where hard-coded white text fails.
  for (const bg of ["#ffff00", "#e0a800", "#f5f5f5", "#c8e6c9", "#ffd166"]) {
    assert.equal(readableTextColor(bg), "#000000", `${bg} should take black text`);
  }
});

test("readableTextColor: dark backgrounds get white text", () => {
  for (const bg of ["#1a1a1a", "#2b3a67", "#7b1fa2", "#0b3d2e"]) {
    assert.equal(readableTextColor(bg), "#ffffff", `${bg} should take white text`);
  }
});

test("readableTextColor: always picks whichever of black/white contrasts more", () => {
  const samples = [
    "#2ea043", "#888888", "#e0a800", "#ffffff", "#000000", "#ff0000",
    "#00ff00", "#0000ff", "#7f7f7f", "#808080", "#123456", "#fedcba",
  ];
  for (const bg of samples) {
    const chosen = readableTextColor(bg);
    const other = chosen === "#000000" ? "#ffffff" : "#000000";
    assert.ok(
      contrastRatio(bg, chosen) >= contrastRatio(bg, other),
      `${bg}: chose ${chosen} (${contrastRatio(bg, chosen).toFixed(2)}) over ` +
      `${other} (${contrastRatio(bg, other).toFixed(2)})`,
    );
  }
});

test("readableTextColor: every valid colour clears WCAG AA for large text (3:1)", () => {
  // Picking the better of black/white always beats 3:1 — the worst case is a
  // mid-grey, which still lands around 4.5 against one of them.
  for (const bg of ["#767676", "#808080", "#7f7f7f", "#949494", "#606060"]) {
    assert.ok(contrastRatio(bg, readableTextColor(bg)) >= 3,
      `${bg} only reached ${contrastRatio(bg, readableTextColor(bg)).toFixed(2)}:1`);
  }
});

test("readableTextColor: falls back to white on malformed input", () => {
  for (const bad of [null, undefined, "red", "#fff", "#fff; background:url(x)", 42]) {
    assert.equal(readableTextColor(bad), "#ffffff");
  }
});

test("readableTextColor: case-insensitive", () => {
  assert.equal(readableTextColor("#FFFF00"), readableTextColor("#ffff00"));
});
