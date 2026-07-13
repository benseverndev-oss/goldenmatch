# GitHub Issues Audit Report
**Date:** 2026-07-12  
**Repository:** benseverndev-oss/goldenmatch  
**Total Open Issues:** 34

---

## Executive Summary

The open issues queue contains a mix of:
- **2 bugs** (RUSTSEC dependency + autoconfig precision regression)
- **3 active blockers** on autoconfig PR2b rollout (#1316, #1317, #1319)
- **5 epic roadmap items** (dedup, agent-memory, fraud-aml, pprl, cdp-mdm)
- **18 epic subtasks** distributed across roadmap initiatives
- **3 performance/correctness issues** (distributed scoring, Layer-2 identity, config-healer)

### Key Findings

| Category | Count | Status | Priority |
|----------|-------|--------|----------|
| **Bugs (Blocking)** | 2 | Active | 🔴 P0 |
| **Autoconfig PR2** | 3 | Active | 🔴 P0 |
| **Roadmap Epics** | 5 | Planned | 🟡 P1 |
| **Roadmap Subtasks** | 18 | Backlog | 🟡 P1 |
| **Performance/Correctness** | 3 | Backlog | 🟡 P1 |
| **Productization** | 1 | Planned | 🟡 P1 |

---

## Category Breakdown

### 🔴 Critical Path (P0)

#### Bugs (2 issues)

**#1164** — 21 days old  
Track: bump pyo3 → 0.29 in pyarrow-coupled native crates once arrow-pyarrow 60 ships (RUSTSEC-2026-0176/0177)
- **Labels:** bug, dependencies
- **Status:** Waiting for arrow-pyarrow 60 release
- **Action:** Track upstream; pre-plan maturin/pyo3 migration once available

**#1207** — 20 days old  
Auto-config under-blocks and precision-collapses on null-sparse multi-source person data
- **Labels:** bug, autoconfig
- **Author:** benzsevern-mjh
- **Impact:** Affects precision on sparse nullability patterns
- **Related:** #1316, #1317, #1319 are PR2 fixes for this

---

#### Autoconfig PR2 Chain (3 issues)

**#1316, #1317, #1319** — All 13 days old, created post #1207 triage  
Subtasks for PR2b precision-anchor controller implementation:
- #1316: Reconcile per-identifier blocking union with learned blocking at >=50k rows
- #1317: TS parity — port #1207 per-identifier blocking union to buildBlocking (TypeScript)
- #1319: PR2b precision-anchor controller rule — measure PR2a sufficiency before (re)building

**Action Items:**
1. Complete #1316 (Python) reconciliation
2. Complete #1317 (TypeScript parity)
3. Complete #1319 (PR2b implementation)
4. Regression test #1207 on null-sparse multi-source dataset

---

### 🟡 High Priority / Planned (P1)

#### Roadmap Epics (5 issues)

**#1080** — 23 days old  
[Epic] Training-Data Dedup at Scale (throughput tier)
- Related issues: #1082, #1084, #1085
- **Subtasks:** Document/text near-dup blocking, distributed billion-scale, corpus-dedup surface

**#1073** — 23 days old (last update: 2026-07-09)  
[Epic] Agent Memory & Identity Layer (production-grade)
- Related issues: #1074, #1075, #1077, #1078, #1079
- **Subtasks:** Durable session store, identity ops, confidence propagation, shared memory, eval harness

**#1094** — 23 days old  
[Epic] Fraud / AML / KYC / Sanctions Screening (compliance-grade)
- Related issues: #1095, #1096, #1097, #1098, #1099, #1100
- **Subtasks:** Screening API, PEP list ingestion, graph analytics, explainability, audit, case mgmt

**#1101** — 23 days old  
[Epic] PPRL & Data Clean Rooms (multi-party, true SMC, hosted)
- Related issues: #1102, #1103, #1104, #1105, #1106, #1107
- **Subtasks:** Multi-party protocol, SMC backend, key mgmt, orchestration, audit, clean-room eval

**#1108** — 23 days old  
[Epic] CDP / MDM Identity Resolution (streaming, cross-channel, stewarded)
- **Status:** Blocked until Agent Memory foundation complete
- **Strategy:** Layer-2 incremental resolution (#966) feeds this

---

#### Roadmap Subtasks (18 issues)

**PPRL (6 issues):**
- #1102: Multi-party (3+) linkage protocol
- #1103: True SMC backend
- #1104: Key management + rotating salts
- #1105: Clean-room orchestration service
- #1106: Linkage audit + cross-party disclosure log
- #1107: Clean-room eval + leakage test suite + demo

**Fraud/AML (6 issues):**
- #1095: One-to-many screening API
- #1096: Sanctions / PEP list ingestion + refresh
- #1097: Graph risk analytics
- #1098: Compliance-grade explainability + match-decision audit
- #1099: Fraud-ring / shell-company detection
- #1100: Case management + SAR-ready audit export

**Agent Memory (5 issues):**
- #1074: Durable agent session & task store
- #1075: Agent-writable identity operations
- #1077: Confidence & uncertainty propagation + escalation
- #1078: Multi-agent shared memory + provenance + audit log
- #1079: Agent-memory eval harness + demo

**Dedup (3 issues):**
- #1082: Document / text near-dup blocking path
- #1084: Distributed billion-scale dedup
- #1085: Corpus-dedup product surface

---

#### Productization (1 issue)

**#1231** — 19 days old  
GoldenSheet: spreadsheet-native surface (Google Sheets Add-on + Excel add-in) for the matching engine
- **Status:** Strategic product surface (post-core delivery)
- **Blockers:** Requires stable core matching + identity APIs

---

### 🟠 Backlog / Performance Correctness (P1)

**#957** — 28 days old  
Distributed scoring under-utilizes the cluster (~6 of 80 CPU at 100M) — Ray Data concurrency cap
- **Author:** benzsevern-mjh
- **Issue:** Ray worker pool saturation under 100M candidate pairs
- **Action:** Profile Ray scheduler; validate ray_remote_args concurrency tuning

**#966** — 28 days old  
Sail identity: Layer-2 incremental resolution (absorb/merge against an existing store)
- **Status:** Foundation for #1108 (CDP/MDM) epic
- **Related:** Feeds Agent Memory identity layer (#1073)

**#1404** — 9 days old (most recent)  
Bound the config-healer loop cost (verify fan-out + per-iteration profiling)
- **Status:** Active investigation
- **Action:** Profile config-healer iteration cost; set cost bound guard

---

## Age & Staleness Analysis

| Age Range | Count | Status |
|-----------|-------|--------|
| 0–10 days | 1 | Active (#1404) |
| 11–20 days | 5 | PR2 chain + #1207 bug |
| 21–28 days | 28 | Roadmap backlog (created 2026-06-19) |

**Observation:** The 23-day cohort (roadmap epics + subtasks) represents the June 19 product roadmap snapshot. No staleness concern; these are intentionally backlogged.

---

## Dependency & Blocking Graph

```
#1207 (bug: autoconfig precision)
  └─ #1316 (Python reconciliation)
       └─ #1317 (TS parity)
            └─ #1319 (PR2b implementation)

#1073 (Agent Memory epic)
  └─ #1074, #1075, #1077, #1078, #1079 (subtasks)
       └─ #1108 (CDP/MDM epic) [blocked]

#1080 (Dedup epic)
  └─ #1082, #1084, #1085 (subtasks)

#1094 (Fraud/AML epic)
  └─ #1095–#1100 (6 subtasks)

#1101 (PPRL epic)
  └─ #1102–#1107 (6 subtasks)

#966 (Layer-2 identity)
  └─ #1108 (CDP/MDM) [feeds input]

#957 (Ray concurrency)
  → Standalone correctness issue

#1164 (pyo3 bump)
  → Blocked on arrow-pyarrow 60 release

#1231 (GoldenSheet)
  → Productization surface (post-core)

#1404 (config-healer)
  → Active performance profiling
```

---

## Recommendations

### Immediate Actions (This Sprint)

1. **Complete PR2b chain** (#1316 → #1317 → #1319)
   - Unblock precision regression test
   - Validate on null-sparse multi-source data

2. **Monitor #1164 (RUSTSEC dependency)**
   - Track arrow-pyarrow 60 release timeline
   - Pre-stage pyo3 0.29 + maturin migration plan

3. **Investigate #1404 (config-healer cost)**
   - Profile fan-out iterations
   - Set cost bound guard to prevent regressions

### Next Sprint

1. **Start #966 (Layer-2 identity)**
   - Foundation for #1108 (CDP/MDM)
   - Feeds Agent Memory epic #1073

2. **Triage #957 (Ray concurrency)**
   - Validate ray_remote_args configuration
   - Measure scaling at 100M+ candidate pairs

3. **Align on roadmap priorities** (6-month horizon)
   - **Agent Memory (#1073):** Q3 goal
   - **Fraud/AML (#1094):** Q3–Q4 goal
   - **PPRL (#1101):** Q4–Q1 goal
   - **Dedup (#1080):** Q2+ goal

### Longer-Term

- **#1231 (GoldenSheet):** Schedule post-core API stabilization (Q3+)
- **#1108 (CDP/MDM):** Unblock after #1073 + #966 complete

---

## Issue Hygiene

- ✅ All issues have clear titles
- ✅ Epics are labeled with `roadmap`
- ⚠️ Some subtasks lack explicit parent-issue references (consider adding GitHub issue links in descriptions for clarity)
- ✅ No duplicates detected
- ✅ Author attribution complete

---

**Report Generated:** 2026-07-12T22:26:00Z  
**Branch:** audit/gh-issues-review  
**Next Review:** Post-PR2b completion (~2026-07-15)
