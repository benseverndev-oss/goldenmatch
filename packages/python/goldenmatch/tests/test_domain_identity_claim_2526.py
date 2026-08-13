"""#2526: a domain-extracted `exact` field is an identity claim only if it is
actually an identifier.

A STANDALONE exact matchkey asserts "same value implies same entity" on its own,
with nothing else consulted. `_DOMAIN_SCORER_MAP` already records how much each
derived column is worth -- 1.0 for identifiers (`__model_norm__`,
`__sw_part_num__`), lower for partial signals (`__color__` 0.2, `__title_key__`
0.8) -- and that weight used to be read and then discarded when the standalone
matchkey was built, so every exact entry made a full-strength claim regardless.

Measured on DBLP-ACM: `__title_key__` is the first significant WORD of the title,
so the emitted `domain_exact_title_key` asserted 33,563 pairs against 2,224 in
ground truth (precision 0.068, clusters up to 96). It cleared the old
`< 0.01` cardinality floor 29x over, because that floor only rejects
NEAR-CONSTANT columns.
"""
from __future__ import annotations

import pytest
from goldenmatch.core.autoconfig import (
    _DOMAIN_IDENTITY_MIN_CARDINALITY,
    _DOMAIN_IDENTITY_WEIGHT,
    _DOMAIN_SCORER_MAP,
    _is_identity_claim,
)


class TestIsIdentityClaim:
    def test_identifier_weight_and_high_cardinality_stands_alone(self):
        assert _is_identity_claim("__model_norm__", 1.0, 0.95) is True

    @pytest.mark.parametrize("weight", [0.2, 0.3, 0.5, 0.8, 0.99])
    def test_sub_identifier_weight_never_stands_alone(self, weight):
        # Even at cardinality 1.0: the weight is the author's statement that this
        # is a partial signal, and a partial signal is not an identity claim.
        assert _is_identity_claim("__whatever__", weight, 1.0) is False

    def test_identifier_weight_with_collapsed_cardinality_does_not_stand_alone(self):
        # Belt-and-braces: a 1.0-weighted extractor that collapses on data it was
        # not tuned for is not an identifier ON THIS DATA.
        assert _is_identity_claim("__model_norm__", 1.0, 0.01) is False

    def test_the_dblp_acm_case_is_rejected(self):
        # __title_key__ measured on DBLP-ACM: weight 0.8, 1427 distinct over 4910.
        weight = _DOMAIN_SCORER_MAP["__title_key__"][1]
        assert _is_identity_claim("__title_key__", weight, 1427 / 4910) is False

    def test_old_cardinality_floor_would_have_accepted_it(self):
        # The regression guard: 0.29 clears a 0.01 floor by 29x, which is why the
        # old check passed it through. Locks in WHY the criterion changed, so a
        # future "simplify back to a cardinality floor" has to confront it.
        assert (1427 / 4910) > 0.01
        assert (1427 / 4910) < _DOMAIN_IDENTITY_MIN_CARDINALITY


class TestDomainScorerMapContract:
    def test_every_exact_entry_declares_a_weight(self):
        for col, (scorer, weight, _transforms) in _DOMAIN_SCORER_MAP.items():
            if scorer == "exact":
                assert isinstance(weight, (int, float)), col
                assert 0.0 < weight <= _DOMAIN_IDENTITY_WEIGHT, col

    def test_the_known_identifiers_still_qualify(self):
        # These are the columns whose standalone-exact behaviour must NOT change:
        # genuine identifiers in the electronics / software domain packs.
        for col in ("__model__", "__model_norm__", "__sw_part_num__"):
            scorer, weight, _ = _DOMAIN_SCORER_MAP[col]
            assert scorer == "exact", col
            assert _is_identity_claim(col, weight, 0.9) is True, col

    def test_the_known_partial_signals_do_not(self):
        # Each of these would otherwise assert e.g. "same colour implies same
        # product" as a standalone matchkey.
        for col in ("__color__", "__sw_edition__", "__sw_platform__",
                    "__sw_version__", "__title_key__"):
            scorer, weight, _ = _DOMAIN_SCORER_MAP[col]
            assert scorer == "exact", col
            assert _is_identity_claim(col, weight, 0.9) is False, col
