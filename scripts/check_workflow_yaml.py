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
import re
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


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


def workflow_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in directory.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file())


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
            continue
        problems.extend(_bad_run_scripts(path))
    return problems, len(files)


def _bad_run_scripts(path: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """Every ``run:`` block must be syntactically valid bash.

    A workflow can parse as YAML and still contain a shell script that cannot
    run, because the two layers escape differently and a `run: |` block has to
    survive BOTH.

    Two checks, because the most common failure is NOT a syntax error:

    1. ``bash -n`` for genuine syntax errors (unbalanced quotes, unterminated
       heredocs -- the latter being easy to produce, since a heredoc terminator
       must land at column 0 only AFTER YAML strips the block indent).

    2. A mid-line literal ``\\n``, which is the shape that actually keeps
       happening: a line continuation collapses into the two characters
       backslash-n, and bash receives ``n`` as an argument.

           ERROR: file or directory not found: n

       ``bash -n`` reports that as VALID, because ``\\n`` is a perfectly legal
       escaped character -- so syntax checking alone would have caught none of
       the three times this repo has paid for it (a bench Compare step, and
       twice in the Spark cluster lane), each costing a full CI round trip.

    The ``\\n`` rule requires whitespace on BOTH sides, which is what a mangled
    continuation looks like (`` \\n            --deselect``). A deliberate
    escape is almost always adjacent to non-space (``f"{a}\\nb"``,
    ``printf 'x\\ny'``) and is not flagged.

    Nothing is executed. ``${{ }}`` expressions are left alone -- GitHub
    substitutes them before bash sees them.
    """
    import shutil
    import subprocess
    import tempfile

    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - CI is Linux and dev is git-bash
        return []

    out: list[tuple[pathlib.Path, str]] = []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return []
    if not isinstance(doc, dict):
        return []

    for job_name, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for i, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict) or "run" not in step:
                continue
            script = step["run"]
            if not isinstance(script, str):
                continue
            # A step can select another shell; only bash is checkable here.
            shell = str(
                step.get("shell") or job.get("defaults", {}).get("run", {}).get("shell") or "bash"
            )
            if not shell.startswith("bash") and shell != "sh":
                continue
            with tempfile.NamedTemporaryFile(
                "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
            ) as fh:
                fh.write(script)
                tmp = fh.name
            try:
                proc = subprocess.run([bash, "-n", tmp], capture_output=True, text=True)
            finally:
                pathlib.Path(tmp).unlink(missing_ok=True)
            label = step.get("name") or f"step {i}"
            if proc.returncode != 0:
                detail = (proc.stderr or "").strip().replace(tmp, "<step>")
                out.append(
                    (
                        path,
                        f"job `{job_name}` step `{label}`: `run:` is not valid bash -- {detail}",
                    )
                )
            for lineno, line in enumerate(script.splitlines(), 1):
                m = _MANGLED_CONTINUATION.search(line)
                if m:
                    out.append(
                        (
                            path,
                            f"job `{job_name}` step `{label}` line {lineno}: a literal "
                            f"`\\n` with whitespace on both sides -- almost certainly a "
                            f"line continuation that collapsed, so bash gets `n` as an "
                            f"argument. `bash -n` calls this VALID. In: "
                            f"{line.strip()[:70]!r}",
                        )
                    )
    return out


#: Backslash-n with whitespace both sides: a collapsed line continuation.
#: A deliberate escape sits next to non-space (``f"{a}\nb"``) and is not matched.
_MANGLED_CONTINUATION = re.compile(r"\s\\n\s")


def _backticks_in_remote_commands(path: pathlib.Path) -> list[str]:
    """Backticks inside an ssh ``--command "..."`` string, which the RUNNER runs.

    Everything between ``--command "`` and its closing quote is a double-quoted
    string evaluated on the RUNNER, so backticks and ``$( )`` inside it are
    command substitutions. That INCLUDES lines beginning with ``#``, because the
    comment marker means nothing to the runner's string parsing.

    Run 32312785745 died on a comment written to explain an earlier failure. It
    quoted a pip invocation in backticks; the runner executed it and spliced
    pip's stdout into the command sent to the remote host, which then tried to
    run ``Collecting`` and exited 127.

    The insidious part is that most such mistakes are SILENT. A backticked
    ``sc://`` substitutes to nothing and only prints to stderr, so the same
    block had carried backticks for months without incident. Only a
    substitution that SUCCEEDS corrupts the command, which is why this cannot be
    caught by "it worked last time" and is worth a gate.

    Detection tracks QUOTE STATE rather than line shapes. A first attempt
    matched the closing quote as a line of its own, which silently leaked: real
    blocks here also close with ``" &`` and ``" || true``, so the scanner stayed
    "inside" for hundreds of lines and flagged ordinary runner-side comments.
    A gate with false positives is worse than no gate.
    """
    problems: list[str] = []
    inside = False
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not inside:
            m = re.search(r'--command "\s*$', line)
            if m:
                inside = True
            continue

        # Walk the line and close on the first UNESCAPED double quote.
        closed_at = None
        k = 0
        while k < len(line):
            c = line[k]
            if c == "\\":
                k += 2
                continue
            if c == '"':
                closed_at = k
                break
            k += 1

        segment = line if closed_at is None else line[:closed_at]
        if "`" in segment:
            problems.append(
                f"line {n}: backtick inside an ssh --command string. The RUNNER "
                f"substitutes it, even in a # comment: {line.strip()[:80]}"
            )
        if closed_at is not None:
            inside = False
    return problems


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

    for wf in sorted(pathlib.Path(args.dir).glob("*.yml")):
        for problem in _backticks_in_remote_commands(wf):
            problems.append((wf, problem))

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
