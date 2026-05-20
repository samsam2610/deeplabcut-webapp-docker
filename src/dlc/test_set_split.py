"""
Split-assembly for the test-set picker.

Pure function — no Flask, no Redis, no Celery. Reads the merged
CollectedData_<scorer>.h5 (via DLC's own merge function) and maps
user marks → positional indices into the merged DataFrame.

Public entry: build_indices(config_path, marks, mode, train_fraction, seed).

The pure inner function build_indices_from_dataframe is exposed for
unit tests so they can fabricate DataFrames without DLC installed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Literal

import numpy as np
import pandas as pd

Mode = Literal["random", "hybrid", "manual"]


def build_indices_from_dataframe(
    data: pd.DataFrame,
    marks: Iterable[tuple[str, str]],
    mode: Mode,
    train_fraction: float,
    seed: int = 42,
) -> tuple[list[int], list[int], dict] | None:
    """Inner pure function — unit-testable without DLC installed.

    `data` must have a row MultiIndex whose levels are
    (constant "labeled-data", video_stem, image_name) — DLC's shape.

    Returns (trainIndices, testIndices, stats) or None when mode == "random".
    stats = {"dropped_marks": int, "total_frames": int}.
    """
    if mode == "random":
        return None

    if mode not in ("hybrid", "manual"):
        raise ValueError(
            f"mode must be one of 'random'|'hybrid'|'manual', got {mode!r}"
        )

    # Build lookup: (stem, image) -> positional index. Drop the constant
    # "labeled-data" prefix from level 0; the marks store keys on (stem, image).
    idx_lookup: dict[tuple[str, str], int] = {}
    for pos, row in enumerate(data.index):
        # row is a tuple ("labeled-data", stem, image)
        if len(row) < 3:
            continue
        stem, image = row[1], row[2]
        idx_lookup[(stem, image)] = pos

    marks_list = list(marks)
    mark_positions: set[int] = set()
    dropped = 0
    for stem, image in marks_list:
        pos = idx_lookup.get((stem, image))
        if pos is None:
            dropped += 1
        else:
            mark_positions.add(pos)

    total = len(data.index)
    all_positions = set(range(total))
    stats = {"dropped_marks": dropped, "total_frames": total}

    if mode == "manual":
        if not mark_positions:
            raise ValueError(
                "Full manual mode requires at least one marked frame; "
                "no marks resolved to a labeled frame in the merged H5."
            )
        test_idx = sorted(mark_positions)
        train_idx = sorted(all_positions - mark_positions)
        return train_idx, test_idx, stats

    # hybrid
    target_test_count = round((1 - train_fraction) * total)
    forced_test = set(mark_positions)
    extra_needed = max(0, target_test_count - len(forced_test))
    if extra_needed > 0:
        pool = sorted(all_positions - forced_test)
        rng = np.random.default_rng(seed)
        extra = rng.choice(pool, size=extra_needed, replace=False)
        test_set = forced_test | {int(i) for i in extra}
    else:
        test_set = forced_test
    train_idx = sorted(all_positions - test_set)
    test_idx = sorted(test_set)
    return train_idx, test_idx, stats


def build_indices(
    config_path: str | Path,
    marks: Iterable[tuple[str, str]],
    mode: Mode,
    train_fraction: float,
    seed: int = 42,
) -> tuple[list[int], list[int], dict] | None:
    """Production entry: loads the merged H5 via DLC, then delegates.

    Imports of `deeplabcut` are deferred so the test module above can
    import this module without DLC installed.
    """
    if mode == "random":
        return None

    # Deferred imports — only when DLC is needed at runtime.
    from deeplabcut.utils import auxiliaryfunctions
    from deeplabcut.generate_training_dataset.trainingsetmanipulation import (
        merge_annotateddatasets,
    )

    cfg = auxiliaryfunctions.read_config(str(config_path))
    scorer = cfg["scorer"]
    project_path = cfg["project_path"]
    training_set_folder = auxiliaryfunctions.get_training_set_folder(cfg)
    training_set_folder_full = Path(project_path) / training_set_folder
    training_set_folder_full.mkdir(parents=True, exist_ok=True)

    # Try cached merged H5 first (matches DLC's behavior in mergeandsplit).
    fn = training_set_folder_full / f"CollectedData_{scorer}.h5"
    if fn.is_file():
        data = pd.read_hdf(str(fn))
    else:
        data = merge_annotateddatasets(cfg, training_set_folder_full)
        if data is None or len(data.index) == 0:
            raise RuntimeError(
                "Merged H5 is empty; nothing to split. Did labeled-data/ get scrubbed?"
            )

    # Strip scorer column level the way mergeandsplit does, so the row index
    # is the canonical ("labeled-data", stem, image) tuple.
    if hasattr(data.columns, "levels") and scorer in data.columns.levels[0]:
        data = data[scorer]

    return build_indices_from_dataframe(data, marks, mode, train_fraction, seed)
