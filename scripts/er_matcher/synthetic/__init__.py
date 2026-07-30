"""Lean synthetic ER data source (Phase 1b).

Pure-stdlib package: vocab -> schemas -> corruption -> ``SyntheticSource``.
Reads bundled data files under ``synthetic/data/`` directly; does NOT import
goldenmatch, so the pure units stay importable/testable without the heavy
stack (see docs/superpowers/plans/2026-07-28-er-matcher-p1b-synthetic-lean.md).
"""
