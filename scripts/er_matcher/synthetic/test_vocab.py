import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import random

from vocab import Vocab


def test_vocab_deterministic_per_seed():
    v = Vocab()
    a = [v.sample_surname(random.Random(1)) for _ in range(20)]
    b = [v.sample_surname(random.Random(1)) for _ in range(20)]
    assert a == b  # same seed -> identical draw


def test_surname_draw_is_frequency_weighted():
    # SMITH (rank 1, count ~2.4M) must dominate a rare top-10k surname over many draws
    v = Vocab()
    rng = random.Random(0)
    draws = [v.sample_surname(rng) for _ in range(5000)]
    assert draws.count("Smith") > 0  # frequent name appears
    # the single most common draw should be among the highest-frequency surnames
    top = max(set(draws), key=draws.count)
    assert v.surname_rank(top.upper()) <= 50


def test_vocab_size_past_ceiling():
    v = Vocab()
    assert v.n_surnames > 1000 and v.n_first_names > 50  # >> old 900-combo ceiling


def test_sample_full_name_and_address_shape():
    v = Vocab()
    rng = random.Random(3)
    first, last = v.sample_person_name(rng)
    assert first and last
    addr = v.sample_address(rng)
    assert set(addr) >= {"street", "city", "state", "zip"}
