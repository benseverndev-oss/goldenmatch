"""GoldenMatch functions for DuckDB.

Registers entity resolution functions as DuckDB UDFs.

Usage:
    import duckdb
    import goldenmatch_duckdb

    # Functions are auto-registered on the default connection.
    # For a specific connection:
    goldenmatch_duckdb.register(con)

    # Score two strings
    con.sql("SELECT goldenmatch_score('John Smith', 'Jon Smyth', 'jaro_winkler')")

    # Deduplicate a table
    con.sql("SELECT goldenmatch_dedupe_table('customers', '{\"exact\": [\"email\"]}')")

AUTHORITATIVE SOURCES (read these instead of inferring behaviour from the UDF
wrappers -- they are thin marshaling shims, the semantics live upstream):

  * ``goldenmatch_duckdb/llms.txt`` -- ships INSIDE this package, next to this
    file. From inside a SQL session: ``SELECT goldenmatch_docs();``.
  * https://docs.bensevern.dev/extensions/sql -- the SQL surface.
  * ``goldenmatch/llms.txt`` -- the engine these UDFs call.
  * https://github.com/benseverndev-oss/goldenmatch -- source + issues.

Why this block exists: which capabilities are in SQL, which are deliberately
deferred (PPRL, streaming, LLM, boost/rerank, graph ER), and which fallbacks are
intentional are all *decisions* -- documented, and not visible from a signature.
"""
__version__ = "0.4.0"

import duckdb

from goldenmatch_duckdb.functions import register

# Auto-register on default connection
try:
    register(duckdb.default_connection())
except Exception:
    pass  # No default connection yet; user calls register(con) explicitly
