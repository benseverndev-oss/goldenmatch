from goldenmatch.core.survivorship.validate import goldenflow_filter


def test_unknown_validator_is_failopen():
    vals = ["a", "b", None]
    kept = goldenflow_filter(vals, "does_not_exist")
    assert kept == vals


def test_validator_drops_invalid(monkeypatch):
    import goldenmatch.core.survivorship.validate as V
    monkeypatch.setattr(
        V, "_resolve_validator",
        lambda name: (lambda values: [v is not None and len(str(v)) == 10 for v in values]),
    )
    vals = ["1234567890", "abc", None, "999"]
    kept = goldenflow_filter(vals, "nanp")
    assert kept == ["1234567890", None, None, None]


def test_real_nanp_validator_if_available():
    import pytest
    pytest.importorskip("goldenflow")
    kept = goldenflow_filter(["212-555-0100", "not-a-phone", None], "nanp")
    assert kept[0] == "212-555-0100" and kept[1] is None and kept[2] is None
