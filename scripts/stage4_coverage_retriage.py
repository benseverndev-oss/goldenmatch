#!/usr/bin/env python
"""Stage 4 harness: pull real per-test coverage data from a CI run, diff the
sync-claims report against today's text-only baseline, and flag the
[tool.coverage.run].omit interpretation trap (a claim in an omitted module
reads as "coverage found nothing" when the truth is "coverage never looked").

Committed rather than left in a scratchpad -- an audit finding's numbers must
be reproducible from the harness that produced them, not just pasted into a
doc. See docs/superpowers/specs/2026-09-*-stage4-coverage-retriage.md for the
write-up this feeds, and docs/superpowers/specs/2026-09-03-coverage-based-
enforcement-design.md (Stage 4) for what this is answering.

Usage:
    uv run python scripts/stage4_coverage_retriage.py --run-id <RUN_ID>

`RUN_ID` is a GitHub Actions run ID (from `gh run list` or a PR's checks)
whose python_goldenmatch/python_goldenmatch_heavy jobs actually ran (i.e. the
commit touched goldenmatch code) and produced gm-cov-* artifacts. Requires
the `gh` CLI, authenticated against benseverndev-oss/goldenmatch (ghx, per
this repo's CLAUDE.md -- never `gh auth switch`).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDENMATCH = REPO / "packages" / "python" / "goldenmatch"
GOLDENMATCH_PYPROJECT = GOLDENMATCH / "pyproject.toml"

# The same 6 files sync_claims's own CI combine step lists -- mirrored here so
# this harness measures the identical population the CI job would, not a
# guess at what "the coverage data" means. See .github/workflows/ci.yml,
# the sync_claims job's "Combine coverage contexts" step.
SHARD_FILES = [
    "coverage_shard1.dat",
    "coverage_shard2.dat",
    "coverage_shard3.dat",
    "coverage_heavy_1.dat",
    "coverage_heavy_2.dat",
    "coverage_heavy_3.dat",
]


def download_artifacts(run_id: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "gh",
            "run",
            "download",
            run_id,
            "--repo",
            "benseverndev-oss/goldenmatch",
            "--pattern",
            "gm-cov-*",
            "--dir",
            str(dest),
        ],
        check=True,
    )


def combine(download_dir: Path, out_coverage: Path) -> None:
    """`coverage combine` writes `.coverage` to CWD, not next to --rcfile --
    confirmed by hand during the branch that built this mechanism (a real
    gotcha, not documentation). cwd is set to `download_dir` here so the
    combined file lands somewhere predictable to move out of."""
    found: dict[str, Path] = {}
    for name in SHARD_FILES:
        matches = list(download_dir.rglob(name))
        if not matches:
            raise SystemExit(
                f"missing {name} under {download_dir} -- did the shard/heavy "
                f"jobs actually run on this commit? (they only run when the "
                f"commit touches packages/python/goldenmatch/goldenmatch/**)"
            )
        found[name] = matches[0]

    if out_coverage.exists():
        out_coverage.unlink()
    subprocess.run(
        [
            "uv",
            "run",
            "coverage",
            "combine",
            f"--rcfile={GOLDENMATCH_PYPROJECT}",
            *[str(found[name]) for name in SHARD_FILES],
        ],
        check=True,
        cwd=str(download_dir),
    )
    combined = download_dir / ".coverage"
    shutil.move(str(combined), str(out_coverage))


def run_report(coverage_db: Path | None) -> dict:
    cmd = ["uv", "run", "python", "-m", "sync_claims.report", "--json"]
    if coverage_db is not None:
        cmd += ["--coverage-db", str(coverage_db)]
    result = subprocess.run(
        cmd,
        cwd=str(REPO),
        # os.environ spreads FIRST -- PYTHONPATH must win over whatever (if
        # anything) is already set, not the other way around.
        env={**os.environ, "PYTHONPATH": "scripts"},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def omitted_modules() -> list[str]:
    data = tomllib.loads(GOLDENMATCH_PYPROJECT.read_text(encoding="utf-8"))
    return data.get("tool", {}).get("coverage", {}).get("run", {}).get("omit", [])


def _matches_omit(path: str, patterns: list[str]) -> bool:
    if not path:
        return False
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)


def diff(text_only: dict, with_coverage: dict, omit_patterns: list[str]) -> dict:
    """text_only and with_coverage are both inventory()'s --json output over
    the SAME population -- the only difference is whether coverage_db was
    passed. A claim moves from "unenforced" in text_only to absent from
    with_coverage's "unenforced" (and present in its "coverage_enforced")
    when the mechanism rescued it."""
    text_ids = {(c["module"], c["symbol"], c["lineno"]) for c in text_only["unenforced"]}
    still_ids = {(c["module"], c["symbol"], c["lineno"]) for c in with_coverage["unenforced"]}
    rescued_ids = text_ids - still_ids

    rescued = [
        c for c in text_only["unenforced"] if (c["module"], c["symbol"], c["lineno"]) in rescued_ids
    ]
    still_unenforced = with_coverage["unenforced"]

    omit_flagged = [
        c
        for c in still_unenforced
        if _matches_omit(c["module"], omit_patterns)
        or _matches_omit(c["target"] or "", omit_patterns)
    ]

    return {
        "text_only_unenforced_count": len(text_only["unenforced"]),
        "coverage_rescued_count": len(rescued),
        "still_unenforced_count": len(still_unenforced),
        "still_unenforced_flagged_omit_scope": len(omit_flagged),
        "coverage_consulted": with_coverage["counts"].get("coverage_consulted"),
        "coverage_functions_with_data": with_coverage["counts"].get("coverage_functions_with_data"),
        "rescued": rescued,
        "still_unenforced": still_unenforced,
        "omit_flagged": omit_flagged,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id", required=True, help="GitHub Actions run ID with gm-cov-* artifacts"
    )
    parser.add_argument("--out-dir", type=Path, default=REPO / ".stage4-scratch")
    args = parser.parse_args(argv)

    download_dir = args.out_dir / "artifacts"
    coverage_db = args.out_dir / "combined.coverage"

    print(f"Downloading gm-cov-* artifacts from run {args.run_id}...")
    download_artifacts(args.run_id, download_dir)

    print("Combining shard + heavy coverage data (mirrors sync_claims's own CI step)...")
    combine(download_dir, coverage_db)

    print("Running text-only baseline report...")
    text_only = run_report(None)

    print("Running coverage-enforced report...")
    with_coverage = run_report(coverage_db)

    omit_patterns = omitted_modules()
    result = diff(text_only, with_coverage, omit_patterns)

    result_path = args.out_dir / "stage4_diff.json"
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"\n{result['text_only_unenforced_count']} high-confidence findings, text-only baseline")
    print(f"{result['coverage_rescued_count']} rescued by coverage")
    print(f"{result['still_unenforced_count']} still unenforced")
    print(
        f"  of which {result['still_unenforced_flagged_omit_scope']} fall inside "
        f"[tool.coverage.run].omit -- coverage never looked there, not a genuine negative"
    )
    print(
        f"coverage_consulted: {result['coverage_consulted']}, "
        f"coverage_functions_with_data: {result['coverage_functions_with_data']}"
    )
    print(f"\nFull diff written to {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
