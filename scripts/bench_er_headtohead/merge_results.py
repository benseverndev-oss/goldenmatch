#!/usr/bin/env python
"""Merge N dispatched head-to-head run artifacts into one final tables object.

A full 6-lane x 2-shape x scale sweep is split across several workflow
dispatches (by scale band and by lane, spec 7.3); each dispatch uploads its own
`bench_results.json` (an `{header, results}` object) as a separate artifact. The
merge job downloads them one-dir-per-artifact (NO `merge-multiple`, spec 7.2) and
this script unions them:

- every distinct run header is kept under `runs`;
- results are folded into a dict keyed `(shape, lane, rows_requested)`; on a key
  collision the entry from the artifact whose header `run_timestamp` is LARGER
  wins (deterministic later-timestamp-wins, spec 7.2) -- this does NOT depend on
  glob/dict/file-mtime ordering, only the numeric `run_timestamp`.

`merge_dir` is dependency-free (pure dict/json). `main()` imports orchestrate
lazily only to render markdown -- so the tested unit needs neither goldenmatch
nor splink.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def merge_dir(root) -> dict:
    """Union every `*/bench_results.json` under `root` (one dir per artifact).

    Returns `{"runs": [header, ...], "results": [entry, ...]}` where each
    `(shape, lane, rows_requested)` key keeps the entry from the LATER
    `run_timestamp` artifact.
    """
    root = Path(root)
    runs: list[dict] = []
    # key -> (source run_timestamp, entry)
    folded: dict[tuple, tuple[float, dict]] = {}

    for path in sorted(root.glob("*/bench_results.json")):
        blob = json.loads(path.read_text())
        header = blob.get("header", {}) or {}
        runs.append(header)
        # Missing/None timestamp sorts oldest so a stamped run always wins.
        ts = header.get("run_timestamp")
        ts = float(ts) if isinstance(ts, (int, float)) else float("-inf")
        for entry in blob.get("results", []) or []:
            key = (entry.get("shape"), entry.get("lane"), entry.get("rows_requested"))
            prev = folded.get(key)
            if prev is None or ts >= prev[0]:
                folded[key] = (ts, entry)

    results = [entry for _, entry in folded.values()]
    return {"runs": runs, "results": results}


def _load(name):
    """Sibling-path module load (same pattern the other bench scripts use)."""
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts-dir", type=Path, required=True)
    ap.add_argument("--out-json", type=Path, required=True)
    ap.add_argument("--out-md", type=Path, required=True)
    args = ap.parse_args()

    merged = merge_dir(args.artifacts_dir)
    args.out_json.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    orchestrate = _load("orchestrate")  # lazy: keeps merge_dir dependency-free
    # Render the banner from the LATEST run header (max run_timestamp), not the
    # first artifact alphabetically -- so a re-dispatch's header wins.
    hdr = (
        max(merged["runs"], key=lambda h: h.get("run_timestamp", float("-inf")))
        if merged["runs"]
        else {}
    )
    md = orchestrate.render_markdown(merged["results"], hdr)
    args.out_md.write_text(md, encoding="utf-8")
    print(md)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(md)


if __name__ == "__main__":
    main()
