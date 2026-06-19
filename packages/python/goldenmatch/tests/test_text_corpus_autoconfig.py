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


def _weighted_matchkey():
    """A weighted matchkey carrying a text field the blocking-swap rules can
    re-key onto (``rule_blocking_key_swap`` walks the first weighted matchkey
    for a text/name field)."""
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    return MatchkeyConfig(
        name="primary",
        type="weighted",
        threshold=0.85,
        fields=[
            MatchkeyField(
                field="name", transforms=["lowercase"],
                scorer="ensemble", weight=1.0,
            ),
        ],
    )


def _static_config():
    """Normal static blocking on ``city`` (NOT the matchkey's ``name`` field,
    so the swap rules propose a genuine change) with a weighted matchkey."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
    )

    return GoldenMatchConfig(
        matchkeys=[_weighted_matchkey()],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
        ),
    )


def _lsh_config():
    """Same matchkey, but lsh blocking (the near-dup-locked strategy). An lsh
    BlockingConfig must NOT carry keys/passes, so blocking is the only
    difference from ``_static_config``."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        GoldenMatchConfig,
        LSHKeyConfig,
    )

    return GoldenMatchConfig(
        matchkeys=[_weighted_matchkey()],
        blocking=BlockingConfig(
            strategy="lsh",
            lsh=LSHKeyConfig(column="body", threshold=0.5),
        ),
    )


