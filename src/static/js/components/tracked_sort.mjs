// tracked_sort.mjs — ordering for the tracked-files list.
//
// Pure and DOM-free: the list is sorted client-side over rows the server
// already returned, so no round trip is involved and this is directly
// unit-testable.

// Recording timestamp embedded in the filename, e.g.
//   khoai-lang-2_cam0_20260507_105538_1_trig1_fps200_exposure1500_gain10.avi
//                     ^^^^^^^^ ^^^^^^
// The 8-then-6 digit shape cannot be produced by fps200 / exposure1500 /
// gain10, and the lookahead keeps it anchored to a field boundary.
const STAMP_RE = /_(\d{8})_(\d{6})(?=_|\.|$)/;

export const DEFAULT_DIRECTION = {
  name: "asc",        // alphabetical is the useful default for a name
  recorded: "desc",   // newest session first
  opened: "desc",     // most recently opened first
};

export function parseRecordedAt(name) {
  if (typeof name !== "string") return null;
  const m = STAMP_RE.exec(name);
  if (!m) return null;
  const [d, t] = [m[1], m[2]];
  // UTC, not local: a daylight-saving transition must not reorder a list.
  const ms = Date.UTC(
    Number(d.slice(0, 4)), Number(d.slice(4, 6)) - 1, Number(d.slice(6, 8)),
    Number(t.slice(0, 2)), Number(t.slice(2, 4)), Number(t.slice(4, 6)),
  );
  return Number.isNaN(ms) ? null : ms;
}

function _key(row, field) {
  if (field === "name") return (row.name || "").toLowerCase();
  if (field === "recorded") return parseRecordedAt(row.name);
  if (field === "opened") {
    const t = Date.parse(row.last_opened_at || "");
    return Number.isNaN(t) ? null : t;
  }
  return undefined;
}

export function sortTrackedFiles(rows, field, direction) {
  const list = [...(rows || [])];
  if (!["name", "recorded", "opened"].includes(field)) return list;
  const sign = direction === "asc" ? 1 : -1;
  const byName = (a, b) =>
    (a.name || "").toLowerCase().localeCompare((b.name || "").toLowerCase());

  return list.sort((a, b) => {
    const ka = _key(a, field);
    const kb = _key(b, field);
    // Rows with no value sink to the bottom in BOTH directions: flipping the
    // sort to surface the files that have no date would be actively unhelpful.
    const na = ka === null || ka === undefined;
    const nb = kb === null || kb === undefined;
    if (na && nb) return byName(a, b);
    if (na) return 1;
    if (nb) return -1;
    if (ka < kb) return -1 * sign;
    if (ka > kb) return 1 * sign;
    return byName(a, b);
  });
}
