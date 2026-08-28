"""The distributed scoring path must fail loudly rather than under-match quietly.

Every worker-side step in `distributed/scoring.py` used to catch Exception, log a
WARNING and carry on with less data. The run then reported SUCCESS with fewer
matches and nothing counted the loss -- and because the exception never reached
Ray, Ray Data's own task retry could not fire either: a task that swallows its
error and returns an empty table has *succeeded*.

These tests drive the real module-level functions with polars frames. No Ray.
"""
from __future__ import annotations

import logging

import polars as pl
import pytest
from goldenmatch.distributed import scoring as S


class _Boom(RuntimeError):
    """Distinctive so a test can prove the ORIGINAL error is preserved."""


# --------------------------------------------------------------- the policy


def test_default_policy_is_raise(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_DISTRIBUTED_ON_ERROR", raising=False)
    assert S._on_error() == "raise"


def test_skip_is_opt_in(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_ON_ERROR", "skip")
    assert S._on_error() == "skip"
    # Tolerant of shell noise, since this is set by hand under pressure.
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_ON_ERROR", "  SKIP ")
    assert S._on_error() == "skip"


def test_unrecognised_policy_raises_rather_than_defaulting(monkeypatch):
    """A typo in the escape hatch must not silently select a policy.

    Defaulting a typo to 'raise' would be safe but confusing; defaulting it to
    'skip' would be the exact silent-loss bug this change removes.
    """
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_ON_ERROR", "skipp")
    with pytest.raises(ValueError, match="not one of"):
        S._on_error()


def test_handler_raises_by_default_and_chains_the_cause(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_DISTRIBUTED_ON_ERROR", raising=False)
    original = _Boom("kernel exploded")
    with pytest.raises(RuntimeError) as ei:
        S._handle_worker_error("per-partition scoring", original)
    assert "per-partition scoring" in str(ei.value)
    assert "GOLDENMATCH_DISTRIBUTED_ON_ERROR=skip" in str(ei.value), (
        "the message must name the escape hatch, or an operator hitting this at "
        "3am has no way to get a partial answer"
    )
    assert ei.value.__cause__ is original, "the original traceback must survive"


def test_handler_under_skip_logs_at_error_not_warning(monkeypatch, caplog):
    """WARNING is what made this invisible; the escape hatch logs at ERROR."""
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_ON_ERROR", "skip")
    with caplog.at_level(logging.WARNING, logger=S.logger.name):
        S._handle_worker_error("per-partition scoring", _Boom("x"))
    recs = [r for r in caplog.records if "per-partition scoring" in r.message]
    assert recs, "the skip path must still say something"
    assert all(r.levelno >= logging.ERROR for r in recs)


# ------------------------------------------------- the real failure surfaces


def _frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3],
            "name": ["ann lee", "ann lee", "bob roy", "bob roy"],
            "email": ["a@x.com", "a@x.com", "b@x.com", "b@x.com"],
        }
    )


def _config_with_standardization():
    from goldenmatch.config.schemas import GoldenMatchConfig

    return GoldenMatchConfig.model_validate(
        {
            "fields": [{"name": "name"}, {"name": "email"}],
            "blocking": {"keys": [{"fields": ["name"]}]},
            "standardization": {"rules": {"name": ["name_lower"]}},
        }
    )


def test_standardization_failure_raises_instead_of_keying_on_raw_fields(monkeypatch):
    """The nastiest of the five: it did not drop rows, it changed the KEYS.

    Falling back to raw fields means records that should co-locate no longer
    hash together, so they are never compared. The output looks structurally
    fine and simply contains fewer matches.
    """
    monkeypatch.delenv("GOLDENMATCH_DISTRIBUTED_ON_ERROR", raising=False)

    import goldenmatch.core.standardize as std_mod

    def _explode(*a, **k):
        raise _Boom("standardization died")

    monkeypatch.setattr(std_mod, "apply_standardization", _explode)

    with pytest.raises(RuntimeError, match="standardization for keys"):
        S._attach_colocation_keys(_frame(), _config_with_standardization())


def test_standardization_failure_is_survivable_when_explicitly_opted_in(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_ON_ERROR", "skip")

    import goldenmatch.core.standardize as std_mod

    monkeypatch.setattr(
        std_mod, "apply_standardization",
        lambda *a, **k: (_ for _ in ()).throw(_Boom("standardization died")),
    )

    out = S._attach_colocation_keys(_frame(), _config_with_standardization())
    assert "__block_key__" in out.columns, (
        "skip must still produce a usable frame -- it trades recall for a result"
    )


def test_colocated_group_scoring_failure_raises(monkeypatch):
    """A dead partition used to contribute zero pairs and report success."""
    monkeypatch.delenv("GOLDENMATCH_DISTRIBUTED_ON_ERROR", raising=False)

    import goldenmatch.core.pipeline as pipeline_mod

    monkeypatch.setattr(
        pipeline_mod, "_score_partition_with_config",
        lambda *a, **k: (_ for _ in ()).throw(_Boom("scorer died")),
    )

    df = _frame().with_columns(
        pl.lit("pass:0").alias("__keyid__"),
        pl.lit("k").alias("__block_key__"),
    )
    with pytest.raises(RuntimeError, match="co-located group scoring"):
        S._score_colocated_groups(df, _config_with_standardization())


def test_colocated_group_scoring_returns_empty_under_skip(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_ON_ERROR", "skip")

    import goldenmatch.core.pipeline as pipeline_mod

    monkeypatch.setattr(
        pipeline_mod, "_score_partition_with_config",
        lambda *a, **k: (_ for _ in ()).throw(_Boom("scorer died")),
    )

    df = _frame().with_columns(
        pl.lit("pass:0").alias("__keyid__"),
        pl.lit("k").alias("__block_key__"),
    )
    assert S._score_colocated_groups(df, _config_with_standardization()) == []
