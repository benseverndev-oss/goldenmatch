import json
import duckdb
import pytest
from goldenmatch_duckdb import functions as gm


@pytest.fixture
def con():
    c = duckdb.connect()
    gm.register(c)
    # ~12 rows with clear duplicate clusters. auto_configure_probabilistic_df only
    # raises ValueError when there are NO matchable columns (name is matchable);
    # the agreeing clusters matter for EM training at dedupe time.
    c.execute(
        "CREATE TABLE people AS SELECT * FROM (VALUES "
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
        ") AS t(name, city, dob)"
    )
    return c


def test_autoconfig_probabilistic_builds_probabilistic_matchkeys(con):
    cfg = con.sql("SELECT goldenmatch_autoconfig('people', 'probabilistic')").fetchone()[0]
    data = json.loads(cfg)
    mks = data.get("matchkeys") or data.get("match_settings", {}).get("matchkeys", [])
    assert any(mk.get("type") == "probabilistic" for mk in mks), cfg


def test_autoconfig_one_arg_still_standard(con):
    cfg = con.sql("SELECT goldenmatch_autoconfig('people')").fetchone()[0]
    assert json.loads(cfg)  # parses; back-compat 1-arg form


def test_autoconfig_probabilistic_parity_with_python(con):
    cfg_sql = json.loads(con.sql("SELECT goldenmatch_autoconfig('people','probabilistic')").fetchone()[0])
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    df = con.sql("SELECT * FROM people").pl()
    cfg_py = json.loads(auto_configure_probabilistic_df(df).model_dump_json(exclude_none=True))
    def mk_types(c):
        mks = c.get("matchkeys") or c.get("match_settings", {}).get("matchkeys", [])
        return sorted(mk.get("type", "") for mk in mks)
    assert mk_types(cfg_sql) == mk_types(cfg_py)
