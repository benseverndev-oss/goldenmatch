"""Cross-backend graph parity: the DuckDB graph UDFs must return the SAME values
as the underlying ``goldenmatch.native`` kernel for the same input.

The DuckDB UDFs add a columnar wrapper (struct/list shaping, accept-both int/str
dictionary mapping) around the native kernels ``dedup_pairs_max_score`` /
``connected_components``. This test pins that the wrapper is value-transparent:
DuckDB output == native kernel output. The Postgres + DataFusion surfaces assert
the same kernel contract in their own CI lanes (same pinned fixtures), so kernel
parity here transitively covers all three backends.

Runs locally against the pure-Python reference kernel (the ``_native`` ext need
not be built); the native module dispatches to Rust when the ext is present and
the values are identical either way.
"""
from __future__ import annotations

import duckdb
import pytest

from goldenmatch_duckdb.functions import register


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    register(c)
    return c


# A fixed candidate-pair set with a tie, a reversed pair, and a singleton.
EDGES = [(2, 1, 0.5), (1, 2, 0.9), (2, 3, 0.8), (5, 5, 0.4)]
ALL_IDS = [1, 2, 3, 4, 5]


def _cols(edges: list[tuple[int, int, float]]) -> tuple[list, list, list]:
    return ([a for a, _, _ in edges], [b for _, b, _ in edges], [s for _, _, s in edges])


def test_pair_dedup_matches_native_kernel(con: duckdb.DuckDBPyConnection) -> None:
    from goldenmatch.native import dedup_pairs_max_score

    a, b, s = _cols(EDGES)
    duck = con.execute(
        "SELECT goldenmatch_pair_dedup(?, ?, ?)", [a, b, s]
    ).fetchone()[0]
    duck_tuples = sorted((r["a"], r["b"], r["s"]) for r in duck)

    kernel = sorted(dedup_pairs_max_score([(int(x), int(y), float(z)) for x, y, z in EDGES]))
    assert duck_tuples == kernel


def test_connected_components_matches_native_kernel(
    con: duckdb.DuckDBPyConnection,
) -> None:
    from goldenmatch.native import connected_components

    a, b, s = _cols(EDGES)
    duck = con.execute(
        "SELECT goldenmatch_connected_components(?, ?, ?, ?)", [a, b, s, ALL_IDS]
    ).fetchone()[0]
    duck_groups = sorted(sorted(c) for c in duck)

    kernel = connected_components(
        [(int(x), int(y), float(z)) for x, y, z in EDGES], ALL_IDS
    )
    kernel_groups = sorted(sorted(c) for c in kernel)
    assert duck_groups == kernel_groups


def test_str_variant_matches_int_under_mapping(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """The _str UDFs must produce the same grouping as the int UDFs when the
    string ids are a relabeling of the int ids (validates the dict round-trip)."""
    label = {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"}
    a = [label[x] for x, _, _ in EDGES]
    b = [label[y] for _, y, _ in EDGES]
    s = [z for _, _, z in EDGES]
    all_str = [label[x] for x in ALL_IDS]

    duck_str = con.execute(
        "SELECT goldenmatch_connected_components_str(?, ?, ?, ?)", [a, b, s, all_str]
    ).fetchone()[0]
    str_groups = sorted(sorted(c) for c in duck_str)

    inv = {v: k for k, v in label.items()}
    int_groups = sorted(sorted(inv[m] for m in c) for c in duck_str)

    ai, bi, si = _cols(EDGES)
    duck_int = con.execute(
        "SELECT goldenmatch_connected_components(?, ?, ?, ?)", [ai, bi, si, ALL_IDS]
    ).fetchone()[0]
    int_groups_direct = sorted(sorted(c) for c in duck_int)

    assert int_groups == int_groups_direct
    # and the string labels round-trip exactly
    assert str_groups == sorted(sorted(label[m] for m in c) for c in duck_int)
