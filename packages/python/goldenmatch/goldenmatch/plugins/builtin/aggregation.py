"""Aggregation / telemetry predefined plugins.

These return SYNTHESIZED scalar metrics about the cluster (not picks
from the cluster's members). Useful for golden-record fields that
should carry merge metadata -- e.g. `n_sources_agreeing` for a
quality column, or `confidence_score` derived from agreement_rate.

count_distinct, count_non_null, agreement_rate.

Each satisfies ``GoldenStrategyPlugin`` from
``goldenmatch.plugins.base``.

Spec: ``docs/superpowers/specs/2026-05-22-predefined-merge-plugins-design.md``
"""
from __future__ import annotations

from collections import Counter
from typing import Any


class CountDistinctStrategy:
    """Number of distinct non-null values in the cluster.

    Useful for audit columns like `n_distinct_addresses`,
    `n_distinct_emails` that surface how much variance the source
    systems had for this field. Higher = more disagreement.

    Returned value is a Python int. Synthesized -> idx=0.
    Confidence: 1.0 when at least one non-null value exists;
    0.0 when all-null (and value is None).
    """

    name = "count_distinct"

    def merge(self, values: list, **_: Any) -> Any:
        non_null = [v for v in values if v is not None]
        if not non_null:
            return (None, 0.0)
        return (len(set(non_null)), 1.0, 0)


class CountNonNullStrategy:
    """Number of non-null values in the cluster.

    Used to surface coverage -- "how many of the merged sources
    contributed a value to this field?". Synthesized scalar.

    Returned value is a Python int. idx=0.
    Confidence: 1.0 always (the count is exact). When ALL values are
    null, returns (0, 1.0) NOT (None, 0.0) -- the count itself is
    well-defined data even when there were no contributions.
    """

    name = "count_non_null"

    def merge(self, values: list, **_: Any) -> Any:
        count = sum(1 for v in values if v is not None)
        return (count, 1.0, 0)


class AgreementRateStrategy:
    """Fraction of non-null values that agree with the modal value.

    Returns a float in [0.0, 1.0]. 1.0 = full agreement; lower =
    more disagreement. Mode-based (not pairwise).

    Calculation::

       non_null = [v for v in values if v is not None]
       mode_count = max(Counter(non_null).values())
       rate = mode_count / len(non_null)

    Useful for audit columns like `field_agreement_score` -- low
    values surface fields that need steward review.

    Returned value is a Python float. Synthesized -> idx=0.
    Confidence: `len(non_null) / len(values)` -- coverage of the
    measurement (a 1.0 agreement rate on a single non-null sample
    is less robust than 1.0 on 10 samples).
    """

    name = "agreement_rate"

    def merge(self, values: list, **_: Any) -> Any:
        non_null = [v for v in values if v is not None]
        if not non_null:
            return (None, 0.0)
        counts = Counter(non_null)
        _winner, mode_count = counts.most_common(1)[0]
        rate = mode_count / len(non_null)
        conf = len(non_null) / len(values)
        return (rate, conf, 0)
