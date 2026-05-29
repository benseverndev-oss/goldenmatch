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

import json
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
# Identity graph reads -- thin wrappers around goldenmatch.identity
# ---------------------------------------------------------------------------


def _identity_resolve(record_id: str, db_path: str) -> Any:
    # TODO(spcs): wire to the goldenmatch.identity entry point that
    # `goldenmatch_identity_resolve` in the DuckDB / pgrx extensions
    # already calls. The Postgres + DuckDB SQL UDFs return JSON in
    # the same shape, so the same Python helper should serve here.
    raise NotImplementedError(
        f"identity_resolve({record_id!r}, db={db_path or DEFAULT_IDENTITY_DB!r})"
    )


def _identity_view(entity_id: str, db_path: str) -> Any:
    # TODO(spcs): wire to goldenmatch.identity equivalent of
    # `goldenmatch_identity_view`.
    raise NotImplementedError(
        f"identity_view({entity_id!r}, db={db_path or DEFAULT_IDENTITY_DB!r})"
    )


def _identity_history(entity_id: str, db_path: str) -> Any:
    raise NotImplementedError(
        f"identity_history({entity_id!r}, db={db_path or DEFAULT_IDENTITY_DB!r})"
    )


def _identity_conflicts(dataset: str, db_path: str) -> Any:
    raise NotImplementedError(
        f"identity_conflicts({dataset!r}, db={db_path or DEFAULT_IDENTITY_DB!r})"
    )


def _identity_list(dataset: str, status: str, db_path: str) -> Any:
    raise NotImplementedError(
        f"identity_list(dataset={dataset!r}, status={status!r}, "
        f"db={db_path or DEFAULT_IDENTITY_DB!r})"
    )


# ---------------------------------------------------------------------------
# GoldenCheck / quality
# ---------------------------------------------------------------------------


def _goldencheck_scan_table(relation: str, domain: str) -> str:
    # TODO(spcs): wire to goldencheck's scan entry point. The DuckDB
    # UDF `goldencheck_scan_table` returns a JSON array of findings
    # in this exact shape; reuse the same serializer.
    raise NotImplementedError(
        f"goldencheck_scan_table({relation!r}, domain={domain!r})"
    )


def _goldencheck_health_score(relation: str) -> float:
    # TODO(spcs): wire to goldencheck's health-score entry point.
    raise NotImplementedError(f"goldencheck_health_score({relation!r})")


# ---------------------------------------------------------------------------
# GoldenFlow transforms
# ---------------------------------------------------------------------------


def _make_flow(name: str):
    # TODO(spcs): import the resolved transform once -- module-level,
    # not per-request -- and call it here. The DuckDB UDFs already do
    # this for each transform; mirror the same import path.

    def call(value: str | None) -> str | None:
        if value is None:
            return None
        raise NotImplementedError(f"goldenflow_{name}({value!r})")

    call.__name__ = f"flow_{name}"
    return call


# ---------------------------------------------------------------------------
# Learning memory writes
# ---------------------------------------------------------------------------


def _correction_add(
    decision: str, dataset: str, memory_path: str, args_json: str,
) -> str:
    # TODO(spcs): wire to goldenmatch.core.memory.store.add_correction.
    # The DuckDB UDF `goldenmatch_correction_add` already takes the
    # same (decision, dataset, memory_path, args_json) tuple --
    # reuse its handler verbatim.
    args = json.loads(args_json or "{}")
    raise NotImplementedError(
        f"correction_add(decision={decision!r}, dataset={dataset!r}, "
        f"memory_path={memory_path or DEFAULT_MEMORY_DB!r}, args={args!r})"
    )


# ---------------------------------------------------------------------------
# Dedupe -- the three output shapes
# ---------------------------------------------------------------------------


def _dedupe_full(input_table: str, config_json: str) -> list[list[Any]]:
    # TODO(spcs): use Snowpark Session to read the input table into
    # a Polars frame, then call goldenmatch.dedupe_df(df, config=cfg)
    # and emit one [cluster_id, golden_variant] row per cluster.
    # Reference the DuckDB UDF `goldenmatch_dedupe_full` for the
    # exact serialization shape.
    raise NotImplementedError(
        f"dedupe_full(input_table={input_table!r}, "
        f"config_bytes={len(config_json)})"
    )


def _dedupe_clusters(input_table: str, config_json: str) -> list[list[Any]]:
    # TODO(spcs): emit one [cluster_id, member_id, score] row per
    # cluster member. Same input + config handling as _dedupe_full.
    raise NotImplementedError(
        f"dedupe_clusters(input_table={input_table!r})"
    )


def _dedupe_pairs(input_table: str, config_json: str) -> list[list[Any]]:
    # TODO(spcs): emit one [id_a, id_b, score] row per scored pair
    # above the configured threshold. The Postgres pgrx UDF
    # `goldenmatch_dedupe_pairs` is the reference shape.
    raise NotImplementedError(
        f"dedupe_pairs(input_table={input_table!r})"
    )


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
