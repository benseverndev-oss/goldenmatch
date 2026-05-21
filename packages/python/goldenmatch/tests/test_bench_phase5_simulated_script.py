"""CLI smoke test for the Phase 5 simulated bench driver script."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_bench_script_has_expected_cli_surface() -> None:
    """--help must list --parquet, --identity, --out, --rows."""
    package_root = Path(__file__).resolve().parent.parent
    script = package_root / "scripts" / "bench_phase5_simulated.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        cwd=str(package_root),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--parquet" in result.stdout
    assert "--identity" in result.stdout
    assert "--out" in result.stdout
    assert "--rows" in result.stdout
