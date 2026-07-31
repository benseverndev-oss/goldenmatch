"""Cross-pass candidate-pair dedup (GOLDENMATCH_FS_PAIR_DEDUP) — parity + gating.

The lever scores each DISTINCT candidate pair once instead of once per
co-blocking pass. Because an FS pair's score depends only on the two rows'
comparison vector (not which pass co-blocked them), deduping + scoring once is
byte-identical to the bucket route's re-score-and-collapse. These tests pin that
parity + the eligibility/memory-cap gating.
"""
from __future__ import annotations

import goldenmatch
import pyarrow as pa
import pytest
from goldenmatch.backends.fs_pair_dedup import fs_pair_dedup_eligible
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

pytestmark = pytest.mark.filterwarnings("ignore")


def _person_table(n=400):
    """Repeated first/last names across many birth places -> multi-pass blocking
    co-blocks the same pairs in several passes (the cross-pass redundancy the
    lever targets)."""
    import random

    rng = random.Random(17)
    firsts = ["john", "jane", "alice", "bob", "carol", "dave", "erin", "frank"]
    lasts = ["smith", "jones", "brown", "clark", "davis", "evans", "green"]
    places = ["london", "paris", "berlin", "rome", "madrid", "vienna", "oslo"]
    cols: dict[str, list] = {
        "record_id": [], "first_name": [], "surname": [], "birth_place": [], "dob": [],
    }
    for i in range(n):
        f, s = rng.choice(firsts), rng.choice(lasts)
        cols["record_id"].append(f"r{i:05d}")
        cols["first_name"].append(f)
        cols["surname"].append(s)
        cols["birth_place"].append(rng.choice(places))
        y = rng.randint(1950, 1999)
        cols["dob"].append(f"{y}-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}")
    return pa.table(cols)


def _cluster_sets(res):
    return {
        frozenset(c["members"])
        for c in res.clusters.values()
        if len(c["members"]) > 1
    }


def _run(monkeypatch, flag, cfg, tbl, *, max_pairs=None):
    monkeypatch.setenv("GOLDENMATCH_FS_PAIR_DEDUP", flag)
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    if max_pairs is not None:
        monkeypatch.setenv("GOLDENMATCH_FS_PAIR_DEDUP_MAX_PAIRS", str(max_pairs))
    return goldenmatch.dedupe_df(tbl, config=cfg)


def test_pair_dedup_byte_identical_clusters(monkeypatch):
    tbl = _person_table()
    cfg = auto_configure_probabilistic_df(tbl)
    off = _run(monkeypatch, "0", cfg, tbl)
    on = _run(monkeypatch, "1", cfg, tbl)
    assert _cluster_sets(on) == _cluster_sets(off), (
        "pair-dedup must produce byte-identical clusters to the bucket route"
    )
    assert len(on.clusters) == len(off.clusters)


def test_memory_cap_falls_back_to_bucket_route(monkeypatch):
    # A tiny cap forces score_fs_pair_dedup to return None -> the dispatch falls
    # through to the bucket route, so the result is still identical to flag-off.
    tbl = _person_table()
    cfg = auto_configure_probabilistic_df(tbl)
    off = _run(monkeypatch, "0", cfg, tbl)
    capped = _run(monkeypatch, "1", cfg, tbl, max_pairs=1)
    assert _cluster_sets(capped) == _cluster_sets(off)


def test_eligibility_declines_embedding_and_ne():
    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[BlockingKeyConfig(fields=["first_name"], transforms=["strip"])],
    )
    ok = MatchkeyConfig(
        name="fs", type="probabilistic",
        fields=[MatchkeyField(field="first_name", scorer="jaro_winkler")],
    )
    assert fs_pair_dedup_eligible(ok, blocking) is True
    # TF-adjustment field declines (not covered by the core prototype path)
    tf = MatchkeyConfig(
        name="fs", type="probabilistic",
        fields=[MatchkeyField(field="first_name", scorer="jaro_winkler", tf_adjustment=True)],
    )
    assert fs_pair_dedup_eligible(tf, blocking) is False
    # non field-hash blocking strategy declines
    learned = blocking.model_copy(update={"strategy": "learned"})
    assert fs_pair_dedup_eligible(ok, learned) is False
