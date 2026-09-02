"""Synthetic fixture: five claims -- enforced, unenforced, prose-only,
unresolvable, module-level.

Module-level claim: this module mirrors slow_lane as a whole and is never
triaged -- a module has no single symbol a test can reference.
"""


def fast_lane():
    """Mirrors slow_lane but skips validation."""
    return 1


def slow_lane():
    return 1


def orphan_lane():
    """Mirrors slow_lane and nothing tests them together."""
    return 1


def prose_lane():
    """Mirrors slow_lane; a test mentions both only in prose."""
    return 1


def stray_lane():
    """Mirrors the legacy pipeline that no longer exists here."""
    return 1


def arrow_lane():
    """Mirrors slow_lane: caller → callee directly, no wrapper indirection."""
    return 1
