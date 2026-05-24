"""Emit cross-language parity fixtures for the goldenpipe TS port.

Runs the Python ``goldenpipe.run`` (file path → full check→flow→dedupe chain)
on small CSV fixtures and dumps the stable, cross-language-robust invariants of
the resulting ``PipeResult`` to JSON under the TS package's fixtures dir.

The TS siblings are version-skewed from the Python ones, so we deliberately
emit only the invariants that survive the skew:
  - ``status`` (pipe-level)
  - ``input_rows``
  - ``stages`` (ordered list of ``{name, status}``) — the skip/run sequence
  - ``skipped`` (stage names)
  - ``golden`` / ``unique`` final row counts

Run with:
    uv run --project packages/python/goldenpipe python \
        packages/python/goldenpipe/scripts/emit_ts_parity_fixtures.py
"""
from __future__ import annotations

import json
from pathlib import Path

import goldenpipe

# Output dir: packages/typescript/goldenpipe/tests/fixtures/
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[4]
OUT_DIR = _REPO_ROOT / "packages" / "typescript" / "goldenpipe" / "tests" / "fixtures"


# Each case: (id, csv_text). Kept tiny + deterministic.
CASES: list[tuple[str, str]] = [
    (
        "people_dupes",
        (
            "first_name,last_name,email,city,state\n"
            "John,Smith,john@example.com,Boston,MA\n"
            "Jon,Smith,john@example.com,Boston,MA\n"
            "Jane,Doe,jane@example.com,Austin,TX\n"
            "Jane,Doe,jane@example.com,Austin,TX\n"
            "Robert,Jones,bob@example.com,Denver,CO\n"
        ),
    ),
    (
        "single_row",
        (
            "first_name,last_name,email,city,state\n"
            "Solo,Person,solo@example.com,Reno,NV\n"
        ),
    ),
    (
        "all_unique",
        (
            "first_name,last_name,email,city,state\n"
            "Alice,Adams,alice@example.com,Miami,FL\n"
            "Bob,Brown,bob@example.com,Tulsa,OK\n"
            "Carol,Clark,carol@example.com,Akron,OH\n"
        ),
    ),
]


def _row_count(artifact: object) -> int | None:
    """Best-effort row count for a polars DataFrame artifact."""
    if artifact is None:
        return None
    height = getattr(artifact, "height", None)
    if height is not None:
        return int(height)
    try:
        return len(artifact)  # type: ignore[arg-type]
    except TypeError:
        return None


def emit_case(case_id: str, csv_text: str, tmp_dir: Path) -> dict:
    csv_path = tmp_dir / f"{case_id}.csv"
    csv_path.write_text(csv_text)

    result = goldenpipe.run(str(csv_path))

    golden = result.artifacts.get("golden")
    unique = result.artifacts.get("unique")

    return {
        "id": case_id,
        "input_csv": csv_text,
        "status": result.status.value,
        "input_rows": result.input_rows,
        "stages": [
            {"name": name, "status": sr.status.value}
            for name, sr in result.stages.items()
        ],
        "skipped": list(result.skipped),
        "golden_count": _row_count(golden),
        "unique_count": _row_count(unique),
    }


def main() -> None:
    import tempfile

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases: list[dict] = []
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        for case_id, csv_text in CASES:
            cases.append(emit_case(case_id, csv_text, tmp_dir))

    out_path = OUT_DIR / "pipe_parity.json"
    payload = {
        "_comment": (
            "Cross-language parity goldens emitted by "
            "scripts/emit_ts_parity_fixtures.py. Only skew-robust invariants are "
            "asserted by the TS parity test."
        ),
        "python_version": goldenpipe.__version__,
        "cases": cases,
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {len(cases)} parity case(s) to {out_path}")
    for c in cases:
        print(
            f"  {c['id']}: status={c['status']} rows={c['input_rows']} "
            f"golden={c['golden_count']} unique={c['unique_count']} "
            f"stages={[s['status'] for s in c['stages']]}"
        )


if __name__ == "__main__":
    main()
