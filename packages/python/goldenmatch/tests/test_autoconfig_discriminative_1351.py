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
