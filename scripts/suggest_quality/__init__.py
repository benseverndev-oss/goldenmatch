"""Config-suggestion quality harness — measure whether applying suggestions
improves F1 across a corpus of labeled ER datasets.

Mirrors the structure and design of ``scripts.autoconfig_quality``:
same ``report`` / ``gate`` / ``bless`` CLI subcommands, same dataset
registry + skip-when-absent pattern, same determinism env.

See docs/superpowers/specs/ for the design spec once Task 15 wires the
oracle and metrics.
"""
