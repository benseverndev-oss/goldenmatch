"""Cross-surface parity: the Python ``goldenprofile_native`` boundary must
reproduce the shared fixture set authored from the host kernel.

The same fixtures are asserted on the TS/WASM side in
``packages/typescript/goldenprofile/tests/parity/goldenprofile-wasm.parity.test.ts``.
Both wrap one kernel (``goldenprofile-core``), so this closes the
Python <-> WASM loop through a single source of truth:

    host oracle  ==  fixtures  ==  Python (here)  ==  WASM (TS test)

Skips cleanly when the native wheel isn't built (``importorskip``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

resolve_json = pytest.importorskip("goldenprofile_native").resolve_json

# Anchor to the repo root so CWD differences (local pkg dir vs CI repo root)
# don't matter. parents: [0]=tests [1]=goldengraph [2]=python [3]=packages [4]=root
_REPO_ROOT = Path(__file__).resolve().parents[4]
_FIXTURE = (
    _REPO_ROOT
    / "packages"
    / "typescript"
    / "goldenprofile"
    / "tests"
    / "parity"
    / "fixtures"
    / "goldenprofile"
    / "resolutions.json"
)


def _load_cases() -> list[dict]:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return data["cases"]


def _canon_clusters(clusters: list[list[int]]) -> list[list[int]]:
    """Canonical partition: sort members within each cluster, then sort clusters.

    The kernel's cluster ORDERING is not canonical across builds (it falls out
    of hash-map iteration order), so the cross-surface invariant is the
    partition (set of sets), not the byte ordering.
    """
    return sorted((sorted(c) for c in clusters), key=lambda c: (c[0], len(c)))


def _canon_edges(edges: list[dict]) -> list[dict]:
    """Canonical edge set: a<=b within each edge, scores 4dp, sorted by (a, b)."""
    out = []
    for e in edges:
        s = e["score"]
        a, b = (e["a"], e["b"]) if e["a"] <= e["b"] else (e["b"], e["a"])
        out.append(
            {
                "a": a,
                "b": b,
                "score": {
                    "name": round(s["name"], 4),
                    "category": round(s["category"], 4),
                    "anchor": round(s["anchor"], 4),
                    "embedding": round(s["embedding"], 4),
                    "attribute_bonus": round(s["attribute_bonus"], 4),
                    "gated_in": s["gated_in"],
                    "score": round(s["score"], 4),
                },
            }
        )
    return sorted(out, key=lambda e: (e["a"], e["b"]))


def test_fixture_file_present() -> None:
    assert _FIXTURE.is_file(), f"missing parity fixtures at {_FIXTURE}"
    assert len(_load_cases()) > 0


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["name"])
def test_python_matches_fixture(case: dict) -> None:
    got = json.loads(resolve_json(json.dumps(case["request"])))
    expected = case["expected"]
    # Same partition (order-independent).
    assert _canon_clusters(got["clusters"]) == _canon_clusters(expected["clusters"])
    # Same edge set + scores to 4dp (order-independent).
    assert _canon_edges(got["edges"]) == _canon_edges(expected["edges"])
