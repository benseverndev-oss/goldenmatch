# Fixture: `tui/engine.py` at `6c89042c7^`

Verbatim copy of the file immediately BEFORE `6c89042c7` ("delete
MatchEngine's copy of the pipeline"). `_run_pipeline`'s docstring reads
"Core pipeline logic - mirrors run_dedupe but returns EngineResult", and
nothing enforced that: 2 tests referenced `_run_pipeline`, 10 referenced
`run_dedupe`, 0 referenced both.

Checked in so the detector can be validated without git history staying
reachable. Parsed by AST only, never imported. Do not edit.
