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
