"""D1/C2/D2 surfacing tests: shared golden_provenance_for_run helper +
CLI explain/lineage + MCP lineage-tool threading.

Kept deliberately tiny (<=3-row datasets, distinct deterministic values) per
the engine's blocking-key hang risk on synthetic surnames.
"""
from __future__ import annotations

import json

import polars as pl
from goldenmatch.config.schemas import GoldenGroupRule, GoldenRulesConfig
from goldenmatch.core.lineage import golden_provenance_for_run


def test_helper_builds_groups_for_survivorship_config():
    data = pl.DataFrame({"__row_id__": [10, 11, 99], "street": ["1 Main St", "1 Main", "X"],
                         "city": ["LA", "LA", "NY"], "zip": [None, "90001", "10001"]})
    clusters = {5: {"members": [10, 11], "size": 2}, 7: {"members": [99], "size": 1}}
    rules = GoldenRulesConfig(default_strategy="most_complete",
                              field_groups=[GoldenGroupRule(name="addr", columns=["street", "city", "zip"])])
    provs = golden_provenance_for_run(data, clusters, rules)
    assert provs is not None
    cp = next(p for p in provs if p.cluster_id == 5)
    assert cp.groups[0].name == "addr"


def test_helper_none_when_no_multimember_clusters():
    data = pl.DataFrame({"__row_id__": [99], "street": ["X"]})
    clusters = {7: {"members": [99], "size": 1}}
    rules = GoldenRulesConfig(default_strategy="most_complete")
    assert golden_provenance_for_run(data, clusters, rules) is None


def test_tool_lineage_inline_has_golden_records(monkeypatch):
    from types import SimpleNamespace

    import goldenmatch.mcp.server as server
    from goldenmatch.config.schemas import GoldenGroupRule, GoldenRulesConfig

    data = pl.DataFrame({"__row_id__": [10, 11], "street": ["1 Main St", "1 Main"],
                         "city": ["LA", "LA"], "zip": [None, "90001"]})
    rules = GoldenRulesConfig(default_strategy="most_complete",
                              field_groups=[GoldenGroupRule(name="addr", columns=["street", "city", "zip"])])
    monkeypatch.setattr(server, "_engine", SimpleNamespace(data=data), raising=False)
    monkeypatch.setattr(server, "_result", SimpleNamespace(scored_pairs=[(10, 11, 0.99)],
                                                           clusters={5: {"members": [10, 11], "size": 2}}), raising=False)
    monkeypatch.setattr(server, "_config", SimpleNamespace(golden_rules=rules, get_matchkeys=lambda: []), raising=False)
    out = server._tool_lineage(max_pairs=10, natural_language=False, output_dir=None)
    assert "golden_records" in out
    assert out["golden_records"][0]["groups"][0]["name"] == "addr"


# ── C2 + D2a: tiny CliRunner round-trip (deterministic, 3 rows) ─────────────

from goldenmatch.cli.main import app  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

runner = CliRunner()

# Rows 0 and 1 share an exact email -> they cluster together; row 2 is distinct.
# The address field_group exercises the survivorship audit block.
_CFG = (
    "matchkeys:\n"
    "  - name: email_exact\n"
    "    type: exact\n"
    "    fields:\n"
    "      - field: email\n"
    "        transforms: [lowercase, strip]\n"
    "golden_rules:\n"
    "  default_strategy: most_complete\n"
    "  field_groups:\n"
    "    - name: addr\n"
    "      columns: [street, city, zip]\n"
)


def _dataset(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text(
        "id,email,street,city,zip\n"
        "1,a@x.com,1 Main St,LA,\n"
        "2,a@x.com,1 Main,LA,90001\n"
        "3,bob@test.com,9 Oak Ave,NY,10001\n"
    )
    return csv


def _cfg(tmp_path):
    cfg = tmp_path / "gm.yml"
    cfg.write_text(_CFG)
    return cfg


def test_explain_cluster_shows_survivorship(tmp_path):
    csv = _dataset(tmp_path)
    cfg = _cfg(tmp_path)
    # Rows 0 and 1 share an email -> cluster id 0 (first multi-member cluster).
    result = runner.invoke(
        app, ["explain", str(csv), "-c", str(cfg), "--cluster", "0"]
    )
    assert result.exit_code == 0, result.stdout
    assert "Survivorship:" in result.stdout
    assert "promoted together" in result.stdout


def test_lineage_output_has_golden_records_with_groups(tmp_path):
    csv = _dataset(tmp_path)
    cfg = _cfg(tmp_path)
    outdir = tmp_path / "out"
    outdir.mkdir()
    result = runner.invoke(
        app, ["lineage", str(csv), "-c", str(cfg), "-o", str(outdir)]
    )
    assert result.exit_code == 0, result.stdout
    files = list(outdir.glob("*_lineage.json"))
    assert files
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert "golden_records" in data
    groups = data["golden_records"][0]["groups"]
    assert isinstance(groups, list)
    assert groups[0]["name"] == "addr"
