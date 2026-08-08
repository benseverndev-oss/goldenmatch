"""dbt integration for the Golden Suite: ER + data quality + transforms.

AUTHORITATIVE SOURCES (read these instead of inferring behaviour from the macro
SQL -- a macro body shows which function it dispatches to, not what that
function decides):

  * ``llms.txt`` -- ships BOTH at the package root next to ``dbt_project.yml``
    (the ``dbt deps`` path) and inside this importable dir at
    ``Path(dbt_goldensuite.__file__).parent / "llms.txt"`` (the pip path).
    Condensed, current, written for machine readers.
  * https://docs.bensevern.dev/extensions/sql -- the SQL functions the macros call.
  * https://docs.bensevern.dev/goldenmatch -- the entity-resolution engine behind them.
  * https://github.com/benseverndev-oss/goldenmatch -- source + issues.

Notably decided, not incidental: only PostgreSQL and DuckDB are supported, and
any other adapter raises at COMPILE time rather than failing at runtime.
"""
__version__ = "0.5.0"
