"""Initialism / abbreviation scorer.

Matches cases where one field name is an abbreviation formed by taking
a non-empty prefix of each token from the other, in order. Examples:

    assay_id          <-> ASSI    (ASS + I)
    confidence_score  <-> CONSC   (CON + SC)
    relationship_type <-> RELATIT (RELATI + T)
    variant_id        <-> VARI    (VAR + I)
    curated_by        <-> CURAB   (CURA + B)

Fires in both directions (source-is-abbrev or target-is-abbrev). Abstains
(returns None) when neither side is a valid prefix-concat of the other —
otherwise this would emit noise on unrelated pairs.

Uses ``canonical_name`` when MapEngine has populated it, so schema-wide
common affixes are already stripped and we only reason about the
semantic tokens (e.g. "assays_" is gone before we see "assay_id").
"""
from __future__ import annotations

import re

from infermap.types import FieldInfo, ScorerResult


_TOKEN_RE = re.compile(r"[A-Za-z][a-z]*|[A-Z]+(?=[A-Z][a-z]|\b)|\d+")


def _tokenize(name: str) -> list[str]:
    """Split a field name into lowercase tokens.

    Handles snake_case, kebab-case, camelCase, and PascalCase. Numbers are
    their own tokens. Returns lowercase strings with no delimiters.
    """
    # Replace common delimiters with spaces, then split camelCase boundaries.
    cleaned = re.sub(r"[_\-. ]+", " ", name.strip())
    tokens: list[str] = []
    for chunk in cleaned.split():
        # Split camelCase: insert space before an uppercase run
        parts = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", chunk)
        tokens.extend(p.lower() for p in parts if p)
    return tokens


def _is_prefix_concat(target: str, source_tokens: list[str]) -> bool:
    """Is *target* a concatenation of non-empty prefixes of *source_tokens*
    (in order), using every source token exactly once?

    Each source token must contribute at least 1 character. DP search.
    """
    target = target.lower()
    n_src = len(source_tokens)
    n_tgt = len(target)
    if n_src == 0 or n_tgt == 0:
        return False

    # dp[i][j] = can we consume target[0:j] using source_tokens[0:i]?
    dp = [[False] * (n_tgt + 1) for _ in range(n_src + 1)]
    dp[0][0] = True
    for i in range(1, n_src + 1):
        tok = source_tokens[i - 1]
        for j in range(1, n_tgt + 1):
            # Try each prefix length k of this token (>=1)
            for k in range(1, min(len(tok), j) + 1):
                if target[j - k : j] == tok[:k] and dp[i - 1][j - k]:
                    dp[i][j] = True
                    break
    return dp[n_src][n_tgt]


def _score_pair(name_a: str, name_b: str) -> float | None:
    """Return a score in (0, 1] if one side abbreviates the other, else None.

    Higher scores mean more of the longer side's content is preserved in
    the abbreviation (i.e. the abbreviation is not too lossy).
    """
    tok_a = _tokenize(name_a)
    tok_b = _tokenize(name_b)

    # Normalize the candidate "single-token" side: join into one string
    # without delimiters, lowercased.
    joined_a = "".join(tok_a)
    joined_b = "".join(tok_b)
    if not joined_a or not joined_b:
        return None
    # Degenerate case: identical after tokenization (handled elsewhere).
    if joined_a == joined_b:
        return None

    # Try: b is an abbreviation of a's tokens.
    if _is_prefix_concat(joined_b, tok_a):
        long, short = joined_a, joined_b
    elif _is_prefix_concat(joined_a, tok_b):
        long, short = joined_b, joined_a
    else:
        return None

    # Compression ratio. 1.0 = same length (no compression), 0.0 = empty.
    # Map to (0.6, 0.95] so a 50%-compressed abbreviation scores ~0.78.
    ratio = len(short) / len(long)
    return 0.6 + 0.35 * ratio


class InitialismScorer:
    """Scores source/target pairs where one side is a prefix-concat
    abbreviation of the other's tokens.

    Abstains (returns None) on pairs that are not abbreviations, to avoid
    polluting the weighted average with zeros.
    """

    name: str = "InitialismScorer"
    weight: float = 0.75

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        src_name = source.canonical_name or source.name
        tgt_name = target.canonical_name or target.name
        score = _score_pair(src_name, tgt_name)
        if score is None:
            return None
        return ScorerResult(
            score=score,
            reasoning=(
                f"Initialism/abbreviation match: '{src_name}' <-> '{tgt_name}' "
                f"(score={score:.3f})"
            ),
        )
