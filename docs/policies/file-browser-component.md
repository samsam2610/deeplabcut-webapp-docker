Parallel to dlc-3D's policy at `../../../deeplabcut-webapp-docker-supports/docs/policies/file-browser-component.md`; the two stay in sync deliberately.

# Policy: file-browser component

**TL;DR:** Every multi-select directory-tree file picker in the main webapp's frontend MUST use `src/static/js/components/file_browser.js`. Do not write a new one.

## The canonical component

Path: `src/static/js/components/file_browser.js`
Export: `makeFileBrowser({ inputEl, paneEl, dirOnly?, fileFilter?, onPick? })`

It owns:
- Single-click highlighting + recursive folder expansion
- File-type filtering (video + image extensions by default; pass `fileFilter` to override; centralise extension changes there)
- The double-click "add to queue" UX, including the transient "Added ✓" badge that fades out without closing the browser
- The `file-browser:pick` and (legacy) `lp-picker-dblclick` events dispatched on the pane

## Consumers

The following cards use the canonical component (and are guarded by `tests/test_file_browser_policy.py`):

- `src/static/js/analyze.js` — file picker (videos/images) and destfolder picker (dir-only)
- `src/static/js/viewer.js` — H5 picker (`.h5` files only)
- `src/static/js/annotator.js` — folder browser (dir-only) and clip browser (videos)
- `src/static/js/postprocess.js` — folder picker (dir-only)

## When to add a new browser

Only when your UX legitimately differs from "user picks one or many files/folders from a tree." Examples that justify a separate widget:
- A single-pick browser tied to a project/session selector (different shape, no queue).
- A grid-based image gallery (different layout, different selection semantics).

If you're tempted to copy `file_browser.js` and "tweak a few things," stop and extend the canonical component instead — add an option to its config object.

## Why this rule exists

Prior to this policy six near-identical implementations existed across `analyze.js`, `viewer.js`, `annotator.js`, and `postprocess.js`. A bug fix (double-click was collapsing the browser instead of just adding to queue) had to land in all of them — and in the dlc-3D module before that. Worse, divergent inline copies silently broke the picker for individual cards during refactors. The static-analysis tests in `tests/test_file_browser_policy.py` enforce that:

1. The canonical component exists at `src/static/js/components/file_browser.js`.
2. It exports `makeFileBrowser`.
3. Its `dblclick` handler does not hide the pane.
4. Each consumer card imports from it (no inline duplicates).
5. No consumer redefines a `_XxMakeEntry` style inline factory.
6. This very policy doc exists.

If a future contributor adds another file picker by copying the factory, those tests will fail and force the conversation back to "extend the component instead."

## Adding a new consumer

```javascript
import { makeFileBrowser } from "./components/file_browser.js";

const picker = makeFileBrowser({
  inputEl: document.getElementById("my-target"),
  paneEl:  document.getElementById("my-browser-pane"),
  dirOnly: false,                       // true to hide files entirely
  fileFilter: (name) => name.endsWith(".h5"),  // optional, defaults to video+image
  onPick:  (path) => myAddToQueue(path) // dblclick callback (optional)
});

document.getElementById("my-browse-btn").addEventListener("click",
  () => picker.openAt("/user-data"));
document.getElementById("my-up-btn").addEventListener("click",
  () => picker.up());
```

Optional: subscribe to `file-browser:pick` instead of (or in addition to) `onPick` if you want multiple listeners.

## Adding capabilities to the component

If your card needs behavior the component doesn't have, **add it to the component**, not your card:
- New filter — pass `fileFilter: (name) => bool` or extend the extension sets in `src/static/js/components/file_browser.js`.
- Multi-select via Shift-click — add `multiSelect: true` and an `onSelectionChange(paths[])` callback.
- Different empty-state copy — add a `dirOnlyEmptyText` / `defaultEmptyText` option.

When you extend it, update both this policy doc and the static-analysis tests to cover the new contract.

## Related: other shared frontend factories

The same "one canonical factory, hard-policed by static tests" pattern is
applied to other multi-card frontend logic. As of 2026-05-20:

- `src/static/js/components/analyzed_frame_player.js`
  Export: `makeAnalyzedFramePlayer({ prefix, frameUrlFn, poseUrlFn, onCsvSaved })`
  Consumers (current): `inline_analysis.js`.
  Consumers (after deferred migration per
  `docs/superpowers/specs/2026-05-20-inline-analysis-design.md` §4):
  `viewer.js` too.
  Policy test: `tests/test_analyzed_frame_player_factory.py`.

When this doc grows beyond just the file browser, rename it to
`docs/policies/shared-components.md` and update the policy-test imports
that reference the file path. See the "Known tech debt" section of the
inline-analysis spec for the migration plan.
