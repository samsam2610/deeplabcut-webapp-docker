#!/usr/bin/env python3
"""
DLC Training Heartbeat → Telegram via OpenClaw.

Runs every minute via cron. Sends a Telegram notification when:
  - A training/analyze job is newly detected (startup summary)
  - A new evaluation result appears in the log (immediate, every run)
  - A job is still running (periodic progress update every 10 minutes)

State is tracked in a small JSON sidecar so new vs ongoing jobs are
distinguished correctly across cron invocations.

Redis:  localhost:6379  (Docker port-mapping from deeplabcut-webapp stack)
Notify: docker exec openclaw-openclaw-gateway-1  openclaw message send
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import redis

# ── Config ────────────────────────────────────────────────────────────────────
REDIS_URL          = "redis://localhost:6379/0"
TELEGRAM_TARGET    = "786989644"
OPENCLAW_CONTAINER = "openclaw-openclaw-gateway-1"
STATE_FILE         = Path(__file__).parent / ".training_heartbeat_state.json"
UPDATE_INTERVAL_S  = 600  # send periodic progress update at most once per 10 min

# ── Log parsing ───────────────────────────────────────────────────────────────

_RE_EPOCH = re.compile(
    r"Epoch\s+(\d+)/(\d+)\s+\(lr=([^)]+)\),\s+train loss\s+([\d.]+)"
    r"(?:,\s+valid(?:ation)? loss\s+([\d.]+))?"
    r"(?:.*?GPU:\s*([\d.]+)/([\d.]+)\s*MiB)?"
)
# Fallback: valid loss with any separator (space, =, :, ,)
_RE_VALID_LOSS = re.compile(r"valid(?:ation)?\s+loss[\s=:,]+([\d.eE+\-]+)", re.IGNORECASE)
_RE_METRIC = re.compile(r"metrics/([\w.]+):\s+([\d.]+)")
_RE_TQDM   = re.compile(r'(\d+)%\|[^|]*\|\s*(\d+)/(\d+)\s*\[([^\]<]+)<([^\],\]]+)')


def parse_training_log(log: str) -> dict:
    """Extract latest epoch info + most recent model performance block."""
    result: dict = {}

    # Walk lines in reverse to get the most recent epoch line
    lines_list = log.splitlines()
    for line in reversed(lines_list):
        m = _RE_EPOCH.search(line)
        if m:
            result["epoch"]        = int(m.group(1))
            result["total_epochs"] = int(m.group(2))
            result["lr"]           = m.group(3)
            result["train_loss"]   = float(m.group(4))
            if m.group(5):
                result["valid_loss"] = float(m.group(5))
            if m.group(6) and m.group(7):
                result["log_vram_used"]  = float(m.group(6))
                result["log_vram_total"] = float(m.group(7))
            break

    # Fallback: if valid_loss wasn't captured by the epoch regex, find the
    # most recent standalone valid/validation loss line in the log
    if "valid_loss" not in result:
        for line in reversed(lines_list):
            mv = _RE_VALID_LOSS.search(line)
            if mv:
                try:
                    result["valid_loss"] = float(mv.group(1))
                except ValueError:
                    pass
                break

    # Find the last "Model performance:" block
    perf_idx = log.rfind("Model performance:")
    if perf_idx != -1:
        perf_block = log[perf_idx:]
        metrics = {}
        for m in _RE_METRIC.finditer(perf_block):
            metrics[m.group(1)] = float(m.group(2))
        if metrics:
            result["metrics"] = metrics

    return result


def parse_analyze_log(log: str) -> dict:
    """Extract latest tqdm frame progress from an analyze log."""
    result: dict = {}
    for m in _RE_TQDM.finditer(log):
        result["pct"]   = int(m.group(1))
        result["done"]  = int(m.group(2))
        result["total"] = int(m.group(3))
        result["eta"]   = m.group(5).strip()
    return result


WORKER_CONTAINER = "deeplabcut-webapp-dlc-refactor-worker-1"


def fetch_training_log(r: redis.Redis, task_id: str) -> str:
    """Read the training log directly from the worker container log file.

    Falls back to scanning /tmp/dlc_train_*.log in the container if the
    log_path key isn't in Redis (jobs started before that was added).
    """
    log_path = r.hget(f"dlc_train_job:{task_id}", "log_path")

    # If no stored path, find the newest dlc_train_*.log in the container
    if not log_path:
        try:
            ls = subprocess.run(
                ["docker", "exec", WORKER_CONTAINER,
                 "sh", "-c", "ls -t /tmp/dlc_train_*.log 2>/dev/null | head -1"],
                capture_output=True, text=True, timeout=5,
            )
            log_path = ls.stdout.strip() or None
        except Exception:
            pass

    if log_path:
        try:
            result = subprocess.run(
                ["docker", "exec", WORKER_CONTAINER, "tail", "-c", "8000", log_path],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout
        except Exception:
            pass

    # Last resort: Celery task meta (may lag significantly behind the file)
    raw = r.get(f"celery-task-meta-{task_id}")
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        return data.get("result", {}).get("log", "") or ""
    except Exception:
        return ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def elapsed(started_at: float) -> str:
    secs = int(time.time() - started_at)
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def send_telegram(message: str) -> bool:
    try:
        result = subprocess.run(
            [
                "docker", "exec", OPENCLAW_CONTAINER,
                "node", "dist/index.js",
                "message", "send",
                "--channel", "telegram",
                "--target", TELEGRAM_TARGET,
                "--message", message,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"[heartbeat] telegram send failed: {result.stderr[:300]}", file=sys.stderr)
            return False
        return True
    except Exception as exc:
        print(f"[heartbeat] telegram send error: {exc}", file=sys.stderr)
        return False


def parse_gpu_stats(r: redis.Redis) -> list[dict]:
    raw = r.get("dlc_gpu_stats")
    if not raw:
        return []
    gpus = []
    for line in raw.strip().split("\n"):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 6:
            try:
                gpus.append({
                    "index":       int(parts[0]),
                    "name":        parts[1],
                    "utilization": int(parts[2]),
                    "mem_used":    int(parts[3]),
                    "mem_total":   int(parts[4]),
                    "temp":        int(parts[5]),
                })
            except (ValueError, IndexError):
                pass
    return gpus


def format_gpu_block(gpus: list[dict]) -> str:
    if not gpus:
        return "  GPU: unavailable"
    lines = []
    for g in gpus:
        lines.append(
            f"  GPU {g['index']} ({g['name']})\n"
            f"    Util: {g['utilization']}%  "
            f"VRAM: {g['mem_used']}/{g['mem_total']} MB  "
            f"Temp: {g['temp']}°C"
        )
    return "\n".join(lines)


def format_epoch_block(info: dict) -> str:
    if "epoch" not in info:
        return ""
    lines = []
    pct = round(info["epoch"] / info["total_epochs"] * 100)
    bar_filled = pct // 5
    bar = "█" * bar_filled + "░" * (20 - bar_filled)
    lines.append(
        f"  Epoch  : {info['epoch']}/{info['total_epochs']}  [{bar}] {pct}%"
    )
    lines.append(f"  LR     : {info['lr']}")
    lines.append(f"  Train↓ : {info['train_loss']:.5f}")
    if "valid_loss" in info:
        lines.append(f"  Valid↓ : {info['valid_loss']:.5f}")
    if "log_vram_used" in info:
        lines.append(
            f"  VRAM   : {info['log_vram_used']:.0f}/{info['log_vram_total']:.0f} MiB"
        )
    return "\n".join(lines)


def format_metrics_block(metrics: dict) -> str:
    if not metrics:
        return ""
    lines = ["  Model performance (last eval):"]
    label_map = {
        "test.rmse":        "RMSE        ",
        "test.rmse_pcutoff":"RMSE@pcutoff",
        "test.mAP":         "mAP         ",
        "test.mAR":         "mAR         ",
    }
    for key, label in label_map.items():
        if key in metrics:
            lines.append(f"    {label}: {metrics[key]:.2f}")
    # Any extra metrics not in the label map
    for key, val in metrics.items():
        short = key.replace("test.", "")
        if key not in label_map:
            lines.append(f"    {short}: {val}")
    return "\n".join(lines)


def build_eval_message(job: dict, metrics: dict) -> str:
    op      = job.get("operation", "train").capitalize()
    project = job.get("project", "unknown")

    lines = [
        f"✅ DLC Evaluation Complete",
        f"Project : {project}",
        f"Op      : {op}",
        f"Time    : {now_str()}",
        "",
    ]
    lines.append(format_metrics_block(metrics))
    return "\n".join(lines)


def is_celery_alive(r: redis.Redis, task_id: str) -> bool:
    """Check Celery task meta directly — catches jobs marked 'dead' by the
    monitoring reconciler when PROGRESS state was missing from its allowlist."""
    raw = r.get(f"celery-task-meta-{task_id}")
    if not raw:
        return False
    try:
        data = json.loads(raw)
        return data.get("status") in ("PENDING", "RECEIVED", "STARTED", "RETRY", "PROGRESS")
    except Exception:
        return False


def fetch_running_jobs(r: redis.Redis) -> list[dict]:
    jobs = []
    for zset, prefix, op_default in [
        ("dlc_train_jobs",   "dlc_train_job:",   "train"),
        ("dlc_analyze_jobs", "dlc_analyze_job:", "analyze"),
    ]:
        for jid in r.zrevrange(zset, 0, 49):
            job = r.hgetall(prefix + jid)
            if not job:
                continue
            status = job.get("status", "")
            tid    = job.get("task_id", jid)
            # Accept 'running' OR 'dead' that Celery still considers alive
            if status == "running" or (status == "dead" and is_celery_alive(r, tid)):
                job.setdefault("operation", op_default)
                jobs.append(job)
    return jobs


def build_start_message(job: dict, gpus: list[dict], log_info: dict) -> str:
    op      = job.get("operation", "train").capitalize()
    project = job.get("project", "unknown")
    engine  = job.get("engine", "?")
    tid     = job.get("task_id", "")[:8]
    target  = job.get("target_path", "")
    started = float(job.get("started_at", time.time()))

    lines = [
        f"🏋️ DLC {op} Started",
        f"Project : {project}",
        f"Engine  : {engine}",
        f"Task    : {tid}…",
        f"Started : {datetime.fromtimestamp(started).strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if target:
        lines.append(f"Target  : {Path(target).name}")

    epoch_block = format_epoch_block(log_info)
    if epoch_block:
        lines.append("")
        lines.append(epoch_block)

    lines.append("")
    lines.append(format_gpu_block(gpus))
    return "\n".join(lines)


def build_analyze_start_message(job: dict, gpus: list[dict], av_info: dict) -> str:
    project = job.get("project", "unknown")
    target  = job.get("target_path", "")
    started = float(job.get("started_at", time.time()))
    lines = [
        "🎬 DLC Analyze Started",
        f"Project : {project}",
        f"Target  : {Path(target).name}",
        f"Started : {datetime.fromtimestamp(started).strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if av_info.get("total"):
        lines.append(f"Frames  : {av_info['total']:,}")
    lines.append("")
    lines.append(format_gpu_block(gpus))
    return "\n".join(lines)


def build_analyze_update_message(job: dict, gpus: list[dict], av_info: dict) -> str:
    project = job.get("project", "unknown")
    target  = job.get("target_path", "")
    started = float(job.get("started_at", time.time()))
    lines = [
        "📊 DLC Analyze Update",
        f"Project : {project}",
        f"Target  : {Path(target).name}",
        f"Running : {elapsed(started)}",
        f"Time    : {now_str()}",
    ]
    if av_info.get("pct") is not None:
        pct       = av_info["pct"]
        bar_filled = pct // 5
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        lines.append(f"  Frames : {av_info.get('done',0):,}/{av_info.get('total',0):,}  [{bar}] {pct}%")
        if av_info.get("eta"):
            lines.append(f"  ETA    : {av_info['eta']}")
    lines.append("")
    lines.append(format_gpu_block(gpus))
    return "\n".join(lines)


def build_update_message(job: dict, gpus: list[dict], log_info: dict) -> str:
    op      = job.get("operation", "train").capitalize()
    project = job.get("project", "unknown")
    engine  = job.get("engine", "?")
    started = float(job.get("started_at", time.time()))

    lines = [
        f"📊 DLC {op} Update",
        f"Project : {project}",
        f"Engine  : {engine}",
        f"Running : {elapsed(started)}",
        f"Time    : {now_str()}",
    ]

    epoch_block = format_epoch_block(log_info)
    if epoch_block:
        lines.append("")
        lines.append(epoch_block)

    metrics_block = format_metrics_block(log_info.get("metrics", {}))
    if metrics_block:
        lines.append("")
        lines.append(metrics_block)

    lines.append("")
    lines.append(format_gpu_block(gpus))
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=3)
        r.ping()
    except Exception as exc:
        print(f"[heartbeat] Redis unavailable: {exc}", file=sys.stderr)
        sys.exit(0)  # don't spam cron errors when stack is down

    state         = load_state()
    gpus          = parse_gpu_stats(r)
    jobs          = fetch_running_jobs(r)
    seen_ids      = set(state.get("seen_ids", []))
    last_metrics  = state.get("last_metrics", {})   # {task_id: metrics_dict}
    last_update   = state.get("last_update", {})    # {task_id: timestamp}

    if not jobs:
        save_state({"seen_ids": [], "last_metrics": {}, "last_update": {}})
        return

    now          = time.time()
    new_seen     = set()
    new_metrics  = {}
    new_update   = dict(last_update)

    for job in jobs:
        tid = job.get("task_id", "")
        if not tid:
            continue
        new_seen.add(tid)

        log      = fetch_training_log(r, tid)
        is_analyze = job.get("operation", "train") == "analyze"

        if is_analyze:
            av_info = parse_analyze_log(log)
            # ── Analyze: Telegram progress notifications ───────────────
            since_last = now - float(last_update.get(tid, 0))
            if tid not in seen_ids:
                send_telegram(build_analyze_start_message(job, gpus, av_info))
                new_update[tid] = now
            elif since_last >= UPDATE_INTERVAL_S:
                send_telegram(build_analyze_update_message(job, gpus, av_info))
                new_update[tid] = now
            new_metrics[tid] = {}
        else:
            log_info = parse_training_log(log)

            # ── Eval notification: fires immediately when metrics change ──────
            current_metrics = log_info.get("metrics", {})
            if current_metrics and current_metrics != last_metrics.get(tid, {}):
                send_telegram(build_eval_message(job, current_metrics))

            new_metrics[tid] = current_metrics

            # ── Progress update: at most once per UPDATE_INTERVAL_S ──────────
            since_last = now - float(last_update.get(tid, 0))
            if tid not in seen_ids:
                send_telegram(build_start_message(job, gpus, log_info))
                new_update[tid] = now
            elif since_last >= UPDATE_INTERVAL_S:
                send_telegram(build_update_message(job, gpus, log_info))
                new_update[tid] = now

    save_state({"seen_ids": list(new_seen), "last_metrics": new_metrics, "last_update": new_update})


if __name__ == "__main__":
    main()
