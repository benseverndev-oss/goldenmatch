#!/usr/bin/env bash
# Build score-wasm for wasm32 and copy the artifact + glue into the goldenmatch
# TS package. Run from anywhere. Requires: rustup wasm32 target + wasm-bindgen-cli.
set -euo pipefail
export CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}"
export RUSTUP_HOME="${RUSTUP_HOME:-$HOME/.rustup}"
export PATH="$CARGO_HOME/bin:$PATH"

CRATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$CRATE_DIR/../../../typescript/goldenmatch/src/core/wasm/artifacts"

rustup target add wasm32-unknown-unknown
cargo build --manifest-path "$CRATE_DIR/Cargo.toml" --target wasm32-unknown-unknown --release

# Install wasm-bindgen-cli at the EXACT version pinned in Cargo.lock — a CLI/crate
# version skew produces broken JS glue that fails at runtime, not build time
# (a known wasm-bindgen footgun). Read the pinned version straight from the
# committed lockfile (the reason we commit it). The anchored grep matches the
# exact `wasm-bindgen` package, never `wasm-bindgen-macro`/`-shared`.
WB_VER="$(grep -A1 '^name = "wasm-bindgen"$' "$CRATE_DIR/Cargo.lock" | grep '^version = ' | head -1 | sed -E 's/version = "([^"]+)"/\1/')"
if [ -z "$WB_VER" ]; then echo "could not resolve wasm-bindgen version from Cargo.lock" >&2; exit 1; fi
echo "Using wasm-bindgen $WB_VER"

# Only (re)install when the on-PATH cli doesn't already match the pinned version.
if ! wasm-bindgen --version 2>/dev/null | grep -q "$WB_VER"; then
  cargo install wasm-bindgen-cli --version "=$WB_VER" --locked
fi
command -v wasm-bindgen >/dev/null 2>&1 || { echo "wasm-bindgen not on PATH after install; aborting" >&2; exit 1; }

wasm-bindgen \
  "$CRATE_DIR/target/wasm32-unknown-unknown/release/goldenmatch_score_wasm.wasm" \
  --target web --out-dir "$OUT_DIR" --out-name score_wasm

echo "Artifact written to $OUT_DIR (score_wasm_bg.wasm + score_wasm.js)"
