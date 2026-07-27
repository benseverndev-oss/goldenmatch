"""Tier 7: budget tests for negative-evidence scoring.

Tests that the batch NE path (_apply_negative_evidence_batch, the vectorized
core the pipeline runs via _apply_negative_evidence_to_exact_pairs) completes
within its wall-clock budget on 50K-row inputs.
"""


def test_negative_evidence_scoring_overhead_under_budget():
    """NE scoring on 50K candidate pairs completes within 2s.

    Exercises the batch path the pipeline actually runs
    (_apply_negative_evidence_to_exact_pairs -> _apply_negative_evidence_batch),
    which fans the hot string scorers out through the native pairwise kernel in
    one FFI crossing rather than a per-pair pure-Python strsim loop.
    """
    import time

    from goldenmatch.config.schemas import (
        MatchkeyConfig,
        MatchkeyField,
        NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import _apply_negative_evidence_batch

    mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="x", transforms=[],
                              scorer="ensemble", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.5, penalty=0.3),
            NegativeEvidenceField(field="address", transforms=[],
                                  scorer="token_sort", threshold=0.4, penalty=0.4),
        ],
    )
    pairs = [
        {"x": ("a", "a"), "phone": ("555-1234", "5559999"),
         "address": ("123 Main", "456 Oak")}
        for _ in range(50_000)
    ]
    start = time.time()
    _apply_negative_evidence_batch(mk, pairs)
    elapsed = time.time() - start
    assert elapsed < 2.0, f"NE scoring took {elapsed:.2f}s on 50K pairs (budget 2s)"


def test_exact_matchkey_ne_scoring_overhead_under_budget():
    """NE scoring on 50K candidate pairs via exact matchkey completes within 2s."""
    import time

    from goldenmatch.config.schemas import (
        MatchkeyConfig,
        MatchkeyField,
        NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import _apply_negative_evidence_batch

    mk = MatchkeyConfig(
        name="exact_email",
        type="exact",
        threshold=0.5,
        fields=[
            MatchkeyField(field="email", transforms=["lowercase"],
                          scorer="exact", weight=1.0)
        ],
        negative_evidence=[
            NegativeEvidenceField(
                field="phone",
                transforms=["digits_only"],
                scorer="exact",
                threshold=0.4,
                penalty=0.3,
            ),
            NegativeEvidenceField(
                field="address",
                transforms=[],
                scorer="token_sort",
                threshold=0.4,
                penalty=0.4,
            ),
        ],
    )
    pairs = [
        {
            "email": ("a@x.com", "a@x.com"),
            "phone": ("555-1234", "555-9999"),
            "address": ("123 Main", "456 Oak"),
        }
        for _ in range(50_000)
    ]
    start = time.time()
    _apply_negative_evidence_batch(mk, pairs)
    elapsed = time.time() - start
    assert elapsed < 2.0, (
        f"exact-matchkey NE scoring took {elapsed:.2f}s on 50K pairs (budget 2s)"
    )
