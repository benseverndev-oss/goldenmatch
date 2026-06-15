"""R3 feature-gate: the Sail pipeline fails LOUDLY on unsupported config
instead of silently degrading (the scale-mode posture).

Pure-Python -- `_validate_sail_pipeline_supported` touches no Spark, so this
runs in every lane (no `sail` extra needed). Covers the two real silent-degrade
cases: an unsupported scorer (would error deep in the UDF on a worker) and an
unrecognized WCC (would silently route to label-prop)."""
from __future__ import annotations

import pytest
from goldenmatch.sail.pipeline import (
    _SUPPORTED_WCC,
    _validate_sail_pipeline_supported,
)
from goldenmatch.sail.scorers import _SUPPORTED as _SUPPORTED_SCORERS


def test_supported_config_passes():
    """Every supported scorer x WCC combination validates without raising."""
    for scorer in _SUPPORTED_SCORERS:
        for wcc in _SUPPORTED_WCC:
            _validate_sail_pipeline_supported(scorer_name=scorer, wcc=wcc)


@pytest.mark.parametrize(
    "scorer", ["embedding", "record_embedding", "llm", "rerank", "boost", "dice", "qgram"]
)
def test_unsupported_scorer_raises(scorer):
    """LLM / embedding / rerank / boost / PPRL scorers don't distribute on Sail."""
    with pytest.raises(NotImplementedError):
        _validate_sail_pipeline_supported(scorer_name=scorer, wcc="scale")


@pytest.mark.parametrize("wcc", ["banana", "labelprop", "two_phase", ""])
def test_unrecognized_wcc_raises(wcc):
    """An unrecognized WCC must error, not silently degrade to label-prop."""
    with pytest.raises(ValueError):
        _validate_sail_pipeline_supported(scorer_name="jaro_winkler", wcc=wcc)
