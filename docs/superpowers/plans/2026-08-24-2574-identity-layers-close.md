# Closing #2574: identity-layers detector — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close #2574 by driving the identity-layer detector's *measured* residual error to zero on the corpora that exist, and by settling — with a measurement, not an argument — whether value-aware detection has any headroom left to justify building it.

**Architecture:** The detector already ships (`infermap/layers.py`, Rust kernel authoritative, TS + WASM mirrors, consumer `goldenmatch/core/segments.py`). Four of the issue's five filed gaps are closed. This plan attacks the two *measured* failures, both of which are name-side, then gates the remaining name-only gap on evidence rather than on the issue's premise.

**Tech Stack:** Python (`infermap`, `goldencheck-types`), Rust (`infermap-core` / `-native` / `-wasm`), TypeScript (`packages/typescript/infermap`), YAML domain packs.

**Spec:** none. #2574's own "Next gate" is a design spec via the brainstorming gate. **Part C below does not start until that spec exists** — and Part B decides whether it is worth writing. Parts A and B are bounded work on shipped code and need no spec.

---

## Status (updated 2026-08-25, after executing Part A)

**Part A is complete.** All three tasks landed; two of them did not go the way
this plan predicted, and the deviations are the interesting part.

| task | outcome |
|---|---|
| A1 `claim` role | shipped, **but the YAML-only fix was not sufficient** |
| A2 stop-list audit | shipped as a **measured decline** — 206 tokens, none declared |
| A3 short qualifiers | shipped, `initial` **0% -> 62%**, pairwise F1 **0.08 -> 0.88** |

**A1 needed a second change this plan did not anticipate.** Declaring `claim`
took the FK corpus to 100% and simultaneously regressed precision-corpus
partition sensitivity 100% -> 86%, because a lone `claim_id` then opened a
one-column party. Auditing the corpus showed that was not a labelling slip:
all 6 positives leave exactly one column uncovered, always the fact's own key.
The rule satisfying both corpora is that a single-column group is viable only
when the token IS the whole column (`bank`, `lender`) — a kernel change across
all four surfaces, which A1 was scoped to avoid. Cost: a lone `plant_code` no
longer reports a `place` party.

**A2 declined to act, which was the point of measuring.** 206 suppressed
tokens across 16 packs. The overwhelming majority should stay suppressed
(`rate`, `price`, `mileage`, `sqft`, `gpa`), and with both corpora at 100%
nothing in the set is costing measurable accuracy — so no instrument can say
which of the plausible ones (`order`, `shipment`, `purchase`, `enrollment`)
deserve a declaration.

**A3's residual sharpens Part B.** The `initial` convention's remaining 3 of 8
is provably irreducible from column names: those are exactly the schemas whose
entity initials collide (`provider`/`patient` -> `p`). Pairwise recall stays
at 1.0, so the failure mode is two parties fused, never one wrongly split.
**This is a concrete, mechanically-labelled instance of the name-blind shape
Part B was written to go looking for** — and a far sharper motivation for
value-aware detection than the issue's own `routing number -> bank` example,
because here the columns exist and only the qualifier is ambiguous. Part B1
should score against it directly.

Not run locally: the TypeScript parity suite (`node_modules` absent in the
worktree, and `pnpm install` is not a step to take casually here). The TS
mirror is a line-for-line port of the Rust kernel and reads the same re-pinned
oracle; CI runs it.

---

## Why this plan is not the plan the issue describes

The issue's 2026-08-23 status comment says gap 4 — "detection is name-only" — is "untouched, and it is the load-bearing gap", and that the raw value signal already exists in the packs. **Both halves are wrong against current `main`, and I checked before planning.**

**The value vocabulary does not exist.** Pack `value_signals` are profiling-*shape* hints — `min_unique_pct: 0.90`, `short_strings: true`, `mixed_case: true`, `numeric: true`, `max_unique: 20`. Nothing there can tell a routing number from any other short string. The only real value-pattern vocabulary in the repo is `scorers/pattern_type.py`'s eight `SEMANTIC_TYPES` (email, uuid, date_iso, ip_v4, url, phone, zip_us, currency) — **not one of which identifies a party.** Wiring the existing signal into detection is not a smaller version of this work; the signal that would be wired is not there. It has to be created.

