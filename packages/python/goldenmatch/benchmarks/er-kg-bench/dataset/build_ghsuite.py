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

# Suite Python packages to search with ripgrep (relative to repo root).
_SUITE_PKG_NAMES = [
    "goldenmatch",
    "goldencheck",
    "goldenflow",
    "goldenpipe",
    "infermap",
    "goldenanalysis",
]

# GitHub repos to search for issues/PRs when ripgrep misses.
_DEFAULT_REPOS = [
    "benseverndev-oss/goldenmatch",
]

# Extensions checkout (optional; skip silently when absent).
_EXTENSIONS_DIR = Path("D:/show_case/goldenmatch-extensions")

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

_rg_missing_warned = False


def _ripgrep_search(
    surface: str, roots: list[Path]
) -> tuple[bool, str | None]:
    """Search for *surface* as a fixed string across *roots* using ripgrep.

    Returns ``(True, prov)`` on the first match, ``(False, None)`` otherwise.
    Provenance format: ``gh:<pkg>:<relpath>`` where ``<pkg>`` is the top-level
    suite directory name and ``<relpath>`` is relative to that directory.
    """
    global _rg_missing_warned  # noqa: PLW0603

    if not roots:
        return False, None

    cmd = ["rg", "-F", "-l", "--", surface, *[str(r) for r in roots]]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        if not _rg_missing_warned:
            print("  [warn] rg not found on PATH -- skipping ripgrep search", file=sys.stderr)
            _rg_missing_warned = True
        return False, None

    if result.returncode == 1:
        # rg exit 1 = no match found; not an error.
        return False, None
    if result.returncode >= 2:
        print(
            f"  [warn] rg error (rc={result.returncode}): {result.stderr.strip()[:200]}",
            file=sys.stderr,
        )
        return False, None

    # Pick the first matching file and build a short provenance string.
    first_file = result.stdout.strip().splitlines()[0]
    first_path = Path(first_file)

    # Try to express the path relative to a known suite package root.
    for root in roots:
        try:
            rel = first_path.relative_to(root)
            # root is something like <repo>/packages/python/goldenmatch
            # Use the directory name of the root as the <pkg> label.
            pkg = root.name
            prov = f"gh:{pkg}:{rel.as_posix()}"
            return True, prov
        except ValueError:
            continue

    # Fallback: path relative to repo root.
    try:
        rel = first_path.relative_to(_REPO_ROOT)
        prov = f"gh:repo:{rel.as_posix()}"
    except ValueError:
        prov = f"gh:file:{first_path.name}"
    return True, prov


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
    roots: list[Path],
    repos: list[str],
) -> Callable[[str], tuple[bool, str | None]]:
    """Return a search callable that tries ripgrep then GitHub issues.

    The returned function maintains an internal per-surface cache so a repeated
    surface is never re-queried.

    Args:
        roots:  Directories to pass to ripgrep.  Empty list skips ripgrep.
        repos:  GitHub ``owner/repo`` strings for issue search.  Empty list
                skips GitHub search.

    Returns:
        A callable ``(surface: str) -> (found: bool, provenance: str | None)``.
    """
    cache: dict[str, tuple[bool, str | None]] = {}

    def search(surface: str) -> tuple[bool, str | None]:
        if surface in cache:
            return cache[surface]

        # 1. Try ripgrep (fast, local, no rate limit).
        found, prov = _ripgrep_search(surface, roots)
        if found:
            cache[surface] = (True, prov)
            return True, prov

        # 2. Try each GitHub repo in order.
        for repo in repos:
            found, prov = _gh_issue_search(surface, repo)
            if found:
                cache[surface] = (True, prov)
                return True, prov

        cache[surface] = (False, None)
        return False, None

    return search


def _build_roots(repo_root: Path) -> list[Path]:
    """Collect existing directories to pass to ripgrep."""
    roots: list[Path] = []
    pkg_base = repo_root / "packages" / "python"
    for name in _SUITE_PKG_NAMES:
        p = pkg_base / name
        if p.is_dir():
            roots.append(p)
    # Also search repo-level docs and markdown.
    docs_dir = repo_root / "docs"
    if docs_dir.is_dir():
        roots.append(docs_dir)
    # Extensions checkout (optional).
    if _EXTENSIONS_DIR.is_dir():
        roots.append(_EXTENSIONS_DIR)
    return roots


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
    roots = _build_roots(_REPO_ROOT)
    search_fn = make_search_fn(roots, _DEFAULT_REPOS)

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
