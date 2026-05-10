"""Generate a v1.10-vintage memory cache entry as a JSON fixture.

v1.10 stored: serialized GoldenMatchConfig (with column_priors + indicators
fields per v1.10 schema, but WITHOUT v1.11's negative_evidence field).

Run: python tests/fixtures/autoconfig/_gen_v1_10_snapshot.py
"""
import json
from pathlib import Path

v1_10_entry = {
    "signature": "v110_test_signature",
    "config_json": {
        "matchkeys": [{
            "name": "primary",
            "type": "weighted",
            "threshold": 0.85,
            "fields": [{
                "field": "email", "transforms": ["lowercase"],
                "scorer": "ensemble", "weight": 1.0,
            }],
        }],
        "blocking": {
            "strategy": "static",
            "keys": [{"fields": ["email"], "transforms": ["lowercase"]}],
            "max_block_size": 1000,
            "skip_oversized": True,
        },
    },
    "succeeded": 1,
    "version_written_by": "1.10.0",
}
out = Path(__file__).parent / "v1_10_memory_snapshot.json"
out.write_text(json.dumps(v1_10_entry, indent=2))
print(f"wrote {out}")
