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


def test_zip_promoted_to_identifier_is_vetoed_from_exact():
    profiles = _mk_profiles([("zip", "identifier", 0.96), ("first_name", "name", 0.9),
                             ("last_name", "name", 0.9)])
    n = 400
    df = pl.DataFrame({
        "zip": [f"{10000 + (i % 40):05d}" for i in range(n)],
        "first_name": [f"fn{i}" for i in range(n)],
        "last_name": [f"ln{i}" for i in range(n)],
    })
    assert "zip" not in _exact_fields(build_matchkeys(profiles, df=df))


def test_npi_identifier_is_kept_as_exact():
    profiles = _mk_profiles([("npi", "identifier", 0.9), ("first_name", "name", 0.9)])
    n = 400
    df = pl.DataFrame({
        "npi": [f"{1000000000 + (i % 40)}" for i in range(n)],
        "first_name": [f"fn{i % 40}" for i in range(n)],
    })
    assert "npi" in _exact_fields(build_matchkeys(profiles, df=df))


def test_df_none_is_noop_keep():
    profiles = _mk_profiles([("email", "identifier", 0.9)])
    assert "email" in _exact_fields(build_matchkeys(profiles, df=None))
