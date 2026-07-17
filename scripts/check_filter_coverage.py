"""Assert CI path filters cover the surfaces they claim to gate (#1846).

A path-filtered job can only catch what its filter matches. When a filter
lists fewer paths than the job actually gates, the job goes SILENT on exactly
the changes it exists to catch -- and the failure surfaces later, on unrelated
PRs that happen to touch a listed path, which reads as "that PR broke it".

That happened: `quality_gate` pins f1 and f1_probabilistic in its scorecard but
did not list `core/probabilistic.py`. It was skipped on #1829 / #1834 / #1836 /
#1840 -- every recent PR able to move those numbers -- and historical_50k
f1_probabilistic fell 0.83 -> 0.33 on the native path with nothing to catch it.

`workflow_lint` only checks that the YAML parses. This checks that it MEANS
something. Same spirit as #435 (benchmark_runner had the identical hole).

Run: python scripts/check_filter_coverage.py
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import yaml

FILTERS = Path(__file__).resolve().parent.parent / ".github" / "filters.yml"

GM = "packages/python/goldenmatch/goldenmatch"

# filter name -> paths that MUST trigger it, with why. Each entry is a real
# regression or a real near-miss, not a hypothetical.
REQUIRED: dict[str, list[tuple[str, str]]] = {
    "quality_gate": [
        (f"{GM}/core/probabilistic.py", "scorecard pins f1_probabilistic (#1834, #1836)"),
        (f"{GM}/core/fused_match.py", "FS scoring path (#1834)"),
        (f"{GM}/backends/score_buckets.py", "native-on planner routes here (#1829)"),
        (f"{GM}/core/learned_blocking.py", "blocking; sibling of blocker.py (#1840, #1841)"),
        (f"{GM}/core/autoconfig.py", "the config decisions the scorecard pins"),
        (f"{GM}/core/blocker.py", "blocking fields/cost signals"),
        (f"{GM}/core/scorer.py", "scorecard pins f1"),
        (f"{GM}/core/cluster.py", "clusters decide the measured pairs"),
        (f"{GM}/core/pipeline.py", "routing picks WHICH scorer runs"),
        ("packages/rust/extensions/native/src/lib.rs", "baseline is blessed native-on"),
        ("scripts/autoconfig_quality/datasets.py", "the harness itself"),
        (".github/filters.yml", "self-test: filter edits must re-run the gate"),
    ],
    "benchmark_runner": [
        (f"{GM}/core/probabilistic.py", "#435: the library being benchmarked"),
        (f"{GM}/core/scorer.py", "#435"),
        (f"{GM}/core/pipeline.py", "#435"),
    ],
}

# Paths that must NOT trigger these filters -- guards against "fix" by
# over-matching, which turns a gate into a tax on every PR.
FORBIDDEN: dict[str, list[str]] = {
    "quality_gate": [
        "README.md",
        "docs/design/foo.md",
        "packages/typescript/goldenmatch/src/cli.ts",
    ],
}


def _matches(path: str, patterns: list[str]) -> str | None:
    """Approximate dorny/paths-filter: globs are unanchored, ** spans dirs."""
    for p in patterns:
        if fnmatch.fnmatch(path, p) or fnmatch.fnmatch(path, p.replace("**", "*")):
            return p
    return None


def main() -> int:
    spec = yaml.safe_load(FILTERS.read_text(encoding="utf-8"))
    failures: list[str] = []

    for name, cases in REQUIRED.items():
        pats = spec.get(name)
        if not pats:
            failures.append(f"{name}: filter missing entirely")
            continue
        for path, why in cases:
            if _matches(path, pats) is None:
                failures.append(
                    f"{name}: does NOT match {path}\n"
                    f"      why it must: {why}\n"
                    f"      -> add a pattern to `{name}:` in .github/filters.yml"
                )

    for name, paths in FORBIDDEN.items():
        pats = spec.get(name) or []
        for path in paths:
            hit = _matches(path, pats)
            if hit is not None:
                failures.append(
                    f"{name}: matches {path} via '{hit}' -- too broad; the gate "
                    f"would run on unrelated PRs"
                )

    if failures:
        print("CI filter coverage FAILED:\n")
        for f in failures:
            print(f"  - {f}")
        print(
            "\nA path-filtered job cannot catch what its filter does not match.\n"
            "If a file can move a number a gate pins, the filter must list it."
        )
        return 1

    total = sum(len(v) for v in REQUIRED.values())
    print(f"CI filter coverage OK ({total} required paths across {len(REQUIRED)} filters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
