"""The documented zero-config file call, `gm.dedupe("customers.csv")`, must work.

It raised `KeyError: 'Field "__placeholder__" does not exist in schema'` (reproduced on
3.18.0): with no config and no exact/fuzzy, the file API built a
stub matchkey on a column that never exists instead of auto-configuring the files
the way `goldenmatch dedupe customers.csv` does. The README and python-api.mdx
both lead with this call.
"""

import csv

import goldenmatch as gm

# 12 records, 8 people: Maria x3, James/Jim x2, Priya x2, Chen/Wei x2, and three
# singletons (Thomas Becker, Aisha Bello, Thomas Baker).
ROWS = [
    ("1", "Maria Gonzalez", "maria.gonzalez@example.com", "555-201-3344", "14 Oak Street", "Springfield", "62704"),
    ("2", "Maria Gonzales", "mgonzalez@example.com", "(555) 201-3344", "14 Oak St", "Springfield", "62704"),
    ("3", "María González", "maria.gonzalez@example.com", "5552013344", "14 Oak St.", "Springfield", "62704"),
    ("4", "James O'Neil", "jim.oneil@example.com", "555-883-1200", "902 Pine Avenue", "Riverton", "84065"),
    ("5", "Jim O'Neill", "jim.oneil@example.com", "555.883.1200", "902 Pine Ave", "Riverton", "84065"),
    ("6", "Priya Raman", "priya.raman@example.com", "555-410-7788", "3 Birch Road", "Lakeside", "49116"),
    ("7", "Priya Ramen", "p.raman@example.com", "555-410-7788", "3 Birch Rd", "Lakeside", "49116"),
    ("8", "Thomas Becker", "tbecker@example.com", "555-667-0192", "77 Elm Court", "Fairview", "97024"),
    ("9", "Aisha Bello", "aisha.bello@example.com", "555-339-2471", "510 Cedar Lane", "Greenville", "29601"),
    ("10", "Chen Wei", "chen.wei@example.com", "555-722-9031", "8 Maple Drive", "Oakdale", "95361"),
    ("11", "Wei Chen", "chen.wei@example.com", "555-722-9031", "8 Maple Dr", "Oakdale", "95361"),
    ("12", "Thomas Baker", "thomas.baker@example.com", "555-120-4455", "41 Walnut Way", "Fairview", "97024"),
]
EXPECTED_GROUPS = {(0, 1, 2), (3, 4), (5, 6), (9, 10)}


def _write_customers(tmp_path):
    path = tmp_path / "customers.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "email", "phone", "address", "city", "zip"])
        writer.writerows(ROWS)
    return str(path)


def test_zero_config_file_dedupe_finds_the_duplicates(tmp_path):
    result = gm.dedupe(_write_customers(tmp_path))
    groups = {
        tuple(sorted(c["members"])) for c in result.clusters.values() if c["size"] > 1
    }
    assert groups == EXPECTED_GROUPS
    # The committed config is the auto-configured one, never the old stub.
    fields = {f.field for mk in result.config.get_matchkeys() for f in mk.fields}
    assert "__placeholder__" not in fields


def test_zero_config_dedupe_to_parquet_runs(tmp_path):
    out = gm.dedupe_to_parquet(_write_customers(tmp_path), out_dir=str(tmp_path / "out"))
    assert out["golden_count"] == len(EXPECTED_GROUPS)
