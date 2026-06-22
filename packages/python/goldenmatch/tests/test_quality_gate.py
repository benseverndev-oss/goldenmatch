"""In-house ER quality gate — a self-contained, license-clean substitute for
the DQbench composite as the *internal CI gate*.

Runs unconditionally in the python lane (no restricted dataset). Two assertions
on synthetic labeled data whose ground truth is known by construction:

1. **Composite floor** — F1 vs ground truth stays above a per-config sanity
   floor (catches gross accuracy regressions). Floors are intentionally
   conservative: synthetic data is "easy", so the absolute number isn't a
   quality *claim* (DQbench / the public sets remain the external leaderboard
   for that). The value here is regression detection. Tighten from CI output.

2. **Backend parity** — polars-direct and bucket produce IDENTICAL clusters on
   the same data + config. This is the runnable substitute for the
   DQbench-on-native gate behind the bucket+native planner flip (#526):
   identical clusters ⟹ identical precision/recall/F1, by construction. Runs
   without the native ext (bucket uses its Python scorer here; the native
   *kernel*'s score-parity is covered separately by test_native_parity.py), so
   it gates every PR.

Synthetic data: distributed surnames (no soundex collapse) + injected near-dup
clones (typo'd name, shared email/zip) with known entity membership →
ground-truth pairs by construction. Deterministic seed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from goldenmatch import dedupe_df

# gen_labeled is the shared person-match anchor (one definition for tests + the
# quality harness). Add repo-root to sys.path so scripts.* is importable.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.evaluate import evaluate_clusters

from scripts.autoconfig_quality.anchors import gen_labeled  # noqa: E402


def _cfg(backend: str | None, kind: str) -> GoldenMatchConfig:
    if kind == "exact_email":
        mks = [MatchkeyConfig(name="email", type="exact",
                              fields=[MatchkeyField(field="email")])]
        blocking = None
    else:  # fuzzy_name (block on zip, fuzzy on first+last name)
        mks = [MatchkeyConfig(
            name="name", type="weighted", threshold=0.85,
            fields=[
                MatchkeyField(field="first_name", scorer="jaro_winkler", weight=0.5),
                MatchkeyField(field="last_name", scorer="jaro_winkler", weight=0.5),
            ],
        )]
        blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    kwargs: dict = {"matchkeys": mks, "backend": backend}
    if blocking is not None:
        kwargs["blocking"] = blocking
    return GoldenMatchConfig(**kwargs)


def _partition(result) -> set:
    return {
        frozenset(c["members"]) for c in result.clusters.values()
        if len(c.get("members", [])) > 1
    }


# Conservative floors (synthetic is easy); tighten to ~actual-0.02 from CI output.
_FLOORS = {"exact_email": 0.90, "fuzzy_name": 0.75}


@pytest.fixture(scope="module")
def labeled():
    return gen_labeled()


@pytest.mark.parametrize("kind", ["exact_email", "fuzzy_name"])
def test_quality_composite_floor(labeled, kind):
    df, gt = labeled
    summary = evaluate_clusters(dedupe_df(df, config=_cfg(None, kind)).clusters, gt).summary()
    assert summary["f1"] >= _FLOORS[kind], (kind, summary)


@pytest.mark.parametrize("kind", ["exact_email", "fuzzy_name"])
def test_backend_parity_polars_vs_bucket(labeled, kind):
    """The bucket backend must cluster identically to polars-direct — the
    runnable gate behind the bucket+native flip (#526)."""
    df, _ = labeled
    polars_direct = dedupe_df(df, config=_cfg(None, kind))
    bucket = dedupe_df(df, config=_cfg("bucket", kind))
    assert _partition(polars_direct) == _partition(bucket), (
        f"{kind}: polars-direct and bucket produced DIFFERENT clusters"
    )
