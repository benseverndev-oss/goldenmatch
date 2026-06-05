"""Incremental matching: match new records against an existing base dataset.

Shared core used by BOTH the CLI ``incremental`` command and the MCP
``incremental`` tool so the two never drift. Exact matchkeys run via a
Polars self-join (``find_exact_matches``); fuzzy matchkeys run via
per-record ``match_one``. New records get ``__row_id__`` offset above the
base max so the two populations never collide.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goldenmatch.config.schemas import GoldenMatchConfig


def run_incremental(
    base_file: str,
    new_file: str,
    config: GoldenMatchConfig,
    threshold: float | None = None,
) -> dict:
    """Match records in ``new_file`` against the existing ``base_file``.

    Returns a dict with the matched ``(new_row_id, base_row_id, score)``
    pairs plus summary counts. Only cross-source pairs (one new, one base)
    are returned; new-vs-new pairs are dropped.
    """
    import polars as pl

    from goldenmatch.core.autofix import auto_fix_dataframe
    from goldenmatch.core.ingest import load_file
    from goldenmatch.core.match_one import match_one
    from goldenmatch.core.matchkey import compute_matchkeys
    from goldenmatch.core.scorer import find_exact_matches
    from goldenmatch.core.standardize import apply_standardization

    matchkeys = config.get_matchkeys()
    if threshold is not None:
        for mk in matchkeys:
            if mk.threshold is not None:
                mk.threshold = threshold

    # Load base dataset, stamp row ids + source.
    base_df = load_file(base_file).collect()
    base_df = base_df.with_row_index("__row_id__").with_columns(
        pl.col("__row_id__").cast(pl.Int64),
        pl.lit("base").alias("__source__"),
    )
    base_df, _ = auto_fix_dataframe(base_df)

    # Load new records, offsetting row ids above the base max. with_row_index
    # numbers base rows 0..height-1, so the next free id is exactly height.
    new_df = load_file(new_file).collect()
    base_max_id = base_df.height
    new_df = new_df.with_row_index("__row_id__").with_columns(
        (pl.col("__row_id__").cast(pl.Int64) + base_max_id).alias("__row_id__"),
        pl.lit("new").alias("__source__"),
    )
    new_df, _ = auto_fix_dataframe(new_df)

    # Standardize + compute matchkeys on the combined frame.
    combined = pl.concat([base_df, new_df], how="diagonal")
    lf = combined.lazy()
    if config.standardization:
        lf = apply_standardization(lf, config.standardization)  # type: ignore[arg-type]
    for mk in matchkeys:
        lf = compute_matchkeys(lf, [mk])
    combined = lf.collect()

    all_matches: list[tuple[int, int, float]] = []
    new_ids = set(range(base_max_id, base_max_id + new_df.height))

    exact_mks = [mk for mk in matchkeys if mk.type == "exact"]
    fuzzy_mks = [mk for mk in matchkeys if mk.type != "exact"]

    # Exact matchkeys via Polars join (match_one doesn't support exact).
    for mk in exact_mks:
        mk_col = f"__mk_{mk.name}__"
        if mk_col not in combined.columns:
            continue
        for a, b, score in find_exact_matches(combined.lazy(), mk):
            # Keep only cross-source pairs (one new, one base).
            if (a in new_ids) != (b in new_ids):
                new_id = a if a in new_ids else b
                base_id = b if a in new_ids else a
                all_matches.append((new_id, base_id, score))

    # Fuzzy matchkeys via match_one, per new record.
    if fuzzy_mks:
        row_index = {row["__row_id__"]: row for row in combined.to_dicts()}
        for new_id in sorted(new_ids):
            row = row_index.get(new_id)
            if not row:
                continue
            for mk in fuzzy_mks:
                for rid, score in match_one(row, combined, mk):
                    if rid not in new_ids:
                        all_matches.append((new_id, rid, score))

    # Deduplicate: keep best score per (new_id, base_id) pair.
    best: dict[tuple[int, int], float] = {}
    for new_id, base_id, score in all_matches:
        key = (new_id, base_id)
        if key not in best or score > best[key]:
            best[key] = score

    matches = [
        {"new_row_id": n, "base_row_id": b, "score": round(s, 4)}
        for (n, b), s in best.items()
    ]
    matched_new_ids = {m["new_row_id"] for m in matches}

    return {
        "base_records": base_df.height,
        "new_records": new_df.height,
        "matched_to_base": len(matched_new_ids),
        "new_entities": len(new_ids) - len(matched_new_ids),
        "total_pairs": len(matches),
        "matches": matches,
    }
