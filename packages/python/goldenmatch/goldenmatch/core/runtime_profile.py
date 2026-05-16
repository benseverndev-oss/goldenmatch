"""Runtime-introspection signals consumed by the controller-v3 planner.

Captured once at controller-start; not mutated. Spec §Signals:
docs/superpowers/specs/2026-05-15-controller-v3-planner-design.md.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeProfile:
    """Machine-level signals available at controller-start.

    Attributes:
        available_ram_gb: ``psutil.virtual_memory().available`` in gigabytes.
        cpu_count: ``os.cpu_count()``; fall back to 1 if None.
        disk_free_gb: ``shutil.disk_usage(cwd).free`` in gigabytes; used
            for spill-threshold sanity checks.
    """

    available_ram_gb: float
    cpu_count: int
    disk_free_gb: float


def capture_runtime_profile() -> RuntimeProfile:
    """Snapshot the current process's runtime context.

    Called at the end of ``AutoConfigController.run`` before the planner
    rules fire. Cheap (psutil + shutil.disk_usage); no caching needed.
    """
    import shutil

    import psutil

    vm = psutil.virtual_memory()
    available_ram_gb = vm.available / (1024 ** 3)
    cpu_count = os.cpu_count() or 1
    du = shutil.disk_usage(os.getcwd())
    disk_free_gb = du.free / (1024 ** 3)
    return RuntimeProfile(
        available_ram_gb=available_ram_gb,
        cpu_count=cpu_count,
        disk_free_gb=disk_free_gb,
    )
