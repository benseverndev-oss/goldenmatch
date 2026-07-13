# Code Scanning Alert Triage
**Date:** 2026-07-12  
**Total Alerts:** 30 findings (organized by rule)

---

## 🔴 High-Priority Code Issues (Real Bugs)

### **py/path-injection** (62 findings) — CRITICAL
**Category:** Security (command injection / path traversal)  
**Severity:** Error  
**Impact:** Potential arbitrary file access / command execution  
**Files Affected:** Python code (likely in file handling / CLI commands)

**Action:**
1. Review all 62 instances for false positives
2. Identify real path-injection vulnerabilities
3. Add input validation / safe path constructors
4. Create focused GitHub issue: "Audit path-injection findings (#62)"

**Severity:** 🔴 P0 — Block merge if any are exploitable

---

### **py/log-injection** (28 findings) — IMPORTANT
**Category:** Security (log injection / information disclosure)  
**Severity:** Error  
**Impact:** Attacker-controlled data logged unsanitized → log parsing attacks, info leakage  
**Pattern:** `logger.info(user_input)` without sanitization

**Action:**
1. Batch review by log level (debug vs. error)
2. Identify user-controlled inputs in logs
3. Add sanitization / parameterized logging
4. Create GitHub issue: "Audit log-injection findings (#28)"

**Severity:** 🟡 P1 — Fix in current sprint

---

### **PinnedDependenciesID** (388 findings) — INFRASTRUCTURE
**Category:** Dependency pinning (CI/CD policy)  
**Severity:** Error  
**Impact:** Floating dependency versions → supply-chain risk, non-reproducible builds  
**Pattern:** Docker image tags, package manager versions without pinned SHAs

**Action:**
1. This is a GitHub Organization Policy check, not a code bug
2. Review `.github/workflows/` for unpinned base images
3. Pin all workflow container `@sha256:...` hashes
4. Document in CONTRIBUTING.md

**Severity:** 🟡 P1 — Plan for next sprint

---

## 🟡 Medium-Priority Issues

### **py/stack-trace-exposure** (4 + 10 JS = 14 findings)
**Category:** Information disclosure  
**Severity:** Error (Python), Warning (JavaScript)  
**Impact:** Stack traces leaked to users in error pages → aid reconnaissance  
**Common Pattern:** `except Exception as e: return str(e)` without sanitization

**Action:**
- Python: Filter sensitive stack traces from API responses
- JavaScript: Enable error sanitization in frontend error boundaries
- Issue: "Audit stack-trace-exposure findings"

**Severity:** 🟡 P1

---

### **TokenPermissionsID** (37 findings)
**Category:** CI/CD access control policy  
**Severity:** Error  
**Impact:** GitHub Actions token permissions too broad → workflow supply-chain risk

