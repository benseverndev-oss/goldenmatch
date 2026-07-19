#!/usr/bin/env python3
"""Validate that docs-site pages render: frontmatter parses and every internal
cross-link resolves to a real page.

Complements check_docs_consistency (nav integrity: nav entry -> file). This checks
the links INSIDE page bodies -- the 50+ `/pkg/page` cross-links between docs that
nothing else validates, so a typo like `/goldenmatch/config-matix` fails CI
instead of shipping a dead link. Frontmatter is parsed too (a broken `---` block
breaks the whole page). Pure stdlib + pyyaml; no Mintlify CLI needed.

Run: python scripts/check_docs_links.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs-site"

# markdown `](/path)` and JSX `href="/path"` links that are internal (start with /)
_LINK_RE = re.compile(r"""\]\((/[^)\s]+)\)|href=["'](/[^"']+)["']""")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_RE = re.compile(r"`[^`]*`")


def _nav_pages() -> set[str]:
    """Every page slug referenced in docs.json, as `/slug`."""
    try:
        import json

        data = json.loads((DOCS / "docs.json").read_text(encoding="utf-8"))
    except Exception:
        return set()
    pages: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, str):
            pages.add("/" + node)
        elif isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)

    walk(data)
    return pages


def _resolve(target: str, nav: set[str]) -> bool:
    rel = target.strip("/")
    return (
        target in nav
        or (DOCS / f"{rel}.mdx").exists()
        or (DOCS / rel / "index.mdx").exists()
    )


def check() -> list[str]:
    nav = _nav_pages()
    problems: list[str] = []
    for mdx in sorted(DOCS.rglob("*.mdx")):
        text = mdx.read_text(encoding="utf-8", errors="ignore")
        rel = mdx.relative_to(ROOT).as_posix()

        # 1. frontmatter must be a terminated block whose top-level lines look like
        #    `key: value` (stdlib-only structural check -- a broken/unterminated
        #    `---` block breaks the whole Mintlify page).
        if not text.startswith("---"):
            problems.append(f"{rel}: missing frontmatter")
        else:
            parts = text.split("---", 2)
            if len(parts) < 3:
                problems.append(f"{rel}: unterminated frontmatter block")
            else:
                for line in parts[1].splitlines():
                    if not line.strip() or line[0] in " \t-#":
                        continue  # blank / list item / continuation / comment
                    if not re.match(r"^[A-Za-z0-9_.-]+\s*:", line):
                        problems.append(f"{rel}: frontmatter line is not `key: value`: {line!r}")
                        break

        # 2. internal links (ignore links inside code) must resolve
        body = _INLINE_RE.sub("", _FENCE_RE.sub("", text))
        for m in _LINK_RE.finditer(body):
            raw = m.group(1) or m.group(2)
            target = raw.split("#", 1)[0]  # drop the #anchor; validate the page
            if not target or target == "/" or target.startswith(("http", "mailto", "//")):
                continue
            if not _resolve(target, nav):
                problems.append(f"{rel}: broken internal link -> {raw}")
    return problems


def main() -> int:
    problems = check()
    n_pages = sum(1 for _ in DOCS.rglob("*.mdx"))
    if problems:
        for p in problems:
            print(f"::error::{p}", file=sys.stderr)
        print(f"\nDocs link/frontmatter check FAILED: {len(problems)} problem(s) "
              f"across {n_pages} pages.", file=sys.stderr)
        return 1
    print(f"Docs link/frontmatter check OK: {n_pages} pages, all frontmatter parses, "
          "all internal links resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
