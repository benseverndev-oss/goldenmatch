"""Snowpark Container Services adapter for goldenmatch.

Exposes the same surface as the Snowpark Python UDFs in
``docs/snowflake-setup.md`` -- identity reads, quality scans,
goldenflow transforms, learning-memory writes, and the three dedupe
output shapes -- but backed by ``goldenmatch[native]`` for native
acceleration.

Snowflake calls this service via the documented batched-request
contract:

    POST /<endpoint>
    Content-Type: application/json
    {"data": [[row_idx, arg1, arg2, ...], [row_idx, arg1, arg2, ...]]}

The handler must return:

    {"data": [[row_idx, col_a, col_b, ...], ...]}

For UDTFs the response can carry multiple output rows per input row
-- repeat the same ``row_idx`` for each.

Reference: Snowflake docs, "Creating a service function".

## Status: structural scaffold

The HTTP contract, batching shape, and route table are complete and
match the Snowflake SPCS spec. The bodies of the per-operation
handlers below are intentionally stubbed -- they map cleanly onto
existing goldenmatch / goldencheck / goldenflow Python entry points,
but the exact attribute names should be re-verified against the
installed version's API surface before deploying. Each stub is
marked with `# TODO(spcs): wire to ...` so they're easy to grep.

End-to-end deploy verification (build image, push to Snowflake image
registry, create compute pool + service, call from SQL) is not
included in this PR -- see docs/snowflake-spcs.md for the deploy
walkthrough.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

# Flask is the runtime dep declared in the Dockerfile alongside
# goldenmatch[native]. The local type-checker will flag this when
# running outside the SPCS image -- expected.
from flask import Flask, jsonify, request  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

DEFAULT_IDENTITY_DB = os.environ.get(
    "GOLDENMATCH_IDENTITY_DB", "/data/identity.db",
)
DEFAULT_MEMORY_DB = os.environ.get(
    "GOLDENMATCH_MEMORY_DB", "/data/memory.db",
)


# ---------------------------------------------------------------------------
# Snowflake batched-request helpers
# ---------------------------------------------------------------------------


def _scalar_handler(fn: Callable[..., Any]):
    """Wrap a per-row callable as a scalar Snowflake service endpoint."""

    def view():
        payload = request.get_json(force=True) or {}
        rows: list[list[Any]] = payload.get("data") or []
        out = []
        for row in rows:
            row_idx, *args = row
            try:
                result = fn(*args)
            except Exception as exc:  # noqa: BLE001
                # SPCS surfaces this as a NULL with a warning in the
                # query history. Logging it lets us debug the worker.
                logger.exception("scalar fn %s failed: %s", fn.__name__, exc)
                result = None
            out.append([row_idx, result])
        return jsonify({"data": out})

    view.__name__ = fn.__name__
    return view


def _table_handler(fn: Callable[..., list[list[Any]]]):
    """Wrap a per-row callable returning a list-of-rows as a UDTF endpoint."""

    def view():
        payload = request.get_json(force=True) or {}
        rows: list[list[Any]] = payload.get("data") or []
        out: list[list[Any]] = []
        for row in rows:
            row_idx, *args = row
            try:
                results = fn(*args) or []
            except Exception as exc:  # noqa: BLE001
                logger.exception("table fn %s failed: %s", fn.__name__, exc)
                results = []
            for r in results:
                out.append([row_idx, *r])
        return jsonify({"data": out})

    view.__name__ = fn.__name__
    return view


# ---------------------------------------------------------------------------
# Shared handler module -- the same goldenmatch.snowflake.udfs functions
# that the Snowpark Python UDFs call into. SPCS just exposes them via
# HTTP instead of being invoked by Snowflake directly. Single source of
# truth for the goldenmatch-side glue keeps the two paths bit-identical.
# ---------------------------------------------------------------------------


from goldenmatch.snowflake import udfs as _gm  # noqa: E402


def _identity_resolve(record_id: str, db_path: str) -> Any:
    return _gm.identity_resolve(record_id, db_path or DEFAULT_IDENTITY_DB)


def _identity_view(entity_id: str, db_path: str) -> Any:
    return _gm.identity_view(entity_id, db_path or DEFAULT_IDENTITY_DB)


def _identity_history(entity_id: str, db_path: str) -> Any:
    return _gm.identity_history(entity_id, db_path or DEFAULT_IDENTITY_DB)


def _identity_conflicts(dataset: str, db_path: str) -> Any:
    return _gm.identity_conflicts(dataset, db_path or DEFAULT_IDENTITY_DB)


def _identity_list(dataset: str, status: str, db_path: str) -> Any:
    return _gm.identity_list(dataset, status, db_path or DEFAULT_IDENTITY_DB)


# ---------------------------------------------------------------------------
# GoldenCheck / quality -- Phase 2 (table-reading; needs Snowpark Session).
# ---------------------------------------------------------------------------


def _goldencheck_scan_table(relation: str, domain: str) -> str:
    # Mirrors the goldenmatch.snowflake.udfs.scan_table scaffold --
    # Phase 2, gated on the Snowflake-native goldencheck profiler.
    return _gm.scan_table(relation, domain)


def _goldencheck_health_score(relation: str) -> float:
    return _gm.health_score(relation)


# ---------------------------------------------------------------------------
# GoldenFlow transforms -- map server endpoint name -> handler function.
# ---------------------------------------------------------------------------


_FLOW_HANDLERS = {
    "normalize_email":       _gm.normalize_email,
    "normalize_phone":       _gm.normalize_phone,
    "normalize_date":        _gm.normalize_date,
    "normalize_name_proper": _gm.normalize_name_proper,
    "canonicalize_url":      _gm.canonicalize_url,
    "canonicalize_address":  _gm.canonicalize_address,
    "strip":                 _gm.strip,
    "whitespace_normalize":  _gm.whitespace_normalize,
}


def _make_flow(name: str):
    fn = _FLOW_HANDLERS[name]

    def call(value: str | None) -> str | None:
        return fn(value)

    call.__name__ = f"flow_{name}"
    return call


# ---------------------------------------------------------------------------
# Learning memory writes
# ---------------------------------------------------------------------------


def _correction_add(
    decision: str, dataset: str, memory_path: str, args_json: str,
) -> str:
    return _gm.correction_add(
        decision, dataset, memory_path or DEFAULT_MEMORY_DB, args_json,
    )


# ---------------------------------------------------------------------------
# Dedupe -- the three output shapes. Phase 2 in goldenmatch.snowflake.udfs;
# SPCS exposes them once the SP migration lands.
# ---------------------------------------------------------------------------


def _dedupe_full(input_table: str, config_json: str) -> list[list[Any]]:
    rows = _gm.DedupeFull().process(input_table, config_json)
    return [list(r) for r in rows] if rows else []


def _dedupe_clusters(input_table: str, config_json: str) -> list[list[Any]]:
    rows = _gm.DedupeClusters().process(input_table, config_json)
    return [list(r) for r in rows] if rows else []


def _dedupe_pairs(input_table: str, config_json: str) -> list[list[Any]]:
    rows = _gm.DedupePairs().process(input_table, config_json)
    return [list(r) for r in rows] if rows else []


# ---------------------------------------------------------------------------
# Route table -- mirrors the SQL function names in
# docs/snowflake-setup.md, one HTTP endpoint per UDF.
# ---------------------------------------------------------------------------


app.add_url_rule(
    "/identity-resolve", view_func=_scalar_handler(_identity_resolve),
    methods=["POST"],
)
app.add_url_rule(
    "/identity-view", view_func=_scalar_handler(_identity_view),
    methods=["POST"],
)
app.add_url_rule(
    "/identity-history", view_func=_scalar_handler(_identity_history),
    methods=["POST"],
)
app.add_url_rule(
    "/identity-conflicts", view_func=_scalar_handler(_identity_conflicts),
    methods=["POST"],
)
app.add_url_rule(
    "/identity-list", view_func=_scalar_handler(_identity_list),
    methods=["POST"],
)

app.add_url_rule(
    "/goldencheck-scan-table",
    view_func=_scalar_handler(_goldencheck_scan_table),
    methods=["POST"],
)
app.add_url_rule(
    "/goldencheck-health-score",
    view_func=_scalar_handler(_goldencheck_health_score),
    methods=["POST"],
)

app.add_url_rule(
    "/correction-add",
    view_func=_scalar_handler(_correction_add),
    methods=["POST"],
)

for _name in (
    "normalize_email", "normalize_phone", "normalize_date",
    "normalize_name_proper", "canonicalize_url", "canonicalize_address",
    "strip", "whitespace_normalize",
):
    app.add_url_rule(
        f"/goldenflow-{_name.replace('_', '-')}",
        view_func=_scalar_handler(_make_flow(_name)),
        methods=["POST"],
        endpoint=f"goldenflow-{_name}",
    )

app.add_url_rule(
    "/dedupe-full", view_func=_table_handler(_dedupe_full),
    methods=["POST"],
)
app.add_url_rule(
    "/dedupe-clusters", view_func=_table_handler(_dedupe_clusters),
    methods=["POST"],
)
app.add_url_rule(
    "/dedupe-pairs", view_func=_table_handler(_dedupe_pairs),
    methods=["POST"],
)


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


if __name__ == "__main__":  # pragma: no cover
    # Local dev path: `python server.py`. Production uses gunicorn
    # via the Dockerfile CMD.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
