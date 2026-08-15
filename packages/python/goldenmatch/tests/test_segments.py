"""Segment labels (#2574 Wave 4) — GoldenMatch's read-only consumer of layers.

Two properties carry the weight here: the adapter is structural (no
goldencheck_types import, so an optional dependency stays optional), and
every path fails open to ``[]`` rather than raising into a matching run.
"""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl
import pytest
from goldenmatch.core.segments import (
    UNKNOWN_SEGMENT,
    Segment,
    column_segments,
    detect_segments,
    is_heterogeneous,
    segments_from_layers,
    segments_from_schema,
)


@dataclass(frozen=True)
class _Layer:
    """Structural stand-in for goldencheck_types.IdentityLayer."""

    role: str
    kind: str
    columns: list[str]
    score: float = 0.9
    reason: str = "affix"


@dataclass(frozen=True)
class _Schema:
    layers: list


def _loan_tape() -> pl.DataFrame:
    return pl.DataFrame({
        "lender_name": ["Acme Bank"],
        "lender_id": ["L1"],
        "borrower_name": ["Jane Roe"],
        "borrower_ssn": ["123456789"],
    })


# ── adaptation ────────────────────────────────────────────────────────────

def test_layers_adapt_to_segments():
    segs = segments_from_layers([
        _Layer("lender", "organization", ["lender_name", "lender_id"]),
        _Layer("borrower", "person", ["borrower_name"], score=0.7, reason="role_hint"),
    ])
    assert [s.label for s in segs] == ["lender", "borrower"]
    assert segs[0].kind == "organization"
    assert segs[0].columns == ["lender_name", "lender_id"]
    assert segs[1].score == 0.7
    assert segs[1].reason == "role_hint"


def test_unknown_role_is_flagged_not_dropped():
    segs = segments_from_layers([_Layer(UNKNOWN_SEGMENT, "unknown", ["zz_id"])])
    assert len(segs) == 1
    assert segs[0].is_unknown


def test_non_layer_objects_are_skipped_not_raised():
    segs = segments_from_layers([object(), _Layer("lender", "organization", ["a"])])
    assert [s.label for s in segs] == ["lender"]


def test_empty_and_none_layers():
    assert segments_from_layers([]) == []
    assert segments_from_layers(None) == []


# ── schema path (the free one) ────────────────────────────────────────────

def test_segments_read_off_a_schema():
    schema = _Schema(layers=[_Layer("lender", "organization", ["lender_id"])])
    assert [s.label for s in segments_from_schema(schema)] == ["lender"]


def test_schema_without_layers_yields_none():
    """A schema predating layers must not raise."""
    assert segments_from_schema(object()) == []
    assert segments_from_schema(None) == []


# ── detection path ────────────────────────────────────────────────────────

def test_detect_segments_on_a_multiparty_frame():
    pytest.importorskip("infermap")
    segs = detect_segments(_loan_tape(), domain="finance")
    labels = {s.label for s in segs}
    assert len(segs) >= 2
    assert {"lender", "borrower"} & labels


def test_detect_segments_matches_the_schema_path():
    """Both entry points must produce the same shape for the same frame."""
    infermap = pytest.importorskip("infermap")
    df = _loan_tape()
    direct = detect_segments(df, domain="finance")
    via_schema = segments_from_schema(
        _Schema(layers=list(infermap.detect_identity_layers(df, domain="finance").layers))
    )
    assert direct == via_schema


def test_detect_segments_fails_open_on_bad_input():
    assert detect_segments(None) == []
    assert detect_segments("not a frame") == []


def test_detect_segments_fails_open_when_infermap_is_absent(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _no_infermap(name, *args, **kwargs):
        if name == "infermap":
            raise ImportError("infermap is optional here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_infermap)
    assert detect_segments(_loan_tape()) == []


# ── consumer helpers (#2575's inputs) ─────────────────────────────────────

def test_column_segments_maps_columns_to_labels():
    segs = [
        Segment("lender", "organization", ["lender_name", "lender_id"]),
        Segment("borrower", "person", ["borrower_name"]),
    ]
    assert column_segments(segs) == {
        "lender_name": "lender",
        "lender_id": "lender",
        "borrower_name": "borrower",
    }


def test_contested_column_goes_to_the_first_segment():
    segs = [
        Segment("lender", "organization", ["shared_id"]),
        Segment("borrower", "person", ["shared_id"]),
    ]
    assert column_segments(segs)["shared_id"] == "lender"


def test_unlabelled_columns_are_absent_not_guessed():
    segs = [Segment("lender", "organization", ["lender_id"])]
    assert "amount" not in column_segments(segs)


def test_heterogeneity_needs_more_than_one_segment():
    one = [Segment("customer", "person", ["name"])]
    two = one + [Segment("lender", "organization", ["lender_id"])]
    assert is_heterogeneous(two)
    assert not is_heterogeneous(one)
    # No detection is not a claim of uniformity — but it is not a licence to
    # partition either.
    assert not is_heterogeneous([])


def test_module_imports_without_goldencheck_types():
    """The structural adapter is what keeps that dependency optional."""
    import pathlib
    import sys

    mod = sys.modules[detect_segments.__module__]
    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    assert "import goldencheck_types" not in src
    assert "from goldencheck_types" not in src
