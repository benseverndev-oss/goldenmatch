"""Sail tier (distributed, Spark Connect) -- the distributed sibling of the
one-box DataFusion spine.

Sail (LakeSail) is programmed via the Spark Connect protocol (PySpark
DataFrame/SQL), NOT the datafusion Python API. This package re-expresses the
spine's relational algorithm against PySpark; it is a parallel implementation,
not a port. Opt-in via ``pip install goldenmatch[sail]``.

Spec: docs/superpowers/specs/2026-06-03-sail-tier-design.md
"""
