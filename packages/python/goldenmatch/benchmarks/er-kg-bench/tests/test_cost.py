"""Per-adapter ``last_cost()`` -- the cost axis the runner records.

The deterministic default (zeros) and the base-class contract are checked here
without importing goldenmatch (keeps these on the plain venv). The two paid
goldenmatch paths -- ``goldenmatch(auto+llm)`` and ``goldenmatch(emb-openai)``
-- can only spend money with an ``OPENAI_API_KEY`` + network, so their non-zero
cost is verified by CI's keyed bench lane, NOT here.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

ZERO = {"llm_calls": 0, "llm_tokens": 0, "llm_usd": 0.0}


def test_zero_cost_constant_shape():
    from erkgbench.adapters.base import ZERO_COST  # pyright: ignore[reportMissingImports]

    assert ZERO_COST == ZERO


def test_adapter_base_default_is_zero():
    # The default lives ONCE on the base class -- a subclass that doesn't
    # override gets zeros.
    from erkgbench.adapters.base import AdapterBase  # pyright: ignore[reportMissingImports]

    class _Det(AdapterBase):
        pass

    assert _Det().last_cost() == ZERO


def test_last_cost_of_defaults_to_zero_for_adapter_without_method():
    # An adapter that never spends (no last_cost at all) still reports zeros
    # through the runner-facing accessor.
    from erkgbench.adapters.base import last_cost_of  # pyright: ignore[reportMissingImports]

    class _NoCost:
        name = "fake"

    assert last_cost_of(_NoCost()) == ZERO


def test_last_cost_of_reads_override():
    from erkgbench.adapters.base import last_cost_of  # pyright: ignore[reportMissingImports]

    class _Paid:
        def last_cost(self) -> dict:
            return {"llm_calls": 3, "llm_tokens": 120, "llm_usd": 0.004}

    assert last_cost_of(_Paid()) == {"llm_calls": 3, "llm_tokens": 120, "llm_usd": 0.004}


def test_last_cost_of_returns_a_copy_not_the_shared_constant():
    # Mutating one adapter's reported cost must not corrupt the shared default.
    from erkgbench.adapters.base import (  # pyright: ignore[reportMissingImports]
        ZERO_COST,
        last_cost_of,
    )

    cost = last_cost_of(object())
    cost["llm_usd"] = 9.99
    assert ZERO_COST["llm_usd"] == 0.0


def test_goldenmatch_adapter_zero_before_resolve():
    # No network: a fresh GoldenMatchAdapter reports zeros until resolve() runs.
    # (The auto+llm non-zero path needs OPENAI_API_KEY; CI's keyed lane covers
    # it.) This import DOES pull goldenmatch, so it runs on the shadow venv.
    from erkgbench.adapters.goldenmatch_adapter import (  # pyright: ignore[reportMissingImports]
        GoldenMatchAdapter,
    )

    assert GoldenMatchAdapter(mode="auto").last_cost() == ZERO
    assert GoldenMatchAdapter(mode="auto_llm").last_cost() == ZERO


def test_goldenmatch_emb_adapter_zero_for_inhouse():
    # provider=None is the offline char-ngram path -- no API, always zero.
    from erkgbench.adapters.goldenmatch_adapter import (  # pyright: ignore[reportMissingImports]
        GoldenMatchEmbAnnAdapter,
    )

    assert GoldenMatchEmbAnnAdapter().last_cost() == ZERO
