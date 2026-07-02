from dataclasses import dataclass

import polars as pl
from goldenmatch.core import autoconfig_discriminative as disc


@dataclass
class _P:
    name: str
    col_type: str


def test_identity_basket_includes_identity_types_excludes_locality():
    profiles = [
        _P("zip", "zip"), _P("first_name", "name"), _P("last_name", "name"),
        _P("email", "email"), _P("npi", "identifier"), _P("city", "geo"),
        _P("notes", "description"),
    ]
    basket = disc.identity_basket("zip", profiles)
    assert set(basket) == {"first_name", "last_name", "email", "npi"}
    assert "zip" not in basket and "city" not in basket and "notes" not in basket


def test_identity_basket_excludes_the_candidate_even_if_identity_typed():
    profiles = [_P("npi", "identifier"), _P("email", "email")]
    assert disc.identity_basket("npi", profiles) == ["email"]


def _zip_like_df(n=300):
    zips = [f"{10000 + (i % 30):05d}" for i in range(n)]
    names = [f"person{i}" for i in range(n)]   # unique -> shared-zip pairs DISAGREE
    return pl.DataFrame({"zip": zips, "name": names})


def _npi_like_df(n=300):
    npis = [f"{1000000000 + (i % 30)}" for i in range(n)]
    names = [f"provider{i % 30}" for i in range(n)]  # shared-npi pairs AGREE
    return pl.DataFrame({"npi": npis, "name": names})


def test_discriminative_power_low_for_shared_locality():
    power, support = disc.discriminative_power(_zip_like_df(), "zip", ["name"])
    assert support >= disc._MIN_SHARED_PAIRS
    assert power < 0.5


def test_discriminative_power_high_for_shared_identity():
    power, support = disc.discriminative_power(_npi_like_df(), "npi", ["name"])
    assert support >= disc._MIN_SHARED_PAIRS
    assert power > 0.9


def test_discriminative_power_zero_support_when_all_unique():
    df = pl.DataFrame({"id": [str(i) for i in range(100)], "name": [f"n{i}" for i in range(100)]})
    power, support = disc.discriminative_power(df, "id", ["name"])
    assert support == 0


def test_discriminative_power_empty_basket_zero():
    assert disc.discriminative_power(_zip_like_df(), "zip", []) == (0.0, 0)


def test_should_veto_zip_true():
    profiles = [_P("zip", "zip"), _P("name", "name")]
    assert disc.should_veto_exact(_zip_like_df(), "zip", profiles) is True


def test_should_veto_npi_false():
    profiles = [_P("npi", "identifier"), _P("name", "name")]
    assert disc.should_veto_exact(_npi_like_df(), "npi", profiles) is False


def test_should_veto_thin_support_false():
    df = pl.DataFrame({"id": [str(i) for i in range(100)], "name": [f"n{i}" for i in range(100)]})
    profiles = [_P("id", "identifier"), _P("name", "name")]
    assert disc.should_veto_exact(df, "id", profiles) is False


def test_should_veto_df_none_false():
    profiles = [_P("zip", "zip"), _P("name", "name")]
    assert disc.should_veto_exact(None, "zip", profiles) is False


def test_should_veto_empty_basket_false():
    profiles = [_P("zip", "zip")]
    assert disc.should_veto_exact(_zip_like_df(), "zip", profiles) is False


def test_should_veto_kill_switch_false(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_DISCRIMINATIVE_VETO", "0")
    profiles = [_P("zip", "zip"), _P("name", "name")]
    assert disc.should_veto_exact(_zip_like_df(), "zip", profiles) is False
