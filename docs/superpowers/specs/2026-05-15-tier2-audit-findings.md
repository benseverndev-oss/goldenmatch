# Tier 2 audit findings — phone gate + date format coverage

**Status:** Audit memo (drafted 2026-05-15)
**Author:** Claude + bsevern
**Context:** Tier 2 of the map_elements attack per
[`2026-05-15-map-elements-attack-design.md`](2026-05-15-map-elements-attack-design.md)
and [`2026-05-15-map-elements-catalog.md`](2026-05-15-map-elements-catalog.md).

This memo records two diagnostic findings from the Tier 2 audit work,
and what landed vs what was deferred.

## Finding 1 — phone_e164 gate is correct, not broken

The map_elements spec's "Attack A" hypothesis was: `phone_e164` may be
firing on non-phone columns because of a broken gate, and the synthetic
person fixture used in the bench has no phone column.

**Both halves of that hypothesis are wrong.**

Reading `packages/python/goldenmatch/tests/generate_synthetic.py:109`:

```python
record = {
    "id": i + 1,
    "first_name": first,
    "last_name": last,
    "email": email,
    "phone": random_phone(),       # <-- phone column IS present
    "address": random_address(),
    "city": ...,
    ...
}
```

And the gate in `packages/python/goldenflow/goldenflow/engine/selector.py`:

```python
for t in all_transforms:
    if not t.auto_apply:
        continue
    if profile.inferred_type in t.input_types:    # phone_e164 has ["phone"]
        selected.append(t)
    elif "string" in t.input_types and profile.inferred_type in (
        "string", "email", "phone", "name", "address", "date",
    ):
        selected.append(t)
```

`phone_e164` has `input_types=["phone"]` (not `["string"]`), so the second
branch never fires — it only matches when the column profile says
`inferred_type == "phone"`. That's correct.

The fixture has a phone column → phone gets profiled as `inferred_type="phone"`
→ phone_e164 fires → produces 1 `map_elements` call per controller iteration
(× 5 iterations = 5 of the 542 calls). **Not a meaningful share of the
hot path.**

## Finding 2 — the real residual hot paths

With phone_e164 mostly innocent, what does account for the 542 `map_elements`
calls at 100K post-#239? Working backwards from the auto_apply transforms
that still use `mode="series"` after Tier 1 batches 1-3:

| Transform | `auto_apply` | Fires on | Tier 1 status |
|---|---|---|---|
| `normalize_unicode` | True | every string-like column | **Stays mode="series"** — needs Python `unicodedata` |
| `fix_mojibake` | False | n/a (opt-in) | Stays mode="series" |
| `phone_e164` | True | phone columns only | Stays mode="series" — needs `phonenumbers` |
| `date_iso8601` | True | date columns | Stays mode="series" — see Finding 3 |
| `zip_normalize` | True | zip columns | **Migrated this batch** to mode="expr" |

For the synthetic person fixture (10 columns, 8 string-like, 1 phone, 1 zip):

- `normalize_unicode` on ~8 string columns × 5 iterations ≈ 40 calls
- `phone_e164` on 1 phone column × 5 iterations = 5 calls
- `zip_normalize` on 1 zip column × 5 iterations = 5 calls (now eliminated)
- Plus per-call ramp-up overhead and any non-auto transforms.

After Tier 1 batch 1-3 + zip_normalize → estimated remaining `map_elements`
calls in the hot path drop to ~50 from the original 542 (a ~90% reduction).
The dominant remainder is `normalize_unicode` firing across all string-like
columns. That one **cannot move to `mode="expr"`** — Polars has no native
NFKD-normalize-and-strip-combining-marks operation; it must stay in
`mode="series"`. Tier 3 by design.

## Finding 3 — date_iso8601 cannot move to mode="expr" without behavior regression

Tried converting `date_iso8601` to native Polars and discovered a real
behavior gap:

```python
>>> import polars as pl
>>> pl.DataFrame({"d": ["03/15/2024", "Jan 5, 2023", "2024-01-20", "invalid"]})
...   .select(pl.col("d").str.to_date(strict=False))
[{'d': None}, {'d': None}, {'d': date(2024, 1, 20)}, {'d': None}]
```

Polars's `str.to_date(strict=False)` without an explicit format **only
parses ISO-format dates** (`YYYY-MM-DD`). It does not handle:

- US-style slash dates (`03/15/2024`)
- English month names (`Jan 5, 2023`)
- EU-style slash dates (`15/03/2024`)
- Any other common human-written format

