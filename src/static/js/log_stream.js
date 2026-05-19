"use strict";

// ─── log_stream.js — shared SSE + poll-tail client for DLC task logs ─────────
//
// Single-EventSource-per-tab discipline for /dlc/task/<id>/log-stream so that
// multiple consumers (jobs.js, gpu_monitor.js, future cards) don't each pin a
// gunicorn sync worker. Subscribers for the SAME taskId fan out from one ES;
// a new subscribe() for a DIFFERENT taskId takes over the ES and demotes
// previous subscribers, which can transparently fall back to pollTail() via
// the cheap /dlc/task/<id>/log-tail endpoint.
//
// Spec: docs/superpowers/specs/2026-05-19-jobs-sse-heartbeat-hybrid-design.md
//
// Public API (attached to window.logStream):
//
//   subscribe(taskId, { onLine, onDone, onDemoted, onStatus }) → unsubscribe()
//   pollTail(taskId, { intervalMs, onLines })                  → stop()

(function attachLogStream(globalObj) {
  // Active EventSource (or null) and the taskId it's bound to.
  let activeES         = null;
  let activeTaskId     = null;
  // taskId → Set of subscriber objects ({ onLine, onDone, onDemoted, onStatus }).
  const subscribers    = new Map();
  // Monotonic id for subscriber identity.
  let nextSubscriberId = 1;

  function _setStatusAll(taskId, text, cls) {
    const set = subscribers.get(taskId);
    if (!set) return;
    set.forEach(s => { try { if (s.onStatus) s.onStatus(text, cls); } catch (e) { console.error("[logStream] onStatus:", e); } });
  }

  function _emitLineAll(taskId, line) {
    const set = subscribers.get(taskId);
    if (!set) return;
    set.forEach(s => { try { if (s.onLine) s.onLine(line); } catch (e) { console.error("[logStream] onLine:", e); } });
  }

  function _emitDoneAll(taskId) {
    const set = subscribers.get(taskId);
    if (!set) return;
    set.forEach(s => { try { if (s.onDone) s.onDone(); } catch (e) { console.error("[logStream] onDone:", e); } });
  }

  function _emitDemotedAll(taskId) {
    const set = subscribers.get(taskId);
    if (!set) return;
    // Snapshot — onDemoted callbacks may re-subscribe or pollTail and we
    // don't want to mutate the set we're iterating.
    Array.from(set).forEach(s => {
      try { if (s.onDemoted) s.onDemoted(); }
      catch (e) { console.error("[logStream] onDemoted:", e); }
    });
  }

  function _closeActiveES() {
    if (activeES) {
      try { activeES.close(); } catch (_) {}
    }
    activeES     = null;
    activeTaskId = null;
  }

  function _openES(taskId) {
    _closeActiveES();
    const es = new EventSource(`/dlc/task/${taskId}/log-stream`);
    activeES     = es;
    activeTaskId = taskId;

    es.addEventListener("message", (ev) => {
      if (activeTaskId !== taskId) return;  // raced past a takeover
      _emitLineAll(taskId, ev.data);
    });

    es.addEventListener("done", () => {
      _emitDoneAll(taskId);
      _closeActiveES();
    });

    es.addEventListener("open", () => {
      _setStatusAll(taskId, "live · streaming", "live");
    });

    es.addEventListener("error", () => {
      // EventSource auto-reconnects on its own; surface a status update so
      // consumers can show a "reconnecting" pill. Do NOT close — let the
      // browser retry. If the connection truly dies the readyState will
      // transition to CLOSED and we just leave the ES bound until another
      // subscribe replaces it.
      _setStatusAll(taskId, "reconnecting…", "paused");
    });

    _setStatusAll(taskId, "live · streaming", "live");
  }

  // ── subscribe ────────────────────────────────────────────────────────────
  function subscribe(taskId, opts) {
    if (!taskId) throw new Error("logStream.subscribe: taskId is required");
    const sub = {
      _id:        nextSubscriberId++,
      onLine:     opts && opts.onLine,
      onDone:     opts && opts.onDone,
      onDemoted:  opts && opts.onDemoted,
      onStatus:   opts && opts.onStatus,
    };

    // If a different task currently owns the ES, demote its subscribers and
    // take over. Demoted subscribers stay registered against THEIR taskId so
    // their pollTail fallback can reach them via the same callback; but the
    // ES is now bound to the new task.
    if (activeTaskId && activeTaskId !== taskId) {
      const oldTask = activeTaskId;
      _closeActiveES();
      _emitDemotedAll(oldTask);
    }

    let set = subscribers.get(taskId);
    if (!set) {
      set = new Set();
      subscribers.set(taskId, set);
    }
    set.add(sub);

    // Open ES if not already bound to this task.
    if (activeTaskId !== taskId) {
      _openES(taskId);
    } else {
      // Already streaming — give the new subscriber a status hint.
      try { if (sub.onStatus) sub.onStatus("live · streaming", "live"); } catch (_) {}
    }

    return function unsubscribe() {
      const s = subscribers.get(taskId);
      if (!s) return;
      s.delete(sub);
      if (s.size === 0) {
        subscribers.delete(taskId);
        // If we just removed the last subscriber for the ACTIVE task, close.
        if (activeTaskId === taskId) {
          _closeActiveES();
        }
      }
    };
  }

  // ── pollTail ─────────────────────────────────────────────────────────────
  // Cheap, one-shot HTTP polling of /dlc/task/<id>/log-tail. Many polls can
  // run concurrently for different tasks; each setInterval fires a fetch
  // and is not held by the server. Uses the `total` field as a cursor so
  // only NEW lines are surfaced.
  function pollTail(taskId, opts) {
    if (!taskId) throw new Error("logStream.pollTail: taskId is required");
    const intervalMs = (opts && opts.intervalMs) || 60000;
    const onLines    = (opts && opts.onLines) || (() => {});
    let cursor       = null;   // last total we've seen; null = uninitialized
    let stopped      = false;
    let timer        = null;

    async function _tick() {
      if (stopped) return;
      try {
        // Ask for a big enough window to cover gaps between polls. The server
        // caps at 10000 lines.
        const res = await fetch(`/dlc/task/${taskId}/log-tail?n=2000`);
        if (!res.ok) return;
        const data = await res.json();
        const lines = data.lines || [];
        const total = typeof data.total === "number" ? data.total : lines.length;
        if (cursor === null) {
          // First poll: establish the baseline. The consumer is expected to
          // have done its own backfill separately, so we do NOT replay the
          // window here — only NEW lines (those past `total`) will be emitted.
          cursor = total;
        } else if (total > cursor) {
          const newCount = total - cursor;
          const newLines = lines.slice(Math.max(0, lines.length - newCount));
          cursor = total;
          if (newLines.length) {
            try { onLines(newLines); }
            catch (e) { console.error("[logStream] pollTail onLines:", e); }
          }
        }
      } catch (e) {
        console.error("[logStream] pollTail fetch:", e);
      }
    }

    // Kick off immediately so first-cursor is established without delay,
    // then on interval.
    _tick();
    timer = setInterval(_tick, intervalMs);

    return function stop() {
      stopped = true;
      if (timer) { clearInterval(timer); timer = null; }
    };
  }

  // Public surface
  globalObj.logStream = {
    subscribe,
    pollTail,
    // Test/diagnostic seam — not part of the documented API.
    _internals: {
      get activeTaskId() { return activeTaskId; },
      get subscriberCount() {
        let n = 0;
        subscribers.forEach(s => { n += s.size; });
        return n;
      },
    },
  };
})(typeof window !== "undefined" ? window : this);
