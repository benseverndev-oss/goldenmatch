#!/usr/bin/env python3
"""Emit a package's SQL operation surface as JSON: {package, postgres, duckdb}.

Unlike emit_python_surface.py / emit_ts_surface.mjs (runtime introspection needing
the package installed), this emitter is a **static source parse** — it needs no
pgrx toolchain, no built DuckDB extension, and no `pip install`, so it is box-safe
and runs anywhere the repo is checked out.

The two SQL surfaces are ADVISORY-ONLY in the parity gate (visibility, not a gate):
- `postgres`: the `#[pg_extern]` functions in the goldenmatch_pg pgrx crate — the
  same set `scripts/check_pgrx_sql_sync.py` extracts (Rust fn name == SQL fn name;
  the crate has no `name = "..."` overrides).
- `duckdb`: the UDFs the `goldenmatch_duckdb` Python package registers via
  `con.create_function("<name>", ...)`, plus any `CREATE (OR REPLACE) MACRO <name>`
  it defines (the public name over an internal `_impl` UDF).

Ownership filter: the goldenmatch_pg crate and the goldenmatch_duckdb package both
BUNDLE sibling kernels (goldencheck_* / goldenflow_*). Those belong to the sibling
packages' surfaces, so they are excluded here; goldenmatch keeps everything else
(goldenmatch_* / gm_* / the generic correction_*/memory_*/apply entrypoints).
Internal impls (leading underscore, e.g. `_goldenmatch_autoconfig_impl`) are dropped
in favor of the public macro name.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Sibling-owned prefixes bundled into the goldenmatch SQL artifacts — not goldenmatch's surface.
_SIBLING_PREFIXES = ("goldencheck_", "goldenflow_")

# `#[pg_extern ...]` then the next `fn NAME(` (pgrx maps the Rust fn name straight
# to the SQL function name). Mirrors scripts/check_pgrx_sql_sync.py.
_PG_EXTERN = re.compile(r"#\[pg_extern[^\]]*\]")
_FN_AFTER_ATTR = re.compile(r"\bfn\s+([a-z_][a-z0-9_]*)\s*[(<]")
_DUCKDB_UDF = re.compile(r"""create_function\(\s*["']([a-z_][a-z0-9_]*)["']""")
_DUCKDB_MACRO = re.compile(r"""CREATE\s+(?:OR\s+REPLACE\s+)?MACRO\s+([a-z_][a-z0-9_]*)""", re.IGNORECASE)


def _owned(name: str) -> bool:
    """True when a bundled SQL function belongs to goldenmatch (not a sibling kernel
    or an internal impl helper)."""
    return not name.startswith(_SIBLING_PREFIXES) and not name.startswith("_")


def postgres_functions(src_dir: Path) -> list[str]:
    """Every goldenmatch-owned `#[pg_extern]` in the pgrx crate, sorted + deduped."""
    found: set[str] = set()
    for path in sorted(src_dir.glob("*.rs")):
        text = path.read_text(encoding="utf-8")
        for m in _PG_EXTERN.finditer(text):
            tail = text[m.end():]
            fn = _FN_AFTER_ATTR.search(tail)
            if fn and _owned(fn.group(1)):
                found.add(fn.group(1))
    return sorted(found)


def duckdb_functions(pkg_dir: Path) -> list[str]:
    """Every goldenmatch-owned DuckDB UDF/macro the Python package registers."""
    found: set[str] = set()
    for path in sorted(pkg_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pat in (_DUCKDB_UDF, _DUCKDB_MACRO):
            for m in pat.finditer(text):
                if _owned(m.group(1)):
                    found.add(m.group(1))
    return sorted(found)


# Per-package source locations. Only goldenmatch ships SQL artifacts today; other
# packages emit empty surfaces (the advisory reconciliation skips empties).
_SQL_SOURCES = {
    "goldenmatch": {
        "postgres": ROOT / "packages" / "rust" / "extensions" / "postgres" / "src",
        "duckdb": ROOT / "packages" / "rust" / "extensions" / "duckdb" / "goldenmatch_duckdb",
    },
}


def emit(package: str) -> dict:
    out: dict[str, object] = {"package": package}
    sources = _SQL_SOURCES.get(package, {})
    pg_dir = sources.get("postgres")
    duck_dir = sources.get("duckdb")
    out["postgres"] = postgres_functions(pg_dir) if pg_dir and pg_dir.is_dir() else []
    out["duckdb"] = duckdb_functions(duck_dir) if duck_dir and duck_dir.is_dir() else []
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: emit_sql_surface.py <package>")
    print(json.dumps(emit(sys.argv[1]), sort_keys=True))
