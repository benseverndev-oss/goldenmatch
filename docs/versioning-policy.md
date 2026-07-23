# GoldenMatch versioning policy (npm vs PyPI)

> **Adopted (2026-07-23).** The recommendation below is now the suite's standing
> policy: **independent semver per surface**, no lockstep. The npm `1.0.0`
> stability milestone shipped (goldenmatch TS is now `1.x`), and the durable,
> user-facing statement of this policy lives in the docs at
> `docs-site/reference/versioning.mdx` (published under **Reference → Versioning
> policy**). This file remains the engineering rationale + rejected-alternatives
> record; the published page is the citable policy.

**Status:** Recommendation, 2026-06-15 (adopted 2026-07-23)
**Question:** Should the npm `goldenmatch` package jump `0.13.0` → `2.0.0` to match
the PyPI package, and then move in lockstep going forward?

## TL;DR

**No — do not bump npm to `2.0.0` now, and do not adopt strict lockstep.** Keep the
`0.x` line until the TS surface is genuinely stable, then cut **`1.0.0`** (not `2.0.0`).
In the interim, publish a **compatibility matrix** so users get the cross-language
version story honestly, today. Reconsider *aligned majors* only after both surfaces
are independently co-stable — and even then, lean against lockstep because the two
packages deliberately cover different scope.

## Update (2026-06-15, post-agent-port)

The largest blocker named below — the **undeclared AgentSession / A2A gap** — is now
**closed**. The agent surface was ported to TS in four waves (PRs #989/#994/#995/#996):
`AgentSession` decision core + shared `AGENT_SKILLS` registry, 14 agent-level MCP tools
(MCP 30→44), the A2A skill-union card + fail-closed bearer auth, and node file-loaders.
The three tools with no TS core (`sensitivity`/`incremental`/`certify_recall`) are now
**declared Python-only** in the TS CLAUDE.md (like the distributed engine / web UI) — so
there is no longer an *undeclared* gap. The recommendation is unchanged (cut **1.0.0**,
not 2.0.0; compatibility matrix, not lockstep); the remaining gates to a `1.0.0` are now
just the in-flight parity chain (`#857 → #860 → #856 → #858`) and the 4-decimal staleness
gate — the AgentSession decision (gate item 3) is done.

## Verified facts (2026-06-15)

| | npm `goldenmatch` | PyPI `goldenmatch` |
|---|---|---|
| Latest published | `0.13.0` | `2.0.0` |
| Stance | "intentionally pre-1.0" (TS CLAUDE.md) | GA, 2.0.0 deprecation-window cut shipped 2026-06-14 |

Parity verdict (cross-checked against the code, the TS CLAUDE.md wave history, and the
2026-06-11 parity-audit roadmap): **SIGNIFICANT GAPS for a stability commitment**, not
"at parity." Core ER (scoring, blocking, clustering, golden, auto-config, identity core,
PPRL, memory, MCP, CLI) IS ported at 4-decimal parity. But:

- **`AgentSession` / A2A protocol** — was entirely absent from TS (~15% of Python's
  public surface) and undeclared. **Resolved 2026-06-15** (see Update above): ported in
  four waves; the 3 no-TS-core tools are now declared Python-only. No longer a gap.
- **Parity fixtures are stale**; #856 flags the staleness gate as a keystone CI blocker,
  with an in-flight chain `#857 → #860 → #856 → #858` still landing.
- Smaller deltas: embedding scorers are structural-only (no torch/Vertex numerics);
  `DOMAIN_EXTRACTED_COLS` is 3 vs Python's 12.
- Intentionally Python-only (declared, fine): Ray/GPU distributed engine, React web UI.

## Why not bump to 2.0.0 now

1. **The precondition fails.** A `2.0.0` tag is a semver stability promise. The TS surface
   is explicitly pre-1.0, still churning (unreleased work in CHANGELOG), and missing a
   first-class Python capability (AgentSession) with no decision recorded.
2. **This exact move was already reverted.** #463 set `package.json` to `2.0.0` (a
   "Phase-5 plugin port" milestone label); it "broke the documented 0.x wave line and was
   never published (npm stayed at 0.10.0)," and was corrected back to `0.11.0`. Repeating
   it re-litigates a settled decision.
3. **It wastes the 1.0 signal.** `0.13 → 2.0` skips the one signal the TS package actually
   needs to send: *"this is now stable" (1.0)*. It never had two generations of breaking
   change, so `2.0` is a number with no semver meaning behind it.
4. **The packages are deliberately different scope.** TS is edge-safe and a subset by
   design. A shared version number implies an equivalence that does not exist.

## Why not strict lockstep

Strict lockstep (same version always) means a breaking change in Python forces a no-op
major bump on npm even when zero TS code changed, and vice versa. The version number
stops carrying honest per-package semver meaning, and you publish empty releases. This is
the standard lockstep tax, and it is *worse* here because the two surfaces are
intentionally non-equivalent — lockstep is for bindings that track the *same* surface.

## Recommended policy

**Near-term (do this now, low cost, high clarity):**
- Publish a **compatibility / parity matrix** in both READMEs and the docs site: a small
  table mapping `npm X ⇄ pip Y`, plus a "scope deltas" column listing the Python-only
  surfaces (distributed, web UI, and — pending the decision below — AgentSession/A2A).
  This delivers ~90% of the "one coherent version story" benefit with zero version churn.

**Gate to npm `1.0.0` (the real stability milestone):**
1. Land the in-flight parity chain (`#857 → #860 → #856`) and get the 4-decimal-tolerant
   **staleness gate green** so Python autoconfig changes can't silently drift TS.
2. Land the zero-config multi-source fix (`#858`).
3. ~~**Make the explicit AgentSession / A2A call:**~~ **DONE (2026-06-15):** ported in 4
   waves (#989/#994/#995/#996); the 3 no-TS-core tools (`sensitivity`/`incremental`/
   `certify_recall`) are declared Python-only in the TS CLAUDE.md. The undeclared gap that
   blocked an honest "stable" claim is closed.
4. Then cut npm **`1.0.0`** with a clear semver contract ("stable through 1.x; breaking
   changes only at 2.0"). Note the `0.13 → 1.0` jump in the changelog.

**Long-term (the lockstep question, revisit after 1.0):**
- Prefer **independent semver + the compatibility matrix** as the durable model, because
  the surfaces are deliberately non-equivalent.
- If a single product-generation signal is later judged worth it, adopt **aligned majors**
  (shared major = product generation; a breaking change in *either* binding bumps both;
  minor/patch float independently per package) — *not* strict lockstep. Accept that even
  aligned-majors pays an occasional no-op-major tax.

## If marketing overrides this

If a single "2.0" number across languages is wanted for launch optics despite the above,
the least-bad version of that is: finish the parity chain + the AgentSession decision
first, then jump npm straight to `2.0.0` *with an explicit changelog note* that it is a
**product-alignment** version (adopting the PyPI line), not a semver-derived one, and
adopt aligned-majors (not strict lockstep) from there. Do not do it on top of stale parity
fixtures or an undeclared AgentSession gap — that ships a stability promise the package
can't keep.
