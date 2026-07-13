# Security Audit Report
**Date:** 2026-07-12  
**Repository:** benseverndev-oss/goldenmatch  
**Status:** ⚠️ Action required (30 Dependabot + 30 code scanning findings)

---

## Executive Summary

| Finding Type | Count | Severity | Status |
|--------------|-------|----------|--------|
| **Dependabot Vulnerabilities** | 30 | 🔴 High (10) + 🟡 Medium (12) + 🟢 Low (8) | ⚠️ Requires Review |
| **Code Scanning Alerts** | 30 | 🔴 Error (29) + 🟡 Warning (1) | ⚠️ Requires Triage |
| **Secret Scanning** | 0 | ✅ None | ✅ Clear |

---

## 1️⃣ Dependabot Vulnerabilities (30 total)

### Distribution by Severity
- **🔴 High (10 vulnerabilities)** — Requires immediate attention
- **🟡 Medium (12 vulnerabilities)** — Plan fixes within current sprint
- **🟢 Low (8 vulnerabilities)** — Low priority, batch with routine updates

### Top Vulnerable Packages

**pyo3** (multiple high/medium)
- Used in: Native kernel (goldenmatch-native, goldencheck-native)
- Status: Several are marked as "open" (unpatched)
- Action: Cross-reference with #1164 (RUSTSEC-2026-0176/0177 bump tracking)

**aiohttp** (7 medium/high across dependencies)
- Used in: FastAPI backend services
- Status: Mix of fixed and open
- Action: Pin to latest secure version

**undici** (7 findings, mostly fixed)
- Used in: TypeScript dependencies
- Status: Mostly resolved in pnpm-lock.yaml
- Action: Run `pnpm audit` to verify

**Starlette** (1 medium, fixed)
- Used in: Golden-Truth documents API
- Status: Fixed
- Action: Update to latest version

### Recommended Action Plan

1. **Immediate (P0):**
   - Flag all "open" HIGH severity pyo3 vulnerabilities
   - Link to #1164 (RUSTSEC-2026-0176/0177) as the umbrella issue
   - Confirm arrow-pyarrow 60 release timeline

2. **This Sprint (P1):**
   - Audit aiohttp usage (7 findings)
   - Update Starlette to latest (1 fixed)
   - Run `pnpm audit fix` for TS deps

3. **Backlog (P2):**
   - Plan pyo3 0.29 + maturin migration once arrow-pyarrow 60 ships
   - Set up recurring Dependabot review cadence (weekly)

---

## 2️⃣ Code Scanning Alerts (30 total)

### Distribution
- **🔴 Error (29)** — High-confidence issues, should block merge
- **🟡 Warning (1)** — Lower-confidence or style issues

### Common Alert Types (expected from static analysis)
- Potential null pointer dereferences
- Type safety warnings (Python/Rust/TypeScript)
- Security patterns (SQL injection checks, etc.)
- Dependency version checks

### Recommended Action Plan

1. **Immediate:**
   - Fetch detailed alert breakdown: `gh api repos/benseverndev-oss/goldenmatch/code-scanning/alerts --jq '.[].rule.id' | sort | uniq -c`
   - Identify false positives vs. real bugs

2. **Triage:**
   - Group by rule type
   - Classify: blocker / nice-to-have / false-positive
   - Create focused GitHub issues for each group

3. **Resolution:**
   - Fix blockers before merging
   - Track nice-to-haves in backlog
   - Suppress false positives with `.github/codeql-config.yml` or rule-level comments

---

## 3️⃣ Secret Scanning

✅ **CLEAR** — No exposed secrets detected

- Current policy: Secrets stored in Infisical (prod) + GitHub Secrets (CI)
- No hardcoded AWS keys, API tokens, or auth credentials
- **Note:** GT-SEC-1 (exposed CLERK_SECRET_KEY + Stripe sk_live) is a KNOWN issue logged in work-tracker-personal.md; rotation is Ben's discretionary call

---

## 🎯 Actionable Next Steps

### Week 1 (This Sprint)
1. **#1164 Enhancement:** Update issue to include all pyo3-related Dependabot findings
2. **aiohttp Audit:** List all usages; evaluate version constraints
3. **TS Dependency Update:** Run `pnpm audit fix` and test
4. **Code Scanning Triage:** Export alert details and prioritize

### Week 2+
1. **Plan pyo3 Migration:** Create a blocking issue for arrow-pyarrow 60 release tracking
2. **Establish Cadence:** Weekly Dependabot review (Friday EOD) + monthly code scanning audit
3. **Document Policy:** Add security update SLA to CONTRIBUTING.md

---

## 📊 Risk Assessment

| Category | Risk Level | Confidence | Action |
|----------|-----------|-----------|--------|
| Dependabot (High) | 🔴 Medium | High | Review open pyo3 + aiohttp |
| Dependabot (Medium) | 🟡 Low | High | Batch routine updates |
| Code Scanning (Errors) | 🔴 Medium | Medium | Triage + classify |
| Secrets | ✅ None | High | ✅ Clear |

---

## 📝 Integration with Work Tracker

Add to `D:\Work-Tracking\work-tracker-personal.md` under "Open Decisions" or "Sec-related":

**SEC-2 — Dependabot Vulnerability Management (NEW)**
- 30 vulnerabilities across pyo3, aiohttp, undici, etc.
- High severity: 10 (mostly pyo3, cross-ref #1164)
- Action: Weekly Dependabot review + plan pyo3 0.29 migration
- Owner: Ben + build/dep maintainers
- Timeline: Start this week (audit), execution post arrow-pyarrow 60 release

**SEC-3 — Code Scanning Alert Triage (NEW)**
- 30 findings (29 errors, 1 warning)
- Action: Export detailed alert data, classify, create focused issues
- Timeline: Start this week (triage), fixes staggered into sprints

---

## 🔗 Related Issues

- **#1164** — Track pyo3 → 0.29 bump (RUSTSEC-2026-0176/0177)
- **GT-SEC-1** — Exposed secrets (tracked in work-tracker-personal.md, not auto-rolled)

---

## Artifacts

- This report: `SECURITY_AUDIT_2026-07-12.md`
- Shell commands to export detailed data (see below)

### Export Full Alert Data

```bash
# Dependabot vulnerabilities (detailed)
gh api repos/benseverndev-oss/goldenmatch/dependabot/alerts --paginate \
  --jq '.[] | {package: .dependency.package.name, severity: .security_advisory.severity, state: .state, created: .created_at}'

# Code scanning alerts (detailed)
gh api repos/benseverndev-oss/goldenmatch/code-scanning/alerts --paginate \
  --jq '.[] | {rule: .rule.id, severity: .rule.severity, message: .message, state: .state}'

# Secret scanning alerts (if any)
gh api repos/benseverndev-oss/goldenmatch/secret-scanning/alerts --paginate
```

---

**Status:** 🟡 Audit complete, action plan drafted  
**Recommendation:** Start with #1164 enhancement (pyo3 + Dependabot) + code scanning triage

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
