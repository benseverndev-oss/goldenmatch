"""The runner must emit a valid fidelity tier for EVERY adapter row."""
from __future__ import annotations

import sys
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

VALID = {"real", "real-inproc", "real-live", "validated", "modeled"}


def test_every_row_has_valid_fidelity():
    from erkgbench import run  # pyright: ignore[reportMissingImports]
    report = run.run(None)  # offline; no key
    assert report["results"], "no adapter rows"
    for r in report["results"]:
        assert r.get("fidelity") in VALID, f"{r.get('name')!r} -> {r.get('fidelity')!r}"


def test_real_neo4j_row_present_and_real():
    from erkgbench import run  # pyright: ignore[reportMissingImports]
    report = run.run(None)
    real = [r for r in report["results"] if r["name"] == "neo4j-graphrag(fuzzy)*"]
    assert real, "real neo4j-graphrag row missing"
    assert real[0]["fidelity"] == "real-inproc"
    assert round(real[0]["overall"]["f1"], 3) == 0.470


def test_emb_rows_present_with_correct_tiers():
    # Tier-only check via a tiny deterministic fake embedder (no torch needed).
    # Both embedder variants stay `modeled`: KGBuilder(emb) because its real
    # edit-distance length guard is irreproducible (elementId-sided; see
    # modeled.py audit + FIDELITY.md), LlamaIndex(emb) because its rule is
    # blog-sourced/unconfirmable -- an embedder fixes neither.
    from erkgbench.adapters.modeled import emb_modeled  # pyright: ignore[reportMissingImports]

    def fake_embed(texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0, 0.0] for t in texts]

    by_name = {a.name: a.fidelity for a in emb_modeled(fake_embed)}
    assert by_name.get("Neo4j-KGBuilder(emb)") == "modeled"
    assert by_name.get("LlamaIndex-PGI(emb)") == "modeled"
