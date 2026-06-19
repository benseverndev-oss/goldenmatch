"""Pipeline-level tests for ``semantic_blocking`` candidate-source union.

Task 6 of the recall-lever plan: when ``config.semantic_blocking`` is set,
THREE additional candidate sources (acronym/initialism, alias, ANN) are unioned
onto the normal candidate set, scored by the same scoring the pipeline already
uses, and fed through clustering. The change must be:

- purely ADDITIVE (never drops an existing pair), and
- BYTE-IDENTICAL when ``semantic_blocking`` is None (the default).
"""

import goldenmatch as gm
import polars as pl


# row ids == row order
def _df():
    return pl.DataFrame(
        {
            "name": [
                "International Business Machines",
                "IBM",
                "Acme Inc",
                "Acme Incorporated",
                "Globex",
            ]
        }
    )


def test_off_is_default_and_changes_nothing():
    base = gm.dedupe_df(_df(), fuzzy={"name": 0.6})
    base2 = gm.dedupe_df(_df(), fuzzy={"name": 0.6}, semantic_blocking=False)
    bp = {(min(a, b), max(a, b)) for a, b, _ in base.scored_pairs}
    bp2 = {(min(a, b), max(a, b)) for a, b, _ in base2.scored_pairs}
    assert bp == bp2


def test_semantic_blocking_only_adds_candidates():
    df = _df()
    off = gm.dedupe_df(df, fuzzy={"name": 0.6})
    on = gm.dedupe_df(df, fuzzy={"name": 0.6}, semantic_blocking=True)
    off_pairs = {(min(a, b), max(a, b)) for a, b, _ in off.scored_pairs}
    on_pairs = {(min(a, b), max(a, b)) for a, b, _ in on.scored_pairs}
    assert off_pairs <= on_pairs  # purely additive

    def cluster_of(rid):
        return next(
            cid for cid, info in on.clusters.items() if rid in info["members"]
        )

    assert cluster_of(0) == cluster_of(1)  # IBM <-> Intl Business Machines merged


def test_acronym_merges_through_standardize():
    """The title-case wall: auto-config's standardize step title-cases the name
    column (``name_proper`` -> ``"IBM"`` becomes ``"Ibm"``) BEFORE semantic
    blocking runs. ``derive_initialism`` requires an all-caps token to treat a
    lone acronym as its own block key, so a standardized ``"Ibm"`` derives ``""``
    and never co-locates with its expansion.

    This test isolates the initialism source ONLY (no ann/alias) so the merge
    can ONLY come from initialism keying off the RAW name. With ann/alias on,
    the ANN embedding similarity masks the wall (it merges IBM<->expansion on
    its own), so a "semantic_blocking=True" test passes regardless of this bug.

    The semantic-blocking path must derive its block keys + confirming scores
    from the RAW (un-standardized) name so the acronym signal survives.
    """
    from goldenmatch.config.schemas import SemanticBlockingConfig
    from goldenmatch.core.autoconfig import auto_configure_df

    df = pl.DataFrame(
        {"name": ["International Business Machines", "IBM", "Globex"]}
    )
    # auto-config carries the name_proper standardization rule that title-cases
    # the column; isolate the initialism source so the ANN source can't mask it.
    cfg = auto_configure_df(df)
    cfg.semantic_blocking = SemanticBlockingConfig(keys=["initialism"], alias_tables=[])
    for mk in cfg.get_matchkeys():
        if mk.type == "weighted":
            mk.rerank = False  # avoid HF cross-encoder download in CI

    on = gm.dedupe_df(df, config=cfg)

    def cluster_of(rid):
        return next(c for c, i in on.clusters.items() if rid in i["members"])

    # IBM <-> International Business Machines despite title-casing upstream.
    assert cluster_of(0) == cluster_of(1)
    # Globex (no initialism link) is NOT swept in.
    assert cluster_of(2) != cluster_of(0)


