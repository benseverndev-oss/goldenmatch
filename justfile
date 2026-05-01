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
