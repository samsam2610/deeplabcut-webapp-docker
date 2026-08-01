// relative_time.mjs — pure "how long ago" formatting for the Tracked Files list.
//
// `nowMs` is a parameter rather than a Date.now() call so the function is
// directly unit-testable. No DOM, no fetch.

export function formatRelative(iso, nowMs) {
  if (!iso) return "never opened";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "never opened";
  // Clamp: a clock skew between server and browser must never print "-3 min ago".
  const sec = Math.max(0, Math.round((nowMs - t) / 1000));
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} h ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day} d ago`;
  const mon = Math.floor(day / 30);
  if (mon < 12) return `${mon} mo ago`;
  return `${Math.floor(day / 365)} y ago`;
}
