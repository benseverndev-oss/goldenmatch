"""A `chunked` backend must not drop FS off the bucket scorer.

`_fs_use_bucket_route` gated on `backend not in (None, "polars-direct",
"duckdb")`, so a committed `backend="chunked"` silently fell to the legacy
batched FS scorer. That is a scoring-semantics change made by a MEMORY
decision, and it costs recall.

Measured, person @ 100,000 rows, zero-config vs the probabilistic lane on the
identical fixture and the identical 7-pass blocking plan:

    lane            backend    blocks   retained pairs      P        R       F1
    zero-config    'chunked'    3,218            9,250  1.0000   0.3827   0.5536
    probabilistic   None       83,367          120,269  0.9992   0.9949   0.9970

A per-pass blocking decision trace showed blocking was HEALTHY and identical in
both lanes -- same passes, same block counts per pass (14,081 / 15,025 / 2,233 /
7,695 / 21,348 / 22,638), ~50M candidate pairs generated either way. The entire
loss was downstream, in which scorer ran.

How zero-config ended up on `chunked` at all: once the controller MEASURES its
blocking profile instead of extrapolating a ~6K sample, the planner sees the
true ~121M candidate pairs for the first time, and `rule_chunked` fires at its
>= 50M threshold. So repairing the measurement is what exposed this -- the
routing bug was latent for as long as the planner was fed numbers too small to
trigger it.

`chunked` belongs with `duckdb` in the allow-list for the reason the code
already gives for duckdb: it is a single-node MEMORY strategy with no distinct
FS route, and the bucket scorer is *more* memory-bounded than the batched path
it was falling back to.

The parity matrix that should have caught this
(`test_bucket_legacy_parity_matrix`) covers `weighted` matchkeys three times and
`probabilistic` zero times, so bucket-vs-legacy divergence on the FS path was
never under test.
"""
from __future__ import annotations

import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.pipeline import _fs_use_bucket_route


def _config(backend):
    return GoldenMatchConfig(
        backend=backend,
        matchkeys=[MatchkeyConfig(
            name="probabilistic_auto", type="probabilistic",
            fields=[MatchkeyField(field="first_name", scorer="jaro_winkler"),
                    MatchkeyField(field="surname", scorer="jaro_winkler")],
        )],
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
            passes=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"]),
                    BlockingKeyConfig(fields=["surname"], transforms=["soundex"])],
        ),
    )


def _mk(cfg):
    return cfg.get_matchkeys()[0]


@pytest.mark.parametrize("backend", [None, "polars-direct", "duckdb", "chunked"])
def test_single_node_backends_keep_the_bucket_scorer(backend):
    """`chunked` is the regression; the other three pin that it was added to an
    allow-list rather than replacing it."""
    cfg = _config(backend)
    assert _fs_use_bucket_route(cfg, _mk(cfg)) is True


@pytest.mark.parametrize("backend", ["ray", "datafusion"])
def test_genuinely_distributed_backends_keep_their_own_routing(backend):
    """The exclusion still has to mean something: ray/datafusion have their own
    FS routes and must NOT be pulled onto the single-node bucket scorer."""
    cfg = _config(backend)
    assert _fs_use_bucket_route(cfg, _mk(cfg)) is False


def test_explicit_bucket_is_honored_at_any_size():
    cfg = _config("bucket")
    assert _fs_use_bucket_route(cfg, _mk(cfg)) is True


def test_kill_switch_still_forces_the_legacy_path(monkeypatch):
    """The escape hatch must survive the widened allow-list."""
    monkeypatch.setenv("GOLDENMATCH_FS_DEFAULT_BUCKET", "0")
    cfg = _config("chunked")
    assert _fs_use_bucket_route(cfg, _mk(cfg)) is False