def test_acronym_merges_through_standardize_extra_columns():
    """The prompt's scenario shape (entity_type/context columns present), but
    isolated to the initialism source so the extra equal-valued columns can't
    carry the (0,1) merge on their own.
    """
    from goldenmatch.config.schemas import SemanticBlockingConfig
    from goldenmatch.core.autoconfig import auto_configure_df

    df = pl.DataFrame(
        {
            "name": [
                "IBM",
                "International Business Machines Corporation",
                "Globex Pharmaceuticals",
                "Globex Pharma",
            ],
            "entity_type": ["org", "org", "org", "org"],
            "context": ["tech", "tech", "chem", "chem"],
        }
    )
    cfg = auto_configure_df(df)
    # Initialism source only, and drop the non-name columns from the weighted
    # matchkey so the equal-valued entity_type/context can't mask the wall.
    cfg.semantic_blocking = SemanticBlockingConfig(keys=["initialism"], alias_tables=[])
    for mk in cfg.get_matchkeys():
        if mk.type == "weighted":
            mk.rerank = False
            mk.fields = [f for f in mk.fields if f.field == "name"]

    on = gm.dedupe_df(df, config=cfg)

    def cluster_of(rid):
        return next(c for c, i in on.clusters.items() if rid in i["members"])

    assert cluster_of(0) == cluster_of(1)  # IBM <-> expansion despite title-casing


def test_initialism_confirmed_merges_at_auto_threshold():
    # Name is the ONLY signal: string similarity scores
    # "International Business Machines" <-> "IBM" ~0.58, BELOW the zero-config
    # ensemble@0.8 threshold. Only the confirming `initialism_match` scorer
    # (=1.0) can carry the pair over. (Adding identical helper columns would
    # let the normal fuzzy path merge it on its own and mask the no-op.)
    df = pl.DataFrame(
        {"name": ["International Business Machines", "IBM", "Globex"]}
    )
    on = gm.dedupe_df(df, semantic_blocking=True)  # zero-config -> auto picks ensemble@0.8

    def cluster_of(rid):
        return next(c for c, i in on.clusters.items() if rid in i["members"])

    assert cluster_of(0) == cluster_of(1)  # confirmed by initialism_match=1.0, NOT string sim
    # ...and Globex (no initialism/alias/string link) is NOT swept in. Guards
    # against a source emitting raw sub-threshold scores that over-merge.
    assert cluster_of(2) != cluster_of(0)


def test_semantic_blocking_registers_initialism_at_point_of_use():
    """Regression: the initialism source must register its transform at the POINT
    OF USE. ``initialism`` is registered only as an import side-effect of
    ``core.acronym``, which the normal ``dedupe_df`` flow never imports -- so on a
    fresh process / xdist worker that has not imported it, the source raises
    ``ValueError: Unknown transform: 'initialism'``, fail-opens, and the merge
    silently never happens (a no-op for users; the cause of the shard-3 xdist
    flake). Simulate that worker by removing ``initialism`` from the registry
    before the run, then assert the pipeline re-registers it and the merge fires.
    """
    from goldenmatch.config.schemas import SemanticBlockingConfig
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.plugins.registry import PluginRegistry

    reg = PluginRegistry.instance()
    saved = reg.get_transform("initialism")
    reg._transforms.pop("initialism", None)
    try:
        assert not reg.has_transform("initialism")  # precondition: unregistered worker

        df = _df()  # row 0 "International Business Machines", row 1 "IBM"
        cfg = auto_configure_df(df)
        # Initialism source only (no ann/alias) so the merge can ONLY come from
        # the initialism transform that the cleared registry is missing.
        cfg.semantic_blocking = SemanticBlockingConfig(keys=["initialism"], alias_tables=[])
        for mk in cfg.get_matchkeys():
            if mk.type == "weighted":
                mk.rerank = False  # avoid HF cross-encoder download in CI

        on = gm.dedupe_df(df, config=cfg)

        def cluster_of(rid):
            return next(c for c, i in on.clusters.items() if rid in i["members"])

        assert cluster_of(0) == cluster_of(1)  # IBM <-> expansion, despite cleared registry
        assert reg.has_transform("initialism")  # pipeline re-registered at point of use
    finally:
        if saved is not None and not reg.has_transform("initialism"):
            reg.register_transform("initialism", saved)
