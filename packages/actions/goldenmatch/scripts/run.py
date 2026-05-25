#!/usr/bin/env python3
"""Run GoldenMatch dedupe on matching files; emit per-file JSON results.

Uses the GoldenMatch Python API (the CLI emits output files, not a JSON
summary). Each file is deduped with either an explicit YAML config or the
`exact` / `fuzzy` keys supplied as inputs — an explicit key/config is required
so CI never triggers the zero-config controller (which can reach the network
for cross-encoder rerank). Writes one JSON file per input to RESULTS_DIR, sets
the composite-action outputs, and fails when duplicates exceed `max-duplicates`.
"""
from __future__ import annotations

import glob
import json
import os
import sys

RESULTS_DIR = "/tmp/goldenmatch-results"


def _set_output(name: str, value: object) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"{name}={value}\n")


def _parse_fields(raw: str) -> list[str]:
    return [c.strip() for c in raw.split(",") if c.strip()]


def main() -> int:
    files_glob = os.environ.get("GM_FILES", "")
    config_path = os.environ.get("GM_CONFIG", "")
    exact = _parse_fields(os.environ.get("GM_EXACT", ""))
    fuzzy_raw = os.environ.get("GM_FUZZY", "")
    max_dupes = int(os.environ.get("GM_MAX_DUPLICATES", "-1"))
    os.makedirs(RESULTS_DIR, exist_ok=True)

    import polars as pl
    import goldenmatch

    config = goldenmatch.load_config(config_path) if config_path else None
    # fuzzy input is a comma list of `field:threshold` pairs.
    fuzzy: dict[str, float] = {}
    for pair in _parse_fields(fuzzy_raw):
        field, _, thr = pair.partition(":")
        fuzzy[field.strip()] = float(thr) if thr else 0.85

    if config is None and not exact and not fuzzy:
        print("::error::Provide a `config`, `exact`, or `fuzzy` input — "
              "zero-config dedupe is disabled in CI to avoid network calls.")
        return 1

    matched = [f for f in sorted(glob.glob(files_glob)) if os.path.isfile(f)]
    if not matched:
        print(f"::error::No files matched pattern: {files_glob}")
        return 1

    total_dupes = 0
    total_clusters = 0
    for path in matched:
        name = os.path.basename(path)
        try:
            df = pl.read_csv(path, infer_schema_length=0)
            if config is not None:
                result = goldenmatch.dedupe_df(df, config=config)
            else:
                kwargs: dict = {}
                if exact:
                    kwargs["exact"] = exact
                if fuzzy:
                    kwargs["fuzzy"] = fuzzy
                result = goldenmatch.dedupe_df(df, **kwargs)
            dupes = result.dupes.height
            clusters = len(result.clusters)
            entry = {
                "file": name,
                "rows": df.height,
                "clusters": clusters,
                "duplicates": dupes,
                "unique": result.unique.height,
            }
            total_dupes += dupes
            total_clusters += clusters
        except Exception as exc:  # noqa: BLE001 - surface as a per-file error
            entry = {"file": name, "error": str(exc)}
        with open(os.path.join(RESULTS_DIR, f"{name}.json"), "w") as f:
            json.dump(entry, f)

    _set_output("clusters", total_clusters)
    _set_output("duplicates", total_dupes)
    _set_output("files_processed", len(matched))

    print(
        f"Deduped {len(matched)} file(s): "
        f"{total_clusters} clusters, {total_dupes} duplicate rows"
    )
    if 0 <= max_dupes < total_dupes:
        print(f"::error::Found {total_dupes} duplicate rows (max allowed: {max_dupes})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
