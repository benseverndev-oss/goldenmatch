"""DuckDB UDFs for the Native Core graph kernels + the local in-house embedder.

The graph UDFs are **native-direct, columnar**: callers pass the WHOLE
candidate-pair / edge columns as DuckDB ``LIST`` arguments (aggregate with
``list(col)``) and get a ``LIST`` back. No JSON wire. Each UDF calls the native
kernel (``goldenmatch.native.dedup_pairs_max_score`` /
``connected_components``) directly -- the Rust kernel when the ``_native`` ext
is built, else the pure-Python reference (identical values either way).

Both int64 ids (fast path) and string ids are supported. DuckDB does NOT allow
overloading one function name across arg signatures, so the string variants are
registered under ``*_str`` names; the int variants keep the bare name.

Exposed in SQL:
- ``goldenmatch_pair_dedup(id_a BIGINT[], id_b BIGINT[], score DOUBLE[])``
  -> ``STRUCT(a BIGINT, b BIGINT, s DOUBLE)[]`` -- canonical max-score pairs.
- ``goldenmatch_pair_dedup_str(id_a VARCHAR[], id_b VARCHAR[], score DOUBLE[])``
  -> ``STRUCT(a VARCHAR, b VARCHAR, s DOUBLE)[]``.
- ``goldenmatch_connected_components(id_a BIGINT[], id_b BIGINT[],
  score DOUBLE[], all_ids BIGINT[])`` -> ``BIGINT[][]`` -- one inner list per
  component; the universe (``all_ids``) makes singletons appear.
- ``goldenmatch_connected_components_str(...VARCHAR variants...)``
  -> ``VARCHAR[][]``.
- ``goldenmatch_embed_local(text, model_path)`` -> JSON float array (unchanged;
  owned by the embed task).

The string variants build a first-seen ``str -> int`` dict, run the int kernel,
then map results back to the original string ids. Bad input fails the query
(no fail-soft JSON envelope) since the columnar shape has no string slot for an
error sentinel -- a malformed list is a binder/type error at call time anyway.

Registered via ``register_core_kernel_functions(con)`` from ``functions.register``.
"""
from __future__ import annotations

import json

import duckdb


def register_core_kernel_functions(con: duckdb.DuckDBPyConnection) -> None:
    """Register the native-kernel graph UDFs + local-embedding UDF."""
    con.create_function(
        "goldenmatch_pair_dedup", _pair_dedup_int,
        ["BIGINT[]", "BIGINT[]", "DOUBLE[]"],
        "STRUCT(a BIGINT, b BIGINT, s DOUBLE)[]",
    )
    con.create_function(
        "goldenmatch_pair_dedup_str", _pair_dedup_str,
        ["VARCHAR[]", "VARCHAR[]", "DOUBLE[]"],
        "STRUCT(a VARCHAR, b VARCHAR, s DOUBLE)[]",
    )
    con.create_function(
        "goldenmatch_connected_components", _connected_components_int,
        ["BIGINT[]", "BIGINT[]", "DOUBLE[]", "BIGINT[]"], "BIGINT[][]",
    )
    con.create_function(
        "goldenmatch_connected_components_str", _connected_components_str,
        ["VARCHAR[]", "VARCHAR[]", "DOUBLE[]", "VARCHAR[]"], "VARCHAR[][]",
    )
    con.create_function(
        "goldenmatch_embed_local", _embed_local,
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )


def _zip_pairs(
    id_a: list, id_b: list, score: list,
) -> list[tuple[int, int, float]]:
    return [(int(a), int(b), float(s)) for a, b, s in zip(id_a, id_b, score)]


def _str_dict(*cols: list) -> tuple[dict, list]:
    """First-seen ``str -> int`` dict over one or more id columns.

    Returns ``(to_int, to_str)`` where ``to_str[i]`` is the original string for
    int code ``i``. Order is first-seen across the columns in argument order.
    """
    to_int: dict = {}
    to_str: list = []
    for col in cols:
        for v in col:
            if v not in to_int:
                to_int[v] = len(to_str)
                to_str.append(v)
    return to_int, to_str


# ── pair_dedup ───────────────────────────────────────────────────────────


def _pair_dedup_int(id_a: list, id_b: list, score: list) -> list[dict]:
    from goldenmatch.native import dedup_pairs_max_score

    out = dedup_pairs_max_score(_zip_pairs(id_a, id_b, score))
    return [{"a": a, "b": b, "s": s} for a, b, s in out]


def _pair_dedup_str(id_a: list, id_b: list, score: list) -> list[dict]:
    from goldenmatch.native import dedup_pairs_max_score

    to_int, to_str = _str_dict(id_a, id_b)
    pairs = [
        (to_int[a], to_int[b], float(s))
        for a, b, s in zip(id_a, id_b, score)
    ]
    out = dedup_pairs_max_score(pairs)
    return [{"a": to_str[a], "b": to_str[b], "s": s} for a, b, s in out]


# ── connected_components ─────────────────────────────────────────────────


def _connected_components_int(
    id_a: list, id_b: list, score: list, all_ids: list,
) -> list[list[int]]:
    from goldenmatch.native import connected_components

    universe = [int(x) for x in all_ids]
    comps = connected_components(_zip_pairs(id_a, id_b, score), universe)
    return [sorted(c) for c in comps]


def _connected_components_str(
    id_a: list, id_b: list, score: list, all_ids: list,
) -> list[list[str]]:
    from goldenmatch.native import connected_components

    to_int, to_str = _str_dict(all_ids, id_a, id_b)
    pairs = [
        (to_int[a], to_int[b], float(s))
        for a, b, s in zip(id_a, id_b, score)
    ]
    universe = [to_int[x] for x in all_ids]
    comps = connected_components(pairs, universe)
    return [sorted(to_str[i] for i in c) for c in comps]


def _embed_local(text: str, model_path: str) -> str:
    try:
        from goldenmatch.embeddings import embed_records

        vecs = embed_records([text], provider="inhouse", model=model_path)
        return json.dumps([round(float(x), 6) for x in vecs[0]])
    except Exception as e:  # noqa: BLE001 - fail-soft per module contract
        return json.dumps({"error": str(e)})
