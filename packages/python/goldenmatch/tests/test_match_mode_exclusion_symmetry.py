"""Auto-config exclusions must drop the SAME columns from both match-mode frames.

`auto_configure_df` runs the GoldenCheck column-exclusion detectors and drops
the excluded columns so the controller never proposes a matchkey over them. In
match mode it was dropping them from ``df`` (the target) only and leaving
``reference`` untouched, so the two frames reached ``run_match_df`` with
different widths. Its polars lane concatenates them with a bare ``pl.concat``,
which is strict about width:

    ShapeError: unable to append to a DataFrame of width 5 with a DataFrame of
    width 6

Every controller iteration raised it, so the run fell back to v0 + a RED
sentinel and emitted almost nothing. Measured on Amazon-Google (#2717): the
`description` column is excluded by ``detect_free_text_notes`` on BOTH frames
independently -- the detectors agreed, only the drop was one-sided.

The arrow lane escaped it because ``pa.concat_tables`` is called with
``promote_options="permissive"``, which fills the missing column with nulls.
That asymmetry is why this never showed up as a test failure.
"""
from __future__ import annotations

import polars as pl
import pytest

from goldenmatch.core.quality_exclusions import detect_autoconfig_exclusions


def _frame(n: int, seed: int) -> pl.DataFrame:
    return pl.DataFrame({
        "name": [f"widget {i % 37} model {seed}{i}" for i in range(n)],
        # >50 mean chars and a name the free-text regex matches -> excluded.
        "description": [
            "a long free text blurb about the product that carries no "
            f"precision signal whatsoever, item number {i}"
            for i in range(n)
        ],
        "city": [["springfield", "shelbyville", "ogdenville"][i % 3] for i in range(n)],
    })


def test_detector_excludes_description_on_both_frames():
    """Precondition: the exclusion is not target-specific, so a one-sided drop
    is unambiguously a bug rather than the detectors disagreeing."""
    for frame in (_frame(60, 1), _frame(80, 2)):
        excluded = {
            e.column for e in detect_autoconfig_exclusions(
                frame, force_exclude=[], force_include=[], skip_columns=set()
            )
        }
        assert "description" in excluded


def test_match_mode_hands_controller_equal_schemas():
    """The controller must receive target and reference with the same columns."""
    import goldenmatch.core.autoconfig_controller as controller_mod
    from goldenmatch import match_df

    seen: list[tuple[list[str], list[str] | None]] = []
    original = controller_mod.AutoConfigController.run

    def spy(self, df, *args, **kwargs):
        ref = kwargs.get("reference")
        seen.append((list(df.columns), None if ref is None else list(ref.columns)))
        return original(self, df, *args, **kwargs)

    controller_mod.AutoConfigController.run = spy
    try:
        match_df(_frame(60, 1), _frame(80, 2))
    finally:
        controller_mod.AutoConfigController.run = original

    assert seen, "controller.run was never called -- the spy did not bind"
    target_cols, ref_cols = seen[0]
    assert ref_cols is not None
    assert "description" not in target_cols
    assert target_cols == ref_cols


def test_match_mode_controller_iterations_do_not_error():
    """The end state the width mismatch produced: every iteration raised."""
    from goldenmatch import match_df
    from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN

    match_df(_frame(60, 1), _frame(80, 2))
    run = _LAST_CONTROLLER_RUN.get()
    if run is None:
        pytest.skip("controller did not record a run (non-controller path)")
    _, history = run
    errors = [e.error for e in history.entries if e.error is not None]
    shape_errors = [e for e in errors if e.exception_type == "ShapeError"]
    assert not shape_errors, f"controller iterations raised ShapeError: {shape_errors}"
