"""Cross-surface conformance: the dbt ``goldenmatch_key_integrity`` macro's
pure-SQL structural reduction reproduces the SHARED ``key_integrity_golden.json``
oracle.

The structural certifier (group-by uniqueness + fan-out) is single-sourced from
the ``key-integrity-core`` Rust kernel on Python / TS / WASM / DuckDB / Postgres.
The dbt test is DELIBERATELY a separate pure-SQL implementation (no UDF) so it
runs on any warehouse without the extension — but that makes it the one
structural implementation not proven against the canonical golden. This locks
it: render the macro SQL, run it on DuckDB over each golden case's inputs, and
assert the computed ``uniqueness`` / ``max_fan_out`` / per-measure ``fan_out``
match the same certificate the kernel emits.

The golden is read DIRECTLY from ``key-integrity-core/golden/`` (the single
oracle the Rust ``golden.rs``, the Python native-parity test, the DuckDB UDF
test, and the TS/WASM parity test all read) — no copy, no drift surface.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2")
import jinja2.ext  # noqa: E402, F811

duckdb = pytest.importorskip("duckdb")

_MACROS_PATH = Path(__file__).resolve().parents[1] / "macros" / "test_key_integrity.sql"
_GOLDEN = (
    Path(__file__).resolve().parents[3]
    / "rust"
    / "extensions"
    / "key-integrity-core"
    / "golden"
    / "key_integrity_golden.json"
)


class _TestTagExtension(jinja2.ext.Extension):
    tags = {"test"}

    def parse(self, parser):  # noqa: ANN001
        while parser.stream.current.type != "block_end":
            next(parser.stream)
        parser.parse_statements(["name:endtest"], drop_needle=True)
        return []


class _ExceptionsStub:
    @staticmethod
    def raise_compiler_error(msg):  # noqa: ANN001
        raise RuntimeError(msg)


def _load_macros():
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_MACROS_PATH.parent)),
        autoescape=jinja2.select_autoescape(),
        extensions=["jinja2.ext.do", _TestTagExtension],
    )
    env.globals["exceptions"] = _ExceptionsStub()
    env.globals["return"] = lambda v: v
    return env.get_template(_MACROS_PATH.name).module


def _golden_cases():
    return json.loads(_GOLDEN.read_text(encoding="utf-8"))["cases"]


def _make_table(con, name: str, case_input: dict) -> tuple[list[str], list[str]]:
    """Materialize a golden case as a DuckDB table.

    Group columns are stored as VARCHAR (grouping is by equality, so INT-vs-str
    storage is irrelevant to the certificate) named ``g0..gN``; measures are
    DOUBLE named exactly as the measure so the macro's ``MAX(<m>)`` resolves.
    """
    group_columns = case_input["group_columns"]
    measures = case_input["measures"]
    n_rows = case_input["n_rows"]
    gcols = [f"g{i}" for i in range(len(group_columns))]
    mcols = [m["name"] for m in measures]
    coldefs = [f"{g} VARCHAR" for g in gcols] + [f'"{m}" DOUBLE' for m in mcols]
    con.execute(f"CREATE TABLE {name} ({', '.join(coldefs)})")
    if n_rows and (gcols or mcols):
        placeholders = ", ".join(["?"] * (len(gcols) + len(mcols)))
        rows = []
        for i in range(n_rows):
            row = [None if group_columns[j][i] is None else str(group_columns[j][i])
                   for j in range(len(gcols))]
            row += [m["values"][i] for m in measures]
            rows.append(row)
        con.executemany(f"INSERT INTO {name} VALUES ({placeholders})", rows)
    return gcols, mcols


def test_macro_matches_golden():
    macros = _load_macros()
    for case in _golden_cases():
        inp = case["input"]
        exp = case["expected"]
        con = duckdb.connect()
        try:
            gcols, mcols = _make_table(con, "parity", inp)
            # Permissive min_uniqueness (2.0) guarantees the single `scored` row
            # always passes the macro's WHERE, so we read the COMPUTED values
            # (not the pass/fail verdict). max_fan_out huge = no spurious cond.
            sql = macros.goldenmatch_key_integrity_sql(
                model="parity", key=gcols, measures=mcols,
                max_fan_out=1e18, min_uniqueness=2.0,
            )
            rows = con.sql(sql).fetchall()
            assert len(rows) == 1, case["name"]
            row = rows[0]
            uniqueness, max_fan_out = row[0], row[1]
            fan_outs = row[2:]

            # max_fan_out (BIGINT count) vs the golden float.
            assert float(max_fan_out) == exp["max_fan_out"], case["name"]

            # uniqueness = 1 - duplicate_key_groups / n_key_groups; NULL for an
            # empty frame (0/0) — which the golden marks is_unique_at_grain.
            n = exp["n_key_groups"]
            if n == 0:
                assert uniqueness is None, case["name"]
                assert exp["is_unique_at_grain"] is True, case["name"]
            else:
                expected_uniq = 1.0 - exp["duplicate_key_groups"] / n
                assert abs(uniqueness - expected_uniq) < 1e-9, case["name"]
                # is_unique_at_grain ⟺ no duplicate key groups ⟺ uniqueness == 1.
                assert (abs(uniqueness - 1.0) < 1e-9) == exp["is_unique_at_grain"], case["name"]

            # per-measure fan-out, in the macro's measure order (== golden order).
            for m, got in zip(mcols, fan_outs):
                assert abs(got - exp["measure_fan_out"][m]) < 1e-9, f"{case['name']}:{m}"
        finally:
            con.close()
