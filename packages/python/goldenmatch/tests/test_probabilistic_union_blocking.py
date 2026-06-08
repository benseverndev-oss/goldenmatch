from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.pipeline import _em_excluded_fields


def test_em_excluded_multipass_is_intersection_empty():
    # No field appears in BOTH passes -> nothing is agree-by-construction
    # everywhere -> exclude nothing (EM can estimate m/u for all of them).
    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["first_name", "birth_year"]),
            BlockingKeyConfig(fields=["surname"]),
        ],
    )
    assert _em_excluded_fields(blocking) == []


def test_em_excluded_single_static_key():
    # Single static key = one pass; its fields always agree within block -> exclude.
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    assert _em_excluded_fields(blocking) == ["zip"]


def test_em_excluded_intersection_of_overlapping_passes():
    # zip is in EVERY pass (always agrees); surname/first_name each only in one.
    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["zip", "surname"]),
            BlockingKeyConfig(fields=["zip", "first_name"]),
        ],
    )
    assert _em_excluded_fields(blocking) == ["zip"]


def test_em_excluded_none_is_empty():
    assert _em_excluded_fields(None) == []


def test_em_excluded_multipass_common_field():
    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["zip", "a"]),
            BlockingKeyConfig(fields=["zip", "b"]),
            BlockingKeyConfig(fields=["zip"]),
        ],
    )
    assert _em_excluded_fields(blocking) == ["zip"]


def test_build_probabilistic_blocking_is_selective_multipass():
    # POST-REWRITE CONTRACT (selective set-cover, NOT the old broad augment):
    # _build_probabilistic_blocking emits a budget-bounded multi_pass UNION with a
    # name-bearing recall anchor. It no longer guarantees a superset of build_blocking
    # (it selects a coverage-greedy subset under K*N), and it re-expresses each pass's
    # transforms as per-field `field_transforms` rather than the shared `transforms`.
    import polars as pl
    from goldenmatch.core.autoconfig import (
        _build_probabilistic_blocking,
        build_blocking,
        profile_columns,
    )
    df = pl.DataFrame({
        "first_name": (["ann","bob","cara","dan","eve","fay","gus","hal"] * 40),
        "surname":    (["lee","kim","ng","ono","poe","qiu","rao","sun"] * 40),
        "dob":        ([f"19{y:02d}-01-01" for y in range(40)] * 8),
        "postcode":   ([f"P{p:03d}" for p in range(40)] * 8),
    })
    profiles = profile_columns(df)
    base = build_blocking(profiles, df)
    base_fields = {f for p in (base.passes or base.keys or []) for f in p.fields}

    blocking = _build_probabilistic_blocking(profiles, df)
    assert blocking.strategy == "multi_pass"
    assert blocking.passes, "must emit passes"
    # RECALL ANCHOR: at least one emitted pass references a recall-floor field
    # (any field build_blocking itself selected).
    assert any(set(p.fields) & base_fields for p in blocking.passes), (
        "no recall-anchor (build_blocking) field survived selection")


def test_build_probabilistic_blocking_carries_transforms_per_field():
    # POST-REWRITE: the recall-driving transforms build_blocking attached are not
    # dropped -- they are carried on each emitted pass's `field_transforms` (per-field),
    # since the selective pool builds passes that way. Assert a transform-bearing base
    # field's transforms survive on the emitted pass that references it.
    import polars as pl
    from goldenmatch.core.autoconfig import (
        _build_probabilistic_blocking,
        profile_columns,
    )
    df = pl.DataFrame({
        "first_name": (["ann","bob","cara","dan"] * 80),
        "surname":    (["lee","kim","ng","ono"] * 80),
        "dob":        ([f"19{y:02d}-01-01" for y in range(40)] * 8),
    })
    profiles = profile_columns(df)
    out = _build_probabilistic_blocking(profiles, df)
    assert out.strategy == "multi_pass"
    assert out.passes
    # every emitted pass expresses its transforms per-field (field_transforms), and at
    # least one carries a non-empty transform chain (the recall lever is preserved).
    assert any(
        p.field_transforms and any(chain for chain in p.field_transforms)
        for p in out.passes
    ), "selective passes dropped all field transforms"


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
