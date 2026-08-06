"""Batch Analyze — run the inline-analysis pipeline over many videos.

An inline-analysis session is keyed by MODEL, not by video
(``snap_key = sha1(config_path|shuffle|snapshot_path)``), so one warm session
serves a whole batch. Batching therefore reduces to: resolve a model, start one
session, and push the right ranges for each video onto that session's queue.

Layout of this module:

  * pure helpers (``resolve_snapshot``, ``merge_windows``, ``chunk_video``,
    ``tagged_frames``, ``scan_snapshots``) — no Flask, no redis, no cv2;
  * ``run_batch`` — the batch body, with every impure dependency injected, so
    the whole flow is testable on host;
  * the Flask routes, which only validate + write the record + dispatch.

``tasks.dlc_batch_analyze`` wires the real redis/cv2/celery implementations
into ``run_batch``.

See docs/superpowers/specs/2026-08-06-batch-analyze-panel-design.md.
"""
from __future__ import annotations

import csv as _csv
import json
import re
import time
import uuid
from pathlib import Path

from flask import Blueprint, request, jsonify

from . import ctx as _ctx
from .inline_analysis import (
    _active_project, _celery_send_task, _disable_reason, _hgetall,
    _sec_check, _snap_key, _user_id,
)
from . import project_settings as _project_settings
from .utils import _engine_info, _TF_ENGINE_ALIASES

bp = Blueprint("dlc_batch_analyze", __name__)

# The /range route caps a single request at 10 000 frames; whole-video runs are
# chunked to fit under it.
RANGE_MAX_FRAMES = 10_000

MODEL_POLICIES = ("pinned", "latest_iter_best", "latest")

# How long a batch may sit in the training gate before giving up.
TRAINING_WAIT_DEADLINE_S = 24 * 3600
# Gap between training-gate checks. Each check re-dispatches the task with a
# countdown, so waiting costs no worker concurrency slot.
TRAINING_POLL_S = 60

# Statuses that mean the run is OVER for good. Everything else — "running",
# "paused", and notably "dead" — leaves Celery to decide.
#
# "dead" is deliberately NOT here. It is a verdict monitoring._reconcile_job
# writes on a transient miss of the Celery state, and which that same function
# flips back to "running" the moment Celery says otherwise. But the flip only
# happens when somebody polls the Jobs page, so with no browser open the false
# "dead" persists — and reading it as finished would release a deferred batch
# onto the pre-training model. Observed on the 2026-08-06 resume: the hash said
# "dead" while the run was logging epochs on a GPU at 100%.
_FINISHED_TRAIN_STATES = {"complete", "failed", "stopped"}
# Celery's own live states. Mirrors monitoring._LIVE_CELERY_STATES; duplicated
# rather than imported so this module stays importable in the worker, where
# monitoring's Flask deps are not wanted.
_LIVE_CELERY_STATES = {"PENDING", "RECEIVED", "STARTED", "RETRY", "PROGRESS"}

# Idle budget handed to a session started for a batch. The interactive card
# uses 300 s, which is fine when a human keeps clicking; a batch submits
# everything up front and then goes quiet while the worker chews.
BATCH_SESSION_TTL_S = 1800


def _batch_key(batch_id: str) -> str:
    return f"dlc:batch:{batch_id}"


# ── Pure helpers ──────────────────────────────────────────────────────────

def merge_windows(frames, before: int, after: int, frame_count: int) -> list[dict]:
    """Expand each frame to [f-before, f+after], clamp, and union overlapping
    or adjacent spans into a minimal sorted list of {start, end, n}.

    Python mirror of ``tag_batch.mjs::mergeWindows`` — the two must agree, or
    the batch panel and the inline card would analyse different frames for the
    same tag. One deliberate difference: a frame_count of 0 (unreadable video)
    yields no ranges here, where the JS would yield the single frame 0. There
    is nothing to analyse in a video we could not probe.
    """
    n_total = int(frame_count or 0)
    if n_total <= 0:
        return []
    last = n_total - 1
    b, a = int(before or 0), int(after or 0)

    spans = []
    for f in frames or []:
        try:
            k = int(f)
        except (TypeError, ValueError):
            continue
        spans.append((min(max(k - b, 0), last), min(max(k + a, 0), last)))
    spans.sort()

    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 1:
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return [{"start": s, "end": e, "n": e - s + 1} for s, e in merged]


