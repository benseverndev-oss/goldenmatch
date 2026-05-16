# `map_elements` catalog — call site audit

**Status:** Catalog (drafted 2026-05-15, executes Step 1 of [`2026-05-15-map-elements-attack-design.md`](2026-05-15-map-elements-attack-design.md))
**Author:** Claude + bsevern
**Method:** `grep -rn "map_elements" packages/python/` across goldenmatch + goldenflow + goldencheck on the post-#239 main commit `48646bb`.

## Summary by package

| Package | Call sites | Category |
|---|---|---|
| `goldenflow/transforms/` | 52 | Per-row UDFs in `mode="series"` registered transforms. **Highest leverage.** |
| `goldenflow/domains/` | 7 | Domain-pack transforms (healthcare/finance/etc.) |
| `goldenflow/llm/corrector.py` | 1 | LLM-corrector (off the hot path; opt-in) |
| `goldenmatch/core/` | 10 | Standardize + matchkey fallbacks, validate, autofix, blocker hash, memory reanchor |
| `goldencheck/engine/fixer.py` | 5 | Quality-scan fixes |
| **Total live hot-path candidates** | **~70** | |

## The framework already has the cure

Per `packages/python/goldenflow/CLAUDE.md`:

> The `mode` field on `TransformInfo` controls how the engine applies a transform:
> - `"expr"`: pl.Expr → pl.Expr. **Pure Polars operations (strip, lowercase). Stays in Rust; fastest.**
> - `"series"`: pl.Series → pl.Series. **Python logic per column (phone parsing, date parsing). Uses `map_batches` internally.**
> - `"dataframe"`: ...

**Every single transform in `goldenflow/transforms/*.py` is declared `mode="series"` and uses `map_elements` internally — even when the actual operation is pure-native Polars.** That's the surgery: re-declare what can be `mode="expr"` and rewrite the body in `pl.Expr`.

## Attack targets — Tier 1 (easy native rewrites, `mode="series"` → `mode="expr"`)

Pure string operations with direct Polars equivalents. No Python needed at all. Probably 30-50% of total map_elements wall.

| File:line | Transform | Current body | Native equivalent |
|---|---|---|---|
| `email.py:27` | `email_lowercase` | `val.strip().lower()` | `pl.col(c).str.strip_chars().str.to_lowercase()` |
| `text.py:107` | `remove_html_tags` | `re.sub(r"<[^>]+>", "", val)` | `pl.col(c).str.replace_all(r"<[^>]+>", "")` |
| `text.py:126` | `remove_urls` | `re.sub(r"https?://\S+", "", val)` | `pl.col(c).str.replace_all(r"https?://\S+", "")` |
| `text.py:47` | `normalize_unicode` | unicodedata normalize | Mostly native; check Polars `str.normalize` |
| `text.py:211` | `truncate` | `val[:n]` | `pl.col(c).str.slice(0, n)` |
| `text.py:232` | `fix_mojibake` | `ftfy.fix_text(val)` | Cannot — requires Python ftfy. **Stays mode="series".** |
| `text.py:250` | `normalize_quotes` | character substitution | `pl.col(c).str.replace_all(...)` chain |
| `text.py:271` | `extract_numbers` | regex extract | `pl.col(c).str.extract(r"\d+", 0)` |
| `text.py:156` | `pad_left` | `val.rjust(n, fill)` | `pl.col(c).str.zfill(n)` (for "0") or chain |
| `text.py:174` | `pad_right` | `val.ljust(n, fill)` | Polars has no direct; concat trick |
| `email.py:75` | `email_extract_domain` | `val.split("@")[1]` | `pl.col(c).str.extract(r"@(.+)$", 1)` |
| `categorical.py:26` | `boolean_normalize` | dict lookup | `pl.col(c).map_dict(table, default=...)` or `pl.when(...).then(...)` chain |
| `numeric.py:23` | `currency_strip` | regex strip + cast | `pl.col(c).str.replace_all(r"[$,]", "").cast(pl.Float64)` |
| `numeric.py:43` | `percentage_normalize` | strip "%" + cast | Same shape |
| `numeric.py:78` | `to_integer` | `int(val)` | `pl.col(c).cast(pl.Int64, strict=False)` |
| `address.py:48` | `address_standardize` | dict lookup | If purely lookup: `map_dict` |
| `address.py:79` | `state_abbreviate` | dict lookup | `map_dict` |
| `address.py:91` | `state_expand` | dict lookup | `map_dict` |
| `names.py:80` | `strip_titles` | regex strip | `pl.col(c).str.replace_all(r"^(Mr|Mrs|...)\.?\s+", "")` |
| `names.py:92` | `strip_suffixes` | regex strip | Same shape |
| `names.py:107` | `name_proper` | `val.title()` | `pl.col(c).str.to_titlecase()` |

