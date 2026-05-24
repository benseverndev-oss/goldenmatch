#!/usr/bin/env python3
"""Run GoldenFlow transforms on matching files; emit per-file JSON results.

Mirrors the GoldenCheck action's run step but uses the GoldenFlow Python API
(the CLI emits files, not a JSON summary). Writes one JSON file per input to
RESULTS_DIR, sets the composite-action outputs on GITHUB_OUTPUT, and exits
non-zero when `strict` is set and any transform errored.
"""
from __future__ import annotations

import glob
import json
import os
import sys

RESULTS_DIR = "/tmp/goldenflow-results"


def _set_output(name: str, value: object) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"{name}={value}\n")


def main() -> int:
    files_glob = os.environ.get("GF_FILES", "")
    config_path = os.environ.get("GF_CONFIG", "")
    domain = os.environ.get("GF_DOMAIN", "")
    strict = os.environ.get("GF_STRICT", "false").lower() == "true"
    os.makedirs(RESULTS_DIR, exist_ok=True)

    import polars as pl
    import goldenflow

    config = None
    if config_path:
        config = goldenflow.load_config(config_path)

    matched = [f for f in sorted(glob.glob(files_glob)) if os.path.isfile(f)]
    if not matched:
        print(f"::error::No files matched pattern: {files_glob}")
        return 1

    total_transforms = 0
    total_errors = 0
    for path in matched:
        name = os.path.basename(path)
        try:
            df = pl.read_csv(path, infer_schema_length=0)
            kwargs = {}
            if config is not None:
                kwargs["config"] = config
            if domain:
                kwargs["domain"] = domain
            result = goldenflow.transform_df(df, **kwargs)
            records = list(result.manifest.records)
            errors = list(getattr(result.manifest, "errors", []) or [])
            entry = {
                "file": name,
                "rows": df.height,
                "transforms_applied": len(records),
                "errors": len(errors),
            }
            total_transforms += len(records)
            total_errors += len(errors)
        except Exception as exc:  # noqa: BLE001 - surface as a per-file error
            entry = {"file": name, "error": str(exc)}
            total_errors += 1
        with open(os.path.join(RESULTS_DIR, f"{name}.json"), "w") as f:
            json.dump(entry, f)

    _set_output("transforms_applied", total_transforms)
    _set_output("files_processed", len(matched))
    _set_output("errors", total_errors)

    print(
        f"Transformed {len(matched)} file(s): "
        f"{total_transforms} transforms applied, {total_errors} errors"
    )
    if strict and total_errors > 0:
        print(f"::error::GoldenFlow transforms reported {total_errors} error(s)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
