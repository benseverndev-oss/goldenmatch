#!/usr/bin/env python3
"""Validate every GitHub Actions workflow: it parses, and it has no duplicate keys.

WHY THIS EXISTS
---------------
Two holes, both found the hard way.

1. DUPLICATE KEYS PARSE CLEAN AND SILENTLY WIN LAST. Inserting an `if:` above a
   step that already had one produces a mapping with two `if` keys. `yaml.safe_load`
   keeps the LAST one and reports no error, so the guard you just added is inert
   while every syntax check stays green. That happened to `bench-er-kg.yml`'s
   demo-upload step; it was caught by printing the parsed value back by hand, not
   by CI. GitHub Actions itself does not reject it either.

2. THE LINT ONLY RAN FOR THREE FILES. `workflow_lint` was gated on the
   `ci_workflow` path filter, which lists exactly `ci.yml`, `filters.yml` and
   `check_filter_coverage.py`. The repo has ~118 workflow files, so editing any
   other one -- a `publish-*`, a `bench-*`, the six nightly lanes -- ran no YAML
   validation at all. The check existed and did not fire, which is the same class
   of bug as the path-filter hole in #1846 and the un-run consumer lanes in #2440.

`ci_workflow` is deliberately NOT widened to fix (2): it also drives `force_all`
in the merge queue, where widening it would make every workflow edit re-run the
full matrix. The lint gets its own `any_workflow` filter instead.

FAIL (exit 1) on a parse error or a duplicate key. Exit 2 if the scan itself looks
broken -- a check that silently inspects nothing must not look like a passing one.

Run from the repo root:  python scripts/check_workflow_yaml.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW_DIR = ROOT / ".github" / "workflows"

# The repo has ~118 workflows. If a scan turns up fewer than this, the glob or the
# directory moved -- report broken rather than clean.
MIN_EXPECTED_WORKFLOWS = 20


class DuplicateKeyError(Exception):
    """A mapping in the document defines the same key twice."""

    def __init__(self, key: object, line: int) -> None:
        super().__init__(f"duplicate key {key!r} at line {line}")
        self.key = key
        self.line = line


class StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys instead of keeping the last."""


def _no_duplicates(loader: yaml.Loader, node: yaml.MappingNode, deep: bool = False):
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            # +1 because yaml marks are 0-indexed and editors are not.
            raise DuplicateKeyError(key, key_node.start_mark.line + 1)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates
)


def workflow_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        p for p in directory.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file()
    )


def check(directory: pathlib.Path) -> tuple[list[tuple[pathlib.Path, str]], int]:
    """Return (problems, files_scanned)."""
    problems: list[tuple[pathlib.Path, str]] = []
    files = workflow_files(directory)
    for path in files:
        try:
            yaml.load(path.read_text(encoding="utf-8"), Loader=StrictLoader)
        except DuplicateKeyError as exc:
            problems.append(
                (
                    path,
                    f"line {exc.line}: duplicate key `{exc.key}` -- YAML keeps the LAST "
                    f"one, so the earlier value is silently discarded",
                )
            )
        except yaml.YAMLError as exc:
            problems.append((path, f"does not parse: {exc}"))
    return problems, len(files)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dir",
        type=pathlib.Path,
        default=WORKFLOW_DIR,
        help="workflow directory to scan (default: .github/workflows)",
    )
    ap.add_argument(
        "--min-files",
        type=int,
        default=MIN_EXPECTED_WORKFLOWS,
        help="fail as broken if fewer than this many workflows are found",
    )
    args = ap.parse_args(argv)

    if not args.dir.is_dir():
        print(f"::error::{args.dir} is not a directory -- the scan is broken", file=sys.stderr)
        return 2

    problems, scanned = check(args.dir)

    if scanned < args.min_files:
        print(
            f"::error::only {scanned} workflow file(s) found under {args.dir} "
            f"(expected at least {args.min_files}) -- the scan is broken",
            file=sys.stderr,
        )
        return 2

    print(f"workflow YAML: {scanned} file(s) scanned for parse errors + duplicate keys")
    if problems:
        for path, reason in problems:
            rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
            print(f"::error file={rel}::{rel}: {reason}", file=sys.stderr)
        print(f"\n{len(problems)} workflow file(s) with problems.", file=sys.stderr)
        return 1
    print("  OK -- all parse, none define a key twice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
