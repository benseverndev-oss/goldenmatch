"""Bucket vs legacy per-block scorer -- cluster-membership parity matrix.

This is the gate for making the bucket scorer the DEFAULT (perf/gm-bucket-default,
`_use_bucket_scorer`): the bucket path must produce byte-identical CLUSTER
MEMBERSHIP to the legacy `score_blocks_parallel` path for every config it is
allowed to default on. Each case runs the SAME data + config twice --
`backend='bucket'` (forced) vs the legacy path (`GOLDENMATCH_BUCKET_DEFAULT=0`) --
and compares the canonical multi-member clustering.

Known gaps (bucket NOT yet identical) are marked `xfail`; `_use_bucket_scorer`
routes them to legacy so they never regress in production. Close a gap => fix
bucket => drop the xfail => widen `_use_bucket_scorer`.
"""
from __future__ import annotations

import os

import pyarrow as pa
import pytest
from goldenmatch import dedupe_df
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    NegativeEvidenceField,
)

_FIRST = ["ann", "bob", "cara", "dan", "eve", "fay", "gus", "hal"]
_LAST = ["smith", "jones", "lee", "poe", "ray", "kim", "cruz", "diaz"]


def _fixture(n: int = 150):
    import random

    import polars as pl

    rng = random.Random(5)
    base = [
        (rng.choice(_FIRST), rng.choice(_LAST), f"{10000 + rng.randint(0, 40)}", f"u{i}@x.com")
        for i in range(int(n * 0.8))
    ]
    first, last, zips, email = [], [], [], []
    for f, l, z, e in base:
        first.append(f); last.append(l); zips.append(z); email.append(e)
    for _ in range(n - len(base)):  # near-dup: typo the first name, keep last+zip+email
        f, l, z, e = base[rng.randrange(len(base))]
        ff = (f[:-1] + ("x" if not f.endswith("x") else "y")) if len(f) > 2 else f
        first.append(ff); last.append(l); zips.append(z); email.append(e)
    idx = list(range(n)); rng.shuffle(idx)
    return pl.DataFrame({
        "first": [first[i] for i in idx], "last": [last[i] for i in idx],
        "zip": [zips[i] for i in idx], "email": [email[i] for i in idx],
    })


def _members(res) -> frozenset:
    cl = res.clusters or {}
    return frozenset(
        frozenset(int(m) for m in c.get("members", []))
        for c in cl.values()
        if len(c.get("members", [])) > 1
    )


