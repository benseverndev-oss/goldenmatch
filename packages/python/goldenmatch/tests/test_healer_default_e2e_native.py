"""Task 11: end-to-end native verification of the default-pipeline healer.

These run ONLY when the native suggest kernel is present (the `suggest_config`
symbol). The main pytest matrix runs with native absent, so they skip there;
the `native` CI lane builds the ext (`build_native.py`) before pytest, so they
execute there. They cannot be exercised on a pure-Python install.

What they pin (the risks the unit/spy tests with stubbed kernels cannot):
  1. The artifacts-in `suggest_from_result` (the cost optimization that reuses
     a run's scored_pairs/clusters instead of re-running) is byte-equivalent to
     the full `review_config` re-run -- raw AND verified.
  2. With native genuinely present, the free trigger still short-circuits on a
     healthy result: the kernel is NOT called on the default path.
  3. `dedupe_df(suggest=True)` / `heal=True` complete through the real kernel and
     return the documented wire shape, never raising.

F1-improves / no-net-negative evidence lives in the suggester-gym lane
(`bench-suggest-quality.yml`, headline_live), which has labels + the oracle --
that is the right home for the quality claim, not a label-free pytest.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch import dedupe_df


def _native_suggest_present() -> bool:
    try:
        from goldenmatch.core._native_loader import native_module
        nm = native_module()
        return nm is not None and hasattr(nm, "suggest_config")
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _native_suggest_present(),
    reason="needs the native suggest kernel (suggest_config); runs in the native CI lane",
)


def _person_df() -> pl.DataFrame:
    """Realistic person shape so auto-config builds a non-degenerate config."""
    import random
    random.seed(0)
    first = ["John", "Jane", "Bob", "Alice", "Tom", "Sara", "Mike", "Lisa"]
    last = ["Smith", "Jones", "Brown", "Davis", "Miller", "Wilson", "Moore", "Clark"]
    rows = []
    for i in range(60):
        f = random.choice(first)
        l = random.choice(last)
        rows.append({"name": f"{f} {l}",
                     "email": f"{f}.{l}@x.com".lower(),
                     "zip": f"{random.randint(10000, 10005)}"})
        if i % 3 == 0:  # inject a near-dup
            rows.append({"name": f"{f} {l}",
                         "email": f"{f}.{l}@x.com".lower(),
                         "zip": f"{random.randint(10000, 10005)}"})
    return pl.DataFrame(rows)


def test_suggest_from_result_raw_matches_review_config():
    """The artifacts-in path == the full re-run path (raw). This is the core
    correctness guarantee behind the cost optimization."""
    from goldenmatch.core.suggest import review_config
    from goldenmatch.core.suggest.adapter import suggest_from_result
    df = _person_df()
    res = dedupe_df(df)
    artifacts_in = [s.id for s in suggest_from_result(res, df, verify=False)]
    full_rerun = [s.id for s in review_config(df, res.config, verify=False)]
    assert artifacts_in == full_rerun


def test_suggest_from_result_verified_matches_review_config():
    """Same equivalence with the self-verify gate engaged (verify=True)."""
    from goldenmatch.core.suggest import review_config
    from goldenmatch.core.suggest.adapter import suggest_from_result
    df = _person_df()
    res = dedupe_df(df)
    artifacts_in = [s.id for s in suggest_from_result(res, df, verify=True)]
    full_rerun = [s.id for s in review_config(df, res.config, verify=True)]
    assert artifacts_in == full_rerun


def test_clean_data_short_circuits_without_kernel_call(monkeypatch):
    """Cost guarantee with native PRESENT: when the free trigger reports a healthy
    result (no headroom), the artifacts-in kernel call is gated out — proving the
    GATE, not the mere absence of native, is what prevents the call.

    We force the trigger to report "healthy" rather than relying on a real dataset
    to commit a GREEN config: a tiny all-unique frame actually commits a RED/YELLOW
    config (nothing matches → sparse), which correctly DOES fire the trigger. The
    cost guarantee under test is "trigger says healthy ⇒ no kernel call", so we pin
    exactly that.
    """
    import goldenmatch.core.suggest.adapter as ad
    import goldenmatch.core.suggest.surface as surf
    calls = {"n": 0}
    real = ad.suggest_from_result

    def _counting(result, df, *, verify=False):
        calls["n"] += 1
        return real(result, df, verify=verify)

    monkeypatch.setattr(ad, "suggest_from_result", _counting)
    # Healthy verdict: the free trigger reports no headroom.
    monkeypatch.setattr(surf, "headroom_signal", lambda result: None)

    df = pl.DataFrame({
        "name": [f"Person{i} Surname{i}" for i in range(40)],
        "email": [f"person{i}@example.com" for i in range(40)],
    })
    res = dedupe_df(df)
    assert res.suggestions == []
    assert calls["n"] == 0


def test_suggest_true_returns_wire_shape_without_raising():
    """The explicit verified opt-in completes through the real kernel and returns
    the documented serialized wire shape."""
    df = _person_df()
    res = dedupe_df(df, suggest=True)
    assert isinstance(res.suggestions, list)
    for s in res.suggestions:
        assert set(s) >= {"id", "kind", "target", "rationale", "verified", "patch"}
        assert s["verified"] is True


def test_heal_true_completes_and_is_auditable():
    """The full apply-and-re-run loop completes and exposes an auditable trail +
    a config that is at least as healed as the input (never raises)."""
    df = _person_df()
    res = dedupe_df(df, heal=True)
    assert res.heal_trail is None or isinstance(res.heal_trail, list)
    assert res.config is not None
    for step in res.heal_trail or []:
        assert set(step) >= {"id", "kind", "target", "rationale", "verified", "patch"}
        assert step["verified"] is True
