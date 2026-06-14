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

**Python — repo footguns** (the "bit us in PR #N" lessons):

| Rule | Severity | Invariant |
|---|---|---|
| `no-clirunner-mix-stderr` | error | `CliRunner(mix_stderr=...)` raises on click>=8.3 — drop the kwarg |
| `no-toplevel-import-torch` | warning | unguarded top-level `import torch` hangs/segfaults on GPU-less boxes — import lazily or guard |
| `no-bare-relative-test-fixture-path` | warning | `Path("tests/...")` resolves off CWD (differs local vs CI) — anchor to `__file__` |

**Polars** (the repo is Polars-native):

| Rule | Severity | Invariant |
|---|---|---|
| `polars-read-excel-needs-engine` | warning | `pl.read_excel(path)` with no `engine=` — pass `engine="openpyxl"` (caught a real one in goldencheck) |
| `polars-no-deprecated-apply` | warning | `pl.col(x).apply(...)` is deprecated/slow — use `.map_elements(..., return_dtype=...)` or a vectorized expr |

**Python — security** (clean guards, no current call sites):

| Rule | Severity | Invariant |
|---|---|---|
| `py-no-eval` | error | `eval(...)` executes arbitrary code — use `ast.literal_eval` / `json.loads` / a dispatch dict |
| `py-no-exec` | error | `exec(...)` runs arbitrary code strings — use real functions / a dispatch table |
| `py-no-subprocess-shell-true` | error | `subprocess.*(..., shell=True)` is a shell-injection risk — pass an argv list, `shell=False` |

**TypeScript ports**:

| Rule | Severity | Invariant |
|---|---|---|
| `ts-no-empty-catch` | error | empty `catch {}` silently swallows errors — log or re-throw |
| `ts-no-spread-math-min-max` | warning | `Math.min/max(...array)` throws on >65K elements — surfaces **11 real sites** for a follow-up cleanup |

**Rust kernels** (`packages/rust/extensions/`):

| Rule | Severity | Invariant |
|---|---|---|
| `rust-no-dbg-macro` | error | `dbg!()` is a debug macro — never commit it (and it prints on the kernel hot path) |
| `rust-no-todo-unimplemented` | warning | `todo!()`/`unimplemented!()` panic at runtime — must not reach a shipped kernel path |

> Dogfood note: scanning Rust + Python found **0 real violations** — the kernels and
> Python tree already comply (the `panic!`/`println!` hits in Rust are all legit:
> test assertions + the `goldenembed` CLI binary). These Rust rules are *preventive*
> guards. The TypeScript `ts-no-spread-math-min-max` rule, by contrast, surfaced 11
> real latent crash sites — fixed in a separate dogfood PR.

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
- **`node:fs`/`process.env` in `src/core/`** (TS edge-safety) — the invariant is
  real, but `src/core/engine/{history,scheduler}.ts` are *documented* intentional
  exceptions (their functions aren't re-exported from `core/index.ts`), and
  `require()`/`process.env` are deliberate lazy patterns elsewhere — so a rule
  fires on correct code. Needs a per-file allowlist a flat AST rule can't carry.
- **`import pandas` in goldencheck** (Polars-native) — the one hit is a *lazy*
  interop import in `engine/db_scanner.py`, not a violation.

## Add a rule

1. Write `.ast-grep/rules/<id>.yml` (`id`, `language`, `severity`, `message`, `rule`).
   Prototype the pattern with `ast-grep run -p '<pattern>' --lang python`.
2. Add `.ast-grep/rule-tests/<id>-test.yml` with `valid:` / `invalid:` snippets.
3. `ast-grep test --update-all` to snapshot, then `ast-grep test` to confirm.
4. `ast-grep scan` to check it doesn't false-positive on the existing tree.

Start new rules at `severity: warning` (non-blocking); promote to `error` once the
tree is clean and the rule has proven low-noise.
