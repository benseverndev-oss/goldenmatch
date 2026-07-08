from types import SimpleNamespace

from goldenpipe.compiler.e2e import surface_golden_provenance


def test_surface_passes_dupes_clusters_rules_and_returns_provenance(monkeypatch):
    calls = {}
    def fake(data_df, clusters, rules):
        calls["args"] = (data_df, clusters, rules)
        return ["PROV"]
    monkeypatch.setattr("goldenmatch.core.lineage.golden_provenance_for_run", fake)
    result = SimpleNamespace(dupes="DUPES_DF", config=SimpleNamespace(golden_rules="RULES"))
    out = surface_golden_provenance(result, {1: {"members": [0, 1], "size": 2}})
    assert out == ["PROV"]
    assert calls["args"] == ("DUPES_DF", {1: {"members": [0, 1], "size": 2}}, "RULES")


def test_surface_none_when_no_rules():
    result = SimpleNamespace(dupes="DUPES_DF", config=SimpleNamespace(golden_rules=None))
    assert surface_golden_provenance(result, {1: {"members": [0, 1]}}) is None


def test_surface_none_when_no_dupes():
    result = SimpleNamespace(dupes=None, config=SimpleNamespace(golden_rules="RULES"))
    assert surface_golden_provenance(result, {1: {"members": [0, 1]}}) is None


def test_surface_none_when_no_clusters():
    result = SimpleNamespace(dupes="DF", config=SimpleNamespace(golden_rules="RULES"))
    assert surface_golden_provenance(result, None) is None


def test_surface_fail_open_on_error(monkeypatch):
    def boom(*a):
        raise RuntimeError("x")
    monkeypatch.setattr("goldenmatch.core.lineage.golden_provenance_for_run", boom)
    result = SimpleNamespace(dupes="DF", config=SimpleNamespace(golden_rules="RULES"))
    assert surface_golden_provenance(result, {1: {"members": [0, 1]}}) is None
