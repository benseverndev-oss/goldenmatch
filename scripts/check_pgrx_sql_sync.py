#!/usr/bin/env python3
"""Guard: every `#[pg_extern]` in the goldenmatch_pg crate is declared in the
current base SQL.

pgrx 0.12.9's SQL generator is broken in this workspace, so the extension's
`sql/goldenmatch_pg--<version>.sql` base + migration files are hand-maintained
(see packages/rust/extensions/CLAUDE.md). Nothing otherwise checks that a Rust
`#[pg_extern]` function actually made it into the SQL — a new or renamed
function whose `CREATE FUNCTION` was forgotten only fails at pgrx build/smoke
time, if the smoke happens to exercise it. This script closes that gap cheaply,
with no pgrx toolchain: it extracts the `#[pg_extern]` function set from the
Rust source and asserts each name appears as a `CREATE FUNCTION "<name>"` in the
base SQL for the crate's current `default_version`.

Direction enforced: Rust -> SQL (a `#[pg_extern]` MUST be in the SQL). The
reverse (SQL functions with no `#[pg_extern]`) is reported as info only, because
the base SQL legitimately also carries `extension_sql!`-defined helpers and
functions whose Rust lives behind cfg/feature gates.

Exit non-zero (and print an actionable message) on any missing function. Run
from the repo root: `python3 scripts/check_pgrx_sql_sync.py`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PG_CRATE = Path("packages/rust/extensions/postgres")
SRC = PG_CRATE / "src"
CONTROL = PG_CRATE / "goldenmatch_pg.control"
SQL_DIR = PG_CRATE / "sql"

# `#[pg_extern ...]` (optionally with args / across the attribute), then the next
# `fn NAME(` — pgrx maps the Rust fn name straight to the SQL function name
# (there are no `name = "..."` overrides in this crate; if one is ever added,
# extend this to read it).
FN_AFTER_ATTR = re.compile(r"\bfn\s+([a-z_][a-z0-9_]*)\s*[(<]")
CREATE_FN = re.compile(r'CREATE(?:\s+OR\s+REPLACE)?\s+FUNCTION\s+"?([a-z_][a-z0-9_]*)"?', re.IGNORECASE)


def pg_extern_fns() -> dict[str, str]:
    """Map each `#[pg_extern]` function name -> the file it is defined in."""
    found: dict[str, str] = {}
    for rs in sorted(SRC.rglob("*.rs")):
        lines = rs.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            # A real attribute starts with `#[pg_extern` after indentation —
            # NOT a doc comment that merely mentions `#[pg_extern]` in prose.
            if not line.lstrip().startswith("#[pg_extern"):
                continue
            # Scan forward past any further attributes / doc comments to the fn.
            for j in range(i + 1, min(i + 12, len(lines))):
                m = FN_AFTER_ATTR.search(lines[j])
                if m:
                    found[m.group(1)] = str(rs.relative_to(PG_CRATE.parent.parent.parent))
                    break
    return found


def default_version() -> str:
    text = CONTROL.read_text(encoding="utf-8")
    m = re.search(r"default_version\s*=\s*'([^']+)'", text)
    if not m:
        sys.exit(f"error: no default_version in {CONTROL}")
    return m.group(1)


def sql_fns(version: str) -> set[str]:
    base = SQL_DIR / f"goldenmatch_pg--{version}.sql"
    if not base.exists():
        sys.exit(
            f"error: base SQL {base} not found for default_version {version}. "
            f"Bumping the extension version needs a new base SQL file "
            f"(see packages/rust/extensions/CLAUDE.md)."
        )
    return {m.group(1).lower() for m in CREATE_FN.finditer(base.read_text(encoding="utf-8"))}


def main() -> int:
    if not CONTROL.exists():
        sys.exit(f"error: run from repo root — {CONTROL} not found")
    version = default_version()
    rust = pg_extern_fns()
    sql = sql_fns(version)

    missing = sorted(name for name in rust if name.lower() not in sql)
    sql_only = sorted(sql - {n.lower() for n in rust})

    print(f"pgrx SQL sync check: {len(rust)} #[pg_extern] fns vs base SQL "
          f"goldenmatch_pg--{version}.sql ({len(sql)} CREATE FUNCTION)")

    if sql_only:
        # Informational: extension_sql! helpers / cfg-gated fns legitimately live
        # only in SQL. Printed so a genuinely-removed Rust fn is still visible.
        print(f"  note: {len(sql_only)} SQL function(s) with no #[pg_extern] "
              f"(extension_sql! helpers / cfg-gated — not an error): "
              f"{', '.join(sql_only)}")

    if missing:
        print()
        print("::error::pgrx SQL is out of sync — these #[pg_extern] functions "
              f"are NOT declared in sql/goldenmatch_pg--{version}.sql:")
        for name in missing:
            print(f"  - {name}  (defined in {rust[name]})")
        print()
        print("Add a `CREATE FUNCTION \"<name>\"(...)` for each to the current "
              "base SQL (and the chained migration). If you added a NEW function, "
              "bump the extension version: new sql/goldenmatch_pg--<X.Y.Z>.sql "
              "base + --<prev>--<X.Y.Z>.sql migration + .control default_version "
              "+ Cargo version + the cp lines in ci.yml / publish-goldenmatch-pg.yml "
              "(see packages/rust/extensions/CLAUDE.md).")
        return 1

    print("  OK — every #[pg_extern] function is declared in the base SQL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
