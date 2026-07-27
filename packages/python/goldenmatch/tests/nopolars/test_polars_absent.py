"""GoldenMatch runs its zero-config dedupe with **polars genuinely uninstalled**.

goldenmatch is arrow-native by design: `polars` is NOT a base dependency (nor an
extra) -- the package ships a lazy `_polars_lazy` proxy and every default path is
meant to run on pyarrow + numpy + rapidfuzz alone. This module imports polars
NOWHERE and proves that contract for the entry point that matters: zero-config
`dedupe_df` on a `pa.Table` (exactly how goldengraph's cross-document entity
resolution calls it).

This is the living guard for the regression that sat red on `main` for ~2 weeks
(fixed in #1956): the bucket fuzzy fallback (`score_buckets._score_block_frame`)
pre-converted an arrow block to polars (`pl.from_arrow`, guarded by an
`isinstance(block_df, pl.DataFrame)` probe that itself forced the polars import),
so every autoconfig iteration crashed with `ModuleNotFoundError: polars` on the
common tiny-N weighted-fuzzy config. Nothing in `ci-required` exercised goldenmatch
with polars absent, so it regressed silently -- this lane closes that gap, the same
way `goldenflow_nopolars` / `goldencheck_nopolars` guard their siblings.

It is `skipif`'d OUT of the normal suite (where polars IS present), so it is inert
there and only executes in the dedicated `goldenmatch_nopolars` CI lane (and any
local run where polars is absent). The native scoring kernel is built for the lane
so the planner takes the `bucket` backend -- the exact path the bug lived on.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

# Keep the diagnostics prompt out of the captured output; the RED zero-config
# config on toy data is expected and irrelevant to the polars-free assertion.
os.environ.setdefault("GOLDEN_DIAGNOSTICS", "0")

_HAS_POLARS = importlib.util.find_spec("polars") is not None

pytestmark = pytest.mark.skipif(
    _HAS_POLARS,
    reason="polars-absent proof -- only runs where polars is NOT installed (the goldenmatch_nopolars lane)",
)


def _cluster_of(clusters: dict, row_id: int) -> int:
    """Return the cluster id that ``row_id`` belongs to (raises if unassigned)."""
    for cid, info in clusters.items():
        if row_id in {int(x) for x in info["members"]}:
            return cid
    raise AssertionError(f"row {row_id} not found in any cluster")


def test_import_goldenmatch_without_polars() -> None:
    import goldenmatch  # must not raise, must not import polars

    assert "polars" not in sys.modules
    # the public entry points survive a polars-absent import
    for name in ("dedupe_df", "match_df", "record_fingerprint", "DedupeResult"):
        assert hasattr(goldenmatch, name), name


def test_zero_config_dedupe_df_arrow_is_polars_free() -> None:
    """The bug reproducer: zero-config ``dedupe_df`` on a ``pa.Table`` completes
    with polars absent and clusters the exact duplicates correctly."""
    import goldenmatch as gm
    import pyarrow as pa

    df = pa.table({"name": ["Acme Inc", "Acme Inc", "Beta"], "type": ["org", "org", "org"]})
    result = gm.dedupe_df(df)

    # the two identical "Acme Inc" rows collapse; "Beta" stays separate
    assert _cluster_of(result.clusters, 0) == _cluster_of(result.clusters, 1)
    assert _cluster_of(result.clusters, 2) != _cluster_of(result.clusters, 0)
    # the scoring path must never have reached for polars
    assert "polars" not in sys.modules


def test_zero_config_dedupe_df_larger_block_is_polars_free() -> None:
    """A bigger arrow input (multi-row blocks -> the vectorized scoring lane)
    also completes polars-free, so the guard covers more than the tiny-N path."""
    import goldenmatch as gm
    import pyarrow as pa

    names = [
        "Acme Inc", "Acme Inc", "Acme Incorporated", "Beta LLC", "Beta LLC",
        "Gamma Co", "Gamma Company", "Delta", "Delta", "Epsilon",
    ] * 3
    df = pa.table({"name": names, "type": ["org"] * len(names)})
    result = gm.dedupe_df(df)

    # the two byte-identical "Acme Inc" rows (indices 0 and 1) still co-cluster
    assert _cluster_of(result.clusters, 0) == _cluster_of(result.clusters, 1)
    assert "polars" not in sys.modules


def test_profile_for_agent_arrow_is_polars_free() -> None:
    """The agent profiler runs on a ``pa.Table`` with polars absent (it was made
    arrow-native via the ``to_frame`` abstraction). Locks the 'agent path is
    polars-free' contract the way the dedupe cases lock the scoring path."""
    import pyarrow as pa
    from goldenmatch.core.agent import profile_for_agent, select_strategy

    df = pa.table({
        "ssn": ["111-22-3333", "444-55-6666", "777-88-9999"],
        "full_name": ["Alice Smith", "Alice Smith", "Bob Jones"],
    })
    profile = profile_for_agent(df)
    assert profile.row_count == 3
    assert profile.has_sensitive  # ssn is a sensitive column name
    # sensitive fields no longer force pprl at the default (allow_pprl=False)
    decision = select_strategy(profile)
    assert decision.pprl_available and decision.strategy != "pprl"
    assert "polars" not in sys.modules


