#!/usr/bin/env python3
"""Ban `x in <frame>.columns` on a frame whose engine the callee cannot know.

THE BUG THIS EXISTS FOR. `packages/python/goldenmatch/goldenmatch/core/
tf_tables.py` and `probabilistic._build_tf_tables` guarded with
`if field not in df.columns`. On a `pl.DataFrame` that tests column NAMES; on
a `pyarrow.Table`, `.columns` is a list of ChunkedArrays, so the test is
ALWAYS true. Every TF-adjustment field read as absent, the frequency tables
came back `None`, and the whole FS term-frequency feature silently did not
exist on the default path -- which by then was Arrow.

Nothing raised. Nothing warned. A full-panel A/B returned +0.0000 on five
datasets with precision and recall identical to three decimals, which reads
exactly like "this lever is neutral" rather than "this lever never ran".

WHY A STATIC GATE AND NOT A TEST. Both engines satisfy every OTHER operation
in that function, so a unit test written against one engine passes and proves
nothing about the other. The defect only appears when a particular caller
hands the other engine to a particular callee, and the flag that selected
that caller was default-off. No reachable test covered it, and by
construction none was going to.

`core/domain.py` already documents the hazard in prose:

    `pa.Table.columns` is a list of ChunkedArrays, not names, so a bare
    `df.columns` either raises (`.startswith` on a ChunkedArray) or, worse,
    silently answers False for every `col in df.columns` membership test.

That comment predates the TF bug by months. Prose did not stop it; this does.

SCOPE. Only MEMBERSHIP tests (`in` / `not in`), and only against a bare
function PARAMETER -- the case where the callee cannot know the engine because
the caller chose it. Iterating `df.columns` or calling `.startswith` on its
elements RAISES on an Arrow table, which is loud and self-reporting; silence
is what makes membership special. A local built inside the function has a
knowable engine and is not flagged.

THE FIX at any flagged site is one line: route through
`goldenmatch.core.frame.to_frame(df).columns`, which returns names on both
engines and is what the rest of the package does at a seam.

RATCHET. `KNOWN` below is a floor to work DOWN, seeded from the state of the
tree when this gate landed. Entries are keyed by (module, function, parameter,
source text) and deliberately NOT by line number: the sync_claims ratchet keys
on line number and broke twice in one day because a pure insertion above an
entry reads as one finding vanishing and a new one appearing. Moving code must
not fail this gate; only adding a new site should.

Run: python scripts/check_frame_engine_safety.py [--list]
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOTS = [ROOT / "packages" / "python" / "goldenmatch" / "goldenmatch"]

# Engine-specific by design: these classes ARE the polars and Arrow backends,
# so they know their own frame and a to_frame round-trip would be circular.
EXEMPT_MODULES = {"core/frame.py"}

# A parameter is frame-shaped if its name reads like one. Deliberately a name
# heuristic and NOT the type annotation: `_build_tf_tables` was annotated
# `df: pl.DataFrame` and received a `pa.Table`. The annotation was the lie.
FRAME_HINTS = ("df", "frame", "table", "tbl", "sdf", "sample", "prepared")

# Calls that hand the frame to the engine-agnostic accessor before use.
WRAPPERS = ("to_frame(", "_tf_dom(", "_tf(", "_tf_cols(")


def _is_frameish(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in FRAME_HINTS)


def _finding_key(module: str, func: str, param: str, text: str) -> tuple[str, str, str, str]:
    return (module, func, param, " ".join(text.split()))


def scan() -> list[tuple[tuple[str, str, str, str], int]]:
    """Return [(key, lineno)] for every bare-parameter membership test."""
    found: list[tuple[tuple[str, str, str, str], int]] = []
    for scan_root in SCAN_ROOTS:
        for path in sorted(scan_root.rglob("*.py")):
            module = path.relative_to(scan_root).as_posix()
            if module in EXEMPT_MODULES:
                continue
            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            lines = source.splitlines()
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                params = {a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs}
                end = fn.end_lineno or fn.lineno
                body = "\n".join(lines[fn.lineno - 1:end])
                if any(w in body for w in WRAPPERS):
                    continue
                for node in ast.walk(fn):
                    if not isinstance(node, ast.Compare):
                        continue
                    if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                        continue
                    for comp in node.comparators:
                        if (
                            isinstance(comp, ast.Attribute)
                            and comp.attr == "columns"
                            and isinstance(comp.value, ast.Name)
                            and comp.value.id in params
                            and _is_frameish(comp.value.id)
                        ):
                            text = lines[node.lineno - 1].strip()
                            found.append(
                                (_finding_key(module, fn.name, comp.value.id, text), node.lineno)
                            )
    return found


# ---------------------------------------------------------------------------
# A floor to work DOWN, seeded 2026-09-09 from the tree as it stood when the
# gate landed. Every entry is a site that MIGHT be handed an Arrow frame; none
# is known to be. They are recorded rather than fixed because a blind
# to_frame() at 63 sites is a large untested diff across blocking, golden
# records, matchkeys and the Splink importer -- and because the honest state is
# "unaudited", not "safe". Work them down by establishing, per site, whether an
# Arrow frame can reach it; if it can, route through to_frame and delete the
# entry.
#
# Keyed by (module, function, parameter, source text), NOT line number.
#
# Two seed entries are already gone: `_build_tf_tables` and
# `value_frequencies` were the sites this gate was written for, and #2908
# routed both through `to_frame`. The floor is 61.
# ---------------------------------------------------------------------------
KNOWN: set[tuple[str, str, str, str]] = {
    ('config/splink_upgrade.py', '_measure_mean_length', 'df',
     'if field not in df.columns:'),
    ('config/splink_upgrade.py', '_measure_mean_token_set_size', 'df',
     'if field not in df.columns:'),
    ('config/splink_upgrade_measure.py', '_resolve_ids', 'df',
     'if candidate in df.columns:'),
    ('config/splink_upgrade_measure.py', '_resolve_ids', 'df',
     'if id_column not in df.columns:'),
    ('core/autoconfig.py', 'build_matchkeys', 'df',
     'if df is not None and p.name in df.columns:'),
    ('core/block_analyzer.py', '_target_pairs_from_similarity', 'sample_frame',
     'valid_cols = [c for c in matchkey_columns if c in sample_frame.columns]'),
    ('core/blocker.py', '_ann_sub_block', 'block_df',
     'if ann_column not in block_df.columns:'),
    ('core/blocking_pass_selection.py', 'select_passes', 'sample_df',
     'if "__row_id__" not in sample_df.columns:'),
    ('core/golden.py', '_build_golden_records_polars_native', 'multi_df',
     'has_row_id = "__row_id__" in multi_df.columns'),
    ('core/golden.py', 'build_golden_record', 'cluster_df',
     'if field_rule.date_column in cluster_df.columns:'),
    ('core/golden.py', 'build_golden_record', 'cluster_df',
     'if field_rule.strategy == "source_priority" and "__source__" in cluster_df.columns:'),
    ('core/golden.py', 'build_golden_record', 'cluster_df',
     'row_ids = cluster_df["__row_id__"].to_list() if "__row_id__" in cluster_df.columns else None'),
    ('core/golden.py', 'build_golden_record_with_provenance', 'df',
     'if cluster_col not in df.columns:'),
    ('core/golden.py', 'build_golden_records_df', 'multi_df',
     'has_row_id = "__row_id__" in multi_df.columns'),
    ('core/golden_fused.py', '_build_date_arrays', 'sdf',
     'if not date_col or date_col not in sdf.columns:'),
    ('core/golden_fused.py', '_build_provenance_records', 'sdf',
     'if "__row_id__" in sdf.columns:'),
    ('core/golden_rules_refiner.py', 'compute_refinement_signals', 'prepared_df',
     'if "__row_id__" in prepared_df.columns:'),
    ('core/golden_rules_refiner.py', 'compute_refinement_signals', 'prepared_df',
     'if "__source__" in prepared_df.columns:'),
    ('core/llm_extract.py', 'apply_llm_extractions', 'df',
     'if "__extract_confidence__" in df.columns:'),
    ('core/llm_extract.py', 'apply_llm_extractions', 'df',
     'if df_col not in df.columns:'),
    ('core/matchkey.py', 'precompute_matchkey_transforms', 'df',
     'if field_obj.field not in df.columns:'),
    ('core/matchkey.py', 'precompute_matchkey_transforms', 'df',
     'if not all(c in df.columns for c in src):'),
    ('core/matchkey.py', 'precompute_matchkey_transforms', 'df',
     'if not src or fld is None or fld in df.columns or fld in _seen_derived:'),
    ('core/matchkey.py', 'precompute_matchkey_transforms', 'df',
     'if sig in seen or sig in df.columns:'),
    ('core/pipeline.py', '_score_partition_with_config', 'df',
     'if "__row_id__" not in df.columns:'),
    ('core/pipeline.py', '_score_partition_with_config', 'df',
     'if "__source__" not in df.columns:'),
    ('core/probabilistic_fast.py', '_resolve_probabilistic_fast_path', 'prepared_df',
     'if xform_col not in prepared_df.columns:'),
    ('core/quality.py', 'row_quality_floor', 'df',
     'if not _goldencheck_available() or row_id_col not in df.columns:'),
    ('core/retrieval.py', '_filtered_rows', 'frame',
     'if "__row_id__" in frame.columns:'),
    ('core/retrieval.py', '_filtered_rows', 'frame',
     'if col not in frame.columns:'),
    ('core/review_queue.py', '_row_to_dict', 'df',
     'cols = [f for f in fields if f in df.columns]'),
    ('core/review_queue.py', '_row_to_dict', 'df',
     'if "__row_id__" not in df.columns or not cols:'),
    ('core/schema_match.py', 'apply_column_mapping', 'df',
     'if all(p in df.columns for p in parts):'),
    ('core/schema_match.py', 'apply_column_mapping', 'df',
     'if src in df.columns:'),
    ('core/scorer.py', 'find_fuzzy_matches', 'block_df',
     'block_df[c].to_list() if c in block_df.columns else [None] * n'),
    ('core/suggest/adapter.py', '_collision_rates', 'df',
     'id_col = "__row_id__" if "__row_id__" in df.columns else None'),
    ('core/suggest/adapter.py', 'review_config', 'df',
     'if "__row_id__" not in df.columns:'),
    ('core/suggest/adapter.py', 'suggest_from_result', 'df',
     'if "__row_id__" not in df.columns:'),
    ('core/survivorship/native.py', '_resolve_conditionals', 'multi_df',
     'has_row_id = "__row_id__" in multi_df.columns'),
    ('core/survivorship/native.py', '_resolve_conditionals', 'multi_df',
     'has_source = "__source__" in multi_df.columns'),
    ('core/survivorship/native.py', '_resolve_scalars', 'multi_df',
     'has_row_id = "__row_id__" in multi_df.columns'),
    ('core/survivorship/native.py', '_sorted_for_group', 'multi_df',
     'if strategy == "anchor" and g.anchor not in multi_df.columns:'),
    ('core/survivorship/native.py', '_sorted_for_group', 'multi_df',
     'if strategy == "most_recent" and g.date_column not in multi_df.columns:'),
    ('core/vector_index.py', '_prep_frame', 'df',
     'if column not in df.columns:'),
    ('core/vector_index.py', '_prep_frame', 'df',
     'if id_column is not None and id_column in df.columns:'),
    ('core/vector_store.py', 'prep_rows', 'df',
     'if column not in df.columns:'),
    ('core/vector_store.py', 'prep_rows', 'df',
     'if id_column is not None and id_column in df.columns:'),
    ('db/sync.py', '_cast_to_schema', 'df',
     'if name not in df.columns:'),
    ('db/sync.py', '_incremental_pipeline', 'new_df',
     'id_col = "id" if "id" in new_df.columns else new_df.columns[0]'),
    ('db/writer.py', '_write_separate', 'golden_df',
     ') if "__cluster_id__" in golden_df.columns else None'),
    ('identity/resolve.py', '_exact_match_rows', 'df',
     'if any(f not in df.columns for f in fields):'),
    ('identity/snowflake_backend.py', 'bulk_add_edges', 'df',
     'missing = [c for c in _EDGE_COLS if c not in df.columns]'),
    ('identity/snowflake_backend.py', 'bulk_emit_events', 'df',
     'missing = [c for c in _EVENT_COLS if c not in df.columns]'),
    ('identity/snowflake_backend.py', 'bulk_upsert_identities', 'df',
     'missing = [c for c in _NODE_COLS if c not in df.columns]'),
    ('identity/snowflake_backend.py', 'bulk_upsert_records', 'df',
     'missing = [c for c in _RECORD_COLS if c not in df.columns]'),
    ('identity/stitching.py', 'stitch_frame', 'df',
     'if "__row_id__" not in df.columns or df.is_empty():'),
    ('identity/store.py', '_pg_copy_direct', 'df',
     'missing = [c for c in cols if c not in df.columns]'),
    ('identity/store.py', '_sqlite_stage', 'df',
     'missing = [c for c in cols if c not in df.columns]'),
    ('identity/store.py', 'bulk_upsert_identities', 'df',
     'missing = [c for c in cols if c not in df.columns]'),
    ('identity/survivorship.py', 'build_golden_with_provenance', 'df',
     'if "__row_id__" not in df.columns or not member_row_ids:'),
    ('spark/identity.py', 'derive_record_ids', 'source_df',
     'has_source = source_col in source_df.columns'),
}

def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    found = scan()
    current = {k for k, _ in found}
    lineno_of = {k: ln for k, ln in found}

    if "--list" in argv:
        for key in sorted(current):
            print(f"{key[0]}:{lineno_of[key]}  {key[1]}({key[2]})  {key[3]}")
        print(f"\n{len(current)} site(s); {len(KNOWN)} recorded in the floor.")
        return 0

    new = current - KNOWN
    gone = KNOWN - current

    if new:
        print(f"Frame-engine safety FAILED: {len(new)} NEW membership test(s) "
              f"on a bare frame parameter:")
        for key in sorted(new):
            print(f"  - {key[0]}:{lineno_of[key]}  {key[1]}({key[2]})")
            print(f"      {key[3]}")
        print()
        print("`x in <frame>.columns` tests column NAMES on a polars frame and")
        print("column ARRAYS on a pyarrow.Table, so it silently answers False")
        print("for every field once an Arrow frame reaches the callee. That is")
        print("how FS term-frequency adjustment shipped doing nothing.")
        print()
        print("Fix: route through goldenmatch.core.frame.to_frame(df).columns,")
        print("which returns names on both engines. If the frame genuinely")
        print("cannot be Arrow here, say so in a comment and add the site to")
        print("KNOWN in this file with that reasoning.")
        return 1

    if gone:
        print(f"Frame-engine safety: {len(gone)} recorded site(s) no longer "
              f"present -- remove them from KNOWN so the floor keeps its value:")
        for key in sorted(gone):
            print(f"  - {key[0]}  {key[1]}({key[2]})")
            print(f"      {key[3]}")
        return 1

    print(f"Frame-engine safety OK: {len(current)} recorded site(s), no new ones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
