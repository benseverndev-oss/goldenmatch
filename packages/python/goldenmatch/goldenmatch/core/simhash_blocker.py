"""SimHash/LSH blocking over dense embeddings (#1082).

A *semantic* near-duplicate candidate-generation blocker built on the SimHash
kernel (``core/sketch.py``): embed each record's text column, SimHash-project
the embedding through random hyperplanes, band the 0/1 signature into LSH
buckets, and emit one block per non-singleton ``(band, bucket)`` group. Records
whose embeddings are cosine-near collide in a band, so they become candidates.

Complements ``lsh_blocker.MinHashLSHBlocker`` (lexical shingle MinHash/LSH):
SimHash buckets dense vectors, MinHash buckets sparse shingle sets. Conforms to
the existing ``BlockResult`` blocker contract; ``blocker.build_blocks``
dispatches here for ``strategy="simhash"``.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from goldenmatch._polars_lazy import pl
from goldenmatch.config.schemas import BlockingConfig, SimHashKeyConfig
from goldenmatch.core import sketch


@dataclass
class SimHashLSHBlocker:
    """Resolved SimHash/LSH parameters + the bucketing/blocking operations."""

    num_planes: int
    num_bands: int
    seed: int

    @classmethod
    def from_config(cls, cfg: SimHashKeyConfig) -> SimHashLSHBlocker:
        if cfg.num_bands is not None:
            num_bands = cfg.num_bands
        else:
            # threshold-driven: optimal_bands picks the (b, r) split. The same
            # host-side (b, r) selector the MinHash path uses — num_planes plays
            # the role of num_perms (both are the signature length).
            assert cfg.threshold is not None  # guaranteed by SimHashKeyConfig validation
            num_bands, _ = sketch.optimal_bands(cfg.num_planes, cfg.threshold)
        return cls(cfg.num_planes, num_bands, cfg.seed)

    def _empty_sentinel(self, dim: int) -> list[int]:
        """Band hashes of an all-zero embedding (all-ones SimHash signature).

        An all-zero embedding carries no direction to block on; such rows would
        otherwise all collide into one giant block. We detect and drop them by
        comparing against this deterministic sentinel — the band hashes of the
        all-ones signature that a zero vector projects to (every plane's dot is
        exactly 0.0, and the ``dot >= 0.0`` tie resolves to 1).
        """
        sig = sketch.simhash_signature([0.0] * dim, self.num_planes, self.seed)
        return sketch.simhash_band_hashes(sig, self.num_bands)

    def buckets(self, embeddings: np.ndarray) -> dict[tuple[int, int], list[int]]:
        """Map ``(band_idx, bucket_hash)`` -> row positions, skipping zero rows.

        ``embeddings`` is an ``(n, dim)`` float64 array.
        """
        emb = np.asarray(embeddings, dtype=np.float64)
        if emb.ndim != 2:
            raise ValueError(f"embeddings must be 2-D (n, dim); got shape {emb.shape}")
        vectors = emb.tolist()
        band_hashes = sketch.simhash_band_hashes_batch(
            vectors, self.num_planes, self.num_bands, self.seed
        )
        sentinel = self._empty_sentinel(emb.shape[1]) if emb.shape[0] else []
        groups: dict[tuple[int, int], list[int]] = {}
        for row_idx, bands in enumerate(band_hashes):
            if bands == sentinel:  # all-zero embedding: no direction to block on
                continue
            for band_idx, bucket in enumerate(bands):
                groups.setdefault((band_idx, bucket), []).append(row_idx)
        return groups

    def candidate_pairs(self, embeddings: np.ndarray) -> set[tuple[int, int]]:
        """De-duplicated ``(min, max)`` candidate pairs across all bands."""
        pairs: set[tuple[int, int]] = set()
        for members in self.buckets(embeddings).values():
            if len(members) < 2:
                continue
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a, b = members[i], members[j]
                    pairs.add((a, b) if a < b else (b, a))
        return pairs

    def blocks(self, df: pl.DataFrame, embeddings: np.ndarray) -> list:
        """One ``BlockResult`` per non-singleton ``(band, bucket)`` group."""
        from goldenmatch.core.blocker import BlockResult

        results: list[BlockResult] = []
        for (band_idx, bucket), members in self.buckets(embeddings).items():
            if len(members) < 2:
                continue
            block_df = df[members]  # positional row select (preserves __row_id__)
            results.append(
                BlockResult(
                    block_key=f"simhash_b{band_idx}_{bucket}",
                    df=block_df.lazy(),
                    strategy="simhash_lsh",
                )
            )
        return results


def build_simhash_blocks(lf: pl.LazyFrame, config: BlockingConfig) -> list:
    """Build SimHash/LSH blocks for ``strategy="simhash"`` (called by ``build_blocks``)."""
    if config.simhash is None:
        raise ValueError("SimHash blocking requires a 'simhash' config block.")
    df = lf.collect()
    column = config.simhash.column
    if column not in df.columns:
        raise ValueError(f"SimHash blocking column {column!r} not found in data.")

    from goldenmatch.core.embedder import get_embedder

    values = df[column].cast(pl.Utf8).fill_null("").to_list()
    model = config.simhash.model
    embedder = get_embedder(model) if model is not None else get_embedder()
    emb = embedder.embed_column(values, cache_key=f"simhash:{column}")
    emb = np.asarray(emb, dtype=np.float64)
    return SimHashLSHBlocker.from_config(config.simhash).blocks(df, emb)
