"""Block analyzer for GoldenMatch — auto-suggests optimal blocking keys."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from itertools import combinations

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
    if df is not None:
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


def estimate_recall(
    df: pl.DataFrame,
    candidate: dict,
    matchkey_columns: list[str],
    sample_size: int = 1000,
) -> float:
    """Estimate recall for a blocking candidate using pair sampling.

    Takes a random sample, finds fuzzy-similar pairs via JaroWinkler on the
    highest-cardinality matchkey column, then checks what fraction would land
    in the same block under this candidate.
    """
    from goldenmatch.core import strsim

    n = len(df)
    if n < 2:
        return 0.0

    from goldenmatch.core.frame import to_frame

    actual_sample = min(sample_size, n)
    sample_frame = to_frame(df).sample(actual_sample, seed=42)

    # Pick highest-cardinality matchkey column
    valid_cols = [c for c in matchkey_columns if c in sample_frame.columns]
    if not valid_cols:
        return 0.0

    best_col = max(valid_cols, key=lambda c: sample_frame.column(c).n_unique())

    # Prepare string values for cdist
    values = (
        sample_frame.column(best_col)
        .cast_str()
        .fill_null("")
        .to_list()
    )
    values = [str(v).lower().strip() for v in values]

    # Compute pairwise JaroWinkler scores
    scores = strsim.pure_field_matrix(values, "jaro_winkler")

    # Find pairs above threshold (upper triangle only)
    threshold = 0.7
    pairs_above = set()
    for i in range(actual_sample):
        for j in range(i + 1, actual_sample):
            if scores[i][j] >= threshold:
                pairs_above.add((i, j))

    if not pairs_above:
        return 1.0  # No pairs to miss

    # Apply candidate transforms to sample and get block keys. The helper
    # returns a seam Frame (not a raw frame), so read the column through the
    # seam -- a raw `["__block_key__"]` subscript raises on the arrow-native
    # lane ('PolarsFrame'/'ArrowFrame' object is not subscriptable).
    if candidate.get("kind") == "token":
        # Token blocking is multi-key: a pair is retained when it shares AT
        # LEAST ONE surviving token, so membership is a set-intersection test
        # rather than a key equality. Built on the sample, so the DF cap is
        # resolved against the sample size the same way scoring did.
        index, _ = _token_index(sample_frame.native, candidate)
        row_tokens: list[set[str]] = [set() for _ in range(actual_sample)]
        for token, members in index.items():
            if len(members) < 2:
                continue
            for r in members:
                row_tokens[r].add(token)
        pairs_in_same_block = sum(
            1 for i, j in pairs_above if row_tokens[i] & row_tokens[j]
        )
        return pairs_in_same_block / len(pairs_above)

    sample_with_key = _apply_candidate_transforms(sample_frame.native, candidate)
    block_keys = to_frame(sample_with_key).column("__block_key__").to_list()

    # Check how many pairs share the same block key
    pairs_in_same_block = sum(
        1 for i, j in pairs_above
        if block_keys[i] is not None and block_keys[i] == block_keys[j]
    )

    return pairs_in_same_block / len(pairs_above)


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


def analyze_blocking(
    df: pl.DataFrame,
    matchkey_columns: list[str],
    sample_size: int = 1000,
    target_block_size: int = 5000,
) -> list[BlockingSuggestion]:
    """Analyze data and return ranked blocking strategy suggestions.

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

    # Sort by score descending to pick top candidates for recall estimation
    scored.sort(key=lambda x: x[1]["score"], reverse=True)

    # Estimate recall for top 10
    top_n = min(10, len(scored))
    for i in range(top_n):
        cand, metrics = scored[i]
        try:
            recall = estimate_recall(df, cand, matchkey_columns, sample_size=sample_size)
        except Exception:
            logger.warning(f"Recall estimation failed for {cand['description']}", exc_info=True)
            recall = 0.0
        metrics["estimated_recall"] = recall

    # For the rest, set recall to 0.0
    for i in range(top_n, len(scored)):
        scored[i][1]["estimated_recall"] = 0.0

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

    # #2488: rank the MEASURED candidates by score x recall.
    #
    # `estimated_recall` was computed here and then thrown away -- the only
    # thing multiplying the score was `recall_bonus`, which is
    # `check_coverage`'s field-membership flag (are the key's columns matchkey
    # columns?) and has nothing to do with how many true pairs the key retains.
    # So the analyzer measured recall, logged it, and ranked as if it hadn't.
    # On Amazon-Google every candidate estimates 0.05-0.07 recall and the top
    # one was picked purely on selectivity.
    #
    # Two tiers, deliberately: recall is only MEASURED for the top `top_n`, so
    # multiplying it into every score would rank an unmeasured candidate
    # (placeholder 0.0) as if it were known-useless, and would push measured
    # candidates below unmeasured ones whenever recall < 1. `suggestions` is
    # built in `scored` order and `scored` is sorted by raw score, so the first
    # `top_n` are exactly the measured ones. They keep that block and are
    # reordered within it; the unmeasured tail stays behind them in score order,
    # exactly where it already was.
    measured, unmeasured = suggestions[:top_n], suggestions[top_n:]
    measured.sort(key=lambda s: (s.score * s.estimated_recall, s.score), reverse=True)
    suggestions = measured + unmeasured

    if suggestions and suggestions[0].estimated_recall < _LOW_RECALL_WARN:
        # Not a hard reject. On a frame where EVERY candidate is below the floor
        # -- which is the Amazon-Google case -- rejecting them all leaves
        # degenerate blocking, and one mega-block is worse than a poor key. The
        # honest move is to run and say so, loudly, rather than to report a
        # 5%-recall plan as a normal success.
        logger.warning(
            "Auto-suggest: best blocking candidate %r estimates only %.1f%% recall "
            "(coverage %.1f%%). Every candidate considered was below %.0f%%, so this "
            "is a low-recall blocking plan, not a tuned one -- expect most true "
            "matches to be missed. Provide an explicit blocking config if this "
            "dataset matters. See #2488.",
            suggestions[0].description,
            100.0 * suggestions[0].estimated_recall,
            100.0 * scored[0][1].get("coverage", 0.0),
            100.0 * _LOW_RECALL_WARN,
        )

    return suggestions
