"""Normalization is the make-or-break for the relational signal -- pin it."""
from normalize import decode_set, encode_set, jaccard, name_key, norm_name


def test_norm_name_is_order_insensitive():
    # Chinese/Western name order varies across papers; the token signature must
    # collide so a co-author isn't split into two people.
    assert norm_name("Haifeng Qian") == norm_name("Qian Haifeng")
    assert norm_name("Ning  Zeng") == norm_name("ning zeng")


def test_norm_name_strips_accents_and_punct():
    assert norm_name("José Peña") == norm_name("Jose Pena")
    assert name_key("Y.-K. Chen") == norm_name("chen k y")


def test_encode_set_is_sorted_deduped_and_drops_empty():
    assert encode_set(["Bob", "alice", "Bob", "", None]) == "alice|bob"
    assert encode_set([]) == ""


def test_decode_is_inverse_of_encode():
    assert decode_set(encode_set(["Carol", "dave"])) == {"carol", "dave"}
    assert decode_set("") == set()
    assert decode_set(None) == set()


def test_jaccard_semantics():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a", "b", "c"}, {"a"}) == 1 / 3
    # two empty sets share NO positive evidence -> 0, not 1
    assert jaccard(set(), set()) == 0.0
    assert jaccard({"a"}, set()) == 0.0
