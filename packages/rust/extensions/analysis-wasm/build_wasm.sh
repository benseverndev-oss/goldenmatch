#!/usr/bin/env bash
# Build analysis-wasm for wasm32 and copy the artifact + glue into the
# goldenanalysis TS package. Run from anywhere. Requires: rustup wasm32 target +
# wasm-bindgen-cli (installed here at the Cargo.lock-pinned version).
set -euo pipefail
export CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}"
export RUSTUP_HOME="${RUSTUP_HOME:-$HOME/.rustup}"
export PATH="$CARGO_HOME/bin:$PATH"

CRATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$CRATE_DIR/../../../typescript/goldenanalysis/src/core/wasm/artifacts"

rustup target add wasm32-unknown-unknown
cargo build --manifest-path "$CRATE_DIR/Cargo.toml" --target wasm32-unknown-unknown --release

# Install wasm-bindgen-cli at the EXACT version pinned in Cargo.lock — a CLI/crate
# version skew produces broken JS glue that fails at runtime, not build time.
WB_VER="$(grep -A1 '^name = "wasm-bindgen"$' "$CRATE_DIR/Cargo.lock" | grep '^version = ' | head -1 | sed -E 's/version = "([^"]+)"/\1/')"
if [ -z "$WB_VER" ]; then echo "could not resolve wasm-bindgen version from Cargo.lock" >&2; exit 1; fi
echo "Using wasm-bindgen $WB_VER"

if ! wasm-bindgen --version 2>/dev/null | grep -q "$WB_VER"; then
  cargo install wasm-bindgen-cli --version "=$WB_VER" --locked
fi
command -v wasm-bindgen >/dev/null 2>&1 || { echo "wasm-bindgen not on PATH after install; aborting" >&2; exit 1; }

wasm-bindgen \
  "$CRATE_DIR/target/wasm32-unknown-unknown/release/goldenmatch_analysis_wasm.wasm" \
  --target web --out-dir "$OUT_DIR" --out-name analysis_wasm

echo "Artifact written to $OUT_DIR (analysis_wasm_bg.wasm + analysis_wasm.js)"
