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
