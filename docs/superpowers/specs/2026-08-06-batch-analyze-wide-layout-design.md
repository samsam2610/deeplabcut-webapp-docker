# Batch Analyze — wide layout and tag multi-select

**Status:** approved 2026-08-06. Follows
`2026-08-06-batch-analyze-panel-design.md`, which this amends.

Client-only: templates, CSS and JS. No route, task or payload changes — the
API already accepts a `tags` array. `src/templates` and `src/static` are
directory mounts on the flask service, so this deploys without recreating any
container (important while a training run is in flight).

## 1. Full-width card

```css
#analyze-card { max-width: none; width: 100%; }
```

One rule, mirroring `#inline-analysis-3d-card`. Cards sit directly in
`<main class="cards">`, which is `align-items: center` with no width cap, so
overriding `.card`'s `max-width: 560px` is all that is needed. The partial is
shared, so this applies on `/` and `/dlc-3d/` alike.

### Reorganising the single-run controls

Widening the card stretches the controls that were designed for 560px. So:

- **Target** and **Output folder** rows stay full width — they hold paths.
- The seven single-run parameter rows (snapshot, shuffle, training-set index,
  batch size, GPU, save-CSV, create-labeled) are wrapped in
  `.av-param-grid`, a `repeat(auto-fit, minmax(240px, 1fr))` grid: 2–3 columns
  on a wide screen, one column when narrow. No JS, no breakpoint guessing.
- The "Parameters for labeled videos" block gets the same grid.
- `#av-progress` is capped at `max-width: 900px` so log text does not run the
  full width of a large monitor.

## 2. Batch panel layout

Source list spans the full width — it is what benefits most, since the
tracked-files rows carry a progress bar and a last-opened column that were
being crushed at 560px. Queue and Options sit beneath it as a two-column grid
that collapses to one column under 900px.

```
[Project Content] [Browse Folders] [Tracked Files]
┌── source list (full width) ──────────────────────────────────────┐
│ ☑ banh-mi-1_cam0_20260704…   ▓▓▓░░░░   opened 2h ago             │
└──────────────────────────────────────────────────────────────────┘
┌── Queue (3) ──────────[Clear]──┐ ┌── Options ────────────────────┐
│ banh-mi-1_cam0.avi          ×  │ │ Model ( )pinned (•)latest-…   │
└────────────────────────────────┘ │ ☑ Both cams ☐ After training  │
                                   └───────────────────────────────┘
tag [_______________] [+ Add]   before[200] after[599]  = 800 frames
tags:  start-success   not-good   start-failure         ← click to select
[▶ Analyze all] [▶ Analyze for tag] [■ Cancel]    status…
```

The source list grows from 200px to 260px tall now that there is room.

## 3. Tags become a multi-select

The substantive change. Chips stop being shortcuts that fill a text box and
become the selection itself.

| | before | after |
|---|---|---|
| chip click | fills the text field | **toggles selection** (`.fe-tag-chip.active`) |
| text field | held the tags to analyse | only mints a new chip |
| what is submitted | parsed field text | **the labels of selected chips** |
| add | dashed `+ tag` pseudo-chip | a real `+ Add` button beside the field |
| `×` on a chip | removes it | unchanged |

**Enablement.** "Analyze for tag" is disabled whenever no chip is selected,
with a hint next to it saying so. "Analyze all" is unaffected — it ignores
tags entirely.

**Adding.** `+ Add` creates ONE chip from the whole trimmed field value. It is
not comma-split: tags are exact-match by design, and splitting would silently
mangle a tag that legitimately contains a comma. **A value equal to an existing
chip is silently ignored** — no chip added, no error, field cleared, as if it
had been added. Empty/whitespace-only input is ignored the same way.

**Persistence.** The chip list persists per project under the existing
`batch_tags` ui-setting — same key, same JSON array of strings, no migration.
**Selection does not persist**: it resets on every load. A batch is a
deliberate and expensive action, and a stale selection firing off 200 k frames
is a worse failure than re-picking two chips.

## 4. What does not change

- Every route, payload and server-side behaviour.
- `before`/`after` (200/599) and their `batch_window` persistence.
- The queue, the three source tabs, "Both cameras", the model radios, the
  training gate checkbox, cancel, and status polling.
- `makeTrackedFiles` — the batch panel already passes the same component the
  inline card uses, so "the exact information as tracked files in inline
  analysis" is a width fix, not a wiring fix. Verified at runtime after deploy.

## 5. Testing

`tests/test_batch_analyze_wiring.py` already fails on any markup/controller
drift and will cover the new `ba-tag-add` button automatically. Added:

- the full-width rule exists for `#analyze-card`;
- the responsive grids exist for the single-run params and for the
  queue/options row;
- the tag field's placeholder no longer advertises comma-separation;
- `ba-run-tag` carries `disabled` in the markup, so it is inert before any
  chip is selected even if the controller fails to load.

Chip selection, duplicate-add suppression and "submitted tags come from the
selected chips, not the field" are behavioural and live in the controller;
they are covered by extracting the pure part into
`src/static/js/internal/batch_tags.mjs` (`addTag`, `toggle`, `selected`) with
node tests, mirroring how `pick_primary_variant.mjs` is tested.
