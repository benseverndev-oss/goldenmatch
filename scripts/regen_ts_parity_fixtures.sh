#!/usr/bin/env bash
#
# Canonical regeneration entrypoint for the TypeScript cross-language parity
# fixtures (#856). Runs EVERY Python emitter that produces a committed TS parity
# fixture, in place. After running, `git diff` shows any drift and
# `scripts/check_ts_parity_freshness.py` is the gate that fails CI on
# non-allowlisted drift (with a float tolerance).
#
# Before this, regenerating the fixtures was tribal knowledge spread across a
# dozen emitter scripts with different argument conventions; this is the single
# source of truth.
#
# Usage (from anywhere in the repo):
#     scripts/regen_ts_parity_fixtures.sh
#
# Requires `goldenmatch` and `goldenpipe` importable. Set PYTHON to choose the
# interpreter (defaults to `python`); CI uses the uv venv:
#     PYTHON=.venv/bin/python scripts/regen_ts_parity_fixtures.sh
#
# Two fixtures are produced here but ALLOWLISTED by the gate (their content is
# optional-dependency-sensitive controller-execution output): the emitters still
# run for completeness, but the checker does not compare them. See
# scripts/check_ts_parity_freshness.py ALLOWLIST.
set -euo pipefail

export POLARS_SKIP_CPU_CHECK=1
export PYTHONIOENCODING=utf-8

PY="${PYTHON:-python}"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

GM="packages/python/goldenmatch"
GP="packages/python/goldenpipe"
PARITY="packages/typescript/goldenmatch/tests/parity"

# $PY may be a multi-word launcher ("uv run python"); intentional word-split.
# shellcheck disable=SC2086

echo "== goldenmatch: controller-stoppoint (allowlisted) + indicators + negative-evidence =="
$PY "$GM/scripts/emit_ts_parity_fixtures.py" \
    --out "$PARITY/controller-stoppoint-fixtures.json" \
    --indicators-out "$PARITY/indicators-fixtures.json" \
    --ne-out "$PARITY/negative-evidence-fixtures.json"

echo "== goldenmatch: v2 (planner / EM / domain / tuners / blocker / clustering / golden) =="
$PY "$GM/scripts/emit_v2_parity_fixtures.py" --out "$PARITY/v2-fixtures.json"

echo "== goldenmatch: numeric / golden-strategy / transform fixtures =="
$PY "$GM/scripts/generate_parity_fixtures.py" --out "$PARITY/fixtures"

echo "== goldenmatch: config-edits / config-optimizer / pprl / resolve =="
$PY "$GM/scripts/emit_config_edits_fixture.py"
$PY "$GM/scripts/emit_config_optimizer_fixture.py"
$PY "$GM/scripts/emit_pprl_fixture.py"
$PY "$GM/scripts/emit_resolve_fixture.py"

echo "== goldenmatch: autoconfig-verify (allowlisted; regenerated for completeness) =="
$PY "$GM/tests/parity/export_ts_fixtures.py"

echo "== goldenpipe: pipe parity =="
$PY "$GP/scripts/emit_ts_parity_fixtures.py"

echo
echo "Done. Verify freshness with:  $PY scripts/check_ts_parity_freshness.py"
