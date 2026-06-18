"""Unit tests for the deterministic config-weakness generator.

`diagnose_config(df, config, result)` maps engine signals (the resolved
config + profile + postflight signals) to plain-English findings. These tests
construct the inputs DIRECTLY (small DataFrame, hand-built GoldenMatchConfig,
minimal DedupeResult carrying a populated postflight_report) so each detector
is exercised at unit level with no engine run — fast and deterministic.

Covered: the three named findings (source_admitted, shared_value_block,
null_sink), plus id_admitted / low_signal_key / over_merge; the no-LLM
phrasing="plain" rendering; severity ranking + max_findings truncation; and
the clean-data "no findings" path.
"""

from __future__ import annotations

import polars as pl
from goldenmatch._api import DedupeResult
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_verify import PostflightReport
from goldenmatch.core.config_critique import diagnose_config

# ── Builders ────────────────────────────────────────────────────────────────


def _exact_config(*, fields, blocking_fields=None, exclude=None):
    """A minimal exact-matchkey config.

    An exact matchkey needs no threshold/scorer/weight and (unlike
    weighted/probabilistic) does NOT require a blocking config, so this builds
    a valid GoldenMatchConfig from just a field list. ``blocking_fields``, when
    given, attaches a static blocking key over those columns.
    """
    blocking = None
    if blocking_fields is not None:
        blocking = BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=list(blocking_fields))],
        )
    return GoldenMatchConfig(
        blocking=blocking,
        matchkeys=[
            MatchkeyConfig(
                name="mk",
                type="exact",
                fields=[MatchkeyField(field=f) for f in fields],
            )
        ],
        exclude_columns=list(exclude or []),
    )


def _weighted_config(*, fields_weights, blocking_fields, threshold=0.7):
    """A weighted-matchkey config (requires blocking + per-field scorer/weight)."""
    return GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=list(blocking_fields))],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="mk",
                type="weighted",
                threshold=threshold,
                fields=[
                    MatchkeyField(field=f, scorer="jaro_winkler", weight=w)
                    for f, w in fields_weights
                ],
            )
        ],
    )


def _result(*, signals=None, clusters=None):
    """A minimal DedupeResult carrying an optional postflight_report."""
    report = None
    if signals is not None:
        report = PostflightReport(signals=signals)
    return DedupeResult(
        clusters=clusters or {},
        postflight_report=report,
    )


# ── source_admitted ───────────────────────────────────────────────────────────


def test_source_admitted_fires_high():
    df = pl.DataFrame({
        "name": ["a", "b", "c"],
        "source": ["crm", "events", "crm"],
    })
    cfg = _exact_config(fields=["name", "source"])
    out = diagnose_config(df, cfg, _result())

    ids = [f["id"] for f in out["findings"]]
    assert "source_admitted" in ids
    finding = next(f for f in out["findings"] if f["id"] == "source_admitted")
    assert finding["severity"] == "high"
    assert finding["evidence"]["column"] == "source"
    assert finding["fix_config_hint"] == {
        "action": "exclude_column",
        "column": "source",
    }


def test_source_admitted_skipped_when_already_excluded():
    df = pl.DataFrame({
        "name": ["a", "b", "c"],
        "source": ["crm", "events", "crm"],
    })
    # 'source' referenced by config but also in exclude_columns -> not a weakness.
    cfg = _exact_config(fields=["name"], exclude=["source"])
    out = diagnose_config(df, cfg, _result())
    assert all(f["id"] != "source_admitted" for f in out["findings"])


# ── id_admitted ────────────────────────────────────────────────────────────────


def test_id_admitted_fires_on_id_suffix():
    df = pl.DataFrame({
        "name": ["a", "b", "c"],
        "customer_id": ["1", "2", "3"],
    })
    cfg = _exact_config(fields=["name", "customer_id"])
    out = diagnose_config(df, cfg, _result())

    ids = [f["id"] for f in out["findings"]]
    assert "id_admitted" in ids
    finding = next(f for f in out["findings"] if f["id"] == "id_admitted")
    assert finding["severity"] == "high"
    assert finding["fix_config_hint"]["action"] == "exclude_column"
    assert finding["fix_config_hint"]["column"] == "customer_id"


# ── shared_value_block / oversized_block ───────────────────────────────────────


