import goldenmatch as gm
import polars as pl
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig


def _fixture_df() -> pl.DataFrame:
    rows = [
        {"name": f"distinct person {i}", "city": f"city{i}", "zip": f"{10000+i}"}
        for i in range(10)
    ]
    rows.append({"name": "john smith", "city": "alpha", "zip": "99999"})   # row 10
    rows.append({"name": "john smith", "city": "beta",  "zip": "99999"})   # row 11
    return pl.DataFrame(rows)


def _name_matchkey():
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    return MatchkeyConfig(
        name="name_match", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
    )


def _two_pass_config():
    from goldenmatch.core.autoconfig import auto_configure_df
    cfg = auto_configure_df(_fixture_df())
    cfg.blocking = BlockingConfig(
        strategy="multi_pass",
        keys=[BlockingKeyConfig(fields=["city"])],
        passes=[BlockingKeyConfig(fields=["city"]), BlockingKeyConfig(fields=["zip"])],
    )
    cfg.matchkeys = [_name_matchkey()]
    assert len(cfg.get_matchkeys()) == 1
    return cfg


def _multi_member_clusters(df, cfg, backend) -> set[frozenset[int]]:
    c = cfg.model_copy(deep=True)
    if backend is not None:
        c.backend = backend
    result = gm.dedupe_df(df, config=c)
    out: set[frozenset[int]] = set()
    for _cid, info in result.clusters.items():
        ids = info.get("members") if isinstance(info, dict) else None
        if ids and len(ids) >= 2:
            out.add(frozenset(int(x) for x in ids))
    return out


def test_bucket_matches_polars_on_multipass():
    df = _fixture_df()
    cfg = _two_pass_config()
    assert cfg.blocking.passes and len(cfg.blocking.passes) == 2
    polars_clusters = _multi_member_clusters(df, cfg, "polars-direct")
    bucket_clusters = _multi_member_clusters(df, cfg, "bucket")
    assert frozenset({10, 11}) in polars_clusters
    assert bucket_clusters == polars_clusters


def test_single_pass_bucket_unchanged():
    df = pl.DataFrame({
        "name": [f"person {i}" for i in range(8)] + ["mary jones", "mary jones"],
        "zip":  [f"{200+i}" for i in range(8)] + ["55555", "55555"],
    })
    from goldenmatch.core.autoconfig import auto_configure_df
    cfg = auto_configure_df(df)
    cfg.blocking = BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"])])
    cfg.matchkeys = [_name_matchkey()]
    assert cfg.blocking.passes is None
    polars = _multi_member_clusters(df, cfg, "polars-direct")
    bucket = _multi_member_clusters(df, cfg, "bucket")
    assert bucket == polars
    assert frozenset({8, 9}) in bucket


def test_cross_pass_duplicate_pairs_collapse():
    # Rows 10/11 share BOTH `city` and `zip`, so the {10,11} pair is emitted by
    # BOTH the city pass AND the zip pass. The bucket path re-scores each pass
    # independently and emits the duplicate pair twice; build_clusters'
    # pair_scores dict collapses the duplicate emissions into ONE cluster. This
    # locks that collapse (no crash, no doubled cluster state) and that it
    # matches polars-direct, which dedups the key across passes upstream.
    rows = [
        {"name": f"distinct person {i}", "city": f"city{i}", "zip": f"{10000+i}"}
        for i in range(10)
    ]
    rows.append({"name": "john smith", "city": "gamma", "zip": "77777"})  # row 10
    rows.append({"name": "john smith", "city": "gamma", "zip": "77777"})  # row 11
    df = pl.DataFrame(rows)

    from goldenmatch.core.autoconfig import auto_configure_df
    cfg = auto_configure_df(df)
    cfg.blocking = BlockingConfig(
        strategy="multi_pass",
        keys=[BlockingKeyConfig(fields=["city"])],
        passes=[BlockingKeyConfig(fields=["city"]), BlockingKeyConfig(fields=["zip"])],
    )
    cfg.matchkeys = [_name_matchkey()]
    assert cfg.blocking.passes and len(cfg.blocking.passes) == 2

    bucket = _multi_member_clusters(df, cfg, "bucket")
    polars = _multi_member_clusters(df, cfg, "polars-direct")

    # (a) duplicate emissions collapse to exactly ONE multi-member cluster
    assert bucket == {frozenset({10, 11})}
    # (b) equals polars-direct
    assert bucket == polars


def test_oversized_block_skipped_matches_polars():
    """A block larger than max_block_size is skipped, matching polars-direct.
    bucket and polars-direct must agree, and neither forms a giant cluster from
    the oversized block."""
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
    from goldenmatch.core.autoconfig import auto_configure_df
    # 60 rows all sharing one blocking key 'grp' = "A" -> one block of 60.
    # With max_block_size=10 + skip_oversized, that block is skipped.
    df = pl.DataFrame({
        "name": [f"person number {i}" for i in range(60)],
        "grp": ["A"] * 60,
    })
    cfg = auto_configure_df(df)
    cfg.blocking = BlockingConfig(
        strategy="static", keys=[BlockingKeyConfig(fields=["grp"])],
        max_block_size=10, skip_oversized=True,
    )
    cfg.matchkeys = [_name_matchkey()]
    polars = _multi_member_clusters(df, cfg, "polars-direct")
    bucket = _multi_member_clusters(df, cfg, "bucket")
    assert bucket == polars  # both skip the 60-row block -> same (likely empty) clusters


def test_bucket_multipass_passes_only_no_keys():
    """multi_pass blocking with `passes` set but `keys` EMPTY must score on the
    bucket backend, matching polars-direct.

    Regression: score_buckets' entry guard read only `blocking_config.keys` and
    returned [] when it was empty -- but multi_pass carries its keys in
    `.passes` (the canonical shape auto-config and explicit configs emit). The
    scoring loop already iterated `passes or keys`, so the guard silently
    no-op'd every passes-only multi_pass config on the bucket backend (FS and
    weighted alike). Every prior multipass test set BOTH keys and passes, so
    the guard was never exercised on the True branch.
    """
    df = _fixture_df()
    from goldenmatch.core.autoconfig import auto_configure_df
    cfg = auto_configure_df(df)
    cfg.blocking = BlockingConfig(
        strategy="multi_pass",
        keys=[],  # <-- the bug condition: passes-only
        passes=[BlockingKeyConfig(fields=["city"]), BlockingKeyConfig(fields=["zip"])],
    )
    cfg.matchkeys = [_name_matchkey()]
    assert not cfg.blocking.keys and cfg.blocking.passes

    polars = _multi_member_clusters(df, cfg, "polars-direct")
    bucket = _multi_member_clusters(df, cfg, "bucket")
    assert frozenset({10, 11}) in bucket  # would be empty under the bug
    assert bucket == polars


def test_missing_pass_field_is_skipped():
    df = _fixture_df()  # has name/city/zip, NOT 'ssn'
    cfg = _two_pass_config()
    cfg.blocking = BlockingConfig(
        strategy="multi_pass",
        keys=[BlockingKeyConfig(fields=["zip"])],
        passes=[BlockingKeyConfig(fields=["zip"]), BlockingKeyConfig(fields=["ssn"])],
    )
    bucket = _multi_member_clusters(df, cfg, "bucket")  # must not raise
    assert frozenset({10, 11}) in bucket
