import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import random

from corruption import PROFILES, char_typo, corrupt_record, nickname, token_drop


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
