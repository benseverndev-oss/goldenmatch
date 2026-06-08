from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.pipeline import _collect_blocking_fields


def test_blocking_fields_include_passes():
    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["first_name", "birth_year"]),
            BlockingKeyConfig(fields=["surname"]),
        ],
    )
    assert set(_collect_blocking_fields(blocking)) == {"first_name", "birth_year", "surname"}


def test_blocking_fields_include_keys_only_still_works():
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    assert set(_collect_blocking_fields(blocking)) == {"zip"}


def test_blocking_fields_union_of_keys_and_passes_deduped():
    blocking = BlockingConfig(
        strategy="multi_pass",
        keys=[BlockingKeyConfig(fields=["zip"])],
        passes=[BlockingKeyConfig(fields=["zip", "surname"])],
    )
    # union, de-duplicated, order-preserving not required by the assertion
    assert set(_collect_blocking_fields(blocking)) == {"zip", "surname"}


def test_blocking_fields_none_is_empty():
    assert _collect_blocking_fields(None) == []


def test_build_probabilistic_blocking_emits_capped_multipass():
    import polars as pl
    from goldenmatch.core.autoconfig import _build_probabilistic_blocking, profile_columns
    df = pl.DataFrame({
        "first_name": ["ann", "ann", "bob", "bob", "cara", "cara"] * 50,
        "surname":    ["lee", "lee", "kim", "kim", "ng", "ng"] * 50,
        "birth_year": ["1990", "1990", "1985", "1985", "1972", "1972"] * 50,
        "postcode":   ["AA1", "AA1", "BB2", "BB2", "CC3", "CC3"] * 50,
    })
    profiles = profile_columns(df)
    blocking = _build_probabilistic_blocking(profiles, df)
    assert blocking.strategy == "multi_pass"
    assert blocking.passes is not None
    assert 1 <= len(blocking.passes) <= 4      # capped
    assert blocking.skip_oversized is True


def test_probabilistic_dedupe_with_multipass_runs():
    import polars as pl
    from goldenmatch import dedupe_df
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    df = pl.DataFrame({
        "first_name": ["ann","an","bob","bobby","cara","cara"],
        "surname":    ["lee","lee","kim","kim","ng","ng"],
        "birth_year": ["1990","1990","1985","1985","1972","1972"],
    })
    cfg = auto_configure_probabilistic_df(df)
    res = dedupe_df(df, config=cfg)
    assert res is not None  # runs end-to-end, multi-pass + F-S, no m/u collapse
