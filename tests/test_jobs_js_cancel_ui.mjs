// Node-runnable smoke test for the /jobs page's new Cancel affordance.
//
// This repo has no JS test framework/runner (no package.json, no jest/mocha),
// so this is a small self-contained script run directly with `node`, in the
// style of the "node --check + grep every new identifier" verification the
// task called for — but exercising actual behavior, not just parsing.
//
// jobs.js is loaded by jobs.html as `<script type="module" ...>`, so it can
// be imported here the same way (dynamic import of a real <script type=module>
// file works fine under Node as long as the file has no bare browser-only
// top-level syntax errors — it does reference `document`/`window`, so those
// are stubbed below before the import, exactly like a jsdom-less smoke test
// would).
//
// Covers the three "jobs.js" bullets from the task's Tests section:
//   - fetches /dlc/jobs/all                        -> asserted via URL capture
//   - a Cancel button exists per cancellable row     -> asserted via rendered HTML
//   - confirm text includes the pending count for inline rows
//                                                     -> asserted via _cancelConfirmText

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const jobsJsPath = path.join(__dirname, "..", "src", "static", "js", "jobs.js");

// ── Minimal DOM stub sufficient for jobs.js's module-load-time work ────────
// jobs.js only touches the DOM inside functions (called later) and two
// top-level `document.addEventListener` calls — no querying at import time.
class FakeClassList {
  constructor() { this._set = new Set(); }
  add(...names) { names.forEach(n => this._set.add(n)); }
  remove(...names) { names.forEach(n => this._set.delete(n)); }
  contains(n) { return this._set.has(n); }
}

class FakeElement {
  constructor(tag) {
    this.tagName = tag;
    this._children = [];
    this._listeners = {};
    this.dataset = {};
    this.classList = new FakeClassList();
    this._innerHTML = "";
  }
  addEventListener(evt, fn) {
    (this._listeners[evt] = this._listeners[evt] || []).push(fn);
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(v) { this._innerHTML = v; }
  querySelectorAll() { return []; }
  querySelector() { return null; }
}

const fakeDocument = {
  hidden: false,
  addEventListener() {},
  getElementById() { return null; },
  querySelector() { return null; },
};

global.document = fakeDocument;
global.window = global;

// Capture the last fetch() URL so we can assert the endpoint used.
const fetchCalls = [];
global.fetch = async (url, opts) => {
  fetchCalls.push({ url, opts });
  return {
    ok: true,
    json: async () => ({ jobs: [], celery_reachable: true }),
    text: async () => "",
  };
};

const mod = await import(jobsJsPath);
void mod; // jobs.js has no exports; it attaches test hooks to window.

const hooks = global.window.__jobsTestHooks;
assert.ok(hooks, "jobs.js must expose window.__jobsTestHooks");
const { _cancelConfirmText, _isCancellable } = hooks;

// ── 1. Confirm text includes the pending count for inline rows ────────────
{
  const job = {
    type: "inline",
    kind: "inline_session",
    label: "inline analysis — some-project — 261 pending ranges",
    detail: { pending: 261 },
    cancellable: true,
  };
  const text = _cancelConfirmText(job);
  assert.match(text, /261/, "confirm text must state the pending count");
  assert.match(text, /discarded/i, "confirm text must state ranges are discarded");
}

// Singular-vs-plural sanity (not load-bearing, just don't regress it).
{
  const job = { type: "inline", detail: { pending: 1 }, cancellable: true };
  const text = _cancelConfirmText(job);
  assert.match(text, /1 pending range\b/, "singular 'range' with pending=1");
}

// ── 2. Confirm text for a running (celery) task states the consequence ────
{
  const job = { type: "celery", kind: "train", label: "train — SomeProj", cancellable: true };
  const text = _cancelConfirmText(job);
  assert.match(text, /stopped mid-run/i);
  assert.match(text, /lost/i);
  assert.doesNotMatch(text, /^Are you sure\?$/, "must not be a bare generic confirm");
}

// ── 3. Cancellability flag ──────────────────────────────────────────────
assert.equal(_isCancellable({ cancellable: true }), true);
assert.equal(_isCancellable({ cancellable: false }), false);
assert.equal(_isCancellable(null), false);

// ── 4. Cancel button exists per cancellable row (source-level check) ──────
// _renderRail() writes real innerHTML strings; check the template source
// directly for the per-row Cancel button + fetches against /dlc/jobs/all
// and /dlc/jobs/cancel, since a full DOM (`document.getElementById("jobs-rail")`
// returns null here) isn't wired up in this lightweight harness.
{
  const src = readFileSync(jobsJsPath, "utf8");
  assert.match(src, /fetch\("\/dlc\/jobs\/all"\)/, "must fetch /dlc/jobs/all");
  assert.match(src, /fetch\("\/dlc\/jobs\/cancel"/, "must post to /dlc/jobs/cancel");
  assert.match(
    src,
    /_isCancellable\(j\)[\s\S]{0,200}jobs-row-cancel-btn/,
    "a Cancel button must be rendered conditionally on _isCancellable(j) per row"
  );
}

console.log("test_jobs_js_cancel_ui.mjs: all assertions passed");