**The measured residual is name-side, and values would not fix either failure.** Running `scripts/layers_fk_groundtruth.py` on `main` today:

```
convention     n    exact  pair-F1  pair-P  pair-R  cnt err
full           8     88%     0.98    1.00    0.96     0.12
abbrev4        8    100%     1.00    1.00    1.00     0.00
suffix         8     88%     0.98    1.00    0.96     0.12
initial        8      0%     0.08    0.05    0.25     2.62
```

Two failure modes, both root-caused:

1. **`claims_ledger` (full + suffix), the only per-case miss.** The three dimension parties are detected *perfectly*; the FACT's own columns are dropped:

   ```
   provider   kind=person       score=0.933  affix+role_hint  [provider_name, provider_npi, provider_specialty]
   patient    kind=person       score=0.900  affix+role_hint  [patient_firstname, patient_lastname, patient_birthdate]
   payer      kind=organization score=0.725  affix+role_hint  [insurer_name, insurer_naiccode]
   unassigned: ['claim_claimnumber', 'claim_lossdate', 'claim_paidamount']
   ```

   Confirmed cause: healthcare's `claim_status` / `claim_notes` field-type hints tokenize to include `claim`, which puts `claim` in `_layers_core_pure`'s stop set; `stop -= role_tokens` cannot rescue it because **no pack declares `claim` as a role**. `abbrev4` passes only because `clai` is not a type hint. This is a domain-pack data gap, not an algorithm gap. **Values are irrelevant to it** — the name says `claim` in plain text.

2. **The `initial` convention (25% of all cases, `pair-F1 0.08`).** A one-character qualifier is below `_MIN_QUALIFIER_LEN = 3`, so the affix signal has nothing to work with. **Values do not fix this either**: TPC-H's `c_name`/`s_name` and `c_acctbal`/`s_acctbal` are customer and supplier, and those two populations hold structurally identical values. No value profile separates them. What separates them is the qualifier the detector currently refuses to look at.

So the honest reading is: **the load-bearing gap is the one nobody filed.** That does not prove value signals are worthless — it proves *this corpus cannot see them*, because every column in it is renamed with a party qualifier by construction. The shape value signals exist for (an unqualified `npi` or `routing_number` column) never occurs in it. Part B builds the corpus that can see that shape before anyone builds the detector for it.

## Gap ledger, re-verified against `main` (2026-08-24)

| # | gap as filed | actual state |
|---|---|---|
| 1 | roles not first-class | **closed.** `RoleSpec` in `goldencheck_types/types.py`, `roles:` blocks in the packs, closed `IDENTITY_KINDS` = {person, organization, asset, place, unknown} as the axis matching keys off, `UNKNOWN_ROLE` sentinel, cross-vertical generic vocabulary unioned in by `_with_generic_roles` |
| 2 | one-domain-per-dataset, winner-take-all | **closed.** `detect_identity_layers` returns every candidate layer |
| 3 | 1:1 assignment constraint | **dissolved, not deferred.** Layer detection is a labelling pass over column names, many-to-many by construction; it never reaches `engine.py`. The status comment calls this "untouched", which reads as open work — it is a non-issue |
| 4 | detection is name-only | **open, premise wrong.** See above |
| 5 | no spec / API / tests | **closed.** Module, public API, Rust kernel, TS + WASM mirrors, three scored corpora |

## Global constraints

