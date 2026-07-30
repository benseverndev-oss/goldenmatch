"""Tests for goldenmatch.semantic.certify_key_integrity (the semantic-layer wedge)."""
from __future__ import annotations

import pyarrow as pa
import pytest
from goldenmatch.core.key_integrity_certificate import KeyIntegrityCertificate
from goldenmatch.semantic import certify_key_integrity


def test_clean_key_is_trustworthy():
    t = pa.table({"customer_id": [1, 2, 3], "revenue": [10, 5, 7]})
    c = certify_key_integrity(t, key="customer_id", measures=["revenue"])
    assert c.is_unique_at_grain is True
    assert c.duplicate_key_groups == 0
    assert c.max_fan_out == 1.0
    assert c.measure_fan_out == {"revenue": 1.0}
    assert c.estimate == 1.0
    assert c.is_trustworthy() is True


def test_duplicated_key_quantifies_fan_out():
    # customer 1 appears twice with identical revenue; customer 2 is clean.
    t = pa.table({"customer_id": [1, 1, 2], "revenue": [10, 10, 5], "name": ["Bob", "Bob", "Amy"]})
    c = certify_key_integrity(t, key="customer_id", measures=["revenue"])
    assert c.is_unique_at_grain is False
    assert c.n_key_groups == 2
    assert c.duplicate_key_groups == 1
    assert c.max_fan_out == 2.0
    # naive SUM=25, de-duplicated SUM=15 -> 1.667x inflation
    assert c.measure_fan_out["revenue"] == pytest.approx(25.0 / 15.0)
    assert c.estimate == pytest.approx(0.5)
    assert c.is_trustworthy() is False


def test_composite_key():
    t = pa.table({"org": ["a", "a", "b"], "user": [1, 1, 1]})  # (a,1) duplicated
    c = certify_key_integrity(t, key=["org", "user"])
    assert c.key_columns == ["org", "user"]
    assert c.duplicate_key_groups == 1
    assert c.max_fan_out == 2.0


def test_non_numeric_measure_skipped_with_note():
    t = pa.table({"customer_id": [1, 2], "label": ["x", "y"]})
    c = certify_key_integrity(t, key="customer_id", measures=["label"])
    assert c.measure_fan_out == {}
    assert "non-numeric measures skipped" in c.note


def test_missing_columns_raise():
    t = pa.table({"customer_id": [1, 2]})
    with pytest.raises(ValueError):
        certify_key_integrity(t, key="nope")
    with pytest.raises(ValueError):
        certify_key_integrity(t, key="customer_id", measures=["nope"])
    with pytest.raises(ValueError):
        certify_key_integrity(t, key=[])


def test_accepts_dict_input():
    c = certify_key_integrity({"id": [1, 2, 2]}, key="id")
    assert c.duplicate_key_groups == 1


def test_resolve_detects_fragmentation():
    # customer_id 100 and 101 are byte-identical people (a dirty/non-conformed
    # key): entity resolution collapses them, so the key over-fragments the entity.
    names = ["Robert Smith", "Robert Smith", "Jane Doe", "Alice Ray", "Tom Lee",
             "Nina Fox", "Ed Poe", "Uma Roy", "Cy Vane", "Al Ives", "Bo Katz", "Di Nash"]
    cities = ["Boston", "Boston", "Denver", "Miami", "Reno", "Akron",
              "Provo", "Ames", "Waco", "Bend", "Ojai", "Enid"]
    cids = [100, 101, 200, 201, 202, 203, 204, 205, 206, 207, 208, 209]
    t = pa.table({"customer_id": cids, "name": names, "city": cities,
                  "revenue": list(range(12))})
    c = certify_key_integrity(t, key="customer_id", measures=["revenue"], resolve=True)
    # the declared key is structurally unique...
    assert c.is_unique_at_grain is True
    # ...but ER shows one real entity split across two declared keys.
    assert c.resolved_entities == 1
    assert c.fragmented_entities == 1
    assert c.undercount_estimate == pytest.approx(1.0)
    # the safe bound discounts the fragmentation the structural pass can't see.
    assert c.safe_bound == pytest.approx(0.0)


def test_resolve_no_attributes_notes_skip():
    t = pa.table({"customer_id": [1, 2, 3], "revenue": [1, 2, 3]})
    c = certify_key_integrity(t, key="customer_id", measures=["revenue"], resolve=True)
    assert c.resolved_entities is None
    assert "resolution skipped" in c.note


def test_certificate_estimate_contract():
    # the {estimate, safe_bound} normalization contract goldenanalysis consumes
    cert = KeyIntegrityCertificate(
        key_columns=["k"], grain=None, n_rows=4, n_key_groups=4,
        is_unique_at_grain=True, duplicate_key_groups=0, max_fan_out=1.0,
    )
    assert cert.estimate == 1.0
    assert cert.safe_bound == 1.0  # no resolution -> collapses to structural estimate
    cert.undercount_estimate = 0.25
    assert cert.safe_bound == pytest.approx(0.75)
