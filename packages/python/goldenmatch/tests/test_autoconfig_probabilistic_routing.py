"""Probabilistic-routing lever: trigger predicate + gated auto-config routing.

The trigger routes a dataset to the Fellegi-Sunter path when it has NO surviving
exact matchkey backed by a strong-identity column (identifier / email / phone) AND
>= 2 fuzzy fields. The strong-identity set is broader than just `identifier`
because the dual-strategy harness showed a clean-email anchor (anchor_person_match)
does BETTER deterministically (its email exact matchkey is a strong identity claim)
-- routing it would regress it.
"""
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.autoconfig import ColumnProfile, _is_probabilistic_shape


def _prof(name, col_type, card=0.5):
    return ColumnProfile(name=name, dtype="Utf8", col_type=col_type,
                         confidence=0.9, null_rate=0.0, cardinality_ratio=card, avg_len=10)


def _exact(field):
    return MatchkeyConfig(name=f"exact_{field}", type="exact", fields=[MatchkeyField(field=field)])


def _weighted(*fields):
    return MatchkeyConfig(name="fuzzy", type="weighted", threshold=0.8,
                          fields=[MatchkeyField(field=f, scorer="jaro_winkler", weight=1.0)
                                  for f in fields])


def test_probabilistic_shape_no_strong_id_two_fuzzy():
    # historical_50k shape: exact on dob (date) + name composites, no strong id.
    profiles = [_prof("first_name", "name"), _prof("surname", "name"), _prof("dob", "date")]
    mks = [_exact("dob"), _weighted("first_name", "surname")]
    assert _is_probabilistic_shape(mks, profiles) is True


def test_identifier_exact_blocks_routing():
    profiles = [_prof("ssn", "identifier", card=0.99), _prof("first_name", "name"), _prof("surname", "name")]
    mks = [_exact("ssn"), _weighted("first_name", "surname")]
    assert _is_probabilistic_shape(mks, profiles) is False


def test_email_exact_blocks_routing():
    # anchor_person_match shape: clean email exact matchkey -> deterministic wins.
    profiles = [_prof("email", "email", card=0.9), _prof("first_name", "name"), _prof("surname", "name")]
    mks = [_exact("email"), _weighted("first_name", "surname")]
    assert _is_probabilistic_shape(mks, profiles) is False


def test_phone_exact_blocks_routing():
    profiles = [_prof("phone", "phone", card=0.9), _prof("first_name", "name"), _prof("surname", "name")]
    mks = [_exact("phone"), _weighted("first_name", "surname")]
    assert _is_probabilistic_shape(mks, profiles) is False


def test_too_few_fuzzy_fields_no_route():
    profiles = [_prof("first_name", "name")]
    mks = [_weighted("first_name")]
    assert _is_probabilistic_shape(mks, profiles) is False
