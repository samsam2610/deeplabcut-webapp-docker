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
    """Each vendored module must expose at least one public callable."""
    from dlc._refinedlc import filtering, outliers, interpolation, smoothing

    for mod in (filtering, outliers, interpolation, smoothing):
        callables = [
            name for name in dir(mod)
            if not name.startswith("_") and callable(getattr(mod, name))
        ]
        assert callables, f"{mod.__name__} exposes no public callables"


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
