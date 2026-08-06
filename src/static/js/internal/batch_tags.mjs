// Tag list + selection for the Batch Analyze panel.
//
// Chips ARE the selection: clicking one toggles whether that tag is part of the
// next "Analyze for tag" run. The text field only mints new chips. Keeping this
// pure — no DOM, no fetch — is what makes the rules testable, because the rules
// are where the mistakes live (silent duplicates, a run submitting the field
// instead of the selection, a stale selection surviving a reload).
//
// The list persists per project under the `batch_tags` ui-setting. The
// SELECTION deliberately does not: a batch is expensive and deliberate, and a
// stale selection quietly firing off 200k frames is worse than re-picking.
"use strict";

/**
 * Add `raw` to `tags`, returning a NEW array.
 *
 * A value equal to an existing tag is silently ignored — no duplicate, no
 * error. Duplicate chips would be indistinguishable in the UI and would submit
 * the same tag twice, so there is nothing to warn about.
 *
 * Not comma-split: tags are matched exactly against the CSV `note` column, and
 * splitting would mangle a tag that legitimately contains a comma.
 *
 * @returns {{tags: string[], added: boolean, reason: ""|"empty"|"duplicate"}}
 */
export function addTag(tags, raw) {
  const list = Array.isArray(tags) ? tags : [];
  const value = String(raw == null ? "" : raw).trim();
  if (!value) return { tags: list, added: false, reason: "empty" };
  if (list.includes(value)) return { tags: list, added: false, reason: "duplicate" };
  return { tags: [...list, value], added: true, reason: "" };
}

/** Remove `tag` from both the list and the selection. */
export function removeTag(tags, selected, tag) {
  return {
    tags: (tags || []).filter((t) => t !== tag),
    selected: (selected || []).filter((t) => t !== tag),
  };
}

/**
 * Toggle one tag's membership of the selection, preserving LIST order.
 *
 * Order matters: the confirm dialog names the tags, and a selection ordered by
 * click would read differently each time for the same run.
 */
export function toggleSelected(tags, selected, tag) {
  const list = Array.isArray(tags) ? tags : [];
  if (!list.includes(tag)) return [...(selected || [])];
  const cur = new Set(selected || []);
  if (cur.has(tag)) cur.delete(tag);
  else cur.add(tag);
  return list.filter((t) => cur.has(t));
}

/**
 * The tags a run would submit: selected chips that still exist, in list order.
 *
 * Guards the case where a chip is deleted while selected — the deleted tag must
 * not reach the payload just because the selection array still names it.
 */
export function submittedTags(tags, selected) {
  const chosen = new Set(selected || []);
  return (Array.isArray(tags) ? tags : []).filter((t) => chosen.has(t));
}

/** Whether "Analyze for tag" may run. */
export function canRunForTag(tags, selected) {
  return submittedTags(tags, selected).length > 0;
}

/** Parse the persisted `batch_tags` value into a string array. */
export function parseStored(raw) {
  try {
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    const out = [];
    for (const t of parsed) {
      const v = String(t == null ? "" : t).trim();
      if (v && !out.includes(v)) out.push(v);   // heal duplicates written earlier
    }
    return out;
  } catch (_) {
    return [];
  }
}
