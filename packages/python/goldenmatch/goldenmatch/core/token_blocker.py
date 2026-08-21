"""DF-pruned token blocking (#2488).

Inverted-index candidate generation for free text: tokenize a column, index
each record under every token it carries, and emit one block per token. A
record belongs to many blocks, so two records are candidates when they share
ANY surviving token.

This is the scheme the block analyzer previously could not express. Every
candidate it generates is an EXACT key -- a prefix, a soundex code, or a
compound of two -- which puts each record in exactly one block and therefore
misses any true pair that disagrees on that one derived value. On free-text
product titles that is most of them.

Why not ``lsh``: MinHash/LSH estimates JACCARD similarity over the whole token
set, so it needs the two strings to overlap substantially. Cross-vendor titles
routinely share two or three discriminative tokens out of fifteen -- strong
evidence, low Jaccard. Measured on Amazon-Google (4589 records, 1300 truth
pairs): LSH word-shingles peak at 55.9% pair recall, DF-pruned token blocking
reaches 98.4%. The two are complements, not substitutes.

Cost is controlled by document-frequency pruning rather than by a block-size
cap. A token in D records forms a block of D and contributes D(D-1)/2 pairs, so
the expensive tokens are the frequent ones -- and frequency is exactly what
makes a token non-discriminative. Dropping them removes almost all the cost and
almost none of the recall.

Conforms to the ``BlockResult`` blocker contract; ``blocker.build_blocks``
dispatches here for ``strategy="token"``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from goldenmatch._polars_lazy import pl
from goldenmatch.config.schemas import BlockingConfig, TokenBlockingConfig
from goldenmatch.core.sketch import _word_tokens

logger = logging.getLogger(__name__)

#: Bounds on the DF cap derived from ``max_df_ratio``. The ratio alone does not
#: bound cost: 2% of a 10M-row frame is a 200k-record block (2e10 pairs). The
#: floor keeps small frames from pruning everything -- at n=200 a 2% ratio caps
#: DF at 4, which would discard nearly every usable token.
_MIN_DERIVED_DF_CAP = 10
_MAX_DERIVED_DF_CAP = 1000


def resolve_max_df(cfg: TokenBlockingConfig, n_rows: int) -> int:
    """The effective document-frequency cap for a frame of ``n_rows``.

    An explicit ``max_df`` wins verbatim. Otherwise it is ``max_df_ratio * n``
    clamped to ``[_MIN_DERIVED_DF_CAP, _MAX_DERIVED_DF_CAP]`` so the cap stays
    bounded at any frame size in both directions.
    """
    if cfg.max_df is not None:
        return cfg.max_df
    derived = int(cfg.max_df_ratio * n_rows)
    return max(_MIN_DERIVED_DF_CAP, min(_MAX_DERIVED_DF_CAP, derived))


@dataclass
class TokenBlocker:
    """Resolved token-blocking parameters + the indexing operations."""

    min_token_length: int
    max_df: int

    @classmethod
    def from_config(cls, cfg: TokenBlockingConfig, n_rows: int) -> TokenBlocker:
        return cls(cfg.min_token_length, resolve_max_df(cfg, n_rows))

    def tokens(self, text: str) -> set[str]:
        """Distinct length-filtered lowercase tokens of one record.

        A set, not a list: a token repeated within one title must not put the
        record into its block twice. Tokenization is ``sketch._word_tokens``,
        the same splitter the MinHash word-shingle path uses, so the two
        text-blocking strategies agree on what a word is.
        """
        return {
            t for t in _word_tokens(text.lower())
            if len(t) >= self.min_token_length
        }


    def index(self, texts: list[str]) -> dict[str, list[int]]:
        """Map surviving token -> row positions, DF-pruned and singleton-free.

        Two passes: count document frequency, then keep only tokens whose DF is
        in ``[2, max_df]``. DF 1 blocks nothing and DF above the cap is a
        non-discriminative mega-block.
        """
        per_row = [self.tokens(t) for t in texts]
        df_count: dict[str, int] = {}
        for toks in per_row:
            for t in toks:
                df_count[t] = df_count.get(t, 0) + 1

        index: dict[str, list[int]] = {}
        for row_idx, toks in enumerate(per_row):
            for t in toks:
                if 2 <= df_count[t] <= self.max_df:
                    index.setdefault(t, []).append(row_idx)
        return index

    def blocks(self, df: pl.DataFrame, texts: list[str]) -> list:
        """One ``BlockResult`` per surviving token."""
        from goldenmatch.core.blocker import BlockResult

        idx = self.index(texts)
        results: list[BlockResult] = []
        for token, members in idx.items():
            if len(members) < 2:
                continue
            results.append(
                BlockResult(
                    block_key=f"token_{token}",
                    df=df[members].lazy(),  # positional select preserves __row_id__
                    strategy="token",
                )
            )
        return results


def build_token_blocks(lf: pl.LazyFrame, config: BlockingConfig) -> list:
    """Build token blocks for ``strategy="token"`` (called by ``build_blocks``)."""
    if config.token is None:
        raise ValueError("Token blocking requires a 'token' config block.")
    df = lf.collect()
    if config.token.column not in df.columns:
        raise ValueError(
            f"Token blocking column {config.token.column!r} not found in data."
        )
    texts = df[config.token.column].cast(pl.Utf8).fill_null("").to_list()
    blocker = TokenBlocker.from_config(config.token, df.height)
    blocks = blocker.blocks(df, texts)
    logger.info(
        "Token blocking on %r: %d block(s) from %d rows (min_token_length=%d, max_df=%d)",
        config.token.column, len(blocks), df.height,
        blocker.min_token_length, blocker.max_df,
    )
    return blocks
