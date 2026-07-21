"""Issue #1805 (checkbox 1) — arrow-lane vs polars-lane FS parity.

`GOLDENMATCH_FRAME` (default ``arrow`` since v3.0) selects the frame backend the
whole pipeline flows through. The frame-backend differential gate
(`test_frame_backend_differential.py`) runs both lanes but its configs are
explicitly exact/weighted only -- NO probabilistic/EM matchkey -- and the
bucket-vs-legacy matrix pins BOTH runs to the polars lane to remove the
arrow-vs-polars confound. So an FS (probabilistic) config had never been run
through both lanes and compared: an arrow/polars divergence in the FS
score/block path (the class the frame gate exists to catch) was uncovered.

This runs one moderate FS `dedupe_df` under each lane and asserts identical
cluster membership. The EM is pinned via a persisted `model_path` so training is
identical across lanes (FS EM is sample-order sensitive; the frame axis is what
we isolate). The fixture keeps true pairs well above and non-pairs well below
threshold so a benign f32/f64 wobble can't flip a borderline decision. Skips
when polars is absent (the arrow lane is the polars-free default).
"""
from __future__ import annotations

import importlib.util
import os

import pytest

_HAS_POLARS = importlib.util.find_spec("polars") is not None

pytestmark = pytest.mark.skipif(
    not _HAS_POLARS, reason="polars lane requires the optional polars dependency"
)


def _fixture(n_entities: int = 120):
    """Two rows per entity: a base + a typo'd near-duplicate sharing an exact
    email (a strong FS agreement). Emails are unique per entity, so no pair
    crosses entities; each entity is a clean 2-member cluster. Blocked by a
    coarse zip so blocks hold several entities."""
    import polars as pl

    first = ["john", "mary", "peter", "susan", "david", "karen", "brian", "nancy"]
    last = ["smith", "jones", "brown", "davis", "wilson", "clark", "lewis", "young"]
    rows = []
    rid = 0
    for e in range(n_entities):
        f = first[e % len(first)]
        l = last[(e // len(first)) % len(last)]
        z = f"{10000 + (e % 6):05d}"
        email = f"e{e}@x.com"
        rows.append({"__row_id__": rid, "first_name": f, "last_name": l, "email": email, "zip": z})
        rid += 1
        # near-dup: adjacent-transpose the first name (keeps jaro_winkler high)
        ff = (f[:2] + f[3] + f[2] + f[4:]) if len(f) >= 5 else f + "x"
        rows.append({"__row_id__": rid, "first_name": ff, "last_name": l, "email": email, "zip": z})
        rid += 1
    return pl.DataFrame(rows)


def _config(model_path: str):
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="fs", type="probabilistic", model_path=model_path,
            fields=[
                MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
                MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2, partial_threshold=0.85),
                MatchkeyField(field="email", scorer="exact", levels=2),
            ],
        )],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"])]),
    )


def _members(res) -> frozenset:
    cl = res.clusters or {}
    return frozenset(
        frozenset(int(m) for m in c.get("members", []))
        for c in cl.values()
        if len(c.get("members", [])) > 1
    )


def _dedupe_on_lane(df, cfg, lane: str, monkeypatch):
    import goldenmatch as gm

    monkeypatch.setenv("GOLDENMATCH_FRAME", lane)
    # Isolate the frame axis from the native-kernel axis.
    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "0")
    return _members(gm.dedupe_df(df, config=cfg, confidence_required=False))


def _pin_model(tmp_path, df, cfg) -> str:
    """Train the EM once and persist it so both lanes reuse the identical model
    (via load_or_train_em's model_path seam)."""
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.probabilistic import train_em

    mk = cfg.matchkeys[0]
    blocks = build_blocks(df.lazy(), cfg.blocking)
    train_em(df, mk, blocks=blocks, blocking_fields=["zip"], seed=42).save_json(mk.model_path)
    return mk.model_path


def test_fs_arrow_polars_cluster_membership_parity(tmp_path, monkeypatch):
    model_path = str(tmp_path / "fs_model.json")
    df = _fixture()
    cfg = _config(model_path)
    _pin_model(tmp_path, df, cfg)
    assert os.path.exists(model_path)

    arrow = _dedupe_on_lane(df, cfg, "arrow", monkeypatch)
    polars = _dedupe_on_lane(df, cfg, "polars", monkeypatch)

    # The fixture must actually merge (else parity is vacuous).
    assert arrow, "fixture produced no multi-member clusters"
    assert arrow == polars, (
        f"arrow-vs-polars FS divergence: only-arrow={len(arrow - polars)} "
        f"only-polars={len(polars - arrow)}"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
