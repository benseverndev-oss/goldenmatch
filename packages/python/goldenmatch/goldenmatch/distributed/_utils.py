"""Tiny helpers shared across distributed submodules."""
from __future__ import annotations
from typing import Any


def is_ray_dataset(obj: Any) -> bool:
    """Return True if obj is a ray.data.Dataset.

    Avoids importing ray at module load time -- duck-types on module name.
    """
    if obj is None:
        return False
    mod = type(obj).__module__
    return isinstance(mod, str) and mod.startswith("ray.data")
