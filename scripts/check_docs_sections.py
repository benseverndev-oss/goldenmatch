#!/usr/bin/env python3
"""Per-section documentation-consistency gate: every docs-site package section
conforms to ONE canonical shape, so the sections stop drifting apart.

This module is the SOURCE OF TRUTH for "what a package section looks like". The
other doc gates cover cross-cutting structure -- nav integrity + orphan detection
(``check_docs_consistency.py``), in-body links + frontmatter parse
(``check_docs_links.py``), the Mintlify render (``mint validate``), and the
per-package config-matrix generation (``gen_config_matrix.py``). None of them
enforce the WITHIN-section shape, which is exactly where the drift lived: one
section had a ``recipes`` page and another didn't, titles mixed Title-Case and
sentence case, and generated pages silently dropped ``keywords``.

Five checks, all deterministic and stdlib-only (~1s):

  1. spine        -- every package section carries the required pages
                     (``overview``, ``config-matrix``, ``recipes``) as real
                     ``.mdx`` files, so no section is structurally thinner than
                     its siblings.
  2. overview-1st -- the section's nav group opens with ``<pkg>/overview``.
  3. page-order   -- FLAT sections order their pages canonically: ``overview``
                     first, then the section-specific concept pages (their
                     authored order preserved), then the REFERENCE band
                     (``config-matrix`` -> ``recipes`` -> ``cli`` ->
                     ``native``/``performance`` -> ``integrations``) in fixed
                     order. Nested sections (goldenmatch's Guides/Features/...
                     subgroups) are exempt from ordering but still spine- and
                     overview-first-checked.
  4. frontmatter  -- every ``docs-site/**/*.mdx`` carries ``title`` +
                     ``description`` + ``keywords``, all non-empty (``keywords``
                     a non-empty list).
  5. title-style  -- titles are sentence case: the first word plus proper nouns
                     and acronyms are capitalized, nothing else -- so the sidebar
                     reads uniformly.

Adding a package section = add it to ``SECTIONS``. Generated pages
(config-matrix, config-linter, suite-matrix) must satisfy the same rules; fix
them in their GENERATOR, not the file (the checks here are format-only, so they
never fight the generated block).

Run: ``python scripts/check_docs_sections.py``
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs-site"

# --- the contract (source of truth) -----------------------------------------

# The package sections gated for shape. Each has a `docs-site/<pkg>/` directory
# and a matching top-level nav group. Adding a suite package = add it here.
SECTIONS = (
    "goldenmatch",
    "goldencheck",
    "goldenflow",
    "goldenpipe",
    "goldenanalysis",
    "infermap",
)

# The canonical spine every section must carry as real files.
REQUIRED_PAGES = ("overview", "config-matrix", "recipes")

# Reference-band pages, in canonical tail order. Any page not listed here and not
# `overview` is a section-specific concept page: it sorts into the MIDDLE band and
# keeps its authored order.
REFERENCE_ORDER = ("config-matrix", "recipes", "cli", "native", "performance", "integrations")

# Words that stay capitalized anywhere in a title (proper nouns / brands), matched
# case-insensitively. The authored capitalization is trusted (we don't re-check a
# brand's internal caps like GoldenMatch/DuckDB), only that the word is allowed to
# start uppercase mid-title. Acronyms (all-caps tokens) and digit-bearing tokens
# (v2.0, GPT-4o) are allowed structurally and need not be listed.
PROPER_NOUNS = frozenset({
    "goldenmatch", "goldencheck", "goldenflow", "goldenpipe", "goldenanalysis",
    "goldenpipe", "infermap", "goldenschema", "goldengraph", "golden", "suite",
    "python", "typescript", "javascript", "node", "rust", "arrow", "polars",
    "duckdb", "postgresql", "postgres", "sqlite", "ray", "bloom", "claude",
    "gpt", "openai", "anthropic", "docker", "dbt", "neo4j", "llamaindex",
    "graphiti", "graphrag", "splink", "bayesian", "hungarian", "jaro-winkler",
    "fellegi-sunter", "minhash", "simhash", "railway", "smithery", "mintlify",
})


# --- frontmatter parsing (stdlib-only) --------------------------------------

def _frontmatter(text: str) -> dict[str, str]:
    """Return the raw top-level `key: value` lines of the frontmatter block.

    Values are returned verbatim (still quoted / still a `[...]` list); the
    per-check logic interprets them. Missing block => empty dict.
    """
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if km:
            out[km.group(1)] = km.group(2).strip()
    return out


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
        return v[1:-1]
    return v


def _is_nonempty_list(value: str) -> bool:
    v = value.strip()
    if not (v.startswith("[") and v.endswith("]")):
        return False
    return bool(v[1:-1].strip())


# --- title sentence-case checker --------------------------------------------

def _is_acronym(core: str) -> bool:
    """All-caps token (>= 2 letters), tolerating a trailing plural 's' (IDs)."""
    base = core[:-1] if core.endswith("s") and len(core) > 2 else core
    return len(base) >= 2 and base.isalpha() and base.isupper()


def title_violations(title: str) -> list[str]:
    """Words in `title` that break sentence case. Empty list => OK."""
    bad: list[str] = []
    for idx, tok in enumerate(title.split()):
        first_alpha = next((c for c in tok if c.isalpha()), None)
        if first_alpha is None:
            continue  # pure punctuation / number token, e.g. "&", "(the"
        core = tok.strip("()[]{}.,:;!?–—")
        has_digit = any(c.isdigit() for c in tok)
        if not first_alpha.isupper():
            # starts lowercase: only the first word must be capitalized, and a
            # digit-led version token (v1, v2.0) is a legitimate first word.
            if idx == 0 and not has_digit:
                bad.append(f"{tok!r} (first word should be capitalized)")
            continue
        # starts uppercase: justified for the first word, acronyms, digit tokens,
        # or an allow-listed proper noun; otherwise it is stray Title-Case.
        if idx == 0 or has_digit or _is_acronym(core) or core.lower() in PROPER_NOUNS:
            continue
        bad.append(f"{tok!r} (use sentence case)")
    return bad


# --- section ordering -------------------------------------------------------

def _slot(slug: str) -> tuple[int, int]:
    """(band, index-within-band). band 0=overview, 1=concept, 2=reference."""
    if slug == "overview":
        return (0, 0)
    if slug in REFERENCE_ORDER:
        return (2, REFERENCE_ORDER.index(slug))
    return (1, 0)


def expected_order(slugs: list[str]) -> list[str]:
    """The canonical order for a flat section's page slugs."""
    order = sorted(range(len(slugs)), key=lambda i: (_slot(slugs[i])[0], _slot(slugs[i])[1], i))
    return [slugs[i] for i in order]


