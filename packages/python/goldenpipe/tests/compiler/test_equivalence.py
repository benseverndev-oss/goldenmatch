"""Equivalence gate (SP1): prove the compiler is byte-identical to the classic Runner.

For three fixture pipelines we run the classic ``Runner`` and ``compile_and_run`` on
the SAME fixture (fresh ``PipeContext`` each) against the REAL engines
(goldencheck / goldenflow / goldenmatch on a tiny fixture) and assert:

1. the produced artifacts are byte-identical after normalizing wall-clock fields, and
2. the recorded IR has the expected node shape (Source / Scan / Map / Partition /
   PairScore / Connected).

This is real-engine integration -- no mocks. The fixture is deliberately DIRTY
(whitespace, MixedCase emails) so goldenflow auto-detect emits transforms, and its
surnames are spread across distinct soundex codes so goldenmatch blocking stays fast.
"""
from __future__ import annotations

import polars as pl
from goldenpipe.compiler.compiled_runner import compile_and_run
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import Resolver
from goldenpipe.engine.runner import Runner
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import PipeContext

# A constant we stamp over every wall-clock field so classic vs compiled compare
# equal (they run microseconds apart; the real timestamps always differ).
_FROZEN_TS = "1970-01-01T00:00:00+00:00"

# Substrings that mark a wall-clock / timing key we must drop before comparing.
_TIMING_MARKERS = ("time", "elapsed", "duration", "sec")

# goldencheck's `pattern_consistency` check samples a NON-deterministic subset of
# minority pattern strings into its message (set-iteration over equal-count patterns);
# the message therefore varies run-to-run even classic-vs-classic. Its (check, column,
# severity) footprint IS stable, so we compare that footprint but drop the message.
# Everything else -- including the message -- is compared in full.
_NONDETERMINISTIC_MESSAGE_CHECKS = frozenset({"pattern_consistency"})


# --------------------------------------------------------------------------- #
# Fixtures + normalizer
# --------------------------------------------------------------------------- #
def _tiny_people_df() -> pl.DataFrame:
    """~26 rows: first_name / last_name / email with DIRTY values (leading/trailing
    whitespace, MixedCase emails) and a few deliberate dupes. Surnames span distinct
    soundex codes so goldenmatch blocking does not hang."""
    surnames = [
        "Smith", "Jones", "Brown", "Garcia", "Lee", "Nguyen", "Patel", "Khan",
        "Diaz", "Okafor", "Wong", "Ali", "Rossi", "Kim", "Silva", "Adams",
        "Cohen", "Ivanov", "Mbeki", "Tanaka", "Reyes", "Haddad", "Novak", "Berg",
    ]
    first = [
        "John", "Jane", "Bob", "Alice", "Carlos", "Mei", "Raj", "Sara", "Luis", "Ada",
        "Wei", "Omar", "Marco", "Yuna", "Ana", "Tom", "David", "Igor", "Thabo", "Ken",
        "Rosa", "Sami", "Petr", "Lena",
    ]
    rows = []
    for i in range(len(surnames)):
        rows.append({
            # dirty: leading/trailing whitespace on every 3rd first_name
            "first_name": f"  {first[i]} " if i % 3 == 0 else first[i],
            # dirty: MixedCase + trailing space on every other email
            "email": (
                f"{first[i]}.{surnames[i]}@Example.COM "
                if i % 2 == 0
                else f"{first[i].lower()}.{surnames[i].lower()}@example.com"
            ),
            "last_name": surnames[i],
        })
    # deliberate dupes (dirty variants of rows 0 and 1)
    rows.append({"first_name": "John ", "email": " JOHN.SMITH@example.com", "last_name": "Smith"})
    rows.append({"first_name": "Jane", "email": "jane.jones@EXAMPLE.com ", "last_name": "Jones"})
    return pl.DataFrame(rows)


