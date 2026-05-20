"""Distributed identity resolution dispatch tests.

The Phase 6 entry point ``resolve_identities_distributed`` is polymorphic
on clusters: dict (today) or ray.data.Dataset (Phase 6 target). Both
paths must produce the same identity assignments against the same
Postgres backend.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest

psycopg = pytest.importorskip("psycopg")
testing_pg = pytest.importorskip("testing.postgresql")
pl = pytest.importorskip("polars")


@pytest.fixture
def pg_url() -> Iterator[str]:
    pg = testing_pg.Postgresql()
    try:
        yield pg.url()
    finally:
        try:
            pg.stop()
        except Exception:
            pass


def _toy_df_clusters() -> tuple:
    df = pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3],
            "__source__": ["s"] * 4,
            "name": ["Alice", "Alice A.", "Bob", "Bobby"],
            "email": [
                "a@x.com", "a@x.com", "b@y.com", "b@y.com",
            ],
        }
    )
    clusters = {
        0: {"members": [0, 1], "size": 2, "confidence": 0.95, "pair_scores": {(0, 1): 0.95}},
        1: {"members": [2, 3], "size": 2, "confidence": 0.92, "pair_scores": {(2, 3): 0.92}},
    }
    scored_pairs = [(0, 1, 0.95), (2, 3, 0.92)]
    return df, clusters, scored_pairs


def test_distributed_identity_dict_path_resolves(pg_url: str) -> None:
    from goldenmatch.distributed.identity import (
        resolve_identities_distributed,
    )
    from goldenmatch.identity.pool import reset_identity_pool
    from goldenmatch.identity.store import IdentityStore

    reset_identity_pool()
    # Pre-create schema (resolver expects tables to exist)
    bootstrap = IdentityStore(backend="postgres", connection=pg_url)
    bootstrap.close()

    df, clusters, scored_pairs = _toy_df_clusters()
    summary = resolve_identities_distributed(
        clusters,
        df,
        scored_pairs,
        matchkey_name="weighted",
        dsn=pg_url,
        run_name=f"run-{datetime.now().isoformat()}",
    )
    assert summary.created == 2
    assert summary.records_upserted == 4
    reset_identity_pool()


def test_distributed_identity_replay_is_idempotent(pg_url: str) -> None:
    from goldenmatch.distributed.identity import (
        resolve_identities_distributed,
    )
    from goldenmatch.identity.pool import reset_identity_pool
    from goldenmatch.identity.store import IdentityStore

    reset_identity_pool()
    bootstrap = IdentityStore(backend="postgres", connection=pg_url)
    bootstrap.close()

    df, clusters, scored_pairs = _toy_df_clusters()
    run = f"run-{datetime.now().isoformat()}"
    s1 = resolve_identities_distributed(
        clusters, df, scored_pairs, matchkey_name="w",
        dsn=pg_url, run_name=run,
    )
    s2 = resolve_identities_distributed(
        clusters, df, scored_pairs, matchkey_name="w",
        dsn=pg_url, run_name=run,
    )
    # Second run is an absorb (records already point at the entity)
    # so no new identities are created.
    assert s1.created == 2
    assert s2.created == 0
    reset_identity_pool()