# --- nav helpers ------------------------------------------------------------

def _load_nav() -> list[dict]:
    data = json.loads((DOCS / "docs.json").read_text(encoding="utf-8"))
    groups: list[dict] = []
    for tab in data.get("navigation", {}).get("tabs", []):
        groups.extend(tab.get("groups", []))
    return groups


def _section_group(groups: list[dict], pkg: str) -> dict | None:
    """The nav group whose pages contain `<pkg>/overview`."""
    def has_overview(pages: object) -> bool:
        if isinstance(pages, list):
            return any(has_overview(p) for p in pages)
        if isinstance(pages, dict):
            return has_overview(pages.get("pages", []))
        return pages == f"{pkg}/overview"

    for g in groups:
        if has_overview(g.get("pages", [])):
            return g
    return None


# --- the checks -------------------------------------------------------------

def check() -> list[str]:
    problems: list[str] = []

    # 4 + 5: frontmatter schema + title style over EVERY docs-site page.
    for path in sorted(DOCS.rglob("*.mdx")):
        rel = path.relative_to(ROOT)
        fm = _frontmatter(path.read_text(encoding="utf-8"))
        for key in ("title", "description", "keywords"):
            if key not in fm:
                problems.append(f"{rel}: frontmatter missing `{key}`")
        if fm.get("title") and not _unquote(fm["title"]):
            problems.append(f"{rel}: frontmatter `title` is empty")
        if fm.get("description") and not _unquote(fm["description"]):
            problems.append(f"{rel}: frontmatter `description` is empty")
        if "keywords" in fm and not _is_nonempty_list(fm["keywords"]):
            problems.append(f"{rel}: frontmatter `keywords` must be a non-empty list")
        if fm.get("title"):
            for v in title_violations(_unquote(fm["title"])):
                problems.append(f"{rel}: title not sentence case: {v}")

    groups = _load_nav()

    for pkg in SECTIONS:
        pkg_dir = DOCS / pkg
        # 1: spine present as real files.
        for page in REQUIRED_PAGES:
            if not (pkg_dir / f"{page}.mdx").exists():
                problems.append(
                    f"{pkg}: missing required spine page `{pkg}/{page}.mdx` "
                    f"(every section must have {', '.join(REQUIRED_PAGES)})"
                )

        group = _section_group(groups, pkg)
        if group is None:
            problems.append(f"{pkg}: no nav group contains `{pkg}/overview`")
            continue

        pages = group.get("pages", [])
        # 2: overview first.
        if not pages or pages[0] != f"{pkg}/overview":
            problems.append(f"{pkg}: nav group must open with `{pkg}/overview`")

        # 3: canonical order for FLAT sections only (all top-level entries are
        # string page refs). Nested sections are exempt from ordering.
        if all(isinstance(p, str) for p in pages):
            slugs = [p.split("/", 1)[1] if "/" in p else p for p in pages]
            want = expected_order(slugs)
            if slugs != want:
                problems.append(
                    f"{pkg}: nav pages out of canonical order.\n"
                    f"       got:  {slugs}\n"
                    f"       want: {want}"
                )

    return problems


def main() -> int:
    problems = check()
    if problems:
        print("Section-consistency check FAILED:\n", file=sys.stderr)
        for p in problems:
            print(f"::error::docs-section: {p}", file=sys.stderr)
            print(f"  - {p}", file=sys.stderr)
        print(
            f"\n{len(problems)} problem(s). Fix the file (or its generator, for "
            "generated pages) so every section matches the canonical shape.",
            file=sys.stderr,
        )
        return 1
    n = len(list(DOCS.rglob("*.mdx")))
    print(f"Section-consistency check OK: {len(SECTIONS)} sections, {n} pages "
          "conform to the canonical shape.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
