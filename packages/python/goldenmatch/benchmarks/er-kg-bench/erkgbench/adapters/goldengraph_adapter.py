"""goldengraph engine adapter -- SP6 Half 1.

Runs the goldengraph KG engine's resolution step (`goldengraph.resolve`, which
wraps goldenmatch's zero-config `dedupe_df` on name+type -- "Provided mode") over
the bench corpus, so the new native KG engine appears in the headline table next
to goldenmatch and the frameworks. Parity is BY CONSTRUCTION (resolve wraps
dedupe_df); `tests/test_goldengraph_adapter.py` locks that the index mapping
doesn't drift.

The goldengraph-core NATIVE resolver (`ResolutionMode::Native`) is a DIFFERENT
algorithm, NOT parity-locked to dedupe_df -- a separate non-parity row, deferred.

goldengraph is imported lazily inside `resolve` so the harness can import the
adapter set even when goldengraph isn't installed; a missing import surfaces as
the runner's per-adapter error row, never fatal.
"""

from __future__ import annotations

from .base import AdapterBase, Record


class GoldenGraphAdapter(AdapterBase):
    deterministic = True
    fidelity = "real"
    name = "goldengraph"
    defaults = (
        "goldengraph engine: resolve (Provided mode) -> store; wraps goldenmatch "
        "dedupe_df on name+type"
    )

    def resolve(self, records: list[Record]) -> list[list[int]]:
        from goldengraph.extract import Mention
        from goldengraph.resolve import resolve as gg_resolve

        ordered = sorted(records, key=lambda r: r.index)
        mentions = [Mention(name=r.mention, typ=r.entity_type) for r in ordered]
        entities = gg_resolve(mentions)
        # ResolvedEntity.member_idx are positions in `ordered`; map back to the
        # caller's record indices (identity when records are 0..n-1 in order).
        idx_of = [r.index for r in ordered]
        return [[idx_of[p] for p in e.member_idx] for e in entities]
