"""DuckDB UDF for the structural key-integrity certifier (the semantic wedge).

``goldenmatch_certify_structural(input_json VARCHAR) -> VARCHAR`` delegates to
``goldenmatch.semantic.certify_structural_json`` -- the SAME reference the
Postgres ``goldenmatch_certify_structural`` (native ``key-integrity-core``) and
the ``goldenmatch[native]`` wheel run -- so the certificate is byte-identical
across Python / TS / DuckDB / Postgres.

JSON in / JSON out (the core's native contract; measures make a columnar
signature unwieldy):

- in : ``{"n_rows": N, "group_columns": [[..], ..],
  "measures": [{"name": .., "values": [..]}, ..]}`` -- the group columns are the
  declared key, or key+grain (the caller picks).
- out: ``{"n_rows", "n_key_groups", "duplicate_key_groups", "max_fan_out",
  "is_unique_at_grain", "measure_fan_out": {name: ratio}}``.

Build the input JSON from columns SQL-side, e.g.::

    SELECT goldenmatch_certify_structural(
      json_object('n_rows', (SELECT count(*) FROM t),
                  'group_columns', json_array((SELECT list(customer_id) FROM t)),
                  'measures', json_array(
                    json_object('name', 'amount',
                                'values', (SELECT list(amount) FROM t)))));

Non-STRICT + fail-soft: invalid JSON / a wrong-shaped input returns a
``{"error": ..}`` envelope (matching the Postgres surface + the DuckDB
convention), never raising, so a row-wise ``SELECT`` over mixed input doesn't
abort.

Registered via ``register_key_integrity_functions(con)`` from
``functions.register`` (fail-open if ``goldenmatch.semantic`` is not importable).
"""
from __future__ import annotations

import json

import duckdb


def register_key_integrity_functions(con: duckdb.DuckDBPyConnection) -> None:
    """Register the structural key-integrity certifier UDF.

    Fail-open: if ``goldenmatch.semantic`` is not importable the function is
    skipped (matching how the sibling optional-dependency modules guard).
    """
    try:
        from goldenmatch.semantic import certify_structural_json  # noqa: F401
    except Exception:  # noqa: BLE001 - semantic subpackage optional at import time
        return

    con.create_function(
        "goldenmatch_certify_structural",
        _certify_structural,
        ["VARCHAR"],
        "VARCHAR",
    )


def _certify_structural(input_json: str) -> str:
    from goldenmatch.semantic import certify_structural_json

    try:
        return certify_structural_json(input_json)
    except Exception as exc:  # noqa: BLE001 - fail-soft to a JSON envelope
        return json.dumps({"error": str(exc)})
