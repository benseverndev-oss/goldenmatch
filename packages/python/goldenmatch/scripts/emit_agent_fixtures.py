#!/usr/bin/env python3
"""Emit Python AgentSession decision goldens for the TypeScript port (Wave 1).

Writes tests/parity/fixtures/agent-decisions.json: the analyze() reasoning,
autoconfigure() telemetry shape, and select_strategy() decision for a fixed set
of datasets, so the TS port can be asserted against Python behavior.

Determinism / parity notes:
  * Datasets contain NO nulls. polars `n_unique` counts null as a distinct
    value while the TS profiler computes distinct over non-nulls, so a
    null-bearing column would diverge. Null-rate logic is covered by TS unit
    tests instead.
  * Numeric columns use pure-digit cell strings so polars infers Int64
    (numeric) and the TS profiler's "all values numeric-parseable -> numeric"
    rule agrees.
  * domain_extraction is intentionally OMITTED from the cross-language fixture:
    Python's domain_confidence is hits/len(signals) but the TS uses
    detectDomain().confidence (= score/10), so the branch is TS-unit-tested
    only (documented Wave-1 divergence).
"""
import csv
import io
import json
import tempfile
from pathlib import Path

from goldenmatch.core.agent import (
    AgentSession,
    DataProfile,
    FieldProfile,
    select_strategy,
)

OUT = (
    Path(__file__).resolve().parents[3]
    / "typescript/goldenmatch/tests/parity/fixtures/agent-decisions.json"
)

# (name, list-of-row-dicts). NO nulls; numeric columns are pure-digit strings.
ROW_DATASETS: list[tuple[str, list[dict]]] = [
    ("sensitive", [
        {"ssn": "111-22-3333", "name": "Alice"},
        {"ssn": "444-55-6666", "name": "Bob"},
        {"ssn": "777-88-9999", "name": "Carol"},
    ]),
    ("strong_id_only", [
        {"customer_id": "C001", "amount": "100"},
        {"customer_id": "C002", "amount": "200"},
        {"customer_id": "C003", "amount": "300"},
        {"customer_id": "C004", "amount": "400"},
        {"customer_id": "C005", "amount": "500"},
    ]),
    ("strong_plus_fuzzy", [
        {"customer_id": "C001", "full_name": "Alice Smith"},
        {"customer_id": "C002", "full_name": "Alice Smith"},
        {"customer_id": "C003", "full_name": "Bob Jones"},
        {"customer_id": "C004", "full_name": "Carol White"},
        {"customer_id": "C005", "full_name": "Dave Brown"},
    ]),
    ("fuzzy_only", [
        {"full_name": "Alice Smith"},
        {"full_name": "Alice Smith"},
        {"full_name": "Bob Jones"},
        {"full_name": "Carol White"},
    ]),
    ("fallback_numeric_only", [
        {"v1": "10", "v2": "20"},
        {"v1": "11", "v2": "21"},
        {"v1": "12", "v2": "22"},
    ]),
]


def _rows_to_csv(rows: list[dict]) -> str:
    # lineterminator="\n" (not the csv default "\r\n") so polars doesn't absorb
    # a trailing "\r" into the last column name / last cell value on Windows.
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _decision_json(d) -> dict:
    return {
        "strategy": d.strategy,
        "why": d.why,
        "domain": d.domain,
        "strong_ids": list(d.strong_ids),
        "fuzzy_fields": list(d.fuzzy_fields),
        "backend": d.backend,
        "auto_execute": d.auto_execute,
    }


def _emit_rows_case(name: str, rows: list[dict]) -> dict:
    csv_text = _rows_to_csv(rows)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8", newline=""
    ) as fh:
        fh.write(csv_text)
        path = fh.name
    try:
        analyze = AgentSession().analyze(path)
    finally:
        Path(path).unlink(missing_ok=True)
    # autoconfigure() telemetry is NOT captured here: it delegates to the
    # already-parity-tested autoConfigureRowsIterate, and it raises
    # ConfigValidationError on no-matchkey data (e.g. numeric-only) -- covered
    # by TS unit tests instead. The keystone parity is analyze + select_strategy.
    return {
        "name": name,
        "rows": rows,
        "analyze": analyze,
    }


def _emit_profile_case(name: str, profile: DataProfile) -> dict:
    decision = select_strategy(profile)
    return {
        "name": name,
        "profile": {
            "row_count": profile.row_count,
            "fields": [
                {
                    "name": f.name,
                    "type": f.type,
                    "uniqueness": f.uniqueness,
                    "null_rate": f.null_rate,
                    "avg_length": f.avg_length,
                }
                for f in profile.fields
            ],
            "has_sensitive": profile.has_sensitive,
        },
        "decision": _decision_json(decision),
    }


def main() -> None:
    rows_cases = [_emit_rows_case(name, rows) for name, rows in ROW_DATASETS]

    # >500k-row backend=ray case: hand-build the profile (no 500k-row frame).
    large_profile = DataProfile(
        row_count=600_000,
        fields=[
            FieldProfile(
                name="customer_id", type="string",
                uniqueness=0.99, null_rate=0.0, avg_length=8.0,
            )
        ],
        has_sensitive=False,
    )
    profile_cases = [_emit_profile_case("large_ray", large_profile)]

    payload = {
        "_meta": {
            "note": (
                "Python AgentSession goldens for the TS port (Wave 1). "
                "No nulls (polars n_unique counts null; TS does not). "
                "domain_extraction omitted (Python hits/len(signals) vs TS "
                "detectDomain score/10 -- TS-unit-tested only)."
            ),
        },
        "rows_cases": rows_cases,
        "profile_cases": profile_cases,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(rows_cases)} rows + {len(profile_cases)} profile cases -> {OUT}")


if __name__ == "__main__":
    main()
