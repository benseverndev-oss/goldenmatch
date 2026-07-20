"""Gate for the committed agent code map (docs/agent-codemap.json).

The map is a static-AST structural view of the Python source, committed so other
people's coding agents get it on clone. It must match a fresh walk (so a moved /
renamed / added module can't leave it stale) and be byte-deterministic.
Regenerate with: python scripts/agent_codemap.py --write
"""
from __future__ import annotations

from agent_codemap import CODEMAP_PATH, build_codemap, codemap_is_current, codemap_json


def test_codemap_current():
    assert codemap_is_current(), (
        f"{CODEMAP_PATH} is stale vs the Python source. "
        "Run: python scripts/agent_codemap.py --write"
    )


def test_codemap_deterministic():
    assert codemap_json() == codemap_json()


def test_codemap_covers_every_registry_package():
    m = build_codemap()
    assert set(m["packages"]) == {
        "goldenmatch", "goldencheck", "goldenflow", "goldenpipe", "infermap", "goldenanalysis"
    }
    # Every package resolved to real modules, and every entry carries a location.
    for name, p in m["packages"].items():
        assert p["module_count"] > 0, f"{name} mapped 0 modules"
        for mod in p["modules"]:
            assert mod["module"] and mod["file"]


def test_codemap_locates_a_known_symbol():
    # A concrete "don't grep" check: score_buckets is findable via the map.
    m = build_codemap()
    gm = m["packages"]["goldenmatch"]["modules"]
    hit = next((mod for mod in gm if "score_buckets" in mod.get("defines", [])), None)
    assert hit is not None, "score_buckets not located in the code map"
    assert hit["file"].endswith(".py")
    assert hit.get("imports"), "expected intra-repo import edges for the scorer module"
