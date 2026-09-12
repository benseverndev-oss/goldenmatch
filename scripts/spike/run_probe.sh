#!/usr/bin/env bash
# THROWAWAY spike driver for the homelab: sync, build native, fetch data, probe.
# Safe to re-run: datasets that already have a result JSON are skipped.
set -euo pipefail
cd "$(dirname "$0")/../.."
export ARROW_DEFAULT_MEMORY_POOL=system
export _RJEM_MALLOC_CONF="dirty_decay_ms:1000,muzzy_decay_ms:0"
export GOLDENMATCH_AUTOCONFIG_MEMORY=0
export GOLDENMATCH_AUTOCONFIG_DETERMINISTIC=1
export POLARS_SKIP_CPU_CHECK=1
export PYTHONHASHSEED=0
OUT="${1:-$HOME/spike-fs-loss}"
uv sync --all-packages
uv run python scripts/build_native.py
uv pip install recordlinkage
bash scripts/spike/fetch_design_data.sh
mkdir -p "$OUT"
uv run python -m scripts.spike.fs_loss_probe --out "$OUT"
echo "summary: $OUT/summary.md"
