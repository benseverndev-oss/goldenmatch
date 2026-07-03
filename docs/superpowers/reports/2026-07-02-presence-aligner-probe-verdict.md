# Presence-Aligner Probe — Verdict

**Date:** 2026-07-02
**Branch:** `feat/presence-aligner-probe`
**Run:** Modal `gg-bench`, `--corpus wiki`, best config (`name_ci` + chunking `(6,2)`, SCHEMA_CANON off), `--presence-probe`. 19 docs, 65 gold. 7B seeded (seed 42) + DeepSeek-V3. Measurement-only diagnostic.

## What this tested

The ~0.49 coverage ceiling is model-invariant (7B == V3). Root cause: provenance lives on edges, not nodes, so an edgeless-but-present entity has no doc association and the doc-keyed aligner can't reach it. Question: if the aligner reached those nodes, how much coverage returns and at what precision cost? Strict (shipped edge-based aligner) vs relaxed (reach edgeless nodes via global surface/alias match) on the same graph.

## Result — the ceiling is a PURE metric artifact, recoverable at ZERO precision cost

| axis | 7B strict | 7B relaxed | V3 strict | V3 relaxed |
|---|---|---|---|---|
| coverage | 0.4923 | **1.0000** | 0.4923 | **1.0000** |
| P(B) | 1.0000 | **1.0000** | 1.0000 | **1.0000** |
| R(B) | 0.3030 | 0.6970 | 0.3990 | 0.8131 |
| F1(B) | 0.4651 | **0.8214** | 0.5704 | **0.8969** |

On **both** graphs, relaxed coverage → **1.0** at relaxed **P(B) = 1.0**. Every one of the ~33 "missing" gold entities is present as a node, matches by surface, and produces **zero collisions**. The edge-requirement was excluding present-and-correct entities for **no precision benefit** on this corpus. The read agrees across the 7B and V3 graphs — robust.

## The honest reframe: the substrate was never ~0.5

The ~0.5 that framed this whole ceiling discussion (and the "is 0.5 good?" question) was **the edge-centric aligner under-counting**, not the substrate. Measured for entity-presence *and* relational quality together, the substrate is:

- **free/local 7B: F1 ≈ 0.82** (R(B) 0.70, P 1.0)
- **DeepSeek-V3: F1 ≈ 0.90** (R(B) 0.81, P 1.0)

That is a genuinely good knowledge substrate. The entities are all extracted, correctly resolved (P=1.0), and — once the metric counts them — well clustered. The "mediocre 0.5" conclusion was a measurement artifact.

## Reading guards (as the spec required — do not harden)

- **"lower bound" is directional, not a theorem.** The relaxed probe reaches nodes *globally* (any doc); a real per-doc node-provenance fix uses a *smaller* per-doc candidate pool, so it has *fewer* collision opportunities. Since the global version already achieved P(B) = 1.0, the per-doc engine fix is **at least as clean** — the direction strongly favours a safe fix.
- **"edgeless" is shorthand** — the global fallback reaches any-doc same-surface nodes, not literally only zero-edge ones. On this corpus it produced zero wrong matches, but that is a property of this corpus's low surface ambiguity, not a guarantee.
- **The two P columns are over different pair populations.** Relaxed clusters *more* gold → *more* predicted pairs, and still P(B) = 1.0 — i.e. more alignments, all correct. The P=1.0 is more impressive under relaxation, not merely preserved.

## Decision — build node provenance

- **PASS: build the node-provenance engine fix** as the next sub-project. Stamp `source_refs` on entity nodes (store schema + `build_batch` + query) and add a per-doc aligner path that reaches doc-attributed edgeless nodes. This does *per-doc* what the probe did *globally*, so it recovers coverage 0.49 → ~1.0 and F1 → ~0.82 (7B) / ~0.90 (V3) with the precision guard intact — and it is a genuine substrate improvement (you can list a doc's entities including isolated ones, not just its related pairs).
- **The metric itself should also report entity-presence coverage separately** from relational R(B) going forward — conflating them (today's edge-requirement) is what hid a 0.82 substrate behind a 0.49 number.

## Honest caveats

- **One corpus, low surface ambiguity.** The zero-collision result is partly a property of 19 clean tech-company Wikipedia leads; a corpus with more same-surface distinct entities (homographs) would exercise the collision risk the probe is built to detect. The per-doc engine fix is safer there than the global probe.
- **Measurement-only.** Nothing shipped changes the engine or the shipped strict metric; this diagnostic quantifies the prize and confirms it's safe to build.
- **This does not raise the *relational* ceiling** — R(B) among genuinely-related entities is still model-bound (V3 > 7B). Node provenance recovers the *presence* axis; relational quality remains where the extractor puts it.

## Follow-ons

1. **Node-provenance engine spec** (the PASS hand-off): `source_refs` on nodes + per-doc aligner path. The real fix.
2. **Split the reported metric** into presence-coverage and relational-R(B) so the substrate's two quality axes stop hiding each other.
3. A homograph-heavy corpus to stress the collision path before trusting the per-doc fix broadly.