def _bucket_vs_legacy(cfg: GoldenMatchConfig, tbl: pa.Table) -> tuple[frozenset, frozenset]:
    """Return (legacy_clustering, bucket_clustering) for the same data+config."""
    # Pure scorer parity: run BOTH on the polars lane (`find_fuzzy_matches` is
    # polars-only), differing ONLY in the block scorer -- legacy per-block vs
    # bucket. Removes the arrow-vs-polars confound; tests that the two SCORERS
    # agree. (Arrow-vs-polars backend parity for bucket is covered separately.)
    prev_bd = os.environ.get("GOLDENMATCH_BUCKET_DEFAULT")
    prev_fr = os.environ.get("GOLDENMATCH_FRAME")
    try:
        os.environ["GOLDENMATCH_FRAME"] = "polars"
        os.environ["GOLDENMATCH_BUCKET_DEFAULT"] = "0"  # legacy per-block
        legacy = _members(dedupe_df(tbl, config=cfg.model_copy(update={"backend": None})))
        os.environ.pop("GOLDENMATCH_BUCKET_DEFAULT", None)
        bucket = _members(dedupe_df(tbl, config=cfg.model_copy(update={"backend": "bucket"})))
    finally:
        for k, v in (("GOLDENMATCH_BUCKET_DEFAULT", prev_bd), ("GOLDENMATCH_FRAME", prev_fr)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return legacy, bucket


def _weighted(scorer: str, blocking: str = "static") -> GoldenMatchConfig:
    fields = [
        MatchkeyField(field="first", scorer=scorer, weight=0.5),
        MatchkeyField(field="last", scorer=scorer, weight=0.5),
    ]
    if blocking == "multi_pass":
        blk = BlockingConfig(strategy="multi_pass", passes=[
            BlockingKeyConfig(fields=["zip"], transforms=["strip"]),
            BlockingKeyConfig(fields=["last"], transforms=["lowercase"]),
        ])
    else:
        blk = BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"], transforms=["strip"])])
    return GoldenMatchConfig(matchkeys=[MatchkeyConfig(name="k", type="weighted", threshold=0.85, fields=fields)], blocking=blk)


# ── Safe envelope: bucket MUST equal legacy (what `_use_bucket_scorer` defaults on) ──


@pytest.mark.parametrize("scorer", ["jaro_winkler", "token_sort", "levenshtein", "soundex_match", "ensemble"])
@pytest.mark.parametrize("blocking", ["static", "multi_pass"])
def test_bucket_equals_legacy_weighted(scorer, blocking):
    legacy, bucket = _bucket_vs_legacy(_weighted(scorer, blocking), _fixture())
    assert bucket == legacy, (
        f"bucket != legacy for {scorer}/{blocking}: "
        f"only-legacy={len(legacy - bucket)} only-bucket={len(bucket - legacy)}"
    )


def test_bucket_equals_legacy_tf_freqs_weighted():
    """#1781: the bucket fast path must thread MatchkeyField.tf_freqs to plugin
    scorers. Legacy scores name_freq_weighted_jw via score_matrix(values,
    tf_freqs=...) (core/scorer.py:1236); pre-fix the bucket resolver grabbed
    plugin.score_pair BARE, so the data-driven downweight was silently dropped.

    Fixture: the table skews 'smith' COMMON (rarity ~0.13 -> weight ~0.65 ->
    identical-pair score ~0.65, clearly BELOW the 0.8 threshold) and 'zorvath'
    RARE (weight 1.0 -> score 1.0, clearly ABOVE). Legacy therefore clusters
    ONLY the zorvath pair -- its clustering depends on the downweight. Pre-fix
    bucket also clusters the smith pair (plain jw=1.0 short-circuit), diverging.
    Scores sit >=0.1 away from the threshold on both sides (legacy float32
    matrix vs bucket float64 per-pair; score_buckets.py:86-97 borderline-flip
    caveat)."""
    import goldenmatch.refdata  # noqa: F401  (registers name_freq_weighted_jw)
    import polars as pl

    df = pl.DataFrame({
        "last": ["smith", "smith", "zorvath", "zorvath", "lee", "poe"],
        "zip": ["10001", "10001", "10002", "10002", "10003", "10004"],
    })
    tf_freqs = {"smith": 0.4, "zorvath": 0.001}
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="k", type="weighted", threshold=0.8,
            fields=[MatchkeyField(
                field="last", scorer="name_freq_weighted_jw",
                weight=1.0, tf_freqs=tf_freqs,
            )],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["zip"], transforms=["strip"])],
        ),
    )
    legacy, bucket = _bucket_vs_legacy(cfg, df)
    # Anchor: legacy's clustering must actually depend on the downweight --
    # exactly ONE multi-member cluster (the rare zorvath pair). Without the
    # table both same-name pairs would cluster (2 clusters) and this test
    # couldn't detect the bucket-side drop.
    assert len(legacy) == 1, f"fixture anchor broken: legacy={legacy}"
    assert bucket == legacy, (
        f"bucket != legacy with tf_freqs: only-legacy={legacy - bucket} "
        f"only-bucket={bucket - legacy}"
    )


# ── Known gaps (bucket NOT yet identical -> routed to legacy by _use_bucket_scorer) ──


def test_bucket_equals_legacy_negative_evidence():
    """FIXED: bucket's slow-path fallback (find_fuzzy_matches, used when the fast
    path can't resolve a scorer like ensemble) now (a) gets a polars block on the
    arrow lane and (b) the slim projection keeps the raw NE/matchkey field columns
    it reads -- so NE penalty is applied identically to legacy."""
    import polars as pl

    df = pl.DataFrame({
        "first_name": ["Brian", "Brian"],
        "email": ["b@x.com", "b@x.com"],
        "phone": ["5551234", "5559999"],  # differ -> NE penalty
    })
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="primary", type="weighted", threshold=0.8,
            fields=[
                MatchkeyField(field="first_name", transforms=["lowercase"], scorer="ensemble", weight=0.5),
                MatchkeyField(field="email", transforms=["lowercase"], scorer="exact", weight=0.5),
            ],
            negative_evidence=[NegativeEvidenceField(
                field="phone", transforms=["digits_only"], scorer="exact", threshold=0.5, penalty=0.4)],
        )],
        blocking=BlockingConfig(strategy="static",
                                keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])]),
    )
    legacy, bucket = _bucket_vs_legacy(cfg, df)
    assert bucket == legacy
