"""TF adjustment must survive an Arrow frame reaching it.

`GOLDENMATCH_FS_TF_ADJUSTMENT=1` set `tf_adjustment=True` on eligible fields
and then changed NOTHING: a full-panel A/B (fs-lever-gate run 34302210694)
came back +0.0000 on all five datasets, precision and recall identical to
three decimals.

The cause was a polars-era membership test inverted by the Arrow migration.
Both `_build_tf_tables` and `value_frequencies` guard with
`if field not in df.columns`. On a `pl.DataFrame` that tests column NAMES; on
a `pyarrow.Table`, `.columns` is a list of `ChunkedArray`, so the test is
ALWAYS true, every TF field was skipped as "missing", the tables came back
`None`, and `_apply_tf_adjustment` early-returned on every pair. Nothing
raised and nothing warned -- the feature silently did not exist on the default
path, which by then was Arrow.

These pin the engine-agnostic behaviour directly, because that is the axis the
bug lived on: the same input as a polars frame and as an Arrow table must
produce the same table.
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import _build_tf_tables
from goldenmatch.core.tf_tables import value_frequencies

_ROWS = [{"last_name": n, "zip5": z} for n, z in (
    [("Smith", "10001")] * 6 + [("Jones", "10002")] * 3 + [("Rare", "10003")]
)]


def _pl() -> pl.DataFrame:
    return pl.DataFrame(_ROWS)


def _arrow():
    return _pl().to_arrow()


@pytest.mark.parametrize("frame_kind", ["polars", "arrow"])
def test_value_frequencies_reads_either_engine(frame_kind):
    df = _pl() if frame_kind == "polars" else _arrow()
    freqs = value_frequencies(df, "last_name", None)
    assert freqs, "no frequencies -- the column lookup failed, not the data"
    assert freqs["Smith"] == pytest.approx(0.6)
    assert freqs["Jones"] == pytest.approx(0.3)
    assert freqs["Rare"] == pytest.approx(0.1)


def test_value_frequencies_is_identical_across_engines():
    assert value_frequencies(_pl(), "last_name", None) == value_frequencies(
        _arrow(), "last_name", None
    )


def test_value_frequencies_still_empty_for_a_genuinely_absent_column():
    """The guard must keep MEANING something -- an absent column is empty on
    both engines, which is what made the inverted test invisible."""
    assert value_frequencies(_pl(), "nope", None) == {}
    assert value_frequencies(_arrow(), "nope", None) == {}


def _mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="mk", type="probabilistic",
        fields=[
            MatchkeyField(field="last_name", scorer="exact", tf_adjustment=True),
            MatchkeyField(field="zip5", scorer="exact", tf_adjustment=True),
        ],
    )


@pytest.mark.parametrize("frame_kind", ["polars", "arrow"])
def test_build_tf_tables_reads_either_engine(frame_kind):
    df = _pl() if frame_kind == "polars" else _arrow()
    freqs, collision = _build_tf_tables(df, _mk())
    assert freqs is not None, "tables came back None -- every field read as missing"
    assert set(freqs) == {"last_name", "zip5"}
    # collision = sum of squared frequencies, the baseline an agreement is
    # adjusted against; 0.6^2 + 0.3^2 + 0.1^2
    assert collision["last_name"] == pytest.approx(0.46)


def test_build_tf_tables_is_identical_across_engines():
    p_freqs, p_coll = _build_tf_tables(_pl(), _mk())
    a_freqs, a_coll = _build_tf_tables(_arrow(), _mk())
    assert p_freqs == a_freqs
    assert p_coll == a_coll


def test_build_tf_tables_still_none_when_no_field_opts_in():
    mk = MatchkeyConfig(
        name="mk", type="probabilistic",
        fields=[MatchkeyField(field="last_name", scorer="exact")],
    )
    assert _build_tf_tables(_arrow(), mk) == (None, None)
