"""The committed `orgs_hard` corpus must stay pinned to its generator.

The corpus is COMMITTED rather than generated at load time on purpose: a
quality baseline is measured against these exact bytes, so a generator edit
must not be able to move them silently. This asserts the generator still
reproduces the committed files, and that the corpus keeps the properties that
make it worth having.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.suggest_quality.datasets import REGISTRY  # noqa: E402
from scripts.suggest_quality.gen_orgs_hard import (  # noqa: E402
    RECORDS_CSV,
    TRUTH_CSV,
    build,
)

_NEGATIVE_CLASSES = {"branch", "parent_sub", "common_token"}


def _records() -> pl.DataFrame:
    return pl.read_csv(RECORDS_CSV)


def test_generator_reproduces_the_committed_records():
    """A generator edit that changes the data must fail here, loudly."""
    rows, _ = build()
    committed = list(csv.DictReader(RECORDS_CSV.open(encoding="utf-8")))
    assert len(rows) == len(committed)
    for got, want in zip(rows, committed):
        assert {k: str(v) for k, v in got.items()} == dict(want)


def test_generator_reproduces_the_committed_truth():
    _, truth = build()
    committed = {
        (int(r["row_a"]), int(r["row_b"]))
        for r in csv.DictReader(TRUTH_CSV.open(encoding="utf-8"))
    }
    assert truth == committed


def test_truth_indices_are_in_range():
    """Off-by-one truth silently scores 0 rather than erroring."""
    n = _records().height
    _, truth = build()
    assert all(0 <= a < n and 0 <= b < n and a < b for a, b in truth)


def test_registered_and_always_available():
    """It is an `anchor`: committed, so it must never report absent."""
    ds = {d.name: d for d in REGISTRY}["orgs_hard"]
    assert ds.kind == "anchor"
    loaded = ds.loader()
    assert loaded is not None
    df, gt = loaded
    assert df.height > 0 and len(gt) > 0
    # `hardness` is a diagnostic label, never an input feature.
    assert "hardness" not in df.columns


def test_carries_hard_negatives():
    """A corpus of only positives measures recall and flatters any config
    that merges aggressively. The negatives are what test a threshold."""
    counts = dict(
        _records().group_by("hardness").len().iter_rows()
    )
    for cls in _NEGATIVE_CLASSES:
        assert counts.get(cls, 0) >= 20, f"{cls} too rare to bite: {counts}"


def test_hard_negatives_are_not_in_the_truth_set():
    """`branch` / `parent_sub` / `common_token` rows are DISTINCT entities.
    If any landed in the truth set the corpus would be rewarding over-merge."""
    df = _records()
    _, truth = build()
    hardness = df["hardness"].to_list()
    # A negative-class row may still be a truth member only via its OWN
    # duplicates; these classes are generated as singleton entities, so none
    # should appear in any truth pair.
    in_truth = {i for pair in truth for i in pair}
    offenders = [
        i for i in in_truth if hardness[i] in _NEGATIVE_CLASSES
    ]
    assert not offenders, f"negative-class rows inside truth: {offenders[:5]}"


def test_non_person_shape():
    """The reason this corpus exists: every other panel dataset is a person
    with a name plus an address or an email."""
    cols = set(_records().columns)
    assert {"org_name", "industry", "website"} <= cols
    assert not ({"first_name", "last_name", "surname", "dob"} & cols)


@pytest.mark.parametrize("cls", ["legal_form", "acronym", "word_order", "abbrev"])
def test_positive_classes_present(cls):
    counts = dict(_records().group_by("hardness").len().iter_rows())
    assert counts.get(cls, 0) >= 20, f"{cls} too rare: {counts}"
