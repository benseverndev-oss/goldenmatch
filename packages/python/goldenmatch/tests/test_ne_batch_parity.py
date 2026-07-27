"""Parity gate for the vectorized negative-evidence penalty core.

`_apply_negative_evidence_batch(matchkey, pairs)` scores NE fields across all
pairs in one shot -- via the native `score_field_pairwise` kernel when
available (scorer ids 0..3), else a per-pair scalar fallback. It MUST return
byte-identical penalties to the scalar `_apply_negative_evidence` applied to
each pair, which is the source of truth (see test_fast_path_ne_penalty.py).

Inputs are chosen so every NE similarity lands unambiguously above or below its
threshold -- so any native-vs-strsim ULP drift can't flip a discrete penalty and
make the parity assertion flaky. The native-vs-strsim byte-identity itself is
gated separately (test_native_field_matrix_parity.py).
"""
from __future__ import annotations

from goldenmatch.config.schemas import (
    MatchkeyConfig,
    MatchkeyField,
    NegativeEvidenceField,
)
from goldenmatch.core.scorer import (
    _apply_negative_evidence,
    _apply_negative_evidence_batch,
)


def _scalar(mk: MatchkeyConfig, pairs: list[dict]) -> list[float]:
    return [_apply_negative_evidence(mk, p) for p in pairs]


def _weighted_mk(ne: list[NegativeEvidenceField]) -> MatchkeyConfig:
    return MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="x", transforms=[], scorer="ensemble", weight=1.0)],
        negative_evidence=ne,
    )


def test_batch_matches_scalar_exact_and_token_sort():
    mk = _weighted_mk([
        NegativeEvidenceField(field="phone", transforms=["digits_only"],
                              scorer="exact", threshold=0.5, penalty=0.3),
        NegativeEvidenceField(field="address", transforms=[],
                              scorer="token_sort", threshold=0.4, penalty=0.4),
    ])
    pairs = [
        # phone disagree (exact 0<0.5 -> +0.3), address disagree (token_sort low -> +0.4)
        {"phone": ("555-1234", "5559999"), "address": ("123 Main St", "999 Oak Ave")},
        # phone agree (exact 1>=0.5 -> 0), address agree (identical -> 1.0 -> 0)
        {"phone": ("555-1234", "555-1234"), "address": ("123 Main St", "123 Main St")},
        # phone disagree only
        {"phone": ("555-0001", "555-0002"), "address": ("50 Elm", "50 Elm")},
    ]
    assert _apply_negative_evidence_batch(mk, pairs) == _scalar(mk, pairs)


def test_batch_empty_ne_returns_zeros():
    mk = _weighted_mk([])
    pairs = [{"phone": ("a", "b")}, {"phone": ("c", "c")}]
    assert _apply_negative_evidence_batch(mk, pairs) == [0.0, 0.0]


def test_batch_handles_nulls_and_missing_field():
    mk = _weighted_mk([
        NegativeEvidenceField(field="phone", transforms=["digits_only"],
                              scorer="exact", threshold=0.5, penalty=0.3),
        NegativeEvidenceField(field="address", transforms=[],
                              scorer="token_sort", threshold=0.4, penalty=0.4),
    ])
    pairs = [
        {"phone": (None, "5559999"), "address": ("123 Main", "999 Oak")},  # phone null -> skip
        {"phone": ("555-1234", "5559999")},                                # address missing -> skip
        {"phone": ("555-1", "555-1"), "address": (None, None)},            # address null -> skip
    ]
    assert _apply_negative_evidence_batch(mk, pairs) == _scalar(mk, pairs)


def test_batch_non_pairwise_scorer_falls_back_to_scalar():
    # soundex_match is native id 4 (matrix-only) -> not pairwise-eligible ->
    # the batch core must fall back to the scalar scorer for that field.
    mk = _weighted_mk([
        NegativeEvidenceField(field="name", transforms=[],
                              scorer="soundex_match", threshold=0.9, penalty=0.5),
    ])
    pairs = [
        {"name": ("Robert", "Rupert")},
        {"name": ("Smith", "Smith")},
        {"name": ("Xylophone", "Aardvark")},
    ]
    assert _apply_negative_evidence_batch(mk, pairs) == _scalar(mk, pairs)


def test_batch_empty_pairs():
    mk = _weighted_mk([
        NegativeEvidenceField(field="phone", transforms=[],
                              scorer="exact", threshold=0.5, penalty=0.3),
    ])
    assert _apply_negative_evidence_batch(mk, []) == []
