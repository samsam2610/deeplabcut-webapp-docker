# Post-Process Predictions Card — Design

**Date:** 2026-05-01
**Branch:** `feat/posture-match-refiner` (or new branch off it)
**Status:** Approved (brainstorm); pending implementation plan

## Problem

After analyzing videos with DeepLabCut, users currently have no in-app way to apply common post-processing transforms to the produced `.h5` / `.csv` predictions. They must drop to the shell, find the right DLC API, or wire up scripts manually. We want a single card in the DLC project view that exposes both DLC's built-in `filterpredictions` and the [refineDLC](https://github.com/wer-kle/refineDLC) toolkit, with safe, non-destructive output.

## Goals

- Add one new button + card to the existing DLC project sidebar, between **View Analyzed Videos/Frames** and **Annotate Video**.
- Support two tool families, switched by a top-level dropdown:
  - **deeplabcut** — DLC's `filterpredictions` only (other DLC post-procs explicitly deferred).
  - **refineDLC** — likelihood filter, outlier removal, interpolation, smoothing; usable as a chained pipeline (fixed order) or as a single action.
- Accept either a single analyzed file or a folder of analyzed files.
- Run on the existing PyTorch Celery `worker` queue with progress and log streaming.
- Never overwrite originals; outputs land in a per-run timestamped subfolder under the input's parent directory.

## Non-Goals

- No GPU usage (these tools are CPU-only).
- No new Celery queue, no new container, no support-module split.
- No DLC post-procs other than `filterpredictions` (deferred: `plot_trajectories`, `create_labeled_video`, `analyzeskeleton`, `extract_outlier_frames`).
- No editing of source analyzed files — outputs are always new files in `postproc/`.
- No project-aware "Project Content" tab (input is single-file or folder browse only).

## UI

### Button placement

`src/templates/partials/card_dlc_project.html` — insert a new button `btn-open-postprocess` between line 119 (`btn-open-view-analyzed`) and line 120 (`btn-open-annotate-video`). Label: **"Post-Process Predictions"**. Icon: sliders/funnel SVG matching the existing button style.

### Card partial

New file `src/templates/partials/card_postprocess.html`:

```html
<section class="card dlc-theme hidden" id="postprocess-card"> ... </section>
```

Top-to-bottom layout:

