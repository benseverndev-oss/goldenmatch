# from_splink: IS NOT NULL guard conjuncts in blocking rules (#1783)

**Date:** 2026-07-15
**Status:** Approved (Ben deferred to session judgment; contained bug fix)
**Issue:** #1783 -- compound CustomRule blocking rules silently dropped; measured
-28pp pairwise recall on a 1M production dedupe (blocked on 3 of 6 keys).

## Root cause (verified by reading the recognizer, not the issue's framing)

`_convert_one_blocking_rule` (config/from_splink.py) ALREADY handles the
conjunction-of-equalities case, Splink-4 paren-wrapping, quoted identifiers,
and the SUBSTR first-initial conjunct. The ONE gap: a Splink rule like

    l.first_name = r.first_name AND l.last_name = r.last_name
    AND l.first_name IS NOT NULL AND l.last_name IS NOT NULL

carries `IS NOT NULL` guard conjuncts. `_recognize_blocking_conjunct` returns
None for them, and one unrecognized conjunct drops the WHOLE rule (by design
for genuinely unparseable conjuncts).

## Why recognize-and-ignore is FAITHFUL, not approximate

GM's blocker already implements the guard semantics: `_build_block_key_expr`
concatenates multi-field keys with `pl.concat_str` (default
`ignore_nulls=False` -> any null component nulls the whole key), and
`_build_static_blocks` filters null/sentinel keys before blocks form
(blocker.py ~366-395, the #372/#375 machinery). A row with a null in ANY key
field forms no block via that key -- exactly what Splink's `IS NOT NULL`
guards express. So a guard on a column that is among the rule's equality
fields adds zero information and is ignored EXACTLY (report.info).

Known microscopic edge (documented, not handled): a float NaN in one field of
a MULTI-field key stringifies to "nan" inside the concatenated key ("smith||nan"),
which the whole-key sentinel filter doesn't catch -> superset of Splink's
candidates. Blocking-superset is safe (scoring decides); same convention as the
file's existing SUBSTR approximation note.

## The change (config/from_splink.py only)

1. New regex `_BLOCK_NOT_NULL_RE`: `(l|r).col IS NOT NULL` (quoted-identifier
   tolerant like `_BLOCK_COL_L/R`, case-insensitive, `\s+` between words;
   fullmatch after the existing `_strip_outer_parens`).
2. `_recognize_blocking_conjunct` returns a new sentinel for guards --
   extend `_BlockConjunct` with `is_null_guard: bool = False` (or a distinct
   marker; implementer's call, keep it simple) carrying the guarded column.
3. `_convert_one_blocking_rule`:
   - Partition recognized conjuncts into equality/SUBSTR components vs guards.
   - Guards whose column is IN the final field set: ignored; mention in the
     rule's `report.info` line (e.g. `null guards ignored (implicit in GM
     blocking): ['first_name', 'last_name']`).
   - Guards whose column is NOT in the field set (e.g. `l.a = r.a AND l.b IS
     NOT NULL`): `report.warn` (approximate mapping, candidates are a
     superset -- GM cannot express a null-constraint on a non-key column) but
     STILL convert the rule. Mirrors the file's SUBSTR-widening warn
     convention (strict=True gates on warns).
   - A rule with ONLY guards (no equality/SUBSTR conjuncts): drop with the
     existing unrecognized warn (no fields to block on).
4. No behavior change for anything currently recognized or currently dropped
   for other reasons (OR, cross-column, arithmetic, conflicting SUBSTR).

## Testing

- `tests/test_from_splink_recognizers.py`: unit rows for the new conjunct
  forms -- `l.x IS NOT NULL`, `r.x IS NOT NULL`, `(l."x" IS NOT NULL)`,
  case-insensitivity (`is not null`), and negative rows (`l.x IS NULL` stays
  unrecognized; `IS NOT NULL` on garbage stays unrecognized).
- `tests/test_from_splink_blocking.py`:
  - THE ISSUE'S EXACT RULES (both examples, verbatim strings) -> convert to
    `BlockingKeyConfig(fields=["first_name","last_name"])` /
    `(fields=["last_name","phone_number"])` with NO warn.
  - Splink-4 paren-wrapped + quoted variant of the same.
  - Guard on non-key column -> converts + warns (strict=False), and
    `strict=True` raises (matching the file's strict-gate convention --
    READ how existing strict tests assert this).
  - Guards-only rule -> dropped with warn (unchanged path).
  - Regression: existing suite for this file stays green untouched.
- E2E-lite: one `from_splink(settings_dict)` test whose
  blocking_rules_to_generate_predictions mixes `block_on("npi")` + the
  compound guarded rule -> emitted BlockingConfig has BOTH keys.

## Out of scope

- Mapping SUBSTR-with-different-offsets or transforms-on-guard-columns.
- The NaN-in-compound-key sentinel edge (documented above).
- The issue's repro-harness/bench (the reporter offered it; unit + conversion
  tests pin the fix).
- Any blocker.py change.
