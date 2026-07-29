import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import random

from corruption import (
    PROFILES,
    case_ws,
    char_typo,
    corrupt_record,
    format_variant,
    nickname,
    token_drop,
)


def test_char_typo_mutates_about_one_char():
    out = char_typo("williams", random.Random(1), rate=1.0)
    assert out != "williams" and abs(len(out) - len("williams")) <= 1


def test_char_typo_rate_zero_is_identity():
    assert char_typo("smith", random.Random(1), rate=0.0) == "smith"


def test_nickname_maps_known_name():
    # a known formal->nickname mapping fires at rate 1.0
    assert nickname("Robert", random.Random(1), rate=1.0) in {"Rob", "Bob", "Bobby"}


def test_token_drop_removes_a_token():
    out = token_drop("123 Main Street", random.Random(1), rate=1.0)
    assert len(out.split()) < 3 and out  # dropped one, still non-empty


def test_corrupt_record_deterministic_and_changes_something():
    rec = {"first": "Robert", "last": "Williams", "email": "r.w@acme.com", "phone": "5551234567"}
    strong_id = "email"
    c1 = corrupt_record(rec, strong_id=strong_id, rng=random.Random(2), profile="heavy")
    c2 = corrupt_record(rec, strong_id=strong_id, rng=random.Random(2), profile="heavy")
    assert c1 == c2  # deterministic
    assert c1 != rec  # something corrupted
    assert c1[strong_id] == rec[strong_id]  # strong id preserved (it identifies the entity)


def test_profiles_exist():
    assert set(PROFILES) == {"light", "heavy"} and PROFILES["heavy"] > PROFILES["light"]


def test_format_variant_phone_round_trip():
    out = format_variant("5551234567", random.Random(1), rate=1.0, kind="phone")
    assert out == "(555) 123-4567"


def test_format_variant_email_variant():
    # uppercase local+domain with a dot in the local part so BOTH the "lower"
    # and "dot" variants are guaranteed to visibly change the value.
    out = format_variant("R.W@ACME.COM", random.Random(3), rate=1.0, kind="email")
    assert out != "R.W@ACME.COM" and "@" in out


def test_format_variant_address_mid_string_token():
    # regression: a compound comma-joined address (e.g. organization.address)
    # must get its street-suffix token corrupted too, not just a bare street.
    value = "476 River Dr, Burlington, VT 05483"
    out = format_variant(value, random.Random(4), rate=1.0, kind="address")
    assert out != value


def test_case_ws_rate_one_changes_rate_zero_identity():
    assert case_ws("Robert Williams", random.Random(5), rate=0.0) == "Robert Williams"
    out = case_ws("Robert Williams", random.Random(5), rate=1.0)
    assert out != "Robert Williams"
