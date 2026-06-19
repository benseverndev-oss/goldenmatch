"""Phase A of #1082: lexical text-corpus auto-enable.

Covers the `_is_text_corpus` detector (A1), the build_blocking routing of a
text corpus to `strategy="lsh"` (A2), the controller guard that keeps the
auto-config refit rules from swapping an lsh/simhash strategy away (A3), and
a zero-config end-to-end text dedupe (A4).
"""
from __future__ import annotations

import polars as pl
from goldenmatch.core.autoconfig import (
    ColumnProfile,
    _is_text_corpus,
)

# ───────────────────────── A1: _is_text_corpus detector ─────────────────────


def _desc(name: str, *, card: float = 0.95, avg_len: float = 60.0) -> ColumnProfile:
    return ColumnProfile(
        name, "Utf8", "description", 0.9,
        sample_values=["a long sentence about something", "another long sentence"],
        null_rate=0.0, cardinality_ratio=card, avg_len=avg_len,
    )


def _name(name: str, *, card: float, col_type: str = "name") -> ColumnProfile:
    return ColumnProfile(
        name, "Utf8", col_type, 0.9,
        sample_values=["smith", "jones", "garcia"],
        null_rate=0.0, cardinality_ratio=card, avg_len=5.0,
    )


def test_single_description_column_is_text_corpus():
    assert _is_text_corpus([_desc("body")]) is True


def test_high_card_name_plus_description_is_not_text_corpus():
    # A real blockable name column (card 0.8 >= 0.1) means this is structured
    # data with a free-text field, NOT a pure text corpus.
    assert _is_text_corpus([_name("last_name", card=0.8), _desc("notes")]) is False


def test_low_card_name_plus_description_is_text_corpus():
    # A low-cardinality "name" (card 0.02) can't block usefully, so a
    # description-bearing dataset still reads as a text corpus.
    assert _is_text_corpus([_name("label", card=0.02), _desc("body")]) is True


def test_name_only_is_not_text_corpus():
    assert _is_text_corpus([_name("last_name", card=0.8)]) is False


def test_multi_name_high_card_blocks_text_corpus():
    # multi_name is also a blockable name type.
    assert _is_text_corpus([_name("authors", card=0.5, col_type="multi_name"), _desc("title")]) is False


# ───────────────────────── A2: route text corpora to lsh ────────────────────


def test_build_blocking_routes_text_corpus_to_lsh():
    from goldenmatch.core.autoconfig import build_blocking, profile_columns

    base = "the quick brown fox jumps over the lazy dog near the river bank"
    variants = [
        base,
        base.replace("quick", "fast"),
        base.replace("lazy", "sleepy"),
        "a completely different statement about astronomy and distant galaxies far away",
        "yet another unrelated remark concerning gardening tools and spring planting",
        "an entirely separate note discussing maritime navigation and old sailing charts",
    ]
    df = pl.DataFrame({"body": variants * 5})
    profiles = profile_columns(df)
    blk = build_blocking(profiles, df)
    assert blk.strategy == "lsh"
    assert blk.lsh is not None
    assert blk.lsh.column == "body"
    assert blk.lsh.mode == "word"
    assert blk.lsh.num_perms == 128


# ───────────────────────── A3: controller guard ─────────────────────────────


def _lsh_config():
    from goldenmatch.config.schemas import (
        BlockingConfig,
        GoldenMatchConfig,
        LSHKeyConfig,
    )

    return GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="lsh",
            lsh=LSHKeyConfig(column="body", threshold=0.5),
        )
    )


def _swap_prone_profile():
    """A ComplexityProfile shaped to trip the blocking-rewrite rules:
    blocks formed, zero candidates compared, oversized p99, low reduction.

    The controller guard short-circuits each rule before it reads the
    profile, so the exact values here only matter as "would otherwise fire"
    bait — they don't need to reflect a real run.
    """
    from goldenmatch.core.complexity_profile import (
        BlockingProfile,
        ComplexityProfile,
        DataProfile,
        ScoringProfile,
    )

    return ComplexityProfile(
        data=DataProfile(
            n_rows=10_000,
            n_cols=1,
            column_types={"body": "text"},
            null_rate={"body": 0.0},
        ),
        blocking=BlockingProfile(
            n_blocks=5,
            block_sizes_p50=1,
            block_sizes_p99=9_000,
            block_sizes_max=9_000,
            reduction_ratio=0.05,
        ),
        scoring=ScoringProfile(candidates_compared=0),
    )


