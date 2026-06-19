"""MinHash/LSH blocking (#1081).

A near-duplicate candidate-generation blocker built on the sketch kernel
(``core/sketch.py``): shingle each record's text column, MinHash it, bucket the
signature into bands, and emit one block per non-singleton ``(band, bucket)``
group. Records sharing >= 1 bucket are candidates; a pair colliding in several
bands is scored once thanks to the downstream ``(min, max)`` pair de-dup (or use
``candidate_pairs`` for a de-duplicated pair set directly).

Conforms to the existing ``BlockResult`` blocker contract; ``blocker.build_blocks``
dispatches here for ``strategy="lsh"``.
"""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from goldenmatch.config.schemas import BlockingConfig, LSHKeyConfig
from goldenmatch.core import sketch


@dataclass
class MinHashLSHBlocker:
    """Resolved MinHash/LSH parameters + the bucketing/blocking operations."""

    mode: str
    k: int
    num_perms: int
    num_bands: int
    seed: int

    @classmethod
    def from_config(cls, lsh: LSHKeyConfig) -> MinHashLSHBlocker:
        if lsh.num_bands is not None:
            num_bands = lsh.num_bands
        else:
            # threshold-driven: optimal_bands picks the (b, r) split.
            assert lsh.threshold is not None  # guaranteed by LSHKeyConfig validation
            num_bands, _ = sketch.optimal_bands(lsh.num_perms, lsh.threshold)
        return cls(lsh.mode, lsh.k, lsh.num_perms, num_bands, lsh.seed)

    def _empty_sentinel(self) -> list[int]:
        """Band hashes of an empty record (all-``u64::MAX`` signature).

        Empty / whitespace-only texts have nothing to block on; they would
        otherwise all collide into one giant block. We detect and drop them by
        comparing against this deterministic sentinel (a non-empty record cannot
        produce an all-MAX signature, so the comparison is exact).
        """
        return sketch.sketch_band_hashes(
            "", self.mode, self.k, self.num_perms, self.num_bands, self.seed
        )

    def buckets(self, texts: list[str]) -> dict[tuple[int, int], list[int]]:
        """Map ``(band_idx, bucket_hash)`` -> row positions, skipping empty rows."""
        band_hashes = sketch.band_hashes_batch(
            texts, self.mode, self.k, self.num_perms, self.num_bands, self.seed
        )
        sentinel = self._empty_sentinel()
        groups: dict[tuple[int, int], list[int]] = {}
        for row_idx, bands in enumerate(band_hashes):
            if bands == sentinel:  # empty / whitespace-only: no content to block on
                continue
            for band_idx, bucket in enumerate(bands):
                groups.setdefault((band_idx, bucket), []).append(row_idx)
        return groups

    def candidate_pairs(self, texts: list[str]) -> set[tuple[int, int]]:
        """De-duplicated ``(min, max)`` candidate pairs across all bands."""
        pairs: set[tuple[int, int]] = set()
        for members in self.buckets(texts).values():
            if len(members) < 2:
                continue
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a, b = members[i], members[j]
                    pairs.add((a, b) if a < b else (b, a))
        return pairs

    def blocks(self, df: pl.DataFrame, texts: list[str]) -> list:
        """One ``BlockResult`` per non-singleton ``(band, bucket)`` group."""
        from goldenmatch.core.blocker import BlockResult

        results: list[BlockResult] = []
        for (band_idx, bucket), members in self.buckets(texts).items():
            if len(members) < 2:
                continue
            block_df = df[members]  # positional row select (preserves __row_id__)
            results.append(
                BlockResult(
                    block_key=f"lsh_b{band_idx}_{bucket}",
                    df=block_df.lazy(),
                    strategy="minhash_lsh",
                )
            )
        return results


def build_lsh_blocks(lf: pl.LazyFrame, config: BlockingConfig) -> list:
    """Build MinHash/LSH blocks for ``strategy="lsh"`` (called by ``build_blocks``)."""
    if config.lsh is None:
        raise ValueError("LSH blocking requires a 'lsh' config block.")
    df = lf.collect()
    if config.lsh.column not in df.columns:
        raise ValueError(f"LSH blocking column {config.lsh.column!r} not found in data.")
    texts = df[config.lsh.column].cast(pl.Utf8).fill_null("").to_list()
    return MinHashLSHBlocker.from_config(config.lsh).blocks(df, texts)
