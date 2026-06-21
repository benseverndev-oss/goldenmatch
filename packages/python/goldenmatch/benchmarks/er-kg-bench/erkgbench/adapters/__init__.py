"""Adapters: the goldenmatch system under test + modelled framework defaults."""

from .base import Adapter, Record
from .goldengraph_adapter import GoldenGraphAdapter
from .goldenmatch_adapter import GoldenMatchAdapter, GoldenMatchEmbAnnAdapter
from .modeled import all_modeled

__all__ = [
    "Adapter",
    "Record",
    "GoldenGraphAdapter",
    "GoldenMatchAdapter",
    "GoldenMatchEmbAnnAdapter",
    "all_modeled",
]
