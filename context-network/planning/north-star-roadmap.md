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
The `dedupe file.csv` user should get the ceiling, not the floor.
- **Default-flip ledger** (`default-flip-ledger.md`): every default-OFF flag
  (`GOLDENMATCH_NATIVE`, Lance store, llama, `mode=scale`, distributed, quality bridges,
  WASM, einstein tier) gets a written criterion that would make it auto-on / auto-detected.
- Retire the first 2-3 (start with the already-safe ones: native-when-wheel-present is
  effectively default — make it honest + documented; auto-detect quality bridges when
  goldencheck is installed).
- **Gate:** N flags moved opt-in -> auto per quarter; time-to-first-success drops.

### W3 — Prove scale-invariant correctness *(commitment #2)*
"Same input -> same answer, laptop -> 100M" becomes *proven*, not asserted-with-an-asterisk.
- **Cheap now:** a cross-scale equivalence CI gate — one fixture through standard / `scale`
  / distributed, assert cluster-equivalence (B^3 or exact). Closes the `decisions/0002`
  "semantically-correct-not-bit-identical" honesty gap today, no cluster needed.
- Fix the known `run_spine` empty/all-singleton `SchemaError`.
- **Long pole (Phase 3):** the Sail binding 100M multi-node run -> collapse the three engine
  paths into one -> flip `mode` default. Infra-blocked on `SAIL_REMOTE`; must not block W1/W2.
- **Gate:** cross-scale equivalence test green in CI on every PR.

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
- `CONTRIBUTING.md` that externalizes the top ~15 CLAUDE.md lessons into outsider-readable
  form; `good first issue` labels.
- Because siblings stay active, the breadth/maintenance tax is *accepted* — so invest in the
  shared substrate (parity harness, CI patterns, the CLAUDE.md lessons) that keeps the tax
  bounded as the suite grows.
- **Gate:** >=1 external contributor merges something.

---

## Phased 90-day plan (interleaved — not serial)

| Phase | Theme | Ships |
|---|---|---|
| **0 (wk 1-2)** | Make the floor honest | Scoreboard live - cross-scale equivalence CI gate (W3) - default-flip ledger (W2) - parity dashboard scaffold (W4) - comparison page draft (W1) |
| **1 (wk 3-6)** | Make it reachable | Getting-started + asciinema - Abt-Buy case study - awesome-list PRs + social image - `SchemaError` fix - CONTRIBUTING (W5) |
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
