"""P4b: `backend` is a closed set, and `spark` is recognized (not silent).

`GoldenMatchConfig.backend` was free text. `_get_block_scorer` returns the
default in-memory scorer for anything it does not recognize -- correct for a
dispatcher, and the wrong place to notice a typo. So `backend="rayy"` produced
an ordinary single-box run with no warning at all: the user believed they were
distributing and were not.

No Spark, no Ray, no native kernel needed.
"""
from __future__ import annotations

import pytest
from goldenmatch.config.schemas import (
    VALID_BACKENDS,
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)


def _cfg(**kw) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="m",
                type="weighted",
                threshold=0.85,
                fields=[
                    MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)
                ],
            )
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["city"])]),
        **kw,
    )


@pytest.mark.parametrize("backend", sorted(VALID_BACKENDS))
def test_every_valid_backend_is_accepted(backend):
    """A closed set that rejected a real backend would break working configs."""
    assert _cfg(backend=backend).backend == backend


def test_none_is_still_the_default():
    assert _cfg().backend is None
    assert _cfg(backend=None).backend is None


@pytest.mark.parametrize("typo", ["rayy", "sparkk", "duck", "Ray", "totally-made-up"])
def test_a_typo_is_refused_instead_of_running_single_box(typo):
    """The regression. Each of these previously ran the default in-memory path
    with no signal, so a user who meant to distribute simply did not."""
    with pytest.raises(ValueError, match="Unknown backend"):
        _cfg(backend=typo)


def test_the_error_names_the_valid_set():
    """A rejection the user cannot act on is only half a fix."""
    with pytest.raises(ValueError) as err:
        _cfg(backend="rayy")
    msg = str(err.value)
    for name in ("ray", "duckdb", "spark"):
        assert name in msg, f"{name!r} missing from: {msg}"


# ── spark is recognized, and refuses the wrong seam loudly ───────────

def test_spark_is_a_valid_backend_value():
    assert "spark" in VALID_BACKENDS
    assert _cfg(backend="spark").backend == "spark"


def test_spark_does_not_silently_fall_through_to_the_local_scorer():
    """The whole point. `spark` must not resolve to `score_blocks_parallel`."""
    from goldenmatch.core.pipeline import _get_block_scorer

    with pytest.raises(NotImplementedError, match="run_config_pipeline"):
        _get_block_scorer(_cfg(backend="spark"))


def test_other_backends_still_resolve_to_a_scorer():
    """A guard that raised for everything would pass the test above while
    breaking every other backend."""
    from goldenmatch.core.pipeline import _get_block_scorer

    assert _get_block_scorer(_cfg()) is not None
    assert _get_block_scorer(_cfg(backend="chunked")) is not None


def test_spark_entry_point_is_exported():
    """The error message tells the user to import this; it must exist."""
    from goldenmatch.spark import run_config_pipeline

    assert callable(run_config_pipeline)
