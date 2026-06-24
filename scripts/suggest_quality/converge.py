"""Unsupervised greedy convergence: apply review_config's (self-verified)
suggestions until none remain. NO ground truth -- the realistic path a user
gets. Distinct from oracle.py's label-driven convergence (which peeks at F1)."""
from __future__ import annotations

import copy

# Imported at module level so tests can monkeypatch these names on this module.
from goldenmatch.core.suggest import apply_suggestion, review_config

_STEP_CAP = 5


def converge_unsupervised(df, config, *, step_cap: int = _STEP_CAP):
    current = copy.deepcopy(config)
    applied_ids: set = set()
    trail: list = []
    for _ in range(step_cap):
        suggestions = review_config(df, current)
        if not suggestions:
            break
        top = suggestions[0]
        if top.id in applied_ids:
            break
        applied_ids.add(top.id)
        current = apply_suggestion(current, top)
        trail.append(top)
    return current, trail
