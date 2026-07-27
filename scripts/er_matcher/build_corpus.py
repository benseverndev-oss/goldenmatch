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
from pathlib import Path
from typing import Any

from sources_config import build_source, load_sources

_SPLITS = ("train", "val", "test")

# Mechanisms whose splits() output is folded into the training corpus.
# `fetch` sources (Magellan, NCVR) are excluded even when eval_only is
# somehow False, since their data is either cite-only/eval-only by design
# or not locally materialized.
_ROW_MECHANISMS = {"bundle", "generate"}


def build_corpus(
    sources_yaml: Path, out_dir: Path, *, seed: int, cap: int | None = None
) -> dict[str, Any]:
    """Blend every bundled/generated, non-eval-only source into
    `out_dir/{train,val,test}.jsonl` + `out_dir/manifest.json`. Returns the
    manifest dict that was written.

    `cap`, if given, limits EACH contributing source to at most `cap` rows
    per split -- taken as the first `cap` rows after the source's own
    deterministic `splits()` ordering (no re-shuffling), so results stay
    reproducible. This is the "cap oversized sources" lever; proportional
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

        contributes = entry.mechanism in _ROW_MECHANISMS and not entry.eval_only
        counts = {s: 0 for s in _SPLITS}

        if contributes:
            splits = src.splits()
            for split in _SPLITS:
                rows = list(splits.get(split, []))
                if cap is not None:
                    rows = rows[:cap]
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
    manifest = {"seed": seed, "sources": manifest_sources, "totals": totals}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


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
