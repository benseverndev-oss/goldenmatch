# ast-grep structural rules

Structural lint rules that encode repo invariants ruff/eslint can't express —
the "this bit us in PR #N, don't do it again" lessons from the CLAUDE.md files,
turned into AST patterns enforced in CI.

## Run locally

```bash
pip install ast-grep-cli            # or: brew install ast-grep / cargo install ast-grep
ast-grep scan                       # lint the repo (exits non-zero only on ERROR rules)
ast-grep test                       # validate the rules against their own test cases
ast-grep scan -r .ast-grep/rules/no-clirunner-mix-stderr.yml   # one rule
```

CI runs `ast-grep test` then `ast-grep scan` (`.github/workflows/ast-grep.yml`).
`scan` fails only on **error**-severity rules; **warning**-severity rules are
advisory (reported, non-blocking) so new rules can land gradually.

## Current rules (`.ast-grep/rules/`)

| Rule | Severity | Invariant |
|---|---|---|
| `no-clirunner-mix-stderr` | error | `CliRunner(mix_stderr=...)` raises on click>=8.3 — drop the kwarg |
| `no-toplevel-import-torch` | warning | unguarded top-level `import torch` hangs/segfaults on GPU-less boxes — import lazily or guard |
| `no-bare-relative-test-fixture-path` | warning | `Path("tests/...")` resolves off CWD (differs local vs CI) — anchor to `__file__` |
| `polars-read-excel-needs-engine` | warning | `pl.read_excel(path)` with no `engine=` — pass `engine="openpyxl"` (caught a real one in goldencheck) |

## Considered but not added (AST can't express them cleanly)

These repo invariants need **dataflow / intent**, not just structure, so a pure
AST pattern would be too noisy or impossible — left to review (or a future
semantic linter):

- **full-frame `df.to_dicts()` in a per-row/hot loop** (the #904 O(N)/probe bug) —
  "whole frame" + "hot loop" aren't structural; a blanket `.to_dicts()` ban
  carpet-bombs legit uses.
- **pair canonicalization `(min(a,b), max(a,b))`** — "this tuple is a stored pair"
  is intent, not shape.
- **`encoding="utf-8"` only inside `pl.read_csv`/`scan_csv`** — the kwarg-string
  match is unreliable in tree-sitter and a broad ban wrongly flags legit stdlib
  `open(encoding="utf-8")`.
- **`pl.read_csv(path)` missing `encoding=`** — 17 legit single-arg call sites, so
  a rule would be pure noise.

## Add a rule

1. Write `.ast-grep/rules/<id>.yml` (`id`, `language`, `severity`, `message`, `rule`).
   Prototype the pattern with `ast-grep run -p '<pattern>' --lang python`.
2. Add `.ast-grep/rule-tests/<id>-test.yml` with `valid:` / `invalid:` snippets.
3. `ast-grep test --update-all` to snapshot, then `ast-grep test` to confirm.
4. `ast-grep scan` to check it doesn't false-positive on the existing tree.

Start new rules at `severity: warning` (non-blocking); promote to `error` once the
tree is clean and the rule has proven low-noise.
