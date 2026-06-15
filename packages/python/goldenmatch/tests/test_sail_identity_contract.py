"""Stable public IdentityGraph API contract (#859).

These tests pin the public surface a downstream store (e.g. the golden-showcase
``IdentityGraph`` seam) depends on, and they run in the NORMAL python lane: the
public API imports without the ``[sail]`` extra because ``pyspark`` is imported
lazily inside the builder bodies. If this file needs ``importorskip``, the
contract has regressed (the import surface started pulling pyspark eagerly).

Freezing this signature is the point: a change here is a breaking change for any
consumer pinning the contract, and should bump the API accordingly.
"""
from __future__ import annotations

import dataclasses
import inspect


def test_public_import_path_no_sail_extra():
    # The stable path. Must import without pyspark / the [sail] extra installed.
    from goldenmatch.sail import (  # noqa: F401
        EDGE_COLUMNS,
        EVENT_COLUMNS,
        NODE_COLUMNS,
        RECORD_COLUMNS,
        IdentityGraphFrames,
        build_identity_graph,
    )


def test_identity_graph_frames_shape():
    from goldenmatch.sail import IdentityGraphFrames

    assert dataclasses.is_dataclass(IdentityGraphFrames)
    fields = {f.name: f for f in dataclasses.fields(IdentityGraphFrames)}
    # The frozen field set: nodes / records / edges + an OPTIONAL events frame.
    assert list(fields) == ["nodes", "records", "edges", "events"]
    # events is optional (defaults to None) so existing 3-arg construction holds.
    assert fields["events"].default is None
    frames = IdentityGraphFrames(nodes="n", records="r", edges="e")
    assert frames.events is None


def test_build_identity_graph_signature():
    from goldenmatch.sail import build_identity_graph

    sig = inspect.signature(build_identity_graph)
    params = sig.parameters
    # Positional inputs the consumer always passes.
    for name in ("pairs", "assignments", "source_df", "golden_df"):
        assert name in params
    # Keyword-only contract knobs + their stable defaults.
    assert params["run_meta"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["source_col"].default == "__source__"
    assert params["source_pk_col"].default is None
    assert params["id_col"].default == "__row_id__"
    assert params["with_events"].default is True


def test_frozen_wire_schema():
    from goldenmatch.sail import (
        EDGE_COLUMNS,
        EVENT_COLUMNS,
        NODE_COLUMNS,
        RECORD_COLUMNS,
    )

    assert NODE_COLUMNS == (
        "entity_id", "status", "merged_into", "golden_record",
        "confidence", "dataset", "created_at", "updated_at",
    )
    assert RECORD_COLUMNS == (
        "record_id", "entity_id", "dataset", "first_seen_at", "last_seen_at",
    )
    # Edge provenance: endpoints + score + the matchkey that fired + the run id.
    assert EDGE_COLUMNS == (
        "entity_id", "record_a_id", "record_b_id", "kind",
        "score", "matchkey_name", "run_name", "dataset", "recorded_at",
    )
    for col in ("record_a_id", "record_b_id", "score", "matchkey_name", "run_name"):
        assert col in EDGE_COLUMNS
    assert EVENT_COLUMNS == ("entity_id", "kind", "run_name", "dataset", "recorded_at")
