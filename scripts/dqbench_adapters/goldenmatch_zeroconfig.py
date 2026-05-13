"""DQbench adapter that uses goldenmatch's zero-config controller path
instead of the hand-tuned config in the published `goldenmatch_adapter`.

Tests the introspective controller against DQbench's tiered ER
benchmarks. The v1.12 DQbench composite of 91.04 was measured with this
adapter.

Previously lived at `.profile_tmp/goldenmatch_zeroconfig_adapter.py`
(gitignored). Promoted to a committed location so
`scripts/run_benchmarks.py` can reproduce the published number from a
fresh clone.
"""
from __future__ import annotations

from pathlib import Path

from dqbench.adapters.base import EntityResolutionAdapter


class GoldenMatchZeroConfigAdapter(EntityResolutionAdapter):
    @property
    def name(self) -> str:
        return "goldenmatch-zeroconfig"

    @property
    def version(self) -> str:
        try:
            import goldenmatch
            return goldenmatch.__version__
        except ImportError:
            return "not-installed"

    def deduplicate(self, csv_path: Path) -> list[tuple[int, int]]:
        try:
            import goldenmatch
        except ImportError as exc:
            raise RuntimeError(
                "goldenmatch is not installed. Run: pip install goldenmatch"
            ) from exc

        import polars as pl
        df = pl.read_csv(csv_path)

        # Pure zero-config — controller chooses the entire config.
        result = goldenmatch.dedupe_df(df)

        pairs: list[tuple[int, int]] = []
        if result.clusters:
            for cluster in result.clusters.values():
                members = sorted(cluster["members"])
                if len(members) < 2:
                    continue
                for i in range(len(members)):
                    for j in range(i + 1, len(members)):
                        pairs.append((members[i], members[j]))
        return pairs


# DQbench's `--adapter` path expects a module-level instance to load.
adapter = GoldenMatchZeroConfigAdapter()
