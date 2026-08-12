"""P6 lane test: zero-config end to end on a real Spark DataFrame.

Runs in the Spark lanes; skips where no Spark Connect client is installed.

The unit tests fake the DataFrame to pin the scale-safety arithmetic. This one
uses a real one, because the parts a fake cannot check are exactly the parts most
likely to be wrong: that `sample` and `count` behave as assumed against Spark
Connect, that the collected rows convert to something auto-config accepts, and
that the config it produces is then executable by the very pipeline that asked
for it.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")

_ID = "__row_id__"
_COLS = [_ID, "first", "last", "city"]

# Enough duplicate structure that auto-config has something to find: three
# near-duplicate name pairs spread across three city blocks.
_ROWS = [
    (0, "jonathan", "smith", "york"),
    (1, "jon", "smith", "york"),
    (2, "amelia", "wong", "leeds"),
    (3, "amelia", "wongg", "leeds"),
    (4, "robert", "clark", "hull"),
    (5, "bob", "clarke", "hull"),
    (6, "priya", "nair", "york"),
    (7, "priya", "nayar", "york"),
]


@pytest.fixture()
def source(spark):
    return spark.createDataFrame(_ROWS, _COLS)


def test_sample_to_driver_round_trips_a_real_dataframe(source):
    """`count` and `sample` against Spark Connect, and the collected rows into
    an Arrow table auto-config can read."""
    from goldenmatch.spark.autoconfig import sample_to_driver

    table, n_full = sample_to_driver(source, n_target=1000)

    assert n_full == len(_ROWS)
    assert table.num_rows == len(_ROWS), "small input should be taken whole"
    assert set(table.column_names) == set(_COLS)


def test_sampling_a_larger_input_stays_within_the_driver_budget(source):
    """With n_target below the row count the tier must sample, and what comes
    back must still be a usable Arrow table rather than an empty one."""
    from goldenmatch.spark.autoconfig import sample_to_driver

    table, n_full = sample_to_driver(source, n_target=4, seed=1)

    assert n_full == len(_ROWS)
    assert 0 < table.num_rows <= len(_ROWS)
    assert set(table.column_names) == set(_COLS)


def test_zero_config_produces_a_config_the_tier_can_execute(source):
    """The end-to-end claim. Auto-config runs on the sample, and the config it
    commits is then executed by `run_config_pipeline` -- which is the part a
    unit test cannot establish, since a config that profiles cleanly can still
    name a feature the Spark tier refuses."""
    from goldenmatch.spark.autoconfig import auto_configure_spark
    from goldenmatch.spark.config_pipeline import (
        _validate_spark_config_supported,
        run_config_pipeline,
    )

    config, provenance = auto_configure_spark(source, n_sample=1000)

    assert config.get_matchkeys(), "no matchkeys were chosen"
    assert config.blocking and config.blocking.keys, "no blocking was chosen"
    assert provenance["n_full"] == len(_ROWS)
    assert provenance["source"] == "spark-sample"

    # The gate the produced config has to pass to be worth anything here.
    _validate_spark_config_supported(config)

    golden = run_config_pipeline(
        source, config, id_col=_ID, golden_cols=["first", "last", "city"]
    )
    assert set(golden.columns) == {"cluster_id", "first", "last", "city"}
    golden.collect()  # must execute, not merely plan


def test_config_none_runs_zero_config_through_the_entry_point(source):
    """`run_config_pipeline(df)` with no config is the zero-config surface."""
    from goldenmatch.spark.config_pipeline import run_config_pipeline

    golden = run_config_pipeline(
        source, None, id_col=_ID, golden_cols=["first", "last", "city"]
    )
    assert set(golden.columns) == {"cluster_id", "first", "last", "city"}
    golden.collect()


def test_a_large_dataset_is_refused_before_any_profiling(source, monkeypatch):
    """The scale refusal against a REAL DataFrame.

    `count` is patched rather than materializing 100k rows -- the row count is
    the only input the refusal reads, and building the data would test Spark's
    ability to hold rows rather than the tier's willingness to refuse.
    """
    from goldenmatch.spark import autoconfig as ac

    n_full = ac._refuse_at_n() + 1
    monkeypatch.setattr(type(source), "count", lambda self: n_full, raising=False)

    with pytest.raises(ac.SparkAutoConfigTooLarge, match=f"{n_full:,}"):
        ac.auto_configure_spark(source, n_sample=100)
