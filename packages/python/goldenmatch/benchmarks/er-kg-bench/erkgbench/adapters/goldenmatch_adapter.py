"""goldenmatch adapters -- the system under test.

Two configurations:

* ``goldenmatch(name)``  -- name string only, the apples-to-apples comparison
  against every framework's name-based default.
* ``goldenmatch(+ctx)`` -- name + a context field, demonstrating the
  multi-field advantage that lets it KEEP distinct entities apart on the
  precision-critical classes (two "First National Bank"s, BTC 2020 vs 2024)
  where single-string resolvers over-merge.

Both call the public ``goldenmatch.dedupe_df`` API. Cluster members come back in
``__row_id__`` space, which equals input row order, so they map straight to
record indices (the harness always resolves the full set, indices 0..n-1).
"""

from __future__ import annotations

import goldenmatch as gm
import polars as pl

from .base import Record


class GoldenMatchAdapter:
    deterministic = True

    def __init__(self, threshold: float = 0.82, use_context: bool = False) -> None:
        self.threshold = threshold
        self.use_context = use_context
        self.name = "goldenmatch(+ctx)" if use_context else "goldenmatch(name)"
        fields = "name+context" if use_context else "name"
        self.defaults = (
            f"dedupe_df fuzzy={{{fields}}} @ {threshold} "
            "(probabilistic multi-field scoring + blocking + clustering)"
        )

    def resolve(self, records: list[Record]) -> list[list[int]]:
        # Resolve the full set in index order so __row_id__ == record index.
        ordered = sorted(records, key=lambda r: r.index)
        data = {"name": [r.mention for r in ordered]}
        fuzzy = {"name": self.threshold}
        if self.use_context:
            data["context"] = [r.context for r in ordered]
            fuzzy["context"] = 0.5
        df = pl.DataFrame(data)
        result = gm.dedupe_df(df, fuzzy=fuzzy)
        clusters = [
            list(info["members"])
            for info in result.clusters.values()
            if info.get("size", len(info["members"])) > 1
        ]
        return clusters
