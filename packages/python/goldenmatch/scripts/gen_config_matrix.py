"""Generate (or check) the config-matrix doc from the live config surface.

  python scripts/gen_config_matrix.py --write   # regenerate the .mdx block
  python scripts/gen_config_matrix.py --check    # exit 1 if the committed .mdx is stale

The pydantic `GoldenMatchConfig` tree + the `VALID_*` vocabularies + the scanned
`GOLDENMATCH_*` env set are the source of truth; the committed page's generated
block must match them. CI runs --check (mirroring gen_lint_docs.py), so adding or
removing a config knob without regenerating the matrix turns the merge queue red.
"""
from __future__ import annotations

import sys

from goldenmatch.core.config_matrix import DOC_PATH, docs_are_current, write_docs


def main(argv: list[str]) -> int:
    if "--check" in argv:
        if docs_are_current():
            print(f"config matrix is current: {DOC_PATH}")
            return 0
        print(
            f"::error::{DOC_PATH} is stale vs the config surface "
            f"(schema / VALID_* / GOLDENMATCH_* env). "
            f"Run: python scripts/gen_config_matrix.py --write",
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
