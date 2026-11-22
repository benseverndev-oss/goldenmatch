"""Ingest stops copying a polars input twice; the Arrow lane builds golden with
the fused kernel.

Two memory fixes from a 14M-row trace:

1. #3003's original-values capture is a free reference only when the table it
   reads IS Arrow. For a polars input it used to convert the frame itself -- a
   second full Arrow copy beside the lane eviction's, resident for the run. It
   now captures from the Arrow table the pipeline runs on. Guarded here by
   buffer identity: every captured column must be the lane table's own buffers.

2. The fused golden kernel declines fast-path-eligible configs only on the
   polars backend, but the pipeline gated the attempt on the polars-only
   eligibility check, so the default Arrow lane converted the whole source to
   polars to build golden there. On Arrow the kernel now runs. Field values are
   unchanged; ``__golden_confidence__`` follows the reference ``merge_field``
   semantics (the columnar builder approximated most_complete as 0.7 whenever
   values differ), and the column order is kept.
"""

from __future__ import annotations

import polars as pl
import pyarrow as pa
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenFieldRule,
    GoldenMatchConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core import pipeline as P
from goldenmatch.core.frame import to_frame


def _kernel_present() -> bool:
    try:
        from goldenmatch.core._native_loader import native_module

        return hasattr(native_module(), "golden_fused")
    except Exception:
        return False


requires_kernel = pytest.mark.skipif(
    not _kernel_present(),
    reason="golden_fused native kernel not built (build_native.py); CI builds it",
)


def _dupes(n_clusters: int = 12) -> pl.DataFrame:
    """Triples sharing an email. Names differ only by a trailing '.', so the
    longest value is unique: reference most_complete confidence is 1.0, the
    columnar approximation's is 0.7."""
    rows = []
    for c in range(n_clusters):
        for m in range(3):
            rows.append({"name": f"Person {c}" + ("." if m == 1 else ""),
                         "email": f"c{c}@example.com", "zip": f"100{c % 9:02d}"})
    return pl.DataFrame(rows)


def _config(*, field_rule: bool) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="email_exact", type="exact",
                                  fields=[MatchkeyField(field="email")])],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])],
                                max_block_size=100, skip_oversized=False),
        golden_rules=GoldenRulesConfig(
            default_strategy="most_complete",
            # A field rule forces the reference (merge_field) path; without it
            # the config is fast-path eligible -- the case this file fixes.
            field_rules={"name": GoldenFieldRule(strategy="most_complete")} if field_rule else {},
            quality_weighting=False,
        ),
    )


def _golden(res) -> pl.DataFrame:
    g = res["golden"]
    return (g if isinstance(g, pl.DataFrame) else pl.from_arrow(g)).sort("__cluster_id__")


def _buffer_addresses(col: pa.ChunkedArray) -> list[int]:
    return [b.address for chunk in col.chunks for b in chunk.buffers() if b is not None]


def test_original_input_references_the_lane_table(monkeypatch):
    """A polars input is converted to Arrow once; the originals are a reference
    into that table, not a second conversion."""
    captured: dict = {}
    real_capture = P.capture_original_input
    real_pipeline = P._run_dedupe_pipeline

    def spy_capture(table, *a, **kw):
        out = real_capture(table, *a, **kw)
        captured["originals"] = out
        return out

    def spy_pipeline(combined, *a, **kw):
        captured["lane"] = combined
        return real_pipeline(combined, *a, **kw)

    monkeypatch.setattr(P, "capture_original_input", spy_capture)
    monkeypatch.setattr(P, "_run_dedupe_pipeline", spy_pipeline)
    P.run_dedupe_df(_dupes(), _config(field_rule=True))

    lane = to_frame(captured["lane"]).native
    originals = captured["originals"]
    assert isinstance(lane, pa.Table), "polars input should have been moved to the Arrow lane"
    assert originals is not None and originals.num_rows == lane.num_rows
    for name in originals.column_names:
        assert _buffer_addresses(originals.column(name)) == _buffer_addresses(lane.column(name)), (
            f"original-values column {name!r} is a copy, not a reference into the lane table"
        )


@requires_kernel
def test_arrow_lane_fast_config_uses_fused_kernel(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_GOLDEN_FUSED", raising=False)
    monkeypatch.delenv("GOLDENMATCH_FRAME", raising=False)
    called = {"frames_builder": 0}
    import goldenmatch.core.golden as G

    real = G.build_golden_records_from_frames

    def spy(*a, **kw):
        called["frames_builder"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(G, "build_golden_records_from_frames", spy)
    res = P.run_dedupe_df(_dupes(), _config(field_rule=False))
    assert res["golden_fused_used"] is True
    assert called["frames_builder"] == 0, (
        "the Arrow lane fell back to the polars frames builder (a whole-source conversion)"
    )


@requires_kernel
def test_arrow_lane_fast_config_matches_reference_semantics(monkeypatch):
    """Same values as the merge_field reference path; leading column order kept."""
    monkeypatch.delenv("GOLDENMATCH_GOLDEN_FUSED", raising=False)
    monkeypatch.delenv("GOLDENMATCH_FRAME", raising=False)
    fast = _golden(P.run_dedupe_df(_dupes(), _config(field_rule=False)))
    ref = _golden(P.run_dedupe_df(_dupes(), _config(field_rule=True)))
    assert fast.height == 12
    assert fast.columns[:2] == ["__cluster_id__", "__golden_confidence__"]
    assert fast.select(sorted(fast.columns)).equals(ref.select(sorted(ref.columns)))
    # The unique-longest name is exact-confidence 1.0 under the reference; the old
    # columnar approximation reported (0.7 + 1 + 1) / 3 here.
    assert fast["__golden_confidence__"].min() == pytest.approx(1.0)
