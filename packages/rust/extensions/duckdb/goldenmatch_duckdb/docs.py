"""`goldenmatch_docs()` -- the in-SQL orientation function.

An agent that meets GoldenMatch through a SQL connection has no filesystem to
read and no package to import: it sees function names and nothing else, so it
reverse-engineers behaviour from call signatures. This UDF gives that agent the
same authoritative pointer every other surface already ships -- the package's
`llms.txt`, returned as a string, from inside the session it already has.

Deliberately zero-argument and dependency-free: no goldenmatch import, no table
read, no network. It answers "what is this and where are the docs" even on a
connection where every other UDF would fail.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

_LLMS_TXT = Path(__file__).with_name("llms.txt")

# Emitted when the packaged file is somehow absent (a partial install, a zipapp
# that flattened package data). Still answers the question that matters.
_FALLBACK = """\
# goldenmatch-duckdb

GoldenMatch entity resolution as DuckDB SQL functions.

Authoritative sources (read these instead of inferring behaviour from the
function signatures):

- https://docs.bensevern.dev/docs/extensions/sql -- the SQL surface (DuckDB + Postgres)
- https://docs.bensevern.dev/docs/llms.txt -- index of every Golden Suite surface
- https://github.com/benseverndev-oss/goldenmatch -- source, issues, decision records
"""


def goldenmatch_docs() -> str:
    """Return the packaged `llms.txt` for this SQL surface."""
    try:
        return _LLMS_TXT.read_text(encoding="utf-8")
    except OSError:
        return _FALLBACK


def register_docs_functions(con: duckdb.DuckDBPyConnection) -> None:
    """Register `goldenmatch_docs()` on a connection."""
    con.create_function("goldenmatch_docs", goldenmatch_docs, [], "VARCHAR")
