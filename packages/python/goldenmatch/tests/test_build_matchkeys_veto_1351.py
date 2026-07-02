import polars as pl
from goldenmatch.core.autoconfig import ColumnProfile, build_matchkeys


def _exact_fields(mks):
    return {f.field for mk in mks if mk.type == "exact" for f in mk.fields if f.field}


def _mk_profiles(specs):  # specs: list[(name, col_type, cardinality_ratio)]
    return [
        ColumnProfile(name=n, dtype="str", col_type=t, confidence=0.9,
                      sample_values=["x"], null_rate=0.0, cardinality_ratio=c, avg_len=6.0)
        for (n, t, c) in specs
    ]


def _distinct_name(seed: int) -> str:
    """Deterministic 6-letter token; different seeds are mutually dissimilar
    (models unrelated people), the same seed reproduces one entity's name."""
    x = ((seed + 1) * 2654435761) & 0xFFFFFFFF
    return "".join(chr(97 + ((x >> (5 * j)) % 26)) for j in range(6))


def test_zip_promoted_to_identifier_is_vetoed_from_exact():
    # zip mis-typed as identifier; shared-zip records are DIFFERENT people with
    # dissimilar names -> low co-agreement -> vetoed out of the exact matchkeys.
    profiles = _mk_profiles([("zip", "identifier", 0.96), ("first_name", "name", 0.9),
                             ("last_name", "name", 0.9)])
    n = 400
    df = pl.DataFrame({
        "zip": [f"{10000 + (i % 40):05d}" for i in range(n)],
        "first_name": [_distinct_name(i) for i in range(n)],
        "last_name": [_distinct_name(i + 100000) for i in range(n)],
    })
    assert "zip" not in _exact_fields(build_matchkeys(profiles, df=df))


def test_npi_identifier_is_kept_as_exact():
    # shared-npi records are the SAME provider (same name) -> high co-agreement -> kept.
    profiles = _mk_profiles([("npi", "identifier", 0.9), ("first_name", "name", 0.9)])
    n = 400
    df = pl.DataFrame({
        "npi": [f"{1000000000 + (i % 40)}" for i in range(n)],
        "first_name": [_distinct_name(i % 40) for i in range(n)],
    })
    assert "npi" in _exact_fields(build_matchkeys(profiles, df=df))


def test_df_none_is_noop_keep():
    profiles = _mk_profiles([("email", "identifier", 0.9)])
    assert "email" in _exact_fields(build_matchkeys(profiles, df=None))
