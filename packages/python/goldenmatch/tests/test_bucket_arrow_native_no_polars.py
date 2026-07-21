"""Regression: the weighted bucket scorer's ``find_fuzzy_matches`` fallback
(reached for scorers with no native/vectorized kernel -- ensemble / qgram /
soundex_match / refdata) must score arrow-native, without importing ``polars``.

The no-polars (arrow-only) suite lanes -- and the goldengraph pipeline, which
installs goldenmatch WITHOUT the optional polars dep -- crashed with
``ModuleNotFoundError: No module named 'polars'`` deep in the block scorer,
because the call site unconditionally converted the arrow block to a polars
frame (``pl.from_arrow``) before the fallback. ``find_fuzzy_matches`` already
reads the block through the ``to_frame`` seam + a ``to_dicts``/``to_pylist``
dual-rep, so it is arrow-native for every field type except ``record_embedding``
-- only that one still needs a polars block. This test blocks the ``polars``
import for the duration and proves an ``ensemble`` weighted matchkey still finds
the planted duplicate.
"""
from __future__ import annotations

import builtins
import contextlib
import sys

import pyarrow as pa
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField


@contextlib.contextmanager
def _polars_unimportable():
    """Make ``import polars`` raise ModuleNotFoundError for the duration, so a
    code path that reaches for polars is caught here instead of only on the
    no-polars CI lane. Restores sys.modules + the import hook on exit."""
    saved = {k: v for k, v in sys.modules.items() if k == "polars" or k.startswith("polars.")}
    for k in saved:
        del sys.modules[k]
    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "polars" or name.startswith("polars."):
            raise ModuleNotFoundError("No module named 'polars'")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = _blocked
    try:
        yield
    finally:
        builtins.__import__ = real_import
        sys.modules.update(saved)


def test_find_fuzzy_matches_ensemble_is_arrow_native_without_polars():
    from goldenmatch.core.scorer import find_fuzzy_matches

    block = pa.table(
        {
            "__row_id__": [0, 1, 2],
            "name": ["Acme Inc", "Acme Inc", "Beta"],
        }
    )
    mk = MatchkeyConfig(
        name="fuzzy_name",
        comparison="weighted",
        threshold=0.7,
        fields=[
            MatchkeyField(
                field="name", scorer="ensemble", weight=1.0,
                transforms=["lowercase", "strip"],
            ),
        ],
    )

    with _polars_unimportable():
        # Sanity: the guard actually blocks polars in this context.
        with pytest.raises(ModuleNotFoundError):
            __import__("polars")
        pairs = find_fuzzy_matches(block, mk, exclude_pairs=frozenset())

    got = {(min(a, b), max(a, b)) for a, b, _ in pairs}
    assert (0, 1) in got  # the two "Acme Inc" rows match
    assert (0, 2) not in got and (1, 2) not in got  # "Beta" stays apart
