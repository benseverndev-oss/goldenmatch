# DataFusion spine (scale-substrate SP2) — full relational spine, B2 Rust ScalarUDF

**Date:** 2026-06-03
**Status:** design (approved scope: full spine + B2; pre-spec-review)
**Parent:** `2026-06-01-arrow-native-finish-line-design.md` § "Gate reframe: engine
portability". Step 1 (id_prep plannable) is PROVEN + merged (#696): id_prep 566→34s
@100M, end-to-end 2.11×. This is step 2 — insert DataFusion as the planner/spill
spine over the relational stages.

**Goal:** Thread **score → dedup → [UF break] → id_prep → golden** on ONE Python
`datafusion` `SessionContext` with **out-of-core spill**, the native scorers as a
**Rust `ScalarUDF` via `datafusion-ffi`** (B2), and Union-Find routed to the
existing distributed **label-propagation** path. Gated behind `mode="scale"`.

**The win being proven (per the handoff):** out-of-core spill — the spine SURVIVES
where the in-memory path OOMs — and engine portability (the same plan distributes
on Sail later). NOT constant-factor op speed.

---

## Scope guard

IN: the full one-box relational spine + the B2 Rust ScalarUDF + UF→label-prop
routing + the scale-mode contract + the out-of-core spill bench.
OUT (later): **Sail** (distributed) — one-box spine proves first. Golden custom
field-rules / quality_scores stay in-memory fallback (distributed-path is the
uniform-strategy case). LLM/rerank/boost/NE/exotic matchkeys — DROPPED (error).

## Architecture

```
            ┌─────────────── one datafusion.SessionContext (memory_limit → spill) ───────────────┐
 blocked    │  score (SQL w/ FFI scorer UDF)  →  dedup (max(score) GROUP BY a,b)  →  pairs_df     │
 candidates │                                                                          │          │
            └──────────────────────────────────────────────────────────────────────────┼─────────┘
                                                                                         ▼  [UF break — not relational]
                                                          label-prop (goldenmatch.distributed.clustering) → assignments_df
                                                                                         │
            ┌────────────────────────────────────────────────────────────────────────────┼─────────┐
            │  id_prep (group_by edges over pairs ⋈ assignments)   +   golden (group_by → representative) │
            └──────────────────────────────────────────────────────────────────────────────────────┘
```

