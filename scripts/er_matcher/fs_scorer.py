#!/usr/bin/env python3
"""goldenmatch Fellegi-Sunter scorer + blocker layer for corpus enrichment (Task 6).

Everything goldenmatch/polars-coupled lives here so `build_corpus.py` stays a
thin stdlib blender + orchestrator. The public surface `build_corpus`'s
`_fs_enrich_source` uses:

  - `_make_fs_scorer(train_records, domain, sample_pairs=) -> (scorer, tau,
    blocking_cfg, scorer_cfg)` -- fit the FS posterior once (falling back to the
    weighted scorer when EM degenerates),
  - `_make_candidates(records, blocking_cfg, gold_linked=, id_prefix=)` -- block a
    split's pool and tag candidate pairs with `gold_match`,
  - `_gold_components(pairs)` -- match-row union-find -> `gold_linked` predicate,
  - `_sample_labeled_pairs` / `_check_separation` -- the posterior-vs-weighted gate,
  - the pure disk-cache helpers (`_pairs_hash`, `_load_fs_cache`, `_save_fs_cache`).

BOX-SAFE AT IMPORT: every goldenmatch/polars import is function-local, so
importing this module (e.g. to box-test the pure adapters) never pulls the heavy
stack. Only `_fit_*`/`_score_*`/`_make_candidates`/`_records_to_frame` touch it,
and only when actually called.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# The FS decision threshold used whenever a real calibrated cutoff isn't
# available (weighted fallback, degenerate normalizer, resolve_thresholds break).
_DEFAULT_TAU = 0.5
# A posterior scorer must beat the non-match mean by at least this margin on the
# train split's labeled sample, else we fall back to the weighted scorer.
_SEPARATION_MARGIN = 0.1
# How many match / non-match pairs to draw for the separation self-check.
_SEPARATION_SAMPLE = 200

# `_block_row_ids` logs the BlockResult shape it saw ONCE per shape (T7 uses it
# to confirm which goldenmatch the run bound against).
_LOGGED_BLOCK_SHAPES: set[str] = set()


def _records_to_frame(records: list[dict], *, with_row_id: bool = True) -> Any:
    """Build a polars DataFrame from a split's raw record pool. With
    ``with_row_id`` (the EM/blocker path) it carries the stable ``__row_id__``
    Int64 key goldenmatch's trainer + blocker expect (`pipeline._add_row_ids`
    uses the same key). Auto-config PROFILES columns and adds its own row id, so
    the profiling calls pass ``with_row_id=False`` -- else `auto_configure_df`'s
    controller trips a duplicate-``__row_id__`` error."""
    import polars as pl

    cols = sorted({k for r in records for k in r})
    data: dict[str, list] = {c: [r.get(c) for r in records] for c in cols}
    df = pl.DataFrame(data)
    if with_row_id:
        df = df.with_columns(
            pl.Series("__row_id__", list(range(len(records))), dtype=pl.Int64)
        )
    return df


def _fields_from_config(mk: Any) -> list:
    """The MatchkeyConfig -> runtime MatchkeyField bridge the weighted
    ``score_pair`` wants. `MatchkeyConfig.fields` is ALREADY a
    ``list[MatchkeyField]`` (see `config/schemas.py`; `match_one`/`db.sync` call
    ``score_pair(a, b, mk.fields)`` directly), so the bridge is just the attr."""
    return list(getattr(mk, "fields", []) or [])


def _fit_fs_posterior(records: list[dict]) -> tuple[Any, Any, Any]:
    """Fit the genuine Fellegi-Sunter POSTERIOR path on `records`:
    `auto_configure_probabilistic_df` builds the probabilistic matchkey +
    blocking; `train_em` fits the m/u weights. Returns ``(mk, em_result,
    blocking)``. `score_pair_probabilistic(a, b, mk, em)` then yields P(match)
    in [0,1] (a sigmoid posterior under the default 'posterior' calibration)."""
    import os

    os.environ.setdefault("GOLDENMATCH_FS_CALIBRATED", "posterior")

    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    from goldenmatch.core.blocker import build_blocks, collect_blocking_fields
    from goldenmatch.core.matchkey import precompute_matchkey_transforms
    from goldenmatch.core.probabilistic import train_em

    config = auto_configure_probabilistic_df(_records_to_frame(records, with_row_id=False))
    mk = config.matchkeys[0]
    score_frame = precompute_matchkey_transforms(_records_to_frame(records), config.matchkeys)
    # build_blocks' static primary builder consumes the ORIGINAL entry object and
    # calls `.collect()` on it, so hand it a LazyFrame (not the eager frame the EM
    # trainer wants).
    blocks = build_blocks(score_frame.lazy(), config.blocking)
    em = train_em(
        score_frame,
        mk,
        blocks=blocks,
        blocking_fields=collect_blocking_fields(config.blocking),
    )
    return mk, em, config.blocking


def _threshold_from_config(mk: Any, em: Any) -> float:
    """The FS decision threshold `tau` = the resolved link cutoff (which prefers
    `EMResult.calibrated_link_threshold`); `_DEFAULT_TAU` when unavailable."""
    try:
        from goldenmatch.core.probabilistic import resolve_thresholds

        link, _review = resolve_thresholds(mk, em)
        return _DEFAULT_TAU if link is None else float(link)
    except Exception as exc:
        # Don't silently score against tau=0.5 if resolve_thresholds actually
        # broke -- surface it so a real regression isn't masked.
        print(
            f"[fs-enrich] resolve_thresholds failed ({exc!r}); "
            f"using default tau={_DEFAULT_TAU}",
            file=sys.stderr,
        )
        return _DEFAULT_TAU


def _score_posterior(mk: Any, em: Any) -> Callable[[dict, dict], float]:
    """Wrap `score_pair_probabilistic` into a `scorer(a, b) -> float` clamped to
    [0,1]. It applies each field's transforms internally (`comparison_vector`),
    so it scores raw record dicts directly -- no precompute needed at score time."""
    from goldenmatch.core.probabilistic import score_pair_probabilistic

    def scorer(a: dict, b: dict) -> float:
        s = float(score_pair_probabilistic(a, b, mk, em))
        return min(1.0, max(0.0, s))

    return scorer


def _fit_weighted(records: list[dict]) -> tuple[Any, Any]:
    """Weighted/iterative fallback config (`auto_configure_df`) -- the aggregator
    whose `score_pair` output is NOT a [0,1] posterior (normalized separately).

    `auto_configure_df` emits a MIX of exact + weighted matchkeys; `score_pair`
    only works on a WEIGHTED (fuzzy-scorer) matchkey (an exact matchkey's fields
    have `scorer=None` and raise on `.fuzzy_scorer`), so pick the first weighted
    one, falling back to `matchkeys[0]` only if none exists."""
    from goldenmatch.core.autoconfig import auto_configure_df

    config = auto_configure_df(_records_to_frame(records, with_row_id=False))
    weighted = [mk for mk in config.matchkeys if getattr(mk, "type", None) == "weighted"]
    mk = weighted[0] if weighted else config.matchkeys[0]
    return mk, config.blocking


def _score_weighted(
    mk: Any, sample_pairs: list[tuple[dict, dict, bool]]
) -> Callable[[dict, dict], float]:
    """Min-max-normalize the weighted `score_pair` aggregate into [0,1] using the
    train sample's observed score range, then clamp (val/test can exceed the
    train range). Falls back to a constant `_DEFAULT_TAU` when the range is
    degenerate."""
    from goldenmatch.core.scorer import score_pair

    fields = _fields_from_config(mk)
    raw = [score_pair(a, b, fields) for a, b, _m in sample_pairs]
    lo = min(raw) if raw else 0.0
    hi = max(raw) if raw else 1.0
    span = hi - lo

    def scorer(a: dict, b: dict) -> float:
        s = score_pair(a, b, fields)
        norm = (s - lo) / span if span > 0 else _DEFAULT_TAU
        return min(1.0, max(0.0, norm))

    return scorer


def _scorer_cfg_signature(mk: Any, blocking: Any, mode: str) -> str:
    """Stable digest input describing the fitted scorer, fed to
    `fs_enrich.cache_key` so a rebuild with an unchanged config skips the FS
    pass. Captures the calibration mode, matchkey name, scored fields, and
    blocking fields (the levers that change scores)."""
    try:
        from goldenmatch.core.blocker import collect_blocking_fields

        blk = sorted(collect_blocking_fields(blocking)) if blocking is not None else []
    except Exception as exc:
        print(
            f"[fs-enrich] collect_blocking_fields failed ({exc!r}); "
            f"cache key omits blocking fields",
            file=sys.stderr,
        )
        blk = []
    fields = [getattr(f, "field", None) for f in getattr(mk, "fields", []) or []]
    return json.dumps(
        {
            "mode": mode,
            "matchkey": getattr(mk, "name", None),
            "fields": fields,
            "blocking": blk,
        },
        sort_keys=True,
    )


def _sample_labeled_pairs(
    pairs: list[dict], k: int = _SEPARATION_SAMPLE
) -> list[tuple[dict, dict, bool]]:
    """Draw up to `k` match and `k` non-match labeled pairs (deterministic) for
    the separation self-check. Returns ``(a, b, is_match)`` tuples."""
    rng = random.Random(0)
    matches = [p for p in pairs if p.get("label") == "match"]
    non = [p for p in pairs if p.get("label") != "match"]

    def take(lst: list[dict]) -> list[dict]:
        return list(lst) if len(lst) <= k else rng.sample(lst, k)

    sel = take(matches) + take(non)
    return [(p["a"], p["b"], p.get("label") == "match") for p in sel]


def _check_separation(
    scorer: Callable[[dict, dict], float], sample: list[tuple[dict, dict, bool]]
) -> tuple[bool, float, float, bool]:
    """Score the labeled sample once and report ``(ok, mean_match,
    mean_nonmatch, all_in_range)``. `ok` requires every score in [0,1] AND
    mean(match) to beat mean(non-match) by `_SEPARATION_MARGIN`. This is the gate
    that decides posterior vs weighted -- a margin/in-range regression silently
    flips every source to the weighted fallback, so it is box-tested directly."""
    scored = [(scorer(a, b), is_m) for a, b, is_m in sample]
    in_range = all(0.0 <= s <= 1.0 for s, _ in scored)
    m = [s for s, is_m in scored if is_m]
    n = [s for s, is_m in scored if not is_m]
    mean_m = sum(m) / len(m) if m else 0.0
    mean_n = sum(n) / len(n) if n else 0.0
    ok = in_range and bool(m) and bool(n) and mean_m > mean_n + _SEPARATION_MARGIN
    return ok, mean_m, mean_n, in_range


def _make_fs_scorer(
    train_records: list[dict],
    domain: str,
    *,
    sample_pairs: list[tuple[dict, dict, bool]] | None = None,
) -> tuple[Callable[[dict, dict], float], float, Any, str]:
    """Fit ONCE on the TRAIN pool; return ``(scorer, tau, blocking_cfg,
    scorer_cfg)``. Prefers the genuine FS posterior; if EM degenerates (the
    posterior fails to separate the labeled sample, or the fit raises -- EM can
    degenerate on tiny pools) it falls back to the weighted `score_pair`
    min-max-normalized into [0,1], logging which path each split used. `scorer`
    always clamps to [0,1]."""
    if not train_records:
        raise ValueError("FS scorer needs a non-empty train record pool")

    # The probabilistic blocking (built without EM) is derived-column-free by
    # design (auto_configure_probabilistic_df adds NO standardization), so it's
    # the safe blocking to mine candidates with even when the SCORER falls back
    # to weighted -- the weighted config's blocking can key on standardized
    # columns (e.g. `__brand__`) the raw record pool doesn't carry.
    posterior_blocking = None
    try:
        mk, em, posterior_blocking = _fit_fs_posterior(train_records)
        scorer = _score_posterior(mk, em)
        tau = _threshold_from_config(mk, em)
        posterior_sig = _scorer_cfg_signature(mk, posterior_blocking, "posterior")
        if not sample_pairs:
            return scorer, tau, posterior_blocking, posterior_sig
        ok, mean_m, mean_n, in_range = _check_separation(scorer, sample_pairs)
        if ok:
            return scorer, tau, posterior_blocking, posterior_sig
        print(
            f"[fs-enrich] posterior did not separate for domain={domain!r} "
            f"(mean_match={mean_m:.3f} mean_nonmatch={mean_n:.3f} "
            f"in_range={in_range}); falling back to weighted",
            file=sys.stderr,
        )
    except Exception as exc:  # EM degeneration / no matchkeys / etc.
        print(
            f"[fs-enrich] posterior fit failed for domain={domain!r}: {exc!r}; "
            f"falling back to weighted",
            file=sys.stderr,
        )

    wmk, wblocking = _fit_weighted(train_records)
    scorer = _score_weighted(wmk, sample_pairs or [])
    mining_blocking = posterior_blocking if posterior_blocking is not None else wblocking
    return scorer, _DEFAULT_TAU, mining_blocking, _scorer_cfg_signature(
        wmk, mining_blocking, "weighted"
    )


def _record_signature(rec: dict) -> tuple:
    """Hashable identity of a record from its field VALUES (record pools strip
    the source id, so values are the only stable cross-reference between a
    split's match rows and its record pool)."""
    return tuple(sorted((str(k), str(v)) for k, v in rec.items()))


def _gold_components(pairs: list[dict]) -> Callable[[dict, dict], bool]:
    """Build a `gold_linked(rec_a, rec_b)` predicate from a split's MATCH rows
    via connected components (union-find) over record signatures. Transitive
    closure is load-bearing: FEBRL emits star-topology positives (anchor-vs-dup
    only), so dup0-vs-dup1 is same-entity but never appears as a match row --
    the component closure recovers it. Records absent from every match row are
    non-matches (predicate returns False)."""
    parent: dict[tuple, tuple] = {}

    def find(x: tuple) -> tuple:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: tuple, b: tuple) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for p in pairs:
        if p.get("label") == "match":
            union(_record_signature(p["a"]), _record_signature(p["b"]))

    def gold_linked(rec_a: dict, rec_b: dict) -> bool:
        sa, sb = _record_signature(rec_a), _record_signature(rec_b)
        if sa not in parent or sb not in parent:
            return False
        return find(sa) == find(sb)

    return gold_linked