- **Four surfaces, one semantics.** `infermap-core::detect_identity_layers` is the source of truth; `_layers_core_pure`, `packages/typescript/infermap/src/core/layers.ts`, and the WASM binding must stay byte-identical. `test_native_parity.py` and `layers-parity.test.ts` pin this. Any scoring change lands in all four in the same PR.
- **Smart pipe, dumb kernel.** Host-side code loads packs and flattens them to plain lists (`_pack_inputs`); the kernel never learns about YAML. Any value signal must be reduced host-side to a plain per-column input before it crosses into the kernel.
- **Scores stay unrounded.** Python banker's rounding, Rust half-away-from-zero and JS `Math.round` disagree; rounding manufactures cross-language divergence.
- **`MIN_SPECIFICITY = 1.0` is not negotiable downward.** "Never invents a party on a single-entity table" is what makes a segmentation trustworthy for #2575. A sensitivity gain that costs specificity is a regression, not a trade.
- **Ratchets are floors, not targets.** `MIN_EXACT_PARTITION = 0.91`, `MIN_PAIRWISE_F1 = 0.98`, `MIN_SPECIFICITY = 1.0`, `MIN_SENSITIVITY_PARTITION = 1.0`. Raise them when a task moves them; never lower one without the reason in the diff.
- **`detect_domain` behaviour is untouched.** `goldenpipe.stages.infer_schema` depends on it.
- **Fail-open at the consumer.** `goldenmatch/core/segments.py` degrades every failure to `[]`, and `[]` means "no information", never "one population". Nothing added here may make InferMap a hard GoldenMatch dependency.
- Branch off freshly-fetched `origin/main`. Squash-merge PR per repo SOP; Ben arms his own merges.
- New public Python symbol → regen the codemap in the same branch, or two checks go red.
- New test files shift `pytest-split` shards; expect unrelated-looking shard failures and read them as shard shift, not as breakage.
- Rust: `cargo clippy -D warnings` on the native extension, and `rustfmt` the touched files **by name**.

---

# PART A — close the measured error (no spec needed)

## Task A1: declare `claim` as a role so the fact's own party stops being dropped

**Files:**
- Modify: `packages/python/goldencheck-types/goldencheck_types/_domains/healthcare.yaml` (`roles:` block)
- Modify: `packages/python/goldencheck-types/goldencheck_types/_domains/insurance.yaml` (`roles:` block)
- Test: `packages/python/infermap/tests/test_layers.py`
- Ratchet: `packages/python/infermap/tests/test_layers_fk_groundtruth.py`

**Interfaces:**
- Consumes: `RoleSpec(name, kind, name_hints, typical_types)`; `_with_generic_roles`'s "the vertical wins collisions" ordering
- Produces: nothing new in code — a pack data change that `_pack_inputs` already reads

- [ ] **Step 1: Write the failing test**

```python
def test_fact_party_survives_a_colliding_field_type_hint():
    """`claim` is a party qualifier AND a field-type hint token. The role
    declaration must win, or the fact's own columns are silently unassigned."""
    from types import SimpleNamespace
    from infermap import detect_identity_layers

    df = SimpleNamespace(columns=[
        "claim_claimnumber", "claim_lossdate", "claim_paidamount",
        "insurer_name", "insurer_naiccode",
        "provider_name", "provider_npi", "provider_specialty",
        "patient_firstname", "patient_lastname", "patient_birthdate",
    ])
    result = detect_identity_layers(df)
    roles = {layer.role for layer in result.layers}
    assert "claim" in roles
    assert result.unassigned == []
```

- [ ] **Step 2: Run it and watch it fail**

```
cd packages/python/infermap && pytest tests/test_layers.py::test_fact_party_survives_a_colliding_field_type_hint -v
```
Expected: FAIL — `assert "claim" in roles`, with `unassigned == ['claim_claimnumber', 'claim_lossdate', 'claim_paidamount']`.

- [ ] **Step 3: Declare the role**

In `healthcare.yaml` under `roles:` (and the equivalent in `insurance.yaml`):

```yaml
  claim:
    kind: asset
    name_hints: ["claim", "case", "encounter"]
    typical_types: ["claim_status", "claim_notes"]
```

`kind: asset` because a claim is a thing that is matched and survivorship-resolved, not a person or an organization — and `IDENTITY_KINDS` is closed, so `asset` is the only honest member. Record that reasoning in a YAML comment above the block; the next person will ask.

- [ ] **Step 4: Re-run the unit test and the FK harness**

```
pytest tests/test_layers.py::test_fact_party_survives_a_colliding_field_type_hint -v
PYTHONPATH="<repo>/packages/python/infermap;<repo>/packages/python/goldencheck-types" python scripts/layers_fk_groundtruth.py
```
Expected: unit test PASSes; `full` and `suffix` both go 88% → 100%, detectable-convention exact 92% → 100%.

