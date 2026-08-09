#!/usr/bin/env python3
"""Reject a commit message containing a CI-skip directive.

WHY THIS EXISTS
---------------
GitHub Actions skips a workflow when the HEAD commit message contains
`[skip ci]`, `[ci skip]`, `[no ci]` or `[skip actions]` ANYWHERE -- including in
descriptive prose. It does not have to be an instruction; it only has to be the
substring.

The consequence is silent and expensive. With no CI run on a PR head, the
required `ci-required` check is MISSING rather than failing, and a missing
required check is not `success` or `skipped` -- so the PR sits in the merge queue
looking armed and never merges. Nothing turns red. You wait.

This has now bitten twice. #1620: an empty commit pushed to RE-FIRE CI mentioned
`[skip ci]` in its own message and skipped itself. #2445: the commit message said
"the [skip ci] trap" while describing the trap, in a PR that was documenting the
trap -- the whole ci.yml run was skipped and only 4 of 99 checks ran.

CI cannot catch this. That is the point of putting it here: the run is skipped,
so there is nothing to fail. It has to be refused before the commit exists.

If you genuinely mean to skip CI, say so deliberately:

    git commit --no-verify

To write ABOUT the directive without triggering it, break the literal -- e.g.
"the skip-CI-directive trap", or `[skip` + ` ci]` in separate backticks.

Used as a pre-commit `commit-msg` hook; also runnable directly:

    python scripts/check_commit_msg.py .git/COMMIT_EDITMSG
"""

from __future__ import annotations

import pathlib
import re
import sys

# The literals GitHub Actions matches. Case-insensitive, anywhere in the message.
# NOTE the docstring above deliberately spells the directives out -- this file is
# documentation, not a commit message, so running the checker against its own
# source reports hits. That is correct behaviour, not a bug: the hook only ever
# sees a commit-message file. Do not "fix" it by obfuscating the docstring; a
# trap you cannot name in its own explanation is worse than the hits.
_DIRECTIVES = ("skip ci", "ci skip", "no ci", "skip actions")
_PATTERN = re.compile(
    r"\[\s*(" + "|".join(d.replace(" ", r"\s+") for d in _DIRECTIVES) + r")\s*\]",
    re.IGNORECASE,
)


def offending_lines(message: str) -> list[tuple[int, str, str]]:
    """(lineno, matched text, the line) for every directive found."""
    hits = []
    for lineno, line in enumerate(message.splitlines(), 1):
        # Comment lines are stripped by git before the message is stored.
        if line.startswith("#"):
            continue
        for m in _PATTERN.finditer(line):
            hits.append((lineno, m.group(0), line.strip()))
    return hits


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(
            "usage: check_commit_msg.py <path-to-commit-message-file>", file=sys.stderr
        )
        return 2

    path = pathlib.Path(args[0])
    try:
        message = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"cannot read commit message file {path}: {exc}", file=sys.stderr)
        return 2

    hits = offending_lines(message)
    if not hits:
        return 0

    print(
        "\nCommit message contains a CI-skip directive:\n", file=sys.stderr
    )
    for lineno, matched, line in hits:
        print(f"  line {lineno}: {matched}", file=sys.stderr)
        print(f"    {line}", file=sys.stderr)
    print(
        "\nGitHub Actions matches these ANYWHERE in the message, including in prose,\n"
        "and skips the whole run. A skipped run leaves `ci-required` MISSING rather\n"
        "than failing, so the PR sits in the merge queue looking armed and never\n"
        "merges -- with nothing red to tell you.\n"
        "\n"
        "If you are writing ABOUT the directive, break the literal (e.g.\n"
        '"the skip-CI-directive trap"). If you really mean to skip CI, commit with\n'
        "--no-verify.\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
