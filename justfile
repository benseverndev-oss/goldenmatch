set shell := ["bash", "-cu"]

default:
    @just --list

install:
    uv sync
    for d in packages/typescript/*; do npm --prefix "$d" install; done
    cd packages/rust/extensions && cargo fetch

test:
    uv run pytest packages/python
    for d in packages/typescript/*; do npm --prefix "$d" test; done
    cd packages/rust/extensions && cargo test --workspace

lint:
    uv run ruff check packages/python
    for d in packages/typescript/*; do npm --prefix "$d" run lint --if-present; done
    cd packages/rust/extensions && cargo clippy --workspace -- -D warnings

build:
    uv build
    for d in packages/typescript/*; do npm --prefix "$d" run build --if-present; done
    cd packages/rust/extensions && cargo build --workspace --release

# Regenerate every derived doc (config matrices, agent manifest + codemap, api
# surface, suite matrix, thesis weaknesses, native docs). Run it, review the
# diff, commit. `uv run` is required, not stylistic: the generators import the
# config classes to introspect them, so every workspace package must be
# importable -- run `uv sync --all-packages` first in a fresh worktree.
docs:
    uv run python scripts/regen_docs.py

# What CI gates on: regenerate, then fail if the committed tree drifted. ~30s --
# nine generator processes, each importing the package tree.
docs-check:
    uv run python scripts/regen_docs.py --check
