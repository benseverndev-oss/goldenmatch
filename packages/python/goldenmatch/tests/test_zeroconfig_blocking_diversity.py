"""Zero-config never gets the blocking-diversification lever, and pays 53% recall.

Measured, person @ 100,000 rows, same fixture, same run (32084546976):

    lane                     scored pairs  pairwise P       R      F1
    gm_probabilistic_shipped      142,933      0.9992  0.9949  0.9970
    gm_zeroconfig                  11,319      1.0000  0.4684  0.6380
    splink                         23,825      0.9999  0.9904  0.9952

Precision 1.0000 with recall 0.4684 is candidate generation, not scoring: the
pairs are never proposed, and no threshold can recover a pair that was never
scored. The resolved blocking explains it exactly --

    auto_configure_probabilistic_df   8 passes
        [city, first_name] | [city, first_name]+substring5 | first_name soundex
        | surname soundex  | surname substring5 | dob | dob substring4 | postcode

    auto_configure_df (zero-config)   2 passes
        [city, first_name] | dob soundex

-- so a duplicate is a candidate only if it agrees on city AND first_name, or on
soundex(dob). The probabilistic path gives it eight independent chances,
including the surname and postcode anchors that catch pairs whose name or city
was corrupted.

The cause is one missing call. Both paths run `build_blocking` ->
`_maybe_prune_blocking_passes` -> `apply_quality_aware_blocking`, but only
`auto_configure_probabilistic_df` then runs
`_diversify_probabilistic_blocking`, whose own docstring describes this failure:

    "Auto-config blocking tends to key entirely on the name column(s); on
     error-heavy PII data that caps the candidate ceiling (audit:
     historical_50k blocking_recall 0.585 -- 42% of true pairs never co-block
     because their names are corrupted even though dob/postcode agree)."

`_legacy_auto_configure_v0`, which the controller calls for its initial config,
stops one line earlier.

## Why this needs a scale projection that the existing caller does not

`auto_configure_probabilistic_df` passes the FULL frame, so the lever's #1857
scale guard measures true block sizes. The controller passes a ~5-6K SAMPLE
(measured: 6,324 rows for a 100K frame). `_projected_max_block` measures the df
it is handed with no correction, so on a sample every anchor looks tiny and is
kept -- including birth-YEAR, which is ~1.5K rows at 100K but ~15K rows at 1M
and is exactly the OOM #1857 added the guard for.

So the guard must project sample -> full population before comparing against the
row cap, using `project_max_block_size` (block size grows ~linearly with N).
Without that, moving the lever would trade a recall bug for a memory bomb.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.core.autoconfig import (
    _diversify_probabilistic_blocking,
    build_blocking,
    profile_columns,
)
from goldenmatch.core.blocking_candidates import project_max_block_size


def _frame(n: int = 3000):
    """A person-shaped frame with a low-cardinality date anchor.

    `birth_date`'s YEAR has 40 distinct values, so its largest block holds a
    fixed FRACTION of rows -- small on a sample, pathological at 1M. That is the
    property the projection has to see.
    """
    return pa.table({
        "record_id": [f"r{i}" for i in range(n)],
        "first_name": [f"name{i % 900}" for i in range(n)],
        "surname": [f"sur{i % 700}" for i in range(n)],
        "birth_date": [f"{1950 + (i % 40)}-01-01" for i in range(n)],
        "postcode": [f"{10000 + (i % 800)}" for i in range(n)],
        "city": [f"city{i % 60}" for i in range(n)],
    })


def _passes(blocking):
    return [(tuple(p.fields), tuple(p.transforms or []))
            for p in (list(blocking.passes or []) or list(blocking.keys or []))]


def test_the_lever_still_diversifies_when_given_the_full_frame():
    """Guard the guard: if the lever stopped adding anchors on a full frame,
    every other assertion here would pass for the wrong reason."""
    df = _frame()
    profiles = profile_columns(df)
    base = build_blocking(profiles, df)
    out = _diversify_probabilistic_blocking(base, profiles, df)
    assert len(_passes(out)) > len(_passes(base))


def test_a_sample_sized_call_projects_to_the_full_population():
    """The controller hands this a ~6K sample of a 1M frame.

    Birth-YEAR here has 40 distinct values: ~75 rows per block on a 3K sample,
    but ~25,000 at 1M -- far past the FS scorer's per-block row cap. Measuring
    the sample alone keeps the pass and reintroduces #1857.
    """
    df = _frame()
    profiles = profile_columns(df)
    base = build_blocking(profiles, df)

    unprojected = _diversify_probabilistic_blocking(base, profiles, df)
    projected = _diversify_probabilistic_blocking(
        base, profiles, df, n_rows_full=1_000_000,
    )

    year_sig = (("birth_date",), ("substring:0:4",))
    assert year_sig in _passes(unprojected), (
        "the year anchor is safe at sample scale, so the unprojected call keeps it"
    )
    assert year_sig not in _passes(projected), (
        "at 1M rows that same anchor is a ~25K-row block; it must be dropped"
    )


def test_projection_is_linear_in_rows():
    """Pinned because the guard's correctness rests on it: a fixed-cardinality
    key's largest block holds a roughly fixed FRACTION of rows."""
    assert project_max_block_size(75, 3_000, 1_000_000) > 20_000
    assert project_max_block_size(75, 3_000, 3_000) == 75


def test_full_frame_callers_are_unchanged():
    """`auto_configure_probabilistic_df` passes the full frame and no
    `n_rows_full`. That path is already correct and must stay byte-identical."""
    df = _frame()
    profiles = profile_columns(df)
    base = build_blocking(profiles, df)
    assert _passes(_diversify_probabilistic_blocking(base, profiles, df)) == _passes(
        _diversify_probabilistic_blocking(base, profiles, df, n_rows_full=None)
    )


def test_n_rows_full_below_sample_height_is_a_noop():
    """Defensive: a caller passing a smaller full-N than the frame it handed us
    must not cause the projection to SHRINK a block and wave through a pass the
    unprojected guard would have dropped."""
    df = _frame()
    profiles = profile_columns(df)
    base = build_blocking(profiles, df)
    assert _passes(
        _diversify_probabilistic_blocking(base, profiles, df, n_rows_full=10)
    ) == _passes(_diversify_probabilistic_blocking(base, profiles, df))
