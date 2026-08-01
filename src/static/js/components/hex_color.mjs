// hex_color.mjs — the one guard for user-chosen colours.
//
// Progress-bar option colours are user input and end up in
// style.setProperty(), where an unvalidated value is a CSS-injection vector.
// Every colour passes through here before it is stored or applied.

const HEX_COLOR_RE = /^#[0-9a-fA-F]{6}$/;

export function isValidHexColor(value) {
  return typeof value === "string" && HEX_COLOR_RE.test(value);
}

// ── Contrast ────────────────────────────────────────────────────────────────
// Option colours are chosen freely by the user, so text drawn on top of them
// cannot be a fixed colour: white on a pale yellow is unreadable, black on a
// dark navy equally so. These implement WCAG 2.x relative luminance and
// contrast ratio, and pick whichever of black/white reads better.

const BLACK = "#000000";
const WHITE = "#ffffff";

// WCAG relative luminance: sRGB channels linearised, then weighted for the
// eye's differing sensitivity (green dominates, blue barely registers).
function relativeLuminance(hex) {
  const channel = (offset) => {
    const c = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

// WCAG contrast ratio, 1 (identical) to 21 (black on white). Symmetric.
export function contrastRatio(hexA, hexB) {
  if (!isValidHexColor(hexA) || !isValidHexColor(hexB)) return 1;
  const a = relativeLuminance(hexA);
  const b = relativeLuminance(hexB);
  const lighter = Math.max(a, b);
  const darker = Math.min(a, b);
  return (lighter + 0.05) / (darker + 0.05);
}

// The more readable of black/white for text on `background`.
// Falls back to white for anything malformed, matching how callers treat an
// invalid colour elsewhere (they skip styling rather than trust the value).
export function readableTextColor(background) {
  if (!isValidHexColor(background)) return WHITE;
  return contrastRatio(background, BLACK) >= contrastRatio(background, WHITE)
    ? BLACK
    : WHITE;
}
