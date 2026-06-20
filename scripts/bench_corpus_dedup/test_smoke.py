import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _env():
    return {**os.environ, "PYTHONPATH": "packages/python/goldenmatch",
            "GOLDENMATCH_NATIVE": "0", "POLARS_SKIP_CPU_CHECK": "1",
            "PYTHONIOENCODING": "utf-8"}


def test_offline_smoke_goldenmatch_only(tmp_path):
    rc = subprocess.run(
        [sys.executable, str(HERE / "orchestrate.py"),
         "--corpus", "offline", "--scales", "200", "--engines", "goldenmatch",
         "--seed", "0", "--workdir", str(tmp_path)],
        env=_env(),
    ).returncode
    assert rc == 0
    results = json.loads((tmp_path / "bench_results.json").read_text())
    assert results and results[0]["engine"] == "goldenmatch"
    assert results[0]["status"] == "ok"
    assert "accuracy" in results[0]
    assert results[0]["accuracy"]["pairwise"]["recall"] >= 0.0
    summary = (tmp_path / "summary.md").read_text()
    assert "docs/sec" in summary and "MB/sec" in summary
