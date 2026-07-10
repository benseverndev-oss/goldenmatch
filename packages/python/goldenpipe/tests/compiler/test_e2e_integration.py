"""SP3 real-pipeline integration proof: run a full load->check->flow->match pipeline
with a survivorship-ACTIVE goldenmatch config so goldenmatch actually produces
ClusterProvenance, then stitch SP2 field-lineage with that provenance and assert an
entry carries BOTH source_row_id (goldenmatch survivorship) AND transforms (SP2
Flow-clean). No mocks -- this exercises real goldenmatch lineage end-to-end."""
from __future__ import annotations

import polars as pl

# Real goldenmatch config schema (verified against goldenmatch/config/schemas.py).
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenGroupRule,
    GoldenMatchConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenpipe.compiler.compiled_runner import compile_and_run
from goldenpipe.compiler.e2e import end_to_end_lineage, format_end_to_end
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import Resolver
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import PipeContext


def _fixture_csv(tmp_path) -> str:
    """~14 rows. Surnames are soundex-spread (avoids blocking/scoring hang). `email`
    is DIRTY (MixedCase + surrounding whitespace) so GoldenFlow auto-detect transforms
    it -- and it is a SCALAR column (NOT in a field_group), so it lands in
    ClusterProvenance.fields (the branch the stitch reads). Three duplicate PAIRS
    (same first+last, same city/state) form multi-member clusters; survivorship needs
    at least one cluster of size >= 2."""
    rows = [
        # cluster A: Robert Smith / Boston MA (dup pair)
        {"first_name": "Robert", "last_name": "Smith", "email": "  RObert.Smith@Example.COM ", "city": "Boston", "state": "MA"},
        {"first_name": "Robert", "last_name": "Smith", "email": "robert.smith@example.com", "city": "Boston", "state": "MA"},
        # cluster B: Maria Garcia / Miami FL (dup pair)
        {"first_name": "Maria", "last_name": "Garcia", "email": " Maria.Garcia@Mail.Com  ", "city": "Miami", "state": "FL"},
        {"first_name": "Maria", "last_name": "Garcia", "email": "maria.garcia@mail.com", "city": "Miami", "state": "FL"},
        # cluster C: James Johnson / Chicago IL (dup pair)
        {"first_name": "James", "last_name": "Johnson", "email": "JAMES.johnson@Work.org ", "city": "Chicago", "state": "IL"},
        {"first_name": "James", "last_name": "Johnson", "email": "james.johnson@work.org", "city": "Chicago", "state": "IL"},
        # singletons -- soundex-spread surnames
        {"first_name": "William", "last_name": "Williams", "email": "  William.W@Example.com", "city": "Dallas", "state": "TX"},
        {"first_name": "Brian", "last_name": "Brown", "email": "Brian.Brown@Mail.com ", "city": "Denver", "state": "CO"},
        {"first_name": "Jennifer", "last_name": "Jones", "email": " Jen.Jones@Work.org", "city": "Seattle", "state": "WA"},
        {"first_name": "Michael", "last_name": "Miller", "email": "Michael.Miller@Example.com  ", "city": "Portland", "state": "OR"},
        {"first_name": "David", "last_name": "Davis", "email": "  David.Davis@Mail.com", "city": "Phoenix", "state": "AZ"},
        {"first_name": "Linda", "last_name": "Lopez", "email": "Linda.Lopez@Work.org ", "city": "Austin", "state": "TX"},
        {"first_name": "Karen", "last_name": "Wilson", "email": " Karen.Wilson@Example.com ", "city": "Atlanta", "state": "GA"},
        {"first_name": "Nancy", "last_name": "Nguyen", "email": "Nancy.Nguyen@Mail.com", "city": "Reno", "state": "NV"},
    ]
    df = pl.DataFrame(rows)
    csv_path = tmp_path / "e2e_people.csv"
    df.write_csv(str(csv_path))
    return str(csv_path)


def _match_config() -> GoldenMatchConfig:
    """Survivorship-ACTIVE config. `golden_rules.field_groups=[loc:(city,state)]` trips
    _survivorship_active (the field_groups branch) with no when:-predicate risk. `email`
    stays a SCALAR golden field (default most_complete) so it lands in cp.fields."""
    golden_rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[
            GoldenGroupRule(name="loc", columns=["city", "state"], strategy="most_complete"),
        ],
    )
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="fuzzy_name",
                type="weighted",
                threshold=0.85,
                fields=[
                    MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0,
                                  transforms=["lowercase", "strip"]),
                    MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0,
                                  transforms=["lowercase", "strip"]),
                ],
            ),
        ],
        blocking=BlockingConfig(
            keys=[BlockingKeyConfig(fields=["last_name"], transforms=["lowercase", "soundex"])],
            max_block_size=500,
            skip_oversized=True,
        ),
        golden_rules=golden_rules,
    )


def test_end_to_end_lineage_real_pipeline(tmp_path):
    reg = StageRegistry()
    reg.discover()
    csv = _fixture_csv(tmp_path)

    gm_config = _match_config()
    stages = [
        StageSpec(use="goldencheck.scan"),
        StageSpec(use="goldenflow.transform"),
        # match adapter does GoldenMatchConfig(**stage_config) -> pass the field kwargs
        StageSpec(use="goldenmatch.dedupe", config=gm_config.model_dump()),
    ]
    plan = Resolver.resolve(PipelineConfig(pipeline="e2e", stages=stages), reg)
    ctx = PipeContext(df=pl.read_csv(csv, ignore_errors=True, encoding="utf8-lossy"))
    ctx.metadata["source"] = csv
    _, compiled = compile_and_run(plan, ctx, reg)

    prov = ctx.artifacts.get("golden_provenance")
    assert prov is not None, "survivorship-active config should surface golden_provenance"

    out = end_to_end_lineage(compiled, prov)
    print("E2E LINEAGE:\n" + format_end_to_end(out))

    # the scalar email entry must carry BOTH source_row_id (goldenmatch) AND
    # transforms (SP2 Flow-clean).
    email_entries = [e for e in out["entries"] if e["column"] == "email"]
    assert email_entries, "email should appear in cp.fields (scalar survivorship)"
    e = email_entries[0]
    assert e["source_row_id"] is not None
    assert e["transforms"], "email was Flow-transformed -> SP2 transforms should be non-empty"
