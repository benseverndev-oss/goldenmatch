import polars as pl
from goldenpipe.compiler.compiled_runner import compile_and_run
from goldenpipe.compiler.lineage import field_lineage, format_lineage
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import Resolver
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import PipeContext


def _fixture_csv(tmp_path):
    surnames = ["Smith", "Jones", "Brown", "Garcia", "Lee", "Nguyen", "Patel", "Khan", "Diaz", "Okafor", "Wong", "Ali"]
    first = ["John", "Jane", "Bob", "Alice", "Carlos", "Mei", "Raj", "Sara", "Luis", "Ada", "Wei", "Omar"]
    rows = []
    for i in range(len(surnames)):
        rows.append({
            "first_name": f"  {first[i]} " if i % 3 == 0 else first[i],
            "email": (f"{first[i]}.{surnames[i]}@Example.COM " if i % 2 == 0 else f"{first[i].lower()}.{surnames[i].lower()}@example.com"),
            "last_name": surnames[i],
        })
    rows.append({"first_name": "John ", "email": " JOHN.SMITH@example.com", "last_name": "Smith"})
    rows.append({"first_name": "Jane", "email": "jane.jones@EXAMPLE.com ", "last_name": "Jones"})
    p = tmp_path / "people.csv"
    pl.DataFrame(rows).write_csv(p)
    return str(p)


def _plan(stages, registry):
    specs = [StageSpec(use=s) for s in stages]
    return Resolver.resolve(PipelineConfig(pipeline="lin", stages=specs), registry)


def test_format_lineage_string():
    lin = {"fields": [
        {"column": "email", "origin": "source", "checks": ["pattern_consistency"], "transforms": ["email_normalize"], "blocking_key": False, "scorer_input": True, "node_ids": [0, 1]},
    ], "unmapped": []}
    assert format_lineage(lin) == "email: checks[pattern_consistency] -> transforms[email_normalize] -> scorer-input"


def test_real_pipeline_lineage(tmp_path):
    reg = StageRegistry(); reg.discover()
    csv = _fixture_csv(tmp_path)
    plan = _plan(["goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe"], reg)
    ctx = PipeContext(df=pl.read_csv(csv, ignore_errors=True, encoding="utf8-lossy"))
    ctx.metadata["source"] = csv
    _, compiled = compile_and_run(plan, ctx, reg)

    lin = field_lineage(compiled)
    by_col = {f["column"]: f for f in lin["fields"]}

    # email's transforms match the manifest records for email (real fidelity vs what ran)
    manifest = ctx.artifacts["manifest"]
    email_transforms = [r.transform for r in manifest.records if r.column == "email"]
    assert by_col.get("email", {}).get("transforms", []) == email_transforms

    # blocking_key columns == the compiled Partition node's keys (honest; no assume-nonempty)
    partition = next((n for n in compiled["nodes"] if n["kind"] == "Partition"), None)
    assert partition is not None
    blocked = {c for c, f in by_col.items() if f["blocking_key"]}
    assert blocked == set(partition["keys"])
    print("LINEAGE:\n" + format_lineage(lin))  # sanity artifact (-s to see)
