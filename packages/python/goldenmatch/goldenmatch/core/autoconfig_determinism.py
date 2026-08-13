"""Deterministic-search mode for auto-config (#2532).

Two layers of the auto-config search cut themselves short on a WALL-CLOCK
budget: the controller's ``ControllerBudget.max_seconds`` (which stops the
iteration loop with ``StopReason.BUDGET_TIME``) and the per-indicator budgets in
:mod:`goldenmatch.core.indicators` (which make an indicator return ``None`` or a
default, silently changing what the indicator-aware rules decide).

Both make the committed config a function of **machine speed and load** rather
than of the code and the data. The same input, on the same commit, can produce a
different config on a slow runner than on a fast one. That is fine for an
interactive run -- a user wants a bounded wait more than a bit-reproducible
search -- and fatal for anything that PINS controller output: a gate over a
wall-clock-dependent value can go red with no code change, and green while
masking a real one. That is the "a check exists and does not fire" family this
repo keeps paying for.

``GOLDENMATCH_AUTOCONFIG_DETERMINISTIC=1`` removes every wall-clock cutoff from
the search, leaving the ITERATION caps as the only stopping rule. Same code +
same data then give the same config on any host, at the cost of an unbounded
runtime. That is the right trade for a pin harness or a scorecard gate and the
wrong one for an interactive run, which is why it is opt-in.

Two deliberate non-properties:

* **It is not a config field.** It describes the harness running auto-config,
  not the dataset being configured, so it must not travel inside a committed
  ``GoldenMatchConfig``.
* **It does not re-enable a DISABLED indicator.** Setting a budget constant to
  ``<= 0.0`` is a semantic "this indicator is off" switch, not a time limit;
  those pre-flight checks are untouched. Only elapsed-time comparisons are
  bypassed.

The env var is read at CALL time, never cached in a module constant, so a
harness can set it after import -- the import-time-constant trap that has bitten
the distributed knobs (#957).
"""
from __future__ import annotations

import os
import time

ENV_VAR = "GOLDENMATCH_AUTOCONFIG_DETERMINISTIC"

_TRUE = ("1", "true", "yes", "on")


def deterministic_search_enabled() -> bool:
    """True when the auto-config search must ignore its wall-clock budgets."""
    return os.environ.get(ENV_VAR, "0").strip().lower() in _TRUE


def over_budget(start: float, budget: float) -> bool:
    """Has ``budget`` seconds elapsed since ``start`` -- unless we're pinned?

    The single place the auto-config search asks "am I out of time". Returns
    ``False`` unconditionally in deterministic mode, which is what turns an
    iteration-and-time-bounded search into an iteration-bounded one.
    """
    if deterministic_search_enabled():
        return False
    return (time.time() - start) > budget
