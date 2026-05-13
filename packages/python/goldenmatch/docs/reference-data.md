---
layout: default
title: Reference Data
nav_order: 11
---

# Bundled Reference Data

GoldenMatch ships five OSS reference-data packs that auto-config picks up when your column names signal a known shape. No external downloads, no API keys, no extra install step — the data files live inside the `goldenmatch` wheel.

The packs add two scorers (`name_freq_weighted_jw`, `given_name_aliased_jw`) and three transforms (`legal_form_strip`, `address_normalize`, `naics_normalize`). The auto-config controller swaps them in automatically when a column matches the relevant name pattern AND the profiled data shape agrees.

---

## The five packs

| Pack | Source | Coverage | Adds |
|---|---|---|---|
| **Surnames** | US Census 2010 | Top 10,000 family names with frequency rank | `name_freq_weighted_jw` scorer |
| **Given names** | Public-domain alias corpus | ~140 alias relationships (William↔Bill, Robert↔Bob, Katherine↔Kate/Kathy) | `given_name_aliased_jw` scorer |
| **Business** | USPTO + curated legal-form list | ~30 corporate suffixes across English-speaking jurisdictions (Inc, LLC, Ltd, GmbH, S.A.) | `legal_form_strip` transform |
| **Addresses** | USPS Publication 28 | Street-suffix + secondary-unit abbreviations (Avenue→AVE, Apartment→APT) | `address_normalize` transform |
| **Industries** | US Census 2022 NAICS | 2,125 codes across all five hierarchy levels (sector → 6-digit US industry) | `naics_normalize` transform |

All packs are loaded lazily on first use. Missing-data fallback is built in — if a wheel build skips a data file, the relevant refinement becomes a no-op and the rest of the pipeline runs normally.

---

## Auto-config integration

The hook `goldenmatch.refdata.autoconfig_hooks.refine_matchkey_field(column_name, scorer, transforms, col_type)` fires once per matchkey field during `auto_configure_df()`. It returns a refined `(scorer, transforms)` tuple — or the input unchanged if no refdata pack applies.

