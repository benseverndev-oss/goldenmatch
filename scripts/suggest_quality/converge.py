"""Unsupervised greedy convergence: apply review_config's (self-verified)
suggestions until none remain. NO ground truth -- the realistic path a user
gets. Distinct from oracle.py's label-driven convergence (which peeks at F1)."""
from __future__ import annotations

import copy

# Imported at module level so tests can monkeypatch these names on this module.
from goldenmatch.core.suggest import apply_suggestion, review_config

_STEP_CAP = 5


def converge_unsupervised(df, config, *, step_cap: int = _STEP_CAP, verify: bool = True):
    """Greedily apply the top suggestion until no more are emitted.

    Args:
        df: The labeled DataFrame (must already have ``__row_id__``).
        config: The starting GoldenMatchConfig.
        step_cap: Maximum number of greedy steps.
        verify: Passed to ``review_config``.  Default True (self-verified,
            realistic user path).  The gym calls with ``verify=False`` to
            test raw kernel rule capability without the health-proxy filter.

    Returns:
        ``(final_config, trail)`` where ``trail`` is a list of applied
        :class:`Suggestion` objects.
    """
    current = copy.deepcopy(config)
    applied_ids: set = set()
    trail: list = []
    for _ in range(step_cap):
        suggestions = review_config(df, current, verify=verify)
        if not suggestions:
            break
        top = suggestions[0]
        if top.id in applied_ids:
            break
        applied_ids.add(top.id)
        current = apply_suggestion(current, top)
        trail.append(top)
    return current, trail
