"""Guards for the blind-labelling corpus.

The corpus itself cannot be tested -- it has no labels until a human supplies
them. What CAN be tested is the property that makes it worth having: that the
worksheet is produced without the detector's involvement.

That is not a style point. A worksheet carrying predicted groupings would
anchor the labeller, and this corpus would quietly become a third measure of
the detector agreeing with itself -- which is the exact weakness it exists to
escape, since the other two corpora were both built in the same workstream.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "layers_blind_label.py"
LABELS = Path(__file__).resolve().parent / "fixtures" / "layers_blind_labels.json"

sys.path.insert(0, str(ROOT / "scripts"))


def _spec() -> dict:
    return json.loads(LABELS.read_text(encoding="utf-8"))


def test_worksheet_generation_never_imports_the_detector():
    """The contamination guard, checked statically on the generating path."""
    src = SCRIPT.read_text(encoding="utf-8")
    head, _, _ = src.partition("def score(")
    assert "import infermap" not in head
    assert "detect_identity_layers" not in head


def test_worksheet_carries_no_predictions():
    """No entry may ship a pre-filled grouping."""
    for entry in _spec()["schemas"]:
        assert entry["parties"] == {} or entry.get("labelled_by_human")


def test_every_schema_is_real_and_traceable():
    """Each row must name the file it came from, so a label can be audited."""
    for entry in _spec()["schemas"]:
        assert entry["source"]
        assert len(entry["columns"]) >= 4
        assert entry["id"]


def test_schemas_are_diverse():
    """A worksheet of near-identical schemas measures one schema many times.

    The generator deduplicates by token-set Jaccard and caps per source family;
    this pins the outcome, because the first two attempts at that filter both
    produced a sheet dominated by one benchmark generator.
    """
    spec = _spec()
    sigs = [
        frozenset(
            tok
            for col in e["columns"]
            for tok in col.lower().replace("-", "_").replace(" ", "_").split("_")
            if tok
        )
        for e in spec["schemas"]
    ]
    for i, a in enumerate(sigs):
        for b in sigs[i + 1:]:
            overlap = len(a & b) / len(a | b) if (a | b) else 0.0
            assert overlap < 0.5, "near-duplicate schemas in the worksheet"


def test_scoring_is_skipped_until_labels_exist():
    """Unlabelled is a normal state, not a failure."""
    from layers_blind_label import score

    pytest.importorskip("infermap")
    summary = score()["summary"]
    assert summary["total"] >= 1
    if summary["labelled"] == 0:
        pytest.skip(f"{summary['total']} schemas awaiting human labels")
    assert 0.0 <= summary["exact_partition"] <= 1.0
    assert 0.0 <= summary["pairwise_f1"] <= 1.0