def test_agent_session_analyze_arrow_is_polars_free(tmp_path) -> None:
    """``AgentSession.analyze`` (CSV read via ``read_table_arrow`` + profiling)
    completes polars-free -- the file-loading half of the agent path."""
    from goldenmatch.core.agent import AgentSession

    csv = tmp_path / "people.csv"
    csv.write_text(
        "ssn,full_name\n111-22-3333,Alice Smith\n444-55-6666,Bob Jones\n",
        encoding="utf-8",
    )
    reasoning = AgentSession().analyze(str(csv))
    assert reasoning["profile"]["row_count"] == 2
    assert reasoning["profile"]["has_sensitive"]
    assert "polars" not in sys.modules


def test_blocking_risk_arrow_is_polars_free() -> None:
    """``core.quality.blocking_risk`` (the quality-aware-blocking recall lever's
    GoldenCheck bridge) runs on a ``pa.Table`` polars-free. It routes through the
    arrow-native ``goldencheck.cell_quality``; before the arrow port it crashed on
    ``df.height`` / ``pl.Utf8`` the moment the recall lever was enabled."""
    import pyarrow as pa
    from goldenmatch.core.quality import blocking_risk

    # 60 rows (clears the fuzzy 50-row guard); "Californa" is a near-duplicate
    # variant of the canonical (more frequent) "California" -> 15/60 = 0.25 risk.
    state = ["California"] * 40 + ["Californa"] * 15 + ["Texas"] * 5
    tbl = pa.table({"__row_id__": list(range(len(state))), "state": state})

    risk = blocking_risk(tbl)
    assert risk is not None and "state" in risk
    assert abs(risk["state"] - 0.25) < 1e-9
    assert "polars" not in sys.modules


def test_guarded_matchkey_arrow_is_polars_free() -> None:
    """A guarded matchkey runs on a ``pa.Table`` polars-free. The pipeline's
    raw-value capture (for the guard's ``a_``/``b_`` predicate) reads through the
    arrow-native ``to_frame`` + Column seam, NOT polars ``collect_schema`` /
    ``.select`` / ``[c]`` indexing -- so a guard evaluates correctly with polars
    absent. Guarded configs are never exercised by the zero-config cases above,
    so this is the only lane that covers the guard capture path polars-free."""
    import goldenmatch as gm
    import pyarrow as pa
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    # rows 0,1 share the placeholder ssn (guard must suppress); 2,3 share a real
    # ssn (guard holds -> merge).
    df = pa.table({
        "ssn": ["000-00-0000", "000-00-0000", "111-22-3333", "111-22-3333"],
        "name": ["A", "B", "C", "D"],
    })
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="ssn", type="exact", fields=[MatchkeyField(field="ssn")],
            guard="a_ssn != '000-00-0000' and b_ssn != '000-00-0000'",
        )],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["ssn"])]),
    )
    result = gm.dedupe_df(df, config=cfg)
    # placeholder pair NOT merged; real-ssn pair merged
    assert _cluster_of(result.clusters, 0) != _cluster_of(result.clusters, 1)
    assert _cluster_of(result.clusters, 2) == _cluster_of(result.clusters, 3)
    assert "polars" not in sys.modules
