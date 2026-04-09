"""CLI for the Python benchmark runner."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click

from . import MANIFEST_VERSION, __version__ as RUNNER_VERSION
from .cases import Case, Expected, load_case
from .compare import compute_delta
from .manifest import CaseRef, load_manifest
from .report import build_report, write_report
from .runner import RunOptions, run_cases

REPO_ROOT = Path(__file__).resolve().parents[4]
BENCHMARK_ROOT = REPO_ROOT / "benchmark"
SELF_TEST_ROOT = BENCHMARK_ROOT / "self-test"


@click.group()
@click.option("--verbose", is_flag=True, help="Enable verbose logging")
def main(verbose: bool):
    """infermap-bench — Python benchmark runner."""
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(message)s")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@main.command()
@click.option("--output", default="python-report.json", help="Where to write the report")
@click.option("--seed", default=42, type=int, help="Seed (unused for loading, kept for symmetry)")
@click.option("--only", default=None, help="Filter cases by prefix or slice (e.g. category:valentine, difficulty:hard, tag:X)")
@click.option("--self-test", "self_test", is_flag=True, help="Run against self-test corpus instead of full benchmark")
@click.option("--assert-against", type=click.Path(exists=True), default=None,
              help="Expected scorecard file for self-test assertion")
def run(output: str, seed: int, only: str | None, self_test: bool, assert_against: str | None):
    """Run the benchmark and write a report.json."""
    import time
    import infermap

    root = SELF_TEST_ROOT if self_test else BENCHMARK_ROOT
    manifest_path = root / "manifest.json"

    if not manifest_path.exists():
        click.echo(f"manifest not found at {manifest_path}", err=True)
        refs: list[CaseRef] = []
    else:
        refs = load_manifest(manifest_path)
        if only:
            refs = [r for r in refs if _matches_filter(r, only)]

    cases: list[Case] = [load_case(root, ref) for ref in refs]

    # Synthetic cases (full benchmark only, not self-test)
    if not self_test:
        synth_path = BENCHMARK_ROOT / "cases" / "synthetic" / "generated.json"
        if synth_path.exists():
            cases.extend(_load_synthetic_cases(synth_path))
        else:
            click.echo(f"note: {synth_path} not found — skipping synthetic cases", err=True)

    # Refuse to write a "perfect" scorecard for an empty corpus — it would
    # poison baselines. An empty run is almost always a misconfiguration
    # (missing manifest, over-aggressive --only filter, etc.).
    if not cases:
        click.echo(
            "ERROR: no cases to run. Check manifest path and --only filter. "
            "Refusing to write a vacuous scorecard.",
            err=True,
        )
        raise SystemExit(2)

    t0 = time.perf_counter()
    results = run_cases(cases, RunOptions())
    duration = time.perf_counter() - t0

    report = build_report(
        results,
        language="python",
        infermap_version=infermap.__version__,
        runner_version=RUNNER_VERSION,
        duration_seconds=duration,
    )
    schema_path = BENCHMARK_ROOT / "report.schema.json"
    write_report(report, output, schema_path)
    click.echo(f"wrote {output} ({len(results)} cases)")

    if assert_against:
        expected = json.loads(Path(assert_against).read_text(encoding="utf-8"))
        _assert_scorecard_matches(report, expected)


# ---------------------------------------------------------------------------
# rebuild-manifest (stub until Phase 11)
# ---------------------------------------------------------------------------


@main.command("rebuild-manifest")
def rebuild_manifest():
    """Scan cases/ and rewrite manifest.json from each case.json. (Stub until Phase 11.)"""
    click.echo("rebuild-manifest: stub — will be implemented when the real corpus lands")


# ---------------------------------------------------------------------------
# regenerate-synthetic
# ---------------------------------------------------------------------------


@main.command("regenerate-synthetic")
def regenerate_synthetic():
    """Regenerate benchmark/cases/synthetic/generated.json from synthetic.config.json."""
    from .synthetic import load_synthetic_config, generate_all_synthetic, write_generated_json
    cfg = load_synthetic_config(BENCHMARK_ROOT / "synthetic.config.json")
    cases = list(generate_all_synthetic(cfg))
    output = BENCHMARK_ROOT / "cases" / "synthetic" / "generated.json"
    write_generated_json(cases, output)
    click.echo(f"wrote {len(cases)} cases to {output}")


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


@main.command()
@click.option("--baseline", type=click.Path(exists=True), required=True)
@click.option("--current", type=click.Path(exists=True), required=True)
def compare(baseline: str, current: str):
    """Compare two report.json files and print the delta."""
    base = json.loads(Path(baseline).read_text(encoding="utf-8"))
    curr = json.loads(Path(current).read_text(encoding="utf-8"))
    delta = compute_delta(base, curr)
    click.echo(f"F1 delta:    {delta.overall['f1']:+.4f}")
    click.echo(f"top-1 delta: {delta.overall['top1']:+.4f}")
    click.echo(f"MRR delta:   {delta.overall['mrr']:+.4f}")
    click.echo(f"ECE delta:   {delta.overall['ece']:+.4f}")
    click.echo(f"Regression (threshold 0.02): {delta.is_regression(0.02)}")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@main.command()
@click.argument("report_path", type=click.Path(exists=True))
def report(report_path: str):
    """Pretty-print a report.json to stdout."""
    data = json.loads(Path(report_path).read_text(encoding="utf-8"))
    sc = data["scorecard"]["overall"]
    click.echo(f"language: {data['language']}")
    click.echo(f"infermap: {data['infermap_version']}")
    click.echo(f"duration: {data['duration_seconds']}s")
    click.echo(f"cases:    {sc['n']}")
    click.echo(f"F1:       {sc['f1']:.4f}")
    click.echo(f"top-1:    {sc['top1']:.4f}")
    click.echo(f"MRR:      {sc['mrr']:.4f}")
    click.echo(f"ECE:      {sc['ece']:.4f}")


# ---------------------------------------------------------------------------
# migrate (stub per spec §6.6)
# ---------------------------------------------------------------------------


@main.command()
@click.option("--from", "from_version", type=int, required=True, help="Current manifest version")
@click.option("--to", "to_version", type=int, required=True, help="Target manifest version")
@click.option("--dry-run", is_flag=True, help="Report what would change without writing")
def migrate(from_version: int, to_version: int, dry_run: bool):
    """Migrate manifest.json / case.json / expected.json between contract versions.

    Stubbed for v1: only supports from=1 to=1 (no-op). Future version bumps
    will land real migration steps here per spec §6.6.
    """
    if from_version == to_version == MANIFEST_VERSION:
        click.echo(f"No migration needed — already at version {MANIFEST_VERSION}")
        return
    if to_version > MANIFEST_VERSION:
        click.echo(
            f"ERROR: target version {to_version} exceeds what this runner supports "
            f"({MANIFEST_VERSION}). Upgrade infermap-bench first.",
            err=True,
        )
        raise click.Abort()
    raise NotImplementedError(
        f"Migration from v{from_version} to v{to_version} is not implemented. "
        f"Add a migration step in infermap_bench/cli.py::migrate per spec §6.6."
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _matches_filter(ref: CaseRef, filter_str: str) -> bool:
    """Match a CaseRef against an --only filter string."""
    if ":" not in filter_str:
        return ref.id.startswith(filter_str)
    key, value = filter_str.split(":", 1)
    if key == "category":
        return ref.category == value
    if key == "difficulty":
        return ref.expected_difficulty == value
    if key == "tag":
        return value in ref.tags
    return False


def _load_synthetic_cases(path: Path) -> list[Case]:
    """Load committed synthetic cases from generated.json into Case objects.

    Sets value_count = len(sample_values) on every field so ProfileScorer
    contributes to scoring (its gate is `source.value_count == 0 or
    target.value_count == 0 → abstain`). Without this, synthetic cases
    would measure with 2 of 6 scorers silently excluded.
    """
    from infermap.types import FieldInfo, SchemaInfo

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: list[Case] = []
    for entry in data["cases"]:
        src_fields = [
            FieldInfo(
                name=f["name"],
                dtype=f["dtype"],
                sample_values=list(f["samples"]),
                value_count=len(f["samples"]),
            )
            for f in entry["source_fields"]
        ]
        tgt_fields = [
            FieldInfo(
                name=f["name"],
                dtype=f["dtype"],
                sample_values=list(f["samples"]),
                value_count=len(f["samples"]),
            )
            for f in entry["target_fields"]
        ]
        out.append(Case(
            id=entry["id"],
            category=entry["category"],
            subcategory=entry["subcategory"],
            tags=list(entry["tags"]),
            expected_difficulty=entry["expected_difficulty"],
            source_schema=SchemaInfo(fields=src_fields),
            target_schema=SchemaInfo(fields=tgt_fields),
            expected=Expected(
                mappings=list(entry["expected"]["mappings"]),
                unmapped_source=list(entry["expected"]["unmapped_source"]),
                unmapped_target=list(entry["expected"]["unmapped_target"]),
            ),
        ))
    return out


def _assert_scorecard_matches(actual: dict, expected: dict, tolerance: float = 1e-4) -> None:
    """Compare the `scorecard.overall` of two reports within tolerance.

    Tolerance is intentionally 1e-4 (not tighter) because the committed
    expected_self_test.json stores metrics rounded to 6 decimal places
    (e.g. f1=0.857143 which is 6/7 rounded). A 1e-6 tolerance would trip on
    that rounding. If the expected file is ever regenerated at higher
    precision, this can be tightened.
    """
    a = actual["scorecard"]["overall"]
    e = expected["scorecard"]["overall"]
    for key in ("f1", "top1", "mrr", "ece"):
        if abs(a[key] - e[key]) > tolerance:
            click.echo(f"MISMATCH on {key}: actual={a[key]} expected={e[key]}", err=True)
            sys.exit(1)
    click.echo("scorecard matches expected (within tolerance)")
