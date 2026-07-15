"""Smoke + guard tests for the scale-envelope v2 head-to-head harness."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_person_shape_metadata():
    shapes = _load("shapes")
    s = shapes.SHAPES["person"]
    assert s.name == "person"
    assert s.columns == ["record_id", "first_name", "surname", "dob", "postcode", "city"]
    assert s.blocking_fields == ["postcode"]
    assert s.blocking_cardinality == 200_000  # C, for the projection guard


def test_shapes_import_does_not_drag_goldenmatch():
    # shapes.py must import cleanly without pulling goldenmatch into sys.modules
    # (run_splink + the generator import it and must stay GM-free at import time).
    for m in [k for k in list(sys.modules) if k == "goldenmatch" or k.startswith("goldenmatch.")]:
        del sys.modules[m]
    _load("shapes")
    assert not any(k == "goldenmatch" or k.startswith("goldenmatch.") for k in sys.modules)
