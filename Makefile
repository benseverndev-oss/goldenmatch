# GoldenMatch monorepo — convenience targets.
# The docs battery (config-matrix, agent-manifest, agent-codemap, api-surface,
# suite-matrix, thesis-weaknesses, native docs) is committed + gated; regenerate
# it all with one command instead of remembering the individual generators.

.PHONY: docs docs-check

# Regenerate every derived doc (a.k.a. "run the whole battery"). Commit the result.
docs:
	uv run python scripts/regen_docs.py

# CI-style: regenerate, then fail if the working tree drifted (nothing to commit
# means the committed docs already matched the code).
docs-check:
	uv run python scripts/regen_docs.py --check
