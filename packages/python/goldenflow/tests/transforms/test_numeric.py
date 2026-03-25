import polars as pl

from goldenflow.transforms.numeric import clamp, currency_strip, percentage_normalize, round_values


def test_currency_strip():
    s = pl.Series("p", ["$1,234.56", "$99.99", "$0.50", "free"])
    result = currency_strip(s)
    assert result[0] == 1234.56
    assert result[1] == 99.99
    assert result[2] == 0.50
    assert result[3] is None


def test_percentage_normalize():
    s = pl.Series("p", ["85%", "100%", "0.5%", "50"])
    result = percentage_normalize(s)
    assert result[0] == 0.85
    assert result[1] == 1.0
    assert result[2] == 0.005


def test_round_values():
    s = pl.Series("v", [1.23456, 2.789, 3.1])
    result = round_values(s, n=2)
    assert result.to_list() == [1.23, 2.79, 3.1]


def test_clamp():
    s = pl.Series("v", [-5.0, 0.0, 50.0, 150.0])
    result = clamp(s, min_val=0.0, max_val=100.0)
    assert result.to_list() == [0.0, 0.0, 50.0, 100.0]