def test_shared_value_block_fires_from_postflight_percentiles():
    df = pl.DataFrame({
        "name": ["a", "b", "c"],
        "state": ["NY", "NY", "CA"],
    })
    cfg = _exact_config(fields=["name"], blocking_fields=["state"])
    # Oversized block: p99 way over the 5000 sanity ceiling.
    signals = {"block_size_percentiles": {"p50": 4, "p95": 900, "p99": 12000, "max": 30000}}
    out = diagnose_config(df, cfg, _result(signals=signals))

    ids = [f["id"] for f in out["findings"]]
    assert "shared_value_block" in ids
    finding = next(f for f in out["findings"] if f["id"] == "shared_value_block")
    assert finding["severity"] in ("high", "medium")
    assert finding["evidence"]["p99"] == 12000
    assert finding["evidence"]["max"] == 30000
    assert finding["fix_config_hint"]["action"] in (
        "tighten_blocking",
        "compound_blocking",
    )


def test_shared_value_block_silent_when_blocks_healthy():
    df = pl.DataFrame({"name": ["a", "b", "c"]})
    cfg = _exact_config(fields=["name"], blocking_fields=["name"])
    signals = {"block_size_percentiles": {"p50": 2, "p95": 10, "p99": 40, "max": 60}}
    out = diagnose_config(df, cfg, _result(signals=signals))
    assert all(f["id"] != "shared_value_block" for f in out["findings"])


# ── null_sink ──────────────────────────────────────────────────────────────────


def test_null_sink_fires_above_threshold():
    # 'phone' is 60% null among a matching column -> null_sink.
    df = pl.DataFrame({
        "name": ["a", "b", "c", "d", "e"],
        "phone": ["555", None, None, None, "777"],
    })
    cfg = _exact_config(fields=["name", "phone"])
    out = diagnose_config(df, cfg, _result())

    ids = [f["id"] for f in out["findings"]]
    assert "null_sink" in ids
    finding = next(f for f in out["findings"] if f["id"] == "null_sink")
    assert finding["severity"] == "medium"
    assert finding["evidence"]["column"] == "phone"
    assert finding["evidence"]["null_rate"] > 0.2


def test_null_sink_silent_when_column_dense():
    df = pl.DataFrame({
        "name": ["a", "b", "c", "d", "e"],
        "phone": ["1", "2", "3", "4", "5"],
    })
    cfg = _exact_config(fields=["name", "phone"])
    out = diagnose_config(df, cfg, _result())
    assert all(f["id"] != "null_sink" for f in out["findings"])


# ── low_signal_key ─────────────────────────────────────────────────────────────


def test_low_signal_key_fires_on_constant_column():
    # 'country' is constant (cardinality_ratio ~ 0.005 << 0.01) over 200 rows.
    df = pl.DataFrame({
        "name": [f"n{i}" for i in range(200)],
        "country": ["US"] * 200,
    })
    cfg = _exact_config(fields=["name", "country"])
    out = diagnose_config(df, cfg, _result())

    ids = [f["id"] for f in out["findings"]]
    assert "low_signal_key" in ids
    finding = next(f for f in out["findings"] if f["id"] == "low_signal_key")
    assert finding["severity"] == "low"
    assert finding["evidence"]["column"] == "country"


def test_null_sink_wins_over_low_signal_key_on_same_column():
    # A mostly-null column also reads as low-cardinality (few distinct non-null
    # values). The emptiness is the root cause, so a single column must never
    # produce BOTH null_sink and low_signal_key — null_sink wins.
    df = pl.DataFrame({
        "name": [f"n{i}" for i in range(200)],
        "phone": (["555"] + [None] * 199),  # 99.5% null + 1 distinct value
    })
    cfg = _exact_config(fields=["name", "phone"])
    out = diagnose_config(df, cfg, _result())

    by_col: dict[str, set[str]] = {}
    for f in out["findings"]:
        col = f.get("evidence", {}).get("column")
        if col is not None:
            by_col.setdefault(col, set()).add(f["id"])
    for col, ids in by_col.items():
        assert not ({"null_sink", "low_signal_key"} <= ids), (
            f"column {col!r} fired both null_sink and low_signal_key"
        )
    assert any(f["id"] == "null_sink" for f in out["findings"])


# ── over_merge ─────────────────────────────────────────────────────────────────


def test_over_merge_fires_on_oversized_clusters():
    df = pl.DataFrame({"name": ["a", "b", "c"]})
    cfg = _exact_config(fields=["name"])
    signals = {
        "oversized_clusters": [{"cluster_id": 7, "size": 4200, "bottleneck_pair": [1, 2]}],
        "preliminary_cluster_sizes": {"p50": 2, "p95": 9, "p99": 50, "max": 4200, "count": 100},
    }
    out = diagnose_config(df, cfg, _result(signals=signals))

    ids = [f["id"] for f in out["findings"]]
    assert "over_merge" in ids
    finding = next(f for f in out["findings"] if f["id"] == "over_merge")
    assert finding["severity"] == "high"
    assert finding["fix_config_hint"]["action"] == "raise_threshold"
    assert finding["evidence"]["max_cluster_size"] == 4200