**Conservative count: 15-20 transforms in this tier.** Each rewrite is 5-10 LOC; correctness test is a 5-row before/after fixture.

## Attack targets — Tier 2 (genuinely Python, but gate-able or batchable)

| File:line | Transform | Why Python | Optimization |
|---|---|---|---|
| `phone.py:23` | `phone_e164` (auto_apply=True) | `phonenumbers.parse()` is Python | **Gate by column-type detection** (must fire only on `input_types=["phone"]` after the column profile says phone). Audit the gate; if it fires on non-phone columns currently, that alone is the win. See [spec Attack A](2026-05-15-map-elements-attack-design.md#attack-a-phone_e164-vectorization). |
| `dates.py:30` | `date_iso8601` | `dateutil.parser.parse()` is Python | Use Polars `str.to_datetime(format="%Y-%m-%d")` strict path for known formats; fall back to dateutil only for ambiguous strings |
| `dates.py:86` | `age_from_dob` | datetime arithmetic | After ISO conversion, `pl.col(c).dt....` natively |
| `dates.py:127` | `extract_year` | `parsed.year` | `pl.col(c).dt.year()` (after dates are typed) |
| `dates.py:146` | `extract_month` | `parsed.month` | `pl.col(c).dt.month()` |
| `dates.py:190` | `extract_day` | `parsed.day` | `pl.col(c).dt.day()` |
| `dates.py:211` | `extract_quarter` | `parsed.month // 3` | `pl.col(c).dt.quarter()` |
| `dates.py:232` | `extract_day_of_week` | strftime | `pl.col(c).dt.weekday()` (returns int; chain `map_dict` for name) |
| `dates.py:168` | `date_shift` | timedelta arith | `pl.col(c).dt.offset_by(...)` |
| `dates.py:252` | `date_validate` | try/except parse | `pl.col(c).str.to_datetime(strict=False).is_not_null()` |
| `email.py:55` | `email_normalize` | + tag stripping, Gmail dot stripping | Decompose: lowercase native; tag/dot stripping native via regex; only the Gmail-domain check needs a `when().then()` chain. All achievable in Polars. |
| `categorical.py:54` | `gender_standardize` | dict lookup w/ fuzzy fallback | Pure lookup path is native; only the fuzzy fallback (likely `rapidfuzz`) stays Python — and even that can use `process.extract` over the dict once per unique value, not per row |

**Conservative count: 10-12 transforms.** Higher effort per rewrite (~30 min each + correctness fixture).

## Attack targets — Tier 3 (genuinely require Python)

| File:line | Transform | Why it stays Python |
|---|---|---|
| `identifiers.py:33,54,75` | `ssn_format`, `ssn_mask`, `ein_format` | Format detection + reformatting; could be regex-vectorized but worth ~ms |
| `numeric.py:132,153` | `comma_decimal`, `scientific_to_decimal` | Could be native; check |
| `auto_correct.py:127` | `category_auto_correct` | Calls rapidfuzz `process.extractOne` per row. Could batch via `cdist` on uniques. Worth investigating. |
| `text.py:232` | `fix_mojibake` | `ftfy.fix_text()` is Python-only |
| `categorical.py:40,80,121` | `category_standardize`, `null_standardize` | Dict-lookup-able; check |
| `url.py:51,77` | `url_normalize`, `url_extract_domain` | `urllib.parse` — could be partial native |
| `domains/*.py` | Domain-pack transforms | Pack-specific; rewrite when the pack ships, not now |
| `goldencheck/engine/fixer.py:56,65,82,99,109` | Quality-fix `map_elements` calls | Lives outside the dedupe hot path; defer unless the bench shows them |

## Attack targets — goldenmatch/core call sites (10 total)

| File:line | Context | Notes |
|---|---|---|
| `standardize.py:381,395` | Fallback path in `_NATIVE_STANDARDIZERS` chain | **Already gated** — fires only when the chain has non-native standardizers. After Tier 1+2 above, this gate hits less. |
| `matchkey.py:106` | Same shape — fallback in `_try_native_chain` | Same as above. |
| `chunked.py:237` | Inside the chunked backend's transform application | Same shape; flows from goldenflow registrations. |
| `block_analyzer.py:143,156` | Custom blocking key computation via user function | Genuinely user-supplied. Stays. |
| `autofix.py:201` | Control-char removal | `pl.col(c).str.replace_all(r"[\x00-\x1f]", "")` would work. Easy Tier 1 win. |
| `validate.py:94` | Format validation via user `checker` | Genuinely user-supplied. Stays. |
| `blocker.py:107,523` | Custom blocking key hashing | Same — user code path. Stays. |
| `memory/corrections.py:98` | Reanchor record_hash → row_id mapping | Per CLAUDE.md, "vectorized O(N)" — already optimized within map_elements constraints. Could be `pl.col(c).hash()` native but invariant must be preserved. Investigate. |

## Recommended execution sequence

Per [the spec's Implementation order step 3](2026-05-15-map-elements-attack-design.md#implementation-order):

1. **First PR — Tier 1 batch.** Pick 10-15 transforms from the Tier 1 table. One PR, one commit per transform. Each commit:
   - Switch `mode="series"` → `mode="expr"`
   - Rewrite body returning `pl.Expr` not `pl.Series.map_elements(...)`
   - Add correctness test asserting bitwise equality with the old `map_elements` version on ≥5 edge cases
   - Run `pytest -k <transform_name>` per transform
   
   Expected win: 5-10s off the 100K cProfile cumtime for `map_elements`. ~10% wall reduction.

2. **Second PR — `phone_e164` audit (spec Attack A).** Confirm `phone_e164` is gate-fired only on actual phone columns. The synthetic person fixture has no phone column per `tests/generate_synthetic.py`; if `phone_e164` is firing in the 100K bench, the gate is broken. That diagnostic alone is the win.

3. **Third PR — Tier 2 dates batch.** All the `extract_*` and date-format transforms once the columns are typed. ~5-7 LOC each.

4. **Fourth PR — controller transform cache (spec Attack C).** Cache per-call transform output across controller iterations. Eliminates 4 of 5 redundant transform invocations per `dedupe_df`.

5. **Re-bench at 100K.** Acceptance per spec: median wall ≤ 24s (from 28.4s, ≥15% reduction). `map_elements` ncalls < 50.

## What we don't know yet (catalog limits)

- **Which transforms actually fire on the bench fixture.** Need step 2 of the spec's implementation order: instrument `_try_native_chain` fallback + `map_elements` call counters, run a 100K bench, see which transforms produced the 542 calls.
- **Whether the gate on `phone_e164` is firing on non-phone columns.** Diagnostic for the next PR.
- **Whether `_NATIVE_STANDARDIZERS` already covers the goldenmatch-side fallbacks** before this work lands. Goldenmatch's standardize fallback may dry up once goldenflow stops using `map_elements`.

These knowns/unknowns frame the next two PRs. The catalog is complete; the attack is now data-grounded.
