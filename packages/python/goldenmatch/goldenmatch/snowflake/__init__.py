"""Snowflake handler module -- the inside of the goldenmatch UDF surface.

``packages/python/goldenmatch/dbt-goldensuite/macros/`` and
``packages/python/goldenmatch/goldenmatch/cli/snowflake.py`` are the
*outside* of the Snowflake surface: how Snowflake calls into
goldenmatch. This subpackage is the *inside*: the Python functions
Snowflake's UDF / Stored Procedure HANDLER clauses point at.

Re-exports ``goldenmatch.snowflake.udfs`` symbols at the top level so
the HANDLER paths in the CLI catalog (``goldenmatch_udfs.foo``) can
also be expressed as ``goldenmatch.snowflake.foo`` once a goldenmatch
release ships them.

Phase 1 in this module ships scalar UDF handlers (transforms +
identity reads). Phase 2 will ship Stored Procedure handlers for
the table-reading operations (dedupe, quality scans, correction
writes) -- those need a Snowpark ``Session`` which is only
available inside Stored Procedures, not pure UDFs.
"""
from __future__ import annotations

from goldenmatch.snowflake import udfs  # noqa: F401  (re-export)

__all__ = ["udfs"]
