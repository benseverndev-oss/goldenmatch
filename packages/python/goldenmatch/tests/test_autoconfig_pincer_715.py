import polars as pl
from goldenmatch.core.autoconfig import ColumnProfile, build_matchkeys


def _df_with(cols):
    return pl.DataFrame({c: ["a", "b", "c"] for c in cols})


def _exact_fields(matchkeys):
    return {
        f.field
        for mk in matchkeys if mk.type == "exact"
        for f in mk.fields
    }


def test_email_high_card_large_n_gets_exact_matchkey():
    """email at card 0.7 must back an exact matchkey regardless of row count
    (Guard 1 / df.height > 10000 must no longer fire)."""
    profiles = [
        ColumnProfile("email", "Utf8", "email", 0.9,
                      null_rate=0.3, cardinality_ratio=0.7),
    ]
    df = _df_with(["email"])
    df = pl.concat([df] * 4000)  # ~12000 rows, would trip old Guard 1
    mks = build_matchkeys(profiles, df=df)
    assert "email" in _exact_fields(mks)


def test_identifier_high_card_gets_exact_matchkey():
    """npi-shaped identifier at card 0.62 must back an exact matchkey
    (col_type='identifier' must no longer be skipped outright)."""
    profiles = [
        ColumnProfile("npi", "Utf8", "identifier", 0.9,
                      null_rate=0.38, cardinality_ratio=0.62),
    ]
    mks = build_matchkeys(profiles, df=_df_with(["npi"]))
    assert "npi" in _exact_fields(mks)


def test_surrogate_key_card_1_excluded():
    """matching_id at card 1.0 is a per-record surrogate key -> NO exact
    matchkey (upper bound of the band)."""
    profiles = [
        ColumnProfile("matching_id", "Utf8", "identifier", 0.9,
                      null_rate=0.0, cardinality_ratio=1.0),
    ]
    mks = build_matchkeys(profiles, df=_df_with(["matching_id"]))
    assert "matching_id" not in _exact_fields(mks)


def test_low_card_still_excluded_megacluster_guard_intact():
    """A low-card column (0.3) must STILL be excluded (mega-cluster guard)."""
    profiles = [
        ColumnProfile("status", "Utf8", "identifier", 0.9,
                      null_rate=0.0, cardinality_ratio=0.3),
    ]
    mks = build_matchkeys(profiles, df=_df_with(["status"]))
    assert "status" not in _exact_fields(mks)


def test_boundary_card_0_5_admitted():
    """card == 0.5 is the lower bound of the admission band (strict < 0.5
    is excluded). Confirm 0.5 itself is admitted."""
    profiles = [
        ColumnProfile("npi", "Utf8", "identifier", 0.9,
                      null_rate=0.0, cardinality_ratio=0.5),
    ]
    mks = build_matchkeys(profiles, df=_df_with(["npi"]))
    assert "npi" in _exact_fields(mks)


def test_aggregate_warning_counts_identifier(caplog):
    """When every exact-eligible column (incl. identifier) is excluded, the
    aggregate warning must count identifier columns too."""
    import logging
    profiles = [
        # low-card identifier -> excluded by the >=0.5 gate
        ColumnProfile("npi", "Utf8", "identifier", 0.9,
                      null_rate=0.0, cardinality_ratio=0.2),
        ColumnProfile("name", "Utf8", "name", 0.9,
                      null_rate=0.0, cardinality_ratio=0.5),
    ]
    with caplog.at_level(logging.WARNING, logger="goldenmatch.core.autoconfig"):
        build_matchkeys(profiles, df=_df_with(["npi", "name"]))
    msgs = " ".join(r.message for r in caplog.records)
    assert "exact-eligible" in msgs and "npi" in msgs


def test_healthcare_shape_commits_exact_matchkey_and_blocking():
    """#715 regression: healthcare-provider shape must auto-configure to a
    config with >= 1 exact matchkey on an identifier-ish column AND a bounded
    blocking key -- not the fuzzy-only, mega-block collapse."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    from goldenmatch.core.autoconfig import auto_configure_df
    from repro_issue_715 import make_healthcare_df

    df = make_healthcare_df(15_000)  # above old Guard 1 (10000), fast
    cfg = auto_configure_df(df, confidence_required=False)

    mks = cfg.get_matchkeys()
    exact_fields = {
        f.field for mk in mks if mk.type == "exact" for f in mk.fields
    }
    assert exact_fields & {"npi", "email", "phone_number"}, (
        f"expected an exact matchkey on an identifier column, got {exact_fields}"
    )

    # Component 2 verification: blocking retained and present.
    blocking = cfg.blocking
    assert blocking is not None and blocking.keys, (
        "expected blocking to be retained, got none"
    )
