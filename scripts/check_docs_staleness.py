#!/usr/bin/env python3
"""Tier-2 documentation-staleness advisory: diff-aware doc-drift detector.

Given a git diff range (default ``origin/main..HEAD``), apply a small set of
HIGH-signal, LOW-false-positive rules that catch the most common "code changed
but its doc surface didn't" drift. Designed to run as an ADVISORY CI job
(``continue-on-error: true``) so it surfaces warnings/annotations without ever
blocking a clean PR -- with ONE exception that is high-signal enough to gate.

Rules
-----
1. flag rule (GATING):
   For every suite package that declares a ``prose_flag_page`` in
   ``scripts/config_matrix/registry.py``: if the diff adds or removes a
   ``<ENV_PREFIX>[A-Z0-9_]+`` env flag in ``packages/python/**/*.py``, that page
   MUST also be in the diff. If not -> ``::error::`` annotation + exit 1.
   Today only goldenmatch declares one (``docs-site/goldenmatch/tuning.mdx``);
   the other packages are N/A by design, because their GENERATED config-matrix
   block already documents every introspected knob and ``docs_regen`` gates it.
   Giving a package a tuning page is a one-line registry change, not a code
   change here.
   To stay false-positive-free, two classes of non-drift are excluded before
   gating: (a) test files (``**/tests/**``, ``test_*.py``, ``*_test.py``,
   ``conftest.py``) -- a flag *mention* in a test exercises an existing flag,
   it does not declare one; (b) flags already in sync with tuning.mdx -- an
   added flag already documented there, or a removed flag already absent from
   it, has nothing left to update.

2. public-symbol rule (ADVISORY):
   If the diff changes a package ``__init__.py`` ``__all__`` / re-export and NO
   doc surface is touched (``docs-site/``, any ``README.md``, ``CHANGELOG.md``,
   ``llms.txt``, ``context-network/``) -> ``::warning::`` only (never fails).

Only the flag rule can change the exit code. Everything else is informational.
``--rule flag`` / ``--rule symbol`` select one rule so CI can run the gating half
as a BLOCKING step and the advisory half as a ``continue-on-error`` one; the
default ``--rule all`` keeps the single-command local behaviour.

Run: ``python scripts/check_docs_staleness.py [--base <ref>] [--head <ref>] [--rule all|flag|symbol]``
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

# The per-package (env prefix -> canonical prose flag page) roster. Imported from
# the config-matrix registry so there is ONE definition of the suite's env
# prefixes, not a second hardcoded copy here. `config_matrix.registry` is pure
# stdlib (the pydantic-dependent render half is lazily re-exported), so this stays
# runnable on a bare setup-python runner.
from config_matrix.registry import REGISTRY  # noqa: E402  (needs the sys.path line above)

# (package, compiled <PREFIX>[A-Z0-9_]+ matcher, prose page) for every package
# that HAS a hand-written flag reference. Packages without one are N/A: their
# generated config-matrix block covers the knob and docs_regen gates it.
FLAG_SPECS: list[tuple[str, re.Pattern[str], str]] = [
    (spec.name, re.compile(spec.env_prefix + r"[A-Z0-9_]+"), spec.prose_flag_page)
    for spec in REGISTRY.values()
    if spec.prose_flag_page
]

_ALL_LINE_RE = re.compile(r"__all__")


def _git(*args: str) -> str:
    # encoding= is load-bearing, not decoration. text=True decodes with the
    # locale default -- cp1252 on Windows -- and tuning.mdx carries non-ASCII
    # punctuation it cannot decode. That decode raises inside subprocess's
    # reader thread, so stdout arrives as None with returncode 0: this function
    # then returns None WITHOUT raising and the caller dies on None. Pinning
    # utf-8 keeps the failure mode honest on every platform.
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _is_test_file(path: str) -> bool:
    """A flag *mention* in a test (``monkeypatch.setenv``, ``os.environ[...]``)
    is exercising an existing flag, not declaring a new one. Test files are
    therefore excluded from the flag-drift scan -- only non-test source can
    introduce/remove the canonical flag surface."""
    name = path.rsplit("/", 1)[-1]
    return (
        "/tests/" in path
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def _documented_flags(head: str, page: str, flag_re: re.Pattern[str]) -> set[str]:
    """Flags matching ``flag_re`` already present in ``page`` at ``head``.

    Used to suppress false positives: a flag that is *already documented* has no
    drift to fix, even if a non-test source line happens to reference it."""
    try:
        content = _git("show", f"{head}:{page}")
    except RuntimeError:
        return set()  # page absent at head -> nothing documented yet
    return set(flag_re.findall(content))


def changed_files(base: str, head: str) -> list[str]:
    out = _git("diff", "--name-only", f"{base}...{head}")
    return [ln for ln in out.splitlines() if ln.strip()]


def diff_for(base: str, head: str, pathspec: list[str]) -> str:
    return _git("diff", "--unified=0", f"{base}...{head}", "--", *pathspec)


def _added_removed_flags(diff_text: str, flag_re: re.Pattern[str]) -> tuple[set[str], set[str]]:
    """Flags appearing on added (+) vs removed (-) diff lines.

    A flag is considered *introduced/removed* only if it nets out: a flag that
    moves within a file (present on both + and - lines) is not drift.
    """
    added: set[str] = set()
    removed: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.update(flag_re.findall(line))
        elif line.startswith("-"):
            removed.update(flag_re.findall(line))
    net_added = added - removed
    net_removed = removed - added
    return net_added, net_removed


def check_flag_rule(base: str, head: str, files: list[str]) -> tuple[bool, list[str]]:
    """Return (ok, messages). ok=False means gate failure.

    Runs once per package that declares a ``prose_flag_page``. The source scan is
    repo-wide across non-test ``packages/python/**/*.py`` rather than scoped to
    the owning package's tree: a ``GOLDENCHECK_*`` flag introduced from
    goldenmatch's directory is still goldencheck's documented surface, and the
    prefix is what identifies the owner.
    """
    py_files = [
        f
        for f in files
        if f.startswith("packages/python/") and f.endswith(".py") and not _is_test_file(f)
    ]
    if not py_files:
        return True, ["flag rule: no non-test packages/python/**/*.py changes -- skipped"]
    if not FLAG_SPECS:
        return True, ["flag rule: no package declares a prose_flag_page -- N/A"]

    diff_text = diff_for(base, head, py_files)
    ok = True
    msgs: list[str] = []

    for pkg, flag_re, page in FLAG_SPECS:
        net_added, net_removed = _added_removed_flags(diff_text, flag_re)

        # Suppress flags that have no actual doc drift:
        #  - an *added* flag already present in the page is already documented;
        #  - a *removed* flag absent from the page is already undocumented.
        # Only the complement is real drift the canonical reference must track.
        documented = _documented_flags(head, page, flag_re)
        drift_added = net_added - documented
        drift_removed = net_removed & documented
        touched_flags = sorted(drift_added | drift_removed)
        if not touched_flags:
            msgs.append(
                f"flag rule [{pkg}]: no flag drift "
                f"(changes are tests, or flags already in sync with {page}) -- OK"
            )
            continue

        if page in files:
            msgs.append(
                f"flag rule [{pkg}]: flags changed ({touched_flags}) and {page} "
                "is in the diff -- OK"
            )
            continue

        # Drift: flag changed but the canonical page untouched.
        ok = False
        msgs.append(
            f"::error file={page}::{pkg}: env flag(s) {touched_flags} added/removed "
            f"in this diff but {page} (the canonical flag reference) was not "
            "updated. Add/remove the flag there in the same PR."
        )

    return ok, msgs


def check_public_symbol_rule(base: str, head: str, files: list[str]) -> list[str]:
    """Advisory only. Return ::warning:: messages (never affects exit code)."""
    init_files = [f for f in files if f.startswith("packages/") and f.endswith("__init__.py")]
    if not init_files:
        return ["public-symbol rule: no __init__.py changes -- skipped"]

    diff_text = diff_for(base, head, init_files)
    changes_all = any(
        _ALL_LINE_RE.search(ln)
        for ln in diff_text.splitlines()
        if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
    )
    if not changes_all:
        return ["public-symbol rule: no __all__/re-export lines changed -- skipped"]

    doc_touched = any(
        f.startswith("docs-site/")
        or f.endswith("README.md")
        or f.endswith("CHANGELOG.md")
        or f.endswith("llms.txt")
        or f.startswith("context-network/")
        for f in files
    )
    if doc_touched:
        return ["public-symbol rule: __all__ changed and a doc surface was touched -- OK"]
    return [
        "::warning::A package __all__/re-export changed but no doc surface "
        "(docs-site/, README.md, CHANGELOG.md, llms.txt, context-network/) was "
        "updated. Public-API changes usually need a doc note. (advisory only)"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="Base ref (default origin/main).")
    parser.add_argument("--head", default="HEAD", help="Head ref (default HEAD).")
    parser.add_argument(
        "--rule",
        choices=("all", "flag", "symbol"),
        default="all",
        help="Which rule to run. CI runs 'flag' as a BLOCKING step and 'symbol' as a "
             "continue-on-error one; 'all' (default) is the local single command.",
    )
    args = parser.parse_args(argv)

    try:
        files = changed_files(args.base, args.head)
    except RuntimeError as exc:
        print(f"::warning::docs-staleness: could not compute diff ({exc}); skipping.")
        return 0

    print(f"Docs staleness [{args.rule}]: {args.base}...{args.head} "
          f"({len(files)} changed file(s))")

    gate_ok = True
    msgs: list[str] = []
    if args.rule in ("all", "flag"):
        gate_ok, flag_msgs = check_flag_rule(args.base, args.head, files)
        msgs += flag_msgs
    if args.rule in ("all", "symbol"):
        msgs += check_public_symbol_rule(args.base, args.head, files)

    for m in msgs:
        print(m)

    if not gate_ok:
        pages = sorted({page for _, _, page in FLAG_SPECS})
        print(f"\nDocs staleness FAILED (flag rule). Update the canonical flag "
              f"reference ({', '.join(pages)}) in the same PR.")
        return 1

    if args.rule == "symbol":
        print("\nDocs staleness OK (symbol rule is advisory; warnings above, if any).")
    else:
        print("\nDocs staleness OK (gating flag rule passed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