- [ ] **Step 5: Raise the ratchet to what was just measured**

In `test_layers_fk_groundtruth.py`, `MIN_EXACT_PARTITION = 0.91` → `0.99`. Set the floor just below the measured value, per the existing comment about floors derived from displayed figures. Note in the diff that the corpus has now saturated on detectable conventions and the discriminating signal has moved to Part A2 and the blind-label corpus.

- [ ] **Step 6: Check the specificity corpus did not move**

```
python scripts/layers_precision.py
```
Expected: specificity 1.0, sensitivity 1.0, unchanged. A new role hint widens what can open a party, so this is the check that it did not start inventing them.

- [ ] **Step 7: Commit**

```bash
git add packages/python/goldencheck-types/goldencheck_types/_domains/healthcare.yaml \
        packages/python/goldencheck-types/goldencheck_types/_domains/insurance.yaml \
        packages/python/infermap/tests/test_layers.py \
        packages/python/infermap/tests/test_layers_fk_groundtruth.py
git commit -m "fix(layers): declare claim as a role so a fact's own party is not stop-listed (#2574)"
```

**Note for the executor:** this is a *class* of bug, not one token. Any token that is both a party qualifier and a substring of some field-type hint is suppressed the same way. Do not sweep speculatively — Task A2 measures the class before anyone widens the packs on a hunch.

## Task A2: measure the stop-list collision class, then decide

**Files:**
- Create: `scripts/layers_stoplist_audit.py`
- Create: `docs/measurements/2026-08-XX-layers-stoplist-collisions.json`

**Interfaces:**
- Consumes: `load_domain`, `_pack_inputs`, `_with_generic_roles`, `_ATTRIBUTE_TOKENS`, `_tokens`
- Produces: `run() -> dict` with `collisions: [{domain, token, suppressed_by, is_declared_role}]`

- [ ] **Step 1: Write the audit**

For every pack: build `role_tokens` and `stop` exactly as `_layers_core_pure` does, then report every token that (a) appears in some type hint, (b) is ≥ `_MIN_QUALIFIER_LEN`, and (c) is **not** in `role_tokens` — i.e. every token a schema could plausibly use as a party qualifier and this pack would silently swallow. Emit `--json`.

- [ ] **Step 2: Run it and commit the output under `docs/measurements/`**

Per repo practice: commit the harness and its output, not a scratchpad run. This is the evidence for whichever call step 3 makes.

- [ ] **Step 3: Decide from the output, in the PR description**

- **Few collisions and each is obviously a party** (the `claim` shape): declare them as roles, same as A1, and re-run both corpora.
- **Many collisions, mostly not parties**: do NOT bulk-declare. Record the list as a known limitation with the measurement behind it and stop. A stop-list that suppresses real parties is better than a role vocabulary that invents them — `MIN_SPECIFICITY = 1.0` is the constraint that decides this, and it decides it the same way every time.

- [ ] **Step 4: Commit**

```bash
git add scripts/layers_stoplist_audit.py docs/measurements/2026-08-XX-layers-stoplist-collisions.json
git commit -m "feat(layers): audit which party qualifiers the field-type stop-list swallows (#2574)"
```

## Task A3: the single-character qualifier blind spot

This is 25% of the FK corpus at `pair-F1 0.08` — by far the largest measured error, and the plan's biggest single win if it lands. It is also the one most likely to cost specificity, so it is gated on that and may correctly end in a "no".

**Files:**
- Modify: `packages/rust/extensions/infermap-core/src/lib.rs`
- Modify: `packages/python/infermap/infermap/layers.py` (`_layers_core_pure`, `_group_is_viable`)
- Modify: `packages/typescript/infermap/src/core/layers.ts`
- Test: `packages/python/infermap/tests/test_layers.py`, `tests/test_native_parity.py`, `packages/typescript/infermap/tests/unit/layers-parity.test.ts`
- Ratchet: `test_layers_fk_groundtruth.py`, `test_layers_precision.py`