def chunk_video(frame_count: int, max_n: int = RANGE_MAX_FRAMES) -> list[dict]:
    """Cover 0..frame_count-1 with ranges no longer than ``max_n``."""
    n_total = int(frame_count or 0)
    if n_total <= 0:
        return []
    cap = max(1, int(max_n))
    out, start = [], 0
    while start < n_total:
        take = min(cap, n_total - start)
        out.append({"start": start, "end": start + take - 1, "n": take})
        start += take
    return out


def tagged_frames(rows, tags) -> list[int]:
    """Sorted, deduped frame numbers whose ``note`` EXACTLY equals one of
    ``tags``. Exact by design — the user owns the spelling, so `start-failure`
    must not pick up `start-failure-2`.
    """
    want = {str(t).strip() for t in (tags or []) if str(t).strip()}
    if not want:
        return []
    out = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if (row.get("note") or "").strip() not in want:
            continue
        raw = row.get("frame_number")
        # No default: a row carrying a note but no frame number is malformed,
        # and defaulting it to 0 would quietly submit a range at the start of
        # the video that nobody tagged.
        if raw is None or str(raw).strip() == "":
            continue
        try:
            fn = int(float(raw))
        except (TypeError, ValueError):
            continue
        if fn >= 0:
            out.add(fn)
    return sorted(out)


def scan_snapshots(project_path: Path, engine: str, shuffle: int | None = None) -> list[dict]:
    """List model snapshots the way ``/dlc/project/snapshots`` does: ascending
    by (iteration, shuffle, mtime), so the last entry is "latest".

    TECH DEBT: this duplicates the scan inside
    ``training.dlc_project_snapshots``. Copied rather than extracted so the
    working route stays untouched; if the sort ever changes, both move.
    """
    models_folder, _, _ = _engine_info(engine)
    models_root = Path(project_path) / models_folder
    snap_ext = "*.index" if (engine or "pytorch").lower() in _TF_ENGINE_ALIASES else "*.pt"

    def _folder_iter(p: Path):
        try:
            m = re.search(r"iteration[-_](\d+)", p.relative_to(models_root).parts[0], re.I)
            return int(m.group(1)) if m else None
        except Exception:
            return None

    def _shuffle_of(p: Path):
        try:
            m = re.search(r"shuffle(\d+)", p.relative_to(models_root).parts[1], re.I)
            return int(m.group(1)) if m else None
        except Exception:
            return None

    raw = []
    for snap in Path(project_path).glob(f"{models_folder}/**/train/{snap_ext}"):
        sh = _shuffle_of(snap)
        if shuffle is not None and sh != shuffle:
            continue
        raw.append({
            "label":     snap.stem,
            "iteration": _folder_iter(snap),
            "shuffle":   sh,
            "rel_path":  str(snap.relative_to(project_path)),
            "mtime":     snap.stat().st_mtime,
        })
    raw.sort(key=lambda s: (s["iteration"] is None, s["iteration"] or 0,
                            s["shuffle"] is None, s["shuffle"] or 0, s["mtime"]))
    return raw


def resolve_snapshot(snapshots, policy: str, pinned: str = ""):
    """Pick the snapshot for a batch. Returns ``(rel_path, None)`` or
    ``(None, reason)``.

    ``snapshots`` is ``scan_snapshots`` output — ascending, latest last.

    An unresolvable choice fails rather than substituting a different model.
    Silently analysing 20 videos with a model the user did not pick is exactly
    the comparison mistake these options exist to prevent.
    """
    if policy not in MODEL_POLICIES:
        return None, f"unknown model option: {policy!r}"
    if not snapshots:
        return None, "no model snapshots found in this project"

    if policy == "pinned":
        pin = (pinned or "").strip()
        if not pin:
            return None, ("no model is pinned — pin one in the inline-analysis "
                          "card, or choose a different model option")
        for s in snapshots:
            if s.get("rel_path") == pin:
                return pin, None
        return None, f"the pinned model is no longer on disk: {pin}"

    if policy == "latest":
        return snapshots[-1].get("rel_path"), None

    iters = [s.get("iteration") for s in snapshots if s.get("iteration") is not None]
    if not iters:
        return None, "no snapshot carries an iteration number"
    top = max(iters)
    best = [s for s in snapshots
            if s.get("iteration") == top
            and str(s.get("label") or "").startswith("snapshot-best")]
    if not best:
        return None, f"iteration {top} has no snapshot-best-* checkpoint"
    return best[-1].get("rel_path"), None


