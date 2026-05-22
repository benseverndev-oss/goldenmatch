"""Identity-safe PK preflight profiler (closes goldenmatch issue #207).

Warns when the input table has NO viable stable unique identifier
column. Without one, downstream consumers like goldenmatch's Identity
Graph fall back to a payload-hash record_id (`{source}:hash:{12 hex}`),
which silently collides on physically-different rows that happen to
have identical column values -- breaking entity_id stability across
runs.

This is a dataset-level relation check: we want to assert that AT
LEAST ONE column qualifies as a stable PK candidate. If none do, emit
a single WARNING-severity finding with a suggestion that the caller
either:

1. Pass an explicit `source_pk_column` to the downstream resolver
2. Or add a stable surrogate key column (UUID, autoincrement)

Heuristic for a "PK candidate":
- All values non-null (no NULLs in the column)
- Fully unique (n_unique == n_rows)
- Not blocked by name heuristics that mean "this is value data, not ID"
  (e.g. `email`, `phone`, `name`, `address` -- those CAN be unique in
  small samples but are semantically value columns, not stable PKs)
- Reasonable type for an identifier (int / string -- not float / bool)
"""
from __future__ import annotations

import polars as pl

from goldencheck.models.finding import Finding, Severity

# Column-name prefixes/substrings that imply "this is data the user
# could plausibly edit later", not a stable PK. Even when fully unique
# in a small sample these are unsafe long-term PK candidates.
_VALUE_COLUMN_PATTERNS: tuple[str, ...] = (
    "email",
    "phone",
    "fax",
    "address",
    "street",
    "city",
    "name",
    "first_name",
    "last_name",
    "company",
    "title",
    "description",
    "notes",
    "comment",
    "url",
    "website",
    "ssn",  # PII; even when unique, downstream may strip it
)

# Column-name patterns that *strongly* suggest a PK / surrogate-key.
_PK_NAME_PATTERNS: tuple[str, ...] = (
    "id",
    "uuid",
    "guid",
    "key",
    "pk",
    "primary_key",
    "row_id",
    "record_id",
    "ext_id",
    "external_id",
)


def _looks_like_value_column(name: str) -> bool:
    lower = name.lower()
    return any(p in lower for p in _VALUE_COLUMN_PATTERNS)


def _looks_like_pk_column(name: str) -> bool:
    lower = name.lower()
    # Exact match or `<thing>_id` style; substring `id` would FP on
    # `paid`, `said`, etc., so we anchor on word-boundary-ish suffixes.
    for p in _PK_NAME_PATTERNS:
        if lower == p or lower.endswith(f"_{p}") or lower.startswith(f"{p}_"):
            return True
    return False


def _column_qualifies_as_pk(
    df: pl.DataFrame,
    column: str,
) -> tuple[bool, str]:
    """Return (qualifies, why_or_disqualifier).

    A column qualifies as a stable PK candidate when:
    1. No NULL values
    2. Fully unique (n_unique == n_rows)
    3. Not a value-shaped column (per _VALUE_COLUMN_PATTERNS)
    4. Reasonable dtype for an identifier (Int*, String/Utf8; NOT Float
       since float equality is unsafe, NOT Bool since bool is binary)
    """
    if _looks_like_value_column(column):
        return False, "value-shaped name (email/name/address/etc.)"
    col = df[column]
    dtype = col.dtype
    if dtype.is_float() or dtype == pl.Boolean:
        return False, f"unsuitable dtype ({dtype})"
    n_rows = len(col)
    if n_rows == 0:
        return False, "empty sample"
    n_nulls = col.null_count()
    if n_nulls > 0:
        return False, f"{n_nulls} null value(s)"
    if col.n_unique() != n_rows:
        return False, "non-unique values"
    return True, "stable unique non-null"


class IdentitySafePkProfiler:
    """Dataset-level preflight: at least one viable stable PK column.

    Emits at most one Finding (a single dataset-level warning when no
    column qualifies). When at least one column qualifies, returns
    `[]` -- the dataset is safe to feed to goldenmatch's Identity
    Graph without explicit `source_pk_column`.
    """

    def profile(self, df: pl.DataFrame) -> list[Finding]:
        if df.width == 0:
            return []

        candidates: list[str] = []
        disqualifiers: dict[str, str] = {}
        named_pk_disqualifiers: dict[str, str] = {}

        for column in df.columns:
            qualifies, reason = _column_qualifies_as_pk(df, column)
            if qualifies:
                candidates.append(column)
            else:
                disqualifiers[column] = reason
                if _looks_like_pk_column(column):
                    named_pk_disqualifiers[column] = reason

        if candidates:
            # At least one viable PK; no preflight warning.
            return []

        # No qualifying PK column. Decide how loud to be.
        # If a column LOOKS like a PK (e.g. `customer_id`) but failed
        # the uniqueness/null test, that's a stronger signal: the
        # caller intended this to be a PK and we should call it out
        # specifically rather than emit a generic "no PK" warning.
        if named_pk_disqualifiers:
            target = next(iter(named_pk_disqualifiers))
            why = named_pk_disqualifiers[target]
            return [Finding(
                severity=Severity.WARNING,
                column=target,
                check="identity_safe_pk",
                message=(
                    f"Column '{target}' looks like a PK by name but "
                    f"isn't stable ({why}). Identity-graph downstreams "
                    f"will fall back to payload-hash record_ids, which "
                    f"silently collide on duplicate raw rows."
                ),
                affected_rows=len(df),
                sample_values=[],
                suggestion=(
                    f"Either fix the column ('{target}' should be "
                    f"non-null + unique), OR pass an explicit "
                    f"source_pk_column to the Identity Graph resolver, "
                    f"OR add a stable surrogate key (UUID / "
                    f"autoincrement) to the dataset."
                ),
                confidence=0.9,
            )]

        # No named-PK column either. Generic warning -- treat as
        # dataset-level (no specific column anchor).
        sample_cols = ", ".join(df.columns[:5])
        if df.width > 5:
            sample_cols += ", ..."
        return [Finding(
            severity=Severity.WARNING,
            column="__dataset__",
            check="identity_safe_pk",
            message=(
                "No viable stable PK column detected. Columns "
                f"({sample_cols}) have nulls, duplicates, or look "
                "like editable value columns (email/name/address)."
            ),
            affected_rows=len(df),
            sample_values=[],
            suggestion=(
                "If feeding this dataset to goldenmatch's Identity "
                "Graph, pass an explicit source_pk_column on "
                "IdentityConfig OR add a stable surrogate key "
                "(UUID / autoincrement). Without one, record_ids fall "
                "back to a payload-hash that collides on duplicate "
                "raw rows."
            ),
            confidence=0.8,
        )]
