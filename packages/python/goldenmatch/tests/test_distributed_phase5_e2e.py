"""Phase 5 end-to-end single-node Ray correctness test.

All tests gated on `ray` being importable. The fixture is kept small
(200 rows) so the controller's autoconfig iterations complete inside
the 120s CI timeout. The wiring verification is the goal; real
multi-node bench at 100M is the bench-phase5-end2end workflow only.
"""
import pathlib

import pytest

ray = pytest.importorskip("ray")


def test_phase5_pipeline_end_to_end_correctness(tmp_path, monkeypatch):
    """200-row fixture: Phase 5 pipeline runs all stages and produces a result.

    Synthesized data has 40 clusters of 5 members each. Asserts:
      - run completes without error
      - result has a clusters attribute
      - output path written when provided (if clusters form)
    """
    import polars as pl
    from goldenmatch.distributed import read_csv_partitioned
    from goldenmatch.distributed.pipeline import run_dedupe_pipeline_distributed

    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_PIPELINE", "2")
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

    # 200 rows, 40 clusters of 5 members each.
    # Kept small so the controller autoconfig completes well within 120s.
    # The wiring verification is the goal here — scale is the bench job's job.
    rows_per_cluster = 5
    n_clusters = 40
    rows = []
    for cid in range(n_clusters):
        for _ in range(rows_per_cluster):
            rows.append({
                "first_name": f"name_{cid}",
                "last_name": f"sur_{cid}",
            })
    csv = tmp_path / "in.csv"
    pl.DataFrame(rows).write_csv(csv)

    ds = read_csv_partitioned(str(csv), n_partitions=4)
    output_path = str(tmp_path / "out.parquet")
    result = run_dedupe_pipeline_distributed(
        ds, confidence_required=False, output_path=output_path,
    )

    assert result is not None
    # The function returns a DedupeResult with a clusters attribute.
    assert hasattr(result, "clusters")

    # Output file written when clusters formed.
    out_p = pathlib.Path(output_path)
    if result.clusters:
        # If clusters formed the output should be on disk.
        assert out_p.exists() or any(out_p.parent.glob("*.parquet")), (
            "clusters formed but output parquet not found"
        )