**Refinement rules** (each gated on the relevant pack's `is_available()` AND on the profiled `col_type`):

| Column name pattern | Profiled `col_type` must be | Effect |
|---|---|---|
| `last_name`, `surname`, `lname`, `family_name`, ... | `name` / `multi_name` | Scorer becomes `name_freq_weighted_jw` |
| `first_name`, `given_name`, `fname`, `forename`, ... | `name` / `multi_name` | Scorer becomes `given_name_aliased_jw` |
| `company`, `business`, `org`, `firm`, `employer`, `legal_name`, `entity_name` | `name` / `multi_name` / `description` / `string` | `legal_form_strip` prepended |
| `address`, `street`, `addr_line`, `mailing_address`, `line_1`, ... | `address` / `string` | `address_normalize` prepended |
| `naics`, `sic`, `industry_code`, `business_type`, ... | `identifier` / `numeric` / `string` / `description` | `naics_normalize` prepended |

The `col_type` gate (PR #224) is the critical safety net: a column literally named `last_name` but holding numeric IDs (a mis-mapped warehouse load, for example) keeps its caller-specified scorer instead of being silently swapped to `name_freq_weighted_jw`, which would IDF-weight pairs of integers as if they were surnames.

Transforms are **prepended** rather than replaced — the existing `lowercase`/`strip` chain still runs after the refdata canonicalization, so blocking-key derivation downstream is unchanged.

A column that matches multiple patterns (e.g. `company_last_name`) gets multiple refinements: scorer swap from the `last_name` rule, transform prepend from the `company` rule.

---

## Scorers

### `name_freq_weighted_jw` — surname IDF-weighted Jaro-Winkler

Modulates plain Jaro-Winkler by the inverse document frequency of each surname in the US Census table. Common surnames (Smith, Johnson, Williams) get down-weighted in the borderline JW zone; rare surnames keep full credit.

```
jw = JaroWinkler.similarity(a, b)
if jw >= 0.95 or jw < 0.70:
    return jw                         # confident — no re-weighting
if either side is OOV in the bundled table:
    return jw                         # can't classify frequency
idf = mean(surname_idf(a), surname_idf(b))
weight = 0.6 + 0.4 * idf
return jw * weight
```

The borderline zone `[0.70, 0.95]` is where frequency evidence carries real discrimination. Outside the zone, plain JW is trusted directly so exact matches aren't degraded. The `0.6` floor ensures matches on Smith~Smyth still carry signal — they just don't score as high as matches on Hu~Xu.

Vectorized `score_matrix(values)` for hot-path NxN scoring uses one `rapidfuzz.cdist` + numpy mean/where rather than an O(N²) Python double-loop.

**Quality lift:** on the synthetic surname-FP fixture (200 TP pairs, 200 FP-candidate common-surname pairs, 600 distractor singletons), `name_freq_weighted_jw` lifts F1 from 0.667 (plain JW baseline) to 0.915 — recall stays at 1.0, precision goes 0.50 → 0.84.

### `given_name_aliased_jw` — alias-aware Jaro-Winkler

Same as plain JW, except known alias pairs (William↔Bill, Katherine↔Kate/Kathy, Robert↔Bob) score 1.0 regardless of edit distance.

```
if a and b are known aliases of the same canonical name:
    return 1.0
else:
    return JaroWinkler.similarity(a, b)
```

The scorer never *lowers* a JW score — it only promotes known aliases. Degrades cleanly to plain JW when the bundled alias table is missing.

---

## Transforms

### `legal_form_strip`

Removes corporate legal forms from the trailing position of a business name. Applied before scoring so `Acme Inc` and `Acme LLC` collapse to `acme` and match on the substantive name.

```
"Acme Inc"               → "acme"
"Beta Holdings, Ltd."    → "beta holdings"
"Gamma Corp"             → "gamma"
"Delta GmbH"             → "delta"
"Epsilon Pty Ltd"        → "epsilon"
```

Suffix table covers Inc, LLC, Ltd, Limited, Corp, Corporation, Co, Company, GmbH, AG, S.A., S.A.S., Pty, Pty Ltd, BV, NV, KG, OY, AB, SRL, plus their common abbreviations and punctuation variants. Case-insensitive; preserves casing of the remaining tokens after lowercasing for comparison.

### `address_normalize`

Canonicalizes street-suffix and unit abbreviations per USPS Publication 28, plus pre-tokenization rewrites for common notation quirks.

```
"123 Main Street #5"        → "123 main st apt 5"
"45 Maple Avenue"           → "45 maple ave"
"PO Box 100"                → "po box 100"
"678 Oak Blvd, Suite 200"   → "678 oak blvd ste 200"
```

Pre-tokenization rewrites handle apartment-hash notation (`#5` → `apt 5`) and PO Box variants (`P.O. Box`, `P O Box`) — without these, `#5` and `Apt 5` would canonicalize to different tokens and fail to match.

### `naics_normalize`

Canonicalizes US NAICS 2022 industry classifications. Accepts numeric codes, codes with trailing titles, and known industry titles — all map to a single canonical code.

```
"111110"                                    → "111110"
"111110 (Soybean Farming)"                  → "111110"
"NAICS 2022 code 511210"                    → "511210"
"Software Publishers"                       → "513210"   (canonical code for the title)
"Information"                               → "51"       (sector code)
"just a random description"                 → "just a random description"  (passthrough)
```

Numeric input scans every digit-run in the string and walks back through hierarchy prefixes — a vintage-year prefix like `2022` is skipped because no NAICS code resolves at any hierarchy level. Unknown 6-digit codes still normalize to digits-only, so two records sharing the same unknown code still match each other after the transform.

---

## Plugin enforcement

Both scorers and all three transforms are registered via `PluginRegistry` on `import goldenmatch.refdata`. Registration uses runtime `isinstance` checks against `ScorerPlugin` / `TransformPlugin` Protocols, so a duck-typed implementation missing a method fails at registration rather than deep inside a scoring loop.

`NameFreqWeightedJW` additionally satisfies the `VectorizedScorerPlugin` Protocol — `core/scorer._fuzzy_score_matrix` detects the vectorized method via `getattr` and uses it for NxN block scoring instead of falling back to a Python double-loop.

---

## Disabling

Refdata refinements are not configurable via YAML in v1 — they fire whenever the relevant column name pattern matches AND the profiled col_type agrees. To pin a different scorer or transform explicitly, set it on the matchkey field — refdata only refines auto-generated configs, never user-specified ones.

```yaml
# Explicit scorer wins; refdata won't override
matchkeys:
  - name: fuzzy_name
    type: weighted
    threshold: 0.85
    fields:
      - field: last_name
        scorer: jaro_winkler  # stays jaro_winkler, no refdata swap
        weight: 0.5
```

To verify what auto-config produced, dump the committed config:

```python
import goldenmatch as gm
config = gm.auto_configure_df(df)
print(config.model_dump_json(indent=2))
```

---

## Performance & extension points

- Each pack lazy-loads on first use. Module-level state is a `@dataclass(frozen=True)` with explicit fields, swapped atomically under a lock on reload — readers never see half-built state mid-rebuild.
- All five packs are pure-Python lookups; no native bindings, no network calls. Adding ~5-50ms of one-time load on first refdata-touching column, ~0ms steady-state.
- Extension hooks for v2:
  - **libpostal binding** under `reference-address-postal` — currently the address pack is rule-based; libpostal would handle international addresses.
  - **OpenCorporates company variants** — full registry-name aliasing, not just legal-form suffix stripping.
  - **Per-scorer threshold tuning** in Learning Memory — currently refdata scorers use the same 0.85 default as their plain counterparts.

---

## See also

- [Scoring](scoring) — full scorer reference
- [Configuration](configuration) — matchkey + transform schema
- [Pipeline](pipeline) — where the refinement hook fires
