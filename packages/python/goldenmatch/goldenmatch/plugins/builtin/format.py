"""Format-canonical predefined plugins.

shortest_value, concat_unique, email_normalize, phone_digits_only.
Each satisfies ``GoldenStrategyPlugin`` from ``goldenmatch.plugins.base``.

Spec: ``docs/superpowers/specs/2026-05-22-predefined-merge-plugins-design.md``
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

# Email plus-addressing pattern: local+anything@domain -> local@domain.
_EMAIL_PLUS_RE = re.compile(r"^([^+@]+)\+[^@]*(@.+)$")
# Phone digits-only pattern.
_NON_DIGIT_RE = re.compile(r"\D+")
# URL scheme + host extraction (lowercase scheme + host, preserve path).
_URL_RE = re.compile(r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<host>[^/?#]+)(?P<rest>.*)$")
# Internal whitespace pattern -- one-or-more whitespace chars.
_WHITESPACE_RE = re.compile(r"\s+")
# Canonical boolean truthy / falsy tokens, lowercased.
_BOOL_TRUTHY = frozenset({"true", "t", "yes", "y", "1", "on"})
_BOOL_FALSY = frozenset({"false", "f", "no", "n", "0", "off"})


class ShortestValueStrategy:
    """Pick the shortest non-null string. Inverse of `longest_value`.

    Useful for codes / identifiers where shorter usually means more
    canonical (e.g. country code 'US' over 'United States of America').
    Quality-weighted tie-break when weights are provided.

    Confidence: 1.0 unique shortest, 0.7 tied (by quality), 0.5
    tied (first-index wins).
    """

    name = "shortest_value"

    def merge(
        self,
        values: list,
        *,
        quality_weights: list[float] | None = None,
        **_: Any,
    ) -> Any:
        non_null = [(i, v) for i, v in enumerate(values) if v is not None]
        if not non_null:
            return (None, 0.0)
        str_vals = [(i, str(v), v) for i, v in non_null]
        min_len = min(len(s) for _, s, _ in str_vals)
        shortest = [(i, v) for i, s, v in str_vals if len(s) == min_len]
        if len(shortest) == 1:
            return (shortest[0][1], 1.0, shortest[0][0])
        if quality_weights is not None:
            best = max(
                shortest,
                key=lambda x: quality_weights[x[0]] if x[0] < len(quality_weights) else 1.0,
            )
            return (best[1], 0.7, best[0])
        return (shortest[0][1], 0.5, shortest[0][0])


class ConcatUniqueStrategy:
    """Join unique non-null values into a sorted, comma-separated string.

    For tags / categories / multi-select fields where the "golden"
    value is the UNION of source values, not one of them.

    Separator override via ``rule_kwargs.separator`` (default ", ").

    NOTE: The output is a synthesized string. Returned idx is 0
    (no real provenance) since no single source row holds the
    concatenated form.

    Confidence: 1.0 when at least one non-null exists.
    """

    name = "concat_unique"

    def merge(
        self,
        values: list,
        *,
        rule_kwargs: dict | None = None,
        **_: Any,
    ) -> Any:
        non_null = [str(v) for v in values if v is not None and str(v) != ""]
        if not non_null:
            return (None, 0.0)
        unique_sorted = sorted(set(non_null))
        sep = (rule_kwargs or {}).get("separator", ", ")
        return (sep.join(unique_sorted), 1.0, 0)


def _canonicalize_email(value: str) -> str:
    """Lowercase + strip plus-addressing. Trims whitespace."""
    v = value.strip().lower()
    match = _EMAIL_PLUS_RE.match(v)
    if match:
        return match.group(1) + match.group(2)
    return v


class EmailNormalizeStrategy:
    """Normalize emails and pick the mode (most common canonical form).

    Normalization: lowercase + strip plus-addressing
    (`bob+work@x.com` -> `bob@x.com`). After normalization, pick the
    most-frequent canonical form. Returns the CANONICAL value, not
    the original (`Bob+Work@X.COM` -> `bob@x.com`).

    Confidence: `count / total`. Single non-null gets 1.0.
    """

    name = "email_normalize"

    def merge(self, values: list, **_: Any) -> Any:
        non_null = [(i, str(v)) for i, v in enumerate(values) if v is not None]
        if not non_null:
            return (None, 0.0)
        normalized = [(i, _canonicalize_email(v)) for i, v in non_null]
        # Mode pick.
        counts = Counter(n for _, n in normalized)
        winner, count = counts.most_common(1)[0]
        # Return idx of first occurrence of the winner.
        first_idx = next(i for i, n in normalized if n == winner)
        conf = count / len(non_null)
        return (winner, conf, first_idx)


class PhoneDigitsOnlyStrategy:
    """Strip phone formatting and pick the value with the most digits.

    Favors international (E.164 / 11+ digits) over local (10 digits)
    over abbreviated. Output is the DIGITS-ONLY form (no '+',
    parentheses, dashes, or spaces).

    Confidence: 1.0 when a unique max-digit form exists; 0.7 on
    ties (first-index wins).
    """

    name = "phone_digits_only"

    def merge(self, values: list, **_: Any) -> Any:
        non_null = [(i, str(v)) for i, v in enumerate(values) if v is not None]
        if not non_null:
            return (None, 0.0)
        stripped = [(i, _NON_DIGIT_RE.sub("", v)) for i, v in non_null]
        # Drop entries that have no digits.
        stripped = [(i, d) for i, d in stripped if d]
        if not stripped:
            return (None, 0.0)
        max_len = max(len(d) for _, d in stripped)
        tied = [(i, d) for i, d in stripped if len(d) == max_len]
        conf = 1.0 if len(tied) == 1 else 0.7
        return (tied[0][1], conf, tied[0][0])


def _canonicalize_url(value: str) -> str:
    """Lowercase scheme + host; trim trailing slash on the path portion.

    Preserves case-sensitive path/query/fragment beyond the host. URL
    canonicalization full-spec is complex; this is the operator-
    common subset (scheme normalization + host case + trailing slash).
    """
    v = value.strip()
    match = _URL_RE.match(v)
    if not match:
        return v
    scheme = match.group("scheme").lower()
    # http -> https upgrade as a canonical normalization for matching
    # the same logical resource. Comment out this line if you want
    # strict scheme preservation.
    if scheme == "http://":
        scheme = "https://"
    host = match.group("host").lower()
    rest = match.group("rest")
    # Trim ONE trailing slash from path (preserve query/fragment).
    if rest == "/":
        rest = ""
    elif rest.endswith("/") and "?" not in rest and "#" not in rest:
        rest = rest[:-1]
    return scheme + host + rest


class UrlCanonicalStrategy:
    """Canonicalize URLs and pick the mode (most common canonical form).

    Normalization (operator-common subset):
    - lowercase scheme + host
    - upgrade http -> https
    - trim trailing slash from path

    Picks the most-frequent canonical form. Returns the CANONICAL value.

    Confidence: `count / total` for the mode pick.
    """

    name = "url_canonical"

    def merge(self, values: list, **_: Any) -> Any:
        non_null = [(i, str(v)) for i, v in enumerate(values) if v is not None]
        if not non_null:
            return (None, 0.0)
        normalized = [(i, _canonicalize_url(v)) for i, v in non_null]
        counts = Counter(n for _, n in normalized)
        winner, count = counts.most_common(1)[0]
        first_idx = next(i for i, n in normalized if n == winner)
        conf = count / len(non_null)
        return (winner, conf, first_idx)


class WhitespaceNormalizeStrategy:
    """Collapse internal whitespace runs to a single space + trim.

    "  Acme   Corp \\t\\nInc  " -> "Acme Corp Inc". Then pick the mode
    of the normalized values (most-common cleaned form).

    Returns the CANONICAL (whitespace-normalized) value, not the
    original. Useful when source systems vary in trailing/internal
    whitespace -- the dedupe is by content, not formatting.

    Confidence: `count / total` for the mode pick.
    """

    name = "whitespace_normalize"

    def merge(self, values: list, **_: Any) -> Any:
        non_null = [(i, str(v)) for i, v in enumerate(values) if v is not None]
        if not non_null:
            return (None, 0.0)
        normalized = [
            (i, _WHITESPACE_RE.sub(" ", v).strip())
            for i, v in non_null
        ]
        # Drop entries that normalize to empty string.
        normalized = [(i, v) for i, v in normalized if v]
        if not normalized:
            return (None, 0.0)
        counts = Counter(n for _, n in normalized)
        winner, count = counts.most_common(1)[0]
        first_idx = next(i for i, n in normalized if n == winner)
        conf = count / len(non_null)
        return (winner, conf, first_idx)


class BooleanNormalizeStrategy:
    """Coerce yes/no/Y/N/1/0/true/false/on/off variants to canonical bool.

    Truthy tokens (case-insensitive, trimmed): true, t, yes, y, 1, on
    Falsy tokens: false, f, no, n, 0, off

    Unknown tokens are ignored. If non-null values map to multiple
    booleans, picks the majority. Ties prefer True (canonical
    "value is present" semantics over "value is absent").

    Returns Python `True` / `False` (not the original string).
    Confidence: `count(winning_bool) / count(parseable_values)`.
    """

    name = "boolean_normalize"

    def merge(self, values: list, **_: Any) -> Any:
        parsed: list[tuple[int, bool]] = []
        for i, v in enumerate(values):
            if v is None:
                continue
            if isinstance(v, bool):
                parsed.append((i, v))
                continue
            token = str(v).strip().lower()
            if token in _BOOL_TRUTHY:
                parsed.append((i, True))
            elif token in _BOOL_FALSY:
                parsed.append((i, False))
        if not parsed:
            return (None, 0.0)
        truthy = sum(1 for _, b in parsed if b)
        falsy = len(parsed) - truthy
        # Tie-break to True.
        winner = True if truthy >= falsy else False
        win_count = truthy if winner else falsy
        # Idx of first occurrence of the winning bool.
        first_idx = next(i for i, b in parsed if b == winner)
        conf = win_count / len(parsed)
        return (winner, conf, first_idx)