# ── phrasing="plain" renders the plain fields, no LLM ──────────────────────────


def test_plain_phrasing_renders_plain_fields_no_llm(monkeypatch):
    # Hard-guarantee no LLM path: even if a key is present, the gate env is off.
    monkeypatch.delenv("GOLDENMATCH_WEAKNESS_LLM", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-used")

    df = pl.DataFrame({
        "name": ["a", "b", "c"],
        "source": ["crm", "events", "crm"],
    })
    cfg = _exact_config(fields=["name", "source"])
    out = diagnose_config(df, cfg, _result(), phrasing="plain")

    assert out["findings"], "expected at least the source_admitted finding"
    for f in out["findings"]:
        for key in ("title_plain", "detail_plain", "fix_plain"):
            assert isinstance(f[key], str) and f[key], f"{key} must be non-empty"
        # plain wording must not leak the raw metric/key jargon that the
        # technical phrasing would use.
        assert "cardinality_ratio" not in f["detail_plain"]
    assert isinstance(out["summary_plain"], str) and out["summary_plain"]


def test_technical_phrasing_is_deterministic_and_distinct():
    df = pl.DataFrame({
        "name": ["a", "b", "c"],
        "source": ["crm", "events", "crm"],
    })
    cfg = _exact_config(fields=["name", "source"])
    plain = diagnose_config(df, cfg, _result(), phrasing="plain")
    tech = diagnose_config(df, cfg, _result(), phrasing="technical")
    # Same finding set, both deterministic.
    assert [f["id"] for f in plain["findings"]] == [f["id"] for f in tech["findings"]]
    # Re-running technical twice is byte-identical (no LLM, no randomness).
    tech2 = diagnose_config(df, cfg, _result(), phrasing="technical")
    assert tech == tech2


# ── ranking + max_findings truncation ──────────────────────────────────────────


def test_findings_ranked_high_to_low_and_truncated():
    # Build a config that fires several detectors of mixed severity:
    #  - source (high), customer_id (high)  -> high
    #  - phone null_sink (medium)
    #  - country low_signal_key (low)
    df = pl.DataFrame({
        "name": [f"n{i}" for i in range(200)],
        "source": (["crm", "events"] * 100),
        "customer_id": [str(i) for i in range(200)],
        "phone": (["555"] + [None] * 199),
        "country": ["US"] * 200,
    })
    cfg = _exact_config(fields=["name", "source", "customer_id", "phone", "country"])
    out = diagnose_config(df, cfg, _result())

    sevs = [f["severity"] for f in out["findings"]]
    rank = {"high": 0, "medium": 1, "low": 2}
    assert sevs == sorted(sevs, key=lambda s: rank[s]), "findings must be high->low"

    # max_findings truncates AFTER ranking — keep the most severe.
    capped = diagnose_config(df, cfg, _result(), max_findings=2)
    assert len(capped["findings"]) == 2
    assert all(f["severity"] == "high" for f in capped["findings"])


# ── clean data: no findings ─────────────────────────────────────────────────────


def test_clean_single_source_no_findings():
    df = pl.DataFrame({
        "name": ["alice", "bob", "carol", "dave"],
        "email": ["a@x.com", "b@x.com", "c@x.com", "d@x.com"],
    })
    cfg = _exact_config(fields=["name", "email"], blocking_fields=["email"])
    signals = {
        "block_size_percentiles": {"p50": 1, "p95": 2, "p99": 3, "max": 3},
        "oversized_clusters": [],
        "preliminary_cluster_sizes": {"p50": 1, "p95": 2, "p99": 2, "max": 2, "count": 4},
    }
    out = diagnose_config(df, cfg, _result(signals=signals))
    assert out["findings"] == []
    assert isinstance(out["summary_plain"], str) and out["summary_plain"]


# ── robustness: missing signals never raise ─────────────────────────────────────


def test_no_postflight_report_does_not_raise():
    df = pl.DataFrame({"name": ["a", "b", "c"]})
    cfg = _exact_config(fields=["name"], blocking_fields=["name"])
    # result with postflight_report=None — block/over_merge detectors must skip.
    out = diagnose_config(df, cfg, DedupeResult())
    assert "findings" in out and "summary_plain" in out


def test_empty_dataframe_does_not_raise():
    df = pl.DataFrame({"name": []}, schema={"name": pl.Utf8})
    cfg = _exact_config(fields=["name"])
    out = diagnose_config(df, cfg, _result())
    assert isinstance(out["findings"], list)
