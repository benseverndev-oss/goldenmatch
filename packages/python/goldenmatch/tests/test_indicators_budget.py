"""Tier 7: budget tests for v1.11 indicators and negative-evidence scoring.

Tests that compute_identity_collision_signal and _apply_negative_evidence
complete within their wall-clock budgets on 50K-row inputs.
"""


def test_compute_identity_collision_signal_50k_under_budget():
    """8s budget on 50K rows."""
    import time
    import polars as pl
    from goldenmatch.core.indicators import compute_identity_collision_signal
    n = 50_000
    df = pl.DataFrame({
        "email": [f"u{i // 5}@x.com" for i in range(n)],   # 10K unique × 5
        "address": [f"{i % 100} Main St" for i in range(n)],
    })
    start = time.time()
    signal = compute_identity_collision_signal(df, "email", ["address"])
    elapsed = time.time() - start
    # Spec budget is 8s; allow CI margin (post-fold shared runners)
    # without masking O(N^2) blowups
    assert elapsed < 10.0, f"collision_signal took {elapsed:.2f}s (target 8s)"


def test_negative_evidence_scoring_overhead_under_budget():
    """NE scoring on 50K candidate pairs completes within 2s."""
    import time
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import _apply_negative_evidence

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
    for pair in pairs:
        _apply_negative_evidence(mk, pair)
    elapsed = time.time() - start
    assert elapsed < 2.0, f"NE scoring took {elapsed:.2f}s on 50K pairs (budget 2s)"
