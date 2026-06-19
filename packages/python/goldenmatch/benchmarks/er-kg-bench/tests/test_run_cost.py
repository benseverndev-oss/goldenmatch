"""The runner records per-run cost + renders an ``LLM?`` headline column.

Two layers:

* ``_render_headline_table`` is a pure function over the result rows -- the
  column-count invariant (header / separator / every data row agree, including
  the new ``LLM?`` column) is checked here without running the pipeline. Fake
  rows with ``cost.llm_calls`` set / unset assert the yes/no rendering.
* A structural smoke confirms ``run.run(None)`` attaches a ``cost`` dict to each
  non-error row. That offline run pulls goldenmatch (deterministic adapters
  only; no key, no network), so it runs on the shadow venv -- the keyed PAID
  numbers stay CI-only.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))


def _ok_row(name: str, *, llm_calls: int, det: bool = True) -> dict:
    """A minimal non-error headline row in the shape the runner emits."""
    return {
        "name": name,
        "fidelity": "modeled",
        "overall": {"precision": 0.9, "recall": 0.8, "f1": 0.85},
        "per_class_precision": {},
        "time_ms": 1.0,
        "deterministic_floor": det,
        "cost": {"llm_calls": llm_calls, "llm_tokens": llm_calls * 10, "llm_usd": 0.0},
    }


def _cells(row: str) -> int:
    # A markdown table row is `| a | b | ... |`; cell count = pipes - 1.
    return row.count("|") - 1


def test_headline_table_has_llm_column_yes_no():
    from erkgbench.run import _render_headline_table  # pyright: ignore[reportMissingImports]

    rows = [
        _ok_row("paid", llm_calls=4),
        _ok_row("free-a", llm_calls=0),
        _ok_row("free-b", llm_calls=0),
    ]
    lines = _render_headline_table(rows)
    header, sep, *data = lines

    # Header carries the new column, adjacent to det-floor.
    assert "LLM?" in header
    assert "det-floor | LLM? |" in header

    # The paid row reads `yes`; the deterministic rows read `no`.
    assert data[0].rstrip().endswith("| yes |")
    assert data[1].rstrip().endswith("| no |")
    assert data[2].rstrip().endswith("| no |")


def test_headline_table_column_counts_match():
    from erkgbench.run import _render_headline_table  # pyright: ignore[reportMissingImports]

    rows = [
        _ok_row("paid", llm_calls=2),
        _ok_row("free", llm_calls=0),
        # An error row must still emit the same number of cells, or the
        # markdown table breaks.
        {"name": "boom", "fidelity": "modeled", "error": "kaboom"},
    ]
    lines = _render_headline_table(rows)
    header, sep, *data = lines

    n = _cells(header)
    assert _cells(sep) == n
    for d in data:
        assert _cells(d) == n, f"row {d!r} has {_cells(d)} cells, expected {n}"


def test_llm_flag_missing_cost_reads_no():
    # A row with no `cost` key (legacy/defensive) reads `no`, never raises.
    from erkgbench.run import _llm_flag  # pyright: ignore[reportMissingImports]

    assert _llm_flag({"name": "x"}) == "no"
    assert _llm_flag({"name": "x", "cost": None}) == "no"
    assert _llm_flag({"name": "x", "cost": {"llm_calls": 0}}) == "no"
    assert _llm_flag({"name": "x", "cost": {"llm_calls": 1}}) == "yes"


def test_run_attaches_cost_to_every_row():
    # Offline smoke (no key): every non-error row carries a cost dict with the
    # three expected keys. Deterministic adapters report zeros; this asserts the
    # plumbing, not paid numbers. Pulls goldenmatch -> shadow venv.
    from erkgbench import run  # pyright: ignore[reportMissingImports]

    report = run.run(None)
    assert report["results"], "no adapter rows"
    for r in report["results"]:
        if "error" in r:
            continue
        assert "cost" in r, f"{r.get('name')!r} missing cost"
        assert set(r["cost"]) == {"llm_calls", "llm_tokens", "llm_usd"}
        # Offline: nothing should have spent an LLM call.
        assert r["cost"]["llm_calls"] == 0