def _write_csv(df: pl.DataFrame, tmp_path) -> str:
    """Write the fixture to ``tmp_path/people.csv`` and return the path string.
    goldencheck.scan reads this FILE (not ctx.df), so pipelines that scan need it."""
    csv_path = tmp_path / "people.csv"
    df.write_csv(csv_path)
    return str(csv_path)


def _read_source(csv_path: str) -> pl.DataFrame:
    """Load the CSV exactly as Pipeline.run does, so ctx.df and the scanned file agree."""
    return pl.read_csv(csv_path, ignore_errors=True, encoding="utf8-lossy")


def _strip_timing(d: dict) -> dict:
    """Drop keys that look like wall-clock/timing (match-stats robustness)."""
    return {
        k: v for k, v in d.items()
        if not any(m in str(k).lower() for m in _TIMING_MARKERS)
    }


def _norm_findings(findings) -> list[tuple]:
    """Canonical, order-independent projection of the findings list. For the
    non-deterministic-message checks we drop the message; for all others we keep it."""
    out = []
    for f in findings:
        check = f.get("check")
        row = (str(check), str(f.get("column")), str(f.get("severity")))
        if check not in _NONDETERMINISTIC_MESSAGE_CHECKS:
            row = (*row, str(f.get("message")))
        out.append(row)
    return sorted(out)


def _norm_value(key: str, value):
    """Canonical, comparable form for a single artifact."""
    if value is None:
        return None
    if key == "manifest":
        d = value.to_dict()
        d["created_at"] = _FROZEN_TS  # freeze the only wall-clock field
        return d
    if key == "golden":  # v3.0.0: pa.Table result frame -> list of row dicts
        return value.to_pylist() if hasattr(value, "num_rows") else value.to_dicts()
    if key == "match_stats":
        return _strip_timing(dict(value))
    if key == "findings":
        return _norm_findings(value)
    if key == "clusters":
        return _norm_clusters(value)
    # profile (DatasetProfile with a real __eq__) is directly comparable.
    return value


def _norm_clusters(clusters: dict) -> dict:
    """Sort each cluster's set-like `members` list. Membership (the partition) is
    deterministic, but intra-cluster member ORDER is not stable across two independent
    goldenmatch runs on Linux (hash/set iteration) — Windows happened to be stable.
    Sorting makes the comparison test the partition, not incidental order."""
    out = {}
    for cid, c in clusters.items():
        cc = dict(c)
        m = cc.get("members")
        if isinstance(m, list):
            cc["members"] = sorted(m)
        out[cid] = cc
    return out


def _snapshot(ctx, keys) -> dict:
    """Comparable snapshot of the named artifacts (missing -> None). The transformed
    frame lives in ``ctx.df`` (a polars DataFrame), NOT in ``ctx.artifacts`` -- so
    "df" is sourced from ``ctx.df`` directly. polars transforms preserve row order,
    so ``.to_dicts()`` is directly comparable."""
    snap = {k: _norm_value(k, ctx.artifacts.get(k)) for k in keys if k != "df"}
    if "df" in keys:
        snap["df"] = ctx.df.to_dicts() if ctx.df is not None else None
    return snap


# --------------------------------------------------------------------------- #
# Engine + plan helpers
# --------------------------------------------------------------------------- #
def _registry() -> StageRegistry:
    reg = StageRegistry()
    reg.discover()
    return reg


def _plan(stages, registry):
    """Build a real ExecutionPlan via the Resolver. The auto-prepended `load` stage
    is KEPT (it is the Source node)."""
    specs = [s if isinstance(s, StageSpec) else StageSpec(use=s) for s in stages]
    config = PipelineConfig(pipeline="equiv", stages=specs)
    return Resolver.resolve(config, registry)


def _run_classic(plan, ctx, registry):
    Runner(registry).run(plan, ctx)
    return ctx


def _run_compiled(plan, ctx, registry):
    _, compiled = compile_and_run(plan, ctx, registry)
    return ctx, compiled


def _kinds(compiled) -> list[str]:
    return [n["kind"] for n in compiled["nodes"]]