def test_guarded_rules_return_none_when_lsh_locked():
    from goldenmatch.core import autoconfig_rules as ar
    from goldenmatch.core.autoconfig_history import RunHistory

    cfg = _lsh_config()
    profile = _swap_prone_profile()
    history = RunHistory()

    guarded = [
        ar.rule_blocking_singleton_trap,
        ar.rule_blocking_too_coarse,
        ar.rule_blocking_key_swap,
        ar.rule_uniform_heavy_blocking,
        ar.rule_blocking_field_null_heavy,
        ar.rule_low_reduction_ratio,
        ar.rule_recall_gap_suspected,
        ar.rule_cross_blocking_disagreement,
        ar.rule_blocking_adaptive_on_p99_outlier,
    ]
    for rule in guarded:
        assert rule(profile, cfg, history) is None, (
            f"{rule.__name__} swapped blocking despite lsh lock"
        )


def test_near_dup_locked_helper():
    from goldenmatch.config.schemas import (
        BlockingConfig,
        GoldenMatchConfig,
        LSHKeyConfig,
    )
    from goldenmatch.core.autoconfig_rules import _near_dup_locked

    lsh = GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="lsh", lsh=LSHKeyConfig(column="body", threshold=0.5)
        )
    )
    assert _near_dup_locked(lsh) is True

    static = GoldenMatchConfig()
    assert _near_dup_locked(static) is False

    no_blocking = GoldenMatchConfig()
    no_blocking.blocking = None
    assert _near_dup_locked(no_blocking) is False


# ───────────────────────── A4: zero-config text dedupe e2e ───────────────────


def test_zero_config_text_corpus_dedupe_e2e():
    import goldenmatch

    base = "the annual budget report shows a significant increase in marketing spend"
    near_dups = [
        base,
        base.replace("significant", "substantial"),
        base.replace("marketing", "advertising"),
    ]
    distinct = [
        "weather patterns over the pacific ocean shifted dramatically this winter season",
        "the museum unveiled a new exhibit featuring ancient pottery from coastal villages",
        "local farmers reported record yields after an unusually wet and mild growing year",
        "the orchestra performed a moving rendition of a forgotten romantic era symphony",
        "engineers completed the suspension bridge two months ahead of the planned schedule",
    ]
    rows = near_dups + distinct
    # Repeat the distinct rows a couple times to give the corpus some bulk;
    # each repeat is itself an exact near-dup group, but the assertions below
    # only check the near_dups group and that distinct sentences don't merge
    # with the near_dups group.
    df = pl.DataFrame({"text": rows})

    result = goldenmatch.dedupe_df(df, confidence_required=False)

    # Recover the cluster id per row via the deduped frame, if exposed; else
    # fall back to the clusters mapping on the result.
    clusters = result.clusters  # dict[int, dict] keyed by cluster id
    # Build row_id -> cluster_id.
    member_to_cluster: dict[int, int] = {}
    for cid, info in clusters.items():
        for member in info["members"]:
            member_to_cluster[member] = cid

    # The three near-dup rows are row ids 0, 1, 2.
    near_dup_clusters = {member_to_cluster.get(i) for i in (0, 1, 2)}
    # They must all land together (one shared cluster id, not None).
    assert len(near_dup_clusters) == 1, (
        f"near-dup rows split across clusters: {near_dup_clusters}"
    )
    shared = next(iter(near_dup_clusters))
    assert shared is not None

    # No distinct sentence (row ids 3..7) should join the near-dup cluster.
    for i in range(3, len(rows)):
        assert member_to_cluster.get(i) != shared, (
            f"distinct row {i} merged into the near-dup cluster"
        )
