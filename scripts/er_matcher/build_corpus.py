#!/usr/bin/env python3
"""Corpus-builder driver for the multi-source ER data pipeline (Task 8).

Blends every BUNDLED/GENERATED, non-eval-only source's `splits()` output
into one unified `train.jsonl`/`val.jsonl`/`test.jsonl` (same
`json.dumps(row, sort_keys=True, ensure_ascii=True)`-per-line style as
`gen_pairs._write`, so downstream training code doesn't care which pipeline
produced the JSONL) plus a `manifest.json` recording every configured
source's provenance/license -- INCLUDING `fetch`/`eval_only` sources
(Magellan, NCVR), which are still constructed (so a broken `sources.yaml`
entry fails loudly at build time) but never contribute rows.

Blend weights (`SourceEntry.weight`) are recorded in the manifest for the
model card but ratio-blending by weight is DEFERRED -- this task only caps
per-source row counts (the `cap` lever); it does not re-weight the mix.

Box-safe: stdlib + `sources_config` (PyYAML) + `fs_scorer` only. `fs_scorer`
keeps every goldenmatch/polars import function-local, so importing it here is
free -- the heavy stack loads only under `--fs-enrich` (the opt-in FS
enrichment layer; default off keeps the blend byte-identical). Never touches
the network -- `fetch` sources contribute no rows here, so no download happens
during a corpus build.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any

from fs_scorer import (
    _gold_components,
    _load_fs_cache,
    _make_candidates,
    _make_fs_scorer,
    _pairs_hash,
    _rebalance_negatives,
    _sample_labeled_pairs,
    _save_fs_cache,
)
from sources_config import build_source, load_sources

_SPLITS = ("train", "val", "test")

# Mechanisms whose splits() output is folded into the training corpus.
# `fetch` sources (Magellan, NCVR) are excluded even when eval_only is
# somehow False, since their data is either cite-only/eval-only by design
# or not locally materialized.
_ROW_MECHANISMS = {"bundle", "generate"}

# ── FS enrichment tunables (Task 6) ────────────────────────────────────────
# The near-threshold hard-negative mining band half-width and per-split mining
# cap threaded into `fs_enrich.enrich`. `DELTA` starts at 0.1 per the plan. The
# goldenmatch-coupled scorer/blocker layer lives in `fs_scorer`.
DELTA = 0.1
MINE_CAP = 200


def _cap_preserving_balance(rows: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """Truncate `rows` to at most `cap`, preserving BOTH labels instead of
    `rows[:cap]` -- which would silently produce an all-`match` (or
    all-`no_match`) slice whenever a loader emits every positive before any
    negative (both bundled loaders do). Splits `rows` into `match`/`no_match`
    (stable order preserved within each group), then interleaves the two
    groups round-robin (match, no_match, match, no_match, ...) before
    truncating -- deterministic given `rows`' own deterministic order, so
    same seed + cap -> byte-identical output still holds."""
    matches = [r for r in rows if r["label"] == "match"]
    non_matches = [r for r in rows if r["label"] != "match"]
    interleaved: list[dict[str, Any]] = []
    for a, b in zip(matches, non_matches):
        interleaved.append(a)
        interleaved.append(b)
    # Leftover tail from whichever group is longer.
    interleaved.extend(matches[len(non_matches):])
    interleaved.extend(non_matches[len(matches):])
    return interleaved[:cap]


# ── FS enrichment orchestrator (Task 6) ────────────────────────────────────
# The goldenmatch-coupled scorer/blocker/cache layer lives in `fs_scorer`; this
# orchestrator only sequences it (fit once per source, enrich each split, cache).


def _fs_enrich_source(
    src: Any,
    entry: Any,
    splits: dict[str, Any],
    out_dir: Path,
    *,
    delta: float,
    mine_cap: int,
) -> dict[str, list[dict]]:
    """Fit the FS scorer once on the source's TRAIN pool and enrich each split's
    pairs (soft confidence + mined near-threshold gold non-matches), threading a
    per-source disk cache keyed on `fs_enrich.cache_key`. Sources without a
    `record_pools()` (or an empty train pool) are returned UNENRICHED -- the
    trainer's per-row confidence has a constant fallback, so mixed rows are
    safe."""
    from fs_enrich import cache_key, enrich

    materialized = {s: list(splits.get(s, [])) for s in _SPLITS}
    pools = getattr(src, "record_pools", lambda: {})()
    train_pool = pools.get("train", [])
    if not train_pool:
        print(
            f"[fs-enrich] source {entry.name!r} has no train record pool; "
            f"skipping FS enrichment (rows keep the training constant fallback)",
            file=sys.stderr,
        )
        return materialized

    sample_pairs = _sample_labeled_pairs(materialized["train"])
    scorer, tau, blocking_cfg, scorer_cfg = _make_fs_scorer(
        train_pool, entry.domain, sample_pairs=sample_pairs
    )

    corpus_hash = _pairs_hash([p for s in _SPLITS for p in materialized[s]])
    key = cache_key(corpus_hash=corpus_hash, scorer_cfg=scorer_cfg, tau=tau, delta=delta)
    cached = _load_fs_cache(out_dir, key)
    if cached is not None:
        print(f"[fs-enrich] cache hit for {entry.name!r} ({key[:12]})", file=sys.stderr)
        return cached

    result: dict[str, list[dict]] = {}
    for split in _SPLITS:
        rows = materialized[split]
        recs = pools.get(split, [])
        gold_linked = _gold_components(rows)
        id_prefix = f"{entry.name}:{split}:"
        if recs:
            candidates_fn = (
                lambda r, gl=gold_linked, pfx=id_prefix: _make_candidates(
                    r, blocking_cfg, gold_linked=gl, id_prefix=pfx
                )
            )
        else:
            candidates_fn = lambda r: []  # noqa: E731 (defensive: no pool -> no mining)
        enriched = enrich(
            rows,
            records=recs,
            scorer=scorer,
            candidates_fn=candidates_fn,
            tau=tau,
            delta=delta,
            mine_cap=mine_cap,
        )
        mined = sum(1 for r in enriched if r.get("negative_kind") == "fs_mined")
        result[split] = _rebalance_negatives(enriched, mined)

    _save_fs_cache(out_dir, key, result)
    return result


def build_corpus(
    sources_yaml: Path,
    out_dir: Path,
    *,
    seed: int,
    cap: int | None = None,
    enrich_fs: bool = False,
    delta: float = DELTA,
    mine_cap: int = MINE_CAP,
) -> dict[str, Any]:
    """Blend every bundled/generated, non-eval-only source into
    `out_dir/{train,val,test}.jsonl` + `out_dir/manifest.json`. Returns the
    manifest dict that was written.

    `cap`, if given, limits EACH contributing source to at most `cap` rows
    per split -- via `_cap_preserving_balance` (interleaves match/no_match
    before truncating, see its docstring), so results stay reproducible AND
    class-balanced. This is the "cap oversized sources" lever; proportional
    weight-blending (`SourceEntry.weight`) is deferred to a later task.

    `enrich_fs` (default OFF) folds the real goldenmatch Fellegi-Sunter scorer
    into each contributing source: fit the FS posterior once on the source's
    train pool, attach an FS-score-driven soft `confidence` to every pair, and
    mine near-threshold gold non-matches (`negative_kind="fs_mined"`). OFF keeps
    the output byte-identical to the pre-Task-6 blend (the pure box tests never
    enable it and never import goldenmatch). `delta`/`mine_cap` thread the mining
    band + per-split cap. eval-only/`fetch` sources are never enriched (they
    never contribute rows).
    """
    entries = load_sources(sources_yaml)

    corpus: dict[str, list[dict[str, Any]]] = {s: [] for s in _SPLITS}
    manifest_sources: dict[str, Any] = {}

    for entry in entries:
        # Always construct -- a broken config (bad kwargs, missing fixture
        # root, etc.) must fail loudly here regardless of whether the
        # source ends up contributing rows.
        src = build_source(entry, seed=seed)

        if getattr(src, "eval_only", False) and not entry.eval_only:
            raise ValueError(
                f"{entry.name}: loader class is eval_only but sources.yaml entry isn't -- "
                "refusing to fold eval-only data into the training corpus"
            )

        contributes = entry.mechanism in _ROW_MECHANISMS and not entry.eval_only
        counts = {s: 0 for s in _SPLITS}

        if contributes:
            splits = src.splits()
            enriched_splits = (
                _fs_enrich_source(
                    src, entry, splits, out_dir, delta=delta, mine_cap=mine_cap
                )
                if enrich_fs
                else {s: list(splits.get(s, [])) for s in _SPLITS}
            )
            for split in _SPLITS:
                rows = enriched_splits.get(split, [])
                if cap is not None:
                    rows = _cap_preserving_balance(rows, cap)
                corpus[split].extend(rows)
                counts[split] = len(rows)

        manifest_sources[entry.name] = {
            "mechanism": entry.mechanism,
            # yaml overrides the loader's own citation; yaml only REQUIRES
            # attribution for CC-BY (license-compliance), so a bundled
            # non-CC-BY source (febrl, magellan, ncvr) that leaves
            # `attribution:` unset in sources.yaml still gets its class's
            # citation recorded here instead of `null`.
            "license": entry.license or getattr(src, "license", None),
            "attribution": entry.attribution or getattr(src, "attribution", None),
            "eval_only": entry.eval_only,
            "weight": entry.weight,
            "domain": entry.domain,
            "counts": counts,
        }

    _write_jsonl(corpus, out_dir)

    totals = {s: sum(v["counts"][s] for v in manifest_sources.values()) for s in _SPLITS}
    label_totals = _label_totals(corpus)
    manifest = {
        "seed": seed,
        "sources": manifest_sources,
        "totals": totals,
        "label_totals": label_totals,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))

    for split, labels in label_totals.items():
        nonzero = [label for label, n in labels.items() if n > 0]
        if len(nonzero) == 1:
            warnings.warn(
                f"build_corpus: split {split!r} is single-class ({nonzero[0]} only) "
                "-- check blend/cap",
                stacklevel=2,
            )

    return manifest


def _label_totals(corpus: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = {}
    for split in _SPLITS:
        labels = {"match": 0, "no_match": 0}
        for row in corpus[split]:
            labels[row["label"]] = labels.get(row["label"], 0) + 1
        totals[split] = labels
    return totals


def _write_jsonl(corpus: dict[str, list[dict[str, Any]]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for split in _SPLITS:
        path = out_dir / f"{split}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for row in corpus[split]:
                fh.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Blend bundled ER-matcher sources into the unified training corpus."
    )
    ap.add_argument("--sources", type=Path, default=Path("scripts/er_matcher/sources.yaml"))
    ap.add_argument("--out-dir", type=Path, default=Path("data/er_matcher"))
    ap.add_argument("--seed", type=int, default=20260727)
    ap.add_argument("--cap", type=int, default=None)
    ap.add_argument(
        "--fs-enrich",
        action="store_true",
        help="Fold the real goldenmatch FS scorer in: soft confidence + mined "
        "near-threshold gold negatives (needs the goldenmatch package + polars).",
    )
    ap.add_argument("--fs-delta", type=float, default=DELTA, help="FS mining band half-width.")
    ap.add_argument("--fs-mine-cap", type=int, default=MINE_CAP, help="Max mined negatives/split.")
    args = ap.parse_args(argv)

    manifest = build_corpus(
        args.sources,
        args.out_dir,
        seed=args.seed,
        cap=args.cap,
        enrich_fs=args.fs_enrich,
        delta=args.fs_delta,
        mine_cap=args.fs_mine_cap,
    )
    print(json.dumps(manifest["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
