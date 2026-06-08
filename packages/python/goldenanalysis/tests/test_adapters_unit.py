"""Suite adapters (pure — duck-typed producer stand-ins, no suite imports)."""

from __future__ import annotations

from types import SimpleNamespace

import polars as pl
from goldenanalysis.adapters.check import CheckArtifactAdapter
from goldenanalysis.adapters.flow import FlowArtifactAdapter
from goldenanalysis.adapters.match import MatchArtifactAdapter
from goldenanalysis.adapters.pipe import PipeArtifactAdapter


def test_match_adapter() -> None:
    result = SimpleNamespace(
        clusters={0: {"members": [0], "size": 1}},
        scored_pairs=[(0, 1, 0.9)],
        stats={"total_records": 2, "match_rate": 0.5},
        config=None,
    )
    inp = MatchArtifactAdapter().load(
        result, dataset="customers", certificate={"estimate": 0.94, "safe_bound": 0.89}
    )
    assert inp.dataset == "customers"
    assert inp.artifacts["__producer__"] == "goldenmatch"
    assert inp.artifacts["clusters"] == result.clusters
    assert inp.artifacts["scored_pairs"] == result.scored_pairs
    assert inp.artifacts["match_stats"] == result.stats
    assert inp.artifacts["recall_certificate"] == {"estimate": 0.94, "safe_bound": 0.89}


def test_match_adapter_reads_result_certificate() -> None:
    # Producer-attached RecallEstimate (dedupe_df certify=True) is normalized.
    cert = SimpleNamespace(recall=0.94, recall_lower=None)
    result = SimpleNamespace(clusters={}, scored_pairs=[], stats={}, config=None, recall_certificate=cert)
    inp = MatchArtifactAdapter().load(result)
    assert inp.artifacts["recall_certificate"] == {"estimate": 0.94, "safe_bound": None}


def test_flow_adapter() -> None:
    df = pl.DataFrame({"a": [1]})
    manifest = SimpleNamespace(records=[])
    inp = FlowArtifactAdapter().load(SimpleNamespace(df=df, manifest=manifest), dataset="d")
    assert inp.artifacts["__producer__"] == "goldenflow"
    assert inp.artifacts["manifest"] is manifest
    assert inp.frame is df


def test_check_adapter_from_scan_is_pure() -> None:
    inp = CheckArtifactAdapter().from_scan(findings=[{"check": "x"}], profile=None, dataset="d")
    assert inp.artifacts["__producer__"] == "goldencheck"
    assert inp.artifacts["findings"] == [{"check": "x"}]
    assert inp.artifacts["profile"] is None


def test_pipe_adapter_passthrough_and_dataset() -> None:
    cert = SimpleNamespace(recall=0.94, recall_lower=0.89)
    result = SimpleNamespace(
        artifacts={
            "clusters": {0: {"members": [0], "size": 1}},
            "scored_pairs": [(0, 1, 0.9)],
            "match_stats": {"match_rate": 0.5},
            "findings": [{"check": "x", "column": "a", "severity": "WARNING"}],
            "manifest": SimpleNamespace(records=[]),
            "recall_certificate": cert,
        },
        source="customers.parquet",
        input_rows=4000,
    )
    inp = PipeArtifactAdapter().load(result)
    assert inp.dataset == "customers"
    assert inp.artifacts["__producer__"] == "goldenpipe"
    assert inp.artifacts["clusters"] == result.artifacts["clusters"]
    assert inp.artifacts["recall_certificate"] == {"estimate": 0.94, "safe_bound": 0.89}


def test_pipe_adapter_dataframe_source() -> None:
    inp = PipeArtifactAdapter().load(SimpleNamespace(artifacts={}, source="<DataFrame>"))
    assert inp.dataset == "frame"
