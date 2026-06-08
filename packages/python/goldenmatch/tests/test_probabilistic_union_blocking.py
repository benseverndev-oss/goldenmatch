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
    # NAME-bearing recall anchor. It no longer guarantees a superset of build_blocking
    # (it selects a coverage-greedy subset under K*N), and it re-expresses each pass's
    # transforms as per-field `field_transforms` rather than the shared `transforms`.
    #
    # Honest invariant: assert a NAME-field-bearing pass survives -- NOT whatever
    # build_blocking happened to emit. (The earlier postcode-bearing fixture let a
    # `[postcode]` zip pass satisfy the assertion while every name pass was dropped --
    # build_blocking itself emits only `[postcode]` for that shape, so there was no name
    # recall to anchor.) Use real name columns where the name-recall pass is the lever.
    import polars as pl
    from goldenmatch.core.autoconfig import (
        _build_probabilistic_blocking,
        profile_columns,
    )
    df = pl.DataFrame({
        "first_name": (["ann","bob","cara","dan","eve","fay","gus","hal"] * 40),
        "surname":    (["lee","kim","ng","ono","poe","qiu","rao","sun"] * 40),
        "dob":        ([f"19{y:02d}-01-01" for y in range(40)] * 8),
    })
    profiles = profile_columns(df)
    # first_name/surname classify as col_type "name", dob as "date".
    assert {p.name: p.col_type for p in profiles}["first_name"] == "name"

    blocking = _build_probabilistic_blocking(profiles, df)
    assert blocking.strategy == "multi_pass"
    assert blocking.passes, "must emit passes"
    # RECALL ANCHOR: at least one emitted pass references a NAME field (first_name or
    # surname). A `[dob]` date pass must NOT satisfy this -- the anchor exists to keep a
    # name-recall pass alive, not just any base-pass field.
    assert any(
        ("first_name" in p.fields or "surname" in p.fields) for p in blocking.passes
    ), "no NAME-bearing recall pass survived selection"


def test_build_probabilistic_blocking_carries_transforms_per_field():
    # POST-REWRITE: the recall-driving transforms build_blocking attached are not
    # dropped -- they are carried on each emitted pass's `field_transforms` (per-field),
    # since the selective pool builds passes that way.
    #
    # Honest invariant: a RECALL-driving transform (soundex / substring -- the
    # typo-tolerance lever) must survive. A trivial `lowercase`/`strip` chain (which the
    # `[dob]` pass carries) does NOT count -- the earlier "any non-empty chain" assertion
    # was vacuous because the date pass alone satisfied it.
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
    # at least one emitted pass carries a soundex/substring recall transform on a field
    # (not merely a lowercase/strip cleanup) -- the typo-tolerance lever is preserved.
    has_recall_xf = any(
        "soundex" in c or "substring" in c
        for p in out.passes
        for chain in (p.field_transforms or [])
        for c in chain
    )
    assert has_recall_xf, "selective passes dropped the soundex/substring recall transforms"


def test_build_probabilistic_blocking_anchor_survives_tight_budget(monkeypatch):
    # REGRESSION GUARD for the recall-anchor bug: build_blocking emits a `[dob]` DATE
    # recall pass (#438), so dob lands in the base-pass field set. If the anchor binds to
    # ALL base fields (incl. dob), a `[dob]` pass SATISFIES the anchor -- so when the real
    # name passes bust the K*N budget and get dropped, no name pass is brought back and
    # name-typo recall is silently lost. The anchor must bind to NAME-type fields only.
    #
    # Fixture: few distinct names + many rows -> name blocks are large relative to K*N;
    # with K=1 the name passes do not fit, so ONLY the cheap `[dob]` pass survives the
    # greedy loop. The anchor (name-type fields, dob excluded) must override the budget
    # and force a name-bearing pass back. Without FIX 1 this emits only `[dob]`.
    import polars as pl
    from goldenmatch.core.autoconfig import _build_probabilistic_blocking, profile_columns
    names = ["smith", "jones"] * 60
    df = pl.DataFrame({
        "first_name": names,
        "surname": names,
        "dob": [f"19{50+(i%40):02d}-01-01" for i in range(120)],
    })
    monkeypatch.setenv("GOLDENMATCH_BLOCKING_CANDIDATE_BUDGET_K", "1")  # tiny budget
    profiles = profile_columns(df)
    # Sanity: dob is date-typed (the field that must NOT satisfy the name anchor).
    by_type = {p.name: p.col_type for p in profiles}
    assert by_type["dob"] == "date"
    assert by_type["first_name"] == "name"
    cfg = _build_probabilistic_blocking(profiles, df)
    name_bearing = [
        p for p in (cfg.passes or cfg.keys or [])
        if ("first_name" in p.fields or "surname" in p.fields)
    ]
    assert name_bearing, "recall anchor must keep a name-bearing pass even under a tiny budget"


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
