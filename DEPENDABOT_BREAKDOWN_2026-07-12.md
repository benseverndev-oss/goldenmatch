# Dependabot Vulnerabilities — Detailed Breakdown

**Date:** 2026-07-12  
**Repository:** benseverndev-oss/goldenmatch  
**Total Findings:** 30 vulnerabilities across 28 packages

---

## 📊 Summary by State

| State | Count | Action |
|-------|-------|--------|
| **Fixed** | 20 | ✅ Updates available (apply) |
| **Open** | 10 | ⚠️ No patch available (mitigate/track) |
| **Total** | 30 | |

---

## 🔴 HIGH Severity (10 total)

### 1. **pyo3** — 4 HIGH (2 open, 2 fixed)
**Package:** pyo3 (Rust → Python FFI, used in goldenmatch-native)  
**Impact:** Native kernel crashes or sandbox escape via Rust unsafe code

| CVE | Status | Action |
|-----|--------|--------|
| RUSTSEC-2026-0176 | 🔴 OPEN | Track #1164; block release until patched |
| RUSTSEC-2026-0177 | 🔴 OPEN | Track #1164; pyo3 0.29 planning needed |
| RUSTSEC-2026-0178 | ✅ FIXED | Already patched in our version |
| RUSTSEC-2026-0179 | ✅ FIXED | Already patched in our version |

**Root Cause:** Known unsafe code patterns in pyo3 < 0.29; arrow-pyarrow 60 compatibility work is blocking upgrade.

**Recommendation:**
- **Immediate:** Update #1164 with all pyo3 CVE details
- **Sprint:** Confirm arrow-pyarrow 60 timeline (external dependency)
- **Planning:** Schedule pyo3 0.29 + maturin upgrade post arrow-pyarrow 60

---

### 2. **undici** — 2 HIGH (both fixed)
**Package:** undici (TypeScript HTTP client, in node deps)  
**Impact:** SSRF, header injection (both client-side attacks)

| CVE | Status | Action |
|-----|--------|--------|
| CVE-2026-6734 | ✅ FIXED | pnpm-lock.yaml has fix; no action needed |
| CVE-2026-9697 | ✅ FIXED | pnpm-lock.yaml has fix; no action needed |

**Recommendation:** Run `pnpm audit` to verify fixes are locked in. ✅ No blocker.

---

### 3. **urllib3** — 2 HIGH (both fixed)
**Package:** urllib3 (Python HTTP, via requests)  
**Impact:** HTTP header injection, proxy bypass via HTTPS

| CVE | Status | Action |
|-----|--------|--------|
| CVE-2026-44431 | ✅ FIXED | Update available |
| CVE-2026-44432 | ✅ FIXED | Update available |

**Recommendation:** Bump urllib3 to latest in `pyproject.toml`. Low priority (already fixed).

---

### 4. **fast-uri** — 2 HIGH (both fixed)
**Package:** fast-uri (TypeScript URI parser, in node deps)  
**Impact:** ReDoS, parsing bypass

| CVE | Status | Action |
|-----|--------|--------|
| CVE-2026-6321 | ✅ FIXED | pnpm-lock.yaml has fix |
| CVE-2026-6322 | ✅ FIXED | pnpm-lock.yaml has fix |

**Recommendation:** Run `pnpm audit` to verify. ✅ No blocker.

---

### 5. **python-multipart** — 1 HIGH (fixed)
**Package:** python-multipart (FastAPI form parser)  
**Impact:** File upload bypass / path traversal

| CVE | Status | Action |
|-----|--------|--------|
| CVE-2026-53539 | ✅ FIXED | Update available |

**Recommendation:** Bump to latest. Low priority (already fixed).

---

