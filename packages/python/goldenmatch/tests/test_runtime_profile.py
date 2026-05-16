"""Unit tests for RuntimeProfile.

Spec §Signals -- captures machine-level signals the planner consumes:
available RAM, CPU count, disk free. Read once at controller-start; not
mutated.
"""
from __future__ import annotations

from goldenmatch.core.runtime_profile import RuntimeProfile, capture_runtime_profile


def test_runtime_profile_is_frozen_dataclass():
    p = RuntimeProfile(available_ram_gb=16.0, cpu_count=4, disk_free_gb=100.0)
    import dataclasses
    assert dataclasses.is_dataclass(p)
    try:
        p.available_ram_gb = 32.0  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("RuntimeProfile should be frozen")


def test_capture_runtime_profile_returns_positive_values():
    p = capture_runtime_profile()
    assert p.available_ram_gb > 0
    assert p.cpu_count >= 1
    assert p.disk_free_gb > 0


def test_capture_runtime_profile_ram_below_total():
    """available_ram_gb should be <= total system RAM (sanity check)."""
    import psutil
    p = capture_runtime_profile()
    total_gb = psutil.virtual_memory().total / (1024 ** 3)
    assert p.available_ram_gb <= total_gb + 0.1  # +0.1 GB slop
