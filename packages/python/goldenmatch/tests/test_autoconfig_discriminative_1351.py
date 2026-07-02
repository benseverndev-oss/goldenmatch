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
    names = {b[0] for b in basket}
    assert names == {"first_name", "last_name", "email", "npi"}
    assert "zip" not in names and "city" not in names and "notes" not in names
    # name-typed fields compare fuzzily; structured ids exactly.
    fuzzy = dict(basket)
    assert fuzzy["first_name"] is True and fuzzy["last_name"] is True
    assert fuzzy["email"] is False and fuzzy["npi"] is False


def test_identity_basket_excludes_the_candidate_even_if_identity_typed():
    profiles = [_P("npi", "identifier"), _P("email", "email")]
    assert disc.identity_basket("npi", profiles) == [("email", False)]


def _distinct_name(seed: int) -> str:
    """Deterministic 6-letter pseudo-random token. Different seeds are mutually
    DISSIMILAR (low SequenceMatcher ratio), modeling unrelated people's names;
    the same seed reproduces the same name, modeling one entity."""
    x = ((seed + 1) * 2654435761) & 0xFFFFFFFF
    return "".join(chr(97 + ((x >> (5 * j)) % 26)) for j in range(6))


def _zip_like_df(n=300):
    zips = [f"{10000 + (i % 30):05d}" for i in range(n)]
    names = [_distinct_name(i) for i in range(n)]  # different people -> dissimilar
    return pl.DataFrame({"zip": zips, "name": names})


def _npi_like_df(n=300):
    npis = [f"{1000000000 + (i % 30)}" for i in range(n)]
    names = [_distinct_name(i % 30) for i in range(n)]  # same provider -> same name
    return pl.DataFrame({"npi": npis, "name": names})


def _corrupted_dup_df(n=300):
    # 30 SSNs x 10 rows each; rows sharing an SSN are the SAME person, but ~half
    # carry a CORRUPTED surname (one extra char) -- exact-equal fails on those,
    # fuzzy agrees. Models febrl3's corrupted duplicates.
    ssns = [f"{700000000 + (i % 30)}" for i in range(n)]
    surnames = []
    for i in range(n):
        base = _distinct_name(i % 30)          # same person per SSN group
        corrupt = (i // 30) % 2 == 1            # ~half the group's rows, by position
        surnames.append(base + "z" if corrupt else base)
    return pl.DataFrame({"soc_sec_id": ssns, "surname": surnames})


def test_discriminative_power_low_for_shared_locality():
    power, support = disc.discriminative_power(_zip_like_df(), "zip", [("name", True)])
    assert support >= disc._MIN_SHARED_PAIRS
    assert power < 0.5


def test_discriminative_power_high_for_shared_identity():
    power, support = disc.discriminative_power(_npi_like_df(), "npi", [("name", True)])
    assert support >= disc._MIN_SHARED_PAIRS
    assert power > 0.9


def test_discriminative_power_fuzzy_keeps_corrupted_duplicate_ids():
    # The febrl3 failure in miniature: a genuine id whose duplicate records have
    # corrupted (fuzzy-similar) names. Exact comparison would read this as
    # disagreement and veto the id; fuzzy comparison keeps it.
    df = _corrupted_dup_df()
    fuzzy_power, support = disc.discriminative_power(df, "soc_sec_id", [("surname", True)])
    exact_power, _ = disc.discriminative_power(df, "soc_sec_id", [("surname", False)])
    assert support >= disc._MIN_SHARED_PAIRS
    assert exact_power < 0.6  # exact under-counts the corrupted duplicates
    assert fuzzy_power > 0.9  # fuzzy recovers them


def test_discriminative_power_zero_support_when_all_unique():
    df = pl.DataFrame({"id": [str(i) for i in range(100)], "name": [f"n{i}" for i in range(100)]})
    power, support = disc.discriminative_power(df, "id", [("name", True)])
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
