"""`_measure_product` reports the LINKAGE lane only (dedupe retired 2026-08-24).

The `(dedupe)` rows were introduced in #2717 to stop one bare number standing
for two different tasks. Measuring them properly showed the dedupe row was not
measuring deduplication at all. On Abt-Buy at the committed config:

    ground truth   1,097 cross-source (98.1%)  vs     21 same-source (1.9%)
    emitted        1,530 cross-source (48.4%)  vs  1,630 same-source (51.6%)
    false pos      1,057 cross-source          vs  1,621 same-source

98.1% of the truth is cross-source, so the row was linkage wearing a dedupe
API -- while half the engine's candidate budget went to a within-source hunt
holding 21 findable pairs, of which it found 9. That waste also polluted the
score distribution the threshold calibrates against, which is why the row
scored 0.2253 where LINKAGE over the same records scored 0.7024.

Real deduplication on Abt-Buy means finding duplicates WITHIN Abt, and this
dataset carries no ground truth for that. Genuine dedupe coverage lives where
real labels exist: NCVR (single source, synthetic duplicates) at 0.9828 and
Febrl3 at 0.9443 -- the engine deduplicates well, and the retired row made it
look otherwise.

These tests pin that the dedupe row is GONE and that the surviving linkage row
is still declared in the floors table, because `_F1_FLOORS` keys on the name
and an undeclared row is a silently unfloored one.
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
    """Stub the linkage helper and the engine entry point it is handed."""
    import dqbench_adapters.leipzig_eval as leipzig  # type: ignore[import-not-found]

    monkeypatch.setattr(
        leipzig,
        "run_two_source_link_zeroconfig",
        lambda datasets_dir, fn, **kw: _result(0.46),
        raising=True,
    )
    monkeypatch.setattr(
        run_benchmarks, "_controller_health", lambda _res: ("yellow", "policy_satisfied")
    )


def test_returns_only_the_linkage_row(stub_lanes):
    rows = run_benchmarks._measure_product(Path("."), "amazon-google")
    assert [r["name"] for r in rows] == ["Amazon-Google (linkage)"]
    assert [r["lane"] for r in rows] == ["linkage"]
    assert [r["f1"] for r in rows] == [0.46]


def test_no_dedupe_row_is_emitted_for_either_dataset(stub_lanes):
    """The retirement, stated so it cannot silently come back: a reinstated
    dedupe row would need a ground truth this dataset does not have."""
    for key in ("abt-buy", "amazon-google"):
        rows = run_benchmarks._measure_product(Path("."), key)
        assert not [r for r in rows if r["lane"] == "dedupe"], (
            f"{key} emitted a dedupe row: {[r['name'] for r in rows]}"
        )


def test_the_retired_rows_are_gone_from_the_tables():
    """`_F1_FLOORS` / `_QUARANTINE` key on the row NAME. A retired row left in
    either table is a floor or a quarantine guarding something nothing emits --
    and a quarantine entry in particular claims an open issue is being tracked."""
    for key in ("abt-buy", "amazon-google"):
        label = run_benchmarks._PRODUCT_SPECS[key]["label"] + " (dedupe)"
        assert label not in run_benchmarks._F1_FLOORS
        assert label not in run_benchmarks._QUARANTINE


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
    it was recorded as DISPUTED and unreproducible. It now guards the lane it
    describes, cleared with margin at a measured 0.7024."""
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
        "run_two_source_link_zeroconfig",
        lambda datasets_dir, fn, **kw: None,
        raising=True,
    )
    assert run_benchmarks._measure_product(Path("."), "amazon-google") == []
