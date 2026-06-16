import duckdb
from dbt_goldensuite.materialize import run_goldenmatch_dedupe


def test_run_dedupe_zero_config_probabilistic(tmp_path):
    db = str(tmp_path / "wh.duckdb")
    con = duckdb.connect(db)
    # ~12 rows with agreeing duplicate clusters so FS EM can train at dedupe time
    # (autoconfig itself only fails when no column is matchable; `name` is).
    con.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES "
        "('John Smith','Austin','1980-01-01'),"
        "('Jon Smith','Austin','1980-01-01'),"
        "('John Smyth','Austin','1980-01-01'),"
        "('Jane Doe','Dallas','1975-05-05'),"
        "('J Doe','Dallas','1975-05-05'),"
        "('Jayne Doe','Dallas','1975-05-05'),"
        "('Robert Brown','Houston','1990-09-09'),"
        "('Rob Brown','Houston','1990-09-09'),"
        "('Bob Brown','Houston','1990-09-09'),"
        "('Mary Jones','Austin','1985-03-03'),"
        "('Mary Jonez','Austin','1985-03-03'),"
        "('Maria Jones','Austin','1985-03-03')"
        ") t(name, city, dob)"
    )
    con.close()
    res = run_goldenmatch_dedupe(
        input_table="raw", output_table="deduped",
        database=db, probabilistic=True,   # NOTE: no config_path
    )
    assert res["input_rows"] == 12
    assert res["output_rows"] >= 1
