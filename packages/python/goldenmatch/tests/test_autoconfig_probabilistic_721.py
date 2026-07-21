"""#721: admit high-cardinality identifiers to probabilistic (F-S) matchkeys.

Mirrors test_autoconfig_pincer_715.py but for ``build_probabilistic_matchkeys``.
The probabilistic path previously blanket-skipped ``col_type="identifier"``,
diverging from the exact path (#715). The admission rule (decided 2026-06-05):

  * identifiers ARE admitted as exact-scorer comparison fields (no lower
    cardinality floor -- F-S self-regulates a weak identifier via a higher u /
    smaller EM weight, so we don't hard-gate low cardinality);
  * the ``cardinality_ratio >= 1.0`` hard gate (a perfect surrogate key carries
    no shared-identity signal) excludes the ambiguous bare ``identifier`` type
    (row PKs), but NOT identity-bearing VALUE types (``email``/``phone``): a
    shared email/phone a duplicate carries verbatim is F-S's single strongest
    signal, and the ratio is measured on a sample that can under-represent
    duplicates -- excluding it collapsed the EM model to zero matches at scale
    (F1=0 at 1M). An F-S exact field is a COMPARISON field (not a pair-generating
    exact matchkey), so a true PK self-regulates to neutral (m~=u) while a shared
    identifier carries a large weight -- admitting email/phone is never harmful.

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


def test_card1_email_admitted_not_treated_as_surrogate():
    """A card == 1.0 email is a shared identity-bearing VALUE, NOT a per-record
    surrogate: it is admitted (unlike the bare `identifier` PK above). The ratio
    is sample-measured and can read 1.0 when duplicates are under-represented, so
    excluding it collapsed zero-config FS to F1=0 at scale. F-S self-regulates a
    truly-never-shared value to neutral, so admitting is safe."""
    profiles = [
        ColumnProfile("email", "Utf8", "email", 0.9,
                      null_rate=0.0, cardinality_ratio=1.0),
        _name(),
    ]
    assert "email" in _prob_fields(build_probabilistic_matchkeys(profiles))


def test_card1_phone_admitted_not_treated_as_surrogate():
    """Same carve-out for phone -- an identity-bearing value type."""
    profiles = [
        ColumnProfile("phone", "Utf8", "phone", 0.9,
                      null_rate=0.0, cardinality_ratio=1.0),
        _name(),
    ]
    assert "phone" in _prob_fields(build_probabilistic_matchkeys(profiles))


def test_high_card_email_still_admitted():
    """Regression guard: a high-card email (0.7) stays admitted."""
    profiles = [
        ColumnProfile("email", "Utf8", "email", 0.9,
                      null_rate=0.0, cardinality_ratio=0.7),
        _name(),
    ]
    assert "email" in _prob_fields(build_probabilistic_matchkeys(profiles))