# ── Default (impure) dependency implementations ───────────────────────────

def probe_frame_count(video_path: str) -> int:
    """Frame count via OpenCV. 0 when the video cannot be opened."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()
        return max(0, n)
    except Exception:
        return 0


def read_notes(video_path: str) -> list[dict]:
    """Rows of the companion CSV that carry a note.

    Same file and columns ``/annotate/csv`` serves; read directly because the
    batch runs in the worker, where there is no request context.
    """
    csv_path = Path(video_path).with_suffix(".csv")
    if not csv_path.is_file():
        return []
    rows = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = _csv.DictReader(fh, skipinitialspace=True)
            if reader.fieldnames:
                reader.fieldnames = [n.strip() for n in reader.fieldnames]
            for row in reader:
                row = {(k.strip() if k else k): v for k, v in row.items()}
                note = (row.get("note") or "").strip()
                if not note:
                    continue
                rows.append({"frame_number": row.get("frame_number", 0), "note": note})
    except Exception:
        return []
    return rows


def resolve_sibling(video_path: str):
    """Sibling camera video, or None. Reuses the resolver the range-triangulate
    path already uses, so batch and triangulation agree on what a pair is."""
    from .triangulate_range import _resolve_cam1
    sib = _resolve_cam1(Path(video_path))
    return str(sib) if sib else None


def celery_state(redis_, task_id: str):
    """Celery's own view of a task, read straight out of the result backend.

    A plain redis GET, not ``AsyncResult`` — the pubsub result consumer leaks
    in gunicorn sync workers (see ``monitoring._celery_task_status``). Returns
    None when the backend has no entry for this id.
    """
    try:
        raw = redis_.get(f"celery-task-meta-{task_id}")
    except Exception:
        return None
    if not raw:
        return None
    try:
        return (json.loads(raw) or {}).get("status")
    except (TypeError, ValueError):
        return None


def training_is_running(redis_, state_of=celery_state) -> bool:
    """True while a training run is genuinely in flight.

    TWO signals are required, because each one alone is wrong in production:

    * The job hash alone is not enough. Its ``status`` field is unreliable —
      the Jobs-page reaper writes "dead" on a transient miss and only undoes it
      when someone polls that page — and it carries no ``updated_at`` to age
      out. What is trustworthy is the hash's EXISTENCE. What it DOES do is slide
      the hash's TTL forward on every progress poll ("Slide the TTL forward so
      long runs (>2 h) stay visible"), making the hash's EXISTENCE a dead-man's
      switch — present while the process writes, gone 2 h after it stops. So
      present-and-``running`` is a real liveness signal, but it carries no
      timestamp of its own, and any staleness rule invented on top of one would
      release the gate on a live run.
    * Celery's state alone is not enough either. The ``dlc_train_jobs`` zset
      outlives the hashes (which expire), and an id whose backend entry has
      been purged reads as PENDING — a "live" state. Trusting that would pin a
      deferred batch for its full 24 h on jobs that finished days ago.

    So: the hash must exist and not claim to be FINISHED, AND Celery must agree.
    "Celery says PROGRESS but the hash has lapsed" is the hard-killed case — a
    SIGKILL never publishes a terminal state, so Celery goes stale while the
    dead-man's switch correctly trips. Its 2 h lag is the price: nothing here
    can tell "killed" from "briefly stalled" any sooner.

    A dispatched-but-not-yet-started task has no backend entry at all; that
    counts as running, because waiting slightly too long is a far cheaper
    mistake than analysing with the pre-training model.
    """
    try:
        ids = redis_.zrevrange("dlc_train_jobs", 0, 49) or []
    except Exception:
        return False
    now = time.time()
    for jid in ids:
        job = _hgetall(redis_, f"dlc_train_job:{jid}") or {}
        if not job:
            continue                      # hash expired — not evidence of a run
        if (job.get("status") or "") in _FINISHED_TRAIN_STATES:
            continue
        try:
            started = float(job.get("started_at") or 0)
        except (TypeError, ValueError):
            started = 0.0
        # Backstop for a hash that outlived its worker entirely.
        if started and (now - started) > TRAINING_WAIT_DEADLINE_S:
            continue
        state = state_of(redis_, jid) if state_of else None
        if state is None or state in _LIVE_CELERY_STATES:
            return True
    return False


# ── The batch body ────────────────────────────────────────────────────────

def _set(redis_, key, **fields):
    try:
        redis_.hset(key, mapping={k: ("" if v is None else str(v))
                                  for k, v in fields.items()})
    except Exception:
        pass


def _fail(redis_, key, reason):
    _set(redis_, key, state="failed", reason=reason, updated_at=time.time())
    return {"state": "failed", "reason": reason}


def run_batch(redis_, batch_id, *, requeue, send_task,
              probe_frames=probe_frame_count, notes_for=read_notes,
              sibling_for=resolve_sibling, is_training=training_is_running,
              now=time.time):
    """Resolve a model, start one session, submit every video's ranges, exit.

    Impure dependencies are injected so the whole flow is testable on host.
    ``requeue(delay_s)`` re-dispatches this batch later — that is how the
    training gate waits without occupying a worker slot.

    Returns a small dict describing the outcome (for tests and logging).
    """
    key = _batch_key(batch_id)
    rec = _hgetall(redis_, key)
    if not rec:
        return {"state": "unknown"}
    if rec.get("state") in ("cancelled", "failed", "complete", "submitted"):
        return {"state": rec.get("state")}
    if str(rec.get("cancelled") or "") == "1":
        _set(redis_, key, state="cancelled", updated_at=now())
        return {"state": "cancelled"}

    # ── training gate ────────────────────────────────────────────────────
    # "after training finishes" means a training run must have been SEEN
    # running and have then stopped. Ticking the box and starting training a
    # minute later must still wait for it, so "nothing running right now" is
    # not on its own permission to proceed.
    if str(rec.get("wait_for_training") or "") == "1":
        try:
            deadline = float(rec.get("deadline") or 0)
        except (TypeError, ValueError):
            deadline = 0.0
        running = bool(is_training(redis_))
        seen = str(rec.get("seen_training") or "") == "1"
        if running:
            _set(redis_, key, state="waiting", seen_training="1",
                 reason="waiting for the running training job to finish",
                 updated_at=now())
            requeue(TRAINING_POLL_S)
            return {"state": "waiting", "seen_training": True}
        if not seen:
            if deadline and now() > deadline:
                return _fail(redis_, key,
                             "gave up waiting: no training job started within 24 h")
            _set(redis_, key, state="waiting",
                 reason="waiting for a training job to start", updated_at=now())
            requeue(TRAINING_POLL_S)
            return {"state": "waiting", "seen_training": False}
        # seen and no longer running → training finished; fall through.

    _set(redis_, key, state="starting", reason="", updated_at=now())

    # ── model ────────────────────────────────────────────────────────────
    config_path = rec.get("config_path") or ""
    project_root = Path(config_path).parent
    shuffle = int(rec.get("shuffle") or 1)
    snaps = scan_snapshots(project_root, rec.get("engine") or "pytorch", shuffle)
    rel, reason = resolve_snapshot(
        snaps, rec.get("policy") or "", rec.get("pinned_snapshot") or "")
    if reason:
        return _fail(redis_, key, reason)
    snap_abs = (project_root / rel).resolve()
    if not snap_abs.is_file():
        return _fail(redis_, key, f"snapshot not found on disk: {rel}")

    user_id = rec.get("user_id") or ""
    snap_key = _snap_key(config_path, shuffle, str(snap_abs))
    _set(redis_, key, snapshot_path=str(snap_abs), snapshot_label=snap_abs.stem,
         snap_key=snap_key, updated_at=now())

    # ── session ──────────────────────────────────────────────────────────
    # Reuse a live session for the same model rather than starting a second
    # one; two sessions on the same snapshot would fight over the same GPU.
    session_key = f"inline:session:{user_id}:{snap_key}"
    existing = _hgetall(redis_, session_key) or {}
    if (existing.get("status") or "") not in ("warming", "ready", "busy"):
        _set(redis_, session_key, status="warming", snapshot_path=str(snap_abs),
             project=project_root.name, started_at=now(), last_activity=now())
        send_task("tasks.dlc_inline_session", kwargs={
            "user_id":          user_id,
            "config_path":      config_path,
            "snap_key":         snap_key,
            "snapshot_path":    str(snap_abs),
            "shuffle":          shuffle,
            "trainingsetindex": int(rec.get("trainingsetindex") or 0),
            "batch_size":       int(rec.get("batch_size") or 8),
            "ttl":              BATCH_SESSION_TTL_S,
        }, queue="pytorch")

    # ── submit ───────────────────────────────────────────────────────────
    videos = json.loads(rec.get("videos") or "[]")
    tags = json.loads(rec.get("tags") or "[]")
    mode = rec.get("mode") or "all"
    before = int(rec.get("before") or 0)
    after = int(rec.get("after") or 0)
    both_cams = str(rec.get("both_cams") or "") == "1"
    save_as_csv = str(rec.get("save_as_csv") or "") == "1"
    batch_size = int(rec.get("batch_size") or 8)
    queue_key = f"inline:queue:{user_id}:{snap_key}"

    req_ids, skipped, submitted = [], [], []
    total_frames = 0

    for video in videos:
        targets = [video]
        if both_cams:
            sib = sibling_for(video)
            if sib:
                targets.append(sib)
            else:
                skipped.append({"video": video,
                                "reason": "no sibling camera found"})

        # Tags are annotated on ONE camera only — the cameras are hardware
        # triggered, so a tagged frame on cam0 is the same instant on cam1 and
        # nobody tags it twice. Real data: banh-mi-1 cam0 carries 141 tagged
        # frames, cam1's CSV carries none. So the windows come from the QUEUED
        # video's CSV and the same ranges go to both cameras, exactly as the
        # 3D inline card's "Analyze for tag" does. Reading each camera's own
        # CSV instead would silently analyse only cam0.
        if mode == "tag":
            tag_frames = tagged_frames(notes_for(video), tags)

        for target in targets:
            if not Path(target).is_file():
                skipped.append({"video": target, "reason": "file not found"})
                continue
            n_frames = probe_frames(target)
            if n_frames <= 0:
                skipped.append({"video": target, "reason": "could not read the video"})
                continue
            if mode == "tag":
                # Re-clamped per target: a sibling a few frames shorter must
                # not receive a range running off its end.
                ranges = merge_windows(tag_frames, before, after, n_frames)
                if not ranges:
                    skipped.append({"video": target,
                                    "reason": "no frames carry any of those tags"})
                    continue
            else:
                ranges = chunk_video(n_frames)
                if not ranges:
                    skipped.append({"video": target, "reason": "video has no frames"})
                    continue

            for rng in ranges:
                req_id = uuid.uuid4().hex
                payload = {
                    "req_id":        req_id,
                    "video_path":    str(target),
                    "start_frame":   rng["start"],
                    "n_frames":      rng["n"],
                    "batch_size":    batch_size,
                    "save_as_csv":   save_as_csv,
                    "snapshot_path": str(snap_abs),
                }
                # RPUSH, not LPUSH: the session drains with BLPOP, so the
                # interactive card's LPUSH still jumps ahead of the whole
                # batch and batch ranges themselves run in queued order.
                redis_.rpush(queue_key, json.dumps(payload))
                req_ids.append(req_id)
                total_frames += rng["n"]
            submitted.append({"video": target, "ranges": len(ranges),
                              "frames": sum(r["n"] for r in ranges)})

    try:
        redis_.hset(session_key, "last_activity", str(now()))
    except Exception:
        pass

    state = "submitted" if req_ids else "failed"
    _set(redis_, key,
         state=state,
         reason="" if req_ids else "nothing to submit — see the skipped list",
         req_ids=json.dumps(req_ids),
         submitted=json.dumps(submitted),
         skipped=json.dumps(skipped),
         n_ranges=len(req_ids),
         n_frames=total_frames,
         submitted_at=now(),
         updated_at=now())
    _write_job_row(redis_, batch_id, rec, len(req_ids), total_frames, state, now)
    return {"state": state, "req_ids": req_ids, "skipped": skipped,
            "n_frames": total_frames}


def _write_job_row(redis_, batch_id, rec, n_ranges, n_frames, state, now):
    """One aggregate row on the Jobs surface, mirroring the shape
    ``/dlc/project/triangulate/batch`` writes."""
    videos = json.loads(rec.get("videos") or "[]")
    first = videos[0] if videos else ""
    try:
        redis_.hset(f"dlc_analyze_job:{batch_id}", mapping={
            "task_id":     batch_id,
            "operation":   "batch_analyze",
            "project":     Path(rec.get("config_path") or "").parent.name,
            "target_path": first,
            "started_at":  str(rec.get("created_at") or now()),
            "updated_at":  str(now()),
            "status":      "running" if state == "submitted" else "complete",
            "total":       n_ranges,
            "done":        0,
            "stage":       f"{n_ranges} ranges · {n_frames} frames queued",
        })
        redis_.expire(f"dlc_analyze_job:{batch_id}", 7200)
        redis_.zadd("dlc_analyze_jobs", {batch_id: now()})
    except Exception:
        pass


# ── Routes ────────────────────────────────────────────────────────────────

def _pinned_snapshot(project_path: str) -> str:
    try:
        return _project_settings.get_setting(project_path, "pinned_snapshot") or ""
    except Exception:
        return ""


@bp.route("/dlc/project/batch-analyze/start", methods=["POST"])
def batch_start():
    project = _active_project()
    if not project:
        return jsonify({"error": "No active DLC project."}), 400
    block = _disable_reason(project)
    if block:
        return jsonify({"error": block[1]}), block[0]

    body = request.get_json(silent=True) or {}
    videos = [str(v).strip() for v in (body.get("videos") or []) if str(v).strip()]
    if not videos:
        return jsonify({"error": "queue at least one video"}), 400
    for v in videos:
        if not _sec_check(Path(v)):
            return jsonify({"error": f"path is outside the data root: {v}"}), 403

    mode = (body.get("mode") or "").strip()
    if mode not in ("all", "tag"):
        return jsonify({"error": "mode must be 'all' or 'tag'"}), 400

    tags = [str(t).strip() for t in (body.get("tags") or []) if str(t).strip()]
    if mode == "tag" and not tags:
        return jsonify({"error": "enter at least one tag"}), 400

    policy = (body.get("policy") or "").strip()
    if policy not in MODEL_POLICIES:
        return jsonify({"error": f"model option must be one of "
                                 f"{', '.join(MODEL_POLICIES)}"}), 400

    config_path = project["config_path"]
    project_path = str(Path(config_path).parent)

    # The snapshot DROPDOWN is authoritative for the "pinned" policy. Without
    # this, a dropdown showing snapshot-180 while the persisted pin named
    # snapshot-050 would silently run snapshot-050. Resolving it here — into
    # the very field run_batch already reads — means the worker needs no
    # change, which matters when a training run is in flight.
    pinned = _pinned_snapshot(project_path)
    snapshot_rel = (body.get("snapshot_rel") or "").strip()
    if snapshot_rel:
        snap_abs = (Path(project_path) / snapshot_rel).resolve()
        if not snap_abs.is_relative_to(Path(project_path).resolve()):
            return jsonify({"error": "snapshot is outside the project"}), 403
        if not snap_abs.is_file():
            return jsonify({"error": f"snapshot not found: {snapshot_rel}"}), 404
        pinned = snapshot_rel

    batch_id = uuid.uuid4().hex
    wait = bool(body.get("wait_for_training"))
    created = time.time()

    redis = _ctx.redis_client()
    redis.hset(_batch_key(batch_id), mapping={
        "batch_id":          batch_id,
        "user_id":           _user_id(),
        "config_path":       config_path,
        "engine":            project.get("engine") or "pytorch",
        "pinned_snapshot":   pinned,
        "videos":            json.dumps(videos),
        "mode":              mode,
        "tags":              json.dumps(tags),
        "before":            int(body.get("before") or 0),
        "after":             int(body.get("after") or 0),
        "both_cams":         "1" if body.get("both_cams") else "0",
        "policy":            policy,
        "shuffle":           int(body.get("shuffle") or 1),
        "trainingsetindex":  int(body.get("trainingsetindex") or 0),
        "batch_size":        int(body.get("batch_size") or 8),
        "save_as_csv":       "1" if body.get("save_as_csv") else "0",
        # Recorded now, honoured once the worker picks up the device change
        # (see the spec: it needs CUDA_DEVICE_ORDER=PCI_BUS_ID and a
        # session restart, which would kill a running training job).
        "gputouse":          str(body.get("gputouse") or ""),
        "wait_for_training": "1" if wait else "0",
        "seen_training":     "0",
        "deadline":          str(created + TRAINING_WAIT_DEADLINE_S),
        "state":             "queued",
        "reason":            "",
        "cancelled":         "0",
        "created_at":        str(created),
        "updated_at":        str(created),
    })
    redis.expire(_batch_key(batch_id), 7 * 24 * 3600)

    _celery_send_task("tasks.dlc_batch_analyze",
                      kwargs={"batch_id": batch_id}, queue="celery")
    return jsonify({"batch_id": batch_id, "state": "queued",
                    "n_videos": len(videos)}), 202


@bp.route("/dlc/project/batch-analyze/status", methods=["GET"])
def batch_status():
    batch_id = (request.args.get("batch_id") or "").strip()
    if not batch_id:
        return jsonify({"error": "batch_id required"}), 400
    redis = _ctx.redis_client()
    rec = _hgetall(redis, _batch_key(batch_id))
    if not rec:
        return jsonify({"error": "unknown batch_id"}), 404

    req_ids = json.loads(rec.get("req_ids") or "[]")
    done = errors = analyzed = skipped_frames = 0
    last_error = ""
    for rid in req_ids:
        h = _hgetall(redis, f"inline:result:{rid}") or {}
        status = h.get("status") or "pending"
        if status == "done":
            done += 1
            analyzed += int(h.get("n_analyzed") or 0)
            skipped_frames += int(h.get("n_skipped") or 0)
        elif status == "error":
            errors += 1
            last_error = h.get("error") or last_error

    state = rec.get("state") or "queued"
    if state == "submitted" and req_ids and (done + errors) >= len(req_ids):
        state = "complete"
        _set(redis, _batch_key(batch_id), state=state, updated_at=time.time())
        try:
            redis.hset(f"dlc_analyze_job:{batch_id}",
                       mapping={"status": "complete", "done": done,
                                "updated_at": str(time.time()),
                                "stage": f"{done}/{len(req_ids)} ranges"})
        except Exception:
            pass

    return jsonify({
        "batch_id":       batch_id,
        "state":          state,
        "reason":         rec.get("reason") or "",
        "mode":           rec.get("mode") or "",
        "policy":         rec.get("policy") or "",
        "snapshot":       rec.get("snapshot_label") or "",
        "n_ranges":       len(req_ids),
        "n_frames":       int(rec.get("n_frames") or 0),
        "ranges_done":    done,
        "ranges_error":   errors,
        "frames_analyzed": analyzed,
        "frames_skipped": skipped_frames,
        "last_error":     last_error,
        "submitted":      json.loads(rec.get("submitted") or "[]"),
        "skipped":        json.loads(rec.get("skipped") or "[]"),
    })


@bp.route("/dlc/project/batch-analyze/cancel", methods=["POST"])
def batch_cancel():
    body = request.get_json(silent=True) or {}
    batch_id = (body.get("batch_id") or "").strip()
    if not batch_id:
        return jsonify({"error": "batch_id required"}), 400
    redis = _ctx.redis_client()
    rec = _hgetall(redis, _batch_key(batch_id))
    if not rec:
        return jsonify({"error": "unknown batch_id"}), 404
    # Only stops a batch that has not submitted yet — once ranges are on the
    # session queue, stopping is the session's job, not the batch's.
    _set(redis, _batch_key(batch_id), cancelled="1",
         state="cancelled" if rec.get("state") in ("queued", "waiting")
               else rec.get("state"),
         updated_at=time.time())
    return jsonify({"ok": True, "state": _hgetall(redis, _batch_key(batch_id)).get("state")})


@bp.route("/dlc/project/batch-analyze/list", methods=["GET"])
def batch_list():
    """Batches this user has queued that are still interesting (not complete).

    Lets the panel re-attach to a waiting batch after a reload — without it,
    a run deferred until training finishes becomes invisible the moment the
    tab is closed, which is exactly the case that feature exists for.
    """
    redis = _ctx.redis_client()
    uid = _user_id()
    out = []
    try:
        for key in redis.scan_iter(match="dlc:batch:*", count=200):
            rec = _hgetall(redis, key) or {}
            if rec.get("user_id") != uid:
                continue
            if rec.get("state") in ("complete", "cancelled", "failed"):
                continue
            out.append({
                "batch_id":   rec.get("batch_id") or "",
                "state":      rec.get("state") or "",
                "reason":     rec.get("reason") or "",
                "mode":       rec.get("mode") or "",
                "created_at": float(rec.get("created_at") or 0),
                "n_videos":   len(json.loads(rec.get("videos") or "[]")),
            })
    except Exception:
        return jsonify({"batches": []})
    out.sort(key=lambda b: b["created_at"], reverse=True)
    return jsonify({"batches": out[:20]})
