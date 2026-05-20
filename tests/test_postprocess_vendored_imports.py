"""Smoke test: every vendored refineDLC module imports cleanly."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_vendored_modules_import():
    """Importing the vendored package must not raise."""
    from dlc import _refinedlc  # noqa: F401
    from dlc._refinedlc import filtering, outliers, interpolation, smoothing  # noqa: F401


def test_vendored_modules_expose_callables():
    """Each vendored module must expose its named processing function(s).

    Asserting on specific names (rather than "any public callable") guards
    against silent renames or removals during a future re-vendor — and avoids
    the tautology that a re-exported `pd`/`np` would satisfy a generic check.
    """
    from dlc._refinedlc import filtering, outliers, interpolation, smoothing

    expected = {
        filtering: ("likelihood_filter",),
        outliers: ("detect_outliers", "position_filter"),
        interpolation: ("interpolate_data",),
        smoothing: ("smooth_coordinates",),
    }

    for mod, names in expected.items():
        for name in names:
            assert hasattr(mod, name), f"{mod.__name__} missing {name}"
            assert callable(getattr(mod, name)), (
                f"{mod.__name__}.{name} is not callable"
            )


def test_vendored_package_reexports():
    """Top-level package must re-export the headline processing functions."""
    from dlc import _refinedlc

    for name in (
        "likelihood_filter",
        "detect_outliers",
        "position_filter",
        "interpolate_data",
        "smooth_coordinates",
    ):
        assert hasattr(_refinedlc, name), f"_refinedlc missing {name}"
        assert callable(getattr(_refinedlc, name))
