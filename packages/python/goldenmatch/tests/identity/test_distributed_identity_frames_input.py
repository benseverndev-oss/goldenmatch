"""Phase 2b: ``resolve_identities_distributed`` accepts ``ClusterFrames``.

GH issue #624 (Arrow-native roadmap Phase 2).

Asserts that the dispatcher detects ``ClusterFrames`` input and calls
the ``cluster_frames_to_dict`` adapter before going downstream. The
downstream Postgres path is not exercised (that's the
test_db / identity-postgres CI lane); we patch ``cluster_frames_to_dict``
with a sentinel side-effect and verify the call shape.

Phase 5 will lift the resolver to consume ``ClusterFrames`` directly;
this test will need to be updated then to assert the no-conversion
path.
"""
from __future__ import annotations

from unittest.mock import patch

import polars as pl
import pytest
from goldenmatch.core.cluster import ClusterFrames


def _make_frames() -> ClusterFrames:
    """Minimal 2-cluster ClusterFrames."""
    assignments = pl.DataFrame({
        "cluster_id": pl.Series([1, 1, 2, 2], dtype=pl.Int64),
        "member_id":  pl.Series([10, 11, 20, 21], dtype=pl.Int64),
    })
    metadata = pl.DataFrame({
        "cluster_id": pl.Series([1, 2], dtype=pl.Int64),
        "size":       pl.Series([2, 2], dtype=pl.Int64),
        "confidence": pl.Series([0.9, 0.85], dtype=pl.Float64),
        "quality":    pl.Series(["strong", "strong"], dtype=pl.Utf8),
        "oversized":  pl.Series([False, False], dtype=pl.Boolean),
        "bottleneck_pair_a": pl.Series([10, 20], dtype=pl.Int64),
        "bottleneck_pair_b": pl.Series([11, 21], dtype=pl.Int64),
    })
    return ClusterFrames(assignments=assignments, metadata=metadata)


# Sentinel exception we use to abort the dispatcher AFTER the
# cluster_frames_to_dict call but BEFORE the downstream Postgres path.
class _AbortAfterAdapter(Exception):
    pass


class TestClusterFramesInputDispatch:
    def test_frames_input_invokes_dict_adapter(self):
        """When ClusterFrames is passed, the dispatcher should call
        ``cluster_frames_to_dict`` -- verified by patching that
        function with a tracker that raises after capturing input."""
        frames = _make_frames()
        df = pl.DataFrame({
            "__row_id__": [10, 11, 20, 21],
            "name": ["a", "b", "c", "d"],
        })
        captured: list = []

        def _trap(arg):
            captured.append(arg)
            # Abort before the dispatcher reaches the Postgres path
            # (which would require a live DSN + connection).
            raise _AbortAfterAdapter("captured")

        with patch(
            "goldenmatch.core.cluster.cluster_frames_to_dict",
            side_effect=_trap,
        ):
            from goldenmatch.distributed.identity import (
                resolve_identities_distributed,
            )
            with pytest.raises(_AbortAfterAdapter):
                resolve_identities_distributed(
                    clusters=frames,
                    df=df,
                    scored_pairs=[],
                    matchkey_name=None,
                    dsn="postgresql://fake/db",
                    run_name="test-run",
                )

        assert len(captured) == 1, (
            "cluster_frames_to_dict was not called exactly once on "
            "ClusterFrames input"
        )
        assert captured[0] is frames, (
            "the adapter received an object other than the ClusterFrames "
            "we passed in"
        )

    def test_dict_input_skips_adapter(self):
        """Existing dict callers must NOT trigger the
        ``cluster_frames_to_dict`` adapter."""
        clusters_dict = {
            1: {"members": [10, 11], "size": 2, "confidence": 0.9,
                "cluster_quality": "strong", "oversized": False,
                "bottleneck_pair": (10, 11), "pair_scores": {}},
        }
        df = pl.DataFrame({"__row_id__": [10, 11]})
        adapter_called: list = []

        def _trap(arg):
            adapter_called.append(arg)
            raise _AbortAfterAdapter()

        # Patch ``get_identity_pool`` to fail fast -- we only care
        # about whether the adapter was invoked before reaching the
        # pool, not about the pool's behavior. Without this patch,
        # ``ConnectionPool`` tries the (fake) DSN for ~30s.
        class _PoolUnreachable(Exception):
            pass

        with patch(
            "goldenmatch.core.cluster.cluster_frames_to_dict",
            side_effect=_trap,
        ), patch(
            "goldenmatch.identity.pool.get_identity_pool",
            side_effect=_PoolUnreachable,
        ):
            from goldenmatch.distributed.identity import (
                resolve_identities_distributed,
            )
            with pytest.raises(Exception) as excinfo:
                resolve_identities_distributed(
                    clusters=clusters_dict,
                    df=df,
                    scored_pairs=[],
                    matchkey_name=None,
                    dsn="postgresql://fake/db",
                    run_name="test-run",
                )

        assert not isinstance(excinfo.value, _AbortAfterAdapter), (
            "dict input incorrectly went through cluster_frames_to_dict; "
            "existing callers would see a behavior change"
        )
        assert adapter_called == [], (
            "cluster_frames_to_dict was called on dict input"
        )
