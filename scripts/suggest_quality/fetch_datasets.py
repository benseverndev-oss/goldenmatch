#!/usr/bin/env python3
"""Fetch the skip-when-absent datasets this gate can score, for CI.

## Why this exists

`scripts/suggest_quality/datasets.py` loads real datasets from
`packages/python/goldenmatch/tests/benchmarks/datasets`, which is gitignored.
On a developer's machine they are usually there; in CI they never are. So
`dblp_acm` used to report `skipped: dblp_acm (absent)` and **could not be
blessed in `scorecard.json`**: blessing a dataset CI cannot load converts a
legitimate skip into a permanent MISSING failure on every run (attempted in
#2566, caught by the gate, reverted).

The gate is right to refuse, and softening it would be the wrong fix. The
missing piece is the data, so this fetches it.

**UPDATE (#2721): `dblp_acm` is now VENDORED** at
`scripts/suggest_quality/vendored/DBLP-ACM/`, and `_dblp_acm_dir()` prefers the
vendored copy over the runtime-download location this writes to. So its fetch
is shadowed in the normal case, and `_already_loadable` below skips it rather
than spending a network round-trip on a result nothing reads.

The fetcher is deliberately KEPT rather than deleted. Vendoring is the
guarantee -- deterministic, offline, and the only thing that can support a
bless, because a best-effort fetch cannot (that is the #2566 failure again).
This is the fallback beneath it: if a vendored corpus is ever removed, or a
future dataset is too large to commit, the path still exists. One mechanism for
availability, one subordinate to it, and the subordination is explicit.

Note the trap #2635 records: a pre-bless run on a laptop cannot warn about
this, because locally the dataset IS present. Local reported 2 skipped where
CI reported 3. A gate can only warn about what it can see.

## What it does NOT do

It does not fail the build. A Leipzig outage degrades to exactly today's
behaviour (the dataset is absent, the gate skips it) rather than reddening a
quality gate on someone else's downtime. Once a dataset is blessed, its
absence becomes a MISSING failure *in the gate*, which is the right place for
that decision to be enforced.

It reuses `run_benchmarks._fetch_dblp_acm` rather than reimplementing the
fetch, so the URL, its `GOLDENMATCH_DBLP_ACM_URL` override, the retry policy
and the zip-flattening quirk stay in one place.

Usage:
    python -m scripts.suggest_quality.fetch_datasets
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.run_benchmarks import _fetch_dblp_acm  # noqa: E402
from scripts.suggest_quality.datasets import _DATASETS_ROOT, REGISTRY  # noqa: E402

# name -> fetcher. Only datasets with a redistributable, stable public source
# belong here; `febrl3` comes from the `recordlinkage` package and `ncvr_real`
# has no public URL, so both stay legitimately skip-when-absent.
_FETCHERS = {
    "dblp_acm": _fetch_dblp_acm,
}


def _already_loadable(name: str) -> bool:
    """True when the gate can already load this dataset without fetching.

    Post-#2721 `dblp_acm` is vendored and `_dblp_acm_dir()` prefers the
    vendored copy, so fetching it would download a corpus nothing subsequently
    reads. Asking the REGISTRY rather than checking a path keeps this honest:
    the question is "can the gate load it", which is what actually matters and
    is not the same as "does some file exist".
    """
    entry = next((d for d in REGISTRY if d.name == name), None)
    if entry is None:
        return False
    try:
        return entry.loader() is not None
    except Exception:  # noqa: BLE001 - a broken loader means "fetch and see"
        return False


def main() -> int:
    _DATASETS_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"datasets root: {_DATASETS_ROOT}")

    for name, fetch in _FETCHERS.items():
        if _already_loadable(name):
            print(f"  {name}: already loadable (vendored) -- skipping fetch")
            continue
        try:
            ok = fetch(_DATASETS_ROOT)
        except Exception as exc:  # noqa: BLE001 - best-effort by design
            print(f"  {name}: fetch raised ({type(exc).__name__}: {exc})")
            ok = False
        print(f"  {name}: {'fetched' if ok else 'NOT fetched (gate will skip it)'}")

    # Report what the gate will actually see, which is the thing that matters
    # and is not the same question as "did the download return 200".
    print("\nloadable by the gate:")
    for d in REGISTRY:
        try:
            loaded = d.loader()
        except Exception as exc:  # noqa: BLE001
            print(f"  {d.name:22s} ERROR {type(exc).__name__}: {str(exc)[:60]}")
            continue
        if loaded is None:
            print(f"  {d.name:22s} absent")
        else:
            df, gt = loaded
            print(f"  {d.name:22s} present  rows={df.height} gt_pairs={len(gt)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
