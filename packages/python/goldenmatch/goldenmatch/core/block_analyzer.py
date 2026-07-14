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


def generate_candidates(matchkey_columns: list[str]) -> list[dict]:
    """Generate blocking key candidates from matchkey columns.

    Produces single-column candidates based on column type heuristics,
    plus compound candidates combining pairs of single-column candidates.
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

    # Check columns exist
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

    if df_valid.height == 0:
        return {
            "group_count": 0,
            "max_group_size": 0,
            "mean_group_size": 0.0,
            "std_group_size": 0.0,
            "total_comparisons": 0,
            "score": 0.0,
        }

    # Group sizes via the seam (column named "len" per group_len contract).
    stats = df_valid.group_len(["__block_key__"])
    sizes = stats.column("len")

    group_count = stats.height
    total_records = df_valid.height

    if group_count == 0:
        return {
            "group_count": 0,
            "max_group_size": 0,
            "mean_group_size": 0.0,
            "std_group_size": 0.0,
            "total_comparisons": 0,
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

    # Score formula
    if mean_group_size == 0:
        score = 0.0
    else:
        score = (
            (group_count / total_records)
            * (1 / (1 + max_group_size / target_block_size))
            * (1 / (1 + std_group_size / mean_group_size))
        )

    return {
        "group_count": group_count,
        "max_group_size": int(max_group_size),
        "mean_group_size": float(mean_group_size),
        "std_group_size": float(std_group_size),
        "total_comparisons": total_comparisons,
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
    from rapidfuzz.distance import JaroWinkler
    from rapidfuzz.process import cdist

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
    scores = cdist(values, values, scorer=JaroWinkler.similarity, workers=1)

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
    candidates = generate_candidates(matchkey_columns)

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

    # Sort by final score descending
    suggestions.sort(key=lambda s: s.score, reverse=True)

    return suggestions
