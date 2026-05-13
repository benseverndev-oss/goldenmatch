"""Sync README "what's new" callouts from CHANGELOG.

Single source of truth: each version section in
``packages/python/goldenmatch/CHANGELOG.md`` may carry a
``<!-- README-callout ... -->`` block. The first three such blocks
(top of file, newest version first) are hoisted into the fenced
``<!-- README-callouts:start --> ... <!-- README-callouts:end -->``
region in both READMEs.

Behavior:

- Default mode rewrites the READMEs in place.
- ``--check`` exits 1 if a rewrite would produce a diff (CI gate).
- Versions without a callout marker are silently skipped, so you can
  ship internal/patch releases without burning a homepage slot.

The callout body inside the marker is a single Markdown paragraph
(blank lines inside the marker are preserved as ``\\n>\\n`` between
callouts on the homepage).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = REPO_ROOT / "packages" / "python" / "goldenmatch" / "CHANGELOG.md"
TARGETS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "packages" / "python" / "goldenmatch" / "README.md",
]

FENCE_START = "<!-- README-callouts:start"
FENCE_END = "<!-- README-callouts:end -->"

MAX_CALLOUTS = 3

# Match a version heading and pair it with the callout block that
# immediately follows. Tolerates either ``[1.2.3] - date`` or
# ``[Unreleased]`` and optional blank lines before the marker.
_SECTION_RE = re.compile(
    r"^## \[(?P<version>[^\]]+)\][^\n]*\n+"
    r"<!-- README-callout\s*\n"
    r"(?P<body>.*?)\n"
    r"-->",
    re.MULTILINE | re.DOTALL,
)


def extract_callouts(changelog_text: str) -> list[tuple[str, str]]:
    """Return ``[(version, body), ...]`` in source order, top of file first."""
    return [
        (m.group("version").strip(), m.group("body").strip())
        for m in _SECTION_RE.finditer(changelog_text)
    ]


def render_block(callouts: list[tuple[str, str]], target_path: Path) -> str:
    if not callouts:
        raise ValueError("No README-callout markers found in CHANGELOG.")
    if target_path == TARGETS[0]:
        comment_suffix = "from packages/python/goldenmatch/CHANGELOG.md"
    else:
        comment_suffix = "from CHANGELOG.md"
    rendered_callouts = callouts[:MAX_CALLOUTS]
    lines = [
        f"{FENCE_START}  (auto-synced {comment_suffix} by scripts/sync_readme_callouts.py — edit the CHANGELOG, not this block) -->"
    ]
    for i, (version, body) in enumerate(rendered_callouts):
        lines.append(_format_callout(version, body, is_first=(i == 0)))
        if i != len(rendered_callouts) - 1:
            lines.append(">")
    lines.append(FENCE_END)
    return "\n".join(lines)


_BOLD_TITLE_RE = re.compile(r"^\*\*(?P<title>[^*]+)\*\*\s*(?P<sep>—|--|-)\s*(?P<rest>.*)$", re.DOTALL)
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+")


def _version_label(version: str) -> str:
    """Prepend ``v`` for bare semver; pass ``Unreleased``-style labels through."""
    return f"v{version}" if _SEMVER_RE.match(version) else version


def _format_callout(version: str, body: str, *, is_first: bool) -> str:
    """Render one callout line as a blockquote.

    Input body is the raw text from the CHANGELOG marker. If it leads
    with ``**Title** — rest``, fold the version into the bold span so
    the homepage reads ``**🆕 vX.Y.Z — Title** — rest``. Otherwise just
    bold the version.
    """
    body = body.strip()
    new_badge = "🆕 " if is_first else ""
    label = _version_label(version)
    match = _BOLD_TITLE_RE.match(body)
    if match:
        title = match.group("title").strip()
        rest = match.group("rest").strip()
        return f"> **{new_badge}{label} — {title}** — {rest}"
    return f"> **{new_badge}{label}** — {body}"


def rewrite_target(path: Path, new_block: str) -> tuple[str, str]:
    original = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"<!-- README-callouts:start.*?<!-- README-callouts:end -->",
        re.DOTALL,
    )
    if not pattern.search(original):
        raise RuntimeError(
            f"{path} is missing the README-callouts fence markers. "
            f"Add <!-- README-callouts:start --> ... <!-- README-callouts:end --> "
            f"around the existing callout block."
        )
    updated = pattern.sub(lambda _m: new_block, original, count=1)
    return original, updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the READMEs would change. Default: rewrite in place.",
    )
    args = parser.parse_args(argv)

    changelog_text = CHANGELOG.read_text(encoding="utf-8")
    callouts = extract_callouts(changelog_text)
    if not callouts:
        print("FAIL: no README-callout markers in CHANGELOG", file=sys.stderr)
        return 1

    any_diff = False
    for target in TARGETS:
        new_block = render_block(callouts, target)
        original, updated = rewrite_target(target, new_block)
        if original == updated:
            continue
        any_diff = True
        if args.check:
            print(f"DRIFT: {target.relative_to(REPO_ROOT)} is out of sync", file=sys.stderr)
        else:
            target.write_text(updated, encoding="utf-8")
            print(f"updated {target.relative_to(REPO_ROOT)}")

    if args.check and any_diff:
        print(
            "\nRun `python scripts/sync_readme_callouts.py` and commit the result.",
            file=sys.stderr,
        )
        return 1
    if not any_diff and not args.check:
        print("no changes (READMEs already in sync)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
