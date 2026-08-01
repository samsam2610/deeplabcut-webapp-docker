import test from "node:test";
import assert from "node:assert/strict";
import { isValidHexColor } from "../../static/js/components/hex_color.mjs";

test("isValidHexColor: accepts #rrggbb in either case", () => {
  assert.equal(isValidHexColor("#2ea043"), true);
  assert.equal(isValidHexColor("#2EA043"), true);
});

test("isValidHexColor: rejects anything that is not exactly #rrggbb", () => {
  assert.equal(isValidHexColor("#fff"), false);       // shorthand
  assert.equal(isValidHexColor("red"), false);        // named
  assert.equal(isValidHexColor("2ea043"), false);     // no hash
  assert.equal(isValidHexColor("#2ea0433"), false);   // too long
});

test("isValidHexColor: rejects CSS-injection payloads", () => {
  assert.equal(isValidHexColor("#fff; background:url(evil)"), false);
  assert.equal(isValidHexColor("}body{display:none}"), false);
});

test("isValidHexColor: rejects non-strings", () => {
  assert.equal(isValidHexColor(null), false);
  assert.equal(isValidHexColor(undefined), false);
  assert.equal(isValidHexColor(0x2ea043), false);
});
