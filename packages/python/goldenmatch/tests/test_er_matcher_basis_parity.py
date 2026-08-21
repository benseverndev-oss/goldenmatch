"""Parity gate: the interp harness must measure the basis the product ships.

Every faithfulness number in ``docs/design/2026-08-03-15b-interp-handoff.md`` is a
claim about the SHIPPED explainer. That claim is only true while the harness
(``scripts/er_matcher/interp/field_attribution.py``) and the product
(``goldenmatch.core.er_matcher.explainer``) compute the same per-field basis.

The harness keeps standalone fallbacks so it can be unit-tested without the
goldenmatch package importable. This file is what stops those fallbacks from
silently drifting: it asserts the two implementations agree, and that the shipped
functions are what the harness actually receives in production.

If this fails, either re-sync the fallback in ``field_attribution.py`` or accept
that the published faithfulness numbers no longer describe what ships.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from goldenmatch.core.er_matcher.explainer import (
    _CONFLICT_THRESHOLD,
    FIELD_SIGNAL_NAMES,
    field_agreement,
    field_signal_vector,
)

_INTERP = Path(__file__).resolve().parents[4] / "scripts" / "er_matcher"
if str(_INTERP) not in sys.path:
    sys.path.insert(0, str(_INTERP))

fa = pytest.importorskip(
    "interp.field_attribution", reason="interp harness not on this checkout"
)

# Deliberately spans the cases that have bitten this thread: exact, fuzzy person
# values, reordered/verbose product titles, alphanumeric-boundary tokens, missing
# on one or both sides, and disjoint strings.
PAIRS = [
    ("John", "John"),
    ("John", "Jon"),
    ("Smith", "Smyth"),
    ("1990-01-01", "1990-01-02"),
    ("Leeds", "Bristol"),
    ("Sony 60GB PS3", "PlayStation 3 60 GB Sony"),
    ("60GB", "60 GB"),
    ("Canon EOS 5D", "Whirlpool dishwasher"),
    ("Sony", "Sony Ericsson W810i phone"),
    ("", "x"),
    ("x", ""),
    ("", ""),
    (None, "x"),
    ("baker", "bakers"),
]


class TestBasisParity:
    def test_signal_names_match(self):
        assert tuple(fa._LOCAL_SIGNAL_NAMES) == tuple(FIELD_SIGNAL_NAMES)

    def test_conflict_threshold_matches(self):
        assert fa._LOCAL_CONFLICT_THRESHOLD == _CONFLICT_THRESHOLD

    @pytest.mark.parametrize(("va", "vb"), PAIRS)
    def test_missing_and_exact_agree(self, va, vb):
        # The structural signals must be identical. `agreement`/`conflict` may
        # legitimately differ (the shipped metric is token-aware, the standalone
        # fallback is plain jaro-winkler) -- that difference is exactly why the
        # harness is required to inject the shipped function in production, which
        # test_harness_uses_shipped_basis below pins.
        local = fa._local_signal_vector(va, vb)
        shipped = field_signal_vector(va, vb)
        assert local["missing"] == shipped["missing"]
        assert local["exact"] == shipped["exact"]
        assert local["len_ratio"] == pytest.approx(shipped["len_ratio"])
        assert local["edit_norm"] == pytest.approx(shipped["edit_norm"])

    @pytest.mark.parametrize(("va", "vb"), PAIRS)
    def test_shipped_agreement_never_below_fallback(self, va, vb):
        # token-awareness can only ADD agreement; if this ever inverts, the
        # "strict improvement, not a trade" claim in the handoff is broken.
        local = fa._local_signal_vector(va, vb)
        shipped = field_signal_vector(va, vb)
        assert shipped["agreement"] >= local["agreement"] - 1e-12

    def test_shipped_vector_is_self_consistent_with_field_agreement(self):
        for va, vb in PAIRS:
            vec = field_signal_vector(va, vb)
            agr = field_agreement(va, vb)
            if agr is None:
                assert vec["missing"] == 1.0
                assert vec["agreement"] == 0.0
            else:
                assert vec["agreement"] == pytest.approx(agr)
                assert vec["conflict"] == (1.0 if agr <= _CONFLICT_THRESHOLD else 0.0)


class TestHarnessUsesShippedBasis:
    """The production harness must inject the shipped functions, not the fallback."""

    def test_richer_features_honour_injected_basis(self):
        rows = {0: {"t": "Sony 60GB PS3"}, 1: {"t": "PlayStation 3 60 GB Sony"}}
        pairs = [(0, 1, 1)]
        fallback, names_f = fa.richer_field_features(rows, pairs, ["t"])
        shipped, names_s = fa.richer_field_features(
            rows, pairs, ["t"],
            signal_fn=field_signal_vector, signal_names=FIELD_SIGNAL_NAMES,
        )
        assert names_f == names_s
        # the injected (shipped, token-aware) basis scores this reordered title
        # materially higher than the fallback -- proving injection took effect
        agr = names_s.index("t__agreement")
        assert shipped[0, agr] > fallback[0, agr] + 0.05

    def test_field_agreements_honour_injected_metric(self):
        rows = {0: {"t": "Sony 60GB PS3"}, 1: {"t": "PlayStation 3 60 GB Sony"}}
        pairs = [(0, 1, 1)]
        fallback = fa.field_agreements(rows, pairs, ["t"])
        shipped = fa.field_agreements(rows, pairs, ["t"], agreement=field_agreement)
        assert shipped[0, 0] > fallback[0, 0] + 0.05

    def test_modal_harness_passes_the_shipped_functions(self):
        # Guards the actual call sites: if someone drops the injection, every
        # published faithfulness number silently starts describing a basis the
        # product does not ship.
        src = (_INTERP / "interp" / "modal_interp.py").read_text(encoding="utf-8")
        assert "agreement=field_agreement" in src
        assert "signal_fn=field_signal_vector" in src
