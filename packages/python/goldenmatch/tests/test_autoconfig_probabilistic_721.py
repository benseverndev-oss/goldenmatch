"""#721: admit high-cardinality identifiers to probabilistic (F-S) matchkeys.

Mirrors test_autoconfig_pincer_715.py but for ``build_probabilistic_matchkeys``.
The probabilistic path previously blanket-skipped ``col_type="identifier"``,
diverging from the exact path (#715). The admission rule (decided 2026-06-05):

  * identifiers ARE admitted as exact-scorer comparison fields (no lower
    cardinality floor -- F-S self-regulates a weak identifier via a higher u /
    smaller EM weight, so we don't hard-gate low cardinality);
  * the ONLY hard gate is ``cardinality_ratio >= 1.0`` (a perfect surrogate key
    carries no shared-identity signal), applied uniformly to EVERY exact-scorer
    field -- which newly covers email/phone too (previously ungated).

m/u estimation + blocking-field exclusion are EM's job at train time, not this
builder's -- these tests only assert field membership + scorer.
"""
from goldenmatch.core.autoconfig import ColumnProfile, build_probabilistic_matchkeys


def _prob_fields(matchkeys):
    return {f.field for mk in matchkeys for f in mk.fields}


def _name():
    # An always-admitted comparison field so the matchkey is non-empty and we
    # assert on the presence/absence of the field under test specifically.
    return ColumnProfile("full_name", "Utf8", "name", 0.9,
                         null_rate=0.0, cardinality_ratio=0.8)


def test_high_card_identifier_admitted():
    """An npi-shaped identifier (card 0.62) is admitted as a probabilistic
    comparison field (no longer skipped outright)."""
    profiles = [
        ColumnProfile("npi", "Utf8", "identifier", 0.9,
                      null_rate=0.38, cardinality_ratio=0.62),
        _name(),
    ]
    assert "npi" in _prob_fields(build_probabilistic_matchkeys(profiles))


def test_low_card_identifier_admitted_no_floor():
    """A low-card identifier (0.3) is STILL admitted -- unlike the exact path,
    F-S has no mega-cluster risk, so there is no lower floor."""
    profiles = [
        ColumnProfile("member_tier", "Utf8", "identifier", 0.9,
                      null_rate=0.0, cardinality_ratio=0.3),
        _name(),
    ]
    assert "member_tier" in _prob_fields(build_probabilistic_matchkeys(profiles))


def test_surrogate_key_identifier_excluded():
    """card == 1.0 identifier is a per-record surrogate key -> excluded."""
    profiles = [
        ColumnProfile("row_pk", "Utf8", "identifier", 0.9,
                      null_rate=0.0, cardinality_ratio=1.0),
        _name(),
    ]
    fields = _prob_fields(build_probabilistic_matchkeys(profiles))
    assert "row_pk" not in fields
    assert "full_name" in fields  # the rest of the matchkey is intact


def test_surrogate_key_email_excluded_uniform_gate():
    """The card>=1.0 gate is uniform across exact-scorer fields: a perfectly
    unique email is now excluded too (previously ungated in the prob path)."""
    profiles = [
        ColumnProfile("email", "Utf8", "email", 0.9,
                      null_rate=0.0, cardinality_ratio=1.0),
        _name(),
    ]
    assert "email" not in _prob_fields(build_probabilistic_matchkeys(profiles))


def test_high_card_email_still_admitted():
    """Regression guard: a high-card email (0.7) stays admitted."""
    profiles = [
        ColumnProfile("email", "Utf8", "email", 0.9,
                      null_rate=0.0, cardinality_ratio=0.7),
        _name(),
    ]
    assert "email" in _prob_fields(build_probabilistic_matchkeys(profiles))