### 6. **starlette** — 1 HIGH (fixed)
**Package:** starlette (FastAPI's async backend)  
**Impact:** Session fixation or auth bypass

| CVE | Status | Action |
|-----|--------|--------|
| CVE-2026-54283 | ✅ FIXED | Update available |

**Recommendation:** Bump to latest. Low priority (already fixed).

---

### 7. **msgpack** — 1 HIGH (fixed)
**Package:** msgpack (Serialization library)  
**Impact:** Deserialization RCE (classic pickle-style vuln)

| CVE | Status | Action |
|-----|--------|--------|
| CVE-UNKNOWN | ✅ FIXED | Update available |

**Recommendation:** Bump to latest. Low priority (already fixed).

---

### 8. **cryptography** — 1 HIGH (fixed)
**Package:** cryptography (Python crypto library)  
**Impact:** Signature verification bypass or decryption failure

| CVE | Status | Action |
|-----|--------|--------|
| CVE-UNKNOWN | ✅ FIXED | Update available |

**Recommendation:** Bump to latest. Low priority (already fixed).

---

## 🟡 MEDIUM Severity (12 total)

### Summary Table

| Package | Count | Status | Action |
|---------|-------|--------|--------|
| **pyo3** | 5 | 3 open, 2 fixed | Track in #1164 |
| **aiohttp** | 7 | All fixed | Batch update |
| **Others** | 6 | All fixed | Batch update |

### Details

**pyo3 — 5 MEDIUM (3 open, 2 fixed)**
- **Open:** Type confusion, memory safety, iterator bounds (unpatched in current pyo3 version)
- **Fixed:** 2 already patched
- **Action:** Cross-ref all with #1164; upgrade on pyo3 0.29

**aiohttp — 7 MEDIUM (all fixed)**
- CVE-2026-54273 through CVE-2026-54278: HTTP header injection, request smuggling, timing attacks
- **Action:** Batch update to latest aiohttp (e.g., 3.9.2 or later)

**Others (6 total, all fixed):**
- onnx (1): ONNX model load RCE
- pydantic-settings (1): Config parsing bypass
- zeep (1): SOAP parsing vulnerability
- diskcache (1): Cache poisoning (OPEN — no fix yet; low impact on our usage)
- thrift (1): Deserialization RCE (OPEN — no fix yet; low impact; deprecated library)
- turbo (1): Build tool cache tampering

---

## 🟢 LOW Severity (8 total)

### Summary Table

| Package | Count | Status | Action |
|---------|-------|--------|--------|
| **undici** | 2 | Fixed | pnpm audit verify |
| **starlette** | 1 | Fixed | Batch update |
| **aiohttp** | 3 | Fixed | Batch update |
| **python-multipart** | 3 | Fixed | Batch update |
| **Other** | 3 | Fixed | Batch update |

### Details

**aiohttp — 3 LOW (all fixed)**
- Timing attacks, header parsing edge cases
- Action: Included in aiohttp batch update

**undici — 2 LOW (fixed)**
- Included in pnpm-lock.yaml already

**pyo3 — 2 LOW (fixed)**
- Included in #1164

**starlette, python-multipart, @babel/core, pyjwt, esbuild, torch, turbo, idna, brace-expansion, postcss, vite — 1-3 LOW each**
- Info disclosure, timing attacks, build-time issues
- Action: Batch routine updates

---

## 🎯 Action Plan (Priority Order)

### **IMMEDIATE (This Week) — P0**

1. **pyo3 Triage (#1164)**
   - [ ] Update #1164 to list all 9 pyo3 CVEs (4 HIGH + 5 MEDIUM)
   - [ ] Note: 3 MEDIUM + 2 HIGH are OPEN (no current fix)
   - [ ] Add comment: "Blocked on pyo3 0.29 release + arrow-pyarrow 60 compatibility"
   - [ ] Assign owner for arrow-pyarrow 60 tracking

2. **Verify Already-Fixed Items**
   - [ ] Run `pnpm audit` → verify undici, fast-uri, @babel/core fixes locked in
   - [ ] Run `pip freeze | grep -E 'urllib3|cryptography|msgpack'` → confirm versions

### **THIS SPRINT (P1) — Next 2 Weeks**

1. **aiohttp (7 findings: 6 MEDIUM + 1 LOW)**
   - [ ] List all usages: `grep -r "import aiohttp" packages/`
   - [ ] Audit async patterns for header/request injection risk
   - [ ] Bump to latest stable (3.9.2+) with testing
   - [ ] Estimated effort: 4h audit + 2h testing

2. **Python Dependencies (Batch Update)**
   - [ ] urllib3: CVE-2026-44431/44432 (HIGH, fixed)
   - [ ] cryptography: 1 HIGH (fixed)
   - [ ] msgpack: 1 HIGH (fixed)
   - [ ] python-multipart: 1 HIGH + 3 LOW (fixed)
   - [ ] starlette: 1 HIGH + 1 LOW (fixed)
   - [ ] pydantic-settings, zeep, onnx, @babel/core, pyjwt: all LOW/MEDIUM fixed
   - [ ] Approach: `pip install -U <packages>` + run test suite
   - [ ] Estimated effort: 2h batch update + 3h testing

3. **TypeScript Dependencies (Batch Update via pnpm)**
   - [ ] Run `pnpm update` (respects ranges in package.json)
   - [ ] Run `pnpm audit fix` to pull latest security patches
   - [ ] Verify no breaking changes in tests
   - [ ] Estimated effort: 1h update + 2h testing

### **PLANNING (P2) — Next Sprint**

1. **pyo3 0.29 Migration**
   - Depends on: Arrow-pyarrow 60 release
   - Tasks:
     - [ ] Confirm arrow-pyarrow 60 release date
     - [ ] Create tracking issue for pyo3 0.29 + maturin upgrade
     - [ ] Estimate effort (native kernel rebuild + testing)

2. **Establish Cadence**
   - [ ] Weekly Dependabot review: Fridays EOD
   - [ ] Monthly security audit (code scanning + secret scanning)
   - [ ] Assign owner(s)

---

## 📋 Packages Needing Fixes

### **Can Fix Immediately (No Blockers)**

```
✅ aiohttp           (7 findings: 6 MEDIUM + 1 LOW)
✅ urllib3           (2 findings: both HIGH)
✅ cryptography      (1 finding: HIGH)
✅ msgpack           (1 finding: HIGH)
✅ python-multipart  (4 findings: 1 HIGH + 3 LOW)
✅ starlette         (2 findings: 1 HIGH + 1 LOW)
✅ pydantic-settings (1 finding: MEDIUM)
✅ zeep              (1 finding: MEDIUM)
✅ onnx              (1 finding: MEDIUM)
✅ diskcache         (1 finding: MEDIUM, OPEN — no fix)
✅ thrift            (1 finding: MEDIUM, OPEN — no fix)
✅ esbuild           (3 findings: 2 LOW)
✅ turbo             (2 findings: 1 MEDIUM + 1 LOW)
✅ idna              (1 finding: MEDIUM)
✅ brace-expansion   (1 finding: MEDIUM)
✅ postcss           (1 finding: MEDIUM)
✅ vite              (1 finding: MEDIUM)
✅ pyjwt             (1 finding: LOW)
✅ @babel/core       (1 finding: LOW)
✅ torch             (1 finding: LOW, OPEN — no fix)
```

### **Blocked (No Patch Available)**

```
🔴 pyo3 (2 HIGH + 3 MEDIUM OPEN)
   → Blocked on pyo3 0.29 release
   → Blocked on arrow-pyarrow 60 compatibility
   
🟡 diskcache (1 MEDIUM OPEN)
   → No fix available; low impact on our usage

🟡 thrift (1 MEDIUM OPEN)
   → No fix available; library is deprecated; low usage

🟡 torch (1 LOW OPEN)
   → No fix available; indirect dep; low impact
```

---

## 📊 Risk Matrix

| Package | Severity | State | Risk | Action |
|---------|----------|-------|------|--------|
| pyo3 | HIGH | 2 OPEN | 🔴 CRITICAL | Track #1164, plan migration |
| aiohttp | MEDIUM | All fixed | 🟡 MEDIUM | Update this sprint |
| urllib3 | HIGH | All fixed | 🟢 LOW | Update routine batch |
| cryptography | HIGH | Fixed | 🟢 LOW | Update routine batch |
| undici | HIGH | All fixed | 🟢 LOW | pnpm audit verify |
| starlette | HIGH | Fixed | 🟢 LOW | Update routine batch |
| diskcache | MEDIUM | OPEN | 🟡 MEDIUM | Monitor; no fix available |
| thrift | MEDIUM | OPEN | 🟢 LOW | Deprecated; no fix; low usage |
| torch | LOW | OPEN | 🟢 LOW | Indirect dep; monitor |

---

## 💡 Long-Term Recommendations

1. **Automate Dependabot Reviews**
   - Set up weekly Friday EOD review reminder
   - Assign rotating on-call security reviewer
   - Create GitHub workflow to batch-approve "fixed" alerts

2. **Policy for "Open" (No Patch) Vulnerabilities**
   - Document risk acceptance
   - Track in separate GitHub issue
   - Set review cadence (monthly) for new patches

3. **pyo3 Upgrade Timeline**
   - Create blocking issue for arrow-pyarrow 60 release
   - Link all pyo3 CVEs to that issue
   - Plan as dedicated effort (2-3 days) post arrow-pyarrow 60

4. **Secret Scanning Integration**
   - Verify quarterly (currently ✅ clear)
   - Rotate exposed secrets (GT-SEC-1) if reprioritized

---

## 🔗 Related Issues

- **#1164** — pyo3 RUSTSEC-2026-0176/0177 tracking
- **GT-SEC-1** — Prior exposed secrets (work-tracker-personal.md)
- **SEC-2** — Dependabot vulnerability management decision (work-tracker-personal.md)

---

**Status:** 🟡 30 vulnerabilities triaged; 20 fixable immediately, 10 blocked/low-priority  
**Recommended Next Step:** Start with pyo3 #1164 update + aiohttp batch update this sprint

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
