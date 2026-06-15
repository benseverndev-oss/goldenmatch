"""Generate (or check) the config-linter docs from the rule registry.

  python scripts/gen_lint_docs.py --write   # regenerate the .mdx
  python scripts/gen_lint_docs.py --check    # exit 1 if the committed .mdx is stale

The registry is the source of truth; the committed page must match it (CI runs
--check, mirroring the docs_staleness gate).
"""
from __future__ import annotations

import sys

from goldenmatch.core.config_lint.docgen import DOC_PATH, docs_are_current, write_docs


def main(argv: list[str]) -> int:
    if "--check" in argv:
        if docs_are_current():
            print(f"config-linter docs are current: {DOC_PATH}")
            return 0
        print(
            f"::error::{DOC_PATH} is stale vs the lint rule registry. "
            f"Run: python scripts/gen_lint_docs.py --write",
            file=sys.stderr,
        )
        return 1
    if "--write" in argv:
        p = write_docs()
        print(f"wrote {p}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
