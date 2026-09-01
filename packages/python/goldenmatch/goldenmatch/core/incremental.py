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
    from goldenmatch.core.autofix import auto_fix_dataframe
    from goldenmatch.core.frame import concat_frames, to_frame
    from goldenmatch.core.io_arrow import read_files_arrow
    from goldenmatch.core.match_one import match_one
    from goldenmatch.core.scorer import find_exact_matches

    matchkeys = config.get_matchkeys()
    if threshold is not None:
        for mk in matchkeys:
            if mk.threshold is not None:
                mk.threshold = threshold

    # Arrow ingest through the frame seam. This was `load_file(...).collect()`
    # plus polars expressions, which made `goldenmatch incremental` raise
    # ImportError on a default install (polars has been an optional extra since
    # v3.1.0).
    base_df = read_files_arrow([(base_file, "base")], source_column="__source__",
                               row_id_column="__row_id__")
    base_df, _ = auto_fix_dataframe(base_df)
    base_max_id = to_frame(base_df).height

    # New records are numbered ABOVE the base max: base rows occupy
    # 0..height-1, so the next free id is exactly height. `ensure_row_ids`
    # takes that offset directly.
    new_df = read_files_arrow([(new_file, "new")], source_column="__source__")
    new_df, _ = auto_fix_dataframe(new_df)
    new_frame = to_frame(new_df).ensure_row_ids("__row_id__", offset=base_max_id)

    # relaxed=True is the old `how="diagonal"`: union of columns, nulls in the
    # gaps. Plain concat raises when base and new carry different columns.
    frame = concat_frames([to_frame(base_df), new_frame], relaxed=True)

    # Standardize + derive matchkeys on the seam rather than through the
    # polars-expression engine in core/standardize.py. These are the same two
    # seam ops the arrow lane of the main pipeline uses for its eager stages.
    if config.standardization:
        _rules = getattr(config.standardization, "rules", config.standardization)
        for _col, _std_names in (_rules or {}).items():
            if _col in frame.columns:
                frame = frame.with_column(
                    _col, frame.derive_standardized_column(_col, _std_names)
                )
    for mk in matchkeys:
        frame = frame.with_column(
            f"__mk_{mk.name}__",
            frame.derive_matchkey(
                [(f.field, list(f.transforms or [])) for f in mk.fields if f.field]
            ),
        )

    all_matches: list[tuple[int, int, float]] = []
    new_ids = set(range(base_max_id, base_max_id + new_frame.height))

    exact_mks = [mk for mk in matchkeys if mk.type == "exact"]
    fuzzy_mks = [mk for mk in matchkeys if mk.type != "exact"]

    # Exact matchkeys via Polars join (match_one doesn't support exact).
    for mk in exact_mks:
        mk_col = f"__mk_{mk.name}__"
        if mk_col not in frame.columns:
            continue
        # find_exact_matches already reads through the seam; the old
        # `.lazy()` was the only polars requirement at this call site.
        for a, b, score in find_exact_matches(frame, mk):
            # Keep only cross-source pairs (one new, one base).
            if (a in new_ids) != (b in new_ids):
                new_id = a if a in new_ids else b
                base_id = b if a in new_ids else a
                all_matches.append((new_id, base_id, score))

    # Fuzzy matchkeys via match_one, per new record.
    if fuzzy_mks:
        row_index = {
            row["__row_id__"]: row
            for row in frame.select_dicts(list(frame.columns))
        }
        for new_id in sorted(new_ids):
            row = row_index.get(new_id)
            if not row:
                continue
            for mk in fuzzy_mks:
                for rid, score in match_one(row, frame, mk):
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
        "base_records": base_max_id,
        "new_records": new_frame.height,
        "matched_to_base": len(matched_new_ids),
        "new_entities": len(new_ids) - len(matched_new_ids),
        "total_pairs": len(matches),
        "matches": matches,
    }
