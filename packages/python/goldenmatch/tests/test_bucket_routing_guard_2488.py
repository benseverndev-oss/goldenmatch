"""The bucket scorer's blocking-expressibility gate is a CORRECTNESS gate and
must hold however ``backend`` became ``"bucket"`` (#2488).

The bug: ``_use_bucket_scorer`` returned True on ``backend == "bucket"`` before
reaching the strategy allowlist that exists to keep non-field-keyed strategies
off the bucket path. ``ExecutionPlan.apply_to`` writes ``backend="bucket"`` onto
the committed config, so on a zero-config run the FINAL dedupe pass took bucket
with a ``token`` plan, derived an empty block key, and emitted zero pairs -- with
no error -- while the controller's own sample pass (routed before the planner
ran) had scored the same plan correctly on the legacy path.

Measured on Amazon-Google before the fix: identical configs differing only in
``backend`` gave 16,545 pairs (unset) vs 0 pairs (``"bucket"``).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    LSHKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
    TokenBlockingConfig,
)
from goldenmatch.core.pipeline import _bucket_can_express_blocking, _use_bucket_scorer


@pytest.fixture
def df() -> pl.DataFrame:
    return pl.DataFrame({
        "record_id": [f"r{i}" for i in range(6)],
        "title": ["alpha widget", "alpha widget", "beta gadget",
                  "beta gadget", "gamma thing", "gamma thing"],
    })


def _cfg(blocking: BlockingConfig | None, backend: str | None = None) -> GoldenMatchConfig:
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="mk", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="title", scorer="token_sort", weight=1.0)],
        )],
        blocking=blocking,
    )
    if backend is not None:
        cfg.backend = backend
    return cfg


# ---- _bucket_can_express_blocking: the predicate itself ----


def test_field_keyed_plans_are_expressible():
    cfg = BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["title"])])
    assert _bucket_can_express_blocking(cfg)


def test_missing_blocking_is_expressible():
    """No blocking plan -> nothing for bucket to misread."""
    assert _bucket_can_express_blocking(None)


@pytest.mark.parametrize("blocking", [
    BlockingConfig(strategy="token", token=TokenBlockingConfig(column="title")),
    BlockingConfig(strategy="lsh", lsh=LSHKeyConfig(column="title", threshold=0.5)),
    BlockingConfig(strategy="learned"),
])
def test_signature_generated_plans_are_not_expressible(blocking: BlockingConfig) -> None:
    """These carry no field keys -- the schema REJECTS `keys` alongside them --
    so bucket would derive an empty key and score an empty candidate set."""
    assert blocking.keys == []
    assert not _bucket_can_express_blocking(blocking)


def test_degenerate_keyless_static_is_not_expressible():
    """`_degenerate_blocking_config()`'s shape. With auto_suggest the real plan
    is chosen mid-pipeline, so routing off this provisional plan can land on a
    strategy bucket cannot express."""
    cfg = BlockingConfig(strategy="static", keys=[], auto_suggest=True)
    assert not _bucket_can_express_blocking(cfg)


# ---- the regression: backend="bucket" must not bypass the gate ----


@pytest.mark.parametrize("backend", [None, "bucket"])
def test_token_plan_never_routes_to_bucket(df: pl.DataFrame, backend: str | None) -> None:
    """THE REGRESSION. Before the fix this returned True for backend='bucket',
    sending a token plan to a scorer that cannot express it."""
    cfg = _cfg(BlockingConfig(strategy="token",
                              token=TokenBlockingConfig(column="title")), backend)
    assert _use_bucket_scorer(cfg, df) is False


@pytest.mark.parametrize("backend", [None, "bucket"])
def test_lsh_plan_never_routes_to_bucket(df: pl.DataFrame, backend: str | None) -> None:
    cfg = _cfg(BlockingConfig(strategy="lsh",
                              lsh=LSHKeyConfig(column="title", threshold=0.5)), backend)
    assert _use_bucket_scorer(cfg, df) is False


def test_explicit_bucket_still_honored_for_expressible_plans(df: pl.DataFrame) -> None:
    """The gate must not break the feature it guards: an explicit
    backend='bucket' on a field-keyed plan is still honored at any size."""
    cfg = _cfg(BlockingConfig(strategy="static",
                              keys=[BlockingKeyConfig(fields=["title"])]), "bucket")
    assert _use_bucket_scorer(cfg, df) is True


def test_planner_written_backend_cannot_bypass_the_gate(df: pl.DataFrame) -> None:
    """ExecutionPlan.apply_to is how `backend` became "bucket" in the wild --
    the gate must not care which writer set it."""
    from goldenmatch.core.execution_plan import ExecutionPlan

    cfg = _cfg(BlockingConfig(strategy="token",
                              token=TokenBlockingConfig(column="title")))
    ExecutionPlan(backend="bucket").apply_to(cfg)
    assert cfg.backend == "bucket"          # the planner really did write it
    assert _use_bucket_scorer(cfg, df) is False