**Interfaces:**
- Consumes: `_MIN_QUALIFIER_LEN`, `_group_is_viable(token, members, role_tokens) -> bool`
- Produces: no signature change. A short qualifier becomes admissible only under a **frame-level** condition, so the change is inside `_layers_core_pure`'s grouping loop, not in the per-group predicate — which means it must be expressed identically in the Rust kernel and the TS mirror.

**The proposed condition — a *clean partition*, not a lower floor.** Admit qualifiers below `_MIN_QUALIFIER_LEN` only when, taken together, they partition the frame: every column carries a short leading qualifier, the set of distinct short qualifiers is small relative to the column count, and each group has ≥ 2 members with distinct remainders. TPC-H's `c_`/`s_`/`p_`/`o_` satisfies all three. A table with one stray `x_flag` does not, so nothing opens. This is deliberately a whole-frame gate: lowering `_MIN_QUALIFIER_LEN` on its own would let `f_`/`x_` noise open parties everywhere and would take specificity down with it.

- [ ] **Step 1: Write the failing test — both directions, in one commit**

```python
def test_single_char_qualifiers_partition_a_tpch_style_frame():
    from types import SimpleNamespace
    from infermap import detect_identity_layers
    df = SimpleNamespace(columns=[
        "c_name", "c_address", "c_phone", "c_acctbal",
        "s_name", "s_address", "s_phone", "s_acctbal",
        "o_orderkey", "o_orderdate", "o_totalprice",
    ])
    layers = detect_identity_layers(df).layers
    groups = sorted(sorted(layer.columns) for layer in layers)
    assert len(groups) == 3
    assert ["c_acctbal", "c_address", "c_name", "c_phone"] in groups

def test_a_stray_short_prefix_does_not_open_a_party():
    """The specificity half. One `x_` column is noise, not a population."""
    from types import SimpleNamespace
    from infermap import detect_identity_layers
    df = SimpleNamespace(columns=[
        "customer_name", "customer_address", "customer_phone", "x_flag",
    ])
    layers = detect_identity_layers(df).layers
    assert all(layer.role != "x" for layer in layers)
    assert len(layers) == 1
```

- [ ] **Step 2: Run both; expect the first to FAIL and the second to PASS**

The second passing now is the point — it is the regression guard, and it has to be green before and after.

- [ ] **Step 3: Implement in the Rust kernel first**

The kernel is the source of truth; writing Python first invites the mirror to be reverse-engineered from it. Add the frame-level pre-pass in `infermap-core::detect_identity_layers`, with the thresholds as named `const`s beside `MIN_QUALIFIER_LEN` so the Python and TS mirrors have something to name.

```bash
cargo test -p infermap-core
cargo clippy -p infermap-native -- -D warnings
rustfmt packages/rust/extensions/infermap-core/src/lib.rs
```

- [ ] **Step 4: Mirror into `_layers_core_pure` and `layers.ts`, then prove they agree**

```
pytest packages/python/infermap/tests/test_native_parity.py -v
pnpm --filter infermap test layers-parity
```

- [ ] **Step 5: Score both corpora**

```
python scripts/layers_fk_groundtruth.py
python scripts/layers_precision.py
```

**The gate, decided by the numbers and not by attachment to the change:** ship only if specificity stays at **1.0** and sensitivity stays at **1.0**. `initial` moving 0% → anything is the win; specificity slipping to 0.9 is a decline even if `initial` reaches 100%. If specificity drops, revert, and record the measurement in the issue as a characterised dead end — the same way #2748's `-sep` tiebreak was recorded.

- [ ] **Step 6: Re-pin the ratchets and remove the blind-spot assertion**

If it shipped: `initial` stops being excluded from the floors, and `test_single_char_qualifiers_are_a_known_blind_spot` is deleted rather than left asserting a stale fact. Set `MIN_EXACT_PARTITION` from the new all-conventions measurement.

- [ ] **Step 7: Commit**

```bash
git commit -m "feat(layers): admit single-char qualifiers when they cleanly partition a frame (#2574)"
```

---

# PART B — settle whether value signals have headroom (no spec needed; this produces one)