- **One `SessionContext`** with `RuntimeEnvBuilder().with_disk_manager_os().with_fair_spill_pool(memory_limit)` (v53 API, confirmed in SP1 #695) so every RELATIONAL stage (score/dedup/id_prep/golden) spills.
- **Stages are SQL/DataFrame ops**; each stage's output registered as a view for the next.
- **The score stage is a block-self-join** (`a.block_key=b.block_key AND a.id<b.id` with the scorer UDF), NOT a flat re-score of a pre-joined pair frame — mirror `score_blocks_datafusion`'s shape (the UDF-batch efficiency depends on it). The spine input is BLOCK-shaped candidates, not pairs.
- **The UF break is an in-memory island OUTSIDE the spill domain.** Below the 50M-pair threshold (`distributed.clustering._LABEL_PROP_PAIR_THRESHOLD`) `build_clusters_distributed` routes to the **scipy.csgraph driver path** (`_build_clusters_scipy_fallback`), NOT distributed label-prop — that's the real one-box code path (name it precisely in tests). It collects the deduped pairs frame to the driver, so its scaling envelope is scipy-on-driver (~50M pairs / ~1.2 GB), separate from DataFusion's spill. Hand `all_ids` as an Arrow array / derive it from the pairs frame inside the spine — do NOT materialize a Python `list[int]` of every record id (the CLAUDE.md WCC-rehydration-OOM trap; the current `all_ids: list[int]` signature does not protect against it). Round-trip IN: consume the returned `{member_id, cluster_id}` frame directly (`.to_arrow()`); do NOT route through `materialize_cluster_dict` (it `take_all()`s into a dict-of-dicts — the rehydration we are eliminating).
- **B2 scorer:** a Rust crate exports the native string scorers as an `FFI_ScalarUDF` (`datafusion-ffi`), surfaced as a PyCapsule via pyo3; Python registers it into the ctx. Replaces the existing Python-batch UDF (`datafusion_backend.py::_make_score_udf`, B1).

## The B2 Rust ScalarUDF (the load-bearing risk — de-risk FIRST)

`datafusion-ffi` (`FFI_ScalarUDF` ↔ `ForeignScalarUDF`; Python side
`ScalarUDF.from_pycapsule()` + the `__datafusion_scalar_udf__()` PyCapsule
protocol — confirmed viable on v53) is the cross-crate ABI. The Python `datafusion`
53 package and our Rust `datafusion` crate are SEPARATE DataFusion instances; the
FFI PyCapsule is the only sound bridge. **Version lockstep (confirmed):** the
crates version with the Python major — `datafusion-ffi 53.x` → `datafusion 53.x`,
matching `datafusion-python` 53. So: **pin the Rust crate `datafusion = "=53.x"` +
`datafusion-ffi = "=53.x"`**, and **bump the Python extra `datafusion>=53,<54`**
(today it's `>=44`, which would let pip resolve a 44–52 wheel that mismatches the
Rust 53 crate → the exact ABI break Stage A guards, hit at install time). Add a CI
assertion that the installed Python `datafusion` major == the Rust crate's
`datafusion` major. **Stage A de-risks this with a trivial UDF first** — if the
boundary doesn't hold, B2 is blocked → fall back to B1 (Python UDF) for the spine,
escalate to the human. **Arrow-major divergence (the concrete reason the crate MUST
be separate, not just "build weight"):** DataFusion 53 pins **arrow 58**; the
existing `_native` crate uses `arrow = "55"`. Two separate cdylibs tolerate this;
putting DataFusion into the `_native` crate would force one arrow major on both
and break the build. The new crate uses `arrow = "58"` with `features=["ffi"]`.

## Stages (the spec decomposes the "full spine" into staged, independently-testable units)

- **Stage A — FFI ScalarUDF spike.** A minimal Rust `ScalarUDF` (e.g. `add_one`)
  exported via `datafusion-ffi` as a PyCapsule; a Python test registers it into a
  `SessionContext` and `SELECT add_one(x)` returns correct values. Proves the
  cross-crate v53 boundary. GATE: if it can't be made to work, STOP and escalate.
- **Stage B — scorer as FFI ScalarUDF.** Wrap the existing native scorers
  (jaro_winkler/token_sort/etc.) as `FFI_ScalarUDF`(s). Parity: the UDF's scores
  equal the in-process native scorer's for a fixture of string pairs (ε for f32).
- **Stage C — spine orchestration.** `run_spine(blocked_candidates, config, *,
  memory_limit) -> (golden_df, assignments_df)`: thread score → dedup →
  UF(scipy/label-prop) → id_prep + golden on one ctx. **Written against the UDF
  INTERFACE, not the FFI impl** — so B1 (Python UDF) ↔ B2 (FFI) is a one-line swap
  and C is testable even if B2 slips. Semantic parity vs the in-memory pipeline:
  Rand-1.0 partition, identical golden content, edge-set parity on id_prep.
- **Stage D — scale-mode contract.** Determinism across `target_partitions`
  {1,3,N}: assert the **emitted pair SET and the cluster PARTITION are identical**
  (NOT raw f32 float equality — scores are f32-origin, so a 1e-12 gate is below f32
  ULP and would flag false non-determinism; the real hazard is a pair within f32-
  ULP of the `score>=threshold` cutoff flipping across partitions and changing the
  edge set → the partition). Use a fixture with NO pair within ε(1e-6) of the
  threshold so the gate measures determinism, not threshold flapping. MAX dedup;
  feature-gating (LLM/rerank/boost/NE/exotic → explicit error, never silent). D
  does not strictly block on C (feature-gating is pure routing).
- **Stage E — out-of-core spill bench.** Full spine at an OOM-seeking scale →
  DataFusion SPILLS and SURVIVES where the in-memory pipeline OOMs. **Scope: the
  spill-survival claim is for the RELATIONAL stages (score/dedup/id_prep/golden)
  ONLY.** The UF break collects pairs to the driver (scipy.csgraph), an in-memory
  island the spill pool does NOT cover — bound the pair-frame size at that
  collection and keep Stage E below the scipy envelope (~50M pairs), else the spine
  OOMs at the UF boundary and falsely reads as "spine doesn't survive" when the
  relational stages actually did their job. (At Sail scale — OUT of this spec — UF
  routes to true distributed label-prop above 50M, removing the island.) 3-way
  where meaningful (in-memory / DataFusion / bucket). Commit numbers to the roadmap.

## Gates (engine-portability, per the reframe — NOT one-box RSS)

- Stage A: the FFI UDF evaluates correctly (feasibility).
- Stage C: semantic parity (Rand 1.0 + golden content + id_prep edge sets) vs
  in-memory; deterministic across `target_partitions`.
- Stage E: spill-survives-where-in-memory-OOMs at the OOM-seeking scale; wall
  holds out-of-core (not a constant-factor target).

## Build / dependency surface

- **A SEPARATE maturin crate** under `packages/rust/extensions/` depending on
  `datafusion = "=53.x"` + `datafusion-ffi = "=53.x"`, with its OWN
  `pyproject.toml` + wheel/publish lane (mirror `goldenmatch-native`). The
  existing `native` crate is a standalone `[workspace]` built by
  `scripts/build_native.py` (hard-codes the `native` crate → `_native.abi3.so`);
  the DataFusion crate needs its own build entrypoint — do NOT extend
  `build_native.py` or the `native` crate (arrow-major clash + bloat, see B2
  section). The Python `goldenmatch[datafusion]` extra exists but pins `>=44`
  (the B1 spike) — **bump to `>=53,<54`** so the Python package matches the Rust
  crate major.
- CI: the spine tests need the python `datafusion` extra + the new Rust crate
  built (cargo). Mirror the native-build lane.
- Posture: subagents validate via `ruff`+`py_compile` (Python) and `cargo check`
  (Rust, if it doesn't hang) — real tests in CI.

## Risks

- **FFI ABI mismatch (Stage A gate)** — the whole B2 premise. De-risked first.
- **DataFusion-crate build weight** — isolate the crate; don't bloat `_native`.
- **UF break round-trip** — collecting the deduped pairs frame out to label-prop
  and back is a materialization point; at scale it must stay frame-native (Arrow),
  not a Python list (the CLAUDE.md WCC-rehydration-OOM lesson). label-prop already
  takes frames (`distributed.clustering`) — verify the interface at Stage C.
- **Determinism across workers** — DataFusion parallel float reduction; pin
  reductions (Stage D), the scale-mode hard requirement.
- **Spill may still not bind** — if the in-memory path doesn't OOM at reachable
  scale, the spine's value is the engine-portability/Sail story, not one-box
  survival. Stage E's OOM-seeking design is what tells us (same honesty as SP1).
