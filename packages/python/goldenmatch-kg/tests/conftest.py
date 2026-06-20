"""Shared test fixtures for the goldenmatch-kg shim tests.

The shim tests verify the BINDING/MARSHALING (entities -> goldenmatch -> the
framework's merge shape), NOT goldenmatch's fuzzy accuracy -- that is covered by
test_core.py's parity test against the real dedupe_df and by ER-KG-Bench.

Why this matters: real zero-config `dedupe_df` on a tiny (~3-row) toy frame commits
a degenerate best-effort RED config (blocking can't profile so few rows), and the
resulting fuzzy merge varies by goldenmatch version (1.30 merged "Apple"/"Apple
Inc"; 2.2 does not) and even across processes. Asserting a specific fuzzy merge on
toy input is therefore inherently flaky. So the shim tests inject a deterministic
first-token stand-in for `resolve_entities` (see `stub_resolution`) and use inputs
whose first token marks the intended grouping ("Acme Corporation" + "Acme"), which
gives a stable decision and isolates exactly what the shim adds: the marshaling.
"""
import pytest
from goldenmatch_kg.core import EntityResolution


def stub_resolution(entities, *, fields=("name", "type", "description")):
    """Deterministic stand-in for goldenmatch `resolve_entities`: group by FIRST token.

    Mirrors the real EntityResolution shape (groups + canonical_id/name maps), so a
    shim consuming any of those fields behaves exactly as it would on a real run --
    but the grouping rule is "share the same first whitespace token", which is stable
    across versions and processes. The canonical representative is the longest name in
    the group (tie -> first occurrence), matching core's real canonical rule, so a
    member with a shorter name (e.g. "Acme" in {"Acme Corporation", "Acme"}) really is
    rewritten to the canonical -- exercising the LlamaIndex name-rewrite path.
    """
    groups: list[tuple[str, ...]] = []
    canonical_id: dict[str, str] = {}
    canonical_name: dict[str, str] = {}
    by_key: dict[str, list] = {}
    order: list[str] = []
    for e in entities:
        key = e.name.split()[0] if e.name.split() else e.name
        if key not in by_key:
            by_key[key] = []
            order.append(key)
        by_key[key].append(e)
    for key in order:
        members = by_key[key]
        rep = max(members, key=lambda e: len(e.name))  # longest name, tie -> first
        groups.append(tuple(e.id for e in members))
        for e in members:
            canonical_id[e.id] = rep.id
            canonical_name[e.id] = rep.name
    return EntityResolution(tuple(groups), canonical_id, canonical_name)


@pytest.fixture
def patch_resolve(monkeypatch):
    """Patch `resolve_entities` in a shim's `_resolve` module with the deterministic stand-in.

    Usage: `patch_resolve("goldenmatch_kg.neo4j_graphrag._resolve")`. Because each
    `_resolve` helper looks up `resolve_entities` as a module global at call time,
    patching the module attribute redirects both the base-free helper and the
    framework-binding path that delegates to it.
    """
    def _patch(resolve_module_path: str) -> None:
        monkeypatch.setattr(
            f"{resolve_module_path}.resolve_entities", stub_resolution
        )

    return _patch
