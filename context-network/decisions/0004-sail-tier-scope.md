# 0004 — Sail tier scope: full, buildable, Sail-native, replaces Ray

**Status:** accepted (2026-06-03, Ben) • **Spec:** `docs/superpowers/specs/2026-06-03-sail-tier-design.md`

## Context
After the Stage E honest-null ([0003](0003-stage-e-spill-honest-null.md)), the distributed
(Sail) path is the real value. Three scoping forks were put to Ben; he chose the ambitious
option on all three.

## Decision
- **Nature:** a **buildable implementation spec now** (not a spike-first, not paper-only).
  Mitigation: the build is staged S1→S4 with the load-bearing WCC de-risk as S1+S2, so the
  riskiest unknown is proven before the rest — the spike is folded into the build, not skipped.
- **Vs Ray:** **Sail-native everything, replace the Ray distributed stack** — including a
  Sail-native connected-components (no native graph in Sail → port two-phase WCC). Ray is
  NOT retired until S4's binding 100M+ bench passes (one-release deprecation window).
- **Scope:** **full spine-on-Sail** — load → block → score → dedup → WCC → golden (incl.
  custom field-rules) → identity.

## Consequences / honest flags
- "Buildable now" is real, but Sail is **Spark Connect**, so this is a re-expression (new
  `goldenmatch.sail` code), not a port of `run_spine`. Every stage self-parity-gates vs the
  one-box spine.
- **WCC-on-Sail (S2) is the gate.** If it can't be made native + correct, the
  Sail-native-everything premise is in question and we escalate; Ray stays.
- No `mode` default-flip and no Ray retirement on faith — both gated on S4.

## Alternatives not taken
- Spike-first / design-on-paper (declined — folded the de-risk into S1+S2 instead).
- Sail owns relational + Ray keeps the UF holdout (declined — chose full Sail-native).
- Minimal binding proof only (declined — chose full spine scope).

---
**Classification:** decision/accepted • **Last updated:** 2026-06-03
