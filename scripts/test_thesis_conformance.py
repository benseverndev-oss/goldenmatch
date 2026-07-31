"""Gate tests for the thesis-conformance T1 default-routing auditor (conformance v2,
0047 amendment). Verifies the harvester reads the real batteries-entry registration
and that the reconcile GATES the fs-default class (owner shipped opt-in while a second
impl is the default). Run: `python scripts/check_thesis_conformance.py --check`.
"""
from __future__ import annotations

import check_thesis_conformance as c


def test_real_repo_default_routing_ok():
    # The live goldenmatch batteries entry auto-registers the fs kernel, so the real
    # inventory reconciles clean and --check exits 0.
    card = c.build_scorecard()
    gating, _advisory = c.check_default_routing(card["inventory"], card["live"]["ts_default_routing"])
    assert gating == [], gating
    assert c.run_check(card, strict=False) == 0


def test_harvest_resolves_import_alias():
    # `import { enableFsWasmScoring as registerFsKernel }` + top-level `registerFsKernel()`
    # must resolve back to the exported symbol.
    harvest = c.harvest_ts_default_routing(c.load_inventory())
    ts = harvest["ts_batteries"]
    assert ts["exists"] is True
    assert "enableFsWasmScoring" in ts["auto_registered"]


def test_owner_default_not_registered_is_gating():
    # The fs-default class: a declared owner-default kernel that is NOT auto-registered
    # at the entry must HARD-fail (gating), independent of --strict.
    inv = {"default_routing": {"x": {"entry": "e", "owner_default": ["enableFooKernel"], "opt_in": []}}}
    harvest = {"x": {"entry": "e", "exists": True, "auto_registered": []}}  # not registered
    gating, advisory = c.check_default_routing(inv, harvest)
    assert any("REGRESSION" in g and "enableFooKernel" in g for g in gating)
    assert advisory == []


def test_optin_kernel_auto_registered_is_advisory():
    inv = {"default_routing": {"x": {"entry": "e", "owner_default": [], "opt_in": ["enableBarWasm"]}}}
    harvest = {"x": {"entry": "e", "exists": True, "auto_registered": ["enableBarWasm"]}}
    gating, advisory = c.check_default_routing(inv, harvest)
    assert gating == []
    assert any("opt_in" in a and "enableBarWasm" in a for a in advisory)


def test_unlisted_auto_registered_kernel_is_advisory():
    inv = {"default_routing": {"x": {"entry": "e", "owner_default": [], "opt_in": []}}}
    harvest = {"x": {"entry": "e", "exists": True, "auto_registered": ["enableNewWasm"]}}
    gating, advisory = c.check_default_routing(inv, harvest)
    assert gating == []
    assert any("absent from the" in a and "enableNewWasm" in a for a in advisory)
