"""A diversified blocking pass must fit the PAIR budget, not just the row cap.

`_diversify_probabilistic_blocking` adds orthogonal anchors (a date column's
YEAR, postcode/zip/identifier) so the FS candidate set is not gated entirely on
corrupted name keys. Its only guard was the FS scorer's per-block ROW cap
(`sqrt(GOLDENMATCH_FS_VEC_MAX_ELEMS)`, ~7,071), which bounds per-block MEMORY
and says nothing about how much WORK a pass creates. Those diverge badly for a
low-cardinality anchor.

Measured, person @ 100,000 rows (ablation run 32138842887), dropping one pass at
a time from the committed 8-pass plan:

    dropped pass                 comparisons saved       dF1
    [dob] substring:0:4                 71,337,394   +0.0000
    [first_name] soundex                31,321,187   +0.0000
    [surname] soundex                   12,286,109   +0.0000
    [surname] substring5                 6,138,978   +0.0000
    [postcode]                              47,022   -0.0269

Birth-YEAR is 59% of every comparison in the run and buys NOTHING. Postcode
carries all of the recall for 47K comparisons. Both clear the row cap easily
(1,549 and 9 rows), so the cap cannot tell them apart -- but in pairs/row they
are 774 vs 4, against the engine's own budget of 50
(`_blocking_pairs_per_row_budget`, "keeps the total pair count linear"), which
was applied when selecting a primary key and never to these additive passes.

End to end on that fixture, this bound takes the probabilistic plan from
121,391,850 comparisons to 50,054,456 -- byte-identical to the ablation's
`minus [dob] substring:0:4` arm, which measured pairwise F1 **0.9970**, the same
as the full plan.

## Why this is a bound and not a deletion

The lever exists for error-heavy PII (its docstring cites historical_50k
blocking_recall 0.585 -- 42% of true pairs never co-block because names are
corrupted while dob/postcode agree). An anchor that is wasteful on clean person
data may be the only thing co-blocking corrupted names elsewhere, and the
measurement here is person-shaped. So the rule is "fit the budget", the cheap
anchors still pass it, and `GOLDENMATCH_BLOCKING_DIVERSIFY_PAIR_BUDGET=0`
restores the old behaviour without a revert.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.core.autoconfig import (
    _blocking_pairs_per_row_budget,
    _diversify_probabilistic_blocking,
    _project_pairs_per_row,
    build_blocking,
    profile_columns,
)


def _frame(n: int = 4000):
    """`birth_date`'s YEAR has 25 distinct values (blocks ~n/25, far over budget);
    `postcode` has 1,000 (blocks ~n/1000, comfortably under)."""
    return pa.table({
        "record_id": [f"r{i}" for i in range(n)],
        "first_name": [f"name{i % 1200}" for i in range(n)],
        "surname": [f"sur{i % 1100}" for i in range(n)],
        "birth_date": [f"{1960 + (i % 25)}-01-01" for i in range(n)],
        "postcode": [f"{10000 + (i % 1000)}" for i in range(n)],
    })


def _passes(blocking):
    return [(tuple(p.fields), tuple(p.transforms or []))
            for p in (list(blocking.passes or []) or list(blocking.keys or []))]


_YEAR = (("birth_date",), ("substring:0:4",))
_ZIP = (("postcode",), ("strip",))


def _diversified(monkeypatch, enabled: str):
    monkeypatch.setenv("GOLDENMATCH_BLOCKING_DIVERSIFY_PAIR_BUDGET", enabled)
    df = _frame()
    profiles = profile_columns(df)
    base = build_blocking(profiles, df)
    return _passes(_diversify_probabilistic_blocking(base, profiles, df))


def test_the_fixture_puts_year_over_budget_and_zip_under():
    """Guard the guard: if both anchors landed on the same side of the budget,
    every assertion below would pass for the wrong reason."""
    budget = _blocking_pairs_per_row_budget()
    assert _project_pairs_per_row(4000 // 25) > budget
    assert _project_pairs_per_row(4000 // 1000) <= budget


def test_the_low_cardinality_anchor_is_dropped(monkeypatch):
    assert _YEAR not in _diversified(monkeypatch, "1")


def test_the_cheap_anchor_survives(monkeypatch):
    """postcode is the pass carrying recall (-0.0269 F1 when dropped at 100K);
    a bound that removed it would be strictly worse than no bound."""
    assert _ZIP in _diversified(monkeypatch, "1")


def test_the_kill_switch_restores_the_year_anchor(monkeypatch):
    """Pinned so the flag is real: the measurement behind the bound is
    person-shaped, and the lever's stated purpose is error-heavy PII."""
    assert _YEAR in _diversified(monkeypatch, "0")


def test_budget_is_the_engines_own_knob(monkeypatch):
    """Not a new constant. Raising the shared budget must let the year anchor
    back in, which is what makes this a bound rather than a special case."""
    monkeypatch.setenv("GOLDENMATCH_BLOCKING_PAIRS_PER_ROW", "100000")
    assert _YEAR in _diversified(monkeypatch, "1")
