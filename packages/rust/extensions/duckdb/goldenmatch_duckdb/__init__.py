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
"""
__version__ = "0.1.0"

import duckdb

from goldenmatch_duckdb.functions import register

# Auto-register on default connection
try:
    register(duckdb.default_connection())
except Exception:
    pass  # No default connection yet; user calls register(con) explicitly
