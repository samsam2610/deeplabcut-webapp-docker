// Choose the "Primary layer (.h5)" for the kinematic-marker overlay.
//
// Rule: the PINNED model if its h5 is present for this video, otherwise the
// latest. Pinning a snapshot is how the user says "analyse with this model";
// having the overlay then default to a different one — usually whatever was
// analysed most recently — quietly shows markers from a model they did not
// choose, which is exactly the comparison mistake pinning exists to prevent.
//
// Matching is by filename, because that is all the variant list carries. DLC
// bakes the model into the scorer suffix:
//
//   pin   dlc-models-pytorch/iteration-24/…/train/snapshot-best-150.pt
//   h5    <stem>DLC_HrnetW48_DREADDJan7shuffle1_iter24_snapshot_best-150.h5
//
// Note the two transforms that make a naive string compare fail: `iteration-24`
// becomes `iter24`, and the first hyphen of the snapshot stem becomes an
// underscore (`snapshot-best-150` -> `snapshot_best-150`).
"use strict";

/**
 * Identifying tokens for a pinned snapshot path, or null if it is not one.
 * @param {string} pinnedPath project-relative .pt path
 * @returns {{iter: string|null, snap: string}|null}
 */
export function pinnedScorerTokens(pinnedPath) {
  if (!pinnedPath || typeof pinnedPath !== "string") return null;
  const base = pinnedPath.split("/").pop() || "";
  const stem = base.replace(/\.pt$/i, "");
  if (!/^snapshot[-_]/.test(stem)) return null;
  const iterMatch = /(?:^|\/)iteration-(\d+)(?:\/|$)/.exec(pinnedPath);
  return {
    iter: iterMatch ? `iter${iterMatch[1]}` : null,
    snap: stem.replace(/^snapshot-/, "snapshot_"),
  };
}

/**
 * Does this variant's path come from the pinned snapshot?
 *
 * When the pin names an iteration, the h5 must carry the matching `_iterNN_`.
 * That is deliberately strict: an h5 written before iteration tagging existed
 * cannot be shown to be the same model, and silently accepting it would
 * reintroduce the ambiguity pinning removes. Such a video simply falls back
 * to "latest".
 */
export function matchesPinned(variantPath, tokens) {
  if (!tokens || !variantPath || typeof variantPath !== "string") return false;
  if (!variantPath.includes(tokens.snap)) return false;
  if (tokens.iter && !variantPath.includes(`_${tokens.iter}_`)) return false;
  return true;
}

/**
 * The variant to select: pinned if present, else `fallback(variants)`.
 *
 * `fallback` stays injectable because each card already has its own "latest"
 * rule — the 3D cards exclude the curated `_analyzed` output, the 2D card
 * prefers dated postproc runs — and this must not quietly change any of them.
 *
 * @param {Array} variants   /dlc/viewer/h5-variants entries
 * @param {string} pinnedPath persisted `pinned_snapshot`, or "" when unpinned
 * @param {(v: Array) => any} fallback
 */
export function pickPrimaryVariant(variants, pinnedPath, fallback) {
  const all = Array.isArray(variants) ? variants : [];
  const tokens = pinnedScorerTokens(pinnedPath);
  if (tokens) {
    const hit = all.find(
      (v) => v && !v.disabled && matchesPinned(v.path || "", tokens));
    if (hit) return hit;
  }
  return typeof fallback === "function" ? fallback(all) : null;
}
