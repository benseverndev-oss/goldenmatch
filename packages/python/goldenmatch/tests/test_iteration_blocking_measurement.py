"""Per-iteration blocking measurement must not be gated to n_rows >= REFUSE_AT_N.

Before this fix, `_should_measure_blocking` was only ever invoked (a) inside
the RED-refuse check, itself gated on n_rows >= REFUSE_AT_N, or (b) after
`best_entry` was already chosen, purely to inform planner backend selection.
Below 100K rows, every iteration's own `profile.blocking` stayed the
sample-extrapolated all-zero default for a multi_pass config -- exactly the
shape that caused the zero-config recall incident, just never triggering the
>=100K refusal path that would have surfaced it. #2663 reproduces this: an
845-row multi_pass run whose blocking never measures during the loop.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.core.autoconfig_controller import (
    AutoConfigController,
    ControllerBudget,
    resolve_planning_effort,
)
from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy


def _small_multipass_frame(n: int = 800) -> pa.Table:
    """Small enough to stay well under REFUSE_AT_N, with a multi_pass-shaped
    blocking key (two passes) so the fast vectorized path applies."""
    return pa.table({
        "id": [f"r{i}" for i in range(n)],
        "postcode": [f"{i % 40:04d}" for i in range(n)],
        "org_name": [f"org-{i % 60}" for i in range(n)],
    })


def test_first_iteration_blocking_is_measured_below_refuse_at_n():
    """The regression this pins: at 800 rows, iteration 0's own committed
    profile.blocking must NOT be the unmeasured all-zero default once a
    multi_pass/static plan is in play."""
    from goldenmatch.core.autoconfig_controller import _LAST_CONTROLLER_RUN

    df = _small_multipass_frame()
    ctrl = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget.for_dataset(800, resolve_planning_effort("normal")),
    )
    ctrl.run(df, confidence_required=False)
    history = _LAST_CONTROLLER_RUN.get()
    assert history is not None
    measured = [
        e for e in history.entries
        if e.config.blocking is not None
        and e.config.blocking.strategy in ("static", "multi_pass")
        and (e.config.blocking.keys or e.config.blocking.passes)
    ]
    assert measured, "no iteration produced a static/multi_pass blocking plan to check"
    assert any(e.profile.blocking.n_blocks > 0 for e in measured), (
        "every measurable-strategy iteration still reports the unmeasured "
        "n_blocks=0 default below REFUSE_AT_N"
    )
