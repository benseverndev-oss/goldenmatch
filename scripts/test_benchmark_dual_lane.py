"""`_measure_product` reports both lanes, labelled (#2717).

The product datasets support two genuinely different tasks over the same files,
and a bare F1 does not say which one it answers:

  * dedupe -- both sources concatenated, everything compared to everything,
    scored against the transitive closure of the mapping;
  * linkage -- `match_df(a, b)`, scored against the raw cross-source mapping,
    which is the task the published DeepMatcher / Ditto figures measure.

Measured on Amazon-Google, 67.5% of the dedupe lane's candidate pairs are
same-source and therefore unmatchable against a cross-source mapping. Reporting
one number for both was the framing error; these tests pin that both are
reported and that the historical row keeps its exact name, because
`_F1_FLOORS` and `_QUARANTINE` key on it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_benchmarks  # type: ignore[import-not-found]  # noqa: E402


def _result(f1: float) -> SimpleNamespace:
    return SimpleNamespace(
        f1=f1,
        precision=f1,
        recall=f1,
        true_positives=1,
        false_positives=2,
        false_negatives=3,
        found_pairs=3,
        ground_truth_pairs=4,
    )


@pytest.fixture
def stub_lanes(monkeypatch):
    """Stub both eval helpers and the engine entry points they are handed."""
    import dqbench_adapters.leipzig_eval as leipzig  # type: ignore[import-not-found]

    monkeypatch.setattr(
        leipzig,
        "run_two_source_dedupe_zeroconfig",
        lambda datasets_dir, fn, **kw: _result(0.10),
        raising=True,
    )
    monkeypatch.setattr(
        leipzig,
        "run_two_source_link_zeroconfig",
        lambda datasets_dir, fn, **kw: _result(0.46),
        raising=True,
    )
    monkeypatch.setattr(
        run_benchmarks, "_controller_health", lambda _res: ("yellow", "policy_satisfied")
    )


def test_returns_one_row_per_lane(stub_lanes):
    rows = run_benchmarks._measure_product(Path("."), "amazon-google")
    assert [r["name"] for r in rows] == ["Amazon-Google", "Amazon-Google (linkage)"]
    assert [r["lane"] for r in rows] == ["dedupe", "linkage"]
    assert [r["f1"] for r in rows] == [0.1, 0.46]


def test_historical_row_keeps_its_exact_name(stub_lanes):
    """`_F1_FLOORS` / `_QUARANTINE` key on the name -- renaming the dedupe row
    would silently drop both, so the series must survive the split."""
    rows = run_benchmarks._measure_product(Path("."), "amazon-google")
    dedupe = rows[0]["name"]
    assert dedupe in run_benchmarks._F1_FLOORS
    assert dedupe in run_benchmarks._QUARANTINE


def test_linkage_rows_are_declared_in_the_floors_table():
    """A row with no `_F1_FLOORS` entry is silently unfloored, so both linkage
    rows must appear -- `None` is how "no trustworthy baseline yet" is said out
    loud, and a number is how a real one is."""
    for key in ("abt-buy", "amazon-google"):
        label = run_benchmarks._PRODUCT_SPECS[key]["label"] + " (linkage)"
        assert label in run_benchmarks._F1_FLOORS


def test_abt_buy_linkage_carries_the_floor_that_was_derived_from_it():
    """The 0.45 floor was derived from a LINKAGE run (0.5037 / P 0.8219 / 494
    emitted pairs) and then enforced against the dedupe lane for months, where
    it was recorded as DISPUTED and unreproducible. Now that the linkage row
    exists it carries its own floor, cleared with margin at a measured 0.7024."""
    assert run_benchmarks._F1_FLOORS["Abt-Buy (linkage)"] == 0.45


def test_amazon_google_linkage_stays_unfloored_until_ci_publishes_one():
    """Measured 0.4636 locally, but a local Windows / native-off run is not a
    CI baseline and no floor is claimed on the strength of it."""
    assert run_benchmarks._F1_FLOORS["Amazon-Google (linkage)"] is None


def test_missing_dataset_yields_no_rows(monkeypatch):
    """A skipped dataset must contribute nothing, not a half-filled row."""
    import dqbench_adapters.leipzig_eval as leipzig  # type: ignore[import-not-found]

    monkeypatch.setattr(
        leipzig,
        "run_two_source_dedupe_zeroconfig",
        lambda datasets_dir, fn, **kw: None,
        raising=True,
    )
    assert run_benchmarks._measure_product(Path("."), "abt-buy") == []


def test_a_missing_linkage_lane_does_not_lose_the_dedupe_row(stub_lanes, monkeypatch):
    """The lanes fail independently; one going missing must not take the other."""
    import dqbench_adapters.leipzig_eval as leipzig  # type: ignore[import-not-found]

    monkeypatch.setattr(
        leipzig, "run_two_source_link_zeroconfig", lambda datasets_dir, fn, **kw: None, raising=True
    )
    rows = run_benchmarks._measure_product(Path("."), "amazon-google")
    assert [r["name"] for r in rows] == ["Amazon-Google"]
