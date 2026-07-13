"""Deep-D2 breadth sweep: run_golden_fused_arrow arrow-input vs polars-input
value-parity across the kernel's covered config surface.

Each scenario runs the SAME data through the polars-input branch (verbatim
legacy) and the arrow-input branch (deep-D2) and asserts value-for-value
equality. Scenarios where the kernel declines on BOTH representations pass
vacuously but assert the DECLINE agrees (a one-sided decline would silently
route the two lanes to different builders).
"""
from __future__ import annotations

from datetime import date, datetime

import polars as pl
import pyarrow as pa
import pytest

from goldenmatch.config.schemas import (
    GoldenFieldRule,
    GoldenGroupRule,
    GoldenRulesConfig,
)
from goldenmatch.core.golden_fused import run_golden_fused_arrow


def _base_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 10, 11, 20, 21],
            "__cluster_id__": [1, 1, 1, 2, 2, 3, 3],
            "__source__": ["crm", "web", "crm", "web", "crm", "web", "crm"],
            "name": ["Bob", "Robert", None, "Sue", "Suzanne", "Zed", "Zed"],
            "email": [None, "b@x.com", "b@x.com", "s@y.com", None, "z@z.com", "z@z.com"],
            "age": [30, None, 31, 44, 44, 9, None],
            "score": [1.5, 2.25, None, 0.001, 44.0, 1e-6, 2.0],
            "active": [True, False, None, True, True, False, False],
            "joined": [
                date(2020, 1, 1), date(2021, 6, 2), date(2019, 12, 31),
                date(2022, 3, 3), None, date(2020, 5, 5), date(2023, 1, 1),
            ],
            "seen_at": [
                datetime(2020, 1, 1, 10), None, datetime(2021, 2, 2, 9),
                datetime(2022, 3, 3, 8), datetime(2022, 3, 3, 7),
                datetime(2020, 5, 5, 6), datetime(2024, 1, 1, 5),
            ],
            "joined_str": [
                "2020-01-01", "2021-06-02", "2019-12-31",
                "2022-03-03", None, "2020-05-05", "2023-01-01",
            ],
        }
    )


SCENARIOS: list[tuple[str, GoldenRulesConfig, dict | None, dict | None]] = [
    ("most_complete_default", GoldenRulesConfig(default_strategy="most_complete"), None, None),
    ("first_non_null", GoldenRulesConfig(default_strategy="first_non_null"), None, None),
    ("longest_value", GoldenRulesConfig(default_strategy="longest_value"), None, None),
    ("majority_vote", GoldenRulesConfig(default_strategy="majority_vote"), None, None),
    ("unanimous_or_null", GoldenRulesConfig(default_strategy="unanimous_or_null"), None, None),
    (
        "source_priority",
        GoldenRulesConfig(
            default_strategy="most_complete",
            field_rules={
                "email": GoldenFieldRule(strategy="source_priority", source_priority=["crm", "web"])
            },
        ),
        None,
        None,
    ),
    (
        "most_recent_date32",
        GoldenRulesConfig(
            default_strategy="most_complete",
            field_rules={"name": GoldenFieldRule(strategy="most_recent", date_column="joined")},
        ),
        None,
        None,
    ),
    (
        "most_recent_timestamp",
        GoldenRulesConfig(
            default_strategy="most_complete",
            field_rules={"email": GoldenFieldRule(strategy="most_recent", date_column="seen_at")},
        ),
        None,
        None,
    ),
    (
        # string date column: the ORDER-SAFE gate must decline the date arrays
        # on BOTH lanes (lexical order != temporal order in general).
        "most_recent_string_date_declines_dates",
        GoldenRulesConfig(
            default_strategy="most_complete",
            field_rules={"name": GoldenFieldRule(strategy="most_recent", date_column="joined_str")},
        ),
        None,
        None,
    ),
    (
        "field_group_most_complete",
        GoldenRulesConfig(
            default_strategy="most_complete",
            field_groups=[
                GoldenGroupRule(name="contact", columns=["name", "email"], strategy="most_complete")
            ],
        ),
        None,
        None,
    ),
    (
        "field_group_source_priority",
        GoldenRulesConfig(
            default_strategy="most_complete",
            field_groups=[
                GoldenGroupRule(
                    name="contact",
                    columns=["name", "email"],
                    strategy="source_priority",
                    source_priority=["web", "crm"],
                )
            ],
        ),
        None,
        None,
    ),
    (
        "field_group_most_recent",
        GoldenRulesConfig(
            default_strategy="most_complete",
            field_groups=[
                GoldenGroupRule(
                    name="contact",
                    columns=["name", "email"],
                    strategy="most_recent",
                    date_column="seen_at",
                )
            ],
        ),
        None,
        None,
    ),
    (
        "quality_scores_weighting",
        GoldenRulesConfig(default_strategy="most_complete"),
        {(0, "name"): 0.2, (1, "name"): 1.0, (10, "email"): 0.1},
        None,
    ),
    (
        "confidence_majority_edges",
        GoldenRulesConfig(
            default_strategy="most_complete",
            field_rules={"email": GoldenFieldRule(strategy="confidence_majority")},
        ),
        None,
        {
            1: {(0, 1): 0.9, (1, 2): 0.6},
            2: {(10, 11): 0.8},
            3: {(20, 21): 0.7},
        },
    ),
    (
        "conditional_when",
        GoldenRulesConfig(
            default_strategy="most_complete",
            field_rules={
                "email": GoldenFieldRule(
                    strategy="first_non_null", when="active == 'true'"
                )
            },
        ),
        None,
        None,
    ),
]


