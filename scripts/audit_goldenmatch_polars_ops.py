"""AST census of Polars usage in goldenmatch -- W0 op audit for the eviction.

Emits markdown: per-file counts of (a) `pl.<attr>` attribute uses and (b) calls
to a curated list of DataFrame/Series/LazyFrame relational methods. The curated
list makes (b) high-signal: bare method names can collide with non-Polars
objects, so treat per-file counts as an upper bound to hand-verify per wave.

Usage: python scripts/audit_goldenmatch_polars_ops.py
(stdout is markdown; paste under the '## Generated census' heading of
docs/design/2026-07-09-goldenmatch-polars-op-inventory.md)
"""
from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "packages" / "python" / "goldenmatch" / "goldenmatch"

# Relational / frame-shaped methods whose ports define the W1-W4 seam ops.
FRAME_METHODS = {
    "join", "join_asof", "group_by", "groupby", "partition_by", "filter",
    "with_columns", "select", "sort", "unique", "drop_nulls", "concat",
    "explode", "pivot", "melt", "unpivot", "vstack", "hstack", "rename",
    "cast", "lazy", "collect", "scan_csv", "read_csv", "read_parquet",
    "write_csv", "write_parquet", "read_excel", "to_arrow", "from_arrow",
    "agg", "over", "replace_strict", "value_counts", "n_unique",
    "null_count", "is_in", "concat_str", "map_elements", "map_batches",
}


def audit_file(path: Path) -> tuple[Counter, Counter]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pl_attrs: Counter = Counter()
    methods: Counter = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "pl":
                pl_attrs[node.attr] += 1
            elif node.attr in FRAME_METHODS:
                methods[node.attr] += 1
    return pl_attrs, methods


def main() -> int:
    rows = []
    total_attrs: Counter = Counter()
    total_methods: Counter = Counter()
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "_polars_lazy import pl" not in text and "import polars" not in text:
            continue
        try:
            pl_attrs, methods = audit_file(path)
        except SyntaxError as exc:
            print(f"warning: skipping {path} ({exc})", file=sys.stderr)
            continue
        if not pl_attrs and not methods:
            continue
        rel = str(path.relative_to(PKG)).replace("\\", "/")
        rows.append((rel, sum(pl_attrs.values()), sum(methods.values())))
        total_attrs.update(pl_attrs)
        total_methods.update(methods)

    print("# goldenmatch Polars op census (generated)\n")
    print("| file | pl.* uses | relational method calls |")
    print("| --- | ---: | ---: |")
    for rel, a, m in sorted(rows, key=lambda r: -(r[1] + r[2])):
        print(f"| {rel} | {a} | {m} |")
    print("\n## pl.* attribute totals\n")
    for name, n in total_attrs.most_common():
        print(f"- `pl.{name}`: {n}")
    print("\n## Relational method totals (upper bound; hand-verify per wave)\n")
    for name, n in total_methods.most_common():
        print(f"- `.{name}(...)`: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