# --------------------------------------------------------------------------- #
# Test 1: load -> goldenflow.transform (explicit transforms)
# --------------------------------------------------------------------------- #
def test_equivalence_load_flow_explicit():
    from goldenflow.config.schema import GoldenFlowConfig, TransformSpec

    # Explicit flow config -> a GoldenFlowConfig object (the adapter calls
    # transform_df(df, **stage_config), so the "config" value must be the real object).
    flow_cfg = GoldenFlowConfig(transforms=[TransformSpec(column="email", ops=["email_normalize"])])
    stages = [StageSpec(use="goldenflow.transform", config={"config": flow_cfg})]

    registry = _registry()
    plan = _plan(stages, registry)
    compare_keys = ("df", "manifest")

    classic = _run_classic(plan, PipeContext(df=_tiny_people_df()), registry)
    compiled_ctx, compiled = _run_compiled(plan, PipeContext(df=_tiny_people_df()), registry)

    assert _snapshot(classic, compare_keys) == _snapshot(compiled_ctx, compare_keys)

    # IR shape: Source (from load) then a Map for the one explicit op.
    assert _kinds(compiled)[:2] == ["Source", "Map"]
    map_nodes = [n for n in compiled["nodes"] if n["kind"] == "Map"]
    assert [(n["column"], n["op"]) for n in map_nodes] == [("email", "email_normalize")]


# --------------------------------------------------------------------------- #
# Test 2: load -> goldencheck.scan -> goldenflow.transform (auto)
# --------------------------------------------------------------------------- #
def test_equivalence_scan_flow_auto(tmp_path):
    csv_path = _write_csv(_tiny_people_df(), tmp_path)
    stages = ["goldencheck.scan", "goldenflow.transform"]

    registry = _registry()
    plan = _plan(stages, registry)
    compare_keys = ("df", "findings", "profile", "manifest")

    def build_ctx() -> PipeContext:
        ctx = PipeContext(df=_read_source(csv_path))
        ctx.metadata["source"] = csv_path  # scan reads the FILE, not ctx.df
        return ctx

    classic = _run_classic(plan, build_ctx(), registry)
    compiled_ctx, compiled = _run_compiled(plan, build_ctx(), registry)

    assert _snapshot(classic, compare_keys) == _snapshot(compiled_ctx, compare_keys)

    kinds = _kinds(compiled)
    assert "Scan" in kinds
    map_nodes = [n for n in compiled["nodes"] if n["kind"] == "Map"]
    assert map_nodes, "expected Map nodes from a dirty auto-detected fixture"
    # auto stages (no explicit config) are resolved records
    assert all(n["resolved"] is True for n in map_nodes)
    assert all(n["resolved"] is True for n in compiled["nodes"] if n["kind"] == "Scan")

    # Fidelity: the Map (column, op) sequence mirrors the flow manifest exactly.
    manifest = compiled_ctx.artifacts["manifest"]
    rec_seq = [(r.column, r.transform) for r in manifest.records]
    assert [(n["column"], n["op"]) for n in map_nodes] == rec_seq


# --------------------------------------------------------------------------- #
# Test 3: load -> goldencheck.scan -> goldenflow.transform -> goldenmatch.dedupe
# --------------------------------------------------------------------------- #
def test_equivalence_full_pipeline(tmp_path):
    csv_path = _write_csv(_tiny_people_df(), tmp_path)
    stages = ["goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe"]

    registry = _registry()
    plan = _plan(stages, registry)
    compare_keys = ("df", "findings", "profile", "manifest", "clusters", "golden", "match_stats")

    def build_ctx() -> PipeContext:
        ctx = PipeContext(df=_read_source(csv_path))
        ctx.metadata["source"] = csv_path
        return ctx

    classic = _run_classic(plan, build_ctx(), registry)
    compiled_ctx, compiled = _run_compiled(plan, build_ctx(), registry)

    assert _snapshot(classic, compare_keys) == _snapshot(compiled_ctx, compare_keys)

    kinds = _kinds(compiled)
    assert "Partition" in kinds
    assert "PairScore" in kinds
    assert "Connected" in kinds