@pytest.mark.parametrize(
    "label,rules,quality_scores,cluster_pair_scores",
    SCENARIOS,
    ids=[s[0] for s in SCENARIOS],
)
def test_arrow_input_matches_polars_input_breadth(
    monkeypatch, label, rules, quality_scores, cluster_pair_scores
):
    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    df = _base_df()
    got_pl = run_golden_fused_arrow(
        df, rules, quality_scores=quality_scores, cluster_pair_scores=cluster_pair_scores
    )
    got_pa = run_golden_fused_arrow(
        df.to_arrow(), rules,
        quality_scores=quality_scores, cluster_pair_scores=cluster_pair_scores,
    )
    # The DECLINE decision must agree between representations.
    assert (got_pl is None) == (got_pa is None), (
        f"{label}: one-sided decline (pl={got_pl is not None}, pa={got_pa is not None})"
    )
    if got_pl is None:
        pytest.skip(f"{label}: kernel declines this config (both lanes agree)")
    assert isinstance(got_pa, pa.Table)
    assert list(got_pa.column_names) == list(got_pl.columns), label
    for c in got_pl.columns:
        assert got_pa.column(c).to_pylist() == got_pl[c].to_list(), f"{label}: {c}"


# -- e2e breadth: decline-replay + quality bridge on the Frame lane -------------------


def _lane_run(tmp_path, monkeypatch, dirty=False):
    import goldenmatch.core.pipeline as P
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
        QualityConfig,
        TransformConfig,
    )

    csv = tmp_path / "people.csv"
    rows = ["first,last,city"]
    names = [
        ("ann", "smith"), ("anne", "smith"), ("bob", "jones"), ("bobby", "jones"),
        ("cara", "lee"), ("kara", "lee"), ("dan", "kim"), ("erin", "park"),
    ]
    for i, (f, l) in enumerate(names):
        city = "  " if dirty and i % 2 else f"c{i % 3}"  # blank-ish cells -> quality penalties
        rows.append(f"{f},{l},{city}")
    csv.write_text("\n".join(rows) + "\n", encoding="utf-8")
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="fuzzy_name", type="weighted", threshold=0.85,
                fields=[
                    MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0, transforms=["lowercase"]),
                    MatchkeyField(field="last", scorer="jaro_winkler", weight=1.0, transforms=["lowercase"]),
                ],
            )
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["last"], transforms=["lowercase"])]),
        quality=QualityConfig(mode="disabled"),
        transform=TransformConfig(mode="disabled"),
        golden_rules=GoldenRulesConfig(
            default_strategy="most_complete",
            field_rules={"city": GoldenFieldRule(strategy="majority_vote")},
        ),
    )

    def norm(r):
        g = r["golden"]
        rows = (
            sorted((tuple(sorted(x.items())) for x in g.to_pylist()), key=str)
            if g is not None
            else None
        )
        return (rows, len(r["clusters"]))

    frame_lane = P.run_dedupe([(str(csv), "people")], cfg)
    monkeypatch.setenv("GOLDENMATCH_FRAME_LANE", "0")
    classic = P.run_dedupe([(str(csv), "people")], cfg)
    monkeypatch.delenv("GOLDENMATCH_FRAME_LANE")
    return norm(frame_lane), norm(classic)


def test_frame_lane_parity_when_kernel_absent(tmp_path, monkeypatch):
    """Decline-replay breadth: with the native kernel unavailable, the Frame
    lane bridges + replays the polars demux -- output must equal classic."""
    import goldenmatch.core.golden_fused as GF

    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    monkeypatch.setattr(GF, "_native_golden_symbol", lambda: None)
    a, b = _lane_run(tmp_path, monkeypatch)
    assert a == b


def test_frame_lane_parity_with_quality_penalties(tmp_path, monkeypatch):
    """quality_weighting bridge breadth: dirty cells produce non-None
    quality_scores through the bridged compute_quality_scores -- parity holds."""
    monkeypatch.setenv("GOLDENMATCH_FRAME", "arrow")
    a, b = _lane_run(tmp_path, monkeypatch, dirty=True)
    assert a == b
