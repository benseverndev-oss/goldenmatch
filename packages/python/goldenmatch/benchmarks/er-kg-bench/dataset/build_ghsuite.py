"""Build records_ghsuite.csv from the curated concepts.jsonl via an injected search backend.

Mirrors build_real.py's structure but is split into two layers:

* Pure record-assembly core (``assemble_records``).  No I/O, no network, no
  ripgrep.  The search backend is injected so the core is fully unit-testable
  without any external calls.
* Real search backends (``_ripgrep_search``, ``_gh_issue_search``,
  ``make_search_fn``) and a CLI ``main()`` that wires them up and writes
  records_ghsuite.csv.

The CSV row schema (shared with the bench harness via FIELDNAMES) is:

    record_id, mention, entity_type, context, entity_id, failure_class, source

Usage::

    python dataset/build_ghsuite.py            # build records_ghsuite.csv
    python dataset/build_ghsuite.py --dry-run  # print keep/drop table, no write
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from dataset.concepts_loader import Concept, load_concepts  # pyright: ignore[reportMissingImports]

# ---------------------------------------------------------------------------
# Repo-relative paths (derived from __file__, never hardcoded)
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent  # dataset/
_REPO_ROOT = Path(__file__).resolve().parents[6]  # worktree root

# SQL extensions now live in-monorepo (the standalone goldenmatch-extensions
# repo was archived + folded in). Optional; skip silently when absent.
_EXTENSIONS_DIR = _REPO_ROOT / "packages" / "rust" / "extensions"

# GitHub repos to search for issues/PRs when the local checkouts miss.
_DEFAULT_REPOS = [
    "benseverndev-oss/goldenmatch",
]

# The bench's own dir would self-match (concepts.jsonl lists every surface
# verbatim), so exclude it from git grep via a pathspec.
_BENCH_EXCLUDE_PATHSPEC = ":(exclude,glob)**/er-kg-bench/**"

FIELDNAMES = [
    "record_id",
    "mention",
    "entity_type",
    "context",
    "entity_id",
    "failure_class",
    "source",
]

# ---------------------------------------------------------------------------
# Pure record-assembly core (no I/O, injected search_fn)
# ---------------------------------------------------------------------------


def assemble_records(
    concepts: list[Concept],
    search_fn: Callable[[str], tuple[bool, str | None]],
    start_id: int = 0,
) -> list[dict]:
    """Assemble bench row dicts from *concepts* using an injected *search_fn*.

    For each concept, iterates variants in order and:
    * Skips any surface already emitted for this concept (dedup within concept).
    * Calls ``search_fn(surface)`` -> ``(found, provenance)``.
    * Drops the variant when ``found`` is False.
    * Otherwise appends a row dict and increments the running record_id.

    Args:
        concepts:   List of ``Concept`` objects from ``concepts_loader``.
        search_fn:  Injected callable ``(surface: str) -> (found: bool, provenance: str | None)``.
                    Must be pure from this function's perspective (no side-effects observed here).
        start_id:   First ``record_id`` value (default 0).

    Returns:
        List of row dicts whose keys match ``FIELDNAMES``.  Pure -- no I/O.
    """
    rows: list[dict] = []
    rid = start_id

    for concept in concepts:
        seen: set[str] = set()
        for variant in concept.variants:
            surface = variant.surface
            if surface in seen:
                continue
            found, prov = search_fn(surface)
            if not found:
                continue
            rows.append(
                {
                    "record_id": rid,
                    "mention": surface,
                    "entity_type": concept.entity_type,
                    "context": concept.context,
                    "entity_id": concept.canonical_id,
                    "failure_class": variant.failure_class,
                    "source": prov,
                }
            )
            seen.add(surface)
            rid += 1

    return rows


# ---------------------------------------------------------------------------
# Search backends
# ---------------------------------------------------------------------------

def _git_grep_search(
    surface: str, repo_root: Path, repo_label: str
) -> tuple[bool, str | None]:
    """Search COMMITTED content of *repo_root* for *surface* via ``git grep``.

    Returns ``(True, prov)`` on the first matching tracked file, ``(False, None)``
    otherwise.  Provenance: ``gh:<repo_label>:<relpath>`` (repo-root-relative).

    ``git grep`` (not ripgrep) by design: only tracked files are searched, so
    untracked local-only files -- the gitignored docs/superpowers design docs
    (incl. THIS bench's own design doc), profiling scratch, gitignored datasets
    -- never match, keeping the drop-absent honesty signal real.  It is also
    git-aware, so it works inside a linked worktree (where ripgrep cannot detect
    the ``.git`` file and silently stops honoring ``.gitignore``).  The bench's
    own dir is excluded via pathspec so a concept cannot self-match concepts.jsonl.
    """
    cmd = [
        "git", "-C", str(repo_root), "grep",
        "-I",  # never match binary files
        "-F",  # fixed string, not regex
        "-w",  # WHOLE-WORD: "ER" must be a token, not a substring of "Server"
        "-l",  # list matching filenames only
        "-e", surface,
        "--", _BENCH_EXCLUDE_PATHSPEC,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("  [warn] git not found on PATH -- skipping git grep search", file=sys.stderr)
        return False, None

    if result.returncode == 1:
        # git grep exit 1 = no match; not an error.
        return False, None
    if result.returncode != 0:
        print(
            f"  [warn] git grep error (rc={result.returncode}) in {repo_label}: "
            f"{result.stderr.strip()[:200]}",
            file=sys.stderr,
        )
        return False, None

    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if not lines:
        return False, None
    rel = lines[0].strip().replace("\\", "/")
    return True, f"gh:{repo_label}:{rel}"


def _gh_issue_search(
    surface: str, repo: str
) -> tuple[bool, str | None]:
    """Search GitHub issues/PRs for *surface* in *repo* via the ``gh`` CLI.

    Returns ``(True, prov)`` on the first hit, ``(False, None)`` otherwise.
    Provenance format: ``gh:<owner>/<repo>#<number>``.
    """
    time.sleep(0.5)  # be rate-limit-friendly
    cmd = [
        "gh", "search", "issues",
        "--repo", repo,
        surface,
        "--json", "number",
        "--limit", "1",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("  [warn] gh not found on PATH -- skipping issue search", file=sys.stderr)
        return False, None

    if result.returncode != 0:
        print(
            f"  [warn] gh search failed for {surface!r} in {repo}: {result.stderr.strip()[:200]}",
            file=sys.stderr,
        )
        return False, None

    try:
        hits = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"  [warn] gh search JSON parse error for {surface!r}: {exc}", file=sys.stderr)
        return False, None

    if not hits:
        return False, None

    number = hits[0].get("number")
    if number is None:
        return False, None

    prov = f"gh:{repo}#{number}"
    return True, prov


def make_search_fn(
    repos: list[tuple[Path, str]],
    gh_repos: list[str],
) -> Callable[[str], tuple[bool, str | None]]:
    """Return a search callable: git grep over local checkouts, then GitHub issues.

    The returned function maintains an internal per-surface cache so a repeated
    surface is never re-queried.

    Args:
        repos:     ``(repo_root, label)`` checkouts searched with ``git grep``
                   (tracked content only).  Empty list skips local search.
        gh_repos:  GitHub ``owner/repo`` strings for issue/PR search when the
                   local checkouts miss.  Empty list skips GitHub search.

    Returns:
        A callable ``(surface: str) -> (found: bool, provenance: str | None)``.
    """
    cache: dict[str, tuple[bool, str | None]] = {}

    def search(surface: str) -> tuple[bool, str | None]:
        if surface in cache:
            return cache[surface]

        # 1. Local committed content (fast, no rate limit).
        for repo_root, label in repos:
            found, prov = _git_grep_search(surface, repo_root, label)
            if found:
                cache[surface] = (True, prov)
                return True, prov

        # 2. GitHub issues/PRs (fallback for mentions not in tracked files).
        for gh_repo in gh_repos:
            found, prov = _gh_issue_search(surface, gh_repo)
            if found:
                cache[surface] = (True, prov)
                return True, prov

        cache[surface] = (False, None)
        return False, None

    return search


def _build_repos(repo_root: Path) -> list[tuple[Path, str]]:
    """Local git checkouts to search (worktree root + optional extensions repo)."""
    repos: list[tuple[Path, str]] = [(repo_root, "goldenmatch")]
    if _EXTENSIONS_DIR.is_dir():
        repos.append((_EXTENSIONS_DIR, "goldenmatch-extensions"))
    return repos


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="load concepts, show keep/drop table; do NOT write records_ghsuite.csv",
    )
    ap.add_argument(
        "--concepts",
        default=str(_HERE / "concepts.jsonl"),
        help="path to concepts.jsonl (default: dataset/concepts.jsonl)",
    )
    ap.add_argument(
        "--out",
        default=str(_HERE / "records_ghsuite.csv"),
        help="output CSV path (default: dataset/records_ghsuite.csv)",
    )
    args = ap.parse_args()

    concepts = load_concepts(args.concepts)
    repos = _build_repos(_REPO_ROOT)
    search_fn = make_search_fn(repos, _DEFAULT_REPOS)

    if args.dry_run:
        # Print a per-variant keep/drop table without writing anything.
        kept = 0
        dropped = 0
        print(f"{'concept':<30} {'variant':<35} {'decision':<8} provenance")
        print("-" * 90)
        for concept in concepts:
            seen: set[str] = set()
            for variant in concept.variants:
                if variant.surface in seen:
                    continue
                seen.add(variant.surface)
                found, prov = search_fn(variant.surface)
                decision = "KEEP" if found else "DROP"
                prov_str = prov or ""
                print(
                    f"{concept.concept:<30} {variant.surface:<35} {decision:<8} {prov_str}"
                )
                if found:
                    kept += 1
                else:
                    dropped += 1
        print()
        print(
            f"{kept} kept / {dropped} dropped / {len(concepts)} concepts"
        )
        print("(dry-run: records_ghsuite.csv not written)")
        return

    rows = assemble_records(concepts, search_fn)
    n_concepts = len({r["entity_id"] for r in rows})
    fc_counts: dict[str, int] = {}
    for r in rows:
        fc_counts[r["failure_class"]] = fc_counts.get(r["failure_class"], 0) + 1

    out_path = Path(args.out)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)

    print(f"{len(rows)} records / {n_concepts} concepts")
    for fc, count in sorted(fc_counts.items()):
        print(f"  {fc}: {count}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
