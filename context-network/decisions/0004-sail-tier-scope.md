# 0004 — Sail tier scope: full, buildable, Sail-native, ADDITIVE to Ray (amended 2026-06-15)

**Status:** accepted (2026-06-03, Ben) • **Amended** 2026-06-15 (Sail is additive, NOT a Ray
replacement — see Amendment) • **Spec:** `docs/superpowers/specs/2026-06-03-sail-tier-design.md`

## Amendment (2026-06-15, Ben) — Sail is additive; Ray stays
The original decision framed Sail as **replacing** the Ray distributed stack (a one-release
deprecation window after S4). **That is revised:** Ray clustering is effective and stays the
default distributed substrate indefinitely. Sail is an **additive** scale-out option that can
be *supercharged* (the R1 native Arrow UDF, etc.), not a retirement target. Concretely:
- **No Ray retirement.** Drop the "replace the Ray distributed stack" / deprecation-window
  language below. Ray remains a first-class, supported, default path.
- **R5 reframes** from "Ray retirement + wiring" to "add `backend="sail"` as an *additional*
  opt-in surface" — Ray is untouched.
- **S4 still binds**, but its verdict is "Sail proven as an additive multi-node option"
  (completes where one-box can't, per-node RSS bounded, wall scales with nodes), NOT "Ray is
  now removable."
- The `mode` default-flip question is unchanged (still gated on its own evidence; unrelated to
  Ray's status).

Everything below is the original 2026-06-03 record, kept for the audit trail; read it through
this amendment.

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
**Classification:** decision/accepted • **Last updated:** 2026-06-15 (amended)
