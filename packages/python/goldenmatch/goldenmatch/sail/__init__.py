"""Deprecated alias for :mod:`goldenmatch.spark`.

The tier moved to ``goldenmatch.spark`` (2026-08-11) because it was never
Sail-specific: it is ``SparkSession.builder.remote(url)`` with zero
Sail-specific calls, and its target is now the Apache Spark cluster a customer
already operates. Sail is one Spark Connect server implementation; the name was
hiding the product from the people it is for. See
``docs/superpowers/specs/2026-08-10-spark-native-execution-design.md``.

This shim keeps ``import goldenmatch.sail`` working. It forwards every attribute
to ``goldenmatch.spark`` and warns once per process.
"""
from __future__ import annotations

import warnings
from typing import Any

_WARNED = False


def _warn_once() -> None:
    global _WARNED
    if _WARNED:
        return
    _WARNED = True
    warnings.warn(
        "goldenmatch.sail is deprecated; use goldenmatch.spark. The tier targets "
        "Apache Spark Connect (any server, including but not limited to Sail) and "
        "was renamed to say so. This alias forwards and will be removed in a "
        "future major.",
        DeprecationWarning,
        stacklevel=3,
    )


def __getattr__(name: str) -> Any:
    # PEP 562: forward submodules and attributes alike, so both
    # `from goldenmatch.sail import scorers` and
    # `import goldenmatch.sail.scorers` resolve.
    import importlib

    _warn_once()
    try:
        return getattr(importlib.import_module("goldenmatch.spark"), name)
    except AttributeError:
        return importlib.import_module(f"goldenmatch.spark.{name}")


def __dir__() -> list[str]:
    import importlib

    return dir(importlib.import_module("goldenmatch.spark"))
