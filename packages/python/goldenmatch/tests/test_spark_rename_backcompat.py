"""P2: `goldenmatch.sail` keeps working after the rename to `goldenmatch.spark`.

The tier moved because it was never Sail-specific -- `builder.remote(url)` with
zero Sail-specific calls -- and the name hid the product from Spark users. But a
rename that silently breaks `import goldenmatch.sail` is a breaking change
dressed as tidying, so the alias is pinned here.

No Spark, no pysail, no native kernel needed.
"""
from __future__ import annotations

import warnings

import pytest


def test_new_module_is_importable():
    import goldenmatch.spark  # noqa: F401


def test_old_path_still_imports_and_warns():
    import importlib

    import goldenmatch.sail as legacy

    importlib.reload(legacy)  # reset the once-per-process warning
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _ = legacy.session
    assert any(issubclass(w.category, DeprecationWarning) for w in caught), (
        f"expected a DeprecationWarning; got {[w.category for w in caught]}"
    )


@pytest.mark.parametrize("name", ["session", "scorers", "clustering", "pipeline", "deps"])
def test_submodules_resolve_through_the_alias(name):
    """`from goldenmatch.sail import X` must reach the same object as
    `goldenmatch.spark.X` -- not merely 'not raise'."""
    import importlib

    legacy = getattr(importlib.import_module("goldenmatch.sail"), name)
    current = importlib.import_module(f"goldenmatch.spark.{name}")
    assert legacy is current, f"{name}: alias resolved to a different object"


def test_the_alias_is_not_a_copy():
    """Guard against someone 'fixing' the shim by duplicating the package: the
    two paths must be the SAME module object, or they drift silently."""
    import importlib

    assert (
        importlib.import_module("goldenmatch.sail").__getattr__("session")
        is importlib.import_module("goldenmatch.spark.session")
    )
