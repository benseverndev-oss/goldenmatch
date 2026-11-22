"""Ingest converts a polars input to Arrow once.

#3003's original-values capture is a free reference only when the table it reads
IS Arrow. For a polars input it used to convert the frame itself -- a second full
Arrow copy beside the lane eviction's (polars string views widen to large_string),
resident for the whole run: ~2.2 GB of a 14M-row run's footprint. It now captures
from the Arrow table the pipeline runs on. Guarded here by buffer identity: every
captured column must be the lane table's own buffers.
"""

from __future__ import annotations

import polars as pl
import pyarrow as pa
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core import pipeline as P
from goldenmatch.core.frame import to_frame


def _dupes(n_clusters: int = 12) -> pl.DataFrame:
    rows = []
    for c in range(n_clusters):
        for m in range(3):
            rows.append({"name": f"Person {c}" + ("." if m == 1 else ""),
                         "email": f"c{c}@example.com", "zip": f"100{c % 9:02d}"})
    return pl.DataFrame(rows)


def _config() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="email_exact", type="exact",
                                  fields=[MatchkeyField(field="email")])],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])],
                                max_block_size=100, skip_oversized=False),
    )


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
    P.run_dedupe_df(_dupes(), _config())

    lane = to_frame(captured["lane"]).native
    originals = captured["originals"]
    assert isinstance(lane, pa.Table), "polars input should have been moved to the Arrow lane"
    assert originals is not None and originals.num_rows == lane.num_rows
    for name in originals.column_names:
        assert _buffer_addresses(originals.column(name)) == _buffer_addresses(lane.column(name)), (
            f"original-values column {name!r} is a copy, not a reference into the lane table"
        )
