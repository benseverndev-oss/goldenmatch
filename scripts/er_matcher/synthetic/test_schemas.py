import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import random

from schemas import DOMAINS, build_record


def test_domains_declared():
    assert set(DOMAINS) == {"crm_contact", "organization", "business"}
    for d in DOMAINS.values():
        assert d.strong_id in d.fields  # strong id is one of the fields
        assert d.name_field in d.fields  # name field (for negative blocking) is a field
        assert d.name_field != d.strong_id  # name shared by hard negs, strong id differs


def test_build_record_yields_all_fields():
    from vocab import Vocab

    v = Vocab()
    for name, dom in DOMAINS.items():
        rec = build_record(name, v, random.Random(7))
        assert set(rec) == set(dom.fields)
        assert all(rec[f] for f in dom.fields)  # non-empty


def test_build_record_deterministic():
    from vocab import Vocab

    v = Vocab()
    r1 = build_record("crm_contact", v, random.Random(9))
    r2 = build_record("crm_contact", v, random.Random(9))
    assert r1 == r2


def test_business_and_organization_names_are_surname_derived():
    # Regression test: company/legal names must come from vocab.sample_surname,
    # NOT vocab.sample_person_name's first-name slot (bug: `surname, _ =
    # vocab.sample_person_name(rng)` silently bound the FIRST name). Replay the
    # same seed against the builder's own draw sequence and check the leading
    # token of the generated name matches the surname draw at that position.
    from vocab import Vocab

    v = Vocab()

    rec = build_record("business", v, random.Random(42))
    expected_surname = v.sample_surname(random.Random(42))  # business draws surname first
    assert rec["name"].split()[0] == expected_surname

    rec = build_record("organization", v, random.Random(42))
    expected_surname = v.sample_surname(random.Random(42))  # organization draws surname first
    assert rec["legal_name"].split()[0] == expected_surname
    assert rec["dba"] == expected_surname

    rec = build_record("crm_contact", v, random.Random(42))
    replay = random.Random(42)
    v.sample_person_name(replay)  # crm_contact draws the contact's own name first
    expected_company_surname = v.sample_surname(replay)  # then the company's surname
    assert rec["company"].split()[0] == expected_company_surname


def test_id_and_digit_field_lengths():
    from vocab import Vocab

    v = Vocab()
    crm = build_record("crm_contact", v, random.Random(11))
    assert len(crm["phone"]) == 10 and crm["phone"].isdigit()

    biz = build_record("business", v, random.Random(11))
    assert len(biz["phone"]) == 10 and biz["phone"].isdigit()

    org = build_record("organization", v, random.Random(11))
    assert len(org["ein"]) == 9 and org["ein"].isdigit()


def test_crm_contact_email_derived_from_own_name():
    from vocab import Vocab

    v = Vocab()
    rec = build_record("crm_contact", v, random.Random(13))
    assert rec["email"].startswith(f"{rec['first'].lower()}.{rec['last'].lower()}@")
