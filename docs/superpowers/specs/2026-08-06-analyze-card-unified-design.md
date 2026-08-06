# Analyze card — one queue, two halves, batch-first

**Status:** approved 2026-08-06. Supersedes the panel structure in
`2026-08-06-batch-analyze-panel-design.md` and
`2026-08-06-batch-analyze-wide-layout-design.md`; their behavioural rules
(RPUSH ordering, the training gate, tag multi-select, cam0-only tag windows)
are unchanged.

The card stops being "single-run, with a batch panel bolted on". There is one
source of files (the queue), one parameter set (two halves), and several
actions. A single run is just a queue of one.

## Layout

```
Analyze Video / Frames
├ [Project Content] [Browse Folders] [Tracked Files]
├ source list                                     (full width)
├ Queue (2) [Clear]   banh-mi-1_cam0.avi ×
├ ┌─ Model ─────────────────┐ ┌─ Run options ──────────────────────┐
│ │ Snapshot [▾]       [↺]  │ │ Batch size [8]    ☐ Save as CSV    │
│ │ ┌ pin ────────────────┐ │ │ GPU to use [auto ▾]                │
│ │ │ ☑ snapshot-050 i26  │ │ │ ☑ Both cameras                     │
│ │ └─────────────────────┘ │ │ ☐ Queue after training finishes    │
│ │ ( ) Selected / pinned   │ │ Output (•) Same as target          │
│ │ (•) Latest iter's best  │ │        ( ) Custom [path][↑][Browse]│
│ │ ( ) Latest              │ │ ☐ Create labeled video → params    │
│ │ Shuffle[1]  Train-set[0]│ └────────────────────────────────────┘
│ └─────────────────────────┘
├ tag [____] [+ Add]   before[200] after[599] = 800
├ tags: start-success  not-good  start-failure
├ [▶ Analyze all] [▶ Analyze for tag] [■ Cancel] [🎬 Create Labeled Video]
└ progress / log        (Create Labeled Video only)
```

Shuffle and training-set index sit with **Model** because they select *which*
trained model, not how the run behaves.

## Removed

- `ba-enable` and the collapsible wrapper — the panel is the card now.
- `av-target-path` and its browser (`av-browse-up`, `av-browse-btn`,
  `av-browser`), plus the old `av-batch-add-btn` / `av-batch-clear-btn` /
  `av-batch-list` strip. The queue replaces all of it.
- `btn-run-analyze` (Start Analysis), `btn-stop-analyze`, `av-run-status`.
  "Analyze all" over a one-file queue is the replacement — that is what
  "treat single run as a batch of one" means.

`av-progress` / `av-log-output` stay: **Create Labeled Video** reports into
them via `_avStartPolling`.

## Create Labeled Video

Keeps working, but takes its target from the **first queued file** instead of
the deleted path box. It still reads `av-shuffle`, `av-trainingsetindex`, the
`clv-*` parameters and the output folder — all of which now live in the halves.
Disabled while the queue is empty, with a hint saying so.

## Snapshot dropdown + pin list

Mirrors the 3D inline card: a `<select>` of every snapshot, a scrollable
checkbox list under it that pins one (radio behaviour — checking one unchecks
the rest), and a refresh button. Pinning persists the project-level
`pinned_snapshot` ui-setting.

**The dropdown is authoritative.** Previously the server resolved the `pinned`
policy from the persisted setting, so a dropdown showing `snapshot-180` while
the pin said `snapshot-050` would silently run `snapshot-050`. Now the client
sends the dropdown's current value as `snapshot_rel`, and
`POST /batch-analyze/start` writes it into the batch record's
`pinned_snapshot` field.

That last detail is deliberate: writing it into the field the existing
`run_batch` already reads means **no worker change and no worker restart** —
which matters because a training run is in flight. `snapshot_rel` is
path-checked against the project before being stored.

Note: `pinned_snapshot` is project-level and shared with the 3D cards, which
read it to choose the Primary overlay layer. One pin, one meaning.

## GPU to use

`av-gputouse` moves into **Run options** and applies to every analysis run.

This needs backend work that is **staged, not shipped**:

1. `CUDA_DEVICE_ORDER=PCI_BUS_ID` on the `worker` service. Load-bearing: the
   container leaves it unset, so CUDA uses FASTEST_FIRST and torch's `cuda:0`
   is the **RTX PRO 6000**, inverted from `nvidia-smi` and from the UI's label.
   `dlc_train_network` already sets it in its child process, and training's
   ordering is the convention to match.
2. `session/start` accepts `gputouse`; `dlc_inline_session` takes a `device`;
   `_dlc_inline_session_loop` passes `device="cuda:N"` to
   `get_pose_inference_runner` (it currently passes `device=None`).
3. The device joins `snap_key`. Sessions are keyed by
   `(config, shuffle, snapshot)`; without the device, asking for GPU 1 while a
   GPU 0 session is warm silently reuses the GPU 0 one and the selector does
   nothing.

Both (1) and (2) require recreating the worker, which kills the training run in
progress. So the client stores `gputouse` in the batch record from now, and the
worker starts honouring it after its next restart. **Until then the dropdown is
recorded but not applied** — stated here because shipping a control that does
nothing is otherwise exactly what this project has been avoiding.

The PRO 6000 is an approved analysis target — the "LLM/orchestration only" line
in CLAUDE.md is out of date.

## Testing

Wiring guards are updated, not deleted:

- no `ba-enable`; no `av-target-path`, `btn-run-analyze`, `btn-stop-analyze`;
- the snapshot dropdown, refresh button and pin list all exist;
- exactly two output radios, and `av-gputouse` still exists;
- **ordering**: `av-gputouse` and the output radios appear *inside* the Run
  options half, i.e. before the run buttons — so a later edit cannot float them
  somewhere they read as unrelated;
- `btn-create-labeled-video` survives and `av-progress` with it.

Route tests: `snapshot_rel` is stored as `pinned_snapshot`, is rejected when
outside the project, and `gputouse` round-trips into the record.
