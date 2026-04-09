"""Example 8 — Score-matrix introspection (new in v0.2)

Demonstrates the `return_score_matrix=True` engine flag and the new
`MapEngine.map_schemas()` API. Useful when you want to:

- show users the runners-up for low-confidence picks,
- build a UI for manual override,
- compute your own ranking metrics on top of the engine output.

Run:
    python examples/08_benchmark_introspection.py
"""
from __future__ import annotations

from infermap import FieldInfo, MapEngine, SchemaInfo


def make_schema(name: str, fields: list[tuple[str, str, list[str]]]) -> SchemaInfo:
    """Tiny helper — real code would use extract_schema() on a CSV/DataFrame."""
    return SchemaInfo(
        fields=[
            FieldInfo(name=n, dtype=dt, sample_values=samples, value_count=len(samples))
            for n, dt, samples in fields
        ],
        source_name=name,
    )


def main() -> None:
    source = make_schema(
        "messy_export",
        [
            ("cust_id", "int64", ["1", "2", "3"]),
            ("e_mail", "string", ["a@x.io", "b@y.io", "c@z.io"]),
            ("amt", "float64", ["19.99", "42.00", "7.50"]),
            ("dt", "string", ["2026-01-01", "2026-01-02", "2026-01-03"]),
        ],
    )
    target = make_schema(
        "warehouse_orders",
        [
            ("customer_id", "int64", ["100", "200", "300"]),
            ("email", "string", ["x@a.io", "y@b.io", "z@c.io"]),
            ("amount_usd", "float64", ["12.34", "56.78", "9.10"]),
            ("order_date", "string", ["2026-02-01", "2026-02-02", "2026-02-03"]),
            ("notes", "string", ["", "", ""]),  # distractor
        ],
    )

    # Opt into the score matrix so we can inspect runners-up.
    engine = MapEngine(return_score_matrix=True)

    # map_schemas() bypasses extract_schema — use it when you already
    # have SchemaInfo in hand. Mirrors the TS `mapSchemas()` API.
    result = engine.map_schemas(source, target)

    print("=== Top picks (above min_confidence) ===")
    for m in result.mappings:
        print(f"  {m.source:>10} -> {m.target:<14} (conf={m.confidence:.3f})")

    print("\n=== Top-3 candidates per source field ===")
    assert result.score_matrix is not None  # because return_score_matrix=True
    for src_name, candidates in result.score_matrix.items():
        ranked = sorted(candidates.items(), key=lambda kv: -kv[1])[:3]
        formatted = ", ".join(f"{tgt}={score:.3f}" for tgt, score in ranked)
        print(f"  {src_name:>10}: {formatted}")

    print(f"\nUnmapped source: {result.unmapped_source}")
    print(f"Unmapped target: {result.unmapped_target}")


if __name__ == "__main__":
    main()
