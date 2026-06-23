"""Perceptual-hash LSH blocking (multimodal-ER crawl tier, ADR 0022).

Banded hamming-LSH over a column of fixed-width hex perceptual hashes (e.g. a
16-char / 64-bit image pHash produced by ``core.perceptual.phash_image``). Split
each hash into ``num_bands`` contiguous bit-bands; two hashes within a small
hamming distance share at least one identical band with high probability, so they
collide into a candidate block. Records that don't share any band are never
compared. Conforms to the ``BlockResult`` contract; ``blocker.build_blocks``
dispatches here for ``strategy="perceptual"``.

This is the *media* near-duplicate blocker, complementing the lexical MinHash/LSH
(``lsh_blocker``) and the semantic SimHash/LSH (``simhash_blocker``) paths.
"""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from goldenmatch.config.schemas import BlockingConfig, PerceptualKeyConfig


def _divisor_band_counts(hash_bits: int) -> list[int]:
    """Ascending band counts that evenly divide ``hash_bits`` (each band >= 1 bit,
    excluding the degenerate all-bits-one-band case). Ascending == most-reduction
    first, so the recommender returns the cheapest blocking that meets the target."""
    return [b for b in range(2, hash_bits + 1) if hash_bits % b == 0]


def lsh_collision_probability(
    hamming_frac: float, num_bands: int, hash_bits: int = 64
) -> float:
    """Probability that two hashes at hamming distance ``hamming_frac * hash_bits``
    share at least one identical band under banded hamming-LSH.

    A band of ``hash_bits // num_bands`` bits matches iff all its bits agree, with
    per-bit agreement ``1 - hamming_frac``; the hashes collide iff any of the
    ``num_bands`` bands matches. This is the standard banded-LSH S-curve and the
    basis for :func:`recommend_num_bands`."""
    band_width = hash_bits // num_bands
    per_band = (1.0 - hamming_frac) ** band_width
    return 1.0 - (1.0 - per_band) ** num_bands


def recommend_num_bands(
    hash_bits: int = 64,
    target_hamming_frac: float = 0.15,
    target_recall: float = 0.95,
) -> int:
    """Smallest band count whose LSH collision probability at ``target_hamming_frac``
    meets ``target_recall`` — the recall-vs-reduction knob, set from a recall target
    instead of a hardcoded count (mirrors the semantic-blocking move in #1090).

    ``target_hamming_frac`` is the near-duplicate radius the blocker must cover,
    i.e. ``1 - scorer_threshold`` (a 0.85 image-pHash threshold => a 0.15 radius).
    Returns the cheapest (fewest-bands, highest-reduction) blocking that still
    recalls the radius; falls back to the finest division if none qualifies.
    Measured on the bench suite: this picks ``num_bands=16`` for the image default,
    lifting blocking recall 0.72 -> 0.97 vs the old hardcoded 8 (ADR 0022)."""
    choices = _divisor_band_counts(hash_bits)
    for b in choices:
        if lsh_collision_probability(target_hamming_frac, b, hash_bits) >= target_recall:
            return b
    return choices[-1] if choices else 1


def _parse_hash(value: str | None) -> int | None:
    """Parse a hex perceptual hash (``0x`` prefix tolerated) to an int, or None."""
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    try:
        return int(s, 16)
    except ValueError:
        return None


@dataclass
class PerceptualLSHBlocker:
    """Resolved banded-hamming-LSH parameters + the bucketing/blocking operations."""

    num_bands: int
    hash_bits: int

    @classmethod
    def from_config(cls, cfg: PerceptualKeyConfig) -> PerceptualLSHBlocker:
        return cls(cfg.num_bands, cfg.hash_bits)

    @property
    def band_width(self) -> int:
        return self.hash_bits // self.num_bands

    def _bands(self, h: int) -> list[int]:
        w = self.band_width
        mask = (1 << w) - 1
        return [(h >> (b * w)) & mask for b in range(self.num_bands)]

    def buckets(self, hashes: list[int | None]) -> dict[tuple[int, int], list[int]]:
        """Map ``(band_idx, band_value)`` -> row positions, skipping null hashes."""
        groups: dict[tuple[int, int], list[int]] = {}
        for row_idx, h in enumerate(hashes):
            if h is None:
                continue
            for band_idx, value in enumerate(self._bands(h)):
                groups.setdefault((band_idx, value), []).append(row_idx)
        return groups

    def candidate_pairs(self, hashes: list[int | None]) -> set[tuple[int, int]]:
        """De-duplicated ``(min, max)`` candidate pairs across all bands."""
        pairs: set[tuple[int, int]] = set()
        for members in self.buckets(hashes).values():
            if len(members) < 2:
                continue
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a, b = members[i], members[j]
                    pairs.add((a, b) if a < b else (b, a))
        return pairs

    def blocks(self, df: pl.DataFrame, hashes: list[int | None]) -> list:
        """One ``BlockResult`` per non-singleton ``(band, band_value)`` group."""
        from goldenmatch.core.blocker import BlockResult

        results: list[BlockResult] = []
        for (band_idx, value), members in self.buckets(hashes).items():
            if len(members) < 2:
                continue
            block_df = df[members]  # positional row select (preserves __row_id__)
            results.append(
                BlockResult(
                    block_key=f"phash_b{band_idx}_{value:x}",
                    df=block_df.lazy(),
                    strategy="perceptual_lsh",
                )
            )
        return results


def build_perceptual_blocks(lf: pl.LazyFrame, config: BlockingConfig) -> list:
    """Build perceptual-hash LSH blocks for ``strategy="perceptual"``."""
    if config.perceptual is None:
        raise ValueError("Perceptual blocking requires a 'perceptual' config block.")
    df = lf.collect()
    column = config.perceptual.column
    if column not in df.columns:
        raise ValueError(f"Perceptual blocking column {column!r} not found in data.")

    blocker = PerceptualLSHBlocker.from_config(config.perceptual)
    raw = df[column].cast(pl.Utf8).to_list()
    hashes = [_parse_hash(v) for v in raw]
    return blocker.blocks(df, hashes)
