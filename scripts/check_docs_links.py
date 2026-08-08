#!/usr/bin/env python3
"""Validate that docs links resolve -- inside docs-site pages, and repo-wide.

Two checks:

1. **docs-site pages render.** Frontmatter parses and every internal `/pkg/page`
   cross-link resolves. Complements check_docs_consistency (nav integrity: nav
   entry -> file); this checks the links INSIDE page bodies, so a typo like
   `/goldenmatch/config-matix` fails CI instead of shipping a dead link.

2. **Every `docs.bensevern.dev` URL anywhere in the repo points at a real page.**
   These are the outward-facing pointers: `[project.urls]` rendered on PyPI,
   `homepage` on npm, the `llms.txt` files shipped inside installed artifacts,
   module docstrings, Rust crate docs. They are the whole discoverability story
   -- an agent that never clones the repo follows one of these or reverse-
   engineers the package instead. A pointer that 404s is worse than no pointer.

   This check exists because both failure modes had already happened. The site is
   served under a **`/docs` prefix**, and every URL in the repo omitted it, so all
   135 of them 404'd. One also named a page (`/golden-suite`) that does not exist
   at all. Neither was visible to check (1), which only looks at MDX bodies.

   Resolution is static, so CI needs no network: a URL must be
   `https://docs.bensevern.dev/docs/<rel>` where `<rel>` is a docs-site page, a
   nav slug, or one of the Mintlify-generated files (`llms.txt`, `llms-full.txt`).

Pure stdlib + pyyaml; no Mintlify CLI needed.

Run: python scripts/check_docs_links.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs-site"

# The docs site is served under a /docs prefix: https://docs.bensevern.dev/ 308s
# to /docs, and a bare https://docs.bensevern.dev/goldenmatch is a 404.
_SITE = "docs.bensevern.dev"
_REQUIRED_PREFIX = f"https://{_SITE}/docs/"
_SITE_URL_RE = re.compile(rf"https?://{re.escape(_SITE)}/[A-Za-z0-9._/-]*")

# Served by Mintlify, generated rather than authored, so they have no source file
# to resolve against. Verified live before being listed here.
_GENERATED = {"llms.txt", "llms-full.txt"}

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


def _is_section_root(rel: str) -> bool:
    """`/docs/goldenmatch` -- a section with pages but no page of its own.

    Mintlify redirects a section root to its first page (verified live: every one
    308s to `<section>/overview` and lands 200), so pointing at the bare section
    is legitimate. Model that statically as "a directory holding at least one
    page" rather than requiring the network.
    """
    d = DOCS / rel
    return d.is_dir() and any(d.glob("*.mdx"))


def _tracked_files() -> list[Path]:
    """Every git-tracked file that mentions the docs host.

    This file is skipped: its own docstring quotes the malformed URLs the check
    exists to reject, which would otherwise make the gate fail on itself.
    """
    out = subprocess.run(
        ["git", "grep", "-lI", _SITE, "--", "."],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if out.returncode not in (0, 1):  # 1 == no matches, which is not an error
        raise RuntimeError(f"git grep failed: {out.stderr.strip()}")
    self_rel = Path(__file__).resolve().relative_to(ROOT).as_posix()
    return [ROOT / line for line in out.stdout.split() if line and line != self_rel]


def check_site_urls(nav: set[str]) -> list[str]:
    """Every docs.bensevern.dev URL in the repo must name a real page."""
    problems: list[str] = []
    seen = 0
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel_file = path.relative_to(ROOT).as_posix()
        for m in _SITE_URL_RE.finditer(text):
            url = m.group(0).rstrip(".,);:'\"")
            seen += 1
            if not url.startswith(_REQUIRED_PREFIX):
                problems.append(
                    f"{rel_file}: {url} -- the site is served under /docs, so this 404s "
                    f"(want {_REQUIRED_PREFIX}...)"
                )
                continue
            rel = url[len(_REQUIRED_PREFIX):].strip("/")
            if not rel or rel in _GENERATED:
                continue
            if not _resolve("/" + rel, nav) and not _is_section_root(rel):
                problems.append(f"{rel_file}: {url} -- no such docs page")
    if not seen:
        # The pointers ARE the feature; silently finding none means the scan broke.
        problems.append("found no docs.bensevern.dev URLs at all -- the scan is broken")
    return problems


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

    problems.extend(check_site_urls(nav))
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
          "all internal links resolve, all docs.bensevern.dev URLs name a real page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
