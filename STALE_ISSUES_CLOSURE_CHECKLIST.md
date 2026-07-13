# Stale Issues Closure Checklist
**Date:** 2026-07-12  
**Audit:** `AUDIT_OPEN_ISSUES_2026-07-12.md`  
**Total to Close:** 28 issues (5 epics + 23 subtasks)  
**Recommendation:** Close as "deprioritized" with a single comment explaining the June 19 roadmap snapshot is stale.

---

## Closure Process

For each epic/subtask below:
1. Open the GitHub issue (`https://github.com/benseverndev-oss/goldenmatch/issues/<number>`)
2. Add a comment: "Deprioritized in July 2026 audit. June 19 roadmap snapshot no longer active. Re-open if this becomes prioritized again."
3. Click "Close as not planned" (or just "Close" if not-planned state isn't available)

---

## Roadmap Epics (5) — Close First

- [ ] #1080 — [Epic] Training-Data Dedup at Scale (throughput tier)
- [ ] #1094 — [Epic] Fraud / AML / KYC / Sanctions Screening (compliance-grade)
- [ ] #1101 — [Epic] PPRL & Data Clean Rooms (multi-party, true SMC, hosted)
- [ ] #1108 — [Epic] CDP / MDM Identity Resolution (streaming, cross-channel, stewarded)
- [ ] #1073 — [Epic] Agent Memory & Identity Layer (production-grade)

---

## Agent Memory Subtasks (5)

- [ ] #1074 — Durable agent session & task store
- [ ] #1075 — Agent-writable identity operations
- [ ] #1077 — Confidence & uncertainty propagation + escalation
- [ ] #1078 — Multi-agent shared memory + provenance + audit log
- [ ] #1079 — Agent-memory eval harness + demo

---

## Fraud / AML Subtasks (6)

- [ ] #1095 — One-to-many screening API
- [ ] #1096 — Sanctions / PEP list ingestion + refresh
- [ ] #1097 — Graph risk analytics
- [ ] #1098 — Compliance-grade explainability + match-decision audit
- [ ] #1099 — Fraud-ring / shell-company detection
- [ ] #1100 — Case management + SAR-ready audit export

---

## PPRL / Data Clean Rooms Subtasks (6)

- [ ] #1102 — Multi-party (3+) linkage protocol
- [ ] #1103 — True SMC backend
- [ ] #1104 — Key management + rotating salts
- [ ] #1105 — Clean-room orchestration service
- [ ] #1106 — Linkage audit + cross-party disclosure log
- [ ] #1107 — Clean-room eval + leakage test suite + demo

---

## Dedup Subtasks (3)

- [ ] #1082 — Document / text near-dup blocking path
- [ ] #1084 — Distributed billion-scale dedup
- [ ] #1085 — Corpus-dedup product surface

---

## Summary

**Before Closing:**
- Keep #1164, #1207, #1316, #1317, #1319, #957, #966, #1404, #1231 open (active priorities)
- Verify no external references to these 28 issues in other repos

**After Closing:**
- 34 → 6 active issues (82% reduction in open queue noise)
- Easier triage for actual blocking work
- New roadmap efforts will generate fresh issues with current context

---

**Status:** Ready for manual closure  
**Estimated Time:** ~5–10 min (28 issues × 10–20 sec per close action)
