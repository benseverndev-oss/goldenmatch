from goldenmatch.core.scorer import score_field


def test_score_field_ensemble_does_not_raise_and_returns_max():
    import jellyfish
    from rapidfuzz.distance import JaroWinkler
    from rapidfuzz.fuzz import token_sort_ratio

    a, b = "Jonathan Smith", "Smith Jonathan"  # token_sort should win here
    got = score_field(a, b, "ensemble")
    jw = JaroWinkler.similarity(a, b)
    ts = token_sort_ratio(a, b) / 100.0
    sx = (1.0 if jellyfish.soundex(a) == jellyfish.soundex(b) else 0.0) * 0.8
    assert got == max(jw, ts, sx)
    assert 0.0 <= got <= 1.0


def test_score_field_ensemble_none_inputs():
    assert score_field(None, "x", "ensemble") is None
    assert score_field("x", None, "ensemble") is None


def test_score_field_ensemble_identical_is_one():
    assert score_field("acme corp", "acme corp", "ensemble") == 1.0