1. **Tool dropdown** — `<select id="pp-tool">` with options `deeplabcut` and `refineDLC`. Switching this swaps the parameters panel.
2. **Input mode toggle** — two buttons "Single File" / "Folder" (matches the View Analyzed card's tab pattern). Active mode reveals:
   - File picker: path input + a browse modal scoped to `.h5` / `.csv`.
   - Folder picker: path input + browse modal; recursively finds analyzed files (skipping any existing `postproc/` subtrees).
3. **Parameters panel** — content swaps with the tool dropdown:
   - **deeplabcut → `filterpredictions`:** filter type (`median` / `arima`), window length, p-bound, ARIMA `(p, d, q)` (only when `arima` selected), save-as-csv toggle. Defaults match DLC's defaults.
   - **refineDLC:** segmented control "Mode": **Pipeline** vs **Single action**.
     - *Pipeline:* four toggleable steps in fixed order (likelihood filter → outlier removal → interpolation → smoothing); each toggle expands to its own parameters block.
     - *Single action:* radio selector over the four steps; only the selected step's parameters render.
4. **Run row** — "Run" button, status badge (`idle` / `queued` / `running` / `done` / `failed` / `cancelled` / `partial`), and an output-path hint (e.g., "Will write to `<input-dir>/postproc/<timestamp>/`").
5. **Live log panel** — collapsible `<pre id="pp-log">` streaming task logs (same shape as Analyze / Train cards).
6. **Recent runs** — last N runs (up to ~10) for the active project, populated by reading sidecar `run.json` files. Columns: timestamp, tool, action, input, status, link to output folder.

### Card open/close

Matches existing cards: opening the post-process card closes any other open card (same delegated handler that Train/Analyze/View/Annotate already share). ESC and the close `×` button both close it.

### Static asset

New `src/static/js/postprocess.js`. Mirrors the pattern of `src/static/js/viewer.js` and `src/static/js/annotator.js`: scoped DOM lookups, `addEventListener` registrations, fetch helpers for the new routes.

### UI invariants ("don't destroy anything")

- The new card and button must not change any existing element's `id`, class, or DOM order beyond inserting the single new button between the two named buttons.
- Opening the post-process card MUST hide every other DLC card; opening any other DLC card MUST hide the post-process card.
- Keyboard shortcuts (ESC) and the global card-close handler must work identically.
- Test these invariants explicitly (see Testing).

## Backend

### Blueprint

New module `src/dlc/postprocess.py`, blueprint name `dlc_postprocess`, registered in `src/app.py` adjacent to the other DLC blueprints.

| Route | Method | Purpose |
|---|---|---|
| `/dlc/postprocess/scan` | POST | Body `{path, mode}`. Returns the list of analyzable files (filters by DLC naming conventions: `*resnet*.h5`, `*resnet*.csv`, etc.). Excludes any path under an existing `postproc/` subfolder. |
| `/dlc/postprocess/run` | POST | Validates params, dispatches Celery task, returns `{task_id}`. |
| `/dlc/postprocess/status/<task_id>` | GET | Returns `{state, progress: {current, total, file, step}, log_tail}`. |
| `/dlc/postprocess/logs/<task_id>` | GET | Streams the tail of the task log (same chunked-response pattern used by inference). |
| `/dlc/postprocess/cancel/<task_id>` | POST | Revokes the Celery task. |
| `/dlc/postprocess/recent` | GET | Reads sidecar `run.json` files under the active project to populate the Recent runs panel. |

### Celery task

`dlc_postprocess_run` in `src/dlc/tasks.py`, dispatched to the existing `worker` (PyTorch) queue. One task per dispatch, processing the full input set (1 file or N files for folder mode). Emits Celery state updates of shape:

```python
{"current": i, "total": N, "file": "<basename>", "step": "<step-name>"}
```

No GPU is required; do not inject `CUDA_VISIBLE_DEVICES`.

### Tool layer (no Flask, fully unit-testable)

- **`src/dlc/postprocess_dlc.py`** — wraps `deeplabcut.filterpredictions`. Resolves the active DLC project's `config.yaml` (DLC requires it), builds output paths, calls DLC, copies/renames the produced file into the per-run subfolder. Source files are untouched.
- **`src/dlc/postprocess_refine.py`** — vendored refineDLC functions (one function per step) plus two drivers:
  - `run_pipeline(df, steps_config) -> df` — applies enabled steps in fixed order.
  - `run_single(df, step, params) -> df` — applies one step.
  - Reads input `.h5` / `.csv` → DataFrame → applies → writes the output preserving DLC's multi-index column format. CSV output mirrors the input format (commas, header rows).

### Vendoring strategy for refineDLC

Approach 1 (in-process Python, vendored) chosen, with explicit care to avoid dep conflicts.

- Copy the relevant refineDLC functions verbatim into `src/dlc/_refinedlc/`, **preserving the original copyright/license header** in each file.
- **Do not** add refineDLC as a `pip install` line — its repo may pin pandas/numpy/scipy versions that clash with DLC's. The worker image already provides compatible versions transitively via DLC.
- Vendor only processing functions and their direct helpers. Skip refineDLC's CLI, plotting, and test harness.
- Record the upstream commit SHA in `src/dlc/_refinedlc/VENDORED.md`.
- Add a CI-time dep-audit test that imports every vendored module inside the worker image; fails fast if a future dep bump breaks the vendoring.

## Output Layout

Output goes under the **input file's parent directory** (per-file basis when folder mode is used):

```
<input-parent>/
  postproc/
    20260501-143022_filterpredictions/      ← <YYYYMMDD-HHMMSS>_<tool-tag>
      MAP2_..._50000_filtered.h5            ← processed file (suffix names the action)
      MAP2_..._50000_filtered.csv           ← if save-as-csv was on
      run.json                              ← sidecar metadata (see below)
      run.log                               ← full task log for this run
```

- **Subfolder name:** `<timestamp>_<tool-tag>`. Timestamp resolution = seconds (collision-proof under any sane click rate). `tool-tag` ∈ `{filterpredictions, refine_pipeline, refine_lh, refine_outliers, refine_interp, refine_smooth}`.
- **Output file naming:** `<original-stem>_<action-suffix>.<ext>`. Preserves the DLC stem so downstream tools (`create_labeled_video`, etc.) can recognize the file.
- **Folder mode:** one `postproc/<timestamp>_<tag>/` subfolder per **input parent directory**. A flat folder yields one output subfolder; a tree yields one per input subdirectory.

### Sidecar `run.json`

Machine-readable; powers the Recent runs panel:

```json
{
  "run_id": "20260501-143022_filterpredictions",
  "started_at": "2026-05-01T14:30:22Z",
  "finished_at": "2026-05-01T14:30:41Z",
  "status": "success",
  "tool": "deeplabcut",
  "action": "filterpredictions",
  "params": { "filtertype": "median", "windowlength": 5, "p_bound": 0.001, "save_as_csv": true },
  "inputs": [
    { "path": "<abs-path>.h5", "output": "<abs-path>_filtered.h5", "status": "success", "error": null }
  ],
  "project": "/user-data/.../DREADD-Ali-2026-01-07",
  "app_version": "<git-sha>"
}
```

`status` ∈ `{success, partial, failed, cancelled}`.

### Safety invariants

- The original `.h5` / `.csv` is **never** touched, renamed, or moved.
- Output paths are computed up-front. If the target subfolder somehow exists already, the run **aborts** rather than overwriting.
- Folder mode is **read-only** outside `postproc/` — scans never recurse into existing `postproc/` subfolders.

## Error Handling

### Server-side validation (before dispatch)

- Path exists and is inside an allowed root (reuse the existing user-data path allowlist used by other DLC routes).
- `file` mode: extension in `{.h5, .csv}`; columns parse as a DLC multi-index. Reject otherwise with a clear message.
- `folder` mode: at least one analyzable file found after recursive scan (excluding `postproc/` subtrees). Empty → 400 "no analyzable files".
- Param ranges:
  - median window length: odd, ≥ 3.
  - smoothing window > polyorder.
  - likelihood threshold ∈ [0, 1].
  - ARIMA `(p, d, q)`: each non-negative int.

### Per-file failure (folder mode)

A single file failing does not abort the batch. Final run status becomes `partial`; `run.json.inputs[i]` records the error string and a stack-trace tail. DLC's `filterpredictions` doesn't surface a clean exception API for some failures — wrap calls in `try/except`, capture stderr.

### Concurrency

Only **one** post-process task may be active per project at a time. Second dispatch returns 409 with `{error: "task already running", task_id: <id>}`. Same lock pattern used by training/analyze tasks.

### Cancellation

"Cancel" button → `/dlc/postprocess/cancel/<task_id>` → revokes the Celery task. Partial outputs already on disk are retained; sidecar status set to `cancelled`. The run subfolder is **not** deleted.

## Testing

All tests live under `tests/`.

| File | Coverage |
|---|---|
| `tests/test_postprocess_dlc.py` | Unit: DLC wrapper on a tiny synthetic `.h5`. Asserts output path layout and that source file size + mtime are unchanged. |
| `tests/test_postprocess_refine.py` | Unit: one test per refineDLC step (likelihood, outliers, interp, smooth) with hand-computed expected DataFrames. Pipeline test asserts step composition runs in fixed order (filter → outliers → interp → smooth). |
| `tests/test_postprocess_routes.py` | Flask blueprint tests: scan, dispatch (Celery mocked), status, recent. Uses `tmp_path` for outputs. |
| `tests/test_postprocess_real_project.py` | `TestRealProjectIntegration` style. Runs DLC `filterpredictions` and a refineDLC pipeline against the real DREADD project's `videos/` outputs (skipped if project not present). Verifies originals are bit-identical (sha256) pre/post run. |
| `tests/test_postprocess_ui_isolation.py` (or extension to existing UI test file) | Static-template assertions: new card's IDs are unique across all templates; `btn-open-postprocess` sits between `btn-open-view-analyzed` and `btn-open-annotate-video`; opening the post-process card hides every other DLC card; opening any other DLC card hides the post-process card. |
| `tests/test_postprocess_vendored_imports.py` | Dep audit: imports every vendored refineDLC module inside the worker image. Fails early if a dep bump breaks the vendoring. |

The real-project integration test is **required** before declaring the feature done, per the project's existing testing convention.

## File Summary

**New files:**

- `src/templates/partials/card_postprocess.html`
- `src/static/js/postprocess.js`
- `src/dlc/postprocess.py` (blueprint + routes)
- `src/dlc/postprocess_dlc.py` (DLC wrapper)
- `src/dlc/postprocess_refine.py` (refineDLC drivers)
- `src/dlc/_refinedlc/` (vendored functions + `VENDORED.md`)
- `tests/test_postprocess_dlc.py`
- `tests/test_postprocess_refine.py`
- `tests/test_postprocess_routes.py`
- `tests/test_postprocess_real_project.py`
- `tests/test_postprocess_ui_isolation.py`
- `tests/test_postprocess_vendored_imports.py`

**Modified files:**

- `src/templates/partials/card_dlc_project.html` (new button)
- `src/templates/base.html` or wherever partials are included (include the new card partial)
- `src/app.py` (register blueprint)
- `src/dlc/tasks.py` (new Celery task)
- `src/dlc/README.md` (document new routes + module)

## Open Items for the Implementation Plan

- Confirm the exact list of card-include sites in `base.html` / equivalent (the spec assumes there is one).
- Confirm the existing UI-isolation testing convention (Playwright vs JSDOM vs static-template assertions) by reading how `vlm_refiner.js` is currently tested, and align the new UI-isolation tests with that.
- Confirm refineDLC's exact public function signatures at the chosen vendored commit.
- Confirm the existing card open/close handler is centralized enough to participate in by adding one new card, or whether each card duplicates the close logic — adjust the open/close wiring to match.
