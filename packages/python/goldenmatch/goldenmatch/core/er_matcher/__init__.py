"""OSS ER-matcher LLM boost (spec/plan 2026-07-26).

Runtime pieces for the offline, fine-tuned record-pair matcher. `prompt` is the
versioned serializer / rubric / verdict-parser shared by training-data
generation, inference, and eval.
"""
from goldenmatch.core.er_matcher.prompt import (
    SERIALIZER_VERSION,
    SYSTEM_RUBRIC,
    build_chat,
    parse_verdict,
    render_target,
    serialize_pair_v1,
)

__all__ = [
    "SERIALIZER_VERSION",
    "SYSTEM_RUBRIC",
    "build_chat",
    "parse_verdict",
    "render_target",
    "serialize_pair_v1",
]
