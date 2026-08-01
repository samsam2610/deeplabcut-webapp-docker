// hex_color.mjs — the one guard for user-chosen colours.
//
// Progress-bar option colours are user input and end up in
// style.setProperty(), where an unvalidated value is a CSS-injection vector.
// Every colour passes through here before it is stored or applied.

const HEX_COLOR_RE = /^#[0-9a-fA-F]{6}$/;

export function isValidHexColor(value) {
  return typeof value === "string" && HEX_COLOR_RE.test(value);
}