**Action:**
1. Audit all `.github/workflows/` files
2. Apply least-privilege token scopes (reduce default `contents: write` to what's needed)
3. Document per-workflow permission rationale

**Severity:** 🟡 P1

---

## 🟢 Low-Priority / Style Issues

### **py/insecure-temporary-file** (7 findings) — SECURITY
**Pattern:** `tempfile` without mode restrictions  
**Action:** Review and apply secure tempfile defaults

### **py/overly-large-range** (7 findings) — PERFORMANCE
**Pattern:** `range(1000000)` in loops → optimization opportunity  
**Action:** Backlog; profile first

### **py/clear-text-logging-sensitive-data** (3 findings) — SECURITY
**Pattern:** Passwords, tokens, PII in log output  
**Action:** Add sanitization / redaction layer

### **py/jinja2/autoescape-false** (1 finding) — SECURITY
**Pattern:** Jinja2 templates with `autoescape=False`  
**Action:** Find the one instance, flip to `True`, test

### **py/redos** (1), **py/http-response-splitting** (1) — SECURITY
**Action:** Audit once, likely one-off

### **js/polynomial-redos** (4) + **js/incomplete-multi-character-sanitization** (2) — REGEX
**Pattern:** Vulnerable regex patterns  
**Action:** Backlog; low runtime impact unless in hot path

### **js/insecure-temporary-file**, **js/file-access-to-http**, **js/regex/missing-regexp-anchor** (1 each) — LOW
**Action:** Backlog or suppress if false positive

### **Full-SSRF** (2 findings)
**Pattern:** Server-Side Request Forgery (external URL fetch without validation)  
**Action:** P1 security audit

---

## 📊 Summary by Category

| Category | Count | Priority | Action |
|----------|-------|----------|--------|
| **Path Injection** | 62 | 🔴 P0 | Create focused issue + review |
| **Log Injection** | 28 | 🟡 P1 | Batch sanitization + logging review |
| **Dependency Pinning** | 388 | 🟡 P1 | Update workflows (CI/CD) |
| **Stack Trace Exposure** | 14 | 🟡 P1 | Error handler sanitization |
| **Token Permissions** | 37 | 🟡 P1 | Least-privilege audit |
| **Temp Files (Secure)** | 7 | 🟡 P1 | Apply secure defaults |
| **SSRF** | 2 | 🟡 P1 | Input validation review |
| **Regex / Perf** | 12 | 🟢 P2 | Backlog |
| **Other (1–3 each)** | 10 | 🟢 P2–P3 | Review / suppress |

---

## 🎯 Recommended Action Plan

### Week 1 (Triage)
1. **Create focused GitHub issues:**
   - [ ] #path-injection-audit (62 findings, P0)
   - [ ] #log-injection-audit (28 findings, P1)
   - [ ] #dependency-pinning-workflows (388 findings, P1)
   - [ ] #stack-trace-exposure-sanitization (14 findings, P1)
   - [ ] #token-permissions-audit (37 findings, P1)

2. **Spot-check top 5 rules for false positives**
   - Export 3 examples per rule
   - Determine: real bug vs. false positive vs. style

### Week 2 (Fixes)
1. Fix P0 path-injection blocker
2. Batch log-injection fixes
3. Update GitHub workflow token scopes
4. Add error sanitization middleware

### Week 3+ (Backlog)
1. Regex optimization (low priority)
2. SSRF validation review (security, P1)
3. Periodic code scanning review (weekly)

---

## Suppression Policy

For **confirmed false positives**, use rule-level comment in `.github/codeql-config.yml`:

```yaml
disable-default-queries: false
queries:
  - uses: security-and-quality
paths-ignore:
  - "**/*_test.py"  # Tests don't need same rigor
rules:
  - id: js/polynomial-redos
    severity: note  # Lower priority if known safe
```

Or suppress inline with:
```python
# lgtm[py/path-injection]
open(user_path)  # Intentional: pre-validated by schema
```

---

## 📋 Template for First Issue

```markdown
## Code Scanning Audit: Path Injection (62 findings)

**Severity:** P0  
**Type:** Security (path traversal / command injection)  
**Finding Count:** 62

### Summary
Static analysis detected 62 instances of potential path-injection vulnerabilities 
(user-controlled input used in file operations without full validation).

### Action
- [ ] Export full alert list with file locations
- [ ] Audit top 10 high-confidence instances
- [ ] Determine real bugs vs. false positives
- [ ] Add input validation / safe path constructor
- [ ] Create follow-up issue per real finding

### Resources
- [CWE-22: Improper Limitation of a Pathname](https://cwe.mitre.org/data/definitions/22.html)
- [CodeQL py/path-injection](https://codeql.github.com/docs/codeql-overview/codeql-and-security-queries/)

### Related
- #1164 (RUSTSEC) — dependency security
- SECURITY_AUDIT_2026-07-12.md — full audit report
```

---

**Status:** 🟡 Triage complete, action plan drafted  
**Next:** Ben creates the 5 focused issues (path-injection, log-injection, etc.)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
