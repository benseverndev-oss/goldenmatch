"""Synthetic fixture: four claims -- enforced, unenforced, prose-only, unresolvable."""


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
