"""Adapters: the goldenmatch system under test + modelled framework defaults."""

from .base import Adapter, Record
from .goldenmatch_adapter import GoldenMatchAdapter, GoldenMatchEmbAnnAdapter
from .modeled import all_modeled

__all__ = [
    "Adapter",
    "Record",
    "GoldenMatchAdapter",
    "GoldenMatchEmbAnnAdapter",
    "all_modeled",
]
