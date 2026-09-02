# Fixture: `tui/engine.py` at `6c89042c7^`

Verbatim copy of the file immediately BEFORE `6c89042c7` ("delete
MatchEngine's copy of the pipeline"). `_run_pipeline`'s docstring reads
"Core pipeline logic - mirrors run_dedupe but returns EngineResult", and
nothing enforced that: 2 tests referenced `_run_pipeline`, 10 referenced
`run_dedupe`, 0 referenced both -- counted by the detector's EXECUTABLE-reference
rule (`Name`/`Attribute`/`alias` AST nodes only, docstrings and comments excluded;
see `scripts/sync_claims/enforcement.py`). A plain text grep over the same file at
that revision gives 7, 14 and 0 respectively -- higher on both sides because it
also matches the docstring mention this fixture exists to NOT count as
enforcement.

Checked in so the detector can be validated without git history staying
reachable. Parsed by AST only, never imported. Do not edit.
