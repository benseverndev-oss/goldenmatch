# North Star Roadmap — closing the gap to "de facto"

The North Star ([../foundation/project-definition.md](../foundation/project-definition.md))
is to be **the tool any developer reaches for by default for entity resolution**. This
roadmap is the honest answer to "where are we falling short, and what closes it?" — graded
against the five commitments, sequenced by *leverage toward adoption*, not by what is most
fun to build.

**Sequencing decision (2026-06-15):** outward/adoption work leads; the Sail engine-
unification (the one big inward arc) is deliberately last because it is infra-blocked and
moves the North Star metric least per hour. Siblings stay active (the suite-as-a-whole is
the product) — so W5 is contributability/bus-factor work, **not** a breadth freeze.

The core diagnosis: **we have largely *built* a tool worthy of being the default and have
barely started *making* it the default.** Rigor (accuracy, traceability) is top-decile;
the shortfall is everything between "it's excellent" and "strangers reach for it."

**Reconciliation note (2026-06-15):** two active *inward* arcs already advance W2/W5 —
the **single-kernel-collapse** (R0-R5, [../decisions/0016-single-kernel-collapse-spike.md](../decisions/0016-single-kernel-collapse-spike.md),
[../architecture/single-kernel-collapse-roadmap.md](../architecture/single-kernel-collapse-roadmap.md);
R0 done, R1 GO #978, R2 in flight #980) and the **config-correctness planner**
([../../docs/superpowers/specs/2026-06-14-config-correctness-planner-design.md](../../docs/superpowers/specs/2026-06-14-config-correctness-planner-design.md),
#965). W2/W5 below *credit and ride* these, not duplicate them. That both missed arcs are
inward only sharpens the thesis: the hours keep flowing inward; the outward gap (W1) is
where the North Star is starved.

---

## The scoreboard (stand up first — without it, "de facto" is unfalsifiable)

Every workstream below gates on a number, not on our own judgment.

| Signal | Why it is the North Star proxy | Today |
|---|---|---|
| Weekly PyPI + npm downloads (suite + goldenmatch alone) | Actual reach | badge exists; not tracked as a goal |
| GitHub stars *velocity* (per week) | Discovery momentum | unmeasured |
| **Inbound issues / PRs from strangers** | The truest "someone reached for it" | ~0 |
| Time-to-first-success (clone → correct dedupe) | Zero-config floor, measured | unmeasured |

---

## Workstreams (one per shortfall)

### W1 — Adoption & distribution *(gap: the verb "reaches for")* — highest leverage
Convert engineering quality into external pull.
- Honest comparison page: goldenmatch vs Splink / Zingg / dedupe.io / RecordLinkage, axis-by-axis, **including where we lose**. Generalize the existing Splink bake-off.
- One real-world case study: **write up the Abt-Buy / Amazon-Google product-matching run** (already done, unpublished — pure waste otherwise).
- Land the prepared awesome-list PRs + the GitHub social-preview image (the discoverability arc's open tail).
- A <5-min getting-started with an asciinema cast on the README.
- **Gate:** stars velocity *and* weekly downloads trend up over a 4-week window; >=1 inbound issue from a stranger.

### W2 — Close the opt-in -> default gap *(commitment #1: zero-config ceiling)*
The `dedupe file.csv` user should get the ceiling, not the floor. **Largely in flight** —
this workstream is now mostly *coordination*, not greenfield:
- **Rides the single-kernel-collapse arc** (R1 GO #978, R2 #980): native scorer/kernel
  paths are being brought under one reversible governed default with parity gates. W2's job
  is to make sure each collapsed default is the *user-facing* default where edge-safety
  allows (note: pure-TS stays the permanent fallback — WASM is opt-in for edge-safety, a
  hard constraint, not a gap to close).
- **Rides the config-correctness planner** (#965): routing becomes correct-by-construction
  so the zero-config user lands on the right path (and gets warned off slow overrides).
- **Net-new W2 piece — the default-flip ledger** (`default-flip-ledger.md`): an *umbrella*
  list of the remaining default-OFF flags NOT covered by the two arcs (Lance store, llama,
  `mode=scale`, quality bridges, einstein tier), each with a written auto-on/auto-detect
  criterion (e.g. auto-detect quality bridges when goldencheck is installed).
- **Gate:** N flags moved opt-in -> auto per quarter; time-to-first-success drops.

### W3 — Scale-invariant correctness *(commitment #2 — largely already met)*
Correction (2026-06-15): equivalence is **already proven**, not asserted-with-an-asterisk,
and a new per-PR equivalence gate would be *costly*, not cheap — running standard / `scale` /
distributed on a meaningful fixture means heavy (and for the binding case, cluster) lanes on
every PR, which the paths-filter CI discipline deliberately avoids. The existing proof:
- DataFusion spine Stage C is parity-gated (Rand 1.0 + golden + edges).
- The Sail tier ships connectivity + pair-set parity gates (S1-S4).
- The 100M Ray run was validated recall-complete (20M clusters exact, #851/#852/#864), and
  a distributed-100M *quality-parity* result is recorded (#955), with distributed-clustering
  correctness fixes since (#968/#970).

So the residual is small and *not* a new CI lane:
- **Discoverability (docs, ~0 cost):** link the existing parity gates + the 100M validation
  from `decisions/0002` and the scale-mode docs so "scale-invariant" reads as *proven*,
  retiring the "semantically-correct-not-bit-identical" asterisk in perception.
- **Fix the known `run_spine` empty/all-singleton `SchemaError`** (small, real).
- **Long pole (Phase 3):** the real architectural debt is that scale is *three paths, not one
  engine*; only the Sail binding 100M multi-node run -> collapsing the paths -> flipping the
  `mode` default closes it. Infra-blocked on `SAIL_REMOTE`; must not block W1/W2.
- **Gate:** the existing proof is documented + discoverable; `SchemaError` fixed. **No new
  costly CI lane.**

### W4 — Surface-parity dashboard *(commitment #3)*
Stop stranding capabilities on one surface.
- A capability x surface matrix (CLI / Python / TS / SQL / MCP / A2A / web), green/red,
  generated from tests.
- Rule: a new core capability ships its parity fixtures *with it*, not in a later sweep.
- Burn down the current reds (resolveClusters, config optimizer, PPRL, AgentSession).
- **Gate:** matrix published and going greener; no new capability lands red.

### W5 — Bus-factor & contributability *(gap: bus-factor of one)*
Make it contributable. (Siblings stay active per the 2026-06-15 decision — so this is about
*lowering the contribution barrier*, not freezing scope.)
- **The duplicated-math half is already in flight:** the single-kernel-collapse arc
  (R0-R5) is collapsing N-language reimplementations onto shared `*-core` kernels — the
  biggest structural source of the maintenance/bus-factor tax. W5 does not re-propose this;
  it tracks it as the substrate win and (at R5) the ast-grep gate that *forbids* algorithm
  math outside `*-core`.
- **Net-new W5 piece:** `CONTRIBUTING.md` that externalizes the top ~15 CLAUDE.md lessons
  into outsider-readable form; `good first issue` labels. (The tribal-knowledge barrier is
  *not* addressed by the collapse arc — that is the human on-ramp, still missing.)
- **Gate:** >=1 external contributor merges something.

---

## Phased 90-day plan (interleaved — not serial)

| Phase | Theme | Ships |
|---|---|---|
| **0 (wk 1-2)** | Make the floor honest | Scoreboard live - link existing scale-equivalence proofs (W3 docs, no new CI lane) - default-flip ledger (W2) - parity dashboard scaffold (W4) - comparison page draft (W1) |
| **1 (wk 3-6)** | Make it reachable | Getting-started + asciinema - Abt-Buy case study - awesome-list PRs + social image - `run_spine` `SchemaError` fix - CONTRIBUTING (W5) |
| **2 (wk 7-10)** | Make it stick | Flip 2-3 defaults (W2) - burn down top parity reds (W4) - good-first-issues - **read the scoreboard: did wk1 work move it?** |
| **3 (wk 11+)** | Unify the engine | Sail 100M binding run -> collapse scale paths -> flip `mode` default (W3 long pole, infra-permitting) |

**Through-line:** Phases 0-2 are ~80% outward/honesty work and move the North Star metric;
Phase 3 is the one big inward arc, deliberately last. That inverts where the hours have gone
historically — which is the point of the diagnosis.

**Not now (deferred, not frozen):** new surfaces and new optional backends wait until the
scoreboard moves. (Sibling *features* are not frozen — that was considered and declined;
the suite-as-a-whole is the product.)

---
**Classification:** planning/active - **Last updated:** 2026-06-15
