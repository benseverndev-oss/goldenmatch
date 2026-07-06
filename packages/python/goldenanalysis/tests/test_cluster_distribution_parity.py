"""Cross-surface lock for cluster.distribution -- the TS analyzer asserts the
byte-identical result against the SAME fixture (clusterDistribution.parity.test.ts).

``cluster_distribution_result.json`` is a byte-identical copy shared with
``packages/typescript/goldenanalysis/tests/fixtures/``. Regenerating it means
regenerating BOTH copies on purpose.
"""

import json
from pathlib import Path

from goldenanalysis.analyzers.cluster_dist import ClusterDistributionAnalyzer
from goldenanalysis.models import AnalyzerInput

FIXTURE = Path(__file__).parent / "fixtures" / "cluster_distribution_result.json"

_CLUSTERS = {
    "0": {"members": [0], "size": 1},
    "1": {"members": [1], "size": 1},
    "2": {"members": [2, 3], "size": 2},
    "3": {"members": [4, 5, 6], "size": 3},
    "4": {"members": [7, 8, 9, 10], "size": 4},
    "5": {"members": [11, 12, 13, 14, 15, 16], "size": 6},
}


def test_cluster_distribution_matches_fixture() -> None:
    r = ClusterDistributionAnalyzer().run(
        AnalyzerInput(
            dataset="customers",
            artifacts={"clusters": _CLUSTERS, "match_stats": {"total_records": 17}},
        )
    )
    got = {
        "metrics": [m.model_dump(mode="json") for m in r.metrics],
        "tables": [t.model_dump(mode="json") for t in r.tables],
    }
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert got == expected