def _pairs_from_blocks(
    id_lists: list[list],
    records_by_id: dict[Any, dict],
    gold_linked: Callable[[dict, dict], bool],
) -> list[dict]:
    """PURE adapter (box-tested with fake blocks + fake gold): enumerate the
    distinct within-block record pairs and tag each with `gold_match`. A mined
    pair is a non-match iff its two records are NOT gold-linked. Deduped across
    passes by the canonical (min, max) id so multi-pass blocking can't
    double-count a pair."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for ids in id_lists:
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                id_a, id_b = ids[i], ids[j]
                if id_a == id_b:
                    continue
                key = (id_a, id_b) if id_a <= id_b else (id_b, id_a)
                if key in seen:
                    continue
                seen.add(key)
                rec_a, rec_b = records_by_id[id_a], records_by_id[id_b]
                out.append(
                    {
                        "a": rec_a,
                        "b": rec_b,
                        "eid_a": id_a,
                        "eid_b": id_b,
                        "gold_match": bool(gold_linked(rec_a, rec_b)),
                    }
                )
    return out


def _make_candidates(
    records: list[dict],
    blocking_cfg: Any,
    *,
    gold_linked: Callable[[dict, dict], bool],
    id_prefix: str = "",
) -> list[dict]:
    """Block WITHIN a single split's record pool with the fitted BlockingConfig
    and return candidate pairs tagged with `gold_match`. Heavy import local."""
    if not records or blocking_cfg is None:
        return []

    from goldenmatch.core.blocker import build_blocks

    frame = _records_to_frame(records)
    records_by_id = {f"{id_prefix}{i}": rec for i, rec in enumerate(records)}
    id_lists: list[list] = []
    try:
        # `.lazy()`: build_blocks' static builder calls `.collect()` on the frame.
        blocks = build_blocks(frame.lazy(), blocking_cfg)
    except Exception as exc:
        # Mining is best-effort: a blocking config that references a column the
        # raw pool doesn't carry (e.g. a standardized `__brand__`) shouldn't sink
        # the whole build -- just skip mined negatives for this split.
        print(
            f"[fs-enrich] blocking failed on pool ({id_prefix!r}): {exc!r}; "
            f"skipping mined negatives for this split",
            file=sys.stderr,
        )
        return []
    for blk in blocks:
        row_ids = _block_row_ids(blk)
        id_lists.append([f"{id_prefix}{int(r)}" for r in row_ids])
    return _pairs_from_blocks(id_lists, records_by_id, gold_linked)


def _block_row_ids(blk: Any) -> list:
    """Read a block's ``__row_id__`` members, robust to the BlockResult shape:
    newer goldenmatch exposes ``.materialize().column(...)`` (seam Frame); older
    builds carry a raw polars frame on ``.df``. Handles both so this wiring
    survives goldenmatch version skew across the install/worktree. Logs the shape
    it saw ONCE (T7 uses it to confirm which goldenmatch bound at run time)."""
    if hasattr(blk, "materialize"):
        _log_block_shape("materialize")
        return blk.materialize().column("__row_id__").to_list()
    _log_block_shape("df")
    df = getattr(blk, "df", None)
    if df is None:
        return []
    if hasattr(df, "collect"):  # LazyFrame -> eager
        df = df.collect()
    return df["__row_id__"].to_list()


def _log_block_shape(shape: str) -> None:
    """Emit a one-time `[fs-enrich]` note naming which BlockResult shape fired."""
    if shape in _LOGGED_BLOCK_SHAPES:
        return
    _LOGGED_BLOCK_SHAPES.add(shape)
    detail = (
        "newer goldenmatch (seam Frame)"
        if shape == "materialize"
        else "older goldenmatch (raw .df frame)"
    )
    print(
        f"[fs-enrich] BlockResult shape: '{shape}' -- {detail}",
        file=sys.stderr,
    )


def _rebalance_negatives(enriched: list[dict], mined_count: int) -> list[dict]:
    """Honor "reduced share of synthetic negatives": for every mined hard
    negative added, drop one ORIGINAL synthetic negative (a `no_match` row with
    no `negative_kind`) so the total negative count stays balanced rather than
    growing -- swapping easy synthetic negatives for hard mined ones.
    Deterministic (drops in encounter order)."""
    if mined_count <= 0:
        return enriched
    to_drop = mined_count
    out: list[dict] = []
    for row in enriched:
        if (
            to_drop > 0
            and row.get("label") == "no_match"
            and "negative_kind" not in row
        ):
            to_drop -= 1
            continue
        out.append(row)
    return out


def _pairs_hash(pairs: list[dict]) -> str:
    """Deterministic digest of a source's pre-enrichment pairs -> the
    `corpus_hash` fed to `fs_enrich.cache_key`."""
    h = hashlib.sha256()
    for p in pairs:
        h.update(
            json.dumps(
                {k: p.get(k) for k in ("a", "b", "label", "eid_a", "eid_b")},
                sort_keys=True,
                default=str,
            ).encode()
        )
    return h.hexdigest()


def _fs_cache_path(out_dir: Path, key: str) -> Path:
    return out_dir / ".fs_cache" / f"{key}.json"


def _load_fs_cache(out_dir: Path, key: str) -> dict[str, list[dict]] | None:
    path = _fs_cache_path(out_dir, key)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _save_fs_cache(out_dir: Path, key: str, result: dict[str, list[dict]]) -> None:
    path = _fs_cache_path(out_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
