// Modular frontend entry point — imports all card modules in dependency order.
// Loaded as <script type="module"> so the DOM is ready when each module runs.
import './state.js';
import './api.js';
import './dlc_project.js';   // defines applyDlcProjectState, browseProject, showProgress
import './anipose.js';        // imports from dlc_project.js
import './frame_extractor.js';
import './training.js';
import './frame_labeler.js';
import './analyze.js';
import './viewer.js';
import './annotator.js';
import './gpu_monitor.js';
import './custom_script.js';
