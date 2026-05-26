"""DuckDB UDFs for the Native Core kernels + the local in-house embedder.

Exposes in SQL:
- ``goldenmatch_connected_components(pairs_json)`` -> JSON list of components.
- ``goldenmatch_pair_dedup(pairs_json)`` -> JSON list of canonical max-score pairs.
- ``goldenmatch_embed_local(text, model_path)`` -> JSON float array.

The graph UDFs wrap ``goldenmatch.native`` (the Rust kernels when the ext is
built, else the pure-Python reference). The embedding UDF wraps the local
in-house embedder (``provider="inhouse"``) — no cloud, no network. ``model_path``
is a directory saved by ``goldenmatch.embeddings.inhouse.GoldenEmbedModel.save``.

JSON in / JSON out, fail-soft to ``{"error": ...}`` (matches the other UDF
modules). ``pairs_json`` is a JSON array of ``[id_a, id_b, score]`` triples;
ids are coerced to int, score to float.

Registered via ``register_core_kernel_functions(con)`` from ``functions.register``.
"""
from __future__ import annotations

import json

import duckdb


def register_core_kernel_functions(con: duckdb.DuckDBPyConnection) -> None:
    """Register the native-kernel + local-embedding UDFs on a connection."""
    con.create_function(
        "goldenmatch_connected_components", _connected_components,
        ["VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "goldenmatch_pair_dedup", _pair_dedup,
        ["VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "goldenmatch_embed_local", _embed_local,
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )


def _parse_pairs(pairs_json: str) -> list[tuple[int, int, float]]:
    raw = json.loads(pairs_json)
    return [(int(p[0]), int(p[1]), float(p[2]) if len(p) > 2 else 1.0) for p in raw]


def _connected_components(pairs_json: str) -> str:
    try:
        from goldenmatch.native import connected_components

        comps = connected_components(_parse_pairs(pairs_json))
        return json.dumps([sorted(c) for c in comps])
    except Exception as e:  # noqa: BLE001 - fail-soft per module contract
        return json.dumps({"error": str(e)})


def _pair_dedup(pairs_json: str) -> str:
    try:
        from goldenmatch.native import dedup_pairs_max_score

        out = dedup_pairs_max_score(_parse_pairs(pairs_json))
        return json.dumps([[a, b, s] for a, b, s in out])
    except Exception as e:  # noqa: BLE001 - fail-soft per module contract
        return json.dumps({"error": str(e)})


def _embed_local(text: str, model_path: str) -> str:
    try:
        from goldenmatch.embeddings import embed_records

        vecs = embed_records([text], provider="inhouse", model=model_path)
        return json.dumps([round(float(x), 6) for x in vecs[0]])
    except Exception as e:  # noqa: BLE001 - fail-soft per module contract
        return json.dumps({"error": str(e)})
