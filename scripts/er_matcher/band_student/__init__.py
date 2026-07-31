"""Offline harness for the distilled band-override scorer (spec/plan 2026-07-31).

We (goldenmatch) train the student centrally from an LLM teacher's band labels and
ship it pinned; users consume it zero-config, fully local. This package is the
OFFLINE training + gate-evaluation harness (not the shipped runtime).
"""
from .evaluate import (
    FidelityResult,
    OverrideResult,
    distillation_fidelity,
    end_to_end_override,
)
from .features import band_features, feature_names, field_features
from .student import BandStudent

__all__ = [
    "band_features",
    "feature_names",
    "field_features",
    "BandStudent",
    "distillation_fidelity",
    "end_to_end_override",
    "FidelityResult",
    "OverrideResult",
]
