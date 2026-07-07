#!/usr/bin/env bash
# Build the goldenflow-wasm artifact INTO the TS package's
# src/core/wasm/artifacts/ so tsup's copy step (see tsup.config +
# scripts/copy_wasm_artifact.mjs) ships it in dist/ and the published npm package
# actually carries the opt-in WASM backend. The artifact is otherwise gitignored
# (CI-built), so without this step `enableWasm()` always falls back to pure-TS for
# published users. Mirrors the `goldenflow_wasm` CI lane's build exactly (same
# wasm-bindgen version pinned from the crate's Cargo.lock).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
CRATE_DIR="$REPO_ROOT/packages/rust/extensions/goldenflow-wasm"
OUT_DIR="$REPO_ROOT/packages/typescript/goldenflow/src/core/wasm/artifacts"

cargo build --manifest-path "$CRATE_DIR/Cargo.toml" \
  --target wasm32-unknown-unknown --release

WB_VER="$(grep -A1 '^name = "wasm-bindgen"$' "$CRATE_DIR/Cargo.lock" \
  | grep '^version = ' | head -1 | sed -E 's/version = "([^"]+)"/\1/')"
if [ -z "$WB_VER" ]; then
  echo "could not resolve wasm-bindgen version from Cargo.lock" >&2
  exit 1
fi
echo "Using wasm-bindgen $WB_VER"
if ! wasm-bindgen --version 2>/dev/null | grep -q "$WB_VER"; then
  cargo install wasm-bindgen-cli --version "=$WB_VER" --locked
fi

wasm-bindgen \
  "$CRATE_DIR/target/wasm32-unknown-unknown/release/goldenflow_wasm.wasm" \
  --target web --out-dir "$OUT_DIR" --out-name goldenflow_wasm

echo "built goldenflow-wasm artifact into $OUT_DIR"
ls -la "$OUT_DIR"
