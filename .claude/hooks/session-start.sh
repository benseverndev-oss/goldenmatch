#!/bin/bash
# SessionStart hook -- provision the codebase-memory MCP server for Claude Code
# on the web. It installs the static binary on a cold container (the container
# image is cached after the hook completes, so the ~266MB download is a
# one-time cold-start cost, not per-session) and (re)indexes this repo so the
# graph tools registered in .mcp.json are queryable from the first turn.
#
# Web-only: locally, the .mcp.json server entry uses whatever
# `codebase-memory-mcp` a dev has on PATH -- we never mutate a local machine.
# Every step is non-fatal: a failed install/index degrades gracefully (the
# session works without the graph) rather than blocking startup.
set -euo pipefail

# Remote (Claude Code on the web) only.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

REPO="${CLAUDE_PROJECT_DIR:-$(pwd)}"

# 1. Install the binary if it isn't already on PATH (idempotent).
if ! command -v codebase-memory-mcp >/dev/null 2>&1; then
  if ! npm install -g codebase-memory-mcp >/dev/null 2>&1; then
    echo "[session-start] codebase-memory-mcp install failed; graph tools unavailable this session" >&2
    exit 0
  fi
fi

# 2. (Re)index the current checkout so the graph reflects HEAD. Cheap (~15s for
#    this repo); honors the .gitignore hierarchy + .cbmignore automatically.
if ! codebase-memory-mcp cli index_repository "{\"repo_path\":\"${REPO}\"}" >/dev/null 2>&1; then
  echo "[session-start] codebase-memory index failed; run index_repository manually" >&2
fi

exit 0
