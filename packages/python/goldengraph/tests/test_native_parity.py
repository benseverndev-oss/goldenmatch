"""Cross-surface parity: the Python ``goldengraph._native`` engine must reproduce
the shared oracle on all 7 graph/query/store ops.

This is the Python mirror of the TS ``goldengraph-wasm.parity.test.ts``: it reads
the SAME generated fixture (``queries.json``, authored from the host boundary by
``goldengraph-wasm/examples/gen_parity_fixtures.rs`` and drift-guarded by the
``fixture_drift`` CI job), runs each case through the native JSON-boundary
function, and asserts byte/structure equality with ``expected``. Because native /
wasm / cabi all marshal the SAME ``goldengraph-core`` over the SAME ``serde_json``
boundary, the three surfaces are identical by construction; this test is what
proves it for the Python surface.

Run under the CI ``goldengraph_native`` lane with ``GOLDENGRAPH_NATIVE=1`` (require
native). Locally: build the ext (``python scripts/build_goldengraph_native.py``)
then ``pytest packages/python/goldengraph/tests/test_native_parity.py``.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

# Anchor to the repo root from this file so the path resolves whether pytest runs
# with CWD=package dir (local) or CWD=repo root (CI).
_REPO = Path(__file__).resolve().parents[4]
_FIXTURE = (
    _REPO
    / "packages"
    / "typescript"
    / "goldengraph"
    / "tests"
    / "parity"
    / "fixtures"
    / "goldengraph"
    / "queries.json"
)
_SO = _REPO / "packages" / "python" / "goldengraph" / "goldengraph" / "_native.abi3.so"


def _load_native():
    """Import the native engine WITHOUT triggering ``goldengraph/__init__`` (which
    pulls heavy deps like numpy). Prefer the in-tree ``.so``; else the wheel."""
    require = os.environ.get("GOLDENGRAPH_NATIVE") == "1"
    if _SO.exists():
        # The abi3 init symbol is ``PyInit__native`` (pymodule name ``_native``),
        # so the module MUST be loaded under the name ``_native``.
        spec = importlib.util.spec_from_file_location("_native", _SO)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    try:
        from goldengraph_native import _native  # pyright: ignore[reportMissingImports]

        return _native
    except Exception:  # noqa: BLE001
        if require:
            raise
        pytest.skip("goldengraph._native not built (set GOLDENGRAPH_NATIVE=1 to require)")


_NATIVE = _load_native()
_CASES = json.loads(_FIXTURE.read_text())["cases"]


def _canon_graph(g: dict) -> dict:
    """Order-independent graph compare (mirror the Rust/TS canonicalizer)."""
    return {
        "entities": sorted(
            (
                {
                    **e,
                    "members": sorted(e["members"]),
                    "surface_names": sorted(e["surface_names"]),
                    "source_refs": sorted(e.get("source_refs", [])),
                }
                for e in g["entities"]
            ),
            key=lambda e: e["entity_id"],
        ),
        "edges": sorted(
            ({**e, "source_refs": sorted(e["source_refs"])} for e in g["edges"]),
            key=lambda e: (e["subj"], e["predicate"], e["obj"]),
        ),
    }


def _snap_arg(v) -> str:
    """A snapshot arg is either "" (fresh) or a snapshot object -> its JSON."""
    return "" if v == "" else json.dumps(v)


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_native_matches_oracle(case: dict) -> None:
    fn = case["fn"]
    a = case["args"]
    exp = case["expected"]

    if fn == "build_graph":
        got = json.loads(
            _NATIVE.build_graph_json(
                json.dumps(a["mentions"]), json.dumps(a["edges"]), json.dumps(a["resolution"])
            )
        )
        assert _canon_graph(got) == _canon_graph(exp)
    elif fn == "neighborhood":
        got = json.loads(
            _NATIVE.neighborhood_json(json.dumps(a["graph"]), json.dumps(a["seeds"]), a["hops"])
        )
        assert _canon_graph(got) == _canon_graph(exp)
    elif fn == "seeds_by_name":
        got = json.loads(_NATIVE.seeds_by_name_json(json.dumps(a["graph"]), a["name"]))
        assert sorted(got) == exp
    elif fn == "communities":
        got = json.loads(_NATIVE.communities_json(json.dumps(a["graph"])))
        assert got == exp
    elif fn == "store_append":
        got = json.loads(_NATIVE.store_append_json(_snap_arg(a["snapshot"]), json.dumps(a["batch"])))
        assert got == exp
    elif fn == "store_as_of":
        got = json.loads(
            _NATIVE.store_as_of_json(json.dumps(a["snapshot"]), a["valid_t"], a["tx_t"])
        )
        assert _canon_graph(got) == _canon_graph(exp)
    elif fn == "store_history":
        got = json.loads(_NATIVE.store_history_json(json.dumps(a["snapshot"]), a["id"]))
        assert got == exp
    else:  # pragma: no cover - guards a new op landing in the fixture unwired
        pytest.fail(f"unhandled fixture op: {fn}")
