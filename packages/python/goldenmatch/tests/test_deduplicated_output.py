"""#2991: user-facing dedupe outputs.

- `result.deduplicated` / `--output-deduplicated`: one row per entity. Golden
  records alone are only the entities that had duplicates -- 4 of the 7 people
  in the file below.
- `dupes` / `unique` / `golden` carry no pipeline working columns
  (`__mk_*`, `__xform_*`, `__block_key__`, `__bucket__`, `__raw__*`).
"""

import csv

import goldenmatch as gm
import pytest
from goldenmatch.core.output_tables import PIPELINE_INTERNAL_PREFIXES
from typer.testing import CliRunner

ROWS = [
    ("1", "Maria Gonzalez", "maria.gonzalez@example.com", "555-201-3344", "14 Oak Street", "62704"),
    ("2", "Maria Gonzales", "mgonzalez@example.com", "(555) 201-3344", "14 Oak St", "62704"),
    ("3", "María González", "maria.gonzalez@example.com", "5552013344", "14 Oak St.", "62704"),
    ("4", "James O'Neil", "jim.oneil@example.com", "555-883-1200", "902 Pine Avenue", "84065"),
    ("5", "Jim O'Neill", "jim.oneil@example.com", "555.883.1200", "902 Pine Ave", "84065"),
    ("6", "Priya Raman", "priya.raman@example.com", "555-410-7788", "3 Birch Road", "49116"),
    ("7", "Priya Ramen", "p.raman@example.com", "555-410-7788", "3 Birch Rd", "49116"),
    ("8", "Thomas Becker", "tbecker@example.com", "555-667-0192", "77 Elm Court", "97024"),
    ("9", "Aisha Bello", "aisha.bello@example.com", "555-339-2471", "510 Cedar Lane", "29601"),
    ("10", "Chen Wei", "chen.wei@example.com", "555-722-9031", "8 Maple Drive", "95361"),
    ("11", "Wei Chen", "chen.wei@example.com", "555-722-9031", "8 Maple Dr", "95361"),
    ("12", "Thomas Baker", "thomas.baker@example.com", "555-120-4455", "41 Walnut Way", "97024"),
]
N_ENTITIES = 7  # 4 duplicate groups + 3 singletons


@pytest.fixture
def customers(tmp_path):
    path = tmp_path / "customers.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "email", "phone", "address", "zip"])
        w.writerows(ROWS)
    return path


def _internal(cols):
    return [c for c in cols if c.startswith(PIPELINE_INTERNAL_PREFIXES)]


def test_result_tables_carry_no_pipeline_working_columns(customers):
    r = gm.dedupe(str(customers))
    for name in ("golden", "dupes", "unique"):
        table = getattr(r, name)
        assert table is not None and table.num_rows
        assert _internal(table.column_names) == [], name
    # Record identity survives: callers join on these.
    assert {"__row_id__", "__source__"} <= set(r.unique.column_names)


def test_deduplicated_is_one_row_per_entity(customers):
    r = gm.dedupe(str(customers))
    d = r.deduplicated
    assert d.num_rows == N_ENTITIES
    assert len(set(d.column("__cluster_id__").to_pylist())) == N_ENTITIES
    names = set(d.column("name").to_pylist())
    # The three people with no duplicate are in it, not only the merged groups.
    assert {"Thomas Becker", "Aisha Bello", "Thomas Baker"} <= names
    assert "__row_id__" not in d.column_names and _internal(d.column_names) == []


def test_to_csv_deduplicated_and_rejects_unknown_kind(customers, tmp_path):
    r = gm.dedupe(str(customers))
    out = tmp_path / "entities.csv"
    r.to_csv(str(out), which="deduplicated")
    with out.open(encoding="utf-8") as f:
        assert sum(1 for _ in f) == N_ENTITIES + 1  # header
    with pytest.raises(ValueError, match="which must be one of"):
        r.to_csv(str(tmp_path / "x.csv"), which="clusters")


def test_cli_default_writes_deduplicated_and_golden(customers, tmp_path):
    from goldenmatch.cli.main import app

    out_dir = tmp_path / "out"
    result = CliRunner().invoke(
        app, ["dedupe", str(customers), "--output-dir", str(out_dir), "--run-name", "run", "-q"]
    )
    assert result.exit_code == 0, result.output
    dedup = out_dir / "run_deduplicated.csv"
    assert dedup.exists() and (out_dir / "run_golden.csv").exists()
    with dedup.open(encoding="utf-8") as f:
        assert sum(1 for _ in f) == N_ENTITIES + 1


def test_entity_and_group_counts_agree_across_surfaces(customers):
    """#2989: the API said clusters=4 and the CLI said "7 clusters found" for the
    same run. Both were right about different things; now each says which."""
    r = gm.dedupe(str(customers))
    assert r.total_clusters == 4  # duplicate groups
    assert r.total_entities == N_ENTITIES == len(r.clusters) == r.deduplicated.num_rows


def test_cli_summary_names_what_it_counts(customers, tmp_path):
    from goldenmatch.cli.main import app

    result = CliRunner().invoke(
        app, ["dedupe", str(customers), "--output-dir", str(tmp_path / "o"), "--hide-controller"]
    )
    assert result.exit_code == 0, result.output
    assert "12 records -> 7 entities (4 duplicate groups)" in result.output
