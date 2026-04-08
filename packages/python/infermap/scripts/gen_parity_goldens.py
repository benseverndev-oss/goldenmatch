"""Generate parity golden files for the TypeScript port.

Loads tests/fixtures/parity_cases.json, runs the Python infermap engine
against each case with the declared min_confidence, and writes a normalized
golden JSON to tests/fixtures/_goldens/<name>.json.

The normalized shape deliberately drops reasoning strings, breakdown
per-scorer details, and elapsed timing — those are expected to drift
between Python and JS. What we lock in for parity:

  - ordered list of (source, target, confidence) mapping triples
  - unmapped_source and unmapped_target lists (sorted by field name)
  - min_confidence used for the run

Run this from the project root:

    python scripts/gen_parity_goldens.py

Regenerate whenever scorer logic changes in Python. Goldens are committed.
"""
from __future__ import annotations

import json
from pathlib import Path

# Import engine + providers without going through infermap.__init__
# (which pulls in yaml that is fine, but keep this script minimal).
from infermap.engine import MapEngine
from infermap.providers.memory import InMemoryProvider

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
MANIFEST = FIXTURES / "parity_cases.json"
GOLDENS_DIR = FIXTURES / "_goldens"

# Rounding precision for confidence scores. Must match the TS parity suite.
CONFIDENCE_PRECISION = 4


def _load_input(spec: dict):
    """Resolve a manifest input spec to something extract_schema can handle."""
    kind = spec.get("kind")
    if kind == "records":
        return spec["records"]
    if kind == "csv":
        return FIXTURES / spec["path"]
    raise ValueError(f"Unknown input kind: {kind!r}")


def _normalize_result(result, min_confidence: float) -> dict:
    """Return a stable, JSON-serializable golden dict for a MapResult."""
    mappings = sorted(
        (
            {
                "source": m.source,
                "target": m.target,
                "confidence": round(m.confidence, CONFIDENCE_PRECISION),
            }
            for m in result.mappings
        ),
        key=lambda m: (m["source"], m["target"]),
    )
    return {
        "min_confidence": min_confidence,
        "mappings": mappings,
        "unmapped_source": sorted(result.unmapped_source),
        "unmapped_target": sorted(result.unmapped_target),
    }


def main() -> int:
    if not MANIFEST.exists():
        print(f"Manifest not found: {MANIFEST}")
        return 1

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    GOLDENS_DIR.mkdir(parents=True, exist_ok=True)

    provider = InMemoryProvider()

    generated = 0
    for case in manifest["cases"]:
        name = case["name"]
        min_confidence = float(case.get("min_confidence", 0.3))

        src_input = _load_input(case["source"])
        tgt_input = _load_input(case["target"])

        # For record inputs, go straight through InMemoryProvider so we don't
        # carry polars schema quirks from other providers into the golden.
        if isinstance(src_input, list):
            src_schema = provider.extract(src_input)
        else:
            from infermap.providers import extract_schema
            src_schema = extract_schema(src_input)

        if isinstance(tgt_input, list):
            tgt_schema = provider.extract(tgt_input)
        else:
            from infermap.providers import extract_schema
            tgt_schema = extract_schema(tgt_input)

        engine = MapEngine(min_confidence=min_confidence)

        # Bypass engine.map() path that expects raw inputs — call the engine's
        # internal pipeline via a synthetic map() using pre-built schemas.
        # We achieve this by patching extract_schema via a monkey substitute.
        # Simpler: reconstruct the engine's logic locally using public scorers.
        import numpy as np
        from infermap.assignment import optimal_assign
        from infermap.types import FieldMapping, MapResult

        src_fields = src_schema.fields
        tgt_fields = tgt_schema.fields
        M, N = len(src_fields), len(tgt_fields)
        score_matrix = np.zeros((M, N), dtype=float)

        for i, sf in enumerate(src_fields):
            for j, tf in enumerate(tgt_fields):
                contributors = []
                for sc in engine.scorers:
                    r = sc.score(sf, tf)
                    if r is not None:
                        contributors.append((r, sc.weight))
                if len(contributors) < 2:
                    combined = 0.0
                else:
                    tw = sum(w for _, w in contributors)
                    ws = sum(r.score * w for r, w in contributors)
                    combined = ws / tw if tw > 0 else 0.0
                score_matrix[i, j] = combined

        assignments = optimal_assign(score_matrix, min_confidence)
        assigned_src = {r for r, _, _ in assignments}
        assigned_tgt = {c for _, c, _ in assignments}
        mappings = [
            FieldMapping(
                source=src_fields[r].name,
                target=tgt_fields[c].name,
                confidence=score,
            )
            for r, c, score in assignments
        ]
        unmapped_source = [src_fields[i].name for i in range(M) if i not in assigned_src]
        unmapped_target = [tgt_fields[j].name for j in range(N) if j not in assigned_tgt]

        result = MapResult(
            mappings=mappings,
            unmapped_source=unmapped_source,
            unmapped_target=unmapped_target,
        )

        golden = _normalize_result(result, min_confidence)
        out_path = GOLDENS_DIR / f"{name}.json"
        out_path.write_text(json.dumps(golden, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out_path.relative_to(ROOT)}")
        generated += 1

    print(f"generated {generated} golden file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
