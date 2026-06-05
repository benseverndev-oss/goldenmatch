"""Wave 3.1: library features surfaced as CLI commands.

- anomalies: standalone suspicious-record detection (was dedupe --anomalies only)
- lineage: per-pair audit trail (was MCP-tool-only)
- explain: NL pair/cluster explanation (had no CLI front door)
"""
from __future__ import annotations

from goldenmatch.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()

_EXACT_EMAIL_CFG = (
    "matchkeys:\n"
    "  - name: email_exact\n"
    "    type: exact\n"
    "    fields:\n"
    "      - field: email\n"
    "        transforms: [lowercase, strip]\n"
)


def _dataset(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text(
        "id,email,name\n"
        "1,a@x.com,Alice\n"
        "2,a@x.com,Alicia\n"
        "3,bob@test.com,Bob\n"
    )
    return csv


def _cfg(tmp_path):
    cfg = tmp_path / "gm.yml"
    cfg.write_text(_EXACT_EMAIL_CFG)
    return cfg


class TestAnomalies:
    def test_help(self):
        result = runner.invoke(app, ["anomalies", "--help"])
        assert result.exit_code == 0

    def test_detects_fake_email_and_writes_output(self, tmp_path):
        csv = tmp_path / "data.csv"
        csv.write_text("id,email\n1,real@company.com\n2,test@test.com\n")
        out = tmp_path / "anom.csv"
        result = runner.invoke(
            app, ["anomalies", str(csv), "--sensitivity", "high", "-o", str(out)]
        )
        assert result.exit_code == 0, result.stdout
        assert out.exists()
        import polars as pl

        df = pl.read_csv(out)
        assert df.height >= 1
        assert "test@test.com" in set(df["value"].to_list())

    def test_rejects_bad_sensitivity(self, tmp_path):
        csv = _dataset(tmp_path)
        result = runner.invoke(app, ["anomalies", str(csv), "--sensitivity", "bogus"])
        assert result.exit_code == 2


class TestLineage:
    def test_help(self):
        result = runner.invoke(app, ["lineage", "--help"])
        assert result.exit_code == 0

    def test_builds_and_writes_lineage(self, tmp_path):
        csv = _dataset(tmp_path)
        cfg = _cfg(tmp_path)
        outdir = tmp_path / "out"
        outdir.mkdir()
        result = runner.invoke(
            app, ["lineage", str(csv), "-c", str(cfg), "-o", str(outdir), "--nl"]
        )
        assert result.exit_code == 0, result.stdout
        assert list(outdir.glob("*lineage*.json"))


class TestExplain:
    def test_requires_exactly_one_target(self, tmp_path):
        csv = _dataset(tmp_path)
        cfg = _cfg(tmp_path)
        # neither --pair nor --cluster
        r1 = runner.invoke(app, ["explain", str(csv), "-c", str(cfg)])
        assert r1.exit_code == 2
        # both
        r2 = runner.invoke(
            app, ["explain", str(csv), "-c", str(cfg), "--pair", "0,1", "--cluster", "0"]
        )
        assert r2.exit_code == 2

    def test_explain_pair(self, tmp_path):
        csv = _dataset(tmp_path)
        cfg = _cfg(tmp_path)
        result = runner.invoke(
            app, ["explain", str(csv), "-c", str(cfg), "--pair", "0,1"]
        )
        assert result.exit_code == 0, result.stdout
        # rows 0 and 1 share an email -> a real explanation panel
        assert "0" in result.stdout and "1" in result.stdout
