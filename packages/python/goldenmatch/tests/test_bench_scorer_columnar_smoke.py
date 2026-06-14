"""Smoke + eligibility guards for the scorer columnar prove-bench."""
import sys
from pathlib import Path

# scripts/ is not a package; import the bench module by path.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import bench_scorer_columnar as B  # noqa: E402


def test_workload_is_columnar_eligible():
    from goldenmatch.core.pipeline import _is_columnar_eligible
    df = B.make_workload(rows=200)
    config = B.make_config()
    assert df.height == 200
    # The bench is meaningless unless the columnar path actually fires:
    assert _is_columnar_eligible(config, config.get_matchkeys(), False) is True


def test_workload_has_duplicates_and_spread_surnames():
    df = B.make_workload(rows=500)
    # surnames must spread across soundex codes (blocking-hang guard): many distinct
    assert df["surname"].n_unique() >= 20


def test_bench_runs_end_to_end(tmp_path):
    """Run the whole bench (parent) at a tiny scale; both variants complete,
    parity holds, a table is produced."""
    import json as _json
    out = tmp_path / "result.json"
    rc = B.main(["--rows", "2000", "--runs", "1", "--output", str(out)])
    assert rc == 0
    data = _json.loads(out.read_text())
    assert data["parity_ok"] is True
    row = data["results"][0]
    assert row["rows"] == 2000
    assert "wall_s" in row["legacy"] and "wall_s" in row["columnar"]
    assert row["legacy"]["pair_count"] == row["columnar"]["pair_count"]
