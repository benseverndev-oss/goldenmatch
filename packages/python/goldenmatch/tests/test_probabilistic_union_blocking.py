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


def test_build_probabilistic_blocking_augments_not_replaces():
    import polars as pl
    from goldenmatch.core.autoconfig import (
        _build_probabilistic_blocking,
        build_blocking,
        profile_columns,
    )
    # person-shaped frame with name cols (build_blocking will pass on these)
    # PLUS orthogonal cols (dob, postcode) that should be ADDED.
    df = pl.DataFrame({
        "first_name": (["ann","bob","cara","dan","eve","fay","gus","hal"] * 40),
        "surname":    (["lee","kim","ng","ono","poe","qiu","rao","sun"] * 40),
        "dob":        ([f"19{y:02d}-01-01" for y in range(40)] * 8),
        "postcode":   ([f"P{p:03d}" for p in range(40)] * 8),
    })
    profiles = profile_columns(df)
    base = build_blocking(profiles, df)
    base_passes = base.passes or base.keys or []
    base_field_sets = {tuple(sorted(p.fields)) for p in base_passes}

    blocking = _build_probabilistic_blocking(profiles, df)
    assert blocking.strategy == "multi_pass"
    assert blocking.passes is not None
    out_field_sets = {tuple(sorted(p.fields)) for p in blocking.passes}
    # FLOOR PRESERVED: every base pass (with its transforms) survives
    assert base_field_sets.issubset(out_field_sets), (
        f"augment dropped base passes: base={base_field_sets} out={out_field_sets}")
    # ORTHOGONAL ADDED: at least one pass references a column not used by base
    base_fields = {f for p in base_passes for f in p.fields}
    extra_fields = {f for p in blocking.passes for f in p.fields} - base_fields
    assert extra_fields, "no orthogonal columns were added"


def test_build_probabilistic_blocking_preserves_transforms_on_base_passes():
    # The transforms build_blocking attached (soundex/substring/token_sort) are
    # the recall drivers; augment must NOT strip them.
    import polars as pl
    from goldenmatch.core.autoconfig import (
        _build_probabilistic_blocking,
        build_blocking,
        profile_columns,
    )
    df = pl.DataFrame({
        "first_name": (["ann","bob","cara","dan"] * 80),
        "surname":    (["lee","kim","ng","ono"] * 80),
        "dob":        ([f"19{y:02d}-01-01" for y in range(40)] * 8),
    })
    profiles = profile_columns(df)
    base = build_blocking(profiles, df)
    base_passes = base.passes or base.keys or []
    base_with_transforms = {(tuple(sorted(p.fields)), tuple(p.transforms))
                            for p in base_passes if p.transforms}
    out = _build_probabilistic_blocking(profiles, df)
    out_with_transforms = {(tuple(sorted(p.fields)), tuple(p.transforms))
                           for p in (out.passes or []) if p.transforms}
    # every transform-bearing base pass is preserved verbatim
    assert base_with_transforms.issubset(out_with_transforms)


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