Part A exhausts what the current corpora can measure. Part B exists because those corpora **cannot see** the case gap 4 is about: every column in the FK corpus is renamed with a party qualifier by construction, so a column whose name carries no party never occurs. Building a value detector against corpora that cannot show it working is how a negative result gets mistaken for a positive one.

## Task B1: measure how often the name-blind shape actually occurs

**Files:**
- Create: `scripts/layers_nameblind_audit.py`
- Modify: `packages/python/infermap/tests/fixtures/layers_blind_labels.json` (unlabelled schemas only)
- Create: `docs/measurements/2026-08-XX-layers-nameblind-frequency.json`

- [ ] **Step 1: Define the shape precisely, in the script's docstring**

A column is **name-blind** when it lands in `unassigned`, or in an `UNKNOWN_ROLE` layer with `kind == "unknown"`, *and* its name contains no token that any pack declares as a role hint. Those are the only columns a value signal could rescue. Anything already correctly assigned is not headroom, however well values might have described it.

- [ ] **Step 2: Run it over every schema in the blind-label corpus and the FK corpus**

Report: name-blind columns as a fraction of all columns, and how many frames have ≥ 1. Do not run it only on schemas chosen for having the shape — that is the self-agreement failure the blind corpus exists to escape.

- [ ] **Step 3: Commit the harness and the output**

- [ ] **Step 4: The gate**

- **Name-blind fraction is negligible** — close #2574 with the negative result: the detector's residual error is name-side, values have nothing to rescue, and here is the measurement. **This is a legitimate close, not a failure**, and it is the more likely outcome given Part A's root causes. Record it as a characterised dead end so the next person does not re-derive it.
- **Name-blind fraction is material** — write the design spec (`docs/superpowers/specs/`) through the brainstorming gate, and only then Part C.

---

# PART C — value-aware detection (BLOCKED on B1's gate and on a spec)

Deliberately not written as bite-sized tasks. Its shape depends on what B1 measures and what the spec decides, and this repo has a recorded lesson about designing before measuring. Sized here so the cost is visible when the gate is reached:

1. **A party-identifier value vocabulary** — genuinely new. `SEMANTIC_TYPES`' eight generic patterns identify no party; pack `value_signals` are shape statistics. Needs routing/ABA, EIN, NPI, VIN, SSN, IBAN, LEI, DUNS — each with a checksum where one exists (ABA and NPI both have one; a checksum is what separates "9 digits" from "a routing number", and without it this signal will invent parties and take `MIN_SPECIFICITY` with it).
2. **Host-side profiling** — sampling values changes `detect_identity_layers`' cost profile, which its docstring currently pins to `detect_domain`'s. Sampling must be opt-in and must not regress the free `segments_from_schema` path.
3. **Kernel change across four surfaces** — one extra reduced input plus a fifth weight; `_W_BASE`/`_W_AFFIX`/`_W_ROLE`/`_W_TYPES` sum to 1.0 today, so adding a term is a rebalance, and every existing score moves. Every ratchet is re-derived.
4. **Kind inference for `UNKNOWN_ROLE` layers** — the highest-value piece and the one most defensible from values alone. `kind` is documented as the axis downstream matching keys off; a layer holding SSNs and given names is `person` whatever its qualifier was. Worth splitting into its own issue even if the rest is declined.
5. **A value-bearing corpus with mechanically-derived labels** — the hard part. Synthesizing values for the FK corpus reintroduces exactly the self-agreement weakness those corpora were built to escape, because whoever generates the values also decides what the detector should see.

---

# PART D — close the issue

- [ ] Post a closing comment carrying: the re-verified gap ledger, Part A's before/after numbers, and B1's gate outcome with its measurement.
- [ ] Correct the record on gaps 1 and 3 — the status comment reads them as open, and gap 3 was dissolved by design rather than deferred.
- [ ] File follow-ups for anything Part A characterised and declined (a stop-list collision list, a reverted single-char relaxation), so a measured dead end is not re-derived.
- [ ] If B1 gated to "spec it", #2574 closes anyway and the spec's issue is the successor — the detector is shipped and its measured error is zero; "values might help someday" is not this issue.
- [ ] Note the effect on #2575, which inherits this precondition and still carries the stale 2026-08-14 assessment.
