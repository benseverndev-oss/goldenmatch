"""The FS twin of #2488: `backend="bucket"` must not send a blocking strategy the bucket
scorer cannot express onto the bucket route.

The bucket scorer derives FIELD-based block keys from `blocking.keys` / `blocking.passes`.
Token / lsh / ann / learned / canopy / sorted_neighborhood candidates are not field-hash
reproducible, and `_fs_use_bucket_route` has an exclusion for exactly those strategies --
but it sat BELOW an unconditional `if backend == "bucket": return True`.
`ExecutionPlan.apply_to` writes `backend="bucket"` onto the committed zero-config config,
so an FS matchkey on a token plan scored through bucket, which blocked on whatever keys the
config happened to carry instead of the plan's tokens. #2488 moved the same check above the
same short-circuit in `_use_bucket_scorer` (the weighted route); the FS route kept the old
order.

Measured on MusicBrainz-20K zero-config (probabilistic matchkey, token plan on `title` with
a stray `language` key): F1 0.034 with the planner's `backend="bucket"`, 0.176 without it.

`_fs_external_blocks_route` is where such a plan should land instead: the memory-bounded
external-blocks scorer, not the legacy batched scorer (the #1826 OOM shape). It only
accepted `backend in (None, "polars-direct")`, so a planner-written "bucket" also has to
count there.
"""
from __future__ import annotations

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
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.core.pipeline import _fs_external_blocks_route, _fs_use_bucket_route


def _token_plan() -> BlockingConfig:
    return BlockingConfig(strategy="token", token=TokenBlockingConfig(column="title"))


def _lsh_plan() -> BlockingConfig:
    return BlockingConfig(strategy="lsh", lsh=LSHKeyConfig(column="title", threshold=0.5))


_UNEXPRESSIBLE = {"token": _token_plan, "lsh": _lsh_plan}


def _config(blocking: BlockingConfig, backend: str | None = None) -> GoldenMatchConfig:
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="probabilistic_auto", type="probabilistic",
            fields=[MatchkeyField(field="title", scorer="jaro_winkler"),
                    MatchkeyField(field="artist", scorer="jaro_winkler")],
        )],
        blocking=blocking,
    )
    if backend is not None:
        cfg.backend = backend
    return cfg


def _mk(cfg: GoldenMatchConfig):
    return cfg.get_matchkeys()[0]


@pytest.mark.parametrize("backend", [None, "bucket"])
@pytest.mark.parametrize("strategy", sorted(_UNEXPRESSIBLE))
def test_unexpressible_plan_never_takes_the_fs_bucket_route(strategy: str, backend: str | None) -> None:
    """THE REGRESSION. Before the fix this returned True for backend='bucket'."""
    cfg = _config(_UNEXPRESSIBLE[strategy](), backend)
    assert _fs_use_bucket_route(cfg, _mk(cfg)) is False


@pytest.mark.parametrize("backend", [None, "bucket"])
@pytest.mark.parametrize("strategy", sorted(_UNEXPRESSIBLE))
def test_unexpressible_plan_takes_the_external_blocks_route(strategy: str, backend: str | None) -> None:
    """Off the bucket route, the plan's own blocks go to the memory-bounded external-blocks
    scorer whichever of these two wrote the backend."""
    cfg = _config(_UNEXPRESSIBLE[strategy](), backend)
    assert _fs_external_blocks_route(cfg) is True


def test_planner_written_backend_cannot_bypass_the_fs_gate() -> None:
    """`ExecutionPlan.apply_to` is how `backend` became "bucket" in the wild."""
    cfg = _config(_token_plan())
    ExecutionPlan(backend="bucket").apply_to(cfg)
    assert cfg.backend == "bucket"
    assert _fs_use_bucket_route(cfg, _mk(cfg)) is False
    assert _fs_external_blocks_route(cfg) is True


def test_explicit_bucket_still_honored_for_a_field_keyed_plan() -> None:
    """The guard must not break what it protects: a keyed plan under an explicit
    backend='bucket' stays on bucket, and does not take the external-blocks route."""
    cfg = _config(
        BlockingConfig(strategy="multi_pass",
                       keys=[BlockingKeyConfig(fields=["title"], transforms=["lowercase"])],
                       passes=[BlockingKeyConfig(fields=["title"], transforms=["lowercase"])]),
        "bucket",
    )
    assert _fs_use_bucket_route(cfg, _mk(cfg)) is True
    assert _fs_external_blocks_route(cfg) is False


@pytest.mark.parametrize("backend", ["ray", "datafusion"])
def test_distributed_backends_keep_their_own_routing(backend: str) -> None:
    """Accepting a planner-written 'bucket' must not pull a distributed backend onto the
    single-node external-blocks scorer."""
    cfg = _config(_token_plan(), backend)
    assert _fs_use_bucket_route(cfg, _mk(cfg)) is False
    assert _fs_external_blocks_route(cfg) is False
