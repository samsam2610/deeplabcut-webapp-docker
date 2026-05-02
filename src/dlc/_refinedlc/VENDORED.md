# Vendored refineDLC

**Upstream:** https://github.com/wer-kle/refineDLC
**Commit:** `72cece845595435d3bafa99ff8b27b070ce05945`
**Date vendored:** 2026-05-01
**License:** MIT (see full text below)

## License (verbatim from upstream LICENSE)

```
MIT License

Copyright (c) 2025 Weronika Klecel, Hadley Rahael & Samantha A. Brooks

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## File Mapping

| This repo | Upstream | Functions extracted |
|---|---|---|
| `filtering.py` | `refinedlc/likelihood_filter.py` | `likelihood_filter`, `process_file` |
| `outliers.py` | `refinedlc/position_filter.py` | `detect_outliers`, `position_filter`, `process_file` |
| `interpolation.py` | `refinedlc/interpolate.py` | `interpolate_data`, `process_file` |
| `smoothing.py` | *not present in upstream* | `smooth_coordinates` (local stub) |

The CLI `main()` entry-points and `argparse` plumbing from each upstream
module were intentionally **not** vendored. We re-use only the processing
functions — adapter code in `src/dlc/postprocess_refine.py` (Tasks T4-T7)
will call them directly with already-validated parameters.

`refinedlc/clean_coordinates.py` was **not** vendored. It performs DLC-CSV
header flattening, sign-flipping of y, and zero-row removal — concerns
handled elsewhere in this codebase (or not needed for the pipeline this
package backs). It can be added in a future vendor pass if required.

`refinedlc/plot_displacements.py` and `refinedlc/plot_trajectories.py`
were intentionally not vendored (matplotlib plotting helpers, not
processing functions).

### About `smoothing.py`

Upstream refineDLC at the vendored SHA does **not** ship a smoothing
module. The post-process card plan requires a `step_smoothing` adapter
(Task T7), so this file is included as a stable home for that adapter to
import from. The current contents are a thin local stub (centered
rolling-mean) — *not* vendored upstream code. T7 may replace it with a
Savitzky-Golay or other production-quality implementation. When that
happens, the file mapping above should be updated to reflect "locally
authored, not vendored".

## Why vendored (not pip-installed)

refineDLC's repo pins `numpy>=1.23`, `pandas>=1.5`, `matplotlib>=3.6` and
declares `scipy` in `setup.py` without bounds. These intersect cleanly with
DeepLabCut's own pins in our worker image (numpy 2.x, pandas 2.x, scipy
1.17.x), but adding refineDLC as a pip dep would also pull in its
`matplotlib` and CLI entry-points which we don't need. We copy only the
processing functions and reuse the existing dep set already provided by
`deeplabcut`.

`outliers.py` uses `statsmodels.stats.stattools.medcouple` for the
`adj_iqr` mode. The worker image already provides `statsmodels` (verified
0.14.6). We softened the upstream `try: from statsmodels ... except
ImportError: raise ImportError(...)` to defer the error until `adj_iqr` is
actually requested, so the module imports cleanly even on hosts without
statsmodels.

## Updating

To update to a newer upstream commit:
1. `git clone https://github.com/wer-kle/refineDLC.git`
2. Diff the upstream files against the vendored copies.
3. Re-vendor any updated functions, preserving the relative-import edits
   and the `statsmodels` lazy-import pattern in `outliers.py`.
4. Update the SHA at the top of this file and in each module's header
   comment.
5. Run `tests/test_postprocess_vendored_imports.py` and
   `tests/test_postprocess_refine.py`.
