"""#2457: an ambient LLM key must not change what the benchmark lane measures.

`goldenmatch.core.llm_extract` reads `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`
straight out of `os.environ` with no opt-in flag, so merely having a key
exported changes what this lane reports. On Abt-Buy that path handles 959 of
2173 records, and it moves the score:

    no key   f1=0.1723  P=0.1068  R=0.4463   <- what CI measures
    key      f1=0.1838  P=0.1132  R=0.4875

A benchmark whose answer depends on an unrelated env var cannot be compared
across machines, which is the whole reason the lane exists. So the key is
dropped rather than merely reported.

Note the guard is justified by determinism alone. It does NOT explain the
committed 0.5037 Abt-Buy baseline: that was the first hypothesis, and the keyed
measurement above refuted it. See the `Abt-Buy` entry in `_F1_FLOORS` for the
full matrix and what remains unexplained.

Two properties are pinned here, and the second is the one with teeth: a guard
that strips the key but still lets the report claim "off" for a run that could
not prove it would be a check that does not fire.
"""
from __future__ import annotations

import os

import pytest
import run_benchmarks as rb

# ── the keys are actually removed ─────────────────────────────────────────

@pytest.mark.parametrize("var", ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"])
def test_ambient_key_is_removed_by_default(monkeypatch, var):
    monkeypatch.setenv(var, "sk-not-a-real-key")
    assert rb._neutralize_ambient_llm_keys(with_llm=False) is True
    assert os.environ.get(var) is None


def test_with_llm_keeps_the_key(monkeypatch):
    """--with-llm is a deliberate request; it must still reach the provider."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
    assert rb._neutralize_ambient_llm_keys(with_llm=True) is False
    assert os.environ["OPENAI_API_KEY"] == "sk-not-a-real-key"


def test_no_key_present_reports_nothing_suppressed(monkeypatch):
    for var in rb._LLM_KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    assert rb._neutralize_ambient_llm_keys(with_llm=False) is False


def test_both_keys_removed_together(monkeypatch):
    """Anthropic is the fallback provider, so stripping only OpenAI leaves the
    extraction path live on a different vendor -- same defect, quieter."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-a")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-b")
    assert rb._neutralize_ambient_llm_keys(with_llm=False) is True
    assert not [k for k in rb._LLM_KEY_VARS if os.environ.get(k)]


# ── the report cannot claim "off" it has not earned ───────────────────────

def test_label_off_only_when_suppression_ran():
    assert rb._llm_label({"with_llm": False, "llm_keys_suppressed": False}) == "off"
    assert rb._llm_label({"with_llm": False, "llm_keys_suppressed": True}) == "off"


def test_label_on_when_requested():
    assert rb._llm_label({"with_llm": True, "llm_keys_suppressed": False}) == "on"


def test_pre_guard_payload_does_not_claim_off():
    """The regression itself: the old committed JSON has no `llm_keys_suppressed`,
    so nothing can prove a key was absent. It must not render as a flat "off"."""
    label = rb._llm_label({"with_llm": False})
    assert label != "off"
    assert "not recorded" in label


# ── product datasets report real controller health ────────────────────────

class _Prof:
    def health(self):
        return type("H", (), {"value": "RED"})()


class _Hist:
    stop_reason = type("S", (), {"value": "BUDGET_ITERATIONS"})()


class _Report:
    controller_profile = _Prof()
    controller_history = _Hist()


class _Result:
    postflight_report = _Report()


def test_controller_health_reads_a_red_verdict():
    assert rb._controller_health(_Result()) == ("RED", "BUDGET_ITERATIONS")


def test_controller_health_unknown_not_na_when_absent():
    """"unknown" rather than "n/a" is load-bearing: `_check_quality_floors` fails
    a RED run outright, so "cannot report" must not look like "nothing to report".
    The product datasets hardcoded "n/a", which is why two RED configs in the
    2026-08-18 nightly passed the RED check in silence."""
    assert rb._controller_health(object()) == ("unknown", "unknown")
    assert rb._controller_health(None) == ("unknown", "unknown")


def test_red_health_is_a_breach_even_above_the_floor():
    """The check the "n/a" hardcode disabled.

    Split in two since Abt-Buy became quarantined (#2717): a quarantined
    dataset's RED is still DETECTED, it just reports instead of failing. The
    guarantee the "n/a" hardcode broke was detection, so that is asserted on
    Abt-Buy directly -- and the failing half is asserted on a dataset that is
    not quarantined, so neither half can pass vacuously.
    """
    base = rb._QUARANTINE["Abt-Buy"]["f1_at_quarantine"]
    failing, quarantined = rb._check_quality_floors([
        {"name": "Abt-Buy", "f1": base, "health": "RED",
         "stop_reason": "BUDGET_ITERATIONS"},
    ])
    assert any("RED" in b for b in quarantined), (failing, quarantined)

    failing, _ = rb._check_quality_floors([
        {"name": "Febrl3", "f1": 0.99, "health": "RED",
         "stop_reason": "BUDGET_ITERATIONS"},
    ])
    assert any("RED" in b for b in failing), failing
