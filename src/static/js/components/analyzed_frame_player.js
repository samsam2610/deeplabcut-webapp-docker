// src/static/js/components/analyzed_frame_player.js
//
// ⚠ DUPLICATION NOTICE
//   This file currently maintains a copy of player/overlay/marker-adjustment/
//   dataset-curation logic that ALSO lives in ../viewer.js. Bug fixes in one
//   must be manually mirrored to the other until viewer.js is migrated to
//   this factory.
//
//   See docs/superpowers/specs/2026-05-20-inline-analysis-design.md
//   (§4 "Player Code Reuse" and "Known tech debt") for the planned migration.
//   Follow-up PR title prefix: `refactor(viewer): migrate to analyzed_frame_player factory`.
//
// USAGE:
//   import { makeAnalyzedFramePlayer } from "./components/analyzed_frame_player.js";
//   const player = makeAnalyzedFramePlayer({
//     prefix: "ia",                            // DOM id prefix (ia-frame-img, ia-overlay-canvas, …)
//     frameUrlFn: (n) => `/annotate/frame?path=${path}&frame=${n}`,
//     poseUrlFn:  (layer, n) => `/dlc/viewer/h5-pose-window?h5=${layer.path}&start=${n}&n=1`,
//     onCsvSaved: () => { /* card refresh hook */ },
//   });
//   player.loadVideo(videoPath, fps, nFrames);
//   player.reloadH5();        // after each inline range completes
//   player.destroy();         // on card close

export function makeAnalyzedFramePlayer(options) {
  // Phase 0: skeleton only — the real body lands in Task 0.2.
  // Returning the documented API surface keeps any accidental early consumer
  // from blowing up at construction time.
  return {
    loadVideo: () => {},
    reloadH5: () => {},
    getCurrentFrame: () => 0,
    setCurrentFrame: () => {},
    destroy: () => {},
    setCurationFrameHook: () => {},
    setMetadataFrameHook: () => {},
  };
}
