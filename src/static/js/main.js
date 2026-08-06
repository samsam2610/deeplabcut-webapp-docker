// Modular frontend entry point — loads all card modules in dependency order.
// Loaded as <script type="module"> so the DOM is ready when each module runs.
//
// These are DYNAMIC imports inside a try/catch, not static `import` statements,
// and that is deliberate. Static ES imports share fate: they evaluate in
// sequence, so the first module that throws while evaluating silently prevents
// every module after it from running at all. You get one console line and a
// page full of buttons that render but were never wired.
//
// That is what happened on /dlc-3d/ (found 2026-08-01). That page ships dlc-3D's
// own frame-labeler card, so `#fl-canvas` is absent; frame_labeler.js
// dereferenced it at module scope, threw, and the eleven modules imported after
// it never executed — including the tracked-files panel, whose button appeared
// to do nothing. A survey found ~208 module-scope dereferences of
// possibly-null getElementById results across these modules, so isolating each
// module here is the fix rather than guarding every one of them.
//
// Order still matters and is preserved: `await` makes each import complete
// before the next begins.
const MODULES = [
  './state.js',
  './api.js',
  './dlc_project.js',           // defines applyDlcProjectState, browseProject, showProgress
  './anipose.js',               // imports from dlc_project.js
  './frame_extractor.js',
  './training.js',
  './frame_labeler.js',
  './test_set_picker.js',
  './analyze.js',
  './batch_analyze.js',         // Batch Analyze panel on the analyze card
  './viewer.js',
  './inline_analysis_player.js',
  './postprocess.js',
  './annotator.js',
  './log_stream.js',            // shared SSE/poll-tail; must load before gpu_monitor.js
  './gpu_monitor.js',
  './admin.js',
  './custom_script.js',
  './tracked_files_panel.js',
];

for (const spec of MODULES) {
  try {
    await import(spec);
  } catch (err) {
    // Loud, per-module, and non-fatal: the remaining modules still load.
    console.error(`[main.js] module failed to load: ${spec}`, err);
  }
}