def _swap_prone_profile():
    """A ComplexityProfile shaped to ACTUALLY trip both
    ``rule_blocking_key_swap`` and ``rule_uniform_heavy_blocking``.

    Trigger conditions matched precisely against the rule bodies:

    - ``rule_blocking_key_swap``: ``candidates_compared > 0`` AND
      ``mass_above_threshold == 0.0``; matchkey carries a text field
      (``name`` typed "text" here). History supplies the prior decision the
      rule requires (it's the iter-1+ fallback).
    - ``rule_uniform_heavy_blocking``: ``avg_block = n_rows/n_blocks >= 30``
      (10_000/100 = 100), ``candidates_compared >= n_rows``,
      ``mass_above_threshold >= 0.5``, ``mass_in_borderline >= 0.5``, and a
      high-cardinality identity-bearing column (``name``, type "name",
      cardinality 0.8, not in blocking) to swap onto.

    Both gates fire on these values, so static-vs-lsh isolates the guard.
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
            n_cols=2,
            column_types={"name": "name", "city": "geo"},
            cardinality_ratio={"name": 0.8, "city": 0.05},
            null_rate={"name": 0.0, "city": 0.0},
        ),
        blocking=BlockingProfile(
            n_blocks=100,
            block_sizes_p50=100,
            block_sizes_p99=120,
            block_sizes_max=150,
            reduction_ratio=0.5,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=50_000,
            # >= n_rows so uniform_heavy treats blocking as over-coarse
            candidates_compared=50_000,
            # 0.0 so blocking_key_swap fires; >= 0.5 satisfies uniform_heavy
            mass_above_threshold=0.0,
            mass_in_borderline=0.6,
        ),
    )


def _swap_prone_profile_uniform():
    """Variant for ``rule_uniform_heavy_blocking`` only: it needs
    ``mass_above_threshold >= 0.5`` (the "everything matches" signature),
    which is mutually exclusive with ``rule_blocking_key_swap``'s
    ``mass_above_threshold == 0.0``. Same shape otherwise."""
    import dataclasses

    base = _swap_prone_profile()
    return dataclasses.replace(
        base,
        scoring=dataclasses.replace(
            base.scoring, mass_above_threshold=0.6, mass_in_borderline=0.6
        ),
    )


def _history_with_prior_decision():
    """A RunHistory with one prior decision — ``rule_blocking_key_swap`` is the
    iter-1+ fallback and bails when ``history.decisions`` is empty."""
    from goldenmatch.core.autoconfig_history import (
        HistoryEntry,
        PolicyDecision,
        RunHistory,
    )

    h = RunHistory()
    h.entries.append(
        HistoryEntry(
            iteration=0,
            config=_static_config(),
            profile=_swap_prone_profile(),
            decision=PolicyDecision(
                rule_name="rule_blocking_field_null_heavy",
                rationale="prior",
                config_diff={},
            ),
            error=None,
            wall_clock_ms=10,
        )
    )
    return h


def test_blocking_key_swap_fires_static_but_guard_blocks_lsh():
    """rule_blocking_key_swap proposes a swap on a static config that trips it,
    and the near-dup guard suppresses the identical proposal under lsh.

    This is the teeth: removing ``if _near_dup_locked(current): return None``
    flips the lsh assertion from None to a proposal.
    """
    from goldenmatch.core.autoconfig_rules import rule_blocking_key_swap

    profile = _swap_prone_profile()
    history = _history_with_prior_decision()

    # static: the profile genuinely trips the rule -> non-None proposal.
    static_out = rule_blocking_key_swap(profile, _static_config(), history)
    assert static_out is not None, "profile should trip rule_blocking_key_swap on static blocking"
    new_cfg, _decision = static_out
    assert new_cfg.blocking is not None
    assert new_cfg.blocking.strategy == "static"
    assert new_cfg.blocking.keys[0].fields == ["name"]

    # lsh: same profile + history, only the guard differs -> None.
    lsh_out = rule_blocking_key_swap(profile, _lsh_config(), history)
    assert lsh_out is None, "near-dup guard must suppress rule_blocking_key_swap under lsh"


def test_uniform_heavy_blocking_fires_static_but_guard_blocks_lsh():
    """rule_uniform_heavy_blocking proposes a swap on a static config that trips
    it, and the near-dup guard suppresses the identical proposal under lsh."""
    from goldenmatch.core.autoconfig_history import RunHistory
    from goldenmatch.core.autoconfig_rules import rule_uniform_heavy_blocking

    profile = _swap_prone_profile_uniform()
    history = RunHistory()

    # static: the profile genuinely trips the rule -> non-None proposal.
    static_out = rule_uniform_heavy_blocking(profile, _static_config(), history)
    assert static_out is not None, "profile should trip rule_uniform_heavy_blocking on static blocking"
    new_cfg, _decision = static_out
    assert new_cfg.blocking is not None
    assert new_cfg.blocking.strategy == "static"
    assert new_cfg.blocking.keys[0].fields == ["name"]

    # lsh: same profile + history, only the guard differs -> None.
    lsh_out = rule_uniform_heavy_blocking(profile, _lsh_config(), history)
    assert lsh_out is None, "near-dup guard must suppress rule_uniform_heavy_blocking under lsh"


def test_guarded_rules_return_none_when_lsh_locked():
    """Breadth check: every guarded rule returns None on an lsh config.

    Kept as a regression net across all nine guarded rules. The per-rule
    teeth (static fires, lsh blocked) live in the two tests above —
    several rules here return None for reasons unrelated to the guard on
    this profile, so this test alone does NOT prove the guard works.
    """
    from goldenmatch.core import autoconfig_rules as ar

    cfg = _lsh_config()
    profile = _swap_prone_profile()
    history = _history_with_prior_decision()

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
    """End-to-end: a single text column dedupes near-dups with NO config.

    Exercises A1 (text-corpus detection) + A2 (lsh routing) + A3 (the
    controller leaving lsh in place) through the public ``dedupe_df`` entry
    point.
    """
    import goldenmatch
    from goldenmatch.core.autoconfig import auto_configure_df

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
    df = pl.DataFrame({"text": rows})

    # The zero-config path must pick lsh blocking for this corpus.
    cfg = auto_configure_df(df, confidence_required=False)
    assert cfg.blocking is not None
    assert cfg.blocking.strategy == "lsh"

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