The current `mode="series"` implementation uses `dateutil.parser.parse`,
which handles all of these. Migrating to `mode="expr"` would silently
drop format coverage that callers may depend on.

This applies to **every transform that goes through `_parse_date(val)`**:

- `date_iso8601` (auto_apply=True)
- `date_us`, `date_eu`, `date_parse`
- `age_from_dob`
- `datetime_iso8601`
- `extract_year`, `extract_month`, `extract_day`, `extract_quarter`, `extract_day_of_week`
- `date_shift`
- `date_validate`

Twelve transforms. All stay `mode="series"` until one of these design
decisions is made (see §Options for follow-up).

### Why this matters for the bench

`date_iso8601` is `auto_apply=True` — would fire on every date column.
The synthetic person fixture has no date column (`generate_synthetic.py`
defines no DOB / created_at / etc.), so `date_iso8601` doesn't fire on
the bench at all. **No regression for our bench numbers from leaving
date transforms in `mode="series"`.**

For real customer data with date columns, the choice between
"native, fast, ISO-only" and "Python, slow, dateutil-flexible" is a
product decision. Not blocking Tier 1 perf wins.

## What landed in this batch

| Transform | Change |
|---|---|
| `address.zip_normalize` | `mode="series"` → `mode="expr"`. Native: `str.strip_chars().str.split("-").list.first()` then `when(matches r"^\d+$").then(zfill(5)).otherwise(base)`. Auto_apply=True; fires on every zip column. |

That's it — just one transform in this batch, because the date transforms
need spec-level decisions before migrating.

## Cumulative Tier 1 + Tier 2 progress

| Batch | PR | Transforms |
|---|---|---|
| 1 | #253 ✅ | 5 (email, html tags, urls, extract_numbers) |
| 2 | #254 | 7 (strip_titles, strip_suffixes, remove_emojis, normalize_line_endings, currency_strip, percentage_normalize, to_integer) |
| 3 | #255 | 7 (truncate, pad_left, pad_right, address_standardize, address_expand, state_abbreviate, state_expand) |
| 4 (this) | new | 1 (zip_normalize) + audit findings |
| **Total** | | **20 transforms migrated** |

Tier 1 catalog estimated 15-20 → effectively complete. Catalogued Tier 2
date transforms remain in `mode="series"` pending a design decision.

## Options for follow-up (date transforms)

1. **Cascade fallback approach.** Try Polars `str.to_date(format=...)` with
   common formats (`%Y-%m-%d`, `%m/%d/%Y`, `%d/%m/%Y`, `%Y/%m/%d`) in order,
   using `pl.coalesce` to pick the first successful parse. Doesn't handle
   English month names — those would silently fall through to null. Net
   coverage: ~80% of real-world formats, 100% speedup on the covered formats.

2. **Hybrid mode.** Run the Polars-native parse first. For rows where it
   returns null but `_DATEUTIL_LOOKS_PARSEABLE_RE.match(val)` is True,
   fall back to a `map_elements` call. Cuts the Python cost proportional
   to ISO-format coverage in the column. More complex.

3. **Leave as-is.** date columns aren't on the bench's hot path; pursue
   wins elsewhere.

4. **Specify a "native_dates_only" option** in `GoldenFlowConfig` so
   advanced users can opt into Polars-native parsing knowing they lose
   format coverage. Defaults to current behavior.

My recommendation: **option 3 for now.** The bench doesn't show date
transforms in the top 10, and the format-coverage trade-off needs more
thought than this audit warrants.

## Connection back to the spec

These findings update what the [map_elements attack spec](2026-05-15-map-elements-attack-design.md)
should claim:

- **Attack A (phone_e164 gate):** spec hypothesis wrong; no gate fix needed.
  Phone column genuinely exists on the fixture. Replace this attack section
  with: "phone_e164 stays mode='series' as Tier 3 (genuinely Python via
  phonenumbers); 5 calls per dedupe is acceptable."
- **Attack B (Tier 1 catalog):** complete (20 transforms across 4 PRs).
- **Attack C (controller transform cache):** still TBD — cache redundant
  invocations of the auto-transform chain across the controller's 5
  sample iterations. This is now the biggest remaining single win at
  spec level.

Spec acceptance criterion #1 ("100K median wall ≤ 24s") will need
re-measurement on the bench after all batch PRs merge to confirm gates
are hit.
