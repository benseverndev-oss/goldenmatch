"""Block analyzer for GoldenMatch — auto-suggests optimal blocking keys."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from goldenmatch._polars_lazy import pl

logger = logging.getLogger(__name__)


# ── Column type detection ───────────────────────────────────────────────────


def detect_column_type(column_name: str) -> str:
    """Heuristic name-based type detection for a column.

    Returns one of: "name", "zip", "email", "phone", "state", "generic".
    """
    lower = column_name.lower()

    if re.search(r"(name|fname|lname)", lower):
        return "name"
    if re.search(r"(zip|postal)", lower):
        return "zip"
    if re.search(r"(email|mail)", lower):
        return "email"
    if re.search(r"(phone|tel|mobile)", lower):
        return "phone"
    if re.search(r"(state)", lower):
        return "state"
    return "generic"


# ── Candidate generation ────────────────────────────────────────────────────


def _single_column_candidates(column: str) -> list[dict]:
    """Generate single-column blocking key candidates based on detected type."""
    col_type = detect_column_type(column)
    candidates = []

    if col_type == "name":
        for length in (3, 4, 5):
            candidates.append({
                "key_fields": [column],
                "transforms": ["lowercase", f"substring:0:{length}"],
                "description": f"{column}[:{length}]",
            })
        candidates.append({
            "key_fields": [column],
            "transforms": ["lowercase", "soundex"],
            "description": f"soundex({column})",
        })
    elif col_type == "zip":
        for length in (3, 5):
            candidates.append({
                "key_fields": [column],
                "transforms": [f"substring:0:{length}"],
                "description": f"{column}[:{length}]",
            })
        candidates.append({
            "key_fields": [column],
            "transforms": [],
            "description": column,
        })
    elif col_type == "state":
        candidates.append({
            "key_fields": [column],
            "transforms": [],
            "description": column,
        })
    elif col_type == "email":
        candidates.append({
            "key_fields": [column],
            "transforms": ["lowercase", "substring:0:5"],
            "description": f"{column}[:5]",
        })
    elif col_type == "phone":
        for length in (3, 6):
            candidates.append({
                "key_fields": [column],
                "transforms": [f"substring:0:{length}"],
                "description": f"{column}[:{length}]",
            })
    else:  # generic
        for length in (3, 4, 5):
            candidates.append({
                "key_fields": [column],
                "transforms": [f"substring:0:{length}"],
                "description": f"{column}[:{length}]",
            })

    return candidates


#: A column averaging at least this many tokens per non-empty value is treated
#: as free text and gets token-blocking candidates (#2488). Names ("John Smith")
#: and addresses sit at 2-4, product titles and descriptions at 8-30. The bar is
#: deliberately above the name range: exact prefix/soundex keys work well on
#: names, and the point is to add a shape for the text they do NOT work on.
_FREE_TEXT_MIN_MEAN_TOKENS = 5.0

#: DF caps offered as token candidates. Spread so the scorer can trade recall
#: against cost on the actual frame instead of one hard-coded guess; on
#: Amazon-Google these span 87.6% recall / 52,777 pairs to 99.0% / 387,183.
_TOKEN_DF_CAPS = (10, 25, 50, 100, 200)

#: Candidate pairs per row a token plan may propose before the score starts
#: discounting it. Free-text pairs are EXPENSIVE to score -- on Amazon-Google a
#: 115,650-pair plan cost 424s on 4589 rows, ~3.6ms/pair -- so an unbounded
#: recall-maximising plan does not finish inside the auto-config time budget and
#: the run falls back to degenerate blocking. Expressed per row so it tracks the
#: frame rather than pinning one absolute number.
_TOKEN_PAIR_BUDGET_PER_ROW = 10


def _token_candidates_enabled() -> bool:
    """Whether auto-suggest may propose token blocking. Default ON (#2717).

    It shipped default-OFF under #2488 on the reasoning below, which a later
    measurement refuted -- both are kept so the flip is auditable.

    The strategy itself is sound and measured -- on Amazon-Google it reaches
    98.2% blocking recall against the committed key's 7.15%. What is NOT yet
    sound is committing it from auto-suggest. Measured end-to-end on that
    benchmark, three runs in one environment:

        clean main          iter0 430.7s  failing_subprofile=scoring   F1 0.1014
        token candidates    iter0 424.1s  failing_subprofile=blocking  F1 0.0000
        + pair-cost term    iter0 429.3s  failing_subprofile=blocking  F1 0.0000

    So the auto-config time-budget blowout at iteration 0 is PRE-EXISTING on
    main (~430s in all three, including baseline) and is not caused by this
    work -- but the F1 collapse is: the baseline reproduces 0.1014 exactly, and
    turning token candidates on takes it to zero. The auto-config subprofile
    that fails flips from `scoring` to `blocking`, `build_token_blocks` never
    logs, and the committed RED config yields no candidate pairs at all.

    **DIAGNOSED 2026-08-22, and the rationale above does not survive it (#2717).**
    There was no integration gap. `build_token_blocks` never logged because
    nothing ever set `strategy="token"`: `auto_suggest` defaults to False so
    `_run_auto_suggest` returns on its first line, and its token branch sits
    behind `if not config.blocking.keys`, which auto-config has already
    populated. The strategy was UNREACHABLE, not broken -- absence of logging
    was read as evidence of failure when it was evidence of never running.

    Invoked directly on Amazon-Google it works, measured against
    `Amzon_GoogleProducts_perfectMapping.csv`:

        df<=50 : 2,718 blocks    94,938 candidate pairs   blocking recall 0.953
        df<=100: 2,754 blocks   163,499 candidate pairs   blocking recall 0.982

    against the committed key's 0.041. So "a plan that finds no pairs" describes
    a config token blocking never produced, and the F1 0.0000 in that experiment
    came from whatever config WAS committed.

    Default flipped ON. `defer_free_text_blocking_to_analyzer` (autoconfig.py)
    only routes FREE-TEXT keys here -- names and addresses sit below the
    token-count bar and never reach it -- so this changes the plan exactly where
    a prefix key is documented as near-useless. The env var still forces it
    either way; read at call time (not import) so it stays settable per-test.
    """
    import os  # noqa: PLC0415

    raw = os.environ.get("GOLDENMATCH_TOKEN_BLOCKING", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return True


def _mean_token_count(df, column: str, sample: int = 2000) -> float:
    """Mean whitespace-token count over non-empty values of ``column``."""
    from goldenmatch.core.frame import to_frame

    f = to_frame(df)
    if column not in f.columns:
        return 0.0
    if f.height > sample:
        f = f.sample(sample, seed=42)
    vals = f.column(column).cast_str().fill_null("").to_list()
    counts = [len(str(v).split()) for v in vals if str(v).strip()]
    return (sum(counts) / len(counts)) if counts else 0.0


def free_text_columns(df, matchkey_columns: list[str]) -> list[str]:
    """Matchkey columns whose values are free text rather than short identifiers.

    Decided on the DATA, not the column name. The name heuristic in
    `detect_column_type` sends `title` and `description` to "generic", which
    produces only `[:3]/[:4]/[:5]` prefix keys -- and a prefix key on a product
    title is a near-useless block.
    """
    return [
        c for c in matchkey_columns
        if _mean_token_count(df, c) >= _FREE_TEXT_MIN_MEAN_TOKENS
    ]


def _token_candidates(column: str) -> list[dict]:
    """Token-blocking candidates for one free-text column (#2488)."""
    return [
        {
            "key_fields": [column],
            "transforms": [],
            "kind": "token",
            "token": {"column": column, "max_df": cap},
            "description": f"tokens({column}, df<={cap})",
        }
        for cap in _TOKEN_DF_CAPS
    ]


def generate_candidates(matchkey_columns: list[str], df=None) -> list[dict]:
    """Generate blocking key candidates from matchkey columns.

    Produces single-column candidates based on column type heuristics,
    plus compound candidates combining pairs of single-column candidates.

    When ``df`` is supplied, free-text columns additionally get token-blocking
    candidates (#2488). They need the data because "is this free text" is a
    property of the values, not of the column name. ``df`` is optional so the
    existing name-only callers keep working unchanged -- they simply get no
    token candidates.
    """
    # Single-column candidates
    single_candidates: dict[str, list[dict]] = {}
    all_candidates: list[dict] = []

    for col in matchkey_columns:
        col_candidates = _single_column_candidates(col)
        single_candidates[col] = col_candidates
        all_candidates.extend(col_candidates)

    # Compound candidates: combine pairs of columns (max 2)
    if len(matchkey_columns) >= 2:
        for col_a, col_b in combinations(matchkey_columns, 2):
            for cand_a in single_candidates[col_a]:
                for cand_b in single_candidates[col_b]:
                    all_candidates.append({
                        "key_fields": [col_a, col_b],
                        "transforms": [cand_a["transforms"], cand_b["transforms"]],
                        "description": f"{cand_a['description']} + {cand_b['description']}",
                    })

    # Token candidates for free text. NOT compounded with the exact keys: a
    # token block ANDed with a prefix key re-imposes the single-derived-value
    # agreement that token blocking exists to avoid.
    #
    # OPT-IN (default OFF) pending an unresolved integration bug -- see
    # `_token_candidates_enabled`.
    if df is not None and _token_candidates_enabled():
        for col in free_text_columns(df, matchkey_columns):
            all_candidates.extend(_token_candidates(col))

    return all_candidates


# ── Scoring ──────────────────────────────────────────────────────────────────


def _apply_candidate_transforms(df, candidate: dict):
    """Apply a candidate's transforms and add __block_key__ column.

    A3 (arrow-native endgame): seam-driven both lanes. Per-field derivation
    is ``derive_transformed_column`` (cast-then-chain -- the D5c-probed twin
    of the old ``cast(Utf8).map_elements(apply_transforms)`` expr). Compound
    keys join "||" in Python with the old ``concat_str`` NULL PROPAGATION
    (any null field -> null key); the analyzer runs on samples, so the list
    round-trip is size-bounded. Returns a seam Frame.
    """
    from goldenmatch.core.frame import PolarsFrame, column_from_values, to_frame

    f = to_frame(df)
    key_fields = candidate["key_fields"]
    transforms = candidate["transforms"]

    if len(key_fields) == 1:
        col = key_fields[0]
        return f.with_column(
            "__block_key__",
            f.derive_transformed_column(col, list(transforms or [])),
        )
    parts = []
    for i, col in enumerate(key_fields):
        tfms = transforms[i] if i < len(transforms) else []
        parts.append(f.derive_transformed_column(col, list(tfms)).to_list())
    joined = [
        "||".join(vals) if all(v is not None for v in vals) else None
        for vals in zip(*parts)
    ]
    backend = "polars" if isinstance(f, PolarsFrame) else "arrow"
    return f.with_column(
        "__block_key__", column_from_values(joined, "utf8", backend=backend)
    )


def _token_index(df, candidate: dict) -> tuple[dict[str, list[int]], int]:
    """The token->rows index for a token candidate, and the row count.

    Delegates to ``TokenBlocker`` -- the analyzer must score exactly what the
    blocker will build, or the ranking is measuring a different scheme than the
    one that runs.
    """
    from goldenmatch.config.schemas import TokenBlockingConfig
    from goldenmatch.core.frame import to_frame
    from goldenmatch.core.token_blocker import TokenBlocker

    f = to_frame(df)
    spec = candidate["token"]
    cfg = TokenBlockingConfig(**spec)
    texts = [str(v) for v in f.column(cfg.column).cast_str().fill_null("").to_list()]
    return TokenBlocker.from_config(cfg, f.height).index(texts), f.height


def _score_token_candidate(df, candidate: dict, target_block_size: int) -> dict:
    """Score a token candidate on the same axes as an exact-key candidate.

    The stats mean the same things, but ``coverage`` is now "fraction of records
    landing in at least one block" rather than "fraction producing a key", and a
    record contributes to several groups.
    """
    index, n_total = _token_index(df, candidate)
    sizes = [len(m) for m in index.values() if len(m) >= 2]
    if not sizes or n_total == 0:
        return {
            "group_count": 0, "max_group_size": 0, "mean_group_size": 0.0,
            "std_group_size": 0.0, "total_comparisons": 0, "coverage": 0.0,
            "score": 0.0,
        }

    blocked = {r for m in index.values() if len(m) >= 2 for r in m}
    coverage = len(blocked) / n_total

    group_count = len(sizes)
    max_group_size = max(sizes)
    mean_group_size = sum(sizes) / group_count
    if group_count > 1:
        var = sum((s - mean_group_size) ** 2 for s in sizes) / (group_count - 1)
        std_group_size = var ** 0.5
    else:
        std_group_size = 0.0
    total_comparisons = sum(s * (s - 1) // 2 for s in sizes)

    # Same formula as the exact-key path, with two changes.
    #
    # (1) The first term there is `group_count / n_total` -- selectivity, "how
    # finely does this key split the frame" -- which relies on one block per
    # record, so more groups means smaller groups. Token blocking breaks that:
    # a record joins many blocks, so group_count can exceed n_total and the term
    # stops being a fraction (Amazon-Google: 2750 blocks over 4589 rows, but
    # ~16k memberships). `coverage` is the honest analogue -- the share of the
    # frame this key can actually place -- and it keeps the term in [0, 1] and
    # comparable across both candidate shapes, which is the only reason the two
    # can be ranked against each other at all.
    #
    # (2) A TOTAL-pair term, which the exact-key formula does not have and does
    # not need. There, `max_group_size` stands in for cost, because one block
    # per record means the biggest block dominates the pair count. Under
    # multi-key blocking that correlation breaks completely: `tokens(title,
    # df<=50)` has a max block of 50 -- tiny -- and 115,650 total pairs, 24x the
    # exact key it displaced. Ranking on max_group_size alone therefore scored
    # it as cheap, the pipeline then spent 424s scoring those pairs on a 4589-row
    # sample, blew the auto-config time budget at iteration 0, and the run ended
    # on the degenerate RED config with F1 0.0 -- WORSE than the 7%-recall key it
    # replaced. Recall is worthless if the plan carrying it never finishes.
    pair_budget = max(1, _TOKEN_PAIR_BUDGET_PER_ROW * n_total)
    score = (
        coverage
        * (1 / (1 + max_group_size / target_block_size))
        * (1 / (1 + std_group_size / mean_group_size))
        * (1 / (1 + total_comparisons / pair_budget))
    ) if mean_group_size else 0.0

    return {
        "group_count": group_count,
        "max_group_size": int(max_group_size),
        "mean_group_size": float(mean_group_size),
        "std_group_size": float(std_group_size),
        "total_comparisons": total_comparisons,
        "coverage": float(coverage),
        "score": float(score),
    }


def score_candidate(
    df: pl.DataFrame,
    candidate: dict,
    target_block_size: int = 5000,
) -> dict:
    """Score a blocking key candidate on the given data.

    Returns a dict with group_count, max_group_size, mean_group_size,
    std_group_size, total_comparisons, and score.
    """
    from goldenmatch.core.frame import to_frame as _tf_a3

    # Check columns exist. Token candidates carry their column in `key_fields`
    # too, so they are guarded here as well -- the branch below must stay AFTER
    # this, or a token candidate naming a missing column raises out of the
    # scoring loop instead of scoring 0 like every other candidate.
    _cols_a3 = _tf_a3(df).columns
    for col in candidate["key_fields"]:
        if col not in _cols_a3:
            return {
                "group_count": 0,
                "max_group_size": 0,
                "mean_group_size": 0.0,
                "std_group_size": 0.0,
                "total_comparisons": 0,
                "score": 0.0,
            }

    if candidate.get("kind") == "token":
        return _score_token_candidate(df, candidate, target_block_size)

    df_with_key = _apply_candidate_transforms(df, candidate)
    # ^ stays a polars expression chain: this MCP tool is unreachable on the
    # arrow backend until W5 lifts the ingest shim; the REDUCTIONS below run
    # through the seam (W3d).


    _keys = df_with_key.column("__block_key__").to_list()
    from goldenmatch.core.frame import PolarsFrame as _PF
    from goldenmatch.core.frame import column_from_values as _cfv

    _backend = "polars" if isinstance(df_with_key, _PF) else "arrow"
    df_valid = df_with_key.filter_mask(
        _cfv([k is not None for k in _keys], "bool", backend=_backend)
    )

    # #2488: records this key CANNOT key. A compound key null-propagates (see
    # `_apply_candidate_transforms`), so one sparse component nulls the whole
    # key -- e.g. an Amazon-Google `manufacturer` component is 100% populated on
    # one source and 7.2% on the other, nulling 65% of the frame. Those records
    # are unblockable by this key, and a pair needs BOTH of its members blocked,
    # so coverage^2 is a hard ceiling on the recall the key can ever achieve.
    n_total = df_with_key.height
    coverage = (df_valid.height / n_total) if n_total else 0.0

    if df_valid.height == 0:
        return {
            "group_count": 0,
            "max_group_size": 0,
            "mean_group_size": 0.0,
            "std_group_size": 0.0,
            "total_comparisons": 0,
            "coverage": 0.0,
            "score": 0.0,
        }

    # Group sizes via the seam (column named "len" per group_len contract).
    stats = df_valid.group_len(["__block_key__"])
    sizes = stats.column("len")

    group_count = stats.height

    if group_count == 0:
        return {
            "group_count": 0,
            "max_group_size": 0,
            "mean_group_size": 0.0,
            "std_group_size": 0.0,
            "total_comparisons": 0,
            "coverage": coverage,
            "score": 0.0,
        }

    max_group_size = sizes.max()
    mean_group_size = sizes.mean()
    std_group_size = sizes.std() if group_count > 1 else 0.0
    if std_group_size is None:
        std_group_size = 0.0

    # total_comparisons = sum(n*(n-1)/2), Python fold over the seam sizes
    # (same values as the old polars expression).
    total_comparisons = sum(k * (k - 1) // 2 for k in sizes.to_list())

    # Score formula. The first term is selectivity -- "how finely does this key
    # split the data".
    #
    # #2488: that term used to divide by `df_valid.height`, the records that
    # PRODUCED a key, which silently normalises coverage away: a key that can
    # only key 35% of the frame was scored as though the frame were just that
    # 35%, so it looked maximally selective AND cheap (`total_comparisons` is
    # likewise summed over survivors only). Dividing by the full height instead
    # caps the term at `coverage`, which is the honest bound -- a key cannot be
    # selective about a record it cannot key.
    #
    # This is a NO-OP for a fully-covered key (df_valid.height == n_total), which
    # is the overwhelming majority, so it only moves the ranking where coverage
    # is genuinely partial.
    if mean_group_size == 0:
        score = 0.0
    else:
        score = (
            (group_count / n_total)
            * (1 / (1 + max_group_size / target_block_size))
            * (1 / (1 + std_group_size / mean_group_size))
        )

    return {
        "group_count": group_count,
        "max_group_size": int(max_group_size),
        "mean_group_size": float(mean_group_size),
        "std_group_size": float(std_group_size),
        "total_comparisons": total_comparisons,
        "coverage": float(coverage),
        "score": float(score),
    }


# ── Coverage check ───────────────────────────────────────────────────────────


def check_coverage(candidate: dict, matchkey_columns: list[str]) -> bool:
    """Check if all key_fields in the candidate are in matchkey_columns."""
    return all(f in matchkey_columns for f in candidate["key_fields"])


# ── Recall estimation ────────────────────────────────────────────────────────


def _target_pairs_from_matchkey(sample_frame: Any, matchkey: Any) -> set[tuple[int, int]]:
    """Pairs the CONFIGURED matchkey would emit above its threshold (#2513).

    This is the right denominator for blocking recall. Blocking's job is not to
    find true duplicates -- that is the scorer's job -- it is to avoid losing
    pairs the scorer would have matched. So "what fraction of the pairs the
    matchkey emits does this candidate retain?" is the question, and anything
    the scorer would reject anyway is not blocking's failure to retain.

    Reuses ``find_fuzzy_matches``, the one authoritative pair scorer, rather
    than rebuilding a second scoring path here (which would also have to
    reimplement composite scorers like ``ensemble`` that the NxN matrix helper
    does not expose).

    Row ids are positional into ``sample_frame`` so callers can index parallel
    per-row lists directly.
    """
    from goldenmatch.core.frame import to_frame
    from goldenmatch.core.scorer import find_fuzzy_matches

    # Row ids through the seam, not `native.with_columns(pl.Series(...))`. That
    # was a POLARS call, and on the arrow-native lane `native` is a `pa.Table`,
    # so it raised `AttributeError: 'pyarrow.lib.Table' object has no attribute
    # 'with_columns'`. `_build_recall_target` catches that and drops to
    # `_target_pairs_from_similarity` -- the proxy its own docstring calls WEAK
    # (Amazon-Google: 2,355 sample pairs, 35 true, 1.5% precision). So on the
    # DEFAULT lane every recall estimate came from the weak proxy, and nothing
    # failed loudly: the warning names a matchkey, not a lane.
    #
    # That proxy is not merely noisy, it is ANTI-correlated with true retention
    # -- on DBLP-ACM it rates `venue[:3]` (5.9% of true pairs) above `title[:5]`
    # (98.2%) -- so anything ranking on its numbers is ranking on noise.
    #
    # `find_fuzzy_matches` accepts a `pa.Table`: its body reads through the
    # `_to_frame_d5` seam and branches on `to_dicts`/`to_pylist`. Only the type
    # annotation said polars. Verified byte-identical on the real DBLP-ACM
    # matchkey incl. the composite `ensemble` scorer -- same 111 pairs, max score
    # delta 0.0 -- and arrow is FASTER (0.37s vs 0.60s at n=1000). No polars, and
    # the vectorised rapidfuzz path is kept: a `score_pair` loop, the obvious
    # polars-free alternative, measured 44.1s for the same work.
    #
    # DROP before ensure: this function's contract is that ids are POSITIONAL
    # into `sample_frame` so callers can index parallel per-row lists.
    # `ensure_row_ids` REUSES an existing `__row_id__` (#844), which upstream may
    # already carry with a different numbering.
    frame = to_frame(sample_frame.native)
    if "__row_id__" in frame.columns:
        frame = frame.drop(["__row_id__"])
    emitted = find_fuzzy_matches(frame.ensure_row_ids("__row_id__").native, matchkey)
    return {(min(a, b), max(a, b)) for a, b, _ in emitted}


def _target_pairs_from_similarity(
    sample_frame: Any, matchkey_columns: list[str]
) -> set[tuple[int, int]]:
    """Fallback denominator when no weighted matchkey is available.

    Character similarity on the highest-cardinality matchkey column. Retained
    only for callers that have column names but no matchkey (CLI / MCP / A2A
    entry points). It is a WEAK proxy -- on Amazon-Google, JW >= 0.7 selects
    2,355 sample pairs of which 35 are true matches (1.5% precision), so a
    candidate is judged largely on how many NON-matches it co-blocks. Prefer
    passing a matchkey; see `_target_pairs_from_matchkey`.
    """
    from goldenmatch.core import strsim

    valid_cols = [c for c in matchkey_columns if c in sample_frame.columns]
    if not valid_cols:
        return set()
    best_col = max(valid_cols, key=lambda c: sample_frame.column(c).n_unique())
    values = [
        str(v).lower().strip()
        for v in sample_frame.column(best_col).cast_str().fill_null("").to_list()
    ]
    scores = strsim.pure_field_matrix(values, "jaro_winkler")
    height = sample_frame.height
    return {
        (i, j)
        for i in range(height)
        for j in range(i + 1, height)
        if scores[i][j] >= 0.7
    }


def _build_recall_target(
    df: pl.DataFrame,
    matchkey_columns: list[str],
    sample_size: int,
    matchkey: Any = None,
) -> tuple[Any, set[tuple[int, int]]]:
    """The fixed sample and the pairs blocking must not lose, computed ONCE.

    Hoisted out of the per-candidate loop deliberately (#2513): the sample is
    seeded, so every candidate saw an identical population and the analyzer
    rebuilt it from scratch each time. On Amazon-Google that was ~19.6s of
    O(n^2) work repeated for all ten measured candidates -- essentially the
    whole 196s runtime of `analyze_blocking`.
    """
    from goldenmatch.core.frame import to_frame

    actual_sample = min(sample_size, len(df))
    sample_frame = to_frame(df).sample(actual_sample, seed=42)

    if matchkey is not None:
        try:
            return sample_frame, _target_pairs_from_matchkey(sample_frame, matchkey)
        except Exception:
            logger.warning(
                "Recall target: scoring the sample with matchkey %r failed; falling "
                "back to the character-similarity proxy",
                getattr(matchkey, "name", "?"), exc_info=True,
            )
    return sample_frame, _target_pairs_from_similarity(sample_frame, matchkey_columns)


def _retention(
    sample_frame: Any, candidate: dict, target_pairs: set[tuple[int, int]]
) -> float:
    """Fraction of ``target_pairs`` this candidate keeps in a shared block."""
    from goldenmatch.core.frame import to_frame

    if not target_pairs:
        return 1.0  # nothing to lose

    height = sample_frame.height
    if candidate.get("kind") == "token":
        # Token blocking is multi-key: a pair is retained when it shares AT
        # LEAST ONE surviving token, so membership is a set-intersection test
        # rather than a key equality.
        index, _ = _token_index(sample_frame.native, candidate)
        row_tokens: list[set[str]] = [set() for _ in range(height)]
        for token, members in index.items():
            if len(members) < 2:
                continue
            for r in members:
                row_tokens[r].add(token)
        kept = sum(1 for i, j in target_pairs if row_tokens[i] & row_tokens[j])
        return kept / len(target_pairs)

    # `_apply_candidate_transforms` returns a seam Frame (not a raw frame), so
    # read the column through the seam -- a raw `["__block_key__"]` subscript
    # raises on the arrow-native lane ('PolarsFrame' object is not subscriptable).
    sample_with_key = _apply_candidate_transforms(sample_frame.native, candidate)
    block_keys = to_frame(sample_with_key).column("__block_key__").to_list()
    kept = sum(
        1 for i, j in target_pairs
        if block_keys[i] is not None and block_keys[i] == block_keys[j]
    )
    return kept / len(target_pairs)


def estimate_recall(
    df: pl.DataFrame,
    candidate: dict,
    matchkey_columns: list[str],
    sample_size: int = 1000,
    matchkey: Any = None,
) -> float:
    """Estimate what fraction of matchable pairs a blocking candidate retains.

    Takes a seeded sample, builds the set of pairs blocking must not lose, then
    checks how many of them this candidate co-blocks.

    When ``matchkey`` is supplied the target set is the pairs that matchkey
    actually emits -- the correct denominator, since blocking is accountable
    for retaining what the scorer would match and nothing else. Without one it
    falls back to a character-similarity proxy that is substantially weaker;
    see `_target_pairs_from_similarity`.

    Single-candidate entry point. `analyze_blocking` builds the target once and
    calls `_retention` directly, so it does not pay for this per candidate.
    """
    if len(df) < 2:
        return 0.0
    sample_frame, target = _build_recall_target(
        df, matchkey_columns, sample_size, matchkey
    )
    if not target and not matchkey_columns:
        return 0.0
    return _retention(sample_frame, candidate, target)


# ── BlockingSuggestion ───────────────────────────────────────────────────────


@dataclass
class BlockingSuggestion:
    """A ranked blocking strategy suggestion."""

    keys: list[dict]
    group_count: int
    max_group_size: int
    mean_group_size: float
    total_comparisons: int
    estimated_recall: float
    score: float
    description: str


# ── Main analyzer ────────────────────────────────────────────────────────────


_SCORE_SAMPLE_THRESHOLD = 100_000
_SCORE_SAMPLE_SIZE = 100_000

#: Below this estimated recall, the chosen blocking plan is reported as
#: low-recall rather than committed silently (#2488). Deliberately generous --
#: the point is to catch a collapse (Amazon-Google estimates 0.05-0.07 across
#: every candidate), not to police a well-tuned plan that trades a little recall
#: for tractability.
_LOW_RECALL_WARN = 0.30
# The ranked pick is flagged when it retains less than this share of what the
# best-recall candidate in the same list retains. Compares two estimates measured
# against the SAME target population, so the estimator's own bias cancels -- which
# is exactly what `_LOW_RECALL_WARN` cannot do, since an absolute floor is compared
# against an estimate whose ceiling is the target's true-match fraction (#2540).
# 0.75 measured: DBLP-ACM's ratio is 0.395 (rank 1 at 0.235 vs `title[:3]` at
# 0.595) and fires; a plan within a quarter of the best available is not a
# trade-off worth interrupting anyone over.
_RECALL_TRADEOFF_RATIO = 0.75


def analyze_blocking(
    df: pl.DataFrame,
    matchkey_columns: list[str],
    sample_size: int = 1000,
    target_block_size: int = 5000,
    matchkey: Any = None,
) -> list[BlockingSuggestion]:
    """Analyze data and return ranked blocking strategy suggestions.

    ``matchkey`` (optional, #2513) is the weighted matchkey the pipeline will
    score with. Supplying it makes recall estimation measure the right thing --
    what fraction of the pairs that matchkey emits each candidate retains --
    instead of a character-similarity stand-in for "duplicate". Callers that
    only have column names may omit it and get the weaker proxy.

    Pipeline:
    1. Generate candidates from matchkey_columns
    2. Score each candidate
    3. Check coverage (demote non-covering ones)
    4. Estimate recall for top candidates (top 10 by score)
    5. Sort by score * recall_bonus
    6. Return ranked list
    """
    candidates = generate_candidates(matchkey_columns, df=df)

    # At scale, per-candidate scoring runs a Python-UDF `map_elements` over the
    # full df. With ~260 candidates that's a multi-GB, multi-minute hang at 5M
    # rows. Block-size distribution is shape-only; sample is sufficient.
    from goldenmatch.core.frame import to_frame as _tf_a3

    n_full = _tf_a3(df).height
    if n_full > _SCORE_SAMPLE_THRESHOLD:
        from goldenmatch.core.frame import to_frame

        score_df = to_frame(df).sample(_SCORE_SAMPLE_SIZE, seed=42).native
        logger.info(
            "analyze_blocking: sampling %d rows from %d for candidate scoring",
            _SCORE_SAMPLE_SIZE, n_full,
        )
    else:
        score_df = df

    # Score each candidate
    scored = []
    for cand in candidates:
        metrics = score_candidate(score_df, cand, target_block_size=target_block_size)
        if metrics["group_count"] == 0:
            continue
        scored.append((cand, metrics))

    if not scored:
        return []

    # Sort by score descending -- the tie-break for equal score*recall below.
    scored.sort(key=lambda x: x[1]["score"], reverse=True)

    # #2513: build the target pair set ONCE, then measure EVERY candidate
    # against it. Previously only the top 10 by score were measured and the
    # rest were assigned 0.0 as an "unmeasured" placeholder -- but 0.0 is also
    # a real recall value, and it was multiplied into the rank, so an unmeasured
    # candidate could never be selected however good it was. On Amazon-Google
    # that zeroed `tokens(title, df<=100)`, which had the HIGHEST true pair
    # recall of every candidate generated (98.2%).
    sample_frame, target_pairs = _build_recall_target(
        df, matchkey_columns, sample_size, matchkey
    )
    for cand, metrics in scored:
        try:
            metrics["estimated_recall"] = _retention(sample_frame, cand, target_pairs)
        except Exception:
            # Fail-open to 1.0, NOT 0.0: a measurement failure must not be
            # indistinguishable from a measured "retains nothing", which is
            # exactly the confusion the placeholder above caused. Leaving the
            # candidate ranked on `score` alone is the neutral outcome.
            logger.warning(
                "Recall estimation failed for %s; ranking it on score alone",
                cand["description"], exc_info=True,
            )
            metrics["estimated_recall"] = 1.0

    # Build suggestions with coverage-based ranking
    suggestions = []
    for cand, metrics in scored:
        covers = check_coverage(cand, matchkey_columns)
        recall_bonus = 1.0 if covers else 0.5
        adjusted_score = metrics["score"] * recall_bonus

        suggestions.append(BlockingSuggestion(
            keys=[cand],
            group_count=metrics["group_count"],
            max_group_size=metrics["max_group_size"],
            mean_group_size=metrics["mean_group_size"],
            total_comparisons=metrics["total_comparisons"],
            estimated_recall=metrics.get("estimated_recall", 0.0),
            score=adjusted_score,
            description=cand["description"],
        ))

    # #2488: rank by score x recall.
    #
    # `estimated_recall` was computed here and then thrown away -- the only
    # thing multiplying the score was `recall_bonus`, which is
    # `check_coverage`'s field-membership flag (are the key's columns matchkey
    # columns?) and has nothing to do with how many true pairs the key retains.
    # So the analyzer measured recall, logged it, and ranked as if it hadn't.
    #
    # #2513: this was a two-tier sort while recall was measured only for the top
    # 10 -- the tail carried a 0.0 placeholder that would have zeroed its rank.
    # Every candidate is measured now, so the tiers are gone and one sort covers
    # the list. `score` stays as the tie-break, and it still carries the
    # tractability half of the trade-off (a key that retains everything by
    # putting everything in one block scores badly on comparison count), so
    # ranking is not a race to maximise recall alone.
    suggestions.sort(key=lambda s: (s.score * s.estimated_recall, s.score), reverse=True)
    suggestions = _apply_recall_tradeoff_gate(suggestions)

    if suggestions:
        _warn_on_recall(suggestions, scored[0][1].get("coverage", 0.0))

    return suggestions


def _apply_recall_tradeoff_gate(suggestions: list) -> list:
    """Refuse a plan retaining < `_RECALL_TRADEOFF_RATIO` of the best available.

    `score * estimated_recall` lets a cheap plan outrank a much better one,
    because `score` carries comparison count and a small block set scores well.
    On DBLP-ACM `title[:5]+authors[:5]` (1,781 comparisons) outranked
    `title[:5]` (39,648) while retaining 0.4164 of TRUE pairs against 0.9820.

    **Only sound because `_target_pairs_from_matchkey` now works.** Against the
    character-similarity proxy the estimate is ANTI-correlated with truth
    (`venue[:3]` 0.2032 est / 0.0594 true vs `title[:5]` 0.0407 / 0.9820), and
    this gate over those numbers promotes `venue[:3]` -- taking DBLP-ACM from
    98.2% recall to 5.9% at 61x the comparisons. That was implemented, measured
    against ground truth, and reverted. Do not enable it if the recall target
    ever falls back to the proxy again.

    Conservative: ordering WITHIN the qualifying set is untouched, so cost still
    decides among plans that clear the bar; gated plans are demoted, not
    dropped; and if nothing clears the bar, or recall could not be estimated at
    all, the list is returned unchanged.
    """
    if not suggestions:
        return suggestions
    best_recall = max(s.estimated_recall for s in suggestions)
    if best_recall <= 0.0:
        return suggestions
    floor = best_recall * _RECALL_TRADEOFF_RATIO
    qualifying = [s for s in suggestions if s.estimated_recall >= floor]
    demoted = [s for s in suggestions if s.estimated_recall < floor]
    if not qualifying:
        return suggestions
    if qualifying[0] is not suggestions[0]:
        logger.info(
            "Auto-suggest: '%s' (recall %.4f) won on comparison count but keeps "
            "only %.0f%% of the best available; promoting '%s' (recall %.4f). "
            "See #2717.",
            suggestions[0].description, suggestions[0].estimated_recall,
            100.0 * suggestions[0].estimated_recall / best_recall,
            qualifying[0].description, qualifying[0].estimated_recall,
        )
    return qualifying + demoted


def _warn_on_recall(suggestions: list, coverage: float) -> None:
    """Surface a low-recall blocking plan, and any higher-recall plan it outranked.

    Two separate signals, because they mean different things and the absolute one
    alone was misleading (#2540):

    **Relative** -- the ranked pick retains materially less than the best candidate
    measured. `rank = score x estimated_recall` has no recall floor, so a key that
    drops most matchable pairs can win purely on comparison count, and nothing said
    so. Measured on DBLP-ACM: rank 1 is `title[:5] + authors[:5]` at 0.235 estimated
    recall and 1,667 comparisons, while `title[:3]` sits in the same list at 0.595
    and 117,348 -- 2.5x the retention traded away for 70x fewer comparisons,
    silently. This is the actionable one: the alternative is named, so the choice
    can be overridden.

    **Absolute** -- every candidate is under `_LOW_RECALL_WARN`. This one is a
    weaker claim than it used to make. `estimated_recall` is measured against the
    pairs the matchkey emits over an *unblocked* sample, which includes the
    scorer's false positives, so its ceiling is that population's true-match
    fraction rather than 1.0 (measured on DBLP-ACM: 40.5% true, so no candidate
    could exceed ~0.4 however good). The old text asserted "expect most true
    matches to be missed", which the estimate cannot support -- `title[:5]` scores
    0.037 there while genuinely retaining 98.2% of true pairs. It is a lower bound
    on true-match retention, and is phrased as one.
    """
    top = suggestions[0]
    best_recall = max(suggestions, key=lambda s: s.estimated_recall)

    if (
        best_recall.description != top.description
        and top.estimated_recall < _RECALL_TRADEOFF_RATIO * best_recall.estimated_recall
    ):
        logger.warning(
            "Auto-suggest: the chosen blocking plan %r retains an estimated %.1f%% "
            "of matchable pairs (%s comparisons), but candidate %r retains %.1f%% "
            "(%s comparisons). Ranking is score x recall with no recall floor, so "
            "the cheaper plan won on comparison count. If recall matters more than "
            "cost here, set that key explicitly. See #2540.",
            top.description, 100.0 * top.estimated_recall,
            f"{top.total_comparisons:,}",
            best_recall.description, 100.0 * best_recall.estimated_recall,
            f"{best_recall.total_comparisons:,}",
        )

    if top.estimated_recall < _LOW_RECALL_WARN:
        # Not a hard reject. On a frame where EVERY candidate is below the floor
        # -- which is the Amazon-Google case -- rejecting them all leaves
        # degenerate blocking, and one mega-block is worse than a poor key. The
        # honest move is to run and say so rather than report it as a clean success.
        logger.warning(
            "Auto-suggest: best blocking candidate %r estimates %.1f%% recall "
            "(coverage %.1f%%), below the %.0f%% floor. Treat this as a LOWER BOUND "
            "on true-match retention, not a measurement of it: the denominator is "
            "the pairs the matchkey emits over an unblocked sample, so it includes "
            "scorer false positives that blocking is right to drop, and its ceiling "
            "is that population's true-match fraction rather than 100%%. Blocking "
            "may still be weak here -- verify against known duplicates, or provide "
            "an explicit blocking config, before trusting the plan. See #2488, #2540.",
            top.description,
            100.0 * top.estimated_recall,
            100.0 * coverage,
            100.0 * _LOW_RECALL_WARN,
        )
