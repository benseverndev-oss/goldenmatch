"""Learning Memory -- persistent corrections and rule learning."""
from goldenmatch.core.memory.corrections import (
    CorrectionStats,
    apply_corrections,
    build_row_lookup,
    compute_field_hash,
    compute_record_hash,
)
from goldenmatch.core.memory.learner import MemoryLearner
from goldenmatch.core.memory.store import Correction, LearnedAdjustment, MemoryStore

__all__ = [
    "MemoryStore", "Correction", "LearnedAdjustment",
    "apply_corrections", "CorrectionStats",
    "compute_field_hash", "compute_record_hash", "build_row_lookup",
    "MemoryLearner",
]
