"""Multi-source ER data pipeline: PairSource contract + registry."""

from __future__ import annotations

from sources.base import PairSource, Row, get_source, iter_sources, register

__all__ = ["Row", "PairSource", "register", "get_source", "iter_sources"]
