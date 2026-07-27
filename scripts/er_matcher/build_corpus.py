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

Box-safe: stdlib + `sources_config` (PyYAML) only. Never touches the
network -- `fetch` sources contribute no rows here, so no download happens
during a corpus build.
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path
from typing import Any

from sources_config import build_source, load_sources

_SPLITS = ("train", "val", "test")

# Mechanisms whose splits() output is folded into the training corpus.
# `fetch` sources (Magellan, NCVR) are excluded even when eval_only is
# somehow False, since their data is either cite-only/eval-only by design
# or not locally materialized.
_ROW_MECHANISMS = {"bundle", "generate"}


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


def build_corpus(
    sources_yaml: Path, out_dir: Path, *, seed: int, cap: int | None = None
) -> dict[str, Any]:
    """Blend every bundled/generated, non-eval-only source into
    `out_dir/{train,val,test}.jsonl` + `out_dir/manifest.json`. Returns the
    manifest dict that was written.

    `cap`, if given, limits EACH contributing source to at most `cap` rows
    per split -- via `_cap_preserving_balance` (interleaves match/no_match
    before truncating, see its docstring), so results stay reproducible AND
    class-balanced. This is the "cap oversized sources" lever; proportional
    weight-blending (`SourceEntry.weight`) is deferred to a later task.
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
            for split in _SPLITS:
                rows = list(splits.get(split, []))
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
    args = ap.parse_args(argv)

    manifest = build_corpus(args.sources, args.out_dir, seed=args.seed, cap=args.cap)
    print(json.dumps(manifest["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
