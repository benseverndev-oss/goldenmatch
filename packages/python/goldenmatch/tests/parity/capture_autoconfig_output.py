"""Capture current auto-config output for three benchmarks as parity pins.
Run ONCE before the AutoConfigDecisions refactor.
Output lives in autoconfig-classification.json.

#2532: `pin_config` -- used BOTH to regenerate the pins and, by
`test_autoconfig_parity_pins_unchanged`, to check them -- forces the auto-config
search into deterministic mode. Without it the controller can stop on
`BUDGET_TIME`, so which config gets committed depends on how fast and how loaded
the host is, and a pin over that value is unstable by construction: red on a slow
runner with no code change, green on a fast one while masking a real change.
Owning the mode here rather than in each caller is what keeps the regenerate side
and the check side pinning the same thing.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import polars as pl
from goldenmatch.core.autoconfig import auto_configure_df
from goldenmatch.core.autoconfig_determinism import ENV_VAR

DATASETS = Path(__file__).parent.parent / "benchmarks" / "datasets"


@contextmanager
def _deterministic_search():
    """Run the auto-config search with wall-clock budgets disabled."""
    previous = os.environ.get(ENV_VAR)
    os.environ[ENV_VAR] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(ENV_VAR, None)
        else:
            os.environ[ENV_VAR] = previous


def _assert_deterministic_stop(name: str) -> None:
    """Fail loudly if the run we're about to pin stopped on the clock anyway.

    Deterministic mode makes `BUDGET_TIME` unreachable, so this can only fire if
    the mode failed to reach the controller -- a stale import-time read, a
    subprocess that didn't inherit the env. That is precisely the silent-failure
    shape this fix exists to remove, so it must not degrade to a warning.
    """
    from goldenmatch.core.autoconfig_controller import _LAST_CONTROLLER_RUN
    from goldenmatch.core.complexity_profile import StopReason

    history = _LAST_CONTROLLER_RUN.get()
    if history is not None and history.stop_reason is StopReason.BUDGET_TIME:
        raise RuntimeError(
            f"{name}: the controller stopped on BUDGET_TIME under "
            f"{ENV_VAR}=1, so this config depends on host speed and must not "
            "be pinned. The determinism flag did not reach the controller."
        )


def pin_config(name: str, df: pl.DataFrame) -> dict:
    with _deterministic_search():
        cfg = auto_configure_df(df)
    _assert_deterministic_stop(name)
    mks = cfg.get_matchkeys()
    return {
        "name": name,
        "rows": df.height,
        "blocking": {
            "strategy": cfg.blocking.strategy,
            "keys": [{"fields": k.fields, "transforms": k.transforms} for k in (cfg.blocking.keys or [])],
            "passes": [{"fields": k.fields, "transforms": k.transforms} for k in (cfg.blocking.passes or [])],
        },
        "matchkeys": [
            {
                "name": mk.name,
                "type": mk.type,
                "threshold": mk.threshold,
                "fields": [
                    {"field": f.field, "scorer": f.scorer, "weight": f.weight,
                     "transforms": f.transforms}
                    for f in mk.fields
                ],
            }
            for mk in mks
        ],
    }

if __name__ == "__main__":
    pins = []
    # DBLP-ACM combined
    d = DATASETS / "DBLP-ACM"
    dblp = pl.read_csv(d / "DBLP2.csv", encoding="utf8-lossy", ignore_errors=True)
    acm  = pl.read_csv(d / "ACM.csv", encoding="utf8-lossy", ignore_errors=True)
    pins.append(pin_config("dblp_acm", pl.concat([dblp, acm], how="diagonal_relaxed")))
    # NCVR 10K
    ncvr_path = DATASETS / "NCVR" / "ncvoter_sample_10k.txt"
    df_ncvr = pl.read_csv(ncvr_path, separator="\t", encoding="utf8-lossy", ignore_errors=True)
    keep = ["county_desc","voter_reg_num","last_name","first_name","middle_name",
            "res_street_address","res_city_desc","state_cd","zip_code",
            "full_phone_number","birth_year","gender_code","race_code"]
    pins.append(pin_config("ncvr_10k", df_ncvr.select([c for c in keep if c in df_ncvr.columns])))
    # Abt-Buy
    d = DATASETS / "Abt-Buy"
    abt = pl.read_csv(d / "Abt.csv", encoding="utf8-lossy", ignore_errors=True)
    buy = pl.read_csv(d / "Buy.csv", encoding="utf8-lossy", ignore_errors=True)
    pins.append(pin_config("abt_buy", pl.concat([abt, buy], how="diagonal_relaxed")))

    out = Path(__file__).parent / "autoconfig-classification.json"
    out.write_text(json.dumps(pins, indent=2, default=str))
    print(f"wrote {out} with {len(pins)} pins")
